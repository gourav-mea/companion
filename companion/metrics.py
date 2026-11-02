"""Lightweight, failure-isolated real-use metrics.

Appends one JSON object per event to config.METRICS_LOG, so the core
interaction loop can be measured from actual use rather than tuned by feel.
Read it back with `talk.py metrics`.

Event kinds:
  wake      -- a wake check that heard something or fired (false-positive rate)
  identify  -- a speaker-ID attempt: score vs threshold (false accept/reject)
  turn      -- one answered question: time-to-first-word, ASR time, mishear flags
  session   -- a wake-to-sleep span: how many real turns it produced

Every entry point is cheap and wrapped in try/except: metrics must NEVER slow
down or break the conversation. Transcripts are only stored when
config.METRICS_LOG_TEXT is on (privacy toggle).
"""
import json
import os
import threading
import time

from . import config

_lock = threading.Lock()


def _rotate_if_needed():
    try:
        if os.path.getsize(config.METRICS_LOG) > config.METRICS_MAX_BYTES:
            os.replace(config.METRICS_LOG, config.METRICS_LOG + ".1")   # keep one previous generation
    except OSError:
        pass


def _append(obj):
    if not config.METRICS_ENABLED:
        return
    try:
        obj.setdefault("ts", time.time())
        line = json.dumps(obj, ensure_ascii=False)
        with _lock:
            _rotate_if_needed()
            with open(config.METRICS_LOG, "a", encoding="utf-8") as f:
                f.write(line + "\n")
    except Exception:
        pass   # metrics must never break the app


def _text(s):
    """Honour the privacy toggle for any free-text field."""
    if not s:
        return ""
    return s if config.METRICS_LOG_TEXT else "<redacted>"


def log_wake(detected, heard="", source="whisper"):
    _append({"event": "wake", "detected": bool(detected), "heard": _text(heard), "source": source})


def log_identify(user_id, user_name, score, threshold, cached):
    _append({"event": "identify", "user_id": user_id, "user": user_name,
             "score": round(float(score), 3), "threshold": threshold, "cached": bool(cached)})


def log_turn(**fields):
    fields["event"] = "turn"
    if not config.METRICS_LOG_TEXT:
        fields.pop("heard", None)
        fields.pop("answer", None)
    _append(fields)


def log_session(answered, duration_s=None):
    _append({"event": "session", "answered": int(answered),
             "duration_s": None if duration_s is None else round(duration_s, 1)})


class TurnTimer:
    """Measures time-to-first-word: the gap between the user finishing their
    question (or routing starting) and the companion's first spoken word -- the
    single number that most decides whether the device feels snappy."""

    def __init__(self):
        self.t0 = time.time()
        self.first_word_ms = None

    def mark_first_word(self):
        if self.first_word_ms is None:
            self.first_word_ms = int((time.time() - self.t0) * 1000)


# ---- synthetic demo data (preview the summary before real data exists) ----------
# Written to a SEPARATE file so the real log is never touched, and removable in
# one command. Numbers are deliberately "Pi-plausible", not flattering: slow-ish
# LLM latency, a realistic false-wake rate, and speaker scores that sometimes dip
# below threshold -- so the preview shows what the summary will actually surface.

def demo_path():
    return config.METRICS_LOG + ".demo"


def generate_demo(days=7, seed=42, path=None):
    """Write ~`days` of plausible synthetic events to the demo file. Returns
    (path, count). Remove with the metrics demo-clear path."""
    import random
    path = path or demo_path()
    rng = random.Random(seed)
    users = ["Gourav", "Asha", "guest"]
    examples = [
        ("what's the weather", "It's sunny and about 24 degrees.", "live"),
        ("what's the time", "It is 3:40 in the afternoon.", "text"),
        ("tell me about the mughal empire", "The Mughal Empire ruled much of South Asia for centuries.", "llm"),
        ("set a timer for ten minutes", "Timer set for ten minutes.", "text"),
        ("what's the dollar rate", "One US dollar is about 83 rupees.", "live"),
        ("remind me to call mom tomorrow", "Okay, I'll remind you tomorrow.", "llm"),
        ("what can you do", "I can answer questions, fetch the weather, set timers, and more.", "text"),
    ]
    base_ms = {"llm": 2600, "live": 1800, "text": 700}
    now = time.time()
    events = []   # (ts, obj)

    for d in range(days):
        day_start = now - (days - 1 - d) * 86400
        for _ in range(rng.randint(5, 11)):                     # sessions that day
            t = day_start + rng.uniform(8 * 3600, 22 * 3600)   # daytime-ish
            events.append((t - 2, {"event": "wake", "detected": True,
                                   "heard": rng.choice(["hey companion", "hi companion", ""]),
                                   "source": "whisper"}))
            if rng.random() < 0.3:                              # idle non-wake noise overheard
                events.append((t - 5, {"event": "wake", "detected": False,
                                       "heard": rng.choice(["thank you", "music", ""]), "source": "whisper"}))
            if rng.random() < 0.15:                             # false wake: no real turn
                events.append((t + 6, {"event": "session", "answered": 0,
                                       "duration_s": round(rng.uniform(3, 8), 1)}))
                continue
            user = rng.choice(users)
            score = rng.uniform(0.35, 0.60) if user == "guest" else rng.gauss(0.74, 0.05)
            events.append((t, {"event": "identify",
                               "user_id": None if user == "guest" else abs(hash(user)) % 1000,
                               "user": user, "score": round(score, 3),
                               "threshold": config.SPEAKER_MATCH_THRESHOLD, "cached": False}))
            nturns = rng.randint(1, 4)
            for i in range(nturns):
                q, a, kind = rng.choice(examples)
                fw = max(300, int(rng.gauss(base_ms[kind], base_ms[kind] * 0.3)))
                events.append((t + 10 + i * 20, {
                    "event": "turn", "kind": kind, "heard": q, "answer": a,
                    "rms": rng.randint(700, 1300), "asr_ms": max(300, int(rng.gauss(1500, 400))),
                    "first_word_ms": fw, "answer_total_ms": fw + rng.randint(500, 3000),
                    "user": user, "score": round(score, 3), "speaker_cached": i > 0,
                    "barge_in": rng.random() < 0.05, "low_conf": rng.random() < 0.10,
                    "continuation": rng.random() < 0.08}))
            events.append((t + 10 + nturns * 20 + 5,
                           {"event": "session", "answered": nturns,
                            "duration_s": round(rng.uniform(20, 120), 1)}))
        # a few noise turns that got discarded
        for _ in range(rng.randint(0, 3)):
            t = day_start + rng.uniform(8 * 3600, 22 * 3600)
            events.append((t, {"event": "turn", "kind": "discarded", "reason": "noise_or_low_rms",
                               "heard": rng.choice(["you", "thanks", "music"]),
                               "rms": rng.randint(400, 700), "asr_ms": rng.randint(600, 1500)}))

    events.sort(key=lambda x: x[0])
    with open(path, "w", encoding="utf-8") as f:
        for ts, obj in events:
            obj["ts"] = ts
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")
    return path, len(events)
