"""Lightweight offline trivia game -- fully local, no network. Starting a
question stashes it in state.pending_trivia; TriviaAnswerIntent (priority 5,
lower than everything else) intercepts the next turn to check the answer."""
import random
import re

from .. import state
from ..core.types import Response
from ..data.trivia import TRIVIA
from .registry import registry


class TriviaAnswerIntent:
    name = "trivia_answer"
    priority = 5   # runs before everything else while an answer is pending

    def matches(self, question, lowered, ctx):
        return state.pending_trivia is not None

    def handle(self, question, lowered, ctx):
        item = state.pending_trivia
        state.pending_trivia = None
        if any(answer in lowered for answer in item["answers"]):
            return Response("text", "That's correct!")
        return Response("text", f"Not quite -- the answer was {item['answers'][0]}.")


class StartTriviaIntent:
    name = "start_trivia"
    priority = 6
    pattern = re.compile(r"\b(trivia|quiz me|ask me a (trivia )?question|play a game)\b")

    def matches(self, question, lowered, ctx):
        return bool(self.pattern.search(lowered))

    def handle(self, question, lowered, ctx):
        item = random.choice(TRIVIA)
        state.pending_trivia = item
        return Response("text", item["question"])


registry.register(TriviaAnswerIntent())
registry.register(StartTriviaIntent())
