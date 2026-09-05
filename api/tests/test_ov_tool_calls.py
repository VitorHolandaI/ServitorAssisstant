"""How a tool call is got out of the model, and constrained on the way in."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcp_module.stremable_http.ov_chat import _parse_tool_calls  # noqa: E402


class _Tool:
    def __init__(self, name, schema):
        self.name = name
        self.description = f"does {name}"
        self.args_schema = schema


class ParseToolCallTests(unittest.TestCase):
    """The reader of what the model produced. Kept working either way."""

    def test_a_single_call_is_read(self):
        raw = '<tool_call>\n{"name": "get_forecast", "arguments": {"city": "CG"}}\n</tool_call>'
        self.assertEqual(_parse_tool_calls(raw),
                         [{"name": "get_forecast", "arguments": {"city": "CG"}}])

    def test_two_calls_are_both_read(self):
        raw = ('<tool_call>{"name": "a", "arguments": {}}</tool_call>'
               '<tool_call>{"name": "b", "arguments": {}}</tool_call>')
        self.assertEqual([c["name"] for c in _parse_tool_calls(raw)], ["a", "b"])

    def test_prose_around_the_call_is_ignored(self):
        raw = 'Let me check.\n<tool_call>{"name": "a", "arguments": {}}</tool_call>\nDone.'
        self.assertEqual(len(_parse_tool_calls(raw)), 1)

    def test_truncated_json_is_dropped_rather_than_guessed(self):
        # This is what happened at max_tokens=128: the block was cut mid-object.
        raw = '<tool_call>\n{"name": "get_forecast", "argum'
        self.assertEqual(_parse_tool_calls(raw), [])

    def test_no_call_means_no_calls(self):
        self.assertEqual(_parse_tool_calls("Just an answer."), [])
