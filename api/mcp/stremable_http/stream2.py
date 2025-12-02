# math_server.py
...

# weather_server.py
from typing import List
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("Weather")

@mcp.tool()
async def get_any_weather(location: str) -> str:
    """Get weather for location. from ANY LOCATION USE IT AWLAYS WHEN IN NEED OF WEATHER INFO"""
    return "It's always sunny in New York"

if __name__ == "__main__":
    mcp.run(transport="streamable-http")
