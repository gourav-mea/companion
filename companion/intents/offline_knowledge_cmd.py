"""A small offline knowledge pack so common factual questions still work
without any network call -- checked before the (online) Wikipedia tool."""
import re

from ..core.types import Response
from ..data.offline_facts import OFFLINE_FACTS
from .registry import registry

_QUESTION_PREFIX = re.compile(r"^(who|what|where|when)\s+(is|was|are|were)\s+", re.I)
_PHRASE_PREFIX = re.compile(r"^(tell me about|look up|search (for|wikipedia for)?)\s+", re.I)


def _extract_topic(question):
    topic = _QUESTION_PREFIX.sub("", question.strip())
    topic = _PHRASE_PREFIX.sub("", topic).strip(" ?.").lower()
    return topic


class OfflineKnowledgeIntent:
    name = "offline_knowledge"
    priority = 245

    def matches(self, question, lowered, ctx):
        topic = _extract_topic(question)
        ctx.scratch["offline_topic"] = topic
        return topic in OFFLINE_FACTS

    def handle(self, question, lowered, ctx):
        return Response("text", OFFLINE_FACTS[ctx.scratch["offline_topic"]])


registry.register(OfflineKnowledgeIntent())
