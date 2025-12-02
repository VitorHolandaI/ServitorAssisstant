from langchain_mcp_adapters.client import MultiServerMCPClient
from prompt import system_prompt
from langgraph.graph import StateGraph, MessagesState, START
from langgraph.prebuilt import ToolNode, tools_condition

from langchain.chat_models import init_chat_model
# model = init_chat_model("openai:gpt-4.1")


def call_model(state: MessagesState):
    response = model.bind_tools(tools).invoke(state["messages"])


# can call tools or not hehehe
def process_msg(state: MessagesState):
    """
    This function right now talks to the local ollama.

    :param talk: a string with query of the user.
    """
    response = model.invoke(state["messages"])
    response = response.content.strip()
    messages = state.messages + \
        [{"role": "assistant", "content": response}]
    responseString = re.sub(r'<think>.*?</think>\n*', '',
                            response, flags=re.DOTALL)
    messages = state.messages + \
        [{"role": "assistant", "content": responseString}]

    return messages


initial_state = MessagesState()

builder = StateGraph(MessagesState)
builder.add_node(call_model)
#builder.add_node(ToolNode(tools))
builder.add_edge(START, "process_msg")
builder.add_conditional_edges(
    "call_model",
    tools_condition,
)

builder.add_edge("tools", "call_model")
graph = builder.compile()
math_response = await graph.ainvoke({"messages": "what's (3 + 5) x 12?"})
weather_response = await graph.ainvoke({"messages": "what is the weather in nyc?"})
