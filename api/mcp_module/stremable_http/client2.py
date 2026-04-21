import os
import asyncio
import logging
import contextlib
from mcp import ClientSession
from langchain_ollama import ChatOllama
from langchain_core.messages import HumanMessage, AIMessage, ToolMessage
from langchain_mcp_adapters.tools import load_mcp_tools
from langgraph.prebuilt import create_react_agent
from mcp.client.streamable_http import streamablehttp_client

DEBUG = os.getenv("DEBUG", "false").lower() == "true"
logger = logging.getLogger(__name__)


def _build_messages(message: str, history: list | None) -> list:
    msgs = []
    for role, content, created_at in (history or []):
        if role == "user":
            msgs.append(HumanMessage(content=f"[{created_at}] {content}"))
        else:
            msgs.append(AIMessage(content=f"[{created_at}] {content}"))
    msgs.append(HumanMessage(content=message))
    if DEBUG:
        logger.debug(f"[client2] built {len(msgs)} messages ({len(history or [])} history + 1 current)")
    return msgs


class llm_mcp_client():
    def __init__(self, mcp_addresses: list, model_name: str, model_address: str, system_prompt: str):
        self.mcp_addresses = mcp_addresses
        self.model_name = model_name
        self.model_address = model_address
        self.prompt = system_prompt
        logger.info(f"[client2] init model={model_name} mcp={mcp_addresses}")

    async def get_response(self, message, history=None, system_prompt=None):
        logger.info(f"[client2] get_response: {message[:80]!r}")
        all_tools = []
        async with contextlib.AsyncExitStack() as stack:
            clients = [await stack.enter_async_context(streamablehttp_client(addr)) for addr in self.mcp_addresses]
            sessions = [await stack.enter_async_context(ClientSession(read, write)) for read, write, _ in clients]

            for session in sessions:
                await session.initialize()
                tools = await load_mcp_tools(session)
                all_tools.extend(tools)

            logger.debug(f"[client2] tools loaded: {[t.name for t in all_tools]}")
            llm = ChatOllama(model=self.model_name, base_url=self.model_address, keep_alive=-1)
            prompt = system_prompt or self.prompt
            agent = create_react_agent(llm, all_tools, prompt=prompt)
            try:
                msgs = _build_messages(message, history)
                response = await agent.ainvoke({"messages": msgs})

                tool_calls_used = []
                for msg in response["messages"]:
                    if isinstance(msg, AIMessage) and getattr(msg, "tool_calls", None):
                        for call in msg.tool_calls:
                            tool_calls_used.append(call.get("name"))
                logger.info(f"[client2] tool calls: {tool_calls_used}")
                return response
            except Exception as error:
                logger.error(f"[client2] get_response error: {error}", exc_info=DEBUG)
                return None

    async def get_response_stream(self, message, history=None, system_prompt=None):
        logger.info(f"[client2] get_response_stream: {message[:80]!r}")
        all_tools = []
        async with contextlib.AsyncExitStack() as stack:
            clients = [await stack.enter_async_context(streamablehttp_client(addr)) for addr in self.mcp_addresses]
            sessions = [await stack.enter_async_context(ClientSession(read, write)) for read, write, _ in clients]

            for session in sessions:
                await session.initialize()
                tools = await load_mcp_tools(session)
                all_tools.extend(tools)

            logger.debug(f"[client2] tools loaded: {[t.name for t in all_tools]}")
            llm = ChatOllama(model=self.model_name, base_url=self.model_address, keep_alive=-1)
            prompt = system_prompt or self.prompt
            agent = create_react_agent(llm, all_tools, prompt=prompt)
            try:
                msgs = _build_messages(message, history)
                in_tool_call = False
                async for event in agent.astream_events({"messages": msgs}, version="v2"):
                    event_type = event["event"]
                    logger.debug(f"[client2] event: {event_type}")

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
                raise
