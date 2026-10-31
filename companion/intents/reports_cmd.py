"""Transparency/trust reports: what left the device, what got blocked."""
import re

from ..core.types import Response
from ..llm.guardrails import guard_report
from ..skills import live_data
from .registry import registry


class PrivacyReportIntent:
    name = "privacy_report"
    priority = 100
    pattern = re.compile(r"\b(privacy report|network report|what have you sent|sent online|egress)\b")

    def matches(self, question, lowered, ctx):
        return bool(self.pattern.search(lowered))

    def handle(self, question, lowered, ctx):
        return Response("text", live_data.privacy_report())


class GuardReportIntent:
    name = "guard_report"
    priority = 110
    pattern = re.compile(r"\b(what (did|have) you block(ed)?|safety report|guard report)\b")

    def matches(self, question, lowered, ctx):
        return bool(self.pattern.search(lowered))

    def handle(self, question, lowered, ctx):
        return Response("text", guard_report())


registry.register(PrivacyReportIntent())
registry.register(GuardReportIntent())
