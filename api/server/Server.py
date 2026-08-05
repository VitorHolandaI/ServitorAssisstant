import os
import re
import json
import wave
import logging
import sqlite3
import datetime
import requests
from zoneinfo import ZoneInfo
from io import BytesIO
from pathlib import Path
from piper import PiperVoice
from dotenv import load_dotenv
import speech_recognition as sr
from piper import SynthesisConfig
from mcp_module.stremable_http.client2 import llm_mcp_client

DB_PATH = Path(__file__).parent.parent.parent / "data" / "tasks.db"

# Context budget: 32K tokens total (num_ctx=32768).
# ~500 tokens for system prompt, ~4000 reserved for tool call results,
# ~500 for user message + buffer. Remainder for history.
# At ~5 chars/token for Portuguese text:
# (32768 - 500 - 4000 - 500) * 5 ≈ 139K chars. Use 80K for safety.
MAX_HISTORY_CHARS = 80_000

load_dotenv(Path(__file__).parent.parent.parent / ".env")
voice_path = os.getenv("VOICE_PATH")
server_ip = os.getenv("SERVER_IP", "localhost")
MCP_ADDRESS = f"http://{server_ip}:8001/mcp"
MCP_EXTRA_ADDRESSES = [
    addr.strip() for addr in os.getenv("MCP_EXTRA_ADDRESSES", "").split(",") if addr.strip()
]
DEBUG = os.getenv("DEBUG", "false").lower() == "true"

logging.basicConfig(
    level=logging.DEBUG if DEBUG else logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)
logger.info(f"[Server] voice={voice_path}  mcp={[MCP_ADDRESS, *MCP_EXTRA_ADDRESSES]}  debug={DEBUG}")


class ServitorServer:

    def __init__(self, name, client_ip):
        self.name = name
        self.client_ip = client_ip
        self.agent = ""

        if not voice_path:
            raise ValueError("VOICE_PATH not set in repository-root .env")
        self.voice = PiperVoice.load(voice_path)
        logger.info(f"[Server] voice model loaded from {voice_path}")
        self.initial_agent()

    def initial_agent(self):
        self.base_prompt = (
            "You are now a warhammer 40k MAGOs, use the same personality as one, "
            "showing curiosity for science in all manners. Only need short responses. "
            "You are like a magos from a library from the imperium and answer all questions. "
            "CRITICAL: You MUST call the appropriate tool to get real data BEFORE responding. "
            "NEVER guess, invent, or hallucinate information about the user's tasks, weather, "
            "or development activity — always use the available tools. "
            "When the user asks about tasks ('list tasks', 'quais tasks', 'mostre tasks', "
            "'tasks pendentes', 'minhas tasks', 'atividades do Nextcloud', 'Nextcloud Tasks', "
            "'o que tem pra fazer', or similar), "
            "ALWAYS call list_nextcloud_tasks() first and only respond based on the tool result. "
            "If the user asks for 'all tasks' or 'todas as tasks', call "
            "list_nextcloud_tasks(show_completed=False, limit=20): all means all incomplete tasks, "
            "including overdue tasks, and never completed tasks. Only set show_completed=True when "
            "the user explicitly asks to include completed tasks or task history. Never request more than 20 tasks. "
            "When the user names a Nextcloud task list such as TrabalhoFNDE, pass that exact name "
            "as the calendar parameter. Use get_nextcloud_task for one task's full details, "
            "update_nextcloud_task to edit fields or reopen it, delete_nextcloud_task only for an "
            "explicit deletion request, and move_nextcloud_task to change its task list. "
            "When the user asks about today's calendar, agenda, appointments, meetings, current event, "
            "or what comes next today, ALWAYS call list_nextcloud_events() with no date first. "
            "For another date, call list_nextcloud_events(date='YYYY-MM-DD'). Use the exact current "
            "time and the ended/ongoing/upcoming labels returned by the tool; never infer calendar state yourself. "
            "When the user asks to create a task or reminder, use create_nextcloud_task(). "
            "When the user asks to add or change a reminder on an existing Nextcloud task, "
            "use set_nextcloud_task_reminder(). A successful reminder must be present both "
            "on the VTODO and on its linked VEVENT; do not claim success without the tool result. "
            "When the user asks to mark, complete, finish, or set a Nextcloud task as done, "
            "ALWAYS call complete_nextcloud_task() with the exact title or UID. "
            "Never claim that a task was completed unless complete_nextcloud_task returned a "
            "successful completed or already-completed result in the current request. "
            "Tools containing '_local_task' access only SQLite. Never call them unless the user "
            "explicitly says 'local' or 'SQLite'. The word 'Nextcloud' always requires "
            "list_nextcloud_tasks() or create_nextcloud_task(). "
            "When the user asks to create a task with a relative time like 'today', 'tomorrow', "
            "'at 5pm', you MUST use the current date/time provided below to calculate the exact "
            "due_at value in 'YYYY-MM-DD HH:MM:SS' format. "
            "When the user asks about weather and does NOT specify a location, ALWAYS call "
            "get_forecast() with NO arguments — the default location is Campina Grande, Paraíba, Brazil. "
            "NEVER ask the user for coordinates or location when calling get_forecast. "
            "For any question about what the user did this week in development, coding activity this week, "
            "development summary, weekly dev work, GitHub activity, Gitea activity, or similar requests, "
            "ALWAYS use summarize_weekly_dev_activity first. "
            "When the user asks for a summary of coding activity this week, use summarize_weekly_dev_activity. "
            "After calling summarize_weekly_dev_activity, respond with a concise human summary of the activity. "
            "Do NOT reinterpret raw event names like mirror_sync_push or mirror_sync_create as user support questions. "
            "Treat those values only as activity labels from the source system."
        )

        ollama_host = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434")
        agent_mcp = llm_mcp_client(
            mcp_addresses=[MCP_ADDRESS, *MCP_EXTRA_ADDRESSES],
            model_name="gemma4:e2b-it-qat",
            model_address=ollama_host,
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
        timezone_name = os.getenv("NC_TIMEZONE", "America/Recife")
        now = datetime.datetime.now(ZoneInfo(timezone_name))
        return (
            f"{self.base_prompt}\n\n"
            f"CURRENT DATE AND TIME: {now.strftime('%Y-%m-%d %H:%M:%S')} "
            f"({now.strftime('%A')}), Timezone: {timezone_name}"
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

        # Different models emit different markers around chain-of-thought.
        # Handle both Ollama "Thinking..." prose and raw <think> tags.
        THINKING_STARTS = ("Thinking...", "<think>")
        THINKING_ENDS = ("...done thinking.", "</think>")
        MAX_MARKER = max(len(m) for m in THINKING_STARTS + THINKING_ENDS)

        def _find_first(text: str, markers):
            best_idx = -1
            best_marker = None
            for m in markers:
                i = text.find(m)
                if i != -1 and (best_idx == -1 or i < best_idx):
                    best_idx = i
                    best_marker = m
            return best_idx, best_marker

        inside_thinking = False
        buffer = ""
        history = self._load_history()

        try:
            async for chunk in self.agent.get_response_stream(talk, history=history, system_prompt=self.get_prompt_with_time()):
                buffer += chunk

                if not inside_thinking:
                    idx, marker = _find_first(buffer, THINKING_STARTS)
                    if idx != -1:
                        before = buffer[:idx]
                        if before.strip():
                            yield ("text", before)
                        buffer = buffer[idx + len(marker):].lstrip("\n")
                        inside_thinking = True
                    else:
                        safe = len(buffer) - MAX_MARKER
                        if safe > 0:
                            yield ("text", buffer[:safe])
                            buffer = buffer[safe:]
                else:
                    idx, marker = _find_first(buffer, THINKING_ENDS)
                    if idx != -1:
                        if idx > 0:
                            yield ("thinking", buffer[:idx])
                        buffer = buffer[idx + len(marker):].lstrip("\n")
                        inside_thinking = False
                    else:
                        safe = len(buffer) - MAX_MARKER
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

    async def transcribe_audio(self, audio_file) -> str | None:
        logger.info("[Server] transcribe_audio")
        r = sr.Recognizer()

        try:
            with sr.AudioFile(audio_file) as source:
                audio = r.record(source)
        except ValueError as e:
            logger.warning(f"[Server] audio file could not be read: {e}")
            return None

        talk = ""
        try:
            raw = r.recognize_vosk(audio)
            logger.debug(f"[Server] vosk raw: {raw!r}")
            if not raw or not raw.strip():
                logger.info("[Server] vosk returned empty result")
                return None
            raw = raw.strip()
            if raw.startswith("{"):
                parsed = json.loads(raw)
                talk = parsed.get("text", "").strip()
            else:
                talk = raw
        except sr.UnknownValueError:
            logger.warning("[Server] vosk could not understand audio")
            return None
        except sr.RequestError as e:
            logger.error(f"[Server] vosk request error: {e}")
            return None
        except json.JSONDecodeError as e:
            logger.warning(f"[Server] vosk invalid result: {e}")
            return None

        logger.info(f"[Server] recognized: {talk!r}")
        if len(talk) < 10 or len(talk.split()) < 3:
            logger.info("[Server] short/noise input, skipping")
            return None
        return talk

    async def process_audio_text(self, audio_file) -> str | None:
        talk = await self.transcribe_audio(audio_file)
        if talk is None:
            return None
        return await self.process_ollama(talk)

    async def process_audio(self, audio_file):
        talk = await self.process_audio_text(audio_file)
        if talk is None:
            return None
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

    async def compact_conversation(self) -> str:
        history = self._load_history()
        if not history:
            return "No conversation to compact."

        convo_text = "\n".join(f"{r}: {c[:200]}" for r, c, _ in history)
        prompt = (
            "Compacte a conversa abaixo em um resumo conciso em português, "
            "preservando todas as informações importantes, decisões tomadas, "
            "e o contexto necessário para continuar a conversa. "
            "Mantenha o tom de Warhammer 40k Magos. Seja breve.\n\n"
            f"{convo_text}"
        )

        response = await self.process_ollama(prompt)
        if response in ("Some error occurred", None):
            return "Compact failed."

        conn = sqlite3.connect(DB_PATH)
        conn.execute("DELETE FROM messages")
        now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        conn.execute(
            "INSERT INTO messages (role, content, created_at) VALUES (?, ?, ?)",
            ("assistant", response, now)
        )
        conn.commit()
        conn.close()
        logger.info(f"[Server] conversation compacted: {len(response)} chars")
        return response

    def send_audio_bytes(self, audio_bytes):
        url = f"http://{self.client_ip}:8000/play_file"
        logger.debug(f"[Server] send_audio_bytes to {url}")
        byte_file = BytesIO(audio_bytes)
        try:
            res = requests.post(url, files={'my_file': byte_file})
            logger.info(f"[Server] audio sent, status={res.status_code}")
        except Exception as e:
            logger.error(f"[Server] send_audio_bytes error: {e}", exc_info=DEBUG)
