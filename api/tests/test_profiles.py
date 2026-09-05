"""The local profile is what keeps a small model's context small."""
from __future__ import annotations

import logging
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcp_module.profiles import (  # noqa: E402
    DEFAULT_PROFILE,
    LOCAL_TOOLS,
    PROFILES,
    select_tools,
)


class _Tool:
    def __init__(self, name: str):
        self.name = name


def _tools(*names: str) -> list[_Tool]:
    return [_Tool(n) for n in names]


class ProfileSelectionTests(unittest.TestCase):
    def test_full_takes_everything_the_servers_offer(self):
        tools = _tools("get_forecast", "wake_on_lan_gpu", "divide_numbers")
        self.assertEqual([t.name for t in select_tools(tools, "full")],
                         ["get_forecast", "wake_on_lan_gpu", "divide_numbers"])

    def test_local_keeps_only_what_it_lists(self):
        tools = _tools("get_forecast", "wake_on_lan_gpu", "divide_numbers")
        self.assertEqual([t.name for t in select_tools(tools, "local")], ["get_forecast"])

    def test_load_order_is_preserved(self):
        tools = _tools("nextcloud_today", "get_forecast")
        self.assertEqual([t.name for t in select_tools(tools, "local")],
                         ["nextcloud_today", "get_forecast"])

    def test_an_unknown_profile_falls_back_to_full_rather_than_to_nothing(self):
        tools = _tools("get_forecast", "wake_on_lan_gpu")
        with self.assertLogs("mcp_module.profiles", level=logging.WARNING):
            kept = select_tools(tools, "typo")
        self.assertEqual(len(kept), 2)

    def test_an_empty_profile_name_is_the_default(self):
        tools = _tools("get_forecast", "wake_on_lan_gpu")
        self.assertEqual(len(select_tools(tools, "")), len(select_tools(tools, DEFAULT_PROFILE)))

    def test_the_profile_name_is_case_insensitive(self):
        self.assertEqual(len(select_tools(_tools("get_forecast"), "LOCAL")), 1)

    def test_a_tool_a_profile_wants_but_no_server_offers_is_reported(self):
        with self.assertLogs("mcp_module.profiles", level=logging.WARNING) as caught:
            select_tools(_tools("get_forecast"), "local")
        self.assertIn("nextcloud_today", "".join(caught.output))

    def test_nothing_destructive_reaches_the_spoken_profile(self):
        for name in LOCAL_TOOLS:
            self.assertNotIn("delete", name)
            self.assertNotIn("wake_on_lan", name)

    def test_the_spoken_profile_drops_what_it_was_asked_to_drop(self):
        for name in ("list_local_tasks", "wake_on_lan_gpu", "add_numbers",
                     "help", "task_help", "default_response"):
            self.assertNotIn(name, LOCAL_TOOLS)

    def test_nextcloud_comes_from_the_slim_server(self):
        nextcloud = {n for n in LOCAL_TOOLS if "nextcloud" in n}
        self.assertEqual(nextcloud, {
            "nextcloud_today", "nextcloud_pending_tasks",
            "nextcloud_add_task", "nextcloud_finish_task",
        })

    def test_full_is_declared_as_no_allow_list(self):
        self.assertIsNone(PROFILES["full"])
