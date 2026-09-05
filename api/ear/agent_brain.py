"""The spoken assistant, with the local MCP tools in its hands.

`LocalBrain` answers from the model alone: it can say what the weather
usually does in September but not what it is doing now, and it cannot write
anything anywhere. This one runs the same model as a ReAct agent over the
MCP servers on this machine, so "what is on my calendar" reaches Nextcloud
and "write to desktop ..." reaches the keyboard.

It shares `llm_mcp_client` with the server rather than reimplementing the
MCP wiring, so tool loading, the profile filter and the OpenVINO chat model
all behave the same in both places.

The ear is threaded and the agent is asyncio, so this owns one event loop on
a daemon thread for the life of the process. The MCP sessions live on that
loop and are opened once: they are long-lived HTTP connections to servers on
loopback, and reopening them per turn would add a round trip to every
sentence.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import os
import threading
import time
from pathlib import Path

from ear.brain import language_clause

logger = logging.getLogger(__name__)

# Spoken, not typed. The tool rules matter more than the prose ones here: a
# small model narrating its plan instead of calling the tool is the failure
# this prompt exists to prevent.
SYSTEM_PROMPT = (
    "You are the Servitor, a terse voice assistant running locally on Vitor's laptop. "
    "Your reply is going to be spoken aloud, so answer in one or two short sentences. "
    "The exception is a tool that hands you something already written to be read "
    "out - a numbered list of choices, for instance. Repeat that back word for "
    "word, every item of it, and do not summarise it or shorten it: the user is "
    "choosing from it and cannot choose from what you left out. "
    "Never use markdown, bullet points, code blocks or emoji. "
    "Use a tool whenever one applies, and never say that you will check, fetch or "
    "look something up: by the time you answer the tool has already run, so state "
    "what it returned. "
    "If you do not know something, say so plainly instead of inventing it."
)

# A spoken turn that has not answered in this long has failed, whatever the
# agent thinks it is still doing. Generous, because a tool may legitimately
# stop mid-call to ask the user something and then wait for them to speak -
# at 90s that wait was being counted as a hang and killed the turn after the
# answer had already been given. The rounds a browse can take are bounded on
# the server, so this is a backstop, not the thing that ends a conversation.
TURN_TIMEOUT = float(os.getenv("EAR_TURN_TIMEOUT", "600"))

# Exchanges kept while a conversation is open. It is dropped the moment the
# conversation ends, so nothing said to the Servitor outlives the wake that
# started it - and the model is not carrying yesterday into today.
HISTORY_TURNS = int(os.getenv("EAR_HISTORY_TURNS", "6"))


class AgentBrain:
    """Answers a spoken command with the local MCP tools available."""

    def __init__(
        self,
        model_dir: Path,
        device: str,
        mcp_addresses: list[str],
        profile: str | None = None,
        max_tokens: int = 512,
        free_after_turn: bool = True,
    ):
        self.model_dir = Path(model_dir)
        self.device = device
        self.mcp_addresses = list(mcp_addresses)
        self.profile = profile
        self.max_tokens = max_tokens
        # An agent turn carries the tool schemas and every tool result, so its
        # KV cache dwarfs a plain chat turn's. Left resident on the shared
        # iGPU that also draws the desktop, three turns reached
        # "CL_OUT_OF_RESOURCES" and took an i915 GPU HANG with them. Freeing
        # between turns costs a rebuild and keeps the session alive.
        self.free_after_turn = free_after_turn
        # Answers an MCP elicitation: a tool asking the user something in the
        # middle of its own execution. Set by the assistant that owns Whisper.
        self.ask_user = None
        # (role, content, when) as _build_messages expects. Lives for one
        # conversation: "play video two" is meaningless without the list that
        # came before it, and every turn arrived with no history at all.
        self._history: list[tuple[str, str, str]] = []
        self._client = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._last_used = 0.0

    # ------------------------------------------------------------- plumbing

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        if self._loop is not None:
            return self._loop
        loop = asyncio.new_event_loop()
        thread = threading.Thread(target=loop.run_forever, name="ear-agent", daemon=True)
        thread.start()
        self._loop, self._thread = loop, thread
        return loop

    def _submit(self, coro, timeout: float):
        return asyncio.run_coroutine_threadsafe(coro, self._ensure_loop()).result(timeout)

    def _ensure_client(self):
        if self._client is not None:
            return self._client
        from mcp_module.stremable_http.client2 import llm_mcp_client

        logger.info(f"[Agent] {len(self.mcp_addresses)} MCP endpoint(s), profile={self.profile}")
        self._client = llm_mcp_client(
            mcp_addresses=self.mcp_addresses,
            model_name=self.model_dir.name,
            model_address=str(self.model_dir),
            system_prompt=SYSTEM_PROMPT,
            profile=self.profile,
            ask_user=self.ask_user,
        )
        self._client._llm.device = self.device
        self._client._llm.max_tokens = self.max_tokens
        return self._client

    # ---------------------------------------------------------------- brain

    def warm(self) -> None:
        """Open the MCP sessions and compile the model before the first wake."""
        client = self._ensure_client()
        try:
            # Sessions are opened per turn now, so warming means compiling the
            # model and proving the servers answer - not holding a connection.
            self._submit(client.probe(), 180.0)
            self._last_used = time.monotonic()
            logger.info("[Agent] tools reachable and model ready")
        except Exception:
            # The always-on half must survive a dead MCP server; the next turn
            # tries again rather than the daemon refusing to start.
            logger.exception("[Agent] warm failed; will retry on the first command")

    def answer(self, question: str, language: str | None = None) -> str:
        client = self._ensure_client()
        prompt = SYSTEM_PROMPT + language_clause(language)
        history = list(self._history)
        try:
            state = self._submit(
                client.get_response(question, history=history, system_prompt=prompt),
                TURN_TIMEOUT,
            )
        except Exception:
            logger.exception("[Agent] turn failed")
            return "Something went wrong reaching my tools."
        finally:
            self._last_used = time.monotonic()
            if self.free_after_turn:
                self.unload()

        if not state:
            return "Something went wrong reaching my tools."
        messages = state.get("messages") or []
        for message in reversed(messages):
            text = getattr(message, "content", "")
            if isinstance(text, str) and text.strip():
                self._remember(question, text.strip())
                return text.strip()
        return ""

    def _remember(self, question: str, answer: str) -> None:
        when = dt.datetime.now().strftime("%H:%M")
        self._history.append(("user", question, when))
        self._history.append(("assistant", answer, when))
        # Oldest first out, in whole exchanges, so a reply never survives the
        # question it answered.
        while len(self._history) > HISTORY_TURNS * 2:
            del self._history[:2]

    def reset(self) -> None:
        """Forget the conversation. Called when the wake session ends."""
        if self._history:
            logger.info(f"[Agent] forgetting {len(self._history) // 2} exchange(s)")
        self._history.clear()

    def unload(self) -> None:
        """Free the model, keeping the MCP sessions open."""
        if self._client is None:
            return
        logger.info(f"[Agent] unloading {self.model_dir.name} from {self.device}")
        self._client.unload()

    def unload_if_idle(self, idle_seconds: float) -> bool:
        if self._client is None or idle_seconds <= 0:
            return False
        if self._client._llm._pipeline is None:
            return False
        if time.monotonic() - self._last_used < idle_seconds:
            return False
        self.unload()
        return True
