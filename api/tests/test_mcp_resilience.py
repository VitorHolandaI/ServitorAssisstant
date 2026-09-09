"""A missing MCP server is a tool the agent does not have, not a dead turn.

Three servers answered, the fourth port had nothing on it, and the whole
conversation died with a five-deep ExceptionGroup. The work the first three
had already done was thrown away with it.
"""

import asyncio
import contextlib
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402

from mcp_module.stremable_http import client2  # noqa: E402


class _Tool:
    def __init__(self, name):
        self.name = name


@contextlib.asynccontextmanager
async def _live_endpoint(_endpoint):
    yield ("read", "write", None)


@contextlib.asynccontextmanager
async def _dead_endpoint(_endpoint):
    raise httpx.ConnectError("All connection attempts failed")
    yield  # pragma: no cover


class _FakeSession:
    def __init__(self, *_args, **_kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def initialize(self):
        return None


class _Client(client2.llm_mcp_client):
    """Skip __init__: it builds a real chat model."""

    def __init__(self, addresses):
        self.mcp_addresses = addresses
        self.profile = "full"
        self.ask_user = None
        self._tools_by_name = {}
        self._llm = object()


class DeadEndpointTest(unittest.TestCase):
    def _run(self, addresses, opener):
        client = _Client(addresses)
        tools = {"http://a/mcp": [_Tool("alpha")], "http://b/mcp": [_Tool("beta")]}

        async def load(session):
            return tools.get(getattr(session, "addr", ""), [_Tool("alpha")])

        async def go():
            async with client._session() as agent:
                return agent

        with patch.object(client2, "_open_endpoint", opener), \
             patch.object(client2, "ClientSession", _FakeSession), \
             patch.object(client2, "load_mcp_tools", load), \
             patch.object(client2, "create_react_agent", lambda llm, chosen: chosen):
            return asyncio.run(go()), client

    def test_a_dead_endpoint_does_not_end_the_turn(self):
        def opener(endpoint):
            return _dead_endpoint(endpoint) if endpoint.endswith("8004/mcp") \
                else _live_endpoint(endpoint)

        agent, client = self._run(
            ["http://127.0.0.1:8001/mcp", "http://127.0.0.1:8004/mcp"], opener
        )

        # The surviving server's tools are still there.
        self.assertEqual([tool.name for tool in agent], ["alpha"])
        self.assertIn("alpha", client._tools_by_name)

    def test_every_endpoint_dead_is_reported_plainly(self):
        addresses = ["http://127.0.0.1:8001/mcp", "http://127.0.0.1:8004/mcp"]

        with self.assertRaisesRegex(RuntimeError, "no MCP server answered"):
            self._run(addresses, _dead_endpoint)


class EndpointUrlTest(unittest.TestCase):
    def test_plain_string(self):
        self.assertEqual(client2._endpoint_url("http://x/mcp"), "http://x/mcp")

    def test_endpoint_object(self):
        endpoint = client2.McpEndpoint(url="http://y/mcp", headers=None, ca_bundle=None)
        self.assertEqual(client2._endpoint_url(endpoint), "http://y/mcp")


if __name__ == "__main__":
    unittest.main()
