"""Current time/date voice queries."""
import re

from ..core.types import Response
from ..skills import utilities
from .registry import registry


class TimeDateIntent:
    name = "time_date"
    priority = 210
    pattern = re.compile(r"(what(?:'s| is)?(?: the)?(?: current)? (time|date)|what time is it|"
                         r"time now|current (time|date)|today'?s? date|what day (is|it)|"
                         r"which day|day of the week|tell me the (time|date)|"
                         r"do you (know|have) the (time|date))")

    def matches(self, question, lowered, ctx):
        return bool(self.pattern.search(lowered))

    def handle(self, question, lowered, ctx):
        return Response("text", utilities.get_time_date(question))


registry.register(TimeDateIntent())
