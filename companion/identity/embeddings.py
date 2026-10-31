"""Pluggable speaker-embedding backends, selected via config.SPEAKER_BACKEND.

Default is Resemblyzer (small, CPU-friendly). SpeechBrain's ECAPA-TDNN is kept
as a documented, heavier/more-accurate alternative behind the same interface
so swapping backends is a one-line config change.
"""
import math
import os

from .. import config


class EmbeddingBackend:
    def embed(self, wav_path):
        raise NotImplementedError


class ResemblyzerBackend(EmbeddingBackend):
    def __init__(self):
        from resemblyzer import VoiceEncoder, preprocess_wav
        self._preprocess_wav = preprocess_wav
        self._encoder = VoiceEncoder()

    def embed(self, wav_path):
        wav = self._preprocess_wav(wav_path)
        return self._encoder.embed_utterance(wav).tolist()


class SpeechBrainECAPABackend(EmbeddingBackend):
    """Heavier, more accurate alternative. Needs the `speechbrain` package."""

    def __init__(self):
        from speechbrain.inference.speaker import EncoderClassifier
        self._classifier = EncoderClassifier.from_hparams(
            source="speechbrain/spkrec-ecapa-voxceleb",
            savedir=os.path.join(config.WORK, "models", "ecapa"))

    def embed(self, wav_path):
        import torchaudio
        signal, _fs = torchaudio.load(wav_path)
        emb = self._classifier.encode_batch(signal)
        return emb.squeeze().detach().cpu().numpy().tolist()


_BACKENDS = {"resemblyzer": ResemblyzerBackend, "speechbrain_ecapa": SpeechBrainECAPABackend}
_backend_instance = None
_backend_tried = False


def get_backend():
    """Lazily construct and cache the configured backend (or None if missing)."""
    global _backend_instance, _backend_tried
    if _backend_tried:
        return _backend_instance
    _backend_tried = True
    cls = _BACKENDS.get(config.SPEAKER_BACKEND)
    if cls is None:
        print(f"speaker id: unknown backend '{config.SPEAKER_BACKEND}'")
        return None
    try:
        _backend_instance = cls()
    except Exception as exc:
        print(f"speaker id backend unavailable ({config.SPEAKER_BACKEND}): {exc}")
        _backend_instance = None
    return _backend_instance


def embed_voice(wav_path):
    """Return an embedding vector for the clip, or None if unavailable."""
    if not wav_path or not os.path.exists(wav_path):
        return None
    backend = get_backend()
    if backend is None:
        return None
    try:
        return backend.embed(wav_path)
    except Exception as exc:
        print("speaker embedding failed:", exc)
        return None


def cosine(a, b):
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0
