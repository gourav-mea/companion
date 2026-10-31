"""Default OpportunityPlanner: a heavier local model (config.OPPORTUNITY_MODEL)
with self-consistency voting for the rare, high-stakes "is this a new
opportunity" judgment, and the regular fast conversational model for the
lower-stakes "advance an already-open thread" judgment. Both calls use
Ollama's JSON mode to avoid malformed output. Fails open (no opportunity /
close the thread) on any error -- a classifier hiccup never blocks or
pesters the user.
"""
import json
from collections import Counter

import requests

from .. import config
from ..core.opportunity_planner import OpportunityDecision, ThreadDecision

# Few-shot examples teach the small/medium local model the exact judgment
# call to make -- far more reliable than asking it zero-shot. New situations
# are handled by the model's own generalization from these, not by adding
# more hardcoded scenarios here.
_DETECT_SYSTEM = (
    "You help a home voice companion decide if a user's remark contains something "
    "worth proactively following up on later -- like a date that could use a reminder, "
    "or plans that could use a clarifying question and some helpful advice. Most remarks "
    "are NOT opportunities -- don't force it, and when unsure say no.\n\n"
    "Examples:\n"
    "User: \"My friend Raj's birthday is on December 25th.\"\n"
    "{\"has_opportunity\": true, \"category\": \"date_reminder\", \"confidence\": 0.9, "
    "\"topic_summary\": \"Raj's birthday on Dec 25\", "
    "\"next_question\": \"Would you like me to set a reminder for Raj's birthday on December 25th?\"}\n\n"
    "User: \"I'm planning a trip to Manali.\"\n"
    "{\"has_opportunity\": true, \"category\": \"travel_planning\", \"confidence\": 0.85, "
    "\"topic_summary\": \"Planning a trip to Manali\", "
    "\"next_question\": \"Nice! Which month are you planning to go, so I can share relevant tips?\"}\n\n"
    "User: \"What's the weather like?\"\n"
    "{\"has_opportunity\": false}\n\n"
    "User: \"I might go to Manali someday, who knows.\"\n"
    "{\"has_opportunity\": false}\n\n"
    "Reply with JSON only, matching one of the shapes above exactly."
)

_ADVANCE_SYSTEM = (
    "You are tracking an in-progress proactive conversation thread with a user. "
    "Given the thread so far and what they just said, decide what to do next.\n"
    "Reply with JSON only: {\"action\": \"continue\"|\"ready\"|\"close\"|\"suppress\", "
    "\"collected_info\": {...}, \"next_question\": \"...\" or null, "
    "\"final_message\": \"...\" or null}.\n"
    "Use \"continue\" if you need to ask one more clarifying question. "
    "Use \"ready\" once you have enough information to actually help them (give advice, "
    "confirm a reminder, etc) -- put that help in final_message. "
    "Use \"close\" if the topic naturally ended without needing action. "
    "Use \"suppress\" if the user signaled they don't want to discuss this further."
)


def _call_json(system, user, model, keep_alive, num_ctx, temperature):
    payload = {"model": model, "stream": False, "format": "json",
               "keep_alive": keep_alive,
               "options": {"num_predict": config.LLM_NUM_PREDICT, "temperature": temperature,
                           "num_thread": config.WHISPER_THREADS, "num_ctx": num_ctx},
               "messages": [{"role": "system", "content": system},
                            {"role": "user", "content": user}]}
    try:
        r = requests.post(config.OLLAMA_URL, json=payload, timeout=config.OPPORTUNITY_CALL_TIMEOUT_SECONDS)
        return json.loads(r.json()["message"]["content"])
    except Exception as exc:
        print("opportunity planner call failed:", exc)
        return None


class LLMOpportunityPlanner:
    def detect(self, question, answer):
        samples = []
        for _ in range(config.OPPORTUNITY_VOTES):
            data = _call_json(_DETECT_SYSTEM, question, config.OPPORTUNITY_MODEL,
                              config.OPPORTUNITY_MODEL_KEEP_ALIVE, config.OPPORTUNITY_NUM_CTX,
                              temperature=0.4)
            if data is not None:
                samples.append(data)
        if not samples:
            return OpportunityDecision(has_opportunity=False)

        votes = Counter(bool(s.get("has_opportunity")) for s in samples)
        has_opportunity = votes.most_common(1)[0][0]
        if not has_opportunity:
            return OpportunityDecision(has_opportunity=False)

        agreeing = [s for s in samples if bool(s.get("has_opportunity"))]
        category_votes = Counter(s.get("category") for s in agreeing if s.get("category"))
        category = category_votes.most_common(1)[0][0] if category_votes else None
        chosen = next((s for s in agreeing if s.get("category") == category), agreeing[0])
        confidences = [float(s.get("confidence", 0)) for s in agreeing if s.get("confidence") is not None]
        avg_confidence = sum(confidences) / len(confidences) if confidences else 0.0
        return OpportunityDecision(
            has_opportunity=True, category=category, confidence=avg_confidence,
            topic_summary=chosen.get("topic_summary"), next_question=chosen.get("next_question"))

    def advance(self, thread, question):
        from .client import current_model   # local import: avoid a hard import-time cycle
        context = (f"Topic: {thread['topic_summary']}\nCollected so far: {thread['collected_info']}\n"
                  f"Last question asked: {thread.get('next_question')}\nUser just said: {question}")
        data = _call_json(_ADVANCE_SYSTEM, context, current_model(),
                          config.LLM_KEEP_ALIVE, config.LLM_NUM_CTX, temperature=0.3)
        if data is None:
            return ThreadDecision(action="close")
        action = data.get("action")
        if action not in ("continue", "ready", "close", "suppress"):
            action = "close"
        return ThreadDecision(
            action=action,
            collected_info=data.get("collected_info") or thread["collected_info"],
            next_question=data.get("next_question"),
            final_message=data.get("final_message"))


planner = LLMOpportunityPlanner()
