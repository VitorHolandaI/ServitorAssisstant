"""Read the MCP tool surface straight out of the server sources.

The benchmark has to show a candidate model the same tools the agent will
show it in production. Copying the schemas by hand means the benchmark
silently measures a surface that no longer exists, so they are parsed from
the `@mcp.tool()` functions instead.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

JSON_TYPES: dict[str, str] = {
    "str": "string",
    "int": "integer",
    "float": "number",
    "bool": "boolean",
    "list": "array",
    "dict": "object",
}


def _annotation_to_json_type(annotation: ast.expr | None) -> str:
    """Map a Python annotation to a JSON Schema type, defaulting to string."""
    if annotation is None:
        return "string"
    if isinstance(annotation, ast.Name):
        return JSON_TYPES.get(annotation.id, "string")
    if isinstance(annotation, ast.BinOp) and isinstance(annotation.op, ast.BitOr):
        # `str | None` — the optional half carries no type information.
        for side in (annotation.left, annotation.right):
            if not (isinstance(side, ast.Constant) and side.value is None):
                return _annotation_to_json_type(side)
    if isinstance(annotation, ast.Subscript):
        return _annotation_to_json_type(annotation.value)
    return "string"


def registered_name(node: ast.AsyncFunctionDef | ast.FunctionDef) -> str | None:
    """The name MCP registers, or None when the function is not a tool.

    `@mcp.tool("create_local_task")` renames the tool, so the function name
    is not what the model is shown. Reading the wrong one makes the
    benchmark offer tools production does not have.
    """
    for decorator in node.decorator_list:
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        if not (isinstance(target, ast.Attribute) and target.attr == "tool"):
            continue
        if isinstance(decorator, ast.Call):
            for argument in decorator.args:
                if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                    return argument.value
            for keyword in decorator.keywords:
                if keyword.arg == "name" and isinstance(keyword.value, ast.Constant):
                    return str(keyword.value.value)
        return node.name
    return None


def _parameters(node: ast.AsyncFunctionDef | ast.FunctionDef) -> dict[str, Any]:
    """Build the JSON Schema for one tool's arguments."""
    args = node.args.args
    defaults = node.args.defaults
    first_with_default = len(args) - len(defaults)
    properties: dict[str, dict[str, str]] = {}
    required: list[str] = []
    for index, arg in enumerate(args):
        properties[arg.arg] = {"type": _annotation_to_json_type(arg.annotation)}
        if index < first_with_default:
            required.append(arg.arg)
    return {"type": "object", "properties": properties, "required": required}


def extract_tools(source: Path) -> list[dict[str, Any]]:
    """Return Ollama-shaped tool definitions for every @mcp.tool() in a file.

    >>> tools = extract_tools(Path("api/mcp_module/stremable_http/stream2.py"))
    >>> tools[0]["function"]["name"]
    'default_response'
    """
    if not source.is_file():
        raise FileNotFoundError(f"MCP server source not found: {source}")
    tree = ast.parse(source.read_text(encoding="utf-8"))
    tools: list[dict[str, Any]] = []
    for node in tree.body:
        if not isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef):
            continue
        name = registered_name(node)
        if name is None:
            continue
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": (ast.get_docstring(node) or node.name).strip(),
                    "parameters": _parameters(node),
                },
            }
        )
    if not tools:
        raise ValueError(f"no @mcp.tool() functions found in {source}")
    return tools


def extract_server_prompt(source: Path) -> str:
    """The system prompt the server actually sends, read out of Server.py.

    Measuring with an invented prompt measures an assistant nobody runs: the
    production one carries a persona, a CRITICAL tool-use block and
    hand-written routing hints, and all three change what the model does.
    """
    if not source.is_file():
        raise FileNotFoundError(f"server source not found: {source}")
    tree = ast.parse(source.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if (isinstance(target, ast.Attribute) and target.attr == "base_prompt"
                    and isinstance(node.value, ast.Constant)):
                return str(node.value.value)
    raise ValueError(f"no `self.base_prompt = \"...\"` literal in {source}")
