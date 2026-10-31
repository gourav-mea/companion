"""Voice macros (multi-step canned routines) and proactive follow-up offers."""
from . import live_data


def run_macro(name):
    name = (name or "").lower()
    if name == "morning":
        import random
        from .utilities import get_time_date
        from ..data.jokes import JOKES
        parts = [get_time_date("date")]
        weather = next((t for t in live_data.LIVE_TOOLS if t["name"] == "weather"), None)
        from .. import config
        if weather and config.LIVE_DATA_ENABLED:
            parts.append(live_data.fetch_live(weather, "weather"))
        parts.append(random.choice(JOKES))
        parts.append("Have a great day.")
        return " ".join(parts)
    if name == "evening":
        return ("Here's your evening wind-down. Would you like a quick summary of today, "
                "or should I set your first reminder for tomorrow? Just ask.")
    return ""


def run_followup(followup):
    """followup is the dict stashed in state.pending_followup (see live_data's
    LIVE_TOOLS 'followup' entries and router's policy-related suggestion)."""
    kind = followup.get("kind") if isinstance(followup, dict) else followup
    if kind == "weather_tomorrow":
        return live_data.announced_fetch(
            "api.open-meteo.com", "the Open-Meteo weather service",
            live_data.get_weather_tomorrow, "tomorrow's forecast", note="forecast only")
    if kind == "policy_related":
        from . import policy_qa
        doc, question = followup["doc"], followup["question"]
        hits = [h for h in policy_qa.policy_retrieve(question, k=5) if h[1] == doc]
        if not hits:
            return "I could not find anything relevant there after all."
        return policy_qa.answer_policy(question, hits)
    if kind == "confirm_memory_update":
        from . import memory_notes
        # Nothing was written when the question was first asked -- the
        # candidate (and its already-computed embedding) only gets
        # persisted now that the user has confirmed it.
        memory_notes.remember_note(
            followup["new_text"], user_id=followup["user_id"], item_type="fact", source="auto",
            attribute=followup["attribute"], confidence=1.0, vec=followup["vec"])
        memory_notes.log_conflict(followup["user_id"], followup["attribute"],
                                  followup["old_content"], followup["new_text"], "confirmed")
        return "Got it, I've updated that."
    return ""
