"""Explicit retraction: "forget that I said my name is..." / "forget my X"."""
import re

from ..core.types import Response
from ..skills import memory_notes
from .registry import registry


class ForgetFactIntent:
    name = "forget_fact"
    priority = 75
    pattern = re.compile(r"^(?:please\s+)?forget\b[:,]?\s*(?:that\s+|about\s+|my\s+)?(.*)$")

    def matches(self, question, lowered, ctx):
        return bool(self.pattern.match(lowered))

    def handle(self, question, lowered, ctx):
        if ctx.speaker is None:
            return Response("text", "I don't have anything remembered for you to forget.")
        hint = self.pattern.match(lowered).group(1).strip()
        forgotten = memory_notes.forget_fact(ctx.speaker["id"], hint)
        if forgotten:
            return Response("text", f"Okay, I've forgotten: {forgotten}")
        return Response("text", "I couldn't find anything matching that to forget.")


registry.register(ForgetFactIntent())
