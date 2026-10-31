"""A quick offline joke/one-liner."""
import random
import re

from ..core.types import Response
from ..data.jokes import JOKES
from .registry import registry


class JokeIntent:
    name = "joke"
    priority = 85
    pattern = re.compile(r"\b(tell me a joke|say something funny|make me laugh)\b")

    def matches(self, question, lowered, ctx):
        return bool(self.pattern.search(lowered))

    def handle(self, question, lowered, ctx):
        return Response("text", random.choice(JOKES))


registry.register(JokeIntent())
