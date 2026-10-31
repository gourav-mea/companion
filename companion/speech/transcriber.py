"""Speech recognition via whisper.cpp -- a persistent HTTP server when
available (no repeated model-load cost per question, see config.
WHISPER_SERVER_ENABLED), falling back to the whisper-cli subprocess
otherwise, the same degrade-gracefully pattern the rest of the TTS/voice
stack already uses (Piper -> espeak, etc.).
"""
import os
import subprocess

import requests

from .. import config


def wake_model():
    return config.WHISPER_WAKE_MODEL if os.path.exists(config.WHISPER_WAKE_MODEL) else config.WHISPER_MODEL


def _server_port(model):
    return (config.WHISPER_SERVER_WAKE_PORT if model == config.WHISPER_WAKE_MODEL
            else config.WHISPER_SERVER_MAIN_PORT)


def _server_base_url(model):
    return f"http://{config.WHISPER_SERVER_HOST}:{_server_port(model)}"


def server_reachable(model):
    try:
        r = requests.get(_server_base_url(model) + "/", timeout=2)
        return r.status_code < 500
    except Exception:
        return False


def _transcribe_via_server(wav, model):
    """Returns the transcript, or None if the server didn't answer (caller
    falls back to the CLI -- this never raises)."""
    try:
        with open(wav, "rb") as f:
            r = requests.post(_server_base_url(model) + "/inference",
                              files={"file": f}, data={"response_format": "json"},
                              timeout=config.WHISPER_SERVER_TIMEOUT)
        r.raise_for_status()
        return (r.json().get("text") or "").strip()
    except Exception as exc:
        print("whisper server unreachable, falling back to whisper-cli:", exc)
        return None


def _transcribe_via_cli(wav, model):
    if not os.path.exists(config.WHISPER_BIN) or not os.path.exists(model):
        return ""
    try:
        res = subprocess.run(
            [config.WHISPER_BIN, "-m", model, "-f", wav, "-nt",
             "-t", str(config.WHISPER_THREADS), "-bs", "1", "-bo", "1"],
            capture_output=True, text=True, check=False)
    except Exception as exc:
        print("whisper-cli failed:", exc)
        return ""
    lines = []
    for line in res.stdout.splitlines():
        piece = line.split("]", 1)[1] if "]" in line else line
        piece = piece.strip()
        if piece:
            lines.append(piece)
    return " ".join(lines).strip()


def transcribe(wav, model=None):
    model = model or config.WHISPER_MODEL
    try:
        if config.WHISPER_SERVER_ENABLED:
            text = _transcribe_via_server(wav, model)
            if text is not None:
                return text
        return _transcribe_via_cli(wav, model)
    finally:
        if os.path.exists(wav):
            try:
                os.remove(wav)
            except OSError:
                pass

