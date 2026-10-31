"""Recurring daily alarms ("remind me every day at 7am", "set a daily alarm
for 7:30am"). Runs before timer_cmd.py's SetTimerIntent (priority 200) so a
"daily"/"every day" phrased alarm isn't misread as a one-shot timer.
"""
import re

from ..core.types import Response
from ..skills import alarms
from .registry import registry

_TIME_PATTERN = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b")


def _parse_time(text):
    m = _TIME_PATTERN.search(text)
    if not m:
        return None
    hour = int(m.group(1))
    minute = int(m.group(2) or 0)
    ampm = m.group(3)
    if ampm == "pm" and hour != 12:
        hour += 12
    if ampm == "am" and hour == 12:
        hour = 0
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour, minute


class SetRecurringAlarmIntent:
    name = "set_recurring_alarm"
    priority = 195

    def matches(self, question, lowered, ctx):
        return bool(re.search(r"\b(every day|daily|each day)\b", lowered)) and _parse_time(lowered) is not None

    def handle(self, question, lowered, ctx):
        hour, minute = _parse_time(lowered)
        user_id = ctx.speaker["id"] if ctx.speaker else None
        alarms.add_alarm(user_id, hour, minute, label="")
        return Response("text", f"Okay, I'll remind you every day at {hour:02d}:{minute:02d}.")


registry.register(SetRecurringAlarmIntent())
