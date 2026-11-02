"""Central configuration. Every tunable constant lives here so the rest of the
codebase depends on a single, well-known source of settings (no magic numbers
scattered across modules).
"""
import os

HOME = os.path.expanduser("~")
WORK = os.path.join(HOME, "companion")

# ---- hardware -----------------------------------------------------------
MIC_DEVICE = "plughw:2,0"          # from `arecord -l`; card 2, device 0
LED_PIN = 17                       # listening light
TALK_PIN = 27                      # press-to-talk button
MUTE_PIN = 22                      # mute toggle (closed = muted)
TOGGLE_ON_WHEN_PRESSED = True

# ---- location / live data -------------------------------------------------
CITY_NAME = "Gurgaon, Haryana"
CITY_LAT = 28.4595
CITY_LON = 77.0266
LIVE_DATA_ENABLED = True
LIVE_TIMEOUT = 8
EGRESS_ALLOWLIST = ["api.open-meteo.com", "api.frankfurter.app", "en.wikipedia.org",
                    "hacker-news.firebaseio.com"]

# ---- recording ------------------------------------------------------------
RECORD_SECONDS = 6
DENOISE = "highpass=f=200,lowpass=f=3000"
MAX_RECORD_SECONDS = 10
SILENCE_STOP = True
SILENCE_SECS = 1.2             # trailing quiet before a recording ends. 0.6 cut people off
                               # mid-sentence on natural thinking pauses; this trades a little
                               # latency for not truncating questions.
SILENCE_THRESH = "2%"
WHISPER_THREADS = 4

# How long to wait for the wake-word listener to let go of the capture device
# before recording anyway (only one process can hold ALSA at a time).
MIC_HANDOVER_TIMEOUT = 2.0
# Lock files, not just in-process Events: CLI commands (test-voice, enroll)
# run in a DIFFERENT process from the always-on service, so thread primitives
# alone can't coordinate the handover.
MIC_LOCK_FILE = os.path.join(WORK, "mic.lock")
MIC_RELEASED_FILE = os.path.join(WORK, "mic.released")
MIC_SETTLE_SECONDS = 0.4   # ALSA doesn't free the device the instant arecord is killed

# If a transcript looks cut off mid-thought (no terminal punctuation, ends on
# a dangling conjunction/preposition), listen briefly for the rest instead of
# answering half a question.
CONTINUATION_ENABLED = True
CONTINUATION_SECONDS = 3
CONTINUATION_DANGLING_WORDS = (
    "and", "but", "or", "so", "because", "if", "when", "while", "that", "which",
    "the", "a", "an", "to", "of", "for", "in", "on", "at", "with", "about",
    "is", "was", "my", "your", "i", "um", "uh",
)

# Persistent whisper.cpp server mode: avoids reloading the model from disk on
# every question (the single biggest per-turn latency cost). Off by default
# -- needs `whisper-server` built and running first (see deploy/). Falls back
# to the whisper-cli subprocess automatically if the server isn't reachable.
WHISPER_SERVER_ENABLED = False
WHISPER_SERVER_HOST = "127.0.0.1"
WHISPER_SERVER_MAIN_PORT = 8180   # serves WHISPER_MODEL (base.en)
WHISPER_SERVER_WAKE_PORT = 8181   # serves WHISPER_WAKE_MODEL (tiny.en)
WHISPER_SERVER_TIMEOUT = 20

# ---- LLM -------------------------------------------------------------------
LLM_NUM_PREDICT = 80
LLM_NUM_CTX = 1024         # smaller context window -> faster first-token latency
LLM_KEEP_ALIVE = "30m"
STREAM_TTS = True
MODEL_PRIMARY = "llama3.2:1b"
MODEL_ALT = "qwen2.5:1.5b"
MIN_POLICY_SCORE = 2
OLLAMA_URL = "http://localhost:11434/api/chat"
EMBED_MODEL = "all-minilm"
EMBED_URL = "http://localhost:11434/api/embeddings"

# ---- face/web ---------------------------------------------------------------
FACE_PORT = 8080

# ---- wake word --------------------------------------------------------------
WAKE_WORDS = ["hey jarvis", "hi jarvis", "hey companion", "hi companion",
              "jarvis", "companion"]
WAKE_SECONDS = 2
QUESTION_SECONDS = 6
WAKE_ENERGY_GATE = True
WAKE_RMS_THRESHOLD = 350
DEBUG_TIMING = False

USE_OPENWAKEWORD = True
OWW_WAKE = ["hey_jarvis"]
OWW_THRESHOLD = 0.5
OWW_FRAME = 1280

# ---- conversation -------------------------------------------------------------
GOODBYE_WORDS = ["goodbye", "good bye", "bye bye", "go to sleep", "sleep now",
                 "stop listening", "that is all", "that's all", "see you"]
CONVO_IDLE_ROUNDS = 6
CONTEXT_TURNS = 3

# ---- guardrails ---------------------------------------------------------------
GUARDRAILS_ENABLED = True
GUARD_USE_LLM_JUDGE = False

# ---- capture quality ------------------------------------------------------------
QUESTION_RMS_THRESHOLD = 950   # measured: room floor ~477. Re-run `talk.py calibrate-noise`
                               # if you move the device or change the mic/gain.
MIN_QUESTION_CHARS = 2
# Whisper's stock hallucinations on non-speech audio -- it invents these
# confidently from wind/laughter/silence, so they must be named explicitly.
NOISE_TOKENS = {
    "you", "thank you", "thank you.", "thanks", "thanks for watching",
    "thanks for watching!", "thank you for watching", "please subscribe",
    "subscribe", "like and subscribe", "bye", "bye bye", "goodbye",
    "[blank_audio]", "blank_audio", "music", "[music]", "(music)",
    "laughter", "[laughter]", "(laughter)", "applause", "[applause]",
    "silence", "[silence]", "noise", "[noise]", "inaudible", "[inaudible]",
    "okay", "ok", "um", "uh", "hmm", "mm", "ah", "oh", "yeah",
    "the end", "end of video", "see you next time",
}

# ---- speech-vs-noise gate (VAD) -------------------------------------------------
# webrtcvad distinguishes actual speech from wind/music/TV, which a loudness
# check fundamentally cannot. Rejects noise BEFORE paying for transcription.
VAD_ENABLED = True
VAD_AGGRESSIVENESS = 3         # 0 = permissive, 3 = most aggressive at calling things non-speech
VAD_FRAME_MS = 30              # webrtcvad accepts 10, 20 or 30 only
VAD_MIN_SPEECH_RATIO = 0.6     # measured: a SILENT room still scores 14-29% speech-like frames,
                               # so anything at/below ~0.3 would wave ambient noise straight through
VAD_MIN_SPEECH_SECONDS = 0.4   # ...and that much speech in absolute terms

# ---- noise backoff ---------------------------------------------------------------
# Acknowledge noise at most once, then go quiet, then go back to sleep rather
# than answering the room forever.
NOISE_ACK_ONCE = True
NOISE_ACK_TEXT = "I heard something, but I couldn't make out a question."
NOISE_SLEEP_AFTER = 3          # consecutive noise-only rounds before sleeping

# ---- bluetooth ----------------------------------------------------------------
BT_MAC = ""

# ---- speech recognition / synthesis binaries --------------------------------
WHISPER_BIN = os.path.join(HOME, "whisper.cpp", "build", "bin", "whisper-cli")
WHISPER_MODEL = os.path.join(HOME, "whisper.cpp", "models", "ggml-base.en.bin")
WHISPER_WAKE_MODEL = os.path.join(HOME, "whisper.cpp", "models", "ggml-tiny.en.bin")

PIPER_BIN = os.path.join(HOME, "piper", "piper")
PIPER_MODEL = os.path.join(WORK, "voices", "en_US-amy-medium.onnx")
ESPEAK_VOICE = "en-us+f3"
ESPEAK_RATE = "150"
AUDIO_OUT = "auto"          # "auto" | "pulse" | "alsa"
PULSE_SINK = ""
APLAY_DEVICE = ""
# EXPERIMENTAL, off by default: reuse one Piper process for a whole streamed
# answer instead of spawning one per sentence. Relies on Piper synthesizing
# each stdin line as it arrives rather than buffering until EOF -- this has
# NOT been verified against a real Piper build. Only takes effect when
# BARGE_IN_ENABLED is False (barge-in needs the per-sentence speak() check
# this bypasses). Test carefully before relying on it.
PIPER_REUSE_PROCESS = False

# ---- accessibility --------------------------------------------------------------
CONFIRM_RMS_THRESHOLD = 450
SPEED_STEPS = {"slower": 1.3, "normal": 1.0, "faster": 0.8}
BARGE_IN_ENABLED = False
BARGE_IN_RMS = 1800
# How "sure" we need to be it's a real interruption and not an echo/transient:
# require this many consecutive loud chunks (of this size) before reacting.
BARGE_IN_CHUNK_MS = 100
BARGE_IN_CONFIRM_CHUNKS = 3
BARGE_IN_CAPTURE_SECONDS = 4     # how long to record what the user said
BARGE_IN_CLARIFY_SECONDS = 3     # how long to listen after "did you say something?"
BARGE_IN_MAX_PER_ANSWER = 2      # thrashing guard in a noisy room
BARGE_IN_BACKCHANNEL_WORDS = {
    "okay", "ok", "yeah", "yes", "uh huh", "mhm", "mm hmm", "go on", "continue",
    "right", "got it", "sure", "alright", "keep going",
}
LANG_VOICES = {"en": PIPER_MODEL, "hi": os.path.join(WORK, "voices", "hi_IN-pratham-medium.onnx")}
LANG_NAMES = {"en": "English", "hi": "Hindi"}
ESPEAK_LANG_VOICE = {"en": ESPEAK_VOICE, "hi": "hi"}

# ---- file paths (data the app persists on disk) -----------------------------------
POLICY_DIR = os.path.join(WORK, "policies")
STATUS_FILE = os.path.join(WORK, "status.json")
FACE_FILE = os.path.join(WORK, "face.html")
MODEL_FILE = os.path.join(WORK, "model.txt")
TRANSCRIPT = os.path.join(WORK, "transcript.txt")
MEM_DB = os.path.join(WORK, "memory.db")
GUARD_LOG = os.path.join(WORK, "guard.log")
EGRESS_LOG = os.path.join(WORK, "egress.log")
SPEED_FILE = os.path.join(WORK, "speed.txt")
LANG_FILE = os.path.join(WORK, "lang.txt")

# ---- speaker identity (trust & personalization) ------------------------------
# Backend is swappable: "resemblyzer" (default, light) or "speechbrain_ecapa"
# (heavier, more accurate). See companion/identity/embeddings.py.
SPEAKER_BACKEND = "resemblyzer"
SPEAKER_MATCH_THRESHOLD = 0.65   # cosine similarity needed to accept a voice match.
                                 # Measured 0.77 via `talk.py test-voice` -- 0.75 left only
                                 # 0.02 of headroom, so normal turn-to-turn variation dropped
                                 # live clips below it. Lower = more false matches between people.
ENROLL_SAMPLES = 5               # voice clips recorded per enrollment
ENROLL_SAMPLE_SECONDS = 4
IDENTITY_DB = os.path.join(WORK, "identity.db")

# ---- admin web UI (account management only, no note/content access) ---------
ADMIN_PORT = 8081
ADMIN_PASSWORD_FILE = os.path.join(WORK, "admin_password.txt")
ADMIN_SESSION_SECONDS = 3600

# ---- proactive behaviour ------------------------------------------------------
PROACTIVE_ENABLED = False        # off by default -- this is new unprompted speech
PROACTIVE_POLL_SECONDS = 60      # how often the scheduler thread wakes up
PROACTIVE_QUIET_HOURS = (22, 7)  # (start, end) local hour, wraps past midnight
PROACTIVE_MAX_PER_HOUR = 4       # global cap across every trigger combined

WELCOME_BACK_SECONDS = 6 * 3600          # "Welcome back" if away longer than this
GUEST_ENROLL_OFFER_WINDOW_SECONDS = 600  # guest turns counted within this window
GUEST_ENROLL_OFFER_THRESHOLD = 3         # offer enrollment after this many guest turns
GUEST_ENROLL_OFFER_COOLDOWN_SECONDS = 3600

PROACTIVE_MORNING_HOUR = 8        # local hour for the daily briefing
PROACTIVE_EVENING_HOUR = 21       # local hour for the end-of-day wind-down
PROACTIVE_NEWS_HOUR = 8           # local hour for the news digest (if enabled)

FX_WATCH_CHECK_SECONDS = 900      # how often queued FX watches are re-checked

CONVO_STILL_THERE_ROUND = 2       # idle round at which to ask "Still there?"

HABIT_MIN_OCCURRENCES = 3         # same-hour occurrences before suggesting a habit

# ---- automatic personal-fact capture (no "remember that..." needed) ---------
FACT_CAPTURE_ENABLED = True
FACT_CAPTURE_AUTO_CONFIDENCE = 0.75   # >= this: auto-update silently; below: ask first
# Cheap pre-filter so most turns never pay for the extraction LLM call.
FACT_CAPTURE_CUES = (
    "my name is", "call me", "i am", "i'm", "i live in", "i work at", "i work as",
    "i like", "i love", "i prefer", "i don't like", "i dont like", "i hate",
    "my favourite", "my favorite", "i was born", "my birthday",
)

# ---- proactive opportunity engine (general-purpose, not hardcoded scenarios) -
# Notices things worth proactively following up on (a mentioned date that
# could use a reminder, plans that could use a clarifying question + advice)
# via an LLM judgment call, not per-scenario rules. See proactive/opportunity_*.
OPPORTUNITY_ENABLED = True
OPPORTUNITY_SHADOW_MODE = True     # log what it WOULD suggest, don't speak it -- flip off after reviewing
OPPORTUNITY_MODEL = "qwen2.5:3b"   # heavier model, used ONLY for the rare "new opportunity" judgment
OPPORTUNITY_MODEL_KEEP_ALIVE = "2m"  # stay resident briefly -- detect() makes 3 back-to-back calls
                                   # (self-consistency voting); "0" forced a cold reload every single
                                   # vote, which is what was causing read timeouts on the Pi
OPPORTUNITY_NUM_CTX = 2048           # bigger context window for the few-shot examples
OPPORTUNITY_VOTES = 3                 # self-consistency voting sample count for the detect() call
OPPORTUNITY_CONFIDENCE_THRESHOLD = 0.7
OPPORTUNITY_CALL_TIMEOUT_SECONDS = 60   # generous -- first vote may need a cold load on Pi CPU
OPPORTUNITY_QUEUE_MAXSIZE = 3             # bounded background queue; full => drop, never block/pile up
OPPORTUNITY_THREAD_TTL_SECONDS = 3600       # abandon an open thread if it goes stale this long

