"""Microphone capture: recording a question/wake snippet and estimating its
loudness (used by the silence/noise gates elsewhere).
"""
import array
import os
import shutil
import subprocess
import wave

from .. import config, state


def record(seconds, denoise=True, tag="input", stop_on_silence=False):
    raw = os.path.join(config.WORK, f"{tag}_raw.wav")
    wav = os.path.join(config.WORK, f"{tag}.wav")
    for f in (raw, wav):
        if os.path.exists(f):
            try:
                os.remove(f)
            except OSError:
                pass
    # LED is the "I am hearing you" light: on only while the mic captures.
    if state.led:
        state.led.on()
    used_sox = False
    if stop_on_silence and config.SILENCE_STOP and shutil.which("sox"):
        # Starts when you speak, stops after SILENCE_SECS of quiet, capped at MAX.
        try:
            subprocess.run(
                ["sox", "-q", "-t", "alsa", config.MIC_DEVICE, "-r", "16000", "-c", "1", "-b", "16", raw,
                 "silence", "1", "0.1", config.SILENCE_THRESH, "1", str(config.SILENCE_SECS), config.SILENCE_THRESH,
                 "trim", "0", str(config.MAX_RECORD_SECONDS)],
                timeout=config.MAX_RECORD_SECONDS + 3, capture_output=True, check=False)
            used_sox = os.path.exists(raw)
        except Exception:
            used_sox = False
    if not used_sox:
        subprocess.run(["arecord", "-q", "-D", config.MIC_DEVICE, "-f", "S16_LE", "-r", "16000",
                        "-c", "1", "-d", str(seconds), raw], check=False)
    if state.led:
        state.led.off()
    if not denoise:
        return raw
    try:
        subprocess.run(["ffmpeg", "-y", "-i", raw, "-af", config.DENOISE, wav],
                       capture_output=True, check=False)
    except Exception:
        return raw
    if not os.path.exists(wav):
        return raw
    try:
        os.remove(raw)
    except OSError:
        pass
    return wav


def wav_rms(path):
    """Rough loudness of a 16-bit mono wav, for the silence gate. Pure Python."""
    try:
        with wave.open(path, "rb") as w:
            data = w.readframes(w.getnframes())
        if not data:
            return 0.0
        samples = array.array("h")
        samples.frombytes(data[: (len(data) // 2) * 2])
        if not samples:
            return 0.0
        step = max(1, len(samples) // 4000)   # subsample; an estimate is enough
        chosen = samples[::step]
        total = sum(s * s for s in chosen)
        return (total / len(chosen)) ** 0.5
    except Exception:
        return 1e9   # on error, don't gate -- fall through to transcription


def mic_onset(threshold):
    """True if a quick 1s sample is louder than threshold (for barge-in)."""
    try:
        out = subprocess.run(
            ["arecord", "-q", "-D", config.MIC_DEVICE, "-f", "S16_LE", "-r", "16000",
             "-c", "1", "-d", "1", "-t", "raw"],
            capture_output=True, timeout=1.5).stdout
    except Exception:
        return False
    if not out:
        return False
    samples = array.array("h")
    samples.frombytes(out[: (len(out) // 2) * 2])
    if not samples:
        return False
    step = max(1, len(samples) // 2000)
    chosen = samples[::step]
    rms = (sum(x * x for x in chosen) / len(chosen)) ** 0.5
    return rms > threshold


def _rms_from_raw(data):
    samples = array.array("h")
    samples.frombytes(data[: (len(data) // 2) * 2])
    if not samples:
        return 0.0
    step = max(1, len(samples) // 2000)
    chosen = samples[::step]
    return (sum(x * x for x in chosen) / len(chosen)) ** 0.5


def wait_for_barge_in(threshold, chunk_ms, confirm_chunks, is_active):
    """Stream the mic in small chunks while `is_active()` is True; return True
    as soon as `confirm_chunks` *consecutive* chunks exceed `threshold` (a
    real, sustained interruption -- not a single spike, which filters out
    transients and some echo/self-noise), or False once `is_active()` goes
    False (playback finished on its own first).
    """
    rate = 16000
    bytes_per_chunk = int(rate * (chunk_ms / 1000.0)) * 2   # 16-bit mono
    proc = subprocess.Popen(
        ["arecord", "-q", "-D", config.MIC_DEVICE, "-f", "S16_LE", "-r", str(rate),
         "-c", "1", "-t", "raw"], stdout=subprocess.PIPE)
    consecutive = 0
    try:
        while is_active():
            data = proc.stdout.read(bytes_per_chunk)
            if not data or len(data) < bytes_per_chunk:
                break
            if _rms_from_raw(data) > threshold:
                consecutive += 1
                if consecutive >= confirm_chunks:
                    return True
            else:
                consecutive = 0
        return False
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except Exception:
            pass
