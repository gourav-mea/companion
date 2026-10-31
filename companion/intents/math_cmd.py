"""Quick local maths (percent/units/arithmetic) -- no LLM call needed."""
from ..core.types import Response
from ..skills import utilities
from .registry import registry


class MathIntent:
    name = "math"
    priority = 220

    def matches(self, question, lowered, ctx):
        result = utilities.try_math(question)
        ctx.scratch["math_result"] = result   # avoid recomputing in handle()
        return result is not None

    def handle(self, question, lowered, ctx):
        return Response("text", ctx.scratch["math_result"])


registry.register(MathIntent())
