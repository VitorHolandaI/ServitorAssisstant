"""The local model that answers a spoken command."""
from __future__ import annotations

import gc
import logging
import time
from pathlib import Path

from ear.devices import guard_device

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are the Servitor, a terse voice assistant running locally on Vitor's laptop. "
    "Your reply is going to be spoken aloud, so answer in one or two short sentences. "
    "Never use markdown, bullet points, code blocks or emoji. "
    "If you do not know something, say so plainly instead of inventing it."
)


# Whisper reports a code; the model answers better when told the name.
LANGUAGE_NAMES = {
    "en": "English", "pt": "Portuguese", "es": "Spanish", "fr": "French",
    "it": "Italian", "de": "German", "nl": "Dutch", "ja": "Japanese",
    "zh": "Chinese", "ko": "Korean", "ru": "Russian", "hi": "Hindi",
}


def language_clause(language: str | None) -> str:
    """An explicit instruction to answer in one language, or nothing."""
    name = LANGUAGE_NAMES.get((language or "").strip().lower())
    return f" Answer in {name}, whatever language these instructions are in." if name else ""


class LocalBrain:
    """Wraps an OpenVINO LLM pipeline and keeps a short spoken-turn history."""

    def __init__(
        self,
        model_dir: Path,
        device: str = "GPU",
        cache_dir: Path | None = None,
        max_new_tokens: int = 256,
        history_turns: int = 6,
    ):
        self.model_dir = Path(model_dir)
        self.device = guard_device(device, "the language model", model_dir=self.model_dir)
        self.cache_dir = Path(cache_dir) if cache_dir else Path.home() / ".cache" / "servitor" / "ov-cache"
        self.max_new_tokens = max_new_tokens
        self.history_turns = history_turns
        if not self.model_dir.is_dir():
            raise FileNotFoundError(f"LLM model not found: {self.model_dir}")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._pipeline = None
        self._history: list[dict] = []
        self._last_used = 0.0

    def warm(self) -> None:
        self._ensure()

    def _ensure(self):
        if self._pipeline is None:
            import openvino_genai as ov_genai

            # Scoped by device: a blob compiled for one plugin handed to
            # another fails deep inside it rather than being recompiled.
            cache = self.cache_dir / f"{self.model_dir.name}-{self.device.lower()}"
            cache.mkdir(parents=True, exist_ok=True)
            logger.info(f"[Brain] loading {self.model_dir.name} on {self.device}")
            self._pipeline = ov_genai.LLMPipeline(
                str(self.model_dir), self.device, CACHE_DIR=str(cache)
            )
            logger.info("[Brain] model ready")
            # Count loading as use. Without this a freshly warmed model reads
            # as idle since the epoch and is dropped on the next tick, which
            # undoes the warm the daemon just paid for.
            self._last_used = time.monotonic()
        return self._pipeline

    @staticmethod
    def _strip_thinking(text: str) -> str:
        """Drop Qwen3's reasoning block, keeping the answer that follows it.

        The markers are literal `<think>` / `</think>` tags — see the model's
        own `chat_template.jinja`, which splits on exactly those. An unterminated
        block means the answer was cut off mid-reasoning; there is nothing left
        to say, so return nothing rather than speaking the reasoning aloud.
        """
        if "</think>" in text:
            return text.split("</think>")[-1].strip()
        if "<think>" in text:
            return ""
        return text.strip()

    def _build_chat(self, language: str | None = None) -> list[dict]:
        messages = [{"role": "system", "content": SYSTEM_PROMPT + language_clause(language)}]
        messages.extend(self._history)
        return messages

    def answer(self, question: str, language: str | None = None) -> str:
        """Ask the model, keeping the turn inside its own chat session."""
        import openvino_genai as ov_genai

        pipeline = self._ensure()

        chat = ov_genai.ChatHistory()
        for msg in self._build_chat(language):
            chat.append(msg)
        chat.append({"role": "user", "content": question})
        # Qwen3 reasons before answering unless the template is told not to.
        # The reasoning is never spoken, but it is generated: turning it off
        # took a tool-calling turn from 28.6 s to 7.9 s on the same model.
        chat.set_extra_context({"enable_thinking": False})

        config = ov_genai.GenerationConfig()
        config.max_new_tokens = self.max_new_tokens
        config.do_sample = False
        config.repetition_penalty = 1.15

        reply = pipeline.generate(chat, config)
        text = reply.texts[0] if hasattr(reply, "texts") else str(reply)
        text = self._strip_thinking(text)

        self._history.append({"role": "user", "content": question})
        self._history.append({"role": "assistant", "content": text})

        if len(self._history) >= self.history_turns * 2:
            self.reset()

        self._last_used = time.monotonic()
        return text

    def reset(self) -> None:
        """Drop the conversation, keeping the loaded model."""
        self._history.clear()

    def unload(self) -> None:
        """Free the model from accelerator memory entirely."""
        if self._pipeline is None:
            return
        logger.info(f"[Brain] unloading {self.model_dir.name} from {self.device}")
        self._pipeline = None
        self._history.clear()
        gc.collect()

    def unload_if_idle(self, idle_seconds: float) -> bool:
        """Free the model once a conversation has clearly ended.

        Measured on qwen3-4b/NPU: holding it costs ~975 MB Rss, dropping it
        costs 3.8 s to rebuild from a warm cache. Neither is worth paying on
        every turn, so it is kept across a conversation and dropped after it.
        """
        if self._pipeline is None or idle_seconds <= 0:
            return False
        if time.monotonic() - self._last_used < idle_seconds:
            return False
        self.unload()
        return True


class ServerBrain:
    """Calls the Servitor server's MCP agent instead of a local model.

    The server runs the full LangGraph ReAct agent with MCP tools (weather,
    Nextcloud, Home Assistant, Wake-on-LAN, etc). The ear sends transcribed
    text in, gets an answer with real tool calls back.
    """

    def __init__(self, server_url: str = "http://localhost:8000", timeout: float = 120.0):
        self.server_url = server_url.rstrip("/")
        self.timeout = timeout

    def warm(self) -> None:
        pass

    def answer(self, question: str, language: str | None = None) -> str:
        import httpx

        logger.info(f"[ServerBrain] asking server: {question[:80]!r}")
        try:
            resp = httpx.post(
                f"{self.server_url}/receive_message",
                json={"message": question},
                timeout=self.timeout,
            )
            resp.raise_for_status()
            result = resp.json().get("response", "")
            # The server prepends "Message received " to the bare agent reply.
            result = result.removeprefix("Message received ")
            logger.info(f"[ServerBrain] reply: {result[:120]!r}")
            return result
        except Exception:
            logger.exception("[ServerBrain] server call failed")
            return "I could not reach the server."

    def reset(self) -> None:
        pass

    def unload(self) -> None:
        pass

    def unload_if_idle(self, idle_seconds: float) -> bool:
        return False
