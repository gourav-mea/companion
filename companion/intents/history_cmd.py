"""Clear or summarise the conversation transcript."""
import re

from ..core.types import Response
from ..skills import utilities
from .registry import registry


class ClearHistoryIntent:
    name = "clear_history"
    priority = 50
    pattern = re.compile(r"\b(clear|delete|wipe|erase)\b.*(conversation|chat|history|transcript)")

    def matches(self, question, lowered, ctx):
        return bool(self.pattern.search(lowered))

    def handle(self, question, lowered, ctx):
        return Response("text", utilities.clear_history())


class SummarizeIntent:
    name = "summarize"
    priority = 60
    pattern = re.compile(r"\b(summari[sz]e|recap|sum up)\b.*(conversation|chat|discussion|talk|we)")

    def matches(self, question, lowered, ctx):
        return bool(self.pattern.search(lowered))

    def handle(self, question, lowered, ctx):
        return Response("text", utilities.summarise())


registry.register(ClearHistoryIntent())
registry.register(SummarizeIntent())
