"""Background health monitoring (keeps the face's degradation banner current)."""
import os
import time

import requests

from . import config, state
from .audio.playback import piper_ready
from .llm.client import current_model


def health_monitor():
    """Background: keep state.offline_parts current so the face can show a banner."""
    while True:
        parts = []
        if not os.path.exists(config.WHISPER_BIN) or not os.path.exists(config.WHISPER_MODEL):
            parts.append("speech")
        if not piper_ready():
            parts.append("voice")
        if state.conversation_active:
            # Don't contend with a live conversation for Ollama's compute --
            # just carry forward last cycle's "brain" status instead of re-probing.
            if "brain" in state.offline_parts:
                parts.append("brain")
        else:
            try:
                requests.post(config.OLLAMA_URL, timeout=4, json={
                    "model": current_model(), "stream": False, "options": {"num_predict": 1},
                    "messages": [{"role": "user", "content": "hi"}]})
            except Exception:
                parts.append("brain")
        state.offline_parts = parts
        time.sleep(15)
