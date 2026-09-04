"""Ties the ear to the two local models: heard -> transcribed -> answered."""
from __future__ import annotations

import logging
from dataclasses import dataclass

from ear.brain import LocalBrain
from ear.ear import EarConfig
from ear.transcribe import OpenVinoWhisper

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Reply:
    """An answer, and the language it should be spoken in."""

    text: str
    language: str = "en"


class LocalAssistant:
    """The responder the ear calls once it has captured a command."""

    def __init__(self, config: EarConfig):
        self.whisper = OpenVinoWhisper(config.whisper_model, config.whisper_device)
        self.brain = LocalBrain(config.llm_model, config.llm_device)

    def warm(self) -> None:
        """Compile both models before the first wake, not during it."""
        self.whisper.warm()
        self.brain.warm()

    def __call__(self, wav_bytes: bytes) -> Reply | None:
        heard = self.whisper.transcribe(wav_bytes)
        logger.info(f"[Assistant] heard [{heard.language}]: {heard.text!r}")
        if not heard:
            return None
        answer = self.brain.answer(heard.text)
        logger.info(f"[Assistant] reply: {answer!r}")
        # Spoken back in the language it was asked in, using the voice that
        # actually belongs to that language.
        return Reply(answer, heard.language) if answer else None


def build(config: EarConfig) -> LocalAssistant | None:
    """Return an assistant, or None when its models are not installed.

    A missing model must degrade the ear to wake-and-acknowledge rather than
    crash it: the always-on half is useful on its own, and is the half that a
    user notices is broken.
    """
    missing = [p for p in (config.whisper_model, config.llm_model) if not p.is_dir()]
    if missing:
        logger.warning(f"[Assistant] disabled, models not found: {', '.join(str(p) for p in missing)}")
        return None
    return LocalAssistant(config)
