"""Set a timer/alarm, and record the hour for habit-based proactive suggestions."""
import re

from ..core.types import Response
from ..proactive import triggers as proactive_triggers
from ..skills import utilities
from .registry import registry


class SetTimerIntent:
    name = "set_timer"
    priority = 200
    pattern = re.compile(r"\b(set (a |an )?(timer|alarm)|remind me in)\b")

    def matches(self, question, lowered, ctx):
        return bool(self.pattern.search(lowered))

    def handle(self, question, lowered, ctx):
        proactive_triggers.record_timer_usage()
        return Response("text", utilities.set_timer_cmd(question))


registry.register(SetTimerIntent())
