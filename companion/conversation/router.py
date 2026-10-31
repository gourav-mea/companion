"""Thin facade over the Intent registry (companion/intents/). This is what
lets a new voice command be added with zero changes here: route()/handle()
keep the exact public signatures session.py and cli.py already depend on,
while every actual command now lives as a pluggable Intent (open/closed).
"""
import re

from .. import config
from ..core.types import Response, TurnContext
from ..intents import registry as intent_registry
from ..llm.client import chat
from ..llm.guardrails import guard_input, guard_output
from ..llm.prompts import brain_system, ground_system, LIVE_GROUND_SYSTEM  # noqa: F401 (re-exported)
from ..skills import live_data

AFFIRM_WORDS = {"yes", "yeah", "yep", "yup", "sure", "okay", "ok", "please",
                "correct", "right", "go ahead", "do it"}
NEGATIVE_WORDS = {"no", "nope", "nah", "wrong", "cancel", "never mind", "nevermind", "stop"}


def is_affirmative(text):
    t = text.strip().lower().strip(" .!?")
    return t in AFFIRM_WORDS or any(w in t for w in ("yes", "yeah", "sure", "please", "correct"))


def is_negative(text):
    t = text.strip().lower().strip(" .!?")
    return t in NEGATIVE_WORDS or any(w in t for w in ("no", "nope", "wrong", "cancel"))


def is_backchannel(text):
    """True for filler like "okay"/"go on" during a barge-in -- these mean
    "keep talking", not "stop and answer something else". Exact-match only
    (plus a couple of multi-word phrases) -- a loose substring check here
    would misclassify real interruptions as filler (e.g. "broken" containing
    "ok"), silently dropping what the user actually said."""
    t = text.strip().lower().strip(" .!?")
    if t in config.BARGE_IN_BACKCHANNEL_WORDS:
        return True
    return any(phrase in t for phrase in ("go on", "keep going", "continue"))


def looks_garbled(text):
    toks = re.findall(r"[a-z]+", text.lower())
    if not toks:
        return True
    if max(len(t) for t in toks) < 3:
        return True
    letters = sum(len(t) for t in toks)
    return letters / max(1, len(text)) < 0.5


def low_confidence(rms, text):
    return rms < config.CONFIRM_RMS_THRESHOLD or looks_garbled(text)


def route(question, speaker=None):
    """Decide how to answer for the given (optionally recognized) speaker.

    Returns ('text', str), ('llm', (system, user, followup_or_None)),
    ('live', (tool, question)) or ('enroll', display_name) to self-enroll.
    `followup`/`tool['followup']` is a proactive offer dict -- see
    skills/macros.run_followup and session.py's _offer_followup.
    """
    # Input guardrail runs before any intent, tool, or model call.
    allowed, refusal = guard_input(question)
    if not allowed:
        return ("text", refusal)
    ctx = TurnContext(speaker=speaker)
    response = intent_registry.dispatch(question, question.lower(), ctx)
    if response is None:
        # Shouldn't happen (FallbackLLMIntent always matches); safety net only.
        response = Response("llm", (brain_system(question, speaker), question, None))
    return (response.kind, response.payload)


def handle(question, speaker=None):
    """Non-streaming answer (used by tests and any direct caller)."""
    kind, payload = route(question, speaker)
    if kind == "text":
        return guard_output(question, payload)
    if kind == "enroll":
        return guard_output(question, "Enrollment needs the live microphone; use the voice flow or the CLI.")
    if kind == "live":
        tool, q = payload
        data = live_data.fetch_live(tool, q)
        if tool.get("ground"):
            return guard_output(question, chat(ground_system() + data, q))
        return guard_output(question, data)
    system, user_question, _followup = payload
    return guard_output(question, chat(system, user_question))

