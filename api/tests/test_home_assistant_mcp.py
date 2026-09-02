import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp_module.stremable_http import client2  # noqa: E402
from mcp_module.stremable_http.client2 import McpEndpoint  # noqa: E402
from server.Server import _home_assistant_endpoint  # noqa: E402

HA_ENV = {
    "HOME_ASSISTANT_MCP_URL": "",
    "HOME_ASSISTANT_TOKEN_FILE": "",
    "HOME_ASSISTANT_CA_BUNDLE": "",
}


class McpEndpointTests(unittest.TestCase):
    def test_repr_never_prints_the_token(self):
        endpoint = McpEndpoint(
            url="https://smart.home/api/mcp/assist",
            headers={"Authorization": "Bearer super-secret"},
        )
        self.assertNotIn("super-secret", repr(endpoint))
        self.assertNotIn("super-secret", f"{[endpoint]}")
        self.assertIn("smart.home", repr(endpoint))

    def test_a_plain_string_endpoint_sends_no_credentials(self):
        with patch.object(client2, "streamablehttp_client") as connect:
            client2._open_endpoint("http://127.0.0.1:8001/mcp")
        connect.assert_called_once_with("http://127.0.0.1:8001/mcp")

    def test_headers_go_only_to_the_endpoint_that_owns_them(self):
        endpoint = McpEndpoint(url="https://smart.home/api/mcp/assist",
                               headers={"Authorization": "Bearer t"})
        with patch.object(client2, "streamablehttp_client") as connect:
            client2._open_endpoint(endpoint)
        connect.assert_called_once_with(
            "https://smart.home/api/mcp/assist",
            headers={"Authorization": "Bearer t"},
        )

    def test_a_ca_bundle_pins_verification_to_that_file(self):
        endpoint = McpEndpoint(url="https://smart.home/api/mcp/assist",
                               headers={"Authorization": "Bearer t"},
                               ca_bundle="/etc/ssl/lab/root-ca.crt")
        with patch.object(client2, "streamablehttp_client") as connect:
            client2._open_endpoint(endpoint)
        factory = connect.call_args.kwargs["httpx_client_factory"]

        with patch.object(client2.httpx, "AsyncClient") as async_client:
            factory(headers={"Authorization": "Bearer t"})
        kwargs = async_client.call_args.kwargs
        self.assertEqual(kwargs["verify"], "/etc/ssl/lab/root-ca.crt")
        self.assertTrue(kwargs["follow_redirects"])


class HomeAssistantEndpointTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.token_file = Path(self.tmp.name) / "token"
        self.token_file.write_text("llat-abc\n")
        self.token_file.chmod(0o600)
        self.ca_file = Path(self.tmp.name) / "root-ca.crt"
        self.ca_file.write_text("-----BEGIN CERTIFICATE-----\n")

    def env(self, **overrides):
        values = dict(HA_ENV)
        values.update(overrides)
        return patch.dict(os.environ, values, clear=False)

    def test_disabled_when_no_url_is_configured(self):
        with self.env():
            self.assertIsNone(_home_assistant_endpoint())

    def test_disabled_when_the_token_file_is_not_configured(self):
        with self.env(HOME_ASSISTANT_MCP_URL="https://smart.home/api/mcp/assist"):
            self.assertIsNone(_home_assistant_endpoint())

    def test_disabled_when_the_token_file_is_missing(self):
        with self.env(HOME_ASSISTANT_MCP_URL="https://smart.home/api/mcp/assist",
                      HOME_ASSISTANT_TOKEN_FILE=f"{self.tmp.name}/nope"):
            self.assertIsNone(_home_assistant_endpoint())

    def test_disabled_when_the_token_file_is_empty(self):
        self.token_file.write_text("   \n")
        with self.env(HOME_ASSISTANT_MCP_URL="https://smart.home/api/mcp/assist",
                      HOME_ASSISTANT_TOKEN_FILE=str(self.token_file)):
            self.assertIsNone(_home_assistant_endpoint())

    def test_a_missing_ca_bundle_disables_it_instead_of_falling_back(self):
        # Falling back to the system store here would silently fail to verify a
        # certificate the system does not know, so it must fail closed.
        with self.env(HOME_ASSISTANT_MCP_URL="https://smart.home/api/mcp/assist",
                      HOME_ASSISTANT_TOKEN_FILE=str(self.token_file),
                      HOME_ASSISTANT_CA_BUNDLE=f"{self.tmp.name}/absent.crt"):
            self.assertIsNone(_home_assistant_endpoint())

    def test_builds_a_bearer_endpoint_pinned_to_the_private_ca(self):
        with self.env(HOME_ASSISTANT_MCP_URL="https://smart.home/api/mcp/assist",
                      HOME_ASSISTANT_TOKEN_FILE=str(self.token_file),
                      HOME_ASSISTANT_CA_BUNDLE=str(self.ca_file)):
            endpoint = _home_assistant_endpoint()
        self.assertEqual(endpoint.url, "https://smart.home/api/mcp/assist")
        self.assertEqual(endpoint.headers, {"Authorization": "Bearer llat-abc"})
        self.assertEqual(endpoint.ca_bundle, str(self.ca_file))

    def test_without_a_ca_bundle_it_uses_the_system_trust_store(self):
        with self.env(HOME_ASSISTANT_MCP_URL="https://smart.home/api/mcp/assist",
                      HOME_ASSISTANT_TOKEN_FILE=str(self.token_file)):
            endpoint = _home_assistant_endpoint()
        self.assertIsNone(endpoint.ca_bundle)

    def test_a_world_readable_token_is_warned_about_but_still_used(self):
        self.token_file.chmod(0o644)
        with self.env(HOME_ASSISTANT_MCP_URL="https://smart.home/api/mcp/assist",
                      HOME_ASSISTANT_TOKEN_FILE=str(self.token_file)), \
                self.assertLogs("server.Server", level="WARNING") as logs:
            endpoint = _home_assistant_endpoint()
        self.assertIsNotNone(endpoint)
        self.assertIn("chmod 600", "\n".join(logs.output))


if __name__ == "__main__":
    unittest.main()
