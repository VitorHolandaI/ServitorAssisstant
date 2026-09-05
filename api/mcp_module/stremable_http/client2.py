import asyncio
import contextlib
import logging
import os
import re
from dataclasses import dataclass, field

import httpx
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_mcp_adapters.tools import load_mcp_tools
from langgraph.prebuilt import create_react_agent
from mcp import ClientSession, types
from mcp.client.streamable_http import streamablehttp_client
from mcp_module.profiles import DEFAULT_PROFILE, select_tools

DEBUG = os.getenv("DEBUG", "false").lower() == "true"
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class McpEndpoint:
    """One MCP server plus the credentials and trust that belong to it alone.

    Servers are not interchangeable: the Home Assistant endpoint needs a bearer
    token and a private CA, and neither may leak to the local servers on :8001
    and :8002. Keeping them per endpoint is what makes that impossible.
    """

    url: str
    headers: dict[str, str] = field(default_factory=dict)
    ca_bundle: str | None = None

    def __repr__(self) -> str:
        # Endpoints end up in log lines; the default repr would print the token.
        return f"McpEndpoint({self.url})"

    def __str__(self) -> str:
        return self.url


def _open_endpoint(endpoint):
    """Open a streamable-HTTP connection, honouring per-endpoint auth and CA."""
    if isinstance(endpoint, str):
        return streamablehttp_client(endpoint)

    if endpoint.ca_bundle is None:
        return streamablehttp_client(endpoint.url, headers=endpoint.headers or None)

    def factory(headers=None, timeout=None, auth=None) -> httpx.AsyncClient:
        # Mirrors mcp.shared._httpx_utils.create_mcp_http_client, but pins trust
        # to the private CA instead of the system store. Never verify=False.
        return httpx.AsyncClient(
            headers=headers,
            timeout=timeout or httpx.Timeout(30.0, read=300.0),
            auth=auth,
            follow_redirects=True,
            verify=endpoint.ca_bundle,
        )

    return streamablehttp_client(
        endpoint.url,
        headers=endpoint.headers or None,
        httpx_client_factory=factory,
    )


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


class llm_mcp_client:
    def __init__(self, mcp_addresses: list, model_name: str, model_address: str, system_prompt: str,
                 profile: str | None = None, ask_user=None):
        self.mcp_addresses = mcp_addresses
        # Which tools this agent may see. The server takes them all; the
        # laptop takes the spoken subset, which is smaller context for a
        # smaller model and a narrower blast radius for a misheard sentence.
        self.profile = profile or os.getenv("MCP_PROFILE", DEFAULT_PROFILE)
        # Answers MCP elicitation: a tool pausing mid-call to ask the user
        # something. Without one, a tool that asks is told nobody is there,
        # which is the truth for a server with no user attached to it.
        self.ask_user = ask_user
        self.model_name = model_name
        self.model_address = model_address
        self.prompt = system_prompt
        self.context_window = int(os.getenv("OLLAMA_NUM_CTX", "32768"))
        self.context_reserved_tokens = 5000
        # Local OpenVINO model — no Ollama, no network. The device is only a
        # request: ov_chat re-checks it against the display and the driver's
        # allocation ceiling before anything is compiled.
        from mcp_module.stremable_http.ov_chat import OpenVINOChat
        self._llm = OpenVINOChat(
            model_path=model_address,
            device=os.getenv("OV_AGENT_DEVICE", "GPU").strip().upper(),
            # A ReAct turn has to fit a <tool_call> block and then the answer.
            # At 128 a call with a few arguments is truncated mid-JSON and is
            # dropped by the parser, so the agent looks like it ignored its tools.
            max_tokens=int(os.getenv("OV_AGENT_MAX_TOKENS", "512")),
            temperature=0.0,
            do_sample=False,
        )
        self._usage_turn_open = False
        self._stack: contextlib.AsyncExitStack | None = None
        self._agent = None
        self._tools_by_name = {}
        # Exact prompt/response token counts reported by Ollama on the last LLM
        # call of the last turn. Authoritative — it is the model's own count.
        self.last_usage: dict | None = None
        logger.info(f"[client2] init model={model_name} profile={self.profile} mcp={mcp_addresses}")

    def _record_usage(self, message) -> None:
        """Store token usage from an AIMessage, if the provider reported any."""
        usage = getattr(message, "usage_metadata", None) or {}
        meta = getattr(message, "response_metadata", None) or {}
        input_tokens = usage.get("input_tokens") or meta.get("prompt_eval_count")
        output_tokens = usage.get("output_tokens") or meta.get("eval_count")
        if not input_tokens:
            return
        previous = (self.last_usage or {}).get("input_tokens", 0)
        if input_tokens < previous and self._usage_turn_open:
            # A react turn makes several calls; the largest prompt is the one
            # that actually sized the context window.
            return
        self.last_usage = {
            "input_tokens": int(input_tokens),
            "output_tokens": int(output_tokens or 0),
            "context_window": self.context_window,
            "model": self.model_name,
        }

    async def _ensure_agent(self):
        if self._agent is not None:
            return
        # Keep the MCP session stack alive across LLM reloads.
        if self._stack is None:
            self._stack = contextlib.AsyncExitStack()
            clients = [await self._stack.enter_async_context(_open_endpoint(addr)) for addr in self.mcp_addresses]
            sessions = [
                await self._stack.enter_async_context(
                    ClientSession(read, write, elicitation_callback=self._elicitation_callback())
                )
                for read, write, _ in clients
            ]
            all_tools = []
            for session in sessions:
                await session.initialize()
                tools = await load_mcp_tools(session)
                all_tools.extend(tools)
            logger.debug(f"[client2] tools loaded: {[t.name for t in all_tools]}")
            self._tools_by_name = {tool.name: tool for tool in all_tools}

        all_tools = select_tools(list(self._tools_by_name.values()), self.profile)
        self._agent = create_react_agent(self._llm, all_tools)

    def _elicitation_callback(self):
        """Route a tool's question to the user, if anyone can be asked."""
        if self.ask_user is None:
            return None

        async def answer(context, params):
            question = getattr(params, "message", "") or ""
            logger.info(f"[client2] tool is asking: {question[:120]!r}")
            # ask_user blocks on a microphone; keep the event loop free.
            said = await asyncio.to_thread(self.ask_user, question)
            if not said:
                logger.info("[client2] nobody answered")
                return types.ElicitResult(action="decline")
            schema = getattr(params, "requestedSchema", None) or {}
            fields = list((schema.get("properties") or {}).keys())
            # Spoken answers are one string; fill whatever single field the
            # tool asked for rather than guessing at a shape.
            content = {fields[0]: said} if len(fields) == 1 else {"answer": said}
            logger.info(f"[client2] answered with {said[:80]!r}")
            return types.ElicitResult(action="accept", content=content)

        return answer

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
        self._llm.unload()

    def unload(self) -> None:
        """Free the LLM from accelerator memory. Keeps MCP connections alive."""
        self._llm.unload()
        # Recreate the agent on next call so it picks up the fresh LLM.
        # The MCP session stack stays alive — only the LangGraph graph is dropped.
        self._agent = None

    async def get_response(self, message, history=None, system_prompt=None):
        logger.info(f"[client2] get_response: {message[:80]!r}")
        try:
            await self._ensure_agent()
            direct_result = await self._try_direct_tool(message)
            if direct_result is not None:
                return {"messages": [AIMessage(content=direct_result)]}
            prompt = system_prompt or self.prompt
            msgs = _build_messages(message, history, prompt)
            # A tool may pause to ask the user something; that wait is legitimate.
            response = await asyncio.wait_for(
                self._agent.ainvoke({"messages": msgs}),
                timeout=float(os.getenv("MCP_AGENT_TIMEOUT", "600")),
            )

            tool_calls_used = []
            self.last_usage = None
            self._usage_turn_open = True
            for msg in response["messages"]:
                if isinstance(msg, AIMessage):
                    self._record_usage(msg)
                    for call in getattr(msg, "tool_calls", None) or []:
                        tool_calls_used.append(call.get("name"))
            self._usage_turn_open = False
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
            self.last_usage = None
            self._usage_turn_open = True
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
                    output = event["data"].get("output")
                    if output is not None:
                        self._record_usage(output)

                elif event_type == "on_tool_start":
                    logger.info(f"[client2] tool call: {event.get('name')}")

                elif event_type == "on_tool_end":
                    logger.info(f"[client2] tool done: {event.get('name')}")
                    in_tool_call = False

        except Exception as error:
            logger.error(f"[client2] stream error: {error}", exc_info=DEBUG)
            await self._recreate_agent()
            raise
        finally:
            self._usage_turn_open = False
            if self.last_usage:
                logger.info(f"[client2] usage: {self.last_usage}")
