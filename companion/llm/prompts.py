"""System-prompt builders shared by the router facade and the fallback LLM
intent. Pulled out of conversation/router.py so intents can use them
without importing router.py (which would create an intents -> router ->
intents import cycle, since router imports the intents package).
"""
import time

from .. import accessibility
from ..skills import memory_notes

LIVE_GROUND_SYSTEM = (
    "Relay this live information to the user in one short, natural spoken sentence. "
    "Use only what is given; add nothing.\n\n"
)


def _personal_context(question, speaker):
    """The "who am I talking to + what do I know about them" block shared by
    every answer path, so personalization/recall isn't limited to the plain LLM
    route (the grounded/live route got none before)."""
    block = ""
    if speaker:
        block += f"\n\nYou are talking with {speaker['display_name']}. Personalize your tone for them."
    recalled = memory_notes.recall_notes(question, user_id=speaker["id"] if speaker else None)
    if recalled:
        block += "\n\nThings this person told you earlier:\n" + recalled
    return block


def brain_system(question, speaker=None):
    system = ("You are a helpful offline assistant. Answer in one or two short sentences. "
              "If unsure, say you are not sure. Never invent facts.")
    system += time.strftime("\n\nFor reference, right now it is %I:%M %p on %A, %d %B %Y.")
    system += _personal_context(question, speaker)
    return system + accessibility.lang_suffix()


def ground_system(question=None, speaker=None):
    """System prompt for relaying live data. Optionally carries the personal
    context so a live answer can use the person's name/preferences -- but the
    facts to relay still come only from the live data block the caller appends.
    Back-compatible: existing callers pass no arguments."""
    system = LIVE_GROUND_SYSTEM
    personal = _personal_context(question, speaker) if (question or speaker) else ""
    if personal:
        system += ("The notes below are only to personalize tone and address the person; "
                   "do NOT treat them as new facts to state.\n" + personal.lstrip() + "\n\n")
    return system + accessibility.lang_suffix()
