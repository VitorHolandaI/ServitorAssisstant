import os
import asyncio
import logging
import contextlib
import re
from mcp import ClientSession
from langchain_ollama import ChatOllama
from langchain_core.messages import HumanMessage, AIMessage, ToolMessage, SystemMessage
from langchain_mcp_adapters.tools import load_mcp_tools
from langgraph.prebuilt import create_react_agent
from mcp.client.streamable_http import streamablehttp_client

DEBUG = os.getenv("DEBUG", "false").lower() == "true"
logger = logging.getLogger(__name__)


def _build_messages(
    message: str,
    history: list | None,
    system_prompt: str | None = None,
) -> list:
    msgs = [SystemMessage(content=system_prompt)] if system_prompt else []
    for role, content, created_at in (history or []):
        if role == "user":
            msgs.append(HumanMessage(content=f"[{created_at}] {content}"))
        else:
            msgs.append(AIMessage(content=f"[{created_at}] {content}"))
    msgs.append(HumanMessage(content=message))
    if DEBUG:
        logger.debug(f"[client2] built {len(msgs)} messages ({len(history or [])} history + current prompt/message)")
    return msgs


def _is_explicit_nextcloud_completion(message: str) -> bool:
    normalized = message.casefold()
    completion = re.search(
        r"\b(marca|marque|marcar|complete|completar|conclua|concluir|conclu[ií]da|done)\b",
        normalized,
    )
    if not completion or re.search(r"\b(n[aã]o)\s+(marca|marque|complete|conclua)", normalized):
        return False
    if re.search(r"\b(local|sqlite)\b", normalized):
        return False
    return bool(
        re.search(r"\b(task|tarefa|nextcloud)\b", normalized)
        or re.search(r"\b[0-9a-f]{8}\b", normalized)
    )


class llm_mcp_client():
    def __init__(self, mcp_addresses: list, model_name: str, model_address: str, system_prompt: str):
        self.mcp_addresses = mcp_addresses
        self.model_name = model_name
        self.model_address = model_address
        self.prompt = system_prompt
        self.context_window = int(os.getenv("OLLAMA_NUM_CTX", "32768"))
        self.context_reserved_tokens = 5000
        self._llm = ChatOllama(model=self.model_name, base_url=self.model_address, keep_alive="10m", timeout=120, num_ctx=self.context_window, model_kwargs={"think": False})
        self._stack: contextlib.AsyncExitStack | None = None
        self._agent = None
        self._tools_by_name = {}
        logger.info(f"[client2] init model={model_name} mcp={mcp_addresses}")

    async def _ensure_agent(self):
        if self._agent is not None:
            return
        self._stack = contextlib.AsyncExitStack()
        clients = [await self._stack.enter_async_context(streamablehttp_client(addr)) for addr in self.mcp_addresses]
        sessions = [await self._stack.enter_async_context(ClientSession(read, write)) for read, write, _ in clients]

        all_tools = []
        for session in sessions:
            await session.initialize()
            tools = await load_mcp_tools(session)
            all_tools.extend(tools)

        logger.debug(f"[client2] tools loaded: {[t.name for t in all_tools]}")
        self._tools_by_name = {tool.name: tool for tool in all_tools}
        self._agent = create_react_agent(self._llm, all_tools)

    async def _try_direct_tool(self, message: str) -> str | None:
        if not _is_explicit_nextcloud_completion(message):
            return None
        tool = self._tools_by_name.get("complete_nextcloud_task")
        if tool is None:
            return None
        logger.info("[client2] forced tool call: complete_nextcloud_task")
        result = await tool.ainvoke({"task": message})
        if isinstance(result, str):
            return result
        if isinstance(result, list):
            text_blocks = [
                item.get("text", "")
                for item in result
                if isinstance(item, dict) and item.get("type") == "text"
            ]
            if text_blocks:
                return "\n".join(text_blocks)
        if isinstance(result, dict) and "result" in result:
            return str(result["result"])
        return str(result)

    async def _recreate_agent(self):
        await self.cleanup()
        self._agent = None
        await self._ensure_agent()

    async def cleanup(self):
        if self._stack:
            await self._stack.aclose()
            self._stack = None
        self._agent = None
        self._tools_by_name = {}

    async def get_response(self, message, history=None, system_prompt=None):
        logger.info(f"[client2] get_response: {message[:80]!r}")
        try:
            await self._ensure_agent()
            direct_result = await self._try_direct_tool(message)
            if direct_result is not None:
                return {"messages": [AIMessage(content=direct_result)]}
            prompt = system_prompt or self.prompt
            msgs = _build_messages(message, history, prompt)
            response = await asyncio.wait_for(self._agent.ainvoke({"messages": msgs}), timeout=120)

            tool_calls_used = []
            for msg in response["messages"]:
                if isinstance(msg, AIMessage) and getattr(msg, "tool_calls", None):
                    for call in msg.tool_calls:
                        tool_calls_used.append(call.get("name"))
            logger.info(f"[client2] tool calls: {tool_calls_used}")
            return response
        except Exception as error:
            logger.error(f"[client2] get_response error: {error}", exc_info=DEBUG)
            await self._recreate_agent()
            return None

    async def get_response_stream(self, message, history=None, system_prompt=None):
        logger.info(f"[client2] get_response_stream: {message[:80]!r}")
        try:
            await self._ensure_agent()
            direct_result = await self._try_direct_tool(message)
            if direct_result is not None:
                yield direct_result
                return
            prompt = system_prompt or self.prompt
            msgs = _build_messages(message, history, prompt)
            in_tool_call = False
            async for event in self._agent.astream_events({"messages": msgs}, version="v2"):
                event_type = event["event"]

                if event_type == "on_chat_model_stream":
                    chunk = event["data"].get("chunk")
                    if not chunk or not hasattr(chunk, "content") or not chunk.content:
                        continue
                    if getattr(chunk, "tool_calls", None) or getattr(chunk, "tool_call_chunks", None):
                        in_tool_call = True
                        continue
                    if in_tool_call:
                        continue
                    logger.debug(f"[client2] yielding {len(chunk.content)} chars")
                    yield chunk.content

                elif event_type == "on_chat_model_end":
                    in_tool_call = False

                elif event_type == "on_tool_start":
                    logger.info(f"[client2] tool call: {event.get('name')}")

                elif event_type == "on_tool_end":
                    logger.info(f"[client2] tool done: {event.get('name')}")
                    in_tool_call = False

        except Exception as error:
            logger.error(f"[client2] stream error: {error}", exc_info=DEBUG)
            await self._recreate_agent()
            raise
