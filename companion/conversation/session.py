"""The back-and-forth conversation loop: listens, transcribes, routes,
answers, and streams speech sentence-by-sentence while the model is still
generating.
"""
import os
import re
import shutil
import threading
import time

from .. import config, metrics, state
from ..audio.capture import record, wav_rms
from ..audio import capture, thinking_cue, vad
from ..audio.playback import speak, StreamingSpeaker
from ..identity import presence, verifier
from ..identity.enrollment import enroll_user
from ..llm.client import chat, chat_stream, offline_message
from ..llm.guardrails import guard_output, rule_scan, guard_log, guard_alert, REFUSALS
from ..proactive import opportunity_worker
from ..proactive import opportunity_repository as opp_repo
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


def _speak_checked(text, question, turn=None):
    """speak() plus barge-in follow-through for single-shot (non-streaming)
    answers. A real interruption raises _BargeIn so the caller can answer
    the new question instead; noise/backchannel/silence just finishes
    normally -- a single speak() call has no sentence-level remainder to
    resume, unlike speak_streaming's sentence-by-sentence case."""
    if turn:
        turn.mark_first_word()   # about to produce the first (and only) utterance
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
        name = state.current_user["display_name"]
        if presence.note_presence(state.current_user["id"]):
            speak(f"Welcome back, {name}!")
        elif config.GREET_BY_NAME:
            # Audible confirmation that voice recognition actually matched --
            # otherwise a recognized user is indistinguishable from a guest.
            speak(f"Hi {name.split()[0]}.")
        for rowid, text in memory_notes.due_notes_for(state.current_user["id"], time.strftime("%Y-%m-%d")):
            speak(f"Before we continue -- you asked me to remind you: {text}")
            memory_notes.clear_due(rowid)
        # Surface a proactive thread whose help became ready while they were away,
        # at this natural return moment rather than at some arbitrary idle gap.
        ready = opp_repo.get_ready_thread(state.current_user["id"])
        if ready and ready.get("final_message") and not opportunity_worker.is_shadow(ready.get("category")):
            speak(ready["final_message"])
            opp_repo.update_thread(ready["id"], "done")
    elif presence.note_guest_interaction():
        speak("I don't think we've met. Want me to learn your voice? "
              "Say, enroll me as your name.")


_SESSION_SUMMARY_SYSTEM = (
    "Summarize this conversation so far into 2-3 short sentences capturing what the "
    "user asked about and any decisions, preferences or facts that came up. Be concise and factual.")


def _summarize_conversation(transcript):
    """One short rolling summary of the given (question, answer) turns, or ''."""
    if not transcript:
        return ""
    text = "\n".join(f"User: {q}\nYou: {a}" for q, a in transcript)
    try:
        return chat(_SESSION_SUMMARY_SYSTEM, text).strip()
    except Exception:
        return ""


def _spawn_summary_update(transcript_snapshot, session):
    """Refresh the rolling summary in the background so it never sits on the
    reply's critical path. Summarizes everything except the most-recent turns,
    which stay in the prompt verbatim."""
    older = transcript_snapshot[:-config.SESSION_VERBATIM_TURNS]
    if not older:
        return

    def run():
        summary = _summarize_conversation(older)
        if summary:
            session["summary"] = summary

    threading.Thread(target=run, daemon=True).start()


def _persist_episode(transcript):
    """At the end of a conversation, store an episode summary for a recognized
    user so it can be recalled in a later session. Runs in the background so it
    doesn't delay the device going back to sleep."""
    user = state.current_user
    if not user or not transcript:
        return
    user_id = user["id"]

    def run():
        summary = _summarize_conversation(transcript)
        if summary:
            try:
                memory_notes.remember_episode(user_id, summary)
            except Exception as exc:
                print("episode persist failed:", exc)

    threading.Thread(target=run, daemon=True).start()


def speak_streaming(system, user, question, cue=None, turn=None):
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
        if cue:
            cue.stop()   # first real speech is about to start -- end the thinking cue
        if turn:
            turn.mark_first_word()
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
        if cue:
            cue.stop()   # the stream produced nothing -- stop the cue before the fallback
        if turn:
            turn.mark_first_word()
        full = offline_message()
        speak(full)
    return full


def conversation_mode():
    """Stay awake answering back-to-back until goodbye or the toggle turns off."""
    state.conversation_active = True
    transcript = []   # full (question, answer) log for this conversation -> episode summary
    stats = {"answered": 0, "woke_ts": time.time()}   # real-use metrics for this wake
    try:
        _conversation_loop(transcript, stats)
    finally:
        state.conversation_active = False
        if config.SESSION_EPISODE_ENABLED:
            _persist_episode(transcript)
        # One session record per wake: answered==0 means the wake produced no
        # real turn -- the signal behind the false-wake rate.
        metrics.log_session(stats["answered"], duration_s=time.time() - stats["woke_ts"])


def _conversation_loop(transcript, stats):
    state.wake_event.clear()
    speak("Yes?")
    idle = 0
    noise_rounds = 0
    last_identify_ts = 0.0   # 0 => identify on the first turn of this conversation
    greeted = False
    session = {"summary": ""}   # rolling summary of older turns, refreshed in the background
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
        if state.ui_muted:
            # UI mute during an active conversation: stop listening and turn
            # the LED off, but -- unlike the physical toggle above -- stay in
            # companion mode and keep polling until the button is pressed again.
            if state.led:
                state.led.off()
            set_status("muted")
            time.sleep(0.3)
            continue
        set_status("listening")
        with capture.mic_session():
            wav, raw_wav = record(config.QUESTION_SECONDS, denoise=True, tag="input",
                                  stop_on_silence=True, keep_raw=True)
        set_status("thinking")
        rms = wav_rms(wav)
        asr_ms = 0                 # per-utterance metrics, reset each round
        continuation_used = False
        low_conf_used = False
        # Re-identifying every turn is wasted CPU: within one conversation the
        # speaker almost never changes. Identify on the first turn, then only
        # after a long gap -- the realistic moment someone else takes over.
        gap = time.time() - last_identify_ts
        needs_identify = (last_identify_ts == 0.0
                          or gap >= config.REIDENTIFY_AFTER_SECONDS)
        id_wav = None
        if needs_identify:
            # Speaker ID gets the RAW clip: the denoise band-pass strips the
            # upper harmonics voice embeddings rely on, and enrollment records
            # raw -- comparing a filtered vector against a raw centroid never
            # matches. It runs concurrently with transcription; both only read.
            id_wav = raw_wav + ".id.wav"
            try:
                shutil.copy(raw_wav, id_wav)
            except OSError:
                id_wav = None
            last_identify_ts = time.time()

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

        identify_thread = None
        if id_wav:
            identify_thread = threading.Thread(target=_identify, args=(id_wav,), daemon=True)
            identify_thread.start()
        _t = time.time()
        # Speech-presence gate BEFORE transcription: whisper invents plausible
        # sentences from wind/laughter, so never give it non-speech to chew on.
        if not vad.has_speech(wav):
            if identify_thread:
                identify_thread.join()
            if os.path.exists(wav):
                try:
                    os.remove(wav)
                except OSError:
                    pass
            question = ""
        else:
            question = transcribe(wav, config.WHISPER_MODEL)
            if identify_thread:
                identify_thread.join()
        if raw_wav != wav and os.path.exists(raw_wav):
            try:
                os.remove(raw_wav)
            except OSError:
                pass
        asr_ms = int((time.time() - _t) * 1000)
        who = state.current_user["display_name"] if state.current_user else "guest"
        print(f"(speaker: {who}  score={state.last_match_score:.3f} "
              f"threshold={config.SPEAKER_MATCH_THRESHOLD}"
              f"{'' if needs_identify else ', cached'})")
        if config.DEBUG_TIMING:
            print(f"  [time] asr {time.time() - _t:.1f}s")
        if needs_identify:
            metrics.log_identify(
                state.current_user["id"] if state.current_user else None,
                who, state.last_match_score, config.SPEAKER_MATCH_THRESHOLD, cached=False)
        if question and (rms < config.QUESTION_RMS_THRESHOLD or utilities.is_noise(question)):
            print(f"(ignored: rms={rms:.0f} text={question!r})")
            metrics.log_turn(kind="discarded", reason="noise_or_low_rms",
                             heard=question, rms=int(rms), asr_ms=asr_ms)
            question = ""
        if question and router.looks_truncated(question):
            # Ended on a dangling word -- they were probably still thinking.
            continuation_used = True
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
        # Attribute this reply to the last proactive nudge (if any) so the engine
        # learns whether that category is welcome -- before the turn is handled.
        opportunity_worker.record_feedback(
            state.current_user["id"] if state.current_user else None, question)
        if is_goodbye(question):
            set_status("idle", heard=question, reply="Goodbye.")
            speak("Goodbye.")
            return
        if not greeted:
            greeted = True
            _greet_and_surface(question)
            user_id = state.current_user["id"] if state.current_user else None
            transcript[:0] = memory_notes.recent_context(user_id, turns=config.CONTEXT_TURNS)
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
            low_conf_used = True
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
        barged = False
        while pending_question is not None:
            current_question = pending_question
            pending_question = None
            if is_goodbye(current_question):
                set_status("idle", heard=current_question, reply="Goodbye.")
                speak("Goodbye.")
                return
            kind, payload = router.route(current_question, state.current_user)
            _t = time.time()
            turn = metrics.TurnTimer()   # time-to-first-word starts at routing
            # Fill the wait before a slow answer starts (LLM reasoning or a
            # live-data fetch): only the slow kinds get a cue -- never an instant
            # built-in answer -- and it's killed the moment real speech begins,
            # so it never overlaps or delays the reply.
            cue = thinking_cue.ThinkingCue(kind) if kind in ("llm", "live") else None
            if cue:
                cue.start()
            try:
                if kind == "enroll":
                    def _record_fn(seconds, tag):
                        with capture.mic_session():
                            return record(seconds, denoise=False, tag=tag, stop_on_silence=True)
                    ok, msg = enroll_user(payload, _record_fn, speak_fn=speak)
                    answer = msg
                    set_status("speaking", heard=current_question, reply=answer)
                    _speak_checked(answer, current_question, turn=turn)
                elif kind == "llm":
                    system, user, followup = payload
                    if config.STREAM_TTS:
                        answer = speak_streaming(
                            utilities.with_context(system, transcript[-config.SESSION_VERBATIM_TURNS:],
                                                   session["summary"]),
                            user, current_question, cue=cue, turn=turn)
                    else:
                        answer = guard_output(current_question, chat(system, user))
                        cue.stop()
                        set_status("speaking", heard=current_question, reply=answer)
                        _speak_checked(answer, current_question, turn=turn)
                    if followup:
                        _offer_followup(followup)
                elif kind == "live":
                    tool, q2 = payload
                    data = live_data.fetch_live(tool, q2)
                    if tool.get("ground") and config.STREAM_TTS:
                        answer = speak_streaming(
                            router.ground_system(current_question, state.current_user) + data,
                            q2, current_question, cue=cue, turn=turn)
                    else:
                        answer = guard_output(current_question, data)
                        cue.stop()
                        set_status("speaking", heard=current_question, reply=answer)
                        _speak_checked(answer, current_question, turn=turn)
                    followup = tool.get("followup")
                    if followup:
                        _offer_followup(followup)
                else:
                    answer = guard_output(current_question, payload)
                    set_status("speaking", heard=current_question, reply=answer)
                    _speak_checked(answer, current_question, turn=turn)
            except _BargeIn as interruption:
                # Abandon the rest of this answer; answer the new question instead.
                pending_question = interruption.text
                barged = True
                continue
            finally:
                if cue:
                    cue.stop()   # idempotent safety net (early return, barge-in, error)
            if config.DEBUG_TIMING:
                print(f"  [time] answer {time.time() - _t:.1f}s")
            question = current_question   # so the bookkeeping below reflects what was actually answered
            state.last_answer = answer
            state.last_latency_ms = turn.first_word_ms or 0   # surfaced live on the face
            metrics.log_turn(
                heard=current_question, answer=answer, kind=kind,
                rms=int(rms), asr_ms=asr_ms, first_word_ms=turn.first_word_ms,
                answer_total_ms=int((time.time() - _t) * 1000),
                user=who, score=round(state.last_match_score, 3), speaker_cached=not needs_identify,
                barge_in=barged, low_conf=low_conf_used, continuation=continuation_used)
            stats["answered"] += 1
            barged = False
            transcript.append((question, answer))
            if (len(transcript) > config.SESSION_VERBATIM_TURNS
                    and len(transcript) % config.SESSION_SUMMARY_EVERY == 0):
                _spawn_summary_update(list(transcript), session)
        utilities.log_turn(question, answer)
        _remember_turn(question, answer)
        if not state.pending_followup:   # don't clobber an existing offer (e.g. weather_tomorrow)
            user_id = state.current_user["id"] if state.current_user else None
            opportunity_worker.enqueue(user_id, question, answer)
        set_status("idle", heard=question, reply=answer)
