"""Pluggable "is this worth proactively engaging on" judgment -- the
general-purpose reasoning step behind the proactive opportunity engine.
Swappable (e.g. a future smarter implementation) without touching the
worker/pipeline that calls it, same Strategy-pattern shape as FactExtractor.
"""
from dataclasses import dataclass, field
from typing import Optional, Protocol


@dataclass
class OpportunityDecision:
    has_opportunity: bool
    category: Optional[str] = None
    confidence: float = 0.0
    topic_summary: Optional[str] = None
    next_question: Optional[str] = None


@dataclass
class ThreadDecision:
    action: str   # "continue" | "ready" | "close" | "suppress"
    collected_info: dict = field(default_factory=dict)
    next_question: Optional[str] = None
    final_message: Optional[str] = None


class OpportunityPlanner(Protocol):
    def detect(self, question: str, answer: str) -> OpportunityDecision: ...

    def advance(self, thread: dict, question: str) -> ThreadDecision: ...
