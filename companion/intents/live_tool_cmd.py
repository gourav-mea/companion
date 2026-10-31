"""Weather/FX (early, before policy) and Wikipedia/news (late, after policy)
-- "late" keeps broad phrasing from hijacking a policy answer."""
from ..core.types import Response
from ..skills import live_data
from .registry import registry


class EarlyLiveToolIntent:
    name = "live_tool_early"
    priority = 230

    def matches(self, question, lowered, ctx):
        tool = live_data.match_live_tool(question, late=False)
        ctx.scratch["live_tool_early"] = tool
        return tool is not None

    def handle(self, question, lowered, ctx):
        return Response("live", (ctx.scratch["live_tool_early"], question))


class LateLiveToolIntent:
    name = "live_tool_late"
    priority = 250

    def matches(self, question, lowered, ctx):
        tool = live_data.match_live_tool(question, late=True)
        ctx.scratch["live_tool_late"] = tool
        return tool is not None

    def handle(self, question, lowered, ctx):
        return Response("live", (ctx.scratch["live_tool_late"], question))


registry.register(EarlyLiveToolIntent())
registry.register(LateLiveToolIntent())
