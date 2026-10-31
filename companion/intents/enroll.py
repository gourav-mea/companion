"""Self-service voice enrollment intents."""
import re

from ..core.types import Response
from .registry import registry


class EnrollNamedIntent:
    name = "enroll_named"
    priority = 10
    pattern = re.compile(r"\benroll me(?: as| called)?\s+([a-z][a-z \-']{1,30})\b")

    def matches(self, question, lowered, ctx):
        return bool(self.pattern.search(lowered))

    def handle(self, question, lowered, ctx):
        name = self.pattern.search(lowered).group(1).strip().title()
        return Response("enroll", name)


class EnrollPromptIntent:
    name = "enroll_prompt"
    priority = 20
    pattern = re.compile(r"\b(enroll me|recognize my voice|remember my voice|learn my voice)\b")

    def matches(self, question, lowered, ctx):
        return bool(self.pattern.search(lowered))

    def handle(self, question, lowered, ctx):
        return Response("text", "What name should I enroll you as? Say, enroll me as, followed by your name.")


registry.register(EnrollNamedIntent())
registry.register(EnrollPromptIntent())
