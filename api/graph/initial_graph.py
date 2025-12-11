from graph import graph_server


class InitialGraph:
    def __init__(self, initial_msg: str):
        self.initial_msg = initial_msg
    def graph_call(self) -> str:
        graph_server.ainvoke({"messages": "what's (3 + 5) x 12?"})
        # post processing?
        return "final response "
