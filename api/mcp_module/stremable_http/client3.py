import asyncio
from mcp import ClientSession
from langchain_ollama import ChatOllama
from langchain.agents import create_agent
from langchain_mcp_adapters.tools import load_mcp_tools
from mcp.client.streamable_http import streamablehttp_client

class llm_mcp_client:
    def __init__(self, mcp_adress: str, model_name: str, model_address: str):
        self.mcp_adress = mcp_adress
        self.model_name = model_name
        self.model_address = model_address
        self.agent = None  # To hold the LangChain Agent
        self._http_client = None # To hold the streamablehttp_client
        self._session = None     # To hold the ClientSession

    #ver docs sobre aenter ee aexit
    # Entry method for 'async with'
    async def __aenter__(self):
        # 1. Open the HTTP client connection
        self._http_client = streamablehttp_client(self.mcp_adress)
        read, write, _ = await self._http_client.__aenter__()

        # 2. Open the MCP session
        self._session = ClientSession(read, write)
        await self._session.__aenter__()

        # 3. Create and store the agent
        await self._session.initialize()
        tools = await load_mcp_tools(self._session)
        llm = ChatOllama(model=self.model_name, base_url=self.model_address)
        self.agent = create_agent(llm, tools)
        
        # Return the object itself so we can access self.agent
        return self

    # Exit method for 'async with' (guarantees cleanup)
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._session:
            await self._session.__aexit__(exc_type, exc_val, exc_tb)
        if self._http_client:
            await self._http_client.__aexit__(exc_type, exc_val, exc_tb)

    # A method to invoke the agent and get the message
    async def ainvoke_message(self, prompt: str):
        if not self.agent:
            raise RuntimeError("Client not entered via 'async with' block.")
            
        # Call the agent
        response = await self.agent.ainvoke({"messages": prompt})
        
        # Return the specific part you want (e.g., the final answer content)
        # Note: Depending on your specific LangChain/LangGraph setup,
        # you might need to adjust how you extract the final message.
        return response
