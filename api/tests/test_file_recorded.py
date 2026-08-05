import io
import sys
import unittest
import wave
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

import ServerApi  # noqa: E402


class FakeServitorServer:
    def __init__(self, name, client_ip):
        self.name = name

    async def check_due_reminders(self):
        return []


def _wav_bytes() -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * 1600)
    return buf.getvalue()


class FileRecordedEndpointTests(unittest.TestCase):
    def setUp(self):
        self.server_patcher = patch.object(ServerApi, "ServitorServer", FakeServitorServer)
        self.server_patcher.start()
        self.addCleanup(self.server_patcher.stop)
        self.client = TestClient(ServerApi.app)

    def _post(self, response_format=None):
        files = {"my_file": ("voice.wav", _wav_bytes(), "audio/wav")}
        data = {"response_format": response_format} if response_format else None
        return self.client.post("/file_recorded", files=files, data=data)

    def test_default_returns_text(self):
        fake = Mock()
        fake.process_audio_text = AsyncMock(return_value="Olá do relógio")
        fake.process_audio = AsyncMock()
        with patch.object(ServerApi, "Servitor", fake):
            res = self._post()
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json(), {"response": "Olá do relógio"})
        fake.process_audio_text.assert_awaited_once()
        fake.process_audio.assert_not_called()

    def test_explicit_text_flag_returns_text(self):
        fake = Mock()
        fake.process_audio_text = AsyncMock(return_value="Resposta em texto")
        with patch.object(ServerApi, "Servitor", fake):
            res = self._post(response_format="text")
        self.assertEqual(res.json(), {"response": "Resposta em texto"})

    def test_audio_flag_returns_wav(self):
        wav = _wav_bytes()
        fake = Mock()
        fake.process_audio = AsyncMock(return_value=wav)
        fake.process_audio_text = AsyncMock()
        with patch.object(ServerApi, "Servitor", fake):
            res = self._post(response_format="audio")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.headers["content-type"], "audio/wav")
        self.assertEqual(res.content, wav)
        fake.process_audio.assert_awaited_once()
        fake.process_audio_text.assert_not_called()

    def test_text_mode_ignored_input(self):
        fake = Mock()
        fake.process_audio_text = AsyncMock(return_value=None)
        with patch.object(ServerApi, "Servitor", fake):
            res = self._post()
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["status"], "ignored")

    def test_audio_mode_ignored_input(self):
        fake = Mock()
        fake.process_audio = AsyncMock(return_value=None)
        with patch.object(ServerApi, "Servitor", fake):
            res = self._post(response_format="audio")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["status"], "ignored")

    def test_corrupt_audio_is_ignored_not_500(self):
        fake = Mock()
        fake.process_audio_text = AsyncMock(return_value=None)
        with patch.object(ServerApi, "Servitor", fake):
            res = self.client.post(
                "/file_recorded",
                files={"my_file": ("broken.wav", b"not a wav", "audio/wav")},
            )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["status"], "ignored")


class TranscribeAudioTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _fake_sr(raw_result):
        from types import SimpleNamespace

        class FakeRecognizer:
            def record(self, source):
                return "audio"

            def recognize_vosk(self, audio):
                return raw_result

        class FakeAudioFile:
            def __init__(self, f):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        return SimpleNamespace(
            Recognizer=FakeRecognizer,
            AudioFile=FakeAudioFile,
            UnknownValueError=ValueError,
            RequestError=RuntimeError,
        )

    def _server(self):
        from server.Server import ServitorServer

        return object.__new__(ServitorServer)

    async def test_empty_vosk_result_returns_none(self):
        with patch("server.Server.sr", self._fake_sr("")):
            result = await self._server().transcribe_audio(object())
        self.assertIsNone(result)

    async def test_invalid_json_result_returns_none(self):
        with patch("server.Server.sr", self._fake_sr("not json")):
            result = await self._server().transcribe_audio(object())
        self.assertIsNone(result)

    async def test_valid_text_passes_through(self):
        with patch("server.Server.sr", self._fake_sr('{"text": "esta frase e valida e longa o suficiente"}')):  # noqa: E501
            result = await self._server().transcribe_audio(object())
        self.assertEqual(result, "esta frase e valida e longa o suficiente")


if __name__ == "__main__":
    unittest.main()
