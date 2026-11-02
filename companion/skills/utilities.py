"""Small local utilities that need no network or LLM call: math, unit
conversion, timers, time/date, the help text, and conversation transcript
logging.
"""
import ast
import operator
import os
import re
import threading
import time

from .. import config, state

_NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
                 "seven": 7, "eight": 8, "nine": 9, "ten": 10, "fifteen": 15,
                 "twenty": 20, "thirty": 30, "forty": 40, "sixty": 60,
                 "a": 1, "an": 1, "half": 0.5}

_SAFE_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
             ast.Div: operator.truediv, ast.Mod: operator.mod, ast.Pow: operator.pow,
             ast.USub: operator.neg}


_BRACKETED = re.compile(r"^[\[\(\*♪♫~\-_.’' ]*$")
# Whisper tags non-speech audio as "(dramatic music)", "[wind howling]",
# "(cough)" etc -- any transcript wholly wrapped in brackets is a sound
# description, never a spoken question, whatever words are inside.
_SOUND_TAG = re.compile(r"^[\[\(].*[\]\)]$")
_PUNCT_ONLY = re.compile(r"^[^a-z0-9]*$")


def is_noise(text):
    """True if a transcript is almost certainly NOT a real spoken question.

    Whisper hallucinates confident, plausible sentences when fed non-speech
    audio (wind, laughter, a TV), so an empty-transcript check isn't enough --
    we also have to recognise its stock inventions by name.
    """
    t = text.strip().lower().strip(" .!?,-…")
    t = re.sub(r"\s+", " ", t)
    if len(t) < config.MIN_QUESTION_CHARS:
        return True
    if _PUNCT_ONLY.match(t) or _BRACKETED.match(t) or _SOUND_TAG.match(t):
        return True
    if t in config.NOISE_TOKENS:
        return True
    # Bracketed sound tags like [music], (laughter), [blank_audio].
    stripped = t.strip("[]()")
    if stripped != t and stripped in config.NOISE_TOKENS:
        return True
    return False


def with_context(system, history, summary=None):
    """Fold conversational context into the system prompt: a rolling summary of
    the older turns (so context survives beyond the last few turns without
    overflowing the small context window), plus the most-recent turns verbatim
    for immediate reference ("set a reminder for that")."""
    parts = []
    if summary:
        parts.append("Summary of the conversation so far:\n" + summary)
    if history:
        parts.append("Most recent exchanges:\n"
                     + "\n".join(f"User: {q}\nYou: {a}" for q, a in history))
    if not parts:
        return system
    return system + "\n\n" + "\n\n".join(parts)


def repeat_last():
    return state.last_answer or "I have not said anything yet."


def help_text():
    return ("I can answer company policy questions and name the source, fetch the weather, "
            "exchange rates, Wikipedia facts and news headlines, set timers, do quick maths, "
            "tell the time, remember notes, summarise our chat, and switch between my fast and "
            "smart models. You can also ask me to speak slower or faster, answer in Hindi, or "
            "say good morning for a quick briefing. Just ask.")


def get_time_date(question=""):
    now = time.localtime()
    if re.search(r"\b(date|day)\b", question.lower()):
        return time.strftime("Today is %A, %d %B %Y.", now)
    return time.strftime("It is %I:%M %p.", now).lstrip("0")


def log_turn(q, a):
    try:
        with open(config.TRANSCRIPT, "a") as f:
            f.write(f"You: {q}\nCompanion: {a}\n\n")
    except Exception:
        pass


def summarise():
    from ..llm.client import chat
    try:
        convo = open(config.TRANSCRIPT).read().strip()
    except Exception:
        convo = ""
    if not convo:
        return "We have not talked about anything yet."
    return chat("Summarise this conversation in two or three short sentences.", convo)


def clear_history():
    if os.path.exists(config.TRANSCRIPT):
        os.remove(config.TRANSCRIPT)
    return "Conversation history cleared."


def parse_duration(text):
    m = re.search(r"(\d+(?:\.\d+)?|[a-z]+)\s*(second|minute|hour)s?", text.lower())
    if not m:
        return None
    raw, unit = m.group(1), m.group(2)
    amount = float(raw) if re.match(r"\d", raw) else _NUMBER_WORDS.get(raw)
    if amount is None:
        return None
    seconds = int(amount * {"second": 1, "minute": 60, "hour": 3600}[unit])
    return seconds if 1 <= seconds <= 24 * 3600 else None


def _timer_fire(label):
    # Runs in the Timer thread. Blink immediately (no lock), then hand the
    # spoken reminder to the main loop so it lands at the next gap, on time.
    if state.led:
        for _ in range(6):
            state.led.on(); time.sleep(0.2); state.led.off(); time.sleep(0.2)
    queue_reminder(f"Your {label} is done.", priority=1)   # an explicit timer outranks speculative nudges


def queue_reminder(text, priority=5, ttl=None, category=None):
    """Enqueue a spoken reminder/nudge with a priority (lower = sooner) and an
    optional time-to-live after which it is dropped undelivered rather than
    spoken stale. Plain strings put on the queue elsewhere still work (they get
    the default priority and no expiry)."""
    if not text:
        return
    state.reminder_queue.put({"text": text, "priority": priority, "category": category,
                              "expires_ts": (time.time() + ttl) if ttl else None})


def plan_reminders(items):
    """Pure ordering/decay step shared by drain_reminders and the memory smoke
    test: drop any that have expired, then order by priority (lower = sooner).
    Kept separate so delivery order is testable without actually speaking."""
    now = time.time()
    live = [it for it in items if not (it.get("expires_ts") and it["expires_ts"] < now)]
    live.sort(key=lambda it: it.get("priority", 5))
    return live


def drain_reminders():
    """Speak any reminders that have come due -- highest priority first, dropping
    any that expired while the device was busy or asleep. Called from the loops'
    gaps."""
    import queue
    from ..audio.playback import speak
    items = []
    while True:
        try:
            raw = state.reminder_queue.get_nowait()
        except queue.Empty:
            break
        items.append(raw if isinstance(raw, dict)
                     else {"text": raw, "priority": 5, "expires_ts": None})
    for it in plan_reminders(items):
        speak(it["text"])


def set_timer_cmd(question):
    seconds = parse_duration(question)
    if not seconds:
        return "I did not catch the duration. Try: set a timer for five minutes."
    label = "alarm" if "alarm" in question.lower() else "timer"
    t = threading.Timer(seconds, _timer_fire, args=(label,))
    t.daemon = True
    t.start()
    mins, secs = divmod(seconds, 60)
    if mins and secs:
        human = f"{mins} minute{'s' if mins != 1 else ''} and {secs} seconds"
    elif mins:
        human = f"{mins} minute{'s' if mins != 1 else ''}"
    else:
        human = f"{secs} second{'s' if secs != 1 else ''}"
    return f"{label.capitalize()} set for {human}."


def _safe_eval(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _SAFE_OPS:
        return _SAFE_OPS[type(node.op)](_safe_eval(node.left), _safe_eval(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _SAFE_OPS:
        return _SAFE_OPS[type(node.op)](_safe_eval(node.operand))
    raise ValueError("unsupported expression")


def _try_units(q):
    m = re.search(r"(\d+(?:\.\d+)?)\s*(kilometres?|kilometers?|km|miles?|mi|"
                  r"kilograms?|kg|pounds?|lb|celsius|fahrenheit|metres?|meters?|feet|foot|ft)\b", q)
    if not m:
        return None
    val, unit = float(m.group(1)), m.group(2)
    if unit.startswith(("km", "kilom")):
        return f"That is {val * 0.621371:g} miles."
    if unit in ("mile", "miles", "mi"):
        return f"That is {val * 1.60934:g} kilometres."
    if unit.startswith(("kg", "kilog")):
        return f"That is {val * 2.20462:g} pounds."
    if unit in ("lb", "pound", "pounds"):
        return f"That is {val * 0.453592:g} kilograms."
    if unit == "celsius":
        return f"That is {val * 9 / 5 + 32:g} degrees Fahrenheit."
    if unit == "fahrenheit":
        return f"That is {(val - 32) * 5 / 9:g} degrees Celsius."
    if unit in ("metre", "metres", "meter", "meters"):
        return f"That is {val * 3.28084:g} feet."
    if unit in ("feet", "foot", "ft"):
        return f"That is {val * 0.3048:g} metres."
    return None


def try_math(question):
    q = question.lower()
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:%|percent)\s*of\s*(\d+(?:\.\d+)?)", q)
    if m:
        return f"That is {float(m.group(1)) / 100 * float(m.group(2)):g}."
    units = _try_units(q)
    if units:
        return units
    expr = re.sub(r"\bplus\b", "+", q)
    expr = re.sub(r"\bminus\b", "-", expr)
    expr = re.sub(r"\b(times|multiplied by)\b", "*", expr)
    expr = re.sub(r"\b(divided by|over)\b", "/", expr)
    cleaned = re.sub(r"[^0-9+\-*/%.()\s]", " ", expr).strip()
    if cleaned and re.search(r"\d", cleaned) and re.search(r"[-+*/%]", cleaned):
        try:
            return f"That is {_safe_eval(ast.parse(cleaned, mode='eval').body):g}."
        except Exception:
            return None
    return None
