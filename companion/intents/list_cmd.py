"""Per-user shopping/to-do list voice commands."""
import re

from ..core.types import Response
from ..skills import lists
from .registry import registry


class AddListItemIntent:
    name = "add_list_item"
    priority = 71
    pattern = re.compile(r"\badd\s+(.+?)\s+to\s+(?:my\s+)?(?:shopping\s+)?list\b")

    def matches(self, question, lowered, ctx):
        return bool(self.pattern.search(lowered))

    def handle(self, question, lowered, ctx):
        item = self.pattern.search(lowered).group(1).strip()
        user_id = ctx.speaker["id"] if ctx.speaker else None
        lists.add_item(user_id, item)
        return Response("text", f"Added {item} to your list.")


class ShowListIntent:
    name = "show_list"
    priority = 72
    pattern = re.compile(r"\bwhat'?s on my list\b|\bread my list\b|\bshow my list\b")

    def matches(self, question, lowered, ctx):
        return bool(self.pattern.search(lowered))

    def handle(self, question, lowered, ctx):
        user_id = ctx.speaker["id"] if ctx.speaker else None
        items = lists.list_items(user_id)
        if not items:
            return Response("text", "Your list is empty.")
        return Response("text", "On your list: " + ", ".join(item for _id, item in items) + ".")


class RemoveListItemIntent:
    name = "remove_list_item"
    priority = 73
    pattern = re.compile(r"\bremove\s+(.+?)\s+from\s+(?:my\s+)?list\b")

    def matches(self, question, lowered, ctx):
        return bool(self.pattern.search(lowered))

    def handle(self, question, lowered, ctx):
        hint = self.pattern.search(lowered).group(1).strip()
        user_id = ctx.speaker["id"] if ctx.speaker else None
        removed = lists.remove_item(user_id, hint)
        if removed:
            return Response("text", f"Removed {removed} from your list.")
        return Response("text", "I couldn't find that on your list.")


class ClearListIntent:
    name = "clear_list"
    priority = 74
    pattern = re.compile(r"\bclear my list\b")

    def matches(self, question, lowered, ctx):
        return bool(self.pattern.search(lowered))

    def handle(self, question, lowered, ctx):
        user_id = ctx.speaker["id"] if ctx.speaker else None
        lists.clear_list(user_id)
        return Response("text", "Your list is cleared.")


registry.register(AddListItemIntent())
registry.register(ShowListIntent())
registry.register(RemoveListItemIntent())
registry.register(ClearListIntent())
