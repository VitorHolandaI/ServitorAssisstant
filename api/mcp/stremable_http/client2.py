import asyncio
import asyncio
from langchain_ollama import ChatOllama   # Updated import
from langchain import tools
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from langchain_mcp_adapters.tools import load_mcp_tools
#from langgraph.prebuilt import create_react_agent
from mcp.client.streamable_http import streamablehttp_client

from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain.agents import create_agent
from langchain_ollama import ChatOllama


async def main():
    async with streamablehttp_client("http://localhost:8000/mcp") as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await load_mcp_tools(session)
            llm = ChatOllama(model="llama3.1:8b",
                             base_url="http://192.168.0.11:11434")

            #llm.bind_tools(tools) #WONT WORK WITH THIS
            agent = create_agent(llm, tools)

            math_response = await agent.ainvoke({"messages": "what's the weather in new york?"})
            print(math_response)
if __name__ == "__main__":
    asyncio.run(main())
