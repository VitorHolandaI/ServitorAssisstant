import json
import asyncio
import logging
import sqlite3
import datetime
import os
from pathlib import Path
from contextlib import asynccontextmanager
from dotenv import load_dotenv

from fastapi import FastAPI, UploadFile
from typing import Dict, Any
from server import ServitorServer
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

import uvicorn

load_dotenv(Path(__file__).parent / ".env")

DB_PATH = Path(__file__).parent.parent / "data" / "tasks.db"
CLIENT_IP = os.getenv("CLIENT_IP", "192.168.0.22")
DEBUG = os.getenv("DEBUG", "false").lower() == "true"

logging.basicConfig(
    level=logging.DEBUG if DEBUG else logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)


def _ensure_messages_table():
    """Create messages table if MCP server hasn't run init_db yet."""
    if not DB_PATH.exists():
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at DATETIME NOT NULL
        )
    """)
    conn.commit()
    conn.close()


def _save_message(role: str, content: str):
    try:
        conn = sqlite3.connect(DB_PATH)
        now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        conn.execute(
            "INSERT INTO messages (role, content, created_at) VALUES (?, ?, ?)",
            (role, content, now)
        )
        conn.commit()
        conn.close()
        logger.debug(f"[API] saved message role={role} len={len(content)}")
    except Exception as e:
        logger.error(f"[API] _save_message error: {e}", exc_info=DEBUG)


async def _reminder_loop():
    while True:
        await asyncio.sleep(60)
        try:
            await Servitor.check_due_reminders()
        except Exception as e:
            logger.error(f"[API] reminder loop error: {e}", exc_info=DEBUG)


@asynccontextmanager
async def lifespan(app: FastAPI):
    _ensure_messages_table()
    task = asyncio.create_task(_reminder_loop())
    yield
    task.cancel()


app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


Servitor = ServitorServer("ServitorServer", CLIENT_IP)


@app.post("/receive_message")
async def receive_text(data: Dict[str, Any]):
    message = data.get("message", "No message provided")
    agent_message = await Servitor.process_ollama(message)
    return {"response": f"Message received {agent_message}"}


@app.post("/stream_message")
async def stream_message(data: Dict[str, Any]):
    message = data.get("message", "No message provided")
    audio_mode = data.get("audio", False)
    logger.info(f"[API] stream_message: {message[:80]!r} audio={audio_mode}")

    async def event_generator():
        await asyncio.to_thread(_save_message, "user", message)
        full_response = ""
        try:
            async for chunk_type, chunk in Servitor.process_ollama_stream(message):
                if chunk_type == "text":
                    full_response += chunk
                yield f"data: {json.dumps({'content': chunk, 'type': chunk_type})}\n\n"
            logger.info(f"[API] stream complete, response={len(full_response)} chars")
        except Exception as e:
            logger.error(f"[API] stream error: {e}", exc_info=DEBUG)
            yield f"data: {json.dumps({'error': str(e), 'type': 'error'})}\n\n"
        finally:
            yield f"data: {json.dumps({'done': True})}\n\n"
            if full_response.strip():
                await asyncio.to_thread(_save_message, "assistant", full_response)
            else:
                logger.warning("[API] stream ended with empty response — assistant message not saved")

        if audio_mode and full_response.strip():
            try:
                audio_bytes = Servitor.generate_audio(full_response)
                Servitor.send_audio_bytes(audio_bytes)
            except Exception as e:
                logger.error(f"[API] audio generation error: {e}", exc_info=DEBUG)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.get("/conversation")
async def get_conversation(limit: int = 100):
    if not DB_PATH.exists():
        return {"messages": []}
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT role, content, created_at FROM messages ORDER BY id DESC LIMIT ?",
        (limit,)
    ).fetchall()
    conn.close()
    return {"messages": [dict(r) for r in reversed(rows)]}


@app.delete("/conversation")
async def clear_conversation():
    if DB_PATH.exists():
        conn = sqlite3.connect(DB_PATH)
        conn.execute("DELETE FROM messages")
        conn.commit()
        conn.close()
    logger.info("[API] conversation cleared")
    return {"status": "cleared"}


@app.get("/check_reminders")
async def check_reminders():
    reminded = await Servitor.check_due_reminders()
    return {"reminded": reminded}


@app.post("/file_recorded")
async def create_upload_file(my_file: UploadFile):
    logger.info(f"[API] file_recorded: {my_file.filename}")
    file = my_file.file
    audio_bytes = await Servitor.process_audio(file)
    if audio_bytes is None:
        return {"status": "ignored", "reason": "short or noise input"}
    Servitor.send_audio_bytes(audio_bytes)
    return {"filename": my_file.filename}


if __name__ == "__main__":
    uvicorn.run("ServerApi:app", host="0.0.0.0", port=8000, log_level="debug" if DEBUG else "info")
