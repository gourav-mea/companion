"""Identify who is speaking from a recorded clip, or fall back to a guest.

An unrecognized or below-threshold voice (including when no backend/users are
available) always resolves to a guest (None) -- never a low-confidence guess.
"""
from .. import config
from . import repository
from .embeddings import cosine, embed_voice


def identify_speaker(wav_path):
    """Returns (user_dict_or_None, score). None means guest/unrecognized."""
    vector = embed_voice(wav_path)
    if vector is None:
        return None, 0.0
    best_user_id, best_name, best_score = None, None, 0.0
    for user_id, (name, centroid) in repository.get_centroids().items():
        score = cosine(vector, centroid)
        if score > best_score:
            best_user_id, best_name, best_score = user_id, name, score
    if best_user_id is None or best_score < config.SPEAKER_MATCH_THRESHOLD:
        return None, best_score
    repository.touch_last_active(best_user_id)
    return {"id": best_user_id, "display_name": best_name}, best_score


class DefaultSpeakerIdentifier:
    """Implements core.speaker_identifier.SpeakerIdentifier by delegating to
    identify_speaker() above -- lets this be swapped behind the Protocol
    (e.g. a future cloud-based identifier) without touching callers that
    depend on the abstraction instead of this module directly."""

    def identify_speaker(self, wav_path):
        return identify_speaker(wav_path)


identifier = DefaultSpeakerIdentifier()
