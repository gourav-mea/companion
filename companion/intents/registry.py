"""The shared IntentRegistry instance every intent module registers into at
import time. companion/intents/__init__.py imports every submodule so this
registry is fully populated by the time anything calls dispatch()."""
from ..core.intent import IntentRegistry

registry = IntentRegistry()
