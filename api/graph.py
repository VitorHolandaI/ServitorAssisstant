
import asyncio
from mcp_module.stremable_http.client2 import llm_mcp_client




async def main():

    agentBuilder = llm_mcp_client(mcp_adress="http://localhost:8000/mcp",
                                  model_name="llama3.1:8b", model_address="http://172.22.165.144:11434")

    response = await agentBuilder.get_response("Whats the weather in new york today?")

    print(response['messages'][-1].content)


if __name__ == '__main__':
    asyncio.run(main())
