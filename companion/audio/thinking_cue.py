"""Latency-adaptive "thinking" cue: a short sound played only when an answer
is slow to start, so the device doesn't sit in dead silence while a local
model reasons or a live-data fetch is in flight.

Two tiers, both optional and configurable (see config.THINKING_CUE_*):
  1. after THINKING_CUE_DELAY of silence -> a soft earcon (a synthesized
     tone: language-agnostic, and it never wears out its welcome);
  2. after THINKING_CUE_ESCALATE -> a short spoken phrase ("Let me think
     about that."), picked by what we're doing (llm reasoning vs live lookup).

Nothing plays if the answer starts quickly, and the caller only ever creates a
cue for the slow route kinds -- never before an instant built-in answer. The
cue runs on its own thread and is killed the instant the real answer is ready
(`stop()`), so it never overlaps or delays actual speech. It also holds the
shared speak lock while a clip plays, obeying the same "serialise all speech"
invariant speak() relies on.
"""
import hashlib
import math
import os
import random
import struct
import threading
import wave

from .. import accessibility, config
from . import playback

_EARCON_READY = False
_earcon_lock = threading.Lock()


def _write_earcon(path):
    """Synthesize a soft two-note chime (mono 16-bit, 22.05 kHz). A tone rather
    than a spoken "hmm": it reads as "working" in any language, and short fades
    keep it click-free and unobtrusive."""
    rate = 22050
    notes = ((523.25, 0.12), (659.25, 0.14))   # C5 -> E5, a gentle rising blip
    amp = 0.18                                  # soft; this is background, not an alert
    frames = bytearray()
    for freq, dur in notes:
        n = int(rate * dur)
        fade = max(1, int(rate * 0.015))        # fade in/out to avoid clicks
        for i in range(n):
            env = min(1.0, i / fade, (n - i) / fade)
            sample = amp * env * math.sin(2 * math.pi * freq * i / rate)
            frames += struct.pack("<h", int(sample * 32767))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(frames))


def _earcon_path():
    """Path to the cached earcon WAV, synthesizing it once on first use."""
    global _EARCON_READY
    path = config.THINKING_CUE_FILE
    if _EARCON_READY and os.path.exists(path):
        return path
    with _earcon_lock:
        if not os.path.exists(path):
            try:
                _write_earcon(path)
            except Exception:
                return None
        _EARCON_READY = True
    return path


def _phrase_path(text, lang):
    """Cached WAV for a spoken cue phrase, rendered once per (lang, text)."""
    key = hashlib.md5(f"{lang}:{text}".encode("utf-8")).hexdigest()[:10]
    path = os.path.join(config.WORK, f"cue_{lang}_{key}.wav")
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return path
    return playback.render_to_wav(text, path)


def prewarm():
    """Render the earcon + every cue phrase for the current language up front,
    off the critical path, so the first slow turn doesn't pay synthesis cost
    inside the very gap it is trying to fill. Safe to call in a daemon thread."""
    if not config.THINKING_CUE_ENABLED:
        return
    try:
        _earcon_path()
        lang = accessibility.get_lang()
        for phrases in config.THINKING_CUE_PHRASES.values():
            for text in phrases:
                _phrase_path(text, lang)
    except Exception:
        pass


class ThinkingCue:
    """One thinking cue for one turn. start() it right after routing; stop() it
    the instant the real answer begins. stop() is idempotent and blocks until
    the cue has fully released the shared speak lock, so the caller's next
    speak() can take it without contention or overlap."""

    def __init__(self, kind):
        self._kind = kind
        self._cancel = threading.Event()
        self._thread = None
        self._proc = None
        self._proc_lock = threading.Lock()

    def start(self):
        if not config.THINKING_CUE_ENABLED:
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        try:
            # Tier 1: wait out the initial delay; the answer usually beats it.
            if self._cancel.wait(config.THINKING_CUE_DELAY):
                return
            if config.THINKING_CUE_EARCON:
                self._play(_earcon_path())
            # Tier 2: a spoken phrase only if it's STILL going a while later.
            remaining = config.THINKING_CUE_ESCALATE - config.THINKING_CUE_DELAY
            if self._cancel.wait(max(0.0, remaining)):
                return
            phrases = config.THINKING_CUE_PHRASES.get(self._kind)
            if phrases:
                self._play(_phrase_path(random.choice(phrases), accessibility.get_lang()))
        except Exception:
            pass

    def _play(self, path):
        if not path or self._cancel.is_set():
            return
        # Hold the shared speak lock so the cue can't overlap a real reply, and
        # release it the moment we're done or cancelled so the answer's speak()
        # can take it immediately.
        with playback.SPEAK_LOCK:
            if self._cancel.is_set():
                return
            try:
                with self._proc_lock:
                    self._proc = playback.start_file_player(path)
                while self._proc.poll() is None:
                    if self._cancel.wait(0.05):   # responsive to stop()
                        break
            except Exception:
                pass
            finally:
                self._terminate()

    def _terminate(self):
        with self._proc_lock:
            proc, self._proc = self._proc, None
        if proc and proc.poll() is None:
            try:
                proc.terminate()
            except Exception:
                pass

    def stop(self):
        """Cancel any pending or in-progress cue immediately. Idempotent."""
        if self._cancel.is_set():
            return
        self._cancel.set()
        self._terminate()
        t = self._thread
        if t and t.is_alive() and t is not threading.current_thread():
            t.join(timeout=1.0)   # ensure the speak lock is released before we return
