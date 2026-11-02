"""Cheap, deterministic pre-filter deciding whether a turn is worth spending
an LLM call on for opportunity detection. This is a cost-control gate, NOT
scenario logic -- it only recognizes generic entity *types* (dates, events,
planning language), never specific situations. What to actually do about a
match is entirely the LLM's judgment (see llm/opportunity_planner.py).
"""
import re

_MONTHS = ("january", "february", "march", "april", "may", "june", "july",
          "august", "september", "october", "november", "december")
_MONTH_PATTERN = "|".join(_MONTHS)

_DATE_PATTERN = re.compile(
    rf"\b(?:{_MONTH_PATTERN})\s+\d{{1,2}}(?:st|nd|rd|th)?\b|"
    rf"\b\d{{1,2}}(?:st|nd|rd|th)?\s+(?:{_MONTH_PATTERN})\b|"
    r"\b(next week|next month|this weekend|tomorrow)\b", re.I)

_EVENT_WORDS = re.compile(
    r"\b(birthday|anniversary|wedding|appointment|trip|vacation|holiday|visit|visiting)\b", re.I)

_PLANNING_VERBS = re.compile(
    r"\b(planning to|going to|thinking of|i'm going|i am going|i want to go|i'll be)\b", re.I)

# Stated tasks, goals and intentions that may deserve a clarifying question or a
# reminder offer later -- still generic signal *types*, never specific scenarios.
_GOAL_TASK = re.compile(
    r"\b(i need to|i have to|i've got to|i gotta|i should|i must|remind me|"
    r"don't let me forget|my goal|i want to|i'd like to|i hope to|trying to|"
    r"i plan to|i'm supposed to|i am supposed to)\b", re.I)


def worth_checking(question):
    """True if this turn contains any generic date/event/planning/goal signal
    that makes it worth an LLM opportunity-detection call. This is a cheap cost
    gate only -- what (if anything) to do about a match is the LLM's judgment."""
    return bool(_DATE_PATTERN.search(question) or _EVENT_WORDS.search(question)
               or _PLANNING_VERBS.search(question) or _GOAL_TASK.search(question))
