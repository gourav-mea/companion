"""The "good morning" voice macro (date + weather briefing on demand)."""
import re

from ..core.types import Response
from ..skills import macros
from .registry import registry


class GoodMorningIntent:
    name = "good_morning"
    priority = 190
    pattern = re.compile(r"\bgood morning\b")

    def matches(self, question, lowered, ctx):
        return bool(self.pattern.search(lowered))

    def handle(self, question, lowered, ctx):
        return Response("text", macros.run_macro("morning"))


registry.register(GoodMorningIntent())
