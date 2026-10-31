"""Wake-word detection: a lightweight Whisper-based check, plus an optional
openWakeWord streaming listener for lower latency/CPU.

`openwakeword_loop` takes its "what to do on wake" behaviour as a callback
instead of importing the conversation loop directly, so this module has no
dependency on the conversation layer (dependency inversion).
"""
import subprocess

from .. import config, state
from ..speech.transcriber import transcribe, wake_model
from . import capture

_oww_model = None
_oww_tried = False


def hears_wake():
    wav = capture.record(config.WAKE_SECONDS, denoise=False, tag="wake")
    if config.WAKE_ENERGY_GATE and capture.wav_rms(wav) < config.WAKE_RMS_THRESHOLD:
        import os
        try:
            os.remove(wav)
        except OSError:
            pass
        return False, ""
    text = transcribe(wav, wake_model()).lower()
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
        _oww_model = Model(wakeword_models=list(config.OWW_WAKE))
        print(f"wake: openWakeWord ({', '.join(config.OWW_WAKE)})")
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
        proc = subprocess.Popen(
            ["arecord", "-q", "-D", config.MIC_DEVICE, "-f", "S16_LE", "-r", "16000",
             "-c", "1", "-t", "raw"],
            stdout=subprocess.PIPE)
        detected = False
        try:
            while switch_enabled():
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
                if scores and max(scores.values()) >= config.OWW_THRESHOLD:
                    detected = True
                    break
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except Exception:
                pass
        if detected:
            try:
                model.reset()   # clear the detector's rolling buffer
            except Exception:
                pass
            on_wake()
    return True
