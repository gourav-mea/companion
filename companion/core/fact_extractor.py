"""Pluggable extraction of a durable personal fact from a conversational
turn -- lets "what's worth remembering and is it a correction" be swapped
(e.g. a smaller local classifier later) without touching the capture
pipeline that calls it (skills/memory_notes.maybe_capture_fact).
"""
from dataclasses import dataclass
from typing import Optional, Protocol


@dataclass
class FactCandidate:
    text: str
    attribute: Optional[str]   # a slot name like "name"/"home_city", or None for free-form
    update_mode: str           # "replace" (singular slot) or "append" (can have many values)
    confidence: float          # 0..1 -- gates auto-update vs ask-first in maybe_capture_fact


class FactExtractor(Protocol):
    def extract(self, question: str, answer: str) -> Optional[FactCandidate]: ...
