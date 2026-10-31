"""Input/output safety guardrails: a fast deterministic rule layer plus an
optional (slower) LLM-judge second pass for paraphrased cases.

The rule layer always runs and catches self-harm/violence/etc. deterministically.
The judge fails open -- a classifier glitch never blocks a normal reply.
"""
import json
import re
import threading
import time

from .. import config, state
from ..core.guard_rule import GuardRuleRegistry, RegexGuardRule
from .client import chat

GUARD_RULES = {
    "self_harm": re.compile(
        r"\b(kill myself|killing myself|suicide|suicidal|end my life|"
        r"take my (own )?life|hurt myself|harm myself|cut myself|"
        r"want to die|better off dead)\b", re.I),
    "violence": re.compile(
        r"\b(how (to|do i) (make|build|create).{0,20}(bomb|explosive|weapon|gun)|"
        r"kill (someone|him|her|them|people)|make a (bomb|explosive)|"
        r"build a (bomb|gun)|poison (someone|somebody)|untraceable)\b", re.I),
    "illegal": re.compile(
        r"\b(how (to|do i) (hack|steal|launder|counterfeit)|"
        r"buy (drugs|cocaine|heroin|meth)|sell drugs|"
        r"break into (a )?(house|car|account)|credit card numbers|"
        r"make (meth|methamphetamine|fake id))\b", re.I),
    "hate": re.compile(
        r"\b(kill all|gas the|ethnic cleansing|racial slur|"
        r"(hate|exterminate) (jews|muslims|blacks|whites|gays))\b", re.I),
    "sexual": re.compile(
        r"\b(child (porn|sexual)|underage (sex|nude)|csam|"
        r"explicit sexual (story|roleplay))\b", re.I),
    "injection": re.compile(
        r"(ignore (all |your )?(previous|prior|above) (instructions|prompt)|"
        r"disregard (your|the) (rules|instructions)|you are now|"
        r"system prompt|developer mode|jailbreak|act as (an? )?(dan|unfiltered))", re.I),
}

# Registry formalizes GUARD_RULES as pluggable rule objects -- a new category
# registers an instance instead of growing the dict (open/closed), while
# GUARD_RULES itself stays available for anything inspecting it directly.
_rule_registry = GuardRuleRegistry()
for _category, _pattern in GUARD_RULES.items():
    _rule_registry.register(RegexGuardRule(_category, _pattern))

REFUSALS = {
    "self_harm": ("I can't help with that, but you deserve support. Please reach out "
                  "to someone you trust, or a local helpline, right now."),
    "violence": "I stopped myself there because that could help someone get hurt. Let's talk about something else.",
    "illegal": "That drifted into illegal territory, so I had to stop. Happy to help with something else.",
    "hate": "I stopped because that was heading somewhere hateful, which I won't do.",
    "sexual": "I stopped because that wasn't appropriate for me to continue.",
    "injection": "That sounded like an attempt to change my instructions, so I stopped and I'm sticking to my normal rules.",
    "default": "I can't help with that one. Is there something else I can do for you?",
}


def guard_log(where, category, text):
    try:
        with open(config.GUARD_LOG, "a") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}\t{where}\t{category}\t{text[:60]}\n")
    except Exception:
        pass
    print(f"  [guard] blocked ({where}/{category})")


def rule_scan(text):
    return _rule_registry.scan(text)


def llm_judge(text):
    """Optional second pass. Returns a category string if UNSAFE, else None."""
    system = ("You are a safety classifier. Decide if the user text is unsafe. "
              "Unsafe categories: self_harm, violence, illegal, hate, sexual. "
              "Reply with JSON only: {\"safe\": true|false, \"category\": \"<name or none>\"}.")
    try:
        raw = chat(system, text)
        match = re.search(r"\{.*\}", raw, re.S)
        if not match:
            return None
        data = json.loads(match.group(0))
        if data.get("safe") is False:
            cat = str(data.get("category") or "default").strip().lower()
            return cat if cat and cat != "none" else "default"
    except Exception:
        return None
    return None


def guard_alert(category):
    def blink():
        if state.led:
            for _ in range(5):
                state.led.on(); time.sleep(0.08); state.led.off(); time.sleep(0.08)
    threading.Thread(target=blink, daemon=True).start()
    from ..web.face_server import set_status   # local import: avoids a hard import-time cycle
    set_status("blocked", reply=f"Blocked: {category.replace('_', ' ')}")


def guard_input(question):
    """Return (allowed, refusal_text). refusal_text is '' when allowed."""
    if not config.GUARDRAILS_ENABLED:
        return True, ""
    category = rule_scan(question)
    if category is None and config.GUARD_USE_LLM_JUDGE:
        category = llm_judge(question)
    if category is None:
        return True, ""
    guard_log("input", category, question)
    guard_alert(category)
    return False, REFUSALS.get(category, REFUSALS["default"])


def guard_output(question, answer):
    """Replace an unsafe model answer with a refusal; otherwise pass it through."""
    if not config.GUARDRAILS_ENABLED or not answer:
        return answer
    category = rule_scan(answer)
    if category is None:
        return answer
    guard_log("output", category, answer)
    guard_alert(category)
    return REFUSALS.get(category, REFUSALS["default"])


def guard_report():
    try:
        lines = [l for l in open(config.GUARD_LOG).read().splitlines() if l.strip()]
    except Exception:
        lines = []
    if not lines:
        return "I have not had to block anything."
    from collections import Counter
    cats = Counter(l.split("\t")[2] for l in lines if len(l.split("\t")) > 2)
    parts = ", ".join(f"{n} {c.replace('_', ' ')}" for c, n in cats.items())
    return f"I have blocked {len(lines)} requests: {parts}."
