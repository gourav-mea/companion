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


def brain_system(question, speaker=None):
    recalled = memory_notes.recall_notes(question, user_id=speaker["id"] if speaker else None)
    system = ("You are a helpful offline assistant. Answer in one or two short sentences. "
              "If unsure, say you are not sure. Never invent facts.")
    system += time.strftime("\n\nFor reference, right now it is %I:%M %p on %A, %d %B %Y.")
    if speaker:
        system += f"\n\nYou are talking with {speaker['display_name']}. Personalize your tone for them."
    if recalled:
        system += "\n\nThings this person told you earlier:\n" + recalled
    return system + accessibility.lang_suffix()


def ground_system():
    return LIVE_GROUND_SYSTEM + accessibility.lang_suffix()
