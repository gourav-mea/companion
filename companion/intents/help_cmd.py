"""Help text and "repeat that" voice commands."""
import re

from ..core.types import Response
from ..skills import utilities
from .registry import registry


class HelpIntent:
    name = "help"
    priority = 80
    pattern = re.compile(r"\b(help|what can you do|your features|what do you do)\b")

    def matches(self, question, lowered, ctx):
        return bool(self.pattern.search(lowered))

    def handle(self, question, lowered, ctx):
        return Response("text", utilities.help_text())


class RepeatLastIntent:
    name = "repeat_last"
    priority = 90
    pattern = re.compile(r"\b(say (that|it) again|repeat (that|it|again)|what did you say|come again)\b")

    def matches(self, question, lowered, ctx):
        return bool(self.pattern.search(lowered))

    def handle(self, question, lowered, ctx):
        return Response("text", utilities.repeat_last())


registry.register(HelpIntent())
registry.register(RepeatLastIntent())
