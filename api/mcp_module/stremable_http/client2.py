import asyncio
from mcp import ClientSession
from langchain_ollama import ChatOllama
from langchain.agents import create_agent
from langchain_mcp_adapters.tools import load_mcp_tools
from mcp.client.streamable_http import streamablehttp_client


class llm_mcp_client():
    def __init__(self,mcp_adress:str,model_name:str,model_address:str):
        self.mcp_adress = mcp_adress
        self.model_name = model_name
        self.model_address = model_address

    async def get_agent(self):
        async with streamablehttp_client(self.mcp_adress) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await load_mcp_tools(session)
                llm = ChatOllama(model=self.model_name,
                                 base_url=self.model_address)
                print("agent Creationm")
                agent = create_agent(llm, tools)
                return agent

