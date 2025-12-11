# math_server.py
from mcp.server.fastmcp import FastMCP
from typing import List
...

# weather_server.py

mcp = FastMCP("Weather")


@mcp.tool()
async def default_response(description="CALL THIS TOOL WHEN NO TOOL EXACLT MATCHES WHAT THE USER WANTS") -> str:
    return "thres not tool to call, so you must now only repond the user questio"



@mcp.tool("add_numbers", description="Add two numbers and return the result.")
async def add_numbers(a: float, b: float):
    """Return the sum of two numbers."""
    return a + b


@mcp.tool("subtract_numbers", description="Subtract the second number from the first.")
async def subtract_numbers(a: float, b: float):
    """Return the result of subtracting b from a."""
    return a - b


@mcp.tool("multiply_numbers", description="Multiply two numbers together.")
async def multiply_numbers(a: float, b: float):
    """Return the product of two numbers."""
    return a * b


@mcp.tool("divide_numbers", description="Divide the first number by the second.")
async def divide_numbers(a: float, b: float):
    """Return the result of dividing a by b. Returns an error for division by zero."""
    if b == 0:
        return "Error: division by zero."
    return a / b

@mcp.tool(description="IF THE USER ASKS ABOUT THE WEATHER ONLY !!!! Get the weather ONLY if the user explicitly asks ABOUT THE WEATHER.")
async def get_any_weather(location: str) -> str:
    """Get weather for location. from ANY LOCATION USE IT AWLAYS WHEN IN NEED OF WEATHER INFO"""
    return "It's always sunny in New York"



if __name__ == "__main__":
    mcp.run(transport="streamable-http")
