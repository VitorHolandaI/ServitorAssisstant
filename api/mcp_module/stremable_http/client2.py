import asyncio
import contextlib
from mcp import ClientSession
from langchain_ollama import ChatOllama
from langchain.agents import create_agent
from langchain.messages import ToolMessage, AIMessage
from langchain_core.messages import HumanMessage, AIMessage as CoreAIMessage
from langchain_mcp_adapters.tools import load_mcp_tools
from mcp.client.streamable_http import streamablehttp_client


def _build_messages(message: str, history: list | None) -> list:
    """Build LangChain message list from history + current message."""
    msgs = []
    for role, content, created_at in (history or []):
        if role == "user":
            msgs.append(HumanMessage(content=f"[{created_at}] {content}"))
        else:
            msgs.append(CoreAIMessage(content=f"[{created_at}] {content}"))
    msgs.append(HumanMessage(content=message))
    return msgs


class llm_mcp_client():
    def __init__(self, mcp_addresses: list, model_name: str, model_address: str, system_prompt: str):
        self.mcp_addresses = mcp_addresses
        self.model_name = model_name
        self.model_address = model_address
        self.prompt = system_prompt

    async def get_response(self, message, history=None, system_prompt=None):
        all_tools = []
        async with contextlib.AsyncExitStack() as stack:
            clients = [await stack.enter_async_context(streamablehttp_client(addr)) for addr in self.mcp_addresses]
            sessions = [await stack.enter_async_context(ClientSession(read, write)) for read, write, _ in clients]

            for session in sessions:
                await session.initialize()
                tools = await load_mcp_tools(session)
                all_tools.extend(tools)

            llm = ChatOllama(model=self.model_name, base_url=self.model_address, keep_alive=0)
            prompt = system_prompt or self.prompt
            agent = create_agent(llm, all_tools, system_prompt=prompt)
            try:
                msgs = _build_messages(message, history)
                response = await agent.ainvoke({"messages": msgs})

                tool_calls_used = []
                for msg in response["messages"]:
                    if isinstance(msg, AIMessage) and getattr(msg, "tool_calls", None):
                        for call in msg.tool_calls:
                            tool_calls_used.append({
                                "tool": call.get("name"),
                                "arguments": call.get("args"),
                                "id": call.get("id"),
                                "type": call.get("type"),
                            })
                print("TOOL CALLS:", tool_calls_used)
                return response
            except Exception as error:
                print(f"Model was not able to be called with message the error was {error}")
                return None

    async def get_response_stream(self, message, history=None, system_prompt=None):
        all_tools = []
        async with contextlib.AsyncExitStack() as stack:
            clients = [await stack.enter_async_context(streamablehttp_client(addr)) for addr in self.mcp_addresses]
            sessions = [await stack.enter_async_context(ClientSession(read, write)) for read, write, _ in clients]

            for session in sessions:
                await session.initialize()
                tools = await load_mcp_tools(session)
                all_tools.extend(tools)

            llm = ChatOllama(model=self.model_name, base_url=self.model_address, keep_alive=0)
            prompt = system_prompt or self.prompt
            agent = create_agent(llm, all_tools, system_prompt=prompt)
            try:
                msgs = _build_messages(message, history)
                text_buffer = ""
                async for event in agent.astream_events({"messages": msgs}, version="v2"):
                    event_type = event["event"]

                    if event_type == "on_chat_model_stream":
                        chunk = event["data"].get("chunk")
                        if chunk and hasattr(chunk, "content") and chunk.content:
                            if getattr(chunk, "tool_calls", None) or getattr(chunk, "tool_call_chunks", None):
                                continue
                            text_buffer += chunk.content

                    elif event_type == "on_chat_model_end":
                        stripped = text_buffer.strip()
                        is_junk = (
                            stripped.startswith("{") and
                            ("function" in stripped or "tool" in stripped or "parameters" in stripped)
                        )
                        if not is_junk and stripped:
                            yield text_buffer
                        text_buffer = ""
            except Exception as error:
                print(f"Streaming error: {error}")
                yield f"[ERROR] {error}"
