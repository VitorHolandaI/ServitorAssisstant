from mcp import ClientSession
from langchain_ollama import ChatOllama
from langchain.agents import create_agent
from langchain.messages import ToolMessage, AIMessage
from langchain_mcp_adapters.tools import load_mcp_tools
from mcp.client.streamable_http import streamablehttp_client


class llm_mcp_client():
    def __init__(self, mcp_adress: str, model_name: str, model_address: str, system_prompt: str):
        self.mcp_adress = mcp_adress
        self.model_name = model_name
        self.model_address = model_address
        self.prompt = system_prompt

    async def get_response(self, message):
        async with streamablehttp_client(self.mcp_adress) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await load_mcp_tools(session)
                llm = ChatOllama(model=self.model_name,
                                 base_url=self.model_address)
                print("agent Creationm")
                agent = create_agent(llm, tools, system_prompt=self.prompt)
                try:
                    response = await agent.ainvoke(
                        {"messages": message})

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
                    print(
                        f"Model was not able to be called with message the error was {error}")
                    return None
