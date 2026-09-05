"""LangChain BaseChatModel wrapping openvino_genai.LLMPipeline.

Drop-in replacement for ChatOllama in client2.py. Runs the Qwen3 model
locally on NPU/GPU via OpenVINO, no network, no Ollama.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from ear.devices import guard_device
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    ChatMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_core.tools import BaseTool
from langchain_core.utils.function_calling import convert_to_openai_tool

logger = logging.getLogger(__name__)


def _block_text(content) -> str:
    """Flatten LangChain block content into the plain string the template needs.

    Qwen3's `chat_template.jinja` opens with

        {%- if message.content is string %} ... {%- else %} set content = '' {%- endif %}

    so anything that is not a string renders as an EMPTY message. MCP tool
    results arrive as `[{"type": "text", "text": ...}]`, which meant every
    `<tool_response>` reached the model blank: it answered with placeholders
    like "[Task 1: Title, Description]" because it had genuinely been shown
    nothing, and invented plausible numbers when pressed for them.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type", "text") == "text":
                parts.append(str(block.get("text", "")))
        return "\n".join(part for part in parts if part)
    return "" if content is None else str(content)


def _to_chatml(messages: list[BaseMessage]) -> list[dict]:
    """Convert LangChain messages to the dicts OpenVINO's ChatHistory expects.

    The assistant's `tool_calls` must survive this trip. Qwen3's own
    `chat_template.jinja` renders them into `<tool_call>` blocks off
    `message.tool_calls`; drop them and the model sees a `<tool_response>`
    answering a question it never asked, and simply asks again - the ReAct
    loop then spins until the recursion limit.
    """
    result = []
    for m in messages:
        text = _block_text(m.content)
        if isinstance(m, SystemMessage):
            result.append({"role": "system", "content": text})
        elif isinstance(m, HumanMessage):
            result.append({"role": "user", "content": text})
        elif isinstance(m, AIMessage):
            entry = {"role": "assistant", "content": text}
            if m.tool_calls:
                entry["tool_calls"] = [
                    {"function": {"name": c["name"], "arguments": c["args"]}}
                    for c in m.tool_calls
                ]
            result.append(entry)
        elif isinstance(m, ToolMessage):
            result.append({"role": "tool", "content": text})
        elif isinstance(m, ChatMessage):
            result.append({"role": m.role, "content": text})
    return result


def _strip_thinking(text: str) -> str:
    """Drop Qwen3's reasoning block, keeping the answer that follows it.

    The markers are `<think>` / `</think>` — see the model's own
    `chat_template.jinja`, which splits on exactly those strings.
    """
    if "</think>" in text:
        return text.split("</think>")[-1].strip()
    if "<think>" in text:
        return ""
    return text.strip()


def _parse_tool_calls(text: str) -> list[dict]:
    """Parse <tool_call>...</tool_call> JSON blocks from model output."""
    calls = []
    for match in re.finditer(r"<tool_call>(.*?)</tool_call>", text, re.DOTALL):
        try:
            parsed = json.loads(match.group(1).strip())
            calls.append(parsed)
        except json.JSONDecodeError:
            logger.warning(f"Failed to parse tool call: {match.group(1)[:80]}")
    return calls


def _tool_schemas(tools: list[BaseTool] | None) -> list[dict]:
    """OpenAI-shaped schemas, which is what Qwen3's template renders."""
    if not tools:
        return []
    return [convert_to_openai_tool(t) for t in tools]


class OpenVINOChat(BaseChatModel):
    """LangChain chat model using openvino_genai.LLMPipeline.

    Wraps a local OpenVINO IR model (e.g. Qwen3-8B-int4-cw-ov) so it can be
    used with LangGraph agents and LangChain tool calling.
    """

    model_path: str
    device: str = "NPU"
    max_tokens: int = 256
    temperature: float = 0.0
    top_p: float = 0.95
    do_sample: bool = False
    repetition_penalty: float = 1.0
    # Without a cache the compiled blob is rebuilt on every process start:
    # measured at 43.7 s and ~3.5 GB peak for qwen3-4b, against 3.8 s warm.
    cache_dir: str = "~/.cache/servitor/ov-cache"
    # NPU fixes the prompt ceiling at compile time and sizes its buffers to
    # it. Measured: qwen3-4b at MAX_PROMPT_LEN=16384 was OOM-killed at a 6 GB
    # cap after 19.7 s, where the same model on the driver default peaked at
    # 1.37 GB. Keep this small, and 0 to leave the driver's own default alone.
    max_prompt_len: int = 2048
    # Qwen3 reasons before answering unless the template is told not to. The
    # reasoning is never spoken and never shown, but it is generated, and it
    # is most of a turn's tokens.
    enable_thinking: bool = False
    _pipeline: Any = None  # openvino_genai.LLMPipeline
    _device_in_use: str = ""
    _bound_tools: Any = None

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    def _load_pipeline(self):
        if self._pipeline is not None:
            return
        import openvino_genai as ov_genai

        path = Path(self.model_path)
        if not path.is_dir():
            raise FileNotFoundError(f"OpenVINO model not found: {path}")

        # The same guard the ear uses. This GPU also drives the desktop, and a
        # model that overran it has hung the session before.
        device = guard_device(self.device, "the agent model", model_dir=path)
        # Scoped by device and prompt length. A shared cache handed an NPU blob
        # built at a different MAX_PROMPT_LEN back to the plugin, which failed
        # with "Cannot find tensor for port ... f16[1,32,1024,128]".
        scope = f"{path.name}-{device.lower()}"
        if device == "NPU" and self.max_prompt_len > 0:
            scope += f"-p{self.max_prompt_len}"
        cache = Path(self.cache_dir).expanduser() / scope
        cache.mkdir(parents=True, exist_ok=True)

        logger.info(f"[OVChat] loading {path.name} on {device}")
        kwargs = {"CACHE_DIR": str(cache)}
        if device == "NPU" and self.max_prompt_len > 0:
            kwargs["MAX_PROMPT_LEN"] = self.max_prompt_len
        self._pipeline = ov_genai.LLMPipeline(str(path), device, **kwargs)
        self._device_in_use = device
        logger.info("[OVChat] model ready")

    def _default_params(self) -> dict:
        return {
            "max_new_tokens": self.max_tokens,
            "do_sample": self.do_sample,
            "temperature": self.temperature,
            "top_p": self.top_p,
            "repetition_penalty": self.repetition_penalty,
        }

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs,
    ) -> ChatResult:
        import openvino_genai as ov_genai

        self._load_pipeline()

        chatml = _to_chatml(messages)
        tools = kwargs.pop("tools", None) or self._bound_tools

        config = ov_genai.GenerationConfig()
        config.max_new_tokens = self.max_tokens
        config.do_sample = self.do_sample
        config.temperature = self.temperature
        config.top_p = self.top_p
        config.repetition_penalty = self.repetition_penalty

        try:
            history = ov_genai.ChatHistory()
            for msg in chatml:
                history.append(msg)
            # Declared through the model's own chat template rather than
            # pasted into the system prompt: the template already knows how to
            # render <tools> and asks for <tool_call> back in a fixed shape.
            schemas = _tool_schemas(tools)
            if schemas:
                history.set_tools(schemas)
            if not self.enable_thinking:
                history.set_extra_context({"enable_thinking": False})
            reply = self._pipeline.generate(history, config)
        except Exception:
            logger.exception("[OVChat] generate failed; dropping the pipeline")
            # NPU state can go stale after a failure. Drop it and let the next
            # call rebuild, rather than paying the rebuild on this one.
            self._pipeline = None
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content=""))])

        raw = reply.texts[0] if hasattr(reply, "texts") else str(reply)
        raw = raw.strip()

        # Check for tool calls
        tool_calls = _parse_tool_calls(raw)
        if tool_calls:
            content = re.sub(r"<tool_call>.*?</tool_call>", "", raw, flags=re.DOTALL)
            content = _strip_thinking(content)
            api_calls = []
            for index, tc in enumerate(tool_calls):
                api_calls.append({
                    "name": tc.get("name", ""),
                    "args": tc.get("arguments", {}),
                    "id": f"call_{index}_{tc.get('name', 'unknown')}",
                })
            message = AIMessage(content=content or "", tool_calls=api_calls)
        else:
            message = AIMessage(content=_strip_thinking(raw))

        return ChatResult(generations=[ChatGeneration(message=message)])

    def _stream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs,
    ) -> Iterator[ChatGenerationChunk]:
        result = self._generate(messages, stop, run_manager, **kwargs)
        for gen in result.generations:
            yield ChatGenerationChunk(message=AIMessageChunk(content=gen.message.content))

    @property
    def _llm_type(self) -> str:
        return "openvino-chat"

    def bind_tools(self, tools: list[BaseTool], **kwargs) -> BaseChatModel:
        self._bound_tools = list(tools)
        return self

    def unload(self) -> None:
        """Free the model from accelerator memory."""
        self._pipeline = None
        import gc
        gc.collect()
        return self
