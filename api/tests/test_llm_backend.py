"""The agent runs on two very different machines.

The box that keeps the sessions and the history is a VM with no GPU and an
Ollama next door. The laptop runs the model on its own iGPU through OpenVINO,
because the microphone is there. A hard-wired backend serves one and breaks
the other.
"""

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcp_module.stremable_http.client2 import _resolve_backend  # noqa: E402
from server import tokens  # noqa: E402


class ResolveBackendTest(unittest.TestCase):
    def test_defaults_to_ollama(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(_resolve_backend(None), "ollama")

    def test_environment_selects_openvino(self):
        with patch.dict(os.environ, {"SERVITOR_LLM_BACKEND": "OpenVINO"}, clear=True):
            self.assertEqual(_resolve_backend(None), "openvino")

    def test_argument_wins_over_environment(self):
        # The ear reaches into _llm.device, so it names its backend outright
        # and must not be talked out of it by a stray environment variable.
        with patch.dict(os.environ, {"SERVITOR_LLM_BACKEND": "ollama"}, clear=True):
            self.assertEqual(_resolve_backend("openvino"), "openvino")

    def test_unknown_backend_is_refused(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "must be one of"):
                _resolve_backend("llama.cpp")


class OllamaHostTest(unittest.TestCase):
    def test_host_is_read_at_call_time(self):
        # Server.py imports tokens before it loads .env. A module-level read
        # pinned the default and counted tokens against the wrong machine.
        with patch.dict(os.environ, {"OLLAMA_HOST": "http://192.168.0.19:11434"}):
            self.assertEqual(tokens._ollama_host(), "http://192.168.0.19:11434")

    def test_host_falls_back_to_localhost(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(tokens._ollama_host(), "http://127.0.0.1:11434")


if __name__ == "__main__":
    unittest.main()
