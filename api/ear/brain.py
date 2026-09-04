"""The local model that answers a spoken command.

Unlike the Whisper pipeline, this one is built once and kept. The NPU state bug
does not reach it: `LLMInferRequest` dispatches on `position_ids`, not on the
stored-token counter that Whisper's request leaves dirty. It also runs on the
GPU, where that plugin is not involved at all.
"""
from __future__ import annotations

import logging
from pathlib import Path

from ear.devices import guard_device

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are the Servitor, a terse voice assistant running locally on Vitor's laptop. "
    "Your reply is going to be spoken aloud, so answer in one or two short sentences. "
    "Never use markdown, bullet points, code blocks or emoji. "
    "Reply in the same language the user spoke. "
    "If you do not know something, say so plainly instead of inventing it."
)


class LocalBrain:
    """Wraps an OpenVINO LLM pipeline and keeps a short spoken-turn history."""

    def __init__(
        self,
        model_dir: Path,
        device: str = "GPU",
        cache_dir: Path | None = None,
        max_new_tokens: int = 120,
        history_turns: int = 6,
    ):
        self.model_dir = Path(model_dir)
        self.device = guard_device(device, "the language model")
        self.cache_dir = Path(cache_dir) if cache_dir else Path.home() / ".cache" / "servitor" / "ov-cache"
        self.max_new_tokens = max_new_tokens
        self.history_turns = history_turns
        if not self.model_dir.is_dir():
            raise FileNotFoundError(f"LLM model not found: {self.model_dir}")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._pipeline = None
        self._chatting = False
        self._turns = 0

    def warm(self) -> None:
        self._ensure()

    def _ensure(self):
        if self._pipeline is None:
            import openvino_genai as ov_genai

            logger.info(f"[Brain] loading {self.model_dir.name} on {self.device}")
            self._pipeline = ov_genai.LLMPipeline(
                str(self.model_dir), self.device, CACHE_DIR=str(self.cache_dir)
            )
            logger.info("[Brain] model ready")
        return self._pipeline

    def answer(self, question: str) -> str:
        """Ask the model, keeping the turn inside its own chat session.

        The chat template comes from the model directory rather than being
        written out here. A hand-built ChatML prompt looked right and mostly
        worked, but one turn in three came back as a run of "!" — a wrong
        template degenerates quietly instead of failing.
        """
        import openvino_genai as ov_genai

        pipeline = self._ensure()
        if not self._chatting:
            pipeline.start_chat(SYSTEM_PROMPT)
            self._chatting = True

        config = ov_genai.GenerationConfig()
        config.max_new_tokens = self.max_new_tokens
        # Greedy, with a mild penalty: a spoken answer that repeats itself is
        # far worse to listen to than one that is a little plain.
        config.do_sample = False
        config.repetition_penalty = 1.15

        reply = str(pipeline.generate(question, config)).strip()
        self._turns += 1
        if self._turns >= self.history_turns:
            # Bound the context the same way the spoken conversation is bounded.
            self.reset()
        return reply

    def reset(self) -> None:
        """Drop the conversation, keeping the loaded model."""
        if self._pipeline is not None and self._chatting:
            self._pipeline.finish_chat()
        self._chatting = False
        self._turns = 0
