import asyncio
from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.graph import StateGraph, MessagesState, START
from langgraph.prebuilt import ToolNode, tools_condition
from langchain_ollama import ChatOllama

model = ChatOllama(
    base_url="http://192.168.0.11:11434",
    model="mistral:7b-instruct-v0.3-q8_0",
    validate_model_on_init=True,
    temperature=0.1,
    num_predict=256,
)


async def run_graph():
    client = MultiServerMCPClient(
        {
            "math": {
                "url": "http://localhost:8001/mcp",
                "transport": "streamable_http",
            }
        }
    )

    tools = await client.get_tools()

    def call_model(state: MessagesState):
        response = model.bind_tools(tools).invoke(state["messages"])
        return {"messages": response}

    builder = StateGraph(MessagesState)
    builder.add_node(call_model)
    builder.add_node(ToolNode(tools))
    builder.add_edge(START, "call_model")
    builder.add_conditional_edges(
        "call_model",
        tools_condition,
    )
    builder.add_edge("tools", "call_model")

    graph = builder.compile()
    math_response = await graph.ainvoke({"messages": "what's (3 + 5) x 12?"})


if __name__ == "__main__":
    asyncio.run(run_graph())
