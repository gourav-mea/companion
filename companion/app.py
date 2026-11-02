"""Wires every layer together and runs the always-on listening loop. This is
the only module that knows about *all* the pieces; everything else only
depends on the layers beneath it.
"""
import os
import shutil
import threading
import time

from . import config, metrics, state
from .accessibility import get_lang  # noqa: F401  (imported for side-effect-free availability check)
from .audio.capture import record  # noqa: F401
from .audio import thinking_cue
from .audio.playback import piper_ready, pulse_player, speak
from .audio.wakeword import hears_wake, openwakeword_loop
from .bootstrap import bootstrap
from .conversation.session import conversation_mode
from .diagnostics import health_monitor
from .llm.client import current_model, warm_model
from .proactive.opportunity_worker import start_worker as start_opportunity_worker
from .proactive.scheduler import proactive_loop
from .skills.utilities import drain_reminders
from .web.admin_server import start_admin_server
from .web.face_server import lan_ip, make_qr, set_status, start_face_server


def _report_optional_deps():
    """Name the interpreter and every optional package that silently degrades
    a whole feature when missing. Running the service under system python
    while the deps live in a venv makes speaker ID return "guest" forever and
    disables the VAD noise gate -- with no error anywhere obvious.
    """
    import sys
    print(f"python: {sys.executable}")
    missing = []
    for module, feature in (("resemblyzer", "speaker recognition (personalization/memory)"),
                            ("webrtcvad", "speech-vs-noise gate"),
                            ("openwakeword", "fast wake word"),
                            ("numpy", "fast wake word"),
                            ("gpiozero", "LED + mute toggle + press-to-talk button")):
        try:
            __import__(module)
        except Exception:
            missing.append(f"{module} -> {feature} DISABLED")
    if missing:
        print("MISSING OPTIONAL DEPENDENCIES:")
        for line in missing:
            print(f"  {line}")
        print(f"  install into THIS interpreter: {sys.executable} -m pip install <package>")
    else:
        print("optional deps: all present (speaker id, vad, wake word)")


def main():
    os.makedirs(config.WORK, exist_ok=True)
    bootstrap()
    for stale in (config.MIC_LOCK_FILE, config.MIC_RELEASED_FILE):
        if os.path.exists(stale):   # left behind by a crash; would deafen the wake word
            try:
                os.remove(stale)
            except OSError:
                pass
    start_face_server()
    start_admin_server()
    start_opportunity_worker()
    state.pi_url = f"http://{lan_ip()}:{config.FACE_PORT}/face.html"
    make_qr(state.pi_url)
    threading.Thread(target=health_monitor, daemon=True).start()
    threading.Thread(target=proactive_loop, daemon=True).start()
    set_status("idle")
    if not os.path.exists(config.WHISPER_BIN):
        print("WARNING: whisper-cli missing; the wake word and speech will not work "
              "until whisper.cpp is built.")
    binary = piper_ready()
    if binary:
        print(f"voice: piper (natural)  [{binary}]")
    else:
        reasons = []
        if not (os.path.exists(config.PIPER_BIN) or shutil.which("piper")):
            reasons.append(f"no piper binary at {config.PIPER_BIN}")
        if not os.path.exists(config.PIPER_MODEL):
            reasons.append(f"no voice model at {config.PIPER_MODEL}")
        elif not os.path.exists(config.PIPER_MODEL + ".json"):
            reasons.append(f"missing {config.PIPER_MODEL}.json (needed next to the .onnx)")
        print("voice: espeak (robotic fallback) -- " + "; ".join(reasons))
    player = pulse_player()
    out = f"pulse ({os.path.basename(player)})" if player else (config.APLAY_DEVICE or "alsa default")
    print(f"gpio: {'yes' if state.HAVE_GPIO else 'no'}  model: {current_model()}  audio-out: {out}")
    _report_optional_deps()
    print("always listening. wake words:", ", ".join(config.WAKE_WORDS))
    print(f"face (open on any device on the LAN): {state.pi_url}")
    print("press the button to wake; say 'goodbye' to end a conversation.")
    threading.Thread(target=warm_model, daemon=True).start()  # load model off the critical path
    threading.Thread(target=thinking_cue.prewarm, daemon=True).start()  # pre-render cue clips off the critical path
    speak("Companion ready. Say, hi companion, or press the button.")
    try:
        while True:
            drain_reminders()
            if not state.switch_enabled():
                if state.led:
                    state.led.off()
                set_status("muted")
                state.wake_event.clear()
                time.sleep(0.4)
                continue
            if state.wake_event.is_set():
                state.wake_event.clear()
                conversation_mode()
                continue
            if config.USE_OPENWAKEWORD and openwakeword_loop(
                    state.switch_enabled, drain_reminders, conversation_mode):
                continue   # openWakeWord handled waking until the toggle went off
            awake, heard = hears_wake()
            if heard or awake:   # skip the many silent/energy-gated checks that heard nothing
                metrics.log_wake(awake, heard, source="whisper")
            if heard:
                print("heard:", heard)
            if awake:
                conversation_mode()
    except KeyboardInterrupt:
        if state.led:
            state.led.off()
        set_status("idle")
        print("\nStopped.")


def run_cli():
    import sys
    from . import cli
    args = sys.argv[1:]
    if args and args[0] in ("doctor", "--doctor"):
        cli.doctor()
    elif args and args[0] in ("selftest", "--selftest"):
        raise SystemExit(cli.selftest())
    elif args and args[0] == "set-admin-password":
        cli.set_admin_password(args[1] if len(args) > 1 else "")
    elif args and args[0] == "enroll":
        cli.enroll_cli(" ".join(args[1:]) or "User")
    elif args and args[0] == "eval-opportunities":
        cli.eval_opportunities()
    elif args and args[0] == "smoke-memory":
        raise SystemExit(cli.smoke_memory())
    elif args and args[0] == "metrics":
        raise SystemExit(cli.metrics_cli(args[1:]))
    elif args and args[0] == "calibrate-noise":
        cli.calibrate_noise()
    elif args and args[0] == "test-mic":
        cli.test_mic_cli()
    elif args and args[0] == "test-gpio":
        cli.test_gpio_cli()
    elif args and args[0] == "users":
        cli.list_users_cli()
    elif args and args[0] == "forget-user":
        cli.forget_user_cli(" ".join(args[1:]))
    elif args and args[0] == "test-voice":
        cli.test_voice_cli()
    elif args and args[0] in ("--text", "-t"):
        cli.cli_text(" ".join(args[1:]) or "what can you do")
    else:
        main()
