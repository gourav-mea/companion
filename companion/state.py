"""Shared mutable runtime state: GPIO handles, cross-thread signals and the
small bits of "what is happening right now" that several modules need to
read or update. Isolating this here keeps the rest of the codebase free of
module-level globals.
"""
import queue
import threading

from . import config

try:
    from gpiozero import LED, Button
    HAVE_GPIO = True
except Exception:
    HAVE_GPIO = False

led = LED(config.LED_PIN) if HAVE_GPIO else None
talk_button = Button(config.TALK_PIN, pull_up=True) if HAVE_GPIO else None
mute_switch = Button(config.MUTE_PIN, pull_up=True) if HAVE_GPIO else None

# Pressing the button is a manual wake: it sets this flag, which the main loop
# checks between listening snippets.
wake_event = threading.Event()
if talk_button is not None:
    talk_button.when_pressed = wake_event.set

# Fired timers push their spoken message here; the loops drain it at the next
# gap so a reminder is never starved by the (unfair) speak lock during a chat.
reminder_queue = queue.Queue()

# Live health (pieces currently offline), LAN face URL, queued follow-up offer
# and the last spoken answer -- all surfaced on the face / used by the loops.
offline_parts = []
pi_url = ""
pending_followup = None
last_answer = ""
# Set each turn by speaker verification: {"id": int, "display_name": str} or
# None for an unrecognized/guest speaker. Reset at the start of every turn.
current_user = None
# A memory-confirmation question ready to be spoken at the next safe gap
# (set by a background fact-capture thread, never spoken from that thread
# directly -- avoids talking over/under a recording in progress).
pending_memory_confirmation = None
# True while conversation_mode() is actively running a turn, so background
# housekeeping (e.g. the health-check ping) can avoid contending with it.
conversation_active = False
# A pending trivia question awaiting an answer on the next turn (see
# intents/trivia_cmd.py), or None.
pending_trivia = None


def switch_enabled():
    """True when the mute toggle is ON (hearing). No GPIO -> always enabled."""
    if mute_switch is None:
        return True
    pressed = mute_switch.is_pressed
    return pressed if config.TOGGLE_ON_WHEN_PRESSED else (not pressed)
