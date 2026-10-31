"""Switch/inspect which Ollama model answers questions."""
import re

from ..core.types import Response
from ..llm.client import current_model, toggle_model
from .registry import registry


class SwitchModelIntent:
    name = "switch_model"
    priority = 30
    pattern = re.compile(r"\b(switch|change|toggle|swap)\b.*model|use the (other|faster|better) model")

    def matches(self, question, lowered, ctx):
        return bool(self.pattern.search(lowered))

    def handle(self, question, lowered, ctx):
        return Response("text", toggle_model())


class WhichModelIntent:
    name = "which_model"
    priority = 40
    pattern = re.compile(r"\b(which|what) model")

    def matches(self, question, lowered, ctx):
        return bool(self.pattern.search(lowered))

    def handle(self, question, lowered, ctx):
        return Response("text", f"I am using the {current_model().split(':')[0]} model.")


registry.register(SwitchModelIntent())
registry.register(WhichModelIntent())
