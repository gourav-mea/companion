"""Transparency: list recent memory corrections for the recognized speaker."""
import re

from ..core.types import Response
from ..skills import memory_notes
from .registry import registry


class ConflictHistoryIntent:
    name = "conflict_history"
    priority = 115
    pattern = re.compile(r"\b(what have you corrected|memory correction|what did you update about me)\b")

    def matches(self, question, lowered, ctx):
        return bool(self.pattern.search(lowered))

    def handle(self, question, lowered, ctx):
        if ctx.speaker is None:
            return Response("text", "I don't have any corrections on record for you.")
        history = memory_notes.conflict_history(ctx.speaker["id"])
        if not history:
            return Response("text", "I haven't had to correct anything about you.")
        parts = [f"{h['old_text']} became {h['new_text']}" for h in history]
        return Response("text", "Here's what I've updated: " + "; ".join(parts))


registry.register(ConflictHistoryIntent())
