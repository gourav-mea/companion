"""Store a user's birthday (month-day) and answer "what is my birthday" --
lets the proactive scheduler wish them on the day (see proactive/triggers.py
birthday_wishes). Priority runs before the generic RememberNoteIntent (70)
so "remember my birthday is March 5th" is parsed as a structured date, not
stored as free-text.
"""
import re

from ..core.types import Response
from ..identity import repository
from .registry import registry

_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}
_MONTH_PATTERN = "|".join(_MONTHS.keys())


def _parse_birthday(text):
    m = re.search(rf"\b({_MONTH_PATTERN})\s+(\d{{1,2}})(?:st|nd|rd|th)?\b", text)
    if m:
        month_name, day = m.group(1), m.group(2)
    else:
        m = re.search(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({_MONTH_PATTERN})\b", text)
        if not m:
            return None
        day, month_name = m.group(1), m.group(2)
    day = int(day)
    if not (1 <= day <= 31):
        return None
    return f"{_MONTHS[month_name]:02d}-{day:02d}"


class SetBirthdayIntent:
    name = "set_birthday"
    priority = 65
    pattern = re.compile(r"\b(my birthday is|remember my birthday)\b")

    def matches(self, question, lowered, ctx):
        return bool(self.pattern.search(lowered)) and ctx.speaker is not None

    def handle(self, question, lowered, ctx):
        birthday = _parse_birthday(lowered)
        if not birthday:
            return Response("text", "I didn't catch the date. Try, my birthday is March 5th.")
        repository.set_pref(ctx.speaker["id"], "birthday", birthday)
        return Response("text", "Got it, I'll remember your birthday.")


class WhatIsMyBirthdayIntent:
    name = "what_is_my_birthday"
    priority = 66
    pattern = re.compile(r"\bwhat(?:'s| is) my birthday\b")

    def matches(self, question, lowered, ctx):
        return bool(self.pattern.search(lowered))

    def handle(self, question, lowered, ctx):
        if ctx.speaker is None:
            return Response("text", "I don't know your birthday -- I don't recognize your voice yet.")
        birthday = repository.get_pref(ctx.speaker["id"], "birthday")
        if not birthday:
            return Response("text", "I don't have your birthday yet. Try, my birthday is March 5th.")
        month, day = birthday.split("-")
        month_name = next(k for k, v in _MONTHS.items() if v == int(month)).title()
        return Response("text", f"Your birthday is {month_name} {int(day)}.")


registry.register(SetBirthdayIntent())
registry.register(WhatIsMyBirthdayIntent())
