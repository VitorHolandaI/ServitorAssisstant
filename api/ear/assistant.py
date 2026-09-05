"""Ties the ear to the two local models: heard -> transcribed -> answered."""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ear.brain import LocalBrain, ServerBrain
from ear.devices import guard_device
from ear.ear import EarConfig
from ear.transcribe import OpenVinoWhisper

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Reply:
    """An answer, the language it should be spoken in, and what prompted it.

    `heard` travels with the reply so the ear can show the transcript without
    reaching into the assistant: the ear never sees the audio decoded, only
    what comes back from the responder.
    """

    text: str
    language: str = "en"
    heard: str = ""


def _brain_for(config: EarConfig):
    """Pick what answers a command: the server, the local tools, or the model.

    Only one of these is ever built. They each hold their own copy of the
    model on the accelerator, and this machine has room for one.
    """
    if config.server_url:
        return ServerBrain(config.server_url)
    if config.agent_enabled and config.mcp_addresses:
        from ear.agent_brain import AgentBrain

        return AgentBrain(
            config.llm_model,
            guard_device(config.llm_device, "the language model", model_dir=config.llm_model),
            list(config.mcp_addresses),
            config.mcp_profile,
            free_after_turn=config.agent_free_after_turn,
        )
    return LocalBrain(config.llm_model, config.llm_device)


class LocalAssistant:
    """The responder the ear calls once it has captured a command."""

    def __init__(self, config: EarConfig):
        self.whisper = OpenVinoWhisper(config.whisper_model, config.whisper_device)
        self.brain = _brain_for(config)
        self.idle_unload_seconds = config.idle_unload_seconds
        if hasattr(self.brain, "ask_user"):
            self.brain.ask_user = self.ask_user
        # Set by the ear. The transcript is worth showing before the model has
        # finished thinking - that is most of the wait, and seeing the words
        # early is how you catch a misheard command while it still matters.
        self.on_heard: Callable[[str], None] | None = None
        # Set by the ear. A tool that needs a choice asks through this: the
        # ear speaks the question and records, Whisper turns it into text.
        self.ask_audio: Callable[[str], bytes | None] | None = None

    def warm(self) -> None:
        """Compile both models before the first wake, not during it."""
        self.whisper.warm()
        self.brain.warm()

    def ask_user(self, question: str) -> str:
        """Ask the user something mid-tool-call and return what they said."""
        if self.ask_audio is None:
            return ""
        wav = self.ask_audio(question)
        if not wav:
            return ""
        heard = self.whisper.transcribe(wav)
        logger.info(f"[Assistant] answered [{heard.language}]: {heard.text!r}")
        return heard.text

    def forget(self) -> None:
        """Drop the conversation once the wake session that held it ends."""
        self.brain.reset()

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
        if self.on_heard is not None:
            self.on_heard(heard.text)
        # Whisper already decided what language this was; telling the model
        # beats asking it to notice, which it does not reliably do.
        answer = self.brain.answer(heard.text, heard.language)
        logger.info(f"[Assistant] reply: {answer!r}")
        # Spoken back in the language it was asked in, using the voice that
        # actually belongs to that language.
        return Reply(answer, heard.language, heard.text) if answer else None


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
