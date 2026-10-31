"""User-adjustable speech rate and reply language. Kept separate from audio
playback so both the TTS engine and the LLM prompt builder can depend on it
without creating a cycle between those two layers.

Recognized users get their own persisted preference (identity.user_prefs);
an unrecognized/guest speaker falls back to the single device-wide file.
"""
import os

from . import config, state


def _current_user_id():
    return state.current_user["id"] if state.current_user else None


def get_speech_scale():
    user_id = _current_user_id()
    if user_id is not None:
        from .identity import repository
        value = repository.get_pref(user_id, "speed_scale")
        if value is not None:
            try:
                return float(value)
            except ValueError:
                pass
    try:
        return float(open(config.SPEED_FILE).read().strip())
    except Exception:
        return 1.0


def set_speed(word):
    scale = config.SPEED_STEPS.get(word, 1.0)
    user_id = _current_user_id()
    if user_id is not None:
        from .identity import repository
        repository.set_pref(user_id, "speed_scale", scale)
    else:
        try:
            with open(config.SPEED_FILE, "w") as f:
                f.write(str(scale))
        except Exception:
            pass
    return f"Okay, I'll speak {word}." if word != "normal" else "Okay, back to a normal pace."


def get_lang():
    user_id = _current_user_id()
    if user_id is not None:
        from .identity import repository
        value = repository.get_pref(user_id, "lang")
        if value:
            return value
    try:
        return (open(config.LANG_FILE).read().strip() or "en")
    except Exception:
        return "en"


def set_lang(code):
    user_id = _current_user_id()
    if user_id is not None:
        from .identity import repository
        repository.set_pref(user_id, "lang", code)
    else:
        try:
            with open(config.LANG_FILE, "w") as f:
                f.write(code)
        except Exception:
            pass
    return f"Okay, I'll answer in {config.LANG_NAMES.get(code, code)}."


def piper_model_for_lang():
    path = config.LANG_VOICES.get(get_lang())
    return path if path and os.path.exists(path) else config.PIPER_MODEL


def lang_suffix():
    code = get_lang()
    if code == "en":
        return ""
    return f"\n\nAlways respond entirely in {config.LANG_NAMES.get(code, code)}."
