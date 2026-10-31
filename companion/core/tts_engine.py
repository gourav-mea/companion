"""Abstraction in front of text-to-speech playback (Piper/espeak today, see
audio/playback.py) -- lets the voice engine be swapped without touching
callers that just want to speak a sentence.
"""
from typing import Protocol


class TTSEngine(Protocol):
    def speak(self, text: str) -> None: ...
