import os
import re
import json
import wave
import logging
import sqlite3
import datetime
import requests
from io import BytesIO
from pathlib import Path
from piper import PiperVoice
from dotenv import load_dotenv
import speech_recognition as sr
from piper import SynthesisConfig
from mcp_module.stremable_http.client2 import llm_mcp_client

DB_PATH = Path(__file__).parent.parent.parent / "data" / "tasks.db"

# lfm2.5-thinking:latest has 32K token context window.
# Reserve 4K for system prompt + response; ~28K for history ≈ 112K chars.
MAX_HISTORY_CHARS = 112_000

load_dotenv(Path(__file__).parent.parent / ".env")
voice_path = os.getenv("VOICE_PATH")
server_ip = os.getenv("SERVER_IP", "localhost")
MCP_ADDRESS = f"http://{server_ip}:8001/mcp"
DEBUG = os.getenv("DEBUG", "false").lower() == "true"

logging.basicConfig(
    level=logging.DEBUG if DEBUG else logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)
logger.info(f"[Server] voice={voice_path}  mcp={MCP_ADDRESS}  debug={DEBUG}")


class ServitorServer:

    def __init__(self, name, client_ip):
        self.name = name
        self.client_ip = client_ip
        self.agent = ""

        if not voice_path:
            raise ValueError("VOICE_PATH not set in api/.env")
        self.voice = PiperVoice.load(voice_path)
        logger.info(f"[Server] voice model loaded from {voice_path}")
        self.initial_agent()

    def initial_agent(self):
        self.base_prompt = (
            "You are now a warhammer 40k MAGOs, use the same personality as one, "
            "showing curiosity for science in all manners. Only need short responses. "
            "You are like a magos from a library from the imperium and answer all questions. "
            "When the user asks to create a task with a relative time like 'today', 'tomorrow', "
            "'at 5pm', you MUST use the current date/time provided below to calculate the exact "
            "due_at value in 'YYYY-MM-DD HH:MM:SS' format. "
            "When the user asks about weather and does NOT specify a location, ALWAYS call "
            "get_forecast() with NO arguments — the default location is Campina Grande, Paraíba, Brazil. "
            "NEVER ask the user for coordinates or location when calling get_forecast."
        )

        agent_mcp = llm_mcp_client(
            mcp_addresses=[MCP_ADDRESS],
            model_name="lfm2.5-thinking:latest",
            model_address="http://127.0.0.1:11434",
            system_prompt=self.base_prompt
        )
        self.agent = agent_mcp

    def _load_history(self) -> list:
        if not DB_PATH.exists():
            logger.debug("[Server] DB not found, returning empty history")
            return []
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT role, content, created_at FROM messages ORDER BY id DESC LIMIT 200"
            ).fetchall()
            conn.close()

            rows = list(reversed(rows))
            history = []
            total_chars = 0
            for row in reversed(rows):
                entry_chars = len(row["content"]) + len(row["created_at"]) + 20
                if total_chars + entry_chars > MAX_HISTORY_CHARS:
                    break
                history.insert(0, (row["role"], row["content"], row["created_at"]))
                total_chars += entry_chars
            logger.debug(f"[Server] loaded {len(history)} history messages ({total_chars} chars)")
            return history
        except Exception as e:
            logger.error(f"[Server] _load_history error: {e}", exc_info=DEBUG)
            return []

    def get_prompt_with_time(self):
        now = datetime.datetime.now()
        return (
            f"{self.base_prompt}\n\n"
            f"CURRENT DATE AND TIME: {now.strftime('%Y-%m-%d %H:%M:%S')} "
            f"({now.strftime('%A')}), Timezone: America/Recife"
        )

    async def process_ollama(self, talk: str):
        logger.info(f"[Server] process_ollama: {talk[:80]!r}")
        history = self._load_history()
        response = await self.agent.get_response(talk, history=history, system_prompt=self.get_prompt_with_time())

        if response is None:
            logger.error("[Server] agent returned None")
            return "Some error occurred"

        try:
            raw = response['messages'][-1].content
            result = re.sub(r'<think>.*?</think>\n*', '', raw, flags=re.DOTALL)
            logger.info(f"[Server] process_ollama response: {result[:120]!r}")
            return result
        except Exception as e:
            logger.error(f"[Server] process_ollama parse error: {e}", exc_info=DEBUG)
            return "Some error occurred"

    async def process_ollama_stream(self, talk: str):
        """Yields (type, content) tuples where type is 'thinking' or 'text'."""
        logger.info(f"[Server] process_ollama_stream: {talk[:80]!r}")

        THINKING_START = "Thinking..."
        THINKING_END = "...done thinking."
        inside_thinking = False
        buffer = ""
        history = self._load_history()

        try:
            async for chunk in self.agent.get_response_stream(talk, history=history, system_prompt=self.get_prompt_with_time()):
                buffer += chunk

                if not inside_thinking:
                    if THINKING_START in buffer:
                        idx = buffer.index(THINKING_START)
                        before = buffer[:idx]
                        if before.strip():
                            yield ("text", before)
                        buffer = buffer[idx + len(THINKING_START):].lstrip("\n")
                        inside_thinking = True
                    else:
                        safe = len(buffer) - len(THINKING_START)
                        if safe > 0:
                            yield ("text", buffer[:safe])
                            buffer = buffer[safe:]
                else:
                    if THINKING_END in buffer:
                        idx = buffer.index(THINKING_END)
                        if idx > 0:
                            yield ("thinking", buffer[:idx])
                        buffer = buffer[idx + len(THINKING_END):].lstrip("\n")
                        inside_thinking = False
                    else:
                        safe = len(buffer) - len(THINKING_END)
                        if safe > 0:
                            yield ("thinking", buffer[:safe])
                            buffer = buffer[safe:]

            if buffer.strip():
                yield ("thinking" if inside_thinking else "text", buffer)

        except Exception as e:
            logger.error(f"[Server] process_ollama_stream error: {e}", exc_info=DEBUG)
            raise

    def generate_audio(self, text: str) -> bytes:
        logger.debug(f"[Server] generate_audio: {len(text)} chars")
        bytes_audio = BytesIO()
        syn_config_1 = SynthesisConfig(
            volume=0.1,
            length_scale=1.0,
            noise_scale=0.5,
            noise_w_scale=1.0,
            normalize_audio=False,
        )
        with wave.open(bytes_audio, "wb") as wav_file:
            self.voice.synthesize_wav(text, wav_file, syn_config=syn_config_1)
        return bytes_audio.getvalue()

    async def process_audio(self, audio_file):
        logger.info("[Server] process_audio")
        r = sr.Recognizer()

        with sr.AudioFile(audio_file) as source:
            audio = r.record(source)

        talk = ""
        try:
            raw = r.recognize_vosk(audio)
            logger.debug(f"[Server] vosk raw: {raw}")
            parsed = json.loads(raw)
            talk = parsed.get("text", "").strip()
        except sr.UnknownValueError:
            logger.warning("[Server] vosk could not understand audio")
            return None
        except sr.RequestError as e:
            logger.error(f"[Server] vosk request error: {e}")
            return None

        logger.info(f"[Server] recognized: {talk!r}")
        if len(talk) < 10 or len(talk.split()) < 3:
            logger.info(f"[Server] short/noise input, skipping")
            return None

        talk = await self.process_ollama(talk)
        return self.generate_audio(talk)

    async def check_due_reminders(self):
        if not DB_PATH.exists():
            logger.debug("[Server] no DB, skipping reminders")
            return []

        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        now = datetime.datetime.now()
        current_time = now.strftime('%Y-%m-%d %H:%M')
        tasks = conn.execute(
            "SELECT * FROM tasks WHERE is_completed = 0 AND due_at IS NOT NULL AND strftime('%Y-%m-%d %H:%M', due_at) = ?",
            (current_time,)
        ).fetchall()
        conn.close()

        if not tasks:
            return []

        reminded = []
        for task in tasks:
            title = task['title']
            desc = task['description'] or ''
            logger.info(f"[Server] reminder due: {title}")

            prompt = (
                f"ALERT: You must remind the user about a scheduled task that is due RIGHT NOW. "
                f"The task is: '{title}'. "
                f"{'Description: ' + desc + '. ' if desc else ''}"
                f"Give a short urgent reminder in character as a Magos. Keep it under 3 sentences."
            )

            response = await self.process_ollama(prompt)
            audio_bytes = self.generate_audio(response)
            self.send_audio_bytes(audio_bytes)
            reminded.append({"id": task['id'], "title": title})

        return reminded

    def send_audio_bytes(self, audio_bytes):
        url = f"http://{self.client_ip}:8000/play_file"
        logger.debug(f"[Server] send_audio_bytes to {url}")
        byte_file = BytesIO(audio_bytes)
        try:
            res = requests.post(url, files={'my_file': byte_file})
            logger.info(f"[Server] audio sent, status={res.status_code}")
        except Exception as e:
            logger.error(f"[Server] send_audio_bytes error: {e}", exc_info=DEBUG)
