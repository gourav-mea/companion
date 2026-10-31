"""Default FactExtractor: one LLM call, made only after a keyword pre-filter
already found the turn worth looking at (see skills/memory_notes.
maybe_capture_fact) -- keeps this affordable on a Pi instead of running on
every single turn.
"""
import json
import re

from ..core.fact_extractor import FactCandidate
from .client import chat

_JSON_RE = re.compile(r"\{.*\}", re.S)

_SYSTEM = (
    "The user just said something that may contain a durable personal fact or "
    "preference (e.g. their name, where they live, what they like). Extract it.\n"
    "Reply with JSON only: "
    "{\"has_fact\": true|false, \"text\": \"<short factual statement>\", "
    "\"attribute\": \"<short slot name like name/home_city/job_title, or null "
    "if there isn't one clear slot>\", "
    "\"update_mode\": \"replace\" or \"append\" (replace for singular facts like "
    "name/home_city, append for things like likes/hobbies that can have many "
    "values), \"confidence\": 0.0-1.0}. "
    "If there is no durable fact worth remembering, reply {\"has_fact\": false}."
)


class LLMFactExtractor:
    def extract(self, question, answer):
        try:
            raw = chat(_SYSTEM, question)
            match = _JSON_RE.search(raw)
            if not match:
                return None
            data = json.loads(match.group(0))
            if not data.get("has_fact") or not data.get("text"):
                return None
            return FactCandidate(
                text=str(data["text"]).strip(),
                attribute=(str(data["attribute"]).strip().lower()
                          if data.get("attribute") else None),
                update_mode="append" if data.get("update_mode") == "append" else "replace",
                confidence=float(data.get("confidence", 0.5)))
        except Exception:
            return None   # fail open: a classifier hiccup never blocks the conversation


extractor = LLMFactExtractor()
