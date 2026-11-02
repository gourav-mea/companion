"""Shared mutable runtime state: GPIO handles, cross-thread signals and the
small bits of "what is happening right now" that several modules need to
read or update. Isolating this here keeps the rest of the codebase free of
module-level globals.
"""
import queue
import threading

from . import config

# Importing gpiozero can succeed while constructing a device still fails (no
# pin factory, e.g. lgpio missing on a Pi 5, or no /dev/gpiomem access), so the
# devices are built inside the guard too -- GPIO is optional, never fatal.
try:
    from gpiozero import LED, Button
    led = LED(config.LED_PIN)
    talk_button = Button(config.TALK_PIN, pull_up=True)
    mute_switch = Button(config.MUTE_PIN, pull_up=True)
    HAVE_GPIO = True
except Exception as _gpio_exc:
    print(f"gpio unavailable ({_gpio_exc}); LED, mute toggle and button are disabled")
    led = talk_button = mute_switch = None
    HAVE_GPIO = False

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
# Cosine similarity from the most recent speaker-ID attempt, for diagnostics.
last_match_score = 0.0
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
# Set when a proactive opportunity nudge is delivered: {"user_id", "category",
# "ts"}. The next user turn is attributed back to it (a clear "no" => dismissal)
# so the engine can learn to stop pestering. Consumed/cleared after one turn.
pending_opportunity_feedback = None

# ---- microphone ownership ---------------------------------------------------
# Only one process may hold the ALSA capture device at a time. The wake-word
# listener streams from it continuously, so anything else needing the mic
# (a question, an enrollment from the admin UI) must ask it to let go first:
# set mic_pause, wait for mic_released, then take mic_lock.
mic_lock = threading.RLock()
mic_pause = threading.Event()
mic_released = threading.Event()


def switch_enabled():
    """True when the mute toggle is ON (hearing). No GPIO -> always enabled."""
    if mute_switch is None:
        return True
    pressed = mute_switch.is_pressed
    return pressed if config.TOGGLE_ON_WHEN_PRESSED else (not pressed)
