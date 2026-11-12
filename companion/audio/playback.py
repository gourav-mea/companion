"""Text-to-speech: Piper (natural voice) with an espeak-ng fallback, played
through PulseAudio/PipeWire (needed for Bluetooth) or plain ALSA.
"""
import os
import shutil
import subprocess
import threading
import time

from .. import accessibility, config, metrics
from . import capture

_speak_lock = threading.Lock()
# Public alias: the thinking cue holds this while playing a filler clip so it
# obeys the same "serialise all speech" invariant speak() relies on.
SPEAK_LOCK = _speak_lock


def piper_ready():
    binary = config.PIPER_BIN if os.path.exists(config.PIPER_BIN) else shutil.which("piper")
    if binary and os.path.exists(config.PIPER_MODEL):
        return binary
    return None


def pulse_player():
    """pw-play/paplay reach PipeWire/PulseAudio sinks, including Bluetooth."""
    if config.AUDIO_OUT == "alsa":
        return None
    return shutil.which("pw-play") or shutil.which("paplay")


def _aplay_cmd(extra):
    cmd = ["aplay", "-q"]
    if config.APLAY_DEVICE:
        cmd += ["-D", config.APLAY_DEVICE]
    return cmd + extra


def _play_raw(stdin_src, rate):
    """Play raw mono s16le coming from stdin_src through the chosen output."""
    player = pulse_player()
    if player and player.endswith("pw-play"):
        # --raw is required: Piper emits headerless PCM, so pw-play must be told
        # not to expect a WAV/sound-file header ("format not recognized" otherwise).
        cmd = [player, "--raw", f"--rate={rate}", "--channels=1", "--format=s16"]
        if config.PULSE_SINK:
            cmd += [f"--target={config.PULSE_SINK}"]
        cmd += ["-"]
    elif player:
        cmd = [player, "--raw", f"--rate={rate}", "--format=s16le", "--channels=1"]
        if config.PULSE_SINK:
            cmd += [f"--device={config.PULSE_SINK}"]
    else:
        cmd = _aplay_cmd(["-r", str(rate), "-f", "S16_LE", "-t", "raw", "-"])
    return subprocess.Popen(cmd, stdin=stdin_src)


def _file_player_cmd(path):
    """The command to play a WAV file through the configured output/sink."""
    player = pulse_player()
    if player:
        cmd = [player]
        if config.PULSE_SINK:
            cmd += [f"--target={config.PULSE_SINK}"] if player.endswith("pw-play") else [f"--device={config.PULSE_SINK}"]
        cmd += [path]
        return cmd
    return _aplay_cmd([path])


def start_file_player(path):
    """Start playing a WAV file and return the player process WITHOUT waiting
    (no barge-in handling). The caller owns the process and must wait() or
    terminate() it -- used by the thinking cue, which has to be killable the
    instant the real answer is ready."""
    return subprocess.Popen(_file_player_cmd(path))


def _voice_settings():
    """(model_path, length_scale) for Piper. A failed preference lookup (e.g. a
    locked database) falls back to the defaults instead of raising -- an exception
    here used to push the whole utterance onto the robotic espeak voice."""
    try:
        model_path = accessibility.piper_model_for_lang()
    except Exception:
        model_path = config.PIPER_MODEL
    try:
        scale = accessibility.get_speech_scale()
    except Exception:
        scale = 1.0
    return model_path, scale


def _budget_seconds(words):
    """Ceiling on how long `words` words of speech may keep playing before we cut
    them off: a floor plus a per-word allowance, scaled up for a slower speech rate.
    It exists to catch a genuinely hung player, not to cut long answers short."""
    scale = max(1.0, _voice_settings()[1])
    return max(config.TTS_MIN_WAIT_SECONDS, 15 + words * config.TTS_SECONDS_PER_WORD * scale)


def _stop_procs(*procs):
    """Terminate any still-running subprocess so a failed or timed-out utterance
    never keeps talking in the background -- a leftover Piper voice playing under
    a second voice is exactly what made the answer sound doubled."""
    for p in procs:
        if p is not None and p.poll() is None:
            try:
                p.terminate()
            except Exception:
                pass
    for p in procs:
        if p is not None:
            try:
                p.wait(timeout=2)
            except Exception:
                try:
                    p.kill()
                except Exception:
                    pass


def render_to_wav(text, path, allow_espeak=True):
    """Render `text` to a WAV at `path` in the companion voice (Piper, else
    espeak-ng when allow_espeak). Returns `path` on success or None. Used to
    pre-render the thinking-cue phrases once so they play back instantly later;
    those pass allow_espeak=False so a cue is never spoken in a different voice
    from the answer that follows it."""
    binary = piper_ready()
    if binary:
        try:
            model_path, scale = _voice_settings()
            subprocess.run([binary, "--model", model_path, "--output_file", path,
                            "--length-scale", f"{scale:g}"],
                           input=text.encode("utf-8"), check=False)
            if os.path.exists(path) and os.path.getsize(path) > 0:
                return path
        except Exception as exc:
            print("piper cue render failed:", exc)
    if not allow_espeak:
        return None
    try:
        rate = str(int(int(config.ESPEAK_RATE) / accessibility.get_speech_scale()))
        voice = config.ESPEAK_LANG_VOICE.get(accessibility.get_lang(), config.ESPEAK_VOICE)
        subprocess.run(["espeak-ng", "-v", voice, "-s", rate, "-w", path, text], check=False)
        if os.path.exists(path) and os.path.getsize(path) > 0:
            return path
    except Exception:
        pass
    return None


def _play_file_interruptible(path, timeout=None):
    """Barge-in-aware file playback (used by the espeak fallback, so
    interruption works even without Piper installed). Returns whether
    playback was cut short by a confirmed interruption."""
    try:
        proc = subprocess.Popen(_file_player_cmd(path))
        return _bargein_wait(proc, timeout)
    except Exception:
        return False


def _bt_reconnect():
    if not config.BT_MAC:
        return
    try:
        subprocess.run(["bluetoothctl", "connect", config.BT_MAC],
                       capture_output=True, timeout=10, check=False)
    except Exception:
        pass


def _bargein_wait(player, timeout=None):
    """Wait for playback to finish. Returns True if it was cut short by a
    confirmed (sustained, debounced) interruption, False if it finished on
    its own. `timeout` is a ceiling for a hung player; a player that outlives it
    is stopped, never left running."""
    if not config.BARGE_IN_ENABLED:
        try:
            player.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            print("playback exceeded its time budget; stopping it")
            metrics.log_tts_issue("cutoff", f"playback exceeded {timeout}s")
            player.terminate()
        return False
    interrupted = capture.wait_for_barge_in(
        config.BARGE_IN_RMS, config.BARGE_IN_CHUNK_MS, config.BARGE_IN_CONFIRM_CHUNKS,
        is_active=lambda: player.poll() is None)
    if interrupted:
        player.terminate()
    try:
        player.wait(timeout=2)
    except Exception:
        pass
    return interrupted


def speak(text):
    """Speak `text`. Returns True if a confirmed interruption cut it short
    (only meaningful when config.BARGE_IN_ENABLED; always False otherwise)."""
    # Serialise speech so a background timer can't talk over a live reply.
    with _speak_lock:
        return _speak_once(text)


_EARLY_CRASH_SECONDS = 3.0   # Piper dying this soon after start produced no usable audio


def _speak_piper(binary, text):
    """Speak `text` with Piper. Returns (handled, interrupted).

    `handled` is False ONLY when Piper failed before it could have produced audio
    (it could not start, or it crashed right away) -- the one case where saying the
    same text again in the espeak voice is safe. Once audio may be playing, any
    failure or timeout stops the playback and is NOT retried in another voice:
    that retry is what produced a second, robotic voice over the first. Whatever
    happens, no Piper/player process is left running when this returns.
    """
    piper = player = None
    t0 = time.time()
    try:
        model_path, scale = _voice_settings()
        piper = subprocess.Popen(
            [binary, "--model", model_path, "--output-raw", "--length-scale", f"{scale:g}"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        player = _play_raw(piper.stdout, 22050)
        piper.stdout.close()
        piper.stdin.write(text.encode("utf-8"))
        piper.stdin.close()
        interrupted = _bargein_wait(player, timeout=_budget_seconds(len(text.split())))
        try:
            piper.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass   # the audio already played; the finally below ends the process
        # Piper played; report success unconditionally. pw-play can report a non-zero
        # code on Bluetooth even after playing fine.
        return True, interrupted
    except Exception as exc:
        crashed_early = (piper is None or (
            (time.time() - t0) < _EARLY_CRASH_SECONDS and piper.poll() not in (None, 0)))
        if crashed_early:
            print("piper failed before speaking:", exc)
            return False, False
        print("piper stopped mid-playback (not repeating it in another voice):", exc)
        metrics.log_tts_issue("cutoff", exc)
        return True, False
    finally:
        _stop_procs(player, piper)


def _speak_once(text):
    print("Companion:", text)
    binary = piper_ready()
    if binary:
        handled, interrupted = _speak_piper(binary, text)
        if handled:
            return interrupted
        _bt_reconnect()
        metrics.log_tts_issue("fallback", "piper failed before producing audio")
        print("using espeak instead")
    # espeak fallback: render to a temp wav, then play through the same output.
    try:
        tmp = os.path.join("/dev/shm" if os.path.isdir("/dev/shm") else config.WORK, "tts.wav")
        rate = str(int(int(config.ESPEAK_RATE) / accessibility.get_speech_scale()))
        voice = config.ESPEAK_LANG_VOICE.get(accessibility.get_lang(), config.ESPEAK_VOICE)
        subprocess.run(["espeak-ng", "-v", voice, "-s", rate, "-w", tmp, text],
                       check=False)
        if os.path.exists(tmp):
            interrupted = _play_file_interruptible(tmp, timeout=_budget_seconds(len(text.split())))
            try:
                os.remove(tmp)
            except OSError:
                pass
            return interrupted
    except Exception:
        pass
    return False


class StreamingSpeaker:
    """EXPERIMENTAL (config.PIPER_REUSE_PROCESS, off by default): keeps one
    Piper process + one player process alive across an entire streamed
    answer instead of spawning fresh ones per sentence, cutting per-sentence
    process-startup overhead to once per answer.

    This relies on Piper synthesizing each stdin line as it arrives rather
    than buffering until EOF -- unverified against a real Piper build, so it
    only activates when explicitly enabled, and only when barge-in is off
    (barge-in needs the per-sentence speak() interruption checkpoint this
    bypasses). Falls back cleanly: if construction or any write fails, `ok`
    is False and the caller should use the normal per-sentence speak().
    """

    def __init__(self):
        self.ok = False
        self._piper = None
        self._player = None
        self._words = 0   # spoken so far, to size the playback ceiling in close()
        if config.BARGE_IN_ENABLED or not config.PIPER_REUSE_PROCESS:
            return
        binary = piper_ready()
        if not binary:
            return
        try:
            model_path, scale = _voice_settings()
            self._piper = subprocess.Popen(
                [binary, "--model", model_path, "--output-raw", "--length-scale", f"{scale:g}"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE)
            self._player = _play_raw(self._piper.stdout, 22050)
            self._piper.stdout.close()
            self.ok = True
        except Exception as exc:
            print("piper process reuse unavailable, falling back per-sentence:", exc)
            self.ok = False

    def say(self, sentence):
        """Returns True if the sentence was handed to the shared process;
        False means the caller should fall back to per-sentence speak()."""
        if not self.ok:
            return False
        try:
            self._piper.stdin.write((sentence.strip() + "\n").encode("utf-8"))
            self._piper.stdin.flush()
            self._words += len(sentence.split())
            return True
        except Exception as exc:
            print("piper stream write failed, falling back per-sentence:", exc)
            self.ok = False
            return False

    def close(self):
        if not self.ok:
            return
        try:
            self._piper.stdin.close()
        except Exception:
            pass
        try:
            self._player.wait(timeout=_budget_seconds(self._words))
        except subprocess.TimeoutExpired:
            print("streamed playback exceeded its time budget; stopping it")
            metrics.log_tts_issue("cutoff", "streamed playback exceeded its budget")
        except Exception:
            pass
        try:
            self._piper.wait(timeout=5)
        except Exception:
            pass
        _stop_procs(self._player, self._piper)   # never leave a voice running behind the answer


class PiperTTSEngine:
    """Implements core.tts_engine.TTSEngine by delegating to speak() above --
    lets the voice engine be swapped behind the Protocol without touching
    callers that depend on the abstraction instead of this module directly."""

    def speak(self, text):
        speak(text)


engine = PiperTTSEngine()
