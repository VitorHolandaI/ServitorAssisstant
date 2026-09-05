"""How a tool call is got out of the model, and constrained on the way in."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcp_module.stremable_http.ov_chat import (  # noqa: E402
    _is_device_fault,
    _parse_tool_calls,
)


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


class DeviceFaultTests(unittest.TestCase):
    """Telling a dead GPU apart from an ordinary bad turn.

    It matters because they are handled oppositely: an ordinary failure drops
    the pipeline and carries on, while a device fault must NOT touch it - the
    destructor throws a second time and std::terminate takes the process.
    """

    def test_the_opencl_error_we_actually_get_is_recognised(self):
        error = RuntimeError(
            "Exception from src/plugins/intel_gpu/src/runtime/ocl/ocl_stream.cpp:395:\n"
            "[GPU] clFinish, error code: -5 CL_OUT_OF_RESOURCES"
        )
        self.assertTrue(_is_device_fault(error))

    def test_the_npu_stale_tensor_error_is_not_a_device_fault(self):
        # This one is recoverable by rebuilding the pipeline.
        self.assertFalse(_is_device_fault(RuntimeError("Cannot find tensor for port Parameter_1")))

    def test_an_ordinary_error_is_not_a_device_fault(self):
        self.assertFalse(_is_device_fault(ValueError("bad json")))
        self.assertFalse(_is_device_fault(RuntimeError("model not found")))
