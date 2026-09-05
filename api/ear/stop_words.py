"""Recognising that the user asked it to stop.

Ending a conversation must not depend on the model agreeing to end it. Asked
to stop, it answered "I will stop listening" and kept the microphone open,
because stopping was only ever a sentence it generated - nothing acted on it.

So this is decided before the model is consulted at all: a short utterance
that is only a stop word ends the turn, and the ear closes the conversation
itself. Kept narrow on purpose. "Stop the timer" is a command about a timer,
not a request to be left alone, so only an utterance that is essentially
nothing but stopping counts.
"""
from __future__ import annotations

import re

# Decided token by token: an utterance is a refusal when every word in it is
# one. "stop stop listening" is three words and still nothing but stopping;
# "stop the timer" has a word that means something else, so it is a command.
_STOP_TOKENS = {
    # english
    "stop", "stopped", "stopping", "listening", "listen", "quiet", "silence",
    "cancel", "cancelled", "nevermind", "mind", "never", "shut", "up",
    "forget", "it", "that", "is", "all", "thats", "nothing", "none",
    "no", "thanks", "thank", "you", "goodbye", "bye", "done", "enough",
    # portuguese
    "para", "parar", "pare", "parou", "chega", "cala", "boca", "quieto",
    "quieta", "cancela", "cancelar", "deixa", "pra", "la", "esquece",
    "esqueca", "nada", "so", "isso", "obrigado", "obrigada", "valeu",
    "tchau", "nao", "silencio",
}
# Words that carry no meaning of their own here.
_FILLER = {"please", "now", "ok", "okay", "just", "por", "favor", "agora",
           "ta", "e", "a", "o", "the", "me", "my"}

_PUNCTUATION = re.compile(r"[^\w\s]", re.UNICODE)
# A real command that happens to contain "stop" is longer than this.
MAX_WORDS = 4


def _normalise(text: str) -> str:
    text = _PUNCTUATION.sub(" ", (text or "").lower())
    return " ".join(text.split())


def is_stop_phrase(text: str) -> bool:
    """Whether this utterance is nothing but a request to stop."""
    words = _normalise(text).split()
    if not words or len(words) > MAX_WORDS:
        return False
    meaningful = [w for w in words if w not in _FILLER]
    if not meaningful:
        return False
    return all(word in _STOP_TOKENS for word in meaningful)
