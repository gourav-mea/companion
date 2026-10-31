"""Chat, streaming chat and embeddings against a local Ollama server."""
import json

import requests

from .. import config


def offline_message():
    """A specific, actionable message when Ollama is confirmed unreachable
    (per diagnostics.health_monitor's ongoing check), instead of a generic
    apology -- most useful for whoever is maintaining the device."""
    from .. import state
    if "brain" in state.offline_parts:
        return ("My reasoning engine isn't responding. If you're setting this up, check that "
                "Ollama is running -- try: ollama serve, or systemctl status ollama.")
    return "Sorry, my thinking part is not responding."


def chat(system, user, model=None):
    payload = {"model": model or current_model(), "stream": False,
               "keep_alive": config.LLM_KEEP_ALIVE,
               "options": {"num_predict": config.LLM_NUM_PREDICT, "temperature": 0.3,
                           "num_thread": config.WHISPER_THREADS, "num_ctx": config.LLM_NUM_CTX},
               "messages": [{"role": "system", "content": system},
                            {"role": "user", "content": user}]}
    try:
        r = requests.post(config.OLLAMA_URL, json=payload, timeout=240)
        return r.json()["message"]["content"].strip()
    except Exception:
        return offline_message()


def chat_stream(system, user, model=None):
    """Yield answer text in chunks as the model produces them."""
    payload = {"model": model or current_model(), "stream": True,
               "keep_alive": config.LLM_KEEP_ALIVE,
               "options": {"num_predict": config.LLM_NUM_PREDICT, "temperature": 0.3,
                           "num_thread": config.WHISPER_THREADS, "num_ctx": config.LLM_NUM_CTX},
               "messages": [{"role": "system", "content": system},
                            {"role": "user", "content": user}]}
    try:
        with requests.post(config.OLLAMA_URL, json=payload, stream=True, timeout=240) as r:
            for line in r.iter_lines():
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except Exception:
                    continue
                chunk = (data.get("message") or {}).get("content", "")
                if chunk:
                    yield chunk
                if data.get("done"):
                    break
    except Exception:
        return


def warm_model():
    """Load the model into RAM in the background so the first reply is quick."""
    try:
        requests.post(config.OLLAMA_URL, timeout=120, json={
            "model": current_model(), "stream": False, "keep_alive": config.LLM_KEEP_ALIVE,
            "options": {"num_predict": 1},
            "messages": [{"role": "user", "content": "hi"}]})
    except Exception:
        pass


def embed(text):
    """Local embedding vector from Ollama (loopback), or None if unavailable."""
    try:
        r = requests.post(config.EMBED_URL, json={"model": config.EMBED_MODEL, "prompt": text},
                          timeout=config.LIVE_TIMEOUT)
        vec = r.json().get("embedding")
        return vec if isinstance(vec, list) and vec else None
    except Exception:
        return None


def current_model():
    try:
        m = open(config.MODEL_FILE).read().strip()
        return m or config.MODEL_PRIMARY
    except Exception:
        return config.MODEL_PRIMARY


def toggle_model():
    cur = current_model()
    target = config.MODEL_ALT if cur == config.MODEL_PRIMARY else config.MODEL_PRIMARY
    with open(config.MODEL_FILE, "w") as f:
        f.write(target)
    short = target.split(":")[0]
    return f"Now using the {short} model."
