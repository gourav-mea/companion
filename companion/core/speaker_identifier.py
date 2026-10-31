"""Abstraction in front of "who is speaking" -- lets the voice-recognition
backend (Resemblyzer today, see identity/embeddings.py) be swapped without
touching the conversation loop.
"""
from typing import Optional, Protocol, Tuple


class SpeakerIdentifier(Protocol):
    def identify_speaker(self, wav_path: str) -> Tuple[Optional[dict], float]: ...
