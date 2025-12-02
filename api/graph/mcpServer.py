from fastapi import FastAPI
from pydantic import BaseModel
import uvicorn

app = FastAPI()

# ---- MCP Models ----

class McpRequest(BaseModel):
    jsonrpc: str
    method: str
    params: dict | None = None
    id: int | str | None = None

# ---- MCP Handlers ----

@app.post("/mcp")
async def mcp_endpoint(req: McpRequest):
    """
    Minimal MCP server for math, serving over HTTP.
    
    The initialize method response has been updated to include
    protocolVersion and serverInfo to satisfy client validation.
    """

    if req.method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req.id,
            "result": {
                # --- FIX: Added required fields for MCP InitializeResult validation ---
                "protocolVersion": "0.1.0",
                "serverInfo": {
                    "name": "MathToolServer",
                    "version": "1.0.0"
                },
                # --------------------------------------------------------------------
                "capabilities": {
                    "tools": {
                        "math": {
                            "description": "Does addition, subtraction, multiplication, division",
                            "input_schema": {
                                "type": "object",
                                "properties": {
                                    "op": {"type": "string"},
                                    "a": {"type": "number"},
                                    "b": {"type": "number"},
                                },
                                "required": ["op", "a", "b"],
                            }
                        }
                    }
                }
            }
        }

    if req.method == "call_tool":
        name = req.params["name"]
        args = req.params["arguments"]

        if name == "math":
            op = args["op"]
            a = args["a"]
            b = args["b"]

            # Perform the calculation
            result = None
            if op == "add":
                result = a + b
            elif op == "sub":
                result = a - b
            elif op == "mul":
                result = a * b
            elif op == "div":
                try:
                    result = a / b
                except ZeroDivisionError:
                    result = "Error: Division by zero"
            else:
                result = f"Error: Unknown operation {op}"

            return {
                "jsonrpc": "2.0",
                "id": req.id,
                "result": {
                    "content": [{
                        "type": "output_text",
                        "text": str(result)
                    }]
                }
            }

    # Unknown method fallback
    return {
        "jsonrpc": "2.0",
        "id": req.id,
        "error": {
            "code": -32601,
            "message": f"Unknown method {req.method}"
        }
    }


if __name__ == "__main__":
    # Ensure uvicorn runs the current app instance
    uvicorn.run(app, host="0.0.0.0", port=8001)
