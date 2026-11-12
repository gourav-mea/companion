"""Microphone capture: recording a question/wake snippet and estimating its
loudness (used by the silence/noise gates elsewhere).
"""
import array
import contextlib
import os
import shutil
import subprocess
import time
import wave

from .. import config, state


def _unlink(path):
    try:
        os.remove(path)
    except OSError:
        pass


def _pid_alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ValueError):
        return False


def _lock_owner():
    """PID currently claiming the mic, or None. Clears locks left behind by
    a dead process so a crash can't deafen the wake word forever."""
    try:
        with open(config.MIC_LOCK_FILE) as f:
            pid = int((f.read() or "0").strip())
    except (OSError, ValueError):
        return None
    if pid and not _pid_alive(pid):
        _unlink(config.MIC_LOCK_FILE)
        return None
    return pid or None


def mic_requested():
    """True if *another* process wants the capture device.

    Our own lock is ignored on purpose: inside one process the threading
    lock already serialises access, and the service's own wake-word
    recordings must not look like an external takeover request.
    """
    if state.mic_pause.is_set():
        return True
    owner = _lock_owner()
    return owner is not None and owner != os.getpid()


def announce_mic_released():
    """Called by the wake-word listener once it has actually dropped the
    device, so the waiting recorder can proceed."""
    state.mic_released.set()
    try:
        with open(config.MIC_RELEASED_FILE, "w") as f:
            f.write(str(os.getpid()))
    except OSError:
        pass


@contextlib.contextmanager
def mic_session(timeout=None):
    """Exclusive use of the capture device.

    The wake-word listener streams from the mic continuously, so a second
    recorder would hit a busy ALSA device and capture nothing. This asks the
    listener to let go first -- via both a threading Event (same process) and
    a PID-stamped lock file (CLI commands run in a separate process from the
    always-on service).
    """
    timeout = config.MIC_HANDOVER_TIMEOUT if timeout is None else timeout
    owner = _lock_owner()
    external = owner is not None and owner != os.getpid()
    _unlink(config.MIC_RELEASED_FILE)
    state.mic_released.clear()
    state.mic_pause.set()
    if not external:
        try:
            with open(config.MIC_LOCK_FILE, "w") as f:
                f.write(str(os.getpid()))
        except OSError:
            pass
    deadline = time.time() + timeout
    handed_over = False
    while time.time() < deadline:
        if state.mic_released.is_set() or os.path.exists(config.MIC_RELEASED_FILE):
            handed_over = True
            break
        time.sleep(0.05)
    if handed_over:
        time.sleep(config.MIC_SETTLE_SECONDS)   # ALSA needs a beat to actually free the device
    elif external:
        print(f"mic: another process (pid {owner}) is holding the device and did not release it")
    elif config.DEBUG_TIMING:
        print("mic: no handover signal (nothing else was listening)")
    try:
        with state.mic_lock:
            yield
    finally:
        if not external and _lock_owner() == os.getpid():
            _unlink(config.MIC_LOCK_FILE)
        _unlink(config.MIC_RELEASED_FILE)
        state.mic_pause.clear()
        state.mic_released.clear()


def _run_capture(cmd, timeout):
    """Run a capture subprocess, registering it so an instant mute can kill it.

    Returns (returncode, stderr_bytes). A mute-triggered terminate() just looks
    like a short/empty recording to the caller, which the listening loops
    discard once they notice state.ui_muted.
    """
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except Exception as exc:
        return 1, str(exc).encode("utf-8", "replace")
    state.register_capture(proc)
    try:
        _out, err = proc.communicate(timeout=timeout)
        return proc.returncode, err
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        return proc.returncode, b""
    finally:
        state.register_capture(None)


def record(seconds, denoise=True, tag="input", stop_on_silence=False, keep_raw=False):
    """Record a clip; returns the path, or (denoised, raw) when keep_raw.

    The denoise filter is a 200-3000Hz band-pass: good for speech recognition,
    bad for speaker identification (it strips the upper harmonics voice
    embeddings depend on), which is why callers doing speaker ID ask for the
    raw file instead.
    """
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
    try:
        if stop_on_silence and config.SILENCE_STOP and shutil.which("sox"):
            # Starts when you speak, stops after SILENCE_SECS of quiet, capped at MAX.
            rc, stderr = _run_capture(
                ["sox", "-q", "-t", "alsa", config.MIC_DEVICE, "-r", "16000", "-c", "1", "-b", "16", raw,
                 "silence", "1", "0.1", config.SILENCE_THRESH, "1", str(config.SILENCE_SECS), config.SILENCE_THRESH,
                 "trim", "0", str(config.MAX_RECORD_SECONDS)],
                timeout=config.MAX_RECORD_SECONDS + 3)
            used_sox = os.path.exists(raw)
            if not used_sox and stderr:
                # A busy capture device looks exactly like silence otherwise.
                print("sox capture failed:", stderr.decode("utf-8", "replace").strip()[:200])
        if not used_sox:
            rc, stderr = _run_capture(
                ["arecord", "-q", "-D", config.MIC_DEVICE, "-f", "S16_LE", "-r", "16000",
                 "-c", "1", "-d", str(seconds), raw],
                timeout=seconds + 3)
            if not os.path.exists(raw) and stderr:
                print("arecord capture failed:", stderr.decode("utf-8", "replace").strip()[:200])
    finally:
        if state.led:
            state.led.off()
    if not denoise:
        return (raw, raw) if keep_raw else raw
    try:
        subprocess.run(["ffmpeg", "-y", "-i", raw, "-af", config.DENOISE, wav],
                       capture_output=True, check=False)
    except Exception:
        return (raw, raw) if keep_raw else raw
    if not os.path.exists(wav):
        return (raw, raw) if keep_raw else raw
    if keep_raw:
        return wav, raw
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
         "-c", "1", "-t", "raw"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
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
