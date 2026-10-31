"""Voice-set FX crossing watches ("notify me if USD to INR crosses 85")."""
import re

from ..core.types import Response
from ..skills import watches
from .registry import registry


class FxWatchIntent:
    name = "fx_watch"
    priority = 180
    pattern = re.compile(r"\b(?:notify|tell|alert|let) me (?:know )?(?:if|when)\s+"
                         r"([a-z]{3})\s+to\s+([a-z]{3})\s+cross(?:es)?\s+(\d+(?:\.\d+)?)\b")

    def matches(self, question, lowered, ctx):
        return bool(self.pattern.search(lowered))

    def handle(self, question, lowered, ctx):
        match = self.pattern.search(lowered)
        base, target, threshold = match.group(1).upper(), match.group(2).upper(), float(match.group(3))
        user_id = ctx.speaker["id"] if ctx.speaker else None
        return Response("text", watches.create_watch(base, target, threshold, user_id=user_id))


registry.register(FxWatchIntent())
