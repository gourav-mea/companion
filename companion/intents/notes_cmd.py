"""Explicit "remember that..." personal notes, scoped to a recognized speaker."""
import datetime
import re

from ..core.types import Response
from ..skills import memory_notes
from .registry import registry


class RememberNoteIntent:
    name = "remember_note"
    priority = 70
    pattern = re.compile(r"^(?:please\s+)?(?:remember|note|make a note)\b[:,]?\s*(?:that\s+)?(.+)")

    def matches(self, question, lowered, ctx):
        match = self.pattern.match(lowered)
        return bool(match and len(match.group(1).split()) >= 2)

    def handle(self, question, lowered, ctx):
        if ctx.speaker is None:
            return Response("text", "I can only remember personal notes for people I recognize. "
                                     "Try saying, enroll me as your name, first.")
        match = self.pattern.match(lowered)
        due_date = (datetime.date.today() + datetime.timedelta(days=1)).isoformat() \
            if "tomorrow" in match.group(1) else None
        memory_notes.remember_note(
            question.split(None, 1)[1] if " " in question else question,
            user_id=ctx.speaker["id"], due_date=due_date)
        return Response("text", "Noted." if not due_date else "Noted. I'll bring it up again tomorrow.")


registry.register(RememberNoteIntent())
