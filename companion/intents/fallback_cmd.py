"""The final fallback: always matches, so dispatch() never returns None."""
from ..core.types import Response
from ..llm.prompts import brain_system
from .registry import registry


class FallbackLLMIntent:
    name = "fallback_llm"
    priority = 1000

    def matches(self, question, lowered, ctx):
        return True

    def handle(self, question, lowered, ctx):
        return Response("llm", (brain_system(question, ctx.speaker), question, None))


registry.register(FallbackLLMIntent())
