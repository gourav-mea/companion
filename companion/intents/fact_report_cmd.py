"""Transparency: list everything currently remembered about the recognized
speaker (the counterpart to privacy_report/guard_report)."""
import re

from ..core.types import Response
from ..skills import memory_notes
from .registry import registry


class WhatDoYouKnowIntent:
    name = "what_do_you_know"
    priority = 105
    pattern = re.compile(r"\b(what do you know about me|what have you remembered|"
                         r"what do you remember about me)\b")

    def matches(self, question, lowered, ctx):
        return bool(self.pattern.search(lowered))

    def handle(self, question, lowered, ctx):
        if ctx.speaker is None:
            return Response("text", "I don't have anything remembered for you -- "
                                     "I don't recognize your voice yet.")
        facts = memory_notes.facts_for_user(ctx.speaker["id"])
        if not facts:
            return Response("text", "I don't have anything remembered about you yet.")
        return Response("text", "Here's what I know about you: " + "; ".join(facts))


registry.register(WhatDoYouKnowIntent())
