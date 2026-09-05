"""Ties the ear to the two local models: heard -> transcribed -> answered."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from ear.brain import LocalBrain, ServerBrain
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
        self.brain = (
            ServerBrain(config.server_url)
            if config.server_url
            else LocalBrain(config.llm_model, config.llm_device)
        )
        self.idle_unload_seconds = config.idle_unload_seconds

    def warm(self) -> None:
        """Compile both models before the first wake, not during it."""
        self.whisper.warm()
        self.brain.warm()

    def tick(self) -> None:
        """Called from the listening loop; drops the LLM once talk has stopped.

        Freeing it after every single turn costs 3.8 s to rebuild on the next
        one, measured on qwen3-4b, which is most of a follow-up question's
        latency. Freeing it after the conversation ends costs nothing and
        returns the same ~975 MB.
        """
        self.brain.unload_if_idle(self.idle_unload_seconds)

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
    if config.server_url:
        logger.info(f"[Assistant] server mode: {config.server_url}")
        missing = [p for p in [config.whisper_model] if not p.is_dir()]
        return _build_helper(config, missing)
    missing = [p for p in (config.whisper_model, config.llm_model) if not p.is_dir()]
    logger.info(f"[Assistant] local mode, missing: {[p.name for p in missing] or 'nothing'}")
    return _build_helper(config, missing)


def _build_helper(config: EarConfig, missing: list[Path]) -> LocalAssistant | None:
    if missing:
        logger.warning(f"[Assistant] disabled, models not found: {', '.join(str(p) for p in missing)}")
        return None
    return LocalAssistant(config)
