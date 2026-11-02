"""Wake-word detection: a lightweight Whisper-based check, plus an optional
openWakeWord streaming listener for lower latency/CPU.

`openwakeword_loop` takes its "what to do on wake" behaviour as a callback
instead of importing the conversation loop directly, so this module has no
dependency on the conversation layer (dependency inversion).
"""
import subprocess
import time

from .. import config, metrics, state
from ..speech.transcriber import transcribe, wake_model
from . import capture

_oww_model = None
_oww_tried = False


def hears_wake():
    with capture.mic_session():
        wav = capture.record(config.WAKE_SECONDS, denoise=False, tag="wake")
    if config.WAKE_ENERGY_GATE and capture.wav_rms(wav) < config.WAKE_RMS_THRESHOLD:
        import os
        try:
            os.remove(wav)
        except OSError:
            pass
        return False, ""
    text = transcribe(wav, wake_model()).lower()
    from ..skills.utilities import is_noise
    if is_noise(text):   # whisper's "(dramatic music)" inventions, not speech
        # Still reported so the caller can log what was captured -- hiding it
        # makes the device look deaf when it is actually hearing noise.
        return False, text.strip()
    return any(w in text for w in config.WAKE_WORDS), text.strip()


def get_oww():
    """Lazily load openWakeWord; cache the result (or None if unavailable)."""
    global _oww_model, _oww_tried
    if _oww_tried:
        return _oww_model
    _oww_tried = True
    if not config.USE_OPENWAKEWORD:
        return None
    try:
        from openwakeword.model import Model
        try:
            from openwakeword.utils import download_models
            download_models()   # no-op once the bundled models are cached
        except Exception:
            pass
        # The keyword changed across openwakeword releases, and passing an
        # unknown one surfaces as a confusing AudioFeatures TypeError.
        last_exc = None
        for attempt in (lambda: Model(wakeword_models=list(config.OWW_WAKE)),
                        lambda: Model(wakeword_model_names=list(config.OWW_WAKE)),
                        lambda: Model()):
            try:
                _oww_model = attempt()
                break
            except TypeError as exc:
                last_exc = exc
        if _oww_model is None and last_exc is not None:
            raise last_exc
        loaded = list(getattr(_oww_model, "models", {}) or config.OWW_WAKE)
        print(f"wake: openWakeWord ({', '.join(str(m) for m in loaded)})")
    except Exception as exc:
        print(f"openWakeWord unavailable ({exc}); using the Whisper wake word")
        _oww_model = None
    return _oww_model


def openwakeword_loop(switch_enabled, drain_reminders, on_wake):
    """Stream mic audio to openWakeWord and call on_wake() on detection.

    Returns False immediately if openWakeWord is not available, so the caller
    can fall back to the Whisper wake word. Returns True once the mute toggle
    turns off (the normal exit path).
    """
    model = get_oww()
    if model is None:
        return False
    import numpy as np
    while switch_enabled():
        # Someone else wants the mic (a question, an admin enrollment, or a
        # CLI command in another process): stay out of the way entirely
        # rather than fighting over a busy capture device.
        if capture.mic_requested():
            capture.announce_mic_released()
            while capture.mic_requested() and switch_enabled():
                time.sleep(0.1)
            continue
        proc = subprocess.Popen(
            ["arecord", "-q", "-D", config.MIC_DEVICE, "-f", "S16_LE", "-r", "16000",
             "-c", "1", "-t", "raw"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)   # terminate() => noisy EINTR otherwise
        if state.led and config.WAKE_LED_PULSE:
            # Pulse, not solid: record() uses solid for "capturing your question".
            state.led.blink(on_time=0.05, off_time=1.5, background=True)
        detected = False
        best_seen = 0.0
        last_report = time.time()
        try:
            while switch_enabled():
                if capture.mic_requested():
                    break   # drop the device; the outer loop signals and waits
                drain_reminders()
                if state.wake_event.is_set():
                    state.wake_event.clear()
                    detected = True
                    break
                data = proc.stdout.read(config.OWW_FRAME * 2)
                if not data or len(data) < config.OWW_FRAME * 2:
                    break
                frame = np.frombuffer(data, dtype=np.int16)
                scores = model.predict(frame)
                # Older/newer openwakeword builds may load the whole default
                # model set; scoring all of them wakes on "alexa" and friends.
                wanted = {k: v for k, v in (scores or {}).items()
                          if any(w in str(k) for w in config.OWW_WAKE)}
                scores = wanted or scores
                top = max(scores.values()) if scores else 0.0
                best_seen = max(best_seen, top)
                if config.WAKE_DEBUG and time.time() - last_report >= 3:
                    print(f"  [wake] best score in last 3s: {best_seen:.3f} "
                          f"(needs >= {config.OWW_THRESHOLD})")
                    best_seen = 0.0
                    last_report = time.time()
                if top >= config.OWW_THRESHOLD:
                    detected = True
                    break
        finally:
            if state.led and config.WAKE_LED_PULSE:
                state.led.off()
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except Exception:
                pass
        if detected:
            print(f"wake detected (score {best_seen:.2f})")
            metrics.log_wake(True, source="oww")
            try:
                model.reset()   # clear the detector's rolling buffer
            except Exception:
                pass
            on_wake()
    return True
