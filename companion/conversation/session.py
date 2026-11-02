"""The back-and-forth conversation loop: listens, transcribes, routes,
answers, and streams speech sentence-by-sentence while the model is still
generating.
"""
import os
import re
import shutil
import threading
import time

from .. import config, state
from ..audio.capture import record, wav_rms
from ..audio import capture, vad
from ..audio.playback import speak, StreamingSpeaker
from ..identity import presence, verifier
from ..identity.enrollment import enroll_user
from ..llm.client import chat, chat_stream, offline_message
from ..llm.guardrails import guard_output, rule_scan, guard_log, guard_alert, REFUSALS
from ..proactive import opportunity_worker
from ..skills import live_data, macros, memory_notes, utilities
from ..speech.transcriber import transcribe
from ..web.face_server import set_status
from . import router

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


class _BargeIn(Exception):
    """Internal signal: the user interrupted with a real new question, which
    should be answered instead of (not in addition to) the one in progress."""
    def __init__(self, text):
        self.text = text
        super().__init__(text)


def is_goodbye(text):
    t = text.lower()
    return any(w in t for w in config.GOODBYE_WORDS)


def _capture_interruption():
    """Capture + classify what was said during a confirmed interruption.

    Returns ("new_question", text) for real new input, or ("resume", None)
    for noise/backchannel/silence -- i.e. nothing worth abandoning the
    current answer for. Re-identifies the speaker, since whoever interrupted
    may not be who was originally talking.
    """
    set_status("listening")
    with capture.mic_session():
        wav = record(config.BARGE_IN_CAPTURE_SECONDS, denoise=True, tag="interrupt", stop_on_silence=True)
    state.current_user, _score = verifier.identify_speaker(wav)
    text = transcribe(wav, config.WHISPER_MODEL)
    if not text or utilities.is_noise(text):
        speak("Did you say something?")
        with capture.mic_session():
            wav2 = record(config.BARGE_IN_CLARIFY_SECONDS, denoise=True, tag="interrupt_confirm", stop_on_silence=True)
        state.current_user, _score = verifier.identify_speaker(wav2)
        text = transcribe(wav2, config.WHISPER_MODEL)
        if not text or utilities.is_noise(text):
            return ("resume", None)
    if router.is_backchannel(text):
        return ("resume", None)
    return ("new_question", text)


def _speak_checked(text, question):
    """speak() plus barge-in follow-through for single-shot (non-streaming)
    answers. A real interruption raises _BargeIn so the caller can answer
    the new question instead; noise/backchannel/silence just finishes
    normally -- a single speak() call has no sentence-level remainder to
    resume, unlike speak_streaming's sentence-by-sentence case."""
    interrupted = speak(text)
    if interrupted:
        action, new_text = _capture_interruption()
        if action == "new_question":
            raise _BargeIn(new_text)


def _offer_followup(followup):
    """Speak a proactive follow-up offer; interactive ones wait for a yes."""
    if followup.get("interactive"):
        state.pending_followup = followup
    speak(followup["prompt"])


def _remember_turn(question, answer):
    """Persist this turn to the short-term context ring buffer so it survives
    the device going back to sleep (not just the in-RAM `history` list)."""
    user_id = state.current_user["id"] if state.current_user else None
    memory_notes.append_turn(user_id, "user", question)
    memory_notes.append_turn(user_id, "companion", answer)


def _greet_and_surface(question):
    """Once per wake: a welcome-back greeting for a returning recognized user,
    any overdue follow-through notes, or a self-enrollment nudge for a
    frequently-heard but unrecognized guest."""
    if state.current_user:
        if presence.note_presence(state.current_user["id"]):
            speak(f"Welcome back, {state.current_user['display_name']}!")
        for rowid, text in memory_notes.due_notes_for(state.current_user["id"], time.strftime("%Y-%m-%d")):
            speak(f"Before we continue -- you asked me to remind you: {text}")
            memory_notes.clear_due(rowid)
    elif presence.note_guest_interaction():
        speak("I don't think we've met. Want me to learn your voice? "
              "Say, enroll me as your name.")


def speak_streaming(system, user, question):
    """Speak the model's answer sentence-by-sentence as it streams. Returns
    full text, or raises _BargeIn if a real new question interrupted it.

    On a confirmed interruption: noise/backchannel/silence just continues
    speaking the remaining sentences (still being pulled from the same
    live `chat_stream` generator -- nothing is restarted or re-generated);
    a real new question aborts the rest and lets the caller answer that
    instead. A per-answer interrupt budget keeps a noisy room from getting
    stuck in an endless stop/resume loop.
    """
    spoken = []
    buffer = ""
    interrupts_left = config.BARGE_IN_MAX_PER_ANSWER
    stream_speaker = StreamingSpeaker()   # no-op (ok=False) unless explicitly enabled

    def flush(sentence):
        nonlocal interrupts_left
        sentence = sentence.strip()
        if not sentence:
            return None
        category = rule_scan(sentence)   # per-sentence output guard
        if category:
            guard_log("output", category, sentence)
            guard_alert(category)
            return REFUSALS.get(category, REFUSALS["default"])
        spoken.append(sentence)
        set_status("speaking", heard=question, reply=" ".join(spoken))
        if stream_speaker.ok and stream_speaker.say(sentence):
            return None   # handed to the shared process; no per-sentence interrupt check here
        interrupted = speak(sentence)
        if interrupted and interrupts_left > 0:
            interrupts_left -= 1
            action, new_text = _capture_interruption()
            if action == "new_question":
                raise _BargeIn(new_text)
        return None

    for chunk in chat_stream(system, user):
        buffer += chunk
        while True:
            match = _SENTENCE_END.search(buffer)
            if not match:
                break
            sentence, buffer = buffer[: match.start()], buffer[match.end():]
            refusal = flush(sentence)
            if refusal:
                stream_speaker.close()
                speak(refusal)
                return refusal
    refusal = flush(buffer)
    stream_speaker.close()
    if refusal:
        speak(refusal)
        return refusal
    full = " ".join(spoken).strip()
    if not full:
        full = offline_message()
        speak(full)
    return full


def conversation_mode():
    """Stay awake answering back-to-back until goodbye or the toggle turns off."""
    state.conversation_active = True
    try:
        _conversation_loop()
    finally:
        state.conversation_active = False


def _conversation_loop():
    state.wake_event.clear()
    speak("Yes?")
    idle = 0
    noise_rounds = 0
    greeted = False
    history = []
    while True:
        utilities.drain_reminders()
        if state.pending_memory_confirmation and not state.pending_followup:
            # Surface a confirmation queued by a background fact-capture
            # thread -- never spoken from that thread directly.
            followup = state.pending_memory_confirmation
            state.pending_memory_confirmation = None
            _offer_followup(followup)
        if not state.switch_enabled():
            if state.led:
                state.led.off()
            set_status("muted")
            return
        set_status("listening")
        with capture.mic_session():
            wav, raw_wav = record(config.QUESTION_SECONDS, denoise=True, tag="input",
                                  stop_on_silence=True, keep_raw=True)
        set_status("thinking")
        rms = wav_rms(wav)
        # Speaker ID and transcription both just need to read the clip, so run
        # them concurrently instead of back-to-back. Speaker ID gets the RAW
        # clip: the denoise band-pass strips the upper harmonics voice
        # embeddings rely on, and enrollment records raw -- comparing a
        # filtered vector against a raw centroid never matches.
        id_wav = raw_wav + ".id.wav"
        try:
            shutil.copy(raw_wav, id_wav)
        except OSError:
            id_wav = None

        def _identify(path):
            try:
                state.current_user, score = verifier.identify_speaker(path)
                state.last_match_score = score
            finally:
                if path and os.path.exists(path):
                    try:
                        os.remove(path)
                    except OSError:
                        pass

        identify_thread = threading.Thread(target=_identify, args=(id_wav,), daemon=True)
        identify_thread.start()
        _t = time.time()
        # Speech-presence gate BEFORE transcription: whisper invents plausible
        # sentences from wind/laughter, so never give it non-speech to chew on.
        if not vad.has_speech(wav):
            identify_thread.join()
            if os.path.exists(wav):
                try:
                    os.remove(wav)
                except OSError:
                    pass
            question = ""
        else:
            question = transcribe(wav, config.WHISPER_MODEL)
            identify_thread.join()
        if raw_wav != wav and os.path.exists(raw_wav):
            try:
                os.remove(raw_wav)
            except OSError:
                pass
        if config.DEBUG_TIMING:
            print(f"  [time] asr {time.time() - _t:.1f}s")
        if question and (rms < config.QUESTION_RMS_THRESHOLD or utilities.is_noise(question)):
            print(f"(ignored: rms={rms:.0f} text={question!r})")
            question = ""
        if question and router.looks_truncated(question):
            # Ended on a dangling word -- they were probably still thinking.
            set_status("listening", heard=question)
            with capture.mic_session():
                more_wav = record(config.CONTINUATION_SECONDS, denoise=True,
                                 tag="continue", stop_on_silence=True)
            if vad.has_speech(more_wav):
                rest = transcribe(more_wav, config.WHISPER_MODEL)
                if rest and not utilities.is_noise(rest):
                    question = f"{question} {rest}".strip()
            elif os.path.exists(more_wav):
                try:
                    os.remove(more_wav)
                except OSError:
                    pass
            set_status("thinking")
        if not question:
            idle += 1
            noise_rounds += 1
            set_status("idle")
            if noise_rounds == 1 and config.NOISE_ACK_ONCE and rms >= config.QUESTION_RMS_THRESHOLD:
                # Something was audible but wasn't a question -- say so once,
                # then stay quiet however long the room keeps making noise.
                speak(config.NOISE_ACK_TEXT)
            elif idle == config.CONVO_STILL_THERE_ROUND:
                speak("Still there?")
            if noise_rounds >= config.NOISE_SLEEP_AFTER or idle >= config.CONVO_IDLE_ROUNDS:
                speak("Going back to sleep. Say hi companion to wake me.")
                set_status("idle")
                return
            continue
        idle = 0
        noise_rounds = 0
        print("You said:", question)
        if is_goodbye(question):
            set_status("idle", heard=question, reply="Goodbye.")
            speak("Goodbye.")
            return
        if not greeted:
            greeted = True
            _greet_and_surface(question)
            user_id = state.current_user["id"] if state.current_user else None
            history[:0] = memory_notes.recent_context(user_id, turns=config.CONTEXT_TURNS)
        # Proactive follow-up: a "yes" to the last offer runs it immediately.
        if state.pending_followup:
            offer = state.pending_followup
            state.pending_followup = None
            if router.is_affirmative(question):
                ans = macros.run_followup(offer)
                set_status("speaking", heard=question, reply=ans)
                speak(ans)
                state.last_answer = ans
                utilities.log_turn(question, ans)
                _remember_turn(question, ans)
                set_status("idle", heard=question, reply=ans)
                continue
        # Low-confidence capture: confirm before acting on a likely mis-hear.
        if router.low_confidence(rms, question):
            speak(f"I think you said. {question}. Is that right?")
            set_status("listening", heard=question, reply=f"Did you say: {question}?")
            with capture.mic_session():
                confirm_wav = record(config.QUESTION_SECONDS, denoise=True, tag="confirm", stop_on_silence=True)
            if router.is_negative(transcribe(confirm_wav, config.WHISPER_MODEL)):
                speak("Sorry about that. Please say it again.")
                continue
        set_status("thinking", heard=question)
        # An interruption mid-answer can hand back a fresh question to answer
        # in place of (not after) the one in progress -- loop until nothing
        # more was interrupted-in.
        pending_question = question
        while pending_question is not None:
            current_question = pending_question
            pending_question = None
            if is_goodbye(current_question):
                set_status("idle", heard=current_question, reply="Goodbye.")
                speak("Goodbye.")
                return
            kind, payload = router.route(current_question, state.current_user)
            _t = time.time()
            try:
                if kind == "enroll":
                    def _record_fn(seconds, tag):
                        with capture.mic_session():
                            return record(seconds, denoise=False, tag=tag, stop_on_silence=True)
                    ok, msg = enroll_user(payload, _record_fn, speak_fn=speak)
                    answer = msg
                    set_status("speaking", heard=current_question, reply=answer)
                    _speak_checked(answer, current_question)
                elif kind == "llm":
                    system, user, followup = payload
                    if config.STREAM_TTS:
                        answer = speak_streaming(utilities.with_context(system, history), user, current_question)
                    else:
                        answer = guard_output(current_question, chat(system, user))
                        set_status("speaking", heard=current_question, reply=answer)
                        _speak_checked(answer, current_question)
                    if followup:
                        _offer_followup(followup)
                elif kind == "live":
                    tool, q2 = payload
                    data = live_data.fetch_live(tool, q2)
                    if tool.get("ground") and config.STREAM_TTS:
                        answer = speak_streaming(router.ground_system() + data, q2, current_question)
                    else:
                        answer = guard_output(current_question, data)
                        set_status("speaking", heard=current_question, reply=answer)
                        _speak_checked(answer, current_question)
                    followup = tool.get("followup")
                    if followup:
                        _offer_followup(followup)
                else:
                    answer = guard_output(current_question, payload)
                    set_status("speaking", heard=current_question, reply=answer)
                    _speak_checked(answer, current_question)
            except _BargeIn as interruption:
                # Abandon the rest of this answer; answer the new question instead.
                pending_question = interruption.text
                continue
            if config.DEBUG_TIMING:
                print(f"  [time] answer {time.time() - _t:.1f}s")
            question = current_question   # so the bookkeeping below reflects what was actually answered
            state.last_answer = answer
            history.append((question, answer))
            history[:] = history[-config.CONTEXT_TURNS:]
        utilities.log_turn(question, answer)
        _remember_turn(question, answer)
        if not state.pending_followup:   # don't clobber an existing offer (e.g. weather_tomorrow)
            user_id = state.current_user["id"] if state.current_user else None
            opportunity_worker.enqueue(user_id, question, answer)
        set_status("idle", heard=question, reply=answer)
