"""Ports (interfaces) the rest of the app depends on instead of concrete
modules. Nothing in here imports from skills/llm/audio/etc -- dependencies
only point inward, never back out (dependency inversion)."""
