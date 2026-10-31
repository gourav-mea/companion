"""Text-to-speech: Piper (natural voice) with an espeak-ng fallback, played
through PulseAudio/PipeWire (needed for Bluetooth) or plain ALSA.
"""
import os
import shutil
import subprocess
import threading
import time

from .. import accessibility, config
from . import capture

_speak_lock = threading.Lock()


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


def _play_file_interruptible(path):
    """Barge-in-aware file playback (used by the espeak fallback, so
    interruption works even without Piper installed). Returns whether
    playback was cut short by a confirmed interruption."""
    player = pulse_player()
    try:
        if player:
            cmd = [player]
            if config.PULSE_SINK:
                cmd += [f"--target={config.PULSE_SINK}"] if player.endswith("pw-play") else [f"--device={config.PULSE_SINK}"]
            cmd += [path]
        else:
            cmd = _aplay_cmd([path])
        proc = subprocess.Popen(cmd)
        return _bargein_wait(proc)
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


def _bargein_wait(player):
    """Wait for playback to finish. Returns True if it was cut short by a
    confirmed (sustained, debounced) interruption, False if it finished on
    its own."""
    if not config.BARGE_IN_ENABLED:
        player.wait(timeout=30)
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


def _speak_once(text):
    print("Companion:", text)
    binary = piper_ready()
    if binary:
        try:
            model_path = accessibility.piper_model_for_lang()
            scale = accessibility.get_speech_scale()
            piper = subprocess.Popen(
                [binary, "--model", model_path, "--output-raw", "--length-scale", f"{scale:g}"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE)
            player = _play_raw(piper.stdout, 22050)
            piper.stdout.close()
            piper.stdin.write(text.encode("utf-8"))
            piper.stdin.close()
            interrupted = _bargein_wait(player)
            piper.wait(timeout=5)
            # Piper played; return unconditionally. pw-play can report a non-zero
            # code on Bluetooth even after playing fine -- falling back to espeak
            # here caused a second, robotic voice over the top.
            return interrupted
        except Exception as exc:
            print("piper failed, using espeak:", exc)
            _bt_reconnect()
    # espeak fallback: render to a temp wav, then play through the same output.
    try:
        tmp = os.path.join("/dev/shm" if os.path.isdir("/dev/shm") else config.WORK, "tts.wav")
        rate = str(int(int(config.ESPEAK_RATE) / accessibility.get_speech_scale()))
        voice = config.ESPEAK_LANG_VOICE.get(accessibility.get_lang(), config.ESPEAK_VOICE)
        subprocess.run(["espeak-ng", "-v", voice, "-s", rate, "-w", tmp, text],
                       check=False)
        if os.path.exists(tmp):
            interrupted = _play_file_interruptible(tmp)
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
        if config.BARGE_IN_ENABLED or not config.PIPER_REUSE_PROCESS:
            return
        binary = piper_ready()
        if not binary:
            return
        try:
            model_path = accessibility.piper_model_for_lang()
            scale = accessibility.get_speech_scale()
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
            self._player.wait(timeout=30)
        except Exception:
            pass
        try:
            self._piper.wait(timeout=5)
        except Exception:
            pass


class PiperTTSEngine:
    """Implements core.tts_engine.TTSEngine by delegating to speak() above --
    lets the voice engine be swapped behind the Protocol without touching
    callers that depend on the abstraction instead of this module directly."""

    def speak(self, text):
        speak(text)


engine = PiperTTSEngine()
