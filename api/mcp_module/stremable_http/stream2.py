from mcp.server.fastmcp import FastMCP
from typing import List
from typing import Any
import httpx
...

# weather_server.py

mcp = FastMCP("Weather")


async def make_nws_request(url: str) -> dict[str, Any] | None:
    """Make a request to the NWS API with proper error handling."""

    USER_AGENT = "weather-app/1.0"
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/geo+json"
    }
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(url, headers=headers, timeout=30.0)
            response.raise_for_status()
            return response.json()
        except Exception:
            return None

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


@mcp.tool()
async def get_forecast(latitude: float, longitude: float) -> str:
    """Get weather forecast for a location.

    Args:
        latitude: Latitude of the location
        longitude: Longitude of the location
    """
    latitude = -7.23071810
    longitude = -35.88166640

    NWS_API_BASE = "https://api.weather.gov"
    points_url = f"{NWS_API_BASE}/points/{latitude},{longitude}"
    points_data = await make_nws_request(points_url)

    if not points_data:
        return "Unable to fetch forecast data for this location."

    forecast_url = points_data["properties"]["forecast"]
    forecast_data = await make_nws_request(forecast_url)

    if not forecast_data:
        return "Unable to fetch detailed forecast."

    periods = forecast_data["properties"]["periods"]
    forecasts = []
    for period in periods[:5]:  # Only show next 5 periods
        forecast = f"""
            For Campina Grande Paraiba
            {period['name']}:
            Temperature: {period['temperature']}°{period['temperatureUnit']}
            Wind: {period['windSpeed']} {period['windDirection']}
            Forecast: {period['detailedForecast']}
            """
        forecasts.append(forecast)

    return "\n---\n".join(forecasts)


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
