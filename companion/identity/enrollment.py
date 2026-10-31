"""Voice enrollment: record a few samples and store a per-user embedding set.

`record_fn(seconds, tag)` must match the shape of audio.capture.record and is
injected rather than imported directly, so this module works identically
whether it's driven by a voice command or the admin UI/CLI.
"""
from .. import config
from . import repository
from .embeddings import embed_voice


def enroll_user(display_name, record_fn, speak_fn=None, role="user", samples=None):
    samples = samples or config.ENROLL_SAMPLES
    user = repository.get_user_by_name(display_name)
    user_id = user["id"] if user else repository.create_user(display_name, role=role)
    collected = 0
    for i in range(samples):
        if speak_fn:
            speak_fn(f"Please say a sentence so I can learn your voice. Sample {i + 1} of {samples}.")
        wav = record_fn(config.ENROLL_SAMPLE_SECONDS, tag=f"enroll_{user_id}_{i}")
        vector = embed_voice(wav)
        if vector is None:
            continue
        repository.add_embedding(user_id, vector)
        collected += 1
    if collected == 0:
        return False, "I could not learn your voice this time. Please check the microphone and try again."
    return True, (f"Thanks, {display_name}. I've learned your voice from "
                  f"{collected} sample{'s' if collected != 1 else ''}.")
