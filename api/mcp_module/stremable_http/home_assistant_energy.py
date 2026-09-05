"""Read-only Home Assistant Energy and Recorder client."""

import asyncio
import json
import os
import ssl
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from websockets.asyncio.client import connect
from websockets.exceptions import WebSocketException

MAX_TOOL_OUTPUT_CHARS = 4096
MAX_STATISTIC_IDS = 32
_PERIOD_MAX_DAYS = {
    "5minute": 7,
    "hour": 90,
    "day": 3660,
    "week": 3660,
    "month": 3660,
    "year": 3660,
}
_READ_ONLY_COMMANDS = {
    "energy/get_prefs",
    "get_states",
    "recorder/get_statistics_metadata",
    "recorder/list_statistic_ids",
    "recorder/statistics_during_period",
}
_HISTORY_FIELDS = {
    "stat_energy_from": "energy_from",
    "stat_energy_to": "energy_to",
    "stat_cost": "cost",
    "stat_compensation": "compensation",
    "stat_consumption": "consumption",
}
_LIVE_FIELDS = {
    **_HISTORY_FIELDS,
    "stat_rate": "rate",
    "stat_soc": "state_of_charge",
    "entity_energy_price": "energy_price",
    "entity_energy_price_export": "export_price",
}


class HomeAssistantEnergyError(RuntimeError):
    """Raised when a safe Home Assistant energy query cannot be completed."""

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code


def _websocket_url(mcp_url: str) -> str:
    parsed = urlsplit(mcp_url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise HomeAssistantEnergyError("HOME_ASSISTANT_MCP_URL must use HTTPS")
    if parsed.username or parsed.password:
        raise HomeAssistantEnergyError(
            "HOME_ASSISTANT_MCP_URL must not contain credentials"
        )
    return urlunsplit(("wss", parsed.netloc, "/api/websocket", "", ""))


def _parse_time(value: str, name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise HomeAssistantEnergyError(
            f"{name} must be an ISO 8601 timestamp"
        ) from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise HomeAssistantEnergyError(f"{name} must include a timezone offset")
    return parsed


def _time_range(
    start_time: str | None,
    end_time: str | None,
    period: str,
) -> tuple[datetime, datetime]:
    if period not in _PERIOD_MAX_DAYS:
        allowed = ", ".join(_PERIOD_MAX_DAYS)
        raise HomeAssistantEnergyError(f"period must be one of: {allowed}")

    end = _parse_time(end_time, "end_time") if end_time else datetime.now(UTC)
    start = (
        _parse_time(start_time, "start_time") if start_time else end - timedelta(days=1)
    )
    if start >= end:
        raise HomeAssistantEnergyError("start_time must be before end_time")
    if end - start > timedelta(days=_PERIOD_MAX_DAYS[period]):
        raise HomeAssistantEnergyError(
            f"period {period} accepts at most {_PERIOD_MAX_DAYS[period]} days"
        )
    return start, end


def _configured_statistics(
    preferences: dict[str, Any],
    *,
    include_live: bool,
) -> list[dict[str, Any]]:
    fields = _LIVE_FIELDS if include_live else _HISTORY_FIELDS
    configured: dict[str, dict[str, Any]] = {}

    def add(source: dict[str, Any], source_name: str) -> None:
        for field_name, role in fields.items():
            statistic_id = source.get(field_name)
            if not isinstance(statistic_id, str) or not statistic_id:
                continue
            item = configured.setdefault(
                statistic_id,
                {"statistic_id": statistic_id, "uses": []},
            )
            item["uses"].append({"source": source_name, "role": role})

    for index, source in enumerate(preferences.get("energy_sources", []), start=1):
        if not isinstance(source, dict):
            continue
        source_type = str(source.get("type", "energy"))
        source_name = str(source.get("name") or f"{source_type}_{index}")[:100]
        add(source, source_name)

    for key, default_name in (
        ("device_consumption", "device"),
        ("device_consumption_water", "water_device"),
    ):
        for index, source in enumerate(preferences.get(key, []), start=1):
            if not isinstance(source, dict):
                continue
            source_name = str(source.get("name") or f"{default_name}_{index}")[:100]
            add(source, source_name)

    return list(configured.values())


def _recorder_energy_statistics(metadata: Any) -> list[dict[str, Any]]:
    if not isinstance(metadata, list):
        raise HomeAssistantEnergyError(
            "Home Assistant returned invalid Recorder metadata"
        )
    configured = []
    for item in metadata:
        if (
            not isinstance(item, dict)
            or item.get("unit_class") != "energy"
            or not item.get("has_sum")
            or not isinstance(item.get("statistic_id"), str)
        ):
            continue
        statistic_id = item["statistic_id"]
        configured.append(
            {
                "statistic_id": statistic_id,
                "uses": [
                    {
                        "source": str(item.get("name") or statistic_id)[:100],
                        "role": "energy",
                    }
                ],
            }
        )
    return configured


def _current_energy_states(states: Any) -> list[dict[str, Any]]:
    if not isinstance(states, list):
        raise HomeAssistantEnergyError("Home Assistant returned invalid state data")
    sources = []
    for state in states:
        if not isinstance(state, dict):
            continue
        attributes = state.get("attributes", {})
        if not isinstance(attributes, dict) or attributes.get("device_class") not in {
            "energy",
            "power",
        }:
            continue
        entity_id = state.get("entity_id")
        if not isinstance(entity_id, str):
            continue
        sources.append(
            {
                "statistic_id": entity_id,
                "uses": [
                    {
                        "source": str(attributes.get("friendly_name") or entity_id)[
                            :100
                        ],
                        "role": str(attributes.get("device_class")),
                    }
                ],
            }
        )
    return sources


def _metadata_unit(metadata: dict[str, Any]) -> Any:
    return metadata.get("statistics_unit_of_measurement") or metadata.get(
        "unit_of_measurement"
    )


def _summarize_statistics(
    configured: list[dict[str, Any]],
    metadata: Any,
    statistics: dict[str, Any],
) -> list[dict[str, Any]]:
    metadata_by_id = {
        item.get("statistic_id"): item
        for item in metadata or []
        if isinstance(item, dict)
    }
    summaries = []
    for item in configured:
        statistic_id = item["statistic_id"]
        rows = statistics.get(statistic_id, [])
        changes = [row.get("change") for row in rows if row.get("change") is not None]
        last = rows[-1] if rows else {}
        summaries.append(
            {
                **item,
                "unit": _metadata_unit(metadata_by_id.get(statistic_id, {})),
                "periods": len(rows),
                "total_change": sum(changes) if changes else None,
                "latest_sum": last.get("sum"),
                "latest_state": last.get("state"),
            }
        )
    return summaries


def _clean_numbers(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, 6)
    if isinstance(value, list):
        return [_clean_numbers(item) for item in value]
    if isinstance(value, dict):
        return {key: _clean_numbers(item) for key, item in value.items()}
    return value


def _bounded_json(payload: dict[str, Any], list_key: str) -> str:
    payload = _clean_numbers(payload)
    original_count = len(payload.get(list_key, []))
    result = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    while len(result) > MAX_TOOL_OUTPUT_CHARS and len(payload.get(list_key, [])) > 2:
        items = payload[list_key]
        reduced = items[::2]
        if reduced[-1] is not items[-1]:
            reduced.append(items[-1])
        payload[list_key] = reduced
        result = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    omitted = original_count - len(payload.get(list_key, []))
    if omitted:
        payload["omitted_items"] = omitted
        result = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if len(result) > MAX_TOOL_OUTPUT_CHARS:
        raise HomeAssistantEnergyError(
            "Home Assistant response exceeds the safe output limit"
        )
    return result


@dataclass(frozen=True)
class HomeAssistantEnergyClient:
    """Query the fixed read-only Home Assistant energy API surface."""

    websocket_url: str
    token: str = field(repr=False)
    ssl_context: ssl.SSLContext = field(repr=False)
    timeout_seconds: float = 15.0

    @classmethod
    def from_env(cls) -> "HomeAssistantEnergyClient":
        mcp_url = os.getenv("HOME_ASSISTANT_MCP_URL", "").strip()
        if not mcp_url:
            raise HomeAssistantEnergyError("HOME_ASSISTANT_MCP_URL is not configured")

        token_file = os.getenv("HOME_ASSISTANT_TOKEN_FILE", "").strip()
        if not token_file:
            raise HomeAssistantEnergyError(
                "HOME_ASSISTANT_TOKEN_FILE is not configured"
            )
        token_path = Path(token_file).expanduser()
        try:
            token = token_path.read_text(encoding="utf-8").strip()
        except OSError as error:
            raise HomeAssistantEnergyError(
                "cannot read HOME_ASSISTANT_TOKEN_FILE"
            ) from error
        if not token:
            raise HomeAssistantEnergyError("HOME_ASSISTANT_TOKEN_FILE is empty")

        ca_file = os.getenv("HOME_ASSISTANT_CA_BUNDLE", "").strip()
        if ca_file and not Path(ca_file).expanduser().is_file():
            raise HomeAssistantEnergyError("HOME_ASSISTANT_CA_BUNDLE does not exist")
        try:
            ssl_context = ssl.create_default_context(
                cafile=str(Path(ca_file).expanduser()) if ca_file else None
            )
        except OSError as error:
            raise HomeAssistantEnergyError(
                "cannot load HOME_ASSISTANT_CA_BUNDLE"
            ) from error

        return cls(_websocket_url(mcp_url), token, ssl_context)

    async def _authenticate(self, websocket: Any) -> None:
        greeting = json.loads(await websocket.recv())
        if greeting.get("type") != "auth_required":
            raise HomeAssistantEnergyError(
                "unexpected Home Assistant authentication greeting"
            )
        await websocket.send(json.dumps({"type": "auth", "access_token": self.token}))
        auth_result = json.loads(await websocket.recv())
        if auth_result.get("type") != "auth_ok":
            raise HomeAssistantEnergyError("Home Assistant authentication failed")

    async def _call(
        self,
        websocket: Any,
        command_id: int,
        command: dict[str, Any],
    ) -> Any:
        command_type = command.get("type")
        if command_type not in _READ_ONLY_COMMANDS:
            raise HomeAssistantEnergyError("command is not in the read-only allowlist")
        await websocket.send(json.dumps({"id": command_id, **command}))
        response = json.loads(await websocket.recv())
        if response.get("id") != command_id or response.get("type") != "result":
            raise HomeAssistantEnergyError("unexpected Home Assistant response")
        if not response.get("success"):
            error = response.get("error") or {}
            code = str(error.get("code", "unknown_error"))[:100]
            message = str(error.get("message", "query failed"))[:300]
            raise HomeAssistantEnergyError(f"{code}: {message}", code=code)
        return response.get("result")

    async def _energy_preferences(self, websocket: Any, command_id: int) -> Any:
        try:
            return await self._call(websocket, command_id, {"type": "energy/get_prefs"})
        except HomeAssistantEnergyError as error:
            if error.code == "not_found":
                return None
            raise

    async def _query(self, operation: Any) -> Any:
        try:
            async with asyncio.timeout(self.timeout_seconds):
                async with connect(
                    self.websocket_url, ssl=self.ssl_context
                ) as websocket:
                    await self._authenticate(websocket)
                    return await operation(websocket)
        except (OSError, ValueError, TimeoutError, WebSocketException) as error:
            raise HomeAssistantEnergyError(
                f"Home Assistant energy query failed ({type(error).__name__})"
            ) from error

    async def current_state(self) -> str:
        async def operation(websocket: Any) -> str:
            preferences = await self._energy_preferences(websocket, 1)
            states = await self._call(websocket, 2, {"type": "get_states"})
            if preferences is not None and not isinstance(preferences, dict):
                raise HomeAssistantEnergyError(
                    "Home Assistant returned invalid preferences"
                )
            if not isinstance(states, list):
                raise HomeAssistantEnergyError(
                    "Home Assistant returned invalid state data"
                )

            state_by_id = {
                state.get("entity_id"): state
                for state in states
                if isinstance(state, dict) and isinstance(state.get("entity_id"), str)
            }
            sources = []
            configured = (
                _configured_statistics(preferences, include_live=True)
                if preferences is not None
                else _current_energy_states(states)
            )
            for item in configured:
                state = state_by_id.get(item["statistic_id"])
                attributes = state.get("attributes", {}) if state else {}
                sources.append(
                    {
                        **item,
                        "state": state.get("state") if state else None,
                        "unit": attributes.get("unit_of_measurement"),
                        "friendly_name": attributes.get("friendly_name"),
                        "last_updated": state.get("last_updated") if state else None,
                    }
                )
            return _bounded_json(
                {
                    "read_only": True,
                    "energy_dashboard_configured": preferences is not None,
                    "sources": sources,
                },
                "sources",
            )

        return await self._query(operation)

    async def summary(
        self,
        start_time: str | None = None,
        end_time: str | None = None,
        period: str = "hour",
    ) -> str:
        start, end = _time_range(start_time, end_time, period)

        async def operation(websocket: Any) -> str:
            preferences = await self._energy_preferences(websocket, 1)
            if preferences is not None and not isinstance(preferences, dict):
                raise HomeAssistantEnergyError(
                    "Home Assistant returned invalid preferences"
                )
            recorder_metadata = None
            if preferences is None:
                recorder_metadata = await self._call(
                    websocket, 2, {"type": "recorder/list_statistic_ids"}
                )
                configured = _recorder_energy_statistics(recorder_metadata)
            else:
                configured = _configured_statistics(preferences, include_live=False)
            if not configured:
                raise HomeAssistantEnergyError("no energy statistics are available")
            if len(configured) > MAX_STATISTIC_IDS:
                raise HomeAssistantEnergyError(
                    f"Energy dashboard has more than {MAX_STATISTIC_IDS} statistics"
                )
            statistic_ids = [item["statistic_id"] for item in configured]
            metadata = recorder_metadata or await self._call(
                websocket,
                2,
                {
                    "type": "recorder/get_statistics_metadata",
                    "statistic_ids": statistic_ids,
                },
            )
            statistics = await self._call(
                websocket,
                3,
                {
                    "type": "recorder/statistics_during_period",
                    "start_time": start.isoformat(),
                    "end_time": end.isoformat(),
                    "statistic_ids": statistic_ids,
                    "period": period,
                    "units": {"energy": "kWh"},
                    "types": ["change", "sum", "state"],
                },
            )
            if not isinstance(statistics, dict):
                raise HomeAssistantEnergyError(
                    "Home Assistant returned invalid statistics"
                )
            payload = {
                "read_only": True,
                "energy_dashboard_configured": preferences is not None,
                "start_time": start.isoformat(),
                "end_time": end.isoformat(),
                "period": period,
                "statistics": _summarize_statistics(configured, metadata, statistics),
            }
            return _bounded_json(payload, "statistics")

        return await self._query(operation)

    async def history(
        self,
        statistic_id: str,
        start_time: str | None = None,
        end_time: str | None = None,
        period: str = "hour",
    ) -> str:
        start, end = _time_range(start_time, end_time, period)

        async def operation(websocket: Any) -> str:
            preferences = await self._energy_preferences(websocket, 1)
            if preferences is not None and not isinstance(preferences, dict):
                raise HomeAssistantEnergyError(
                    "Home Assistant returned invalid preferences"
                )
            recorder_metadata = None
            if preferences is None:
                recorder_metadata = await self._call(
                    websocket, 2, {"type": "recorder/list_statistic_ids"}
                )
                configured = _recorder_energy_statistics(recorder_metadata)
            else:
                configured = _configured_statistics(preferences, include_live=False)
            allowed = {item["statistic_id"]: item for item in configured}
            if statistic_id not in allowed:
                raise HomeAssistantEnergyError(
                    "statistic_id is not an allowed energy statistic"
                )
            metadata = recorder_metadata or await self._call(
                websocket,
                2,
                {
                    "type": "recorder/get_statistics_metadata",
                    "statistic_ids": [statistic_id],
                },
            )
            statistics = await self._call(
                websocket,
                3,
                {
                    "type": "recorder/statistics_during_period",
                    "start_time": start.isoformat(),
                    "end_time": end.isoformat(),
                    "statistic_ids": [statistic_id],
                    "period": period,
                    "units": {"energy": "kWh"},
                    "types": ["change", "sum", "state"],
                },
            )
            if not isinstance(statistics, dict):
                raise HomeAssistantEnergyError(
                    "Home Assistant returned invalid statistics"
                )
            stat_metadata = next(
                (
                    item
                    for item in metadata or []
                    if isinstance(item, dict)
                    and item.get("statistic_id") == statistic_id
                ),
                {},
            )
            payload = {
                "read_only": True,
                "energy_dashboard_configured": preferences is not None,
                **allowed[statistic_id],
                "unit": _metadata_unit(stat_metadata),
                "start_time": start.isoformat(),
                "end_time": end.isoformat(),
                "period": period,
                "points": statistics.get(statistic_id, []),
            }
            return _bounded_json(payload, "points")

        return await self._query(operation)
