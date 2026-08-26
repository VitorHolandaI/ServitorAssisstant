"""Real token accounting for the context meter.

Ollama 0.32.1 has no /api/tokenize and this deployment runs without
--embeddings, so the only exact tokenizer available is the model itself:
/api/chat with num_predict=1 returns prompt_eval_count, the number of tokens
the model actually consumed after applying its own chat template. That is what
the context window is measured in, so it is what the UI shows.

Results are cached by payload hash — an unchanged conversation costs nothing,
and appending one message only re-counts once.
"""

import hashlib
import json
import logging
import os
from collections import OrderedDict

import requests

logger = logging.getLogger(__name__)

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434")
COUNT_TIMEOUT = float(os.getenv("TOKEN_COUNT_TIMEOUT", "30"))
CACHE_SIZE = 64

# Fallback only — used when Ollama is unreachable. Portuguese averages close to
# 4 characters per token on the Gemma tokenizer.
FALLBACK_CHARS_PER_TOKEN = 4

_cache: "OrderedDict[str, int]" = OrderedDict()


def _cache_get(key: str) -> int | None:
    if key in _cache:
        _cache.move_to_end(key)
        return _cache[key]
    return None


def _cache_put(key: str, value: int):
    _cache[key] = value
    _cache.move_to_end(key)
    while len(_cache) > CACHE_SIZE:
        _cache.popitem(last=False)


def _fallback(messages: list[dict], tools: list | None) -> int:
    chars = sum(len(m.get("content", "")) for m in messages)
    if tools:
        chars += len(json.dumps(tools))
    return chars // FALLBACK_CHARS_PER_TOKEN


def count_chat_tokens(model: str, messages: list[dict], tools: list | None = None) -> tuple[int, bool]:
    """Exact prompt token count for `messages`.

    Returns (tokens, exact). `exact` is False when the count came from the
    character fallback because Ollama could not be reached.
    """
    payload: dict = {
        "model": model,
        "messages": messages,
        "stream": False,
        "options": {"num_predict": 1},
        "keep_alive": "10m",
    }
    if tools:
        payload["tools"] = tools

    key = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()
    cached = _cache_get(key)
    if cached is not None:
        return cached, True

    try:
        resp = requests.post(
            f"{OLLAMA_HOST.rstrip('/')}/api/chat", json=payload, timeout=COUNT_TIMEOUT
        )
        resp.raise_for_status()
        count = resp.json().get("prompt_eval_count")
        if not isinstance(count, int):
            raise ValueError(f"no prompt_eval_count in response: {resp.text[:200]}")
    except Exception as e:
        logger.warning(f"[tokens] exact count failed ({e}) — using char fallback")
        return _fallback(messages, tools), False

    _cache_put(key, count)
    return count, True


def tool_schemas(agent) -> list[dict]:
    """OpenAI-style tool schemas for the tools bound to the LangGraph agent."""
    tools_by_name = getattr(agent, "_tools_by_name", {}) or {}
    schemas = []
    for tool in tools_by_name.values():
        try:
            args = tool.args_schema
            if hasattr(args, "model_json_schema"):
                parameters = args.model_json_schema()
            elif isinstance(args, dict):
                parameters = args
            else:
                parameters = {"type": "object", "properties": {}}
        except Exception:
            parameters = {"type": "object", "properties": {}}
        schemas.append({
            "type": "function",
            "function": {
                "name": tool.name,
                "description": getattr(tool, "description", "") or "",
                "parameters": parameters,
            },
        })
    return schemas
