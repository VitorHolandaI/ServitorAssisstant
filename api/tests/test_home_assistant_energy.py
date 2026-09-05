import json
import ssl
import unittest
from unittest.mock import patch

from api.mcp_module.stremable_http.home_assistant_energy import (
    HomeAssistantEnergyClient,
    HomeAssistantEnergyError,
    _configured_statistics,
    _time_range,
    _websocket_url,
)

PREFERENCES = {
    "energy_sources": [
        {
            "type": "grid",
            "name": "Main grid",
            "stat_energy_from": "sensor.grid_energy",
            "stat_rate": "sensor.grid_power",
            "stat_cost": "sensor.grid_cost",
        }
    ],
    "device_consumption": [
        {"name": "Office", "stat_consumption": "sensor.office_energy"}
    ],
}


class FakeWebSocket:
    def __init__(self, responses):
        self.responses = [json.dumps(response) for response in responses]
        self.sent = []

    async def recv(self):
        if not self.responses:
            raise AssertionError("Unexpected WebSocket receive")
        return self.responses.pop(0)

    async def send(self, message):
        self.sent.append(json.loads(message))


class FakeConnection:
    def __init__(self, websocket):
        self.websocket = websocket
        self.calls = []

    def __call__(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self

    async def __aenter__(self):
        return self.websocket

    async def __aexit__(self, _exc_type, _exc, _traceback):
        return False


class HomeAssistantEnergyHelpersTest(unittest.TestCase):
    def test_derives_websocket_url_from_official_mcp_url(self):
        self.assertEqual(
            _websocket_url("https://smart.home/api/mcp/assist"),
            "wss://smart.home/api/websocket",
        )

    def test_rejects_insecure_url(self):
        with self.assertRaisesRegex(HomeAssistantEnergyError, "must use HTTPS"):
            _websocket_url("http://smart.home/api/mcp/assist")

    def test_time_range_requires_timezone_and_bounds_five_minute_queries(self):
        with self.assertRaisesRegex(HomeAssistantEnergyError, "timezone offset"):
            _time_range("2026-09-01T00:00:00", "2026-09-02T00:00:00Z", "hour")
        with self.assertRaisesRegex(HomeAssistantEnergyError, "at most 7 days"):
            _time_range("2026-08-01T00:00:00Z", "2026-09-01T00:00:00Z", "5minute")

    def test_extracts_only_configured_energy_statistics(self):
        history = _configured_statistics(PREFERENCES, include_live=False)
        live = _configured_statistics(PREFERENCES, include_live=True)

        self.assertEqual(
            {item["statistic_id"] for item in history},
            {"sensor.grid_energy", "sensor.grid_cost", "sensor.office_energy"},
        )
        self.assertIn("sensor.grid_power", {item["statistic_id"] for item in live})


class HomeAssistantEnergyClientTest(unittest.IsolatedAsyncioTestCase):
    def make_client(self, responses):
        websocket = FakeWebSocket(
            [{"type": "auth_required"}, {"type": "auth_ok"}, *responses]
        )
        connection = FakeConnection(websocket)
        client = HomeAssistantEnergyClient(
            "wss://smart.home/api/websocket",
            "secret-token",
            ssl.create_default_context(),
        )
        return client, websocket, connection

    async def test_current_state_only_reads_preferences_and_states(self):
        client, websocket, connection = self.make_client(
            [
                {"id": 1, "type": "result", "success": True, "result": PREFERENCES},
                {
                    "id": 2,
                    "type": "result",
                    "success": True,
                    "result": [
                        {
                            "entity_id": "sensor.grid_power",
                            "state": "420",
                            "attributes": {
                                "friendly_name": "Grid power",
                                "unit_of_measurement": "W",
                            },
                            "last_updated": "2026-09-03T12:00:00Z",
                        }
                    ],
                },
            ]
        )

        with patch(
            "api.mcp_module.stremable_http.home_assistant_energy.connect", connection
        ):
            result = json.loads(await client.current_state())

        self.assertTrue(result["read_only"])
        grid_power = next(
            item
            for item in result["sources"]
            if item["statistic_id"] == "sensor.grid_power"
        )
        self.assertEqual(grid_power["state"], "420")
        self.assertEqual(
            [message["type"] for message in websocket.sent],
            ["auth", "energy/get_prefs", "get_states"],
        )
        self.assertNotIn("secret-token", repr(client))

    async def test_summary_uses_only_read_only_recorder_commands(self):
        statistic_ids = [
            "sensor.grid_energy",
            "sensor.grid_cost",
            "sensor.office_energy",
        ]
        client, websocket, connection = self.make_client(
            [
                {"id": 1, "type": "result", "success": True, "result": PREFERENCES},
                {
                    "id": 2,
                    "type": "result",
                    "success": True,
                    "result": [
                        {
                            "statistic_id": statistic_id,
                            "statistics_unit_of_measurement": "kWh",
                        }
                        for statistic_id in statistic_ids
                    ],
                },
                {
                    "id": 3,
                    "type": "result",
                    "success": True,
                    "result": {
                        "sensor.grid_energy": [
                            {"start": 1, "end": 2, "change": 1.5, "sum": 10.0}
                        ],
                        "sensor.grid_cost": [],
                        "sensor.office_energy": [],
                    },
                },
            ]
        )

        with patch(
            "api.mcp_module.stremable_http.home_assistant_energy.connect", connection
        ):
            result = json.loads(
                await client.summary(
                    "2026-09-01T00:00:00-03:00",
                    "2026-09-02T00:00:00-03:00",
                    "hour",
                )
            )

        self.assertEqual(result["statistics"][0]["total_change"], 1.5)
        command_types = [message["type"] for message in websocket.sent]
        self.assertEqual(
            command_types,
            [
                "auth",
                "energy/get_prefs",
                "recorder/get_statistics_metadata",
                "recorder/statistics_during_period",
            ],
        )
        self.assertNotIn("call_service", command_types)
        self.assertNotIn("energy/save_prefs", command_types)

    async def test_history_rejects_id_outside_energy_preferences(self):
        client, websocket, connection = self.make_client(
            [{"id": 1, "type": "result", "success": True, "result": PREFERENCES}]
        )

        with (
            patch(
                "api.mcp_module.stremable_http.home_assistant_energy.connect",
                connection,
            ),
            self.assertRaisesRegex(HomeAssistantEnergyError, "not an allowed"),
        ):
            await client.history("sensor.not_allowed")

        self.assertEqual(
            [message["type"] for message in websocket.sent],
            ["auth", "energy/get_prefs"],
        )

    async def test_missing_dashboard_falls_back_to_current_energy_sensors(self):
        client, websocket, connection = self.make_client(
            [
                {
                    "id": 1,
                    "type": "result",
                    "success": False,
                    "error": {"code": "not_found", "message": "No prefs"},
                },
                {
                    "id": 2,
                    "type": "result",
                    "success": True,
                    "result": [
                        {
                            "entity_id": "sensor.grid_power",
                            "state": "420",
                            "attributes": {
                                "device_class": "power",
                                "unit_of_measurement": "W",
                            },
                        },
                        {
                            "entity_id": "sensor.temperature",
                            "state": "25",
                            "attributes": {"device_class": "temperature"},
                        },
                    ],
                },
            ]
        )

        with patch(
            "api.mcp_module.stremable_http.home_assistant_energy.connect", connection
        ):
            result = json.loads(await client.current_state())

        self.assertFalse(result["energy_dashboard_configured"])
        self.assertEqual(
            [item["statistic_id"] for item in result["sources"]],
            ["sensor.grid_power"],
        )
        self.assertEqual(
            [message["type"] for message in websocket.sent],
            ["auth", "energy/get_prefs", "get_states"],
        )

    async def test_write_command_is_rejected_before_send(self):
        client, websocket, _ = self.make_client([])

        # The private call is the enforcement boundary this test must exercise.
        with self.assertRaisesRegex(HomeAssistantEnergyError, "read-only allowlist"):
            await client._call(  # pylint: disable=protected-access
                websocket, 1, {"type": "energy/save_prefs"}
            )

        self.assertEqual(websocket.sent, [])
