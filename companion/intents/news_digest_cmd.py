"""Enable/disable the proactive daily news digest."""
import re

from .. import config
from ..core.types import Response
from ..proactive import state_store as proactive_state
from .registry import registry


class NewsDigestOnIntent:
    name = "news_digest_on"
    priority = 160
    pattern = re.compile(r"\b(enable|turn on|start)\b.*news digest")

    def matches(self, question, lowered, ctx):
        return bool(self.pattern.search(lowered))

    def handle(self, question, lowered, ctx):
        proactive_state.set("news_digest_enabled", True)
        return Response("text", f"Okay, I'll give you a news briefing around "
                                 f"{config.PROACTIVE_NEWS_HOUR} o'clock each day.")


class NewsDigestOffIntent:
    name = "news_digest_off"
    priority = 170
    pattern = re.compile(r"\b(disable|turn off|stop)\b.*news digest")

    def matches(self, question, lowered, ctx):
        return bool(self.pattern.search(lowered))

    def handle(self, question, lowered, ctx):
        proactive_state.set("news_digest_enabled", False)
        return Response("text", "Okay, daily news briefings are off.")


registry.register(NewsDigestOnIntent())
registry.register(NewsDigestOffIntent())
