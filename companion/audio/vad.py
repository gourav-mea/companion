"""Speech-presence detection (VAD): decides whether a recorded clip actually
contains *speech*, as opposed to wind, laughter, music or a TV in the
background.

This exists because whisper confidently hallucinates plausible sentences
when fed non-speech audio ("Thanks for watching!", "Bye." etc) -- a loudness
(RMS) check alone can't tell a gust of wind from a spoken question, since
both are simply "loud". webrtcvad is a small, fast, purpose-built classifier
that can.

Fails OPEN on any problem (missing library, odd wav format, decode error):
if we can't tell, we let the clip through to transcription rather than
silently swallowing a real question.
"""
import array
import wave

from .. import config

_vad = None
_tried = False


def _get_vad():
    """Lazily construct the detector; cache None if unavailable so a missing
    library costs one failed import, not one per turn."""
    global _vad, _tried
    if _tried:
        return _vad
    _tried = True
    if not config.VAD_ENABLED:
        return None
    try:
        import webrtcvad
        _vad = webrtcvad.Vad(config.VAD_AGGRESSIVENESS)
    except Exception as exc:
        print(f"webrtcvad unavailable ({exc}); falling back to the RMS noise gate only")
        _vad = None
    return _vad


def speech_stats(path):
    """(speech_ratio, speech_seconds) for a 16-bit mono wav.

    Returns (1.0, large) -- i.e. "definitely speech" -- if VAD can't run, so
    callers fail open.
    """
    vad = _get_vad()
    if vad is None:
        return 1.0, 1e9
    try:
        with wave.open(path, "rb") as w:
            if w.getnchannels() != 1 or w.getsampwidth() != 2:
                return 1.0, 1e9
            rate = w.getframerate()
            if rate not in (8000, 16000, 32000, 48000):   # webrtcvad only supports these
                return 1.0, 1e9
            data = w.readframes(w.getnframes())
    except Exception:
        return 1.0, 1e9

    frame_bytes = int(rate * (config.VAD_FRAME_MS / 1000.0)) * 2
    if frame_bytes <= 0 or len(data) < frame_bytes:
        return 0.0, 0.0
    total = voiced = 0
    for start in range(0, len(data) - frame_bytes + 1, frame_bytes):
        frame = data[start:start + frame_bytes]
        try:
            if vad.is_speech(frame, rate):
                voiced += 1
        except Exception:
            return 1.0, 1e9
        total += 1
    if not total:
        return 0.0, 0.0
    return voiced / total, voiced * (config.VAD_FRAME_MS / 1000.0)


def has_speech(path):
    """True if the clip holds enough speech to be worth transcribing."""
    if not config.VAD_ENABLED:
        return True
    ratio, seconds = speech_stats(path)
    return ratio >= config.VAD_MIN_SPEECH_RATIO and seconds >= config.VAD_MIN_SPEECH_SECONDS


def ambient_rms_sample(seconds=2):
    """Record a short clip and report its loudness -- used by the
    `talk.py calibrate-noise` command to recommend a room-specific
    QUESTION_RMS_THRESHOLD instead of guessing a number."""
    import os
    from .capture import record, wav_rms
    wav = record(seconds, denoise=True, tag="calibrate")
    try:
        return wav_rms(wav), speech_stats(wav)[0]
    finally:
        if os.path.exists(wav):
            try:
                os.remove(wav)
            except OSError:
                pass
