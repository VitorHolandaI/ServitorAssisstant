import json
import asyncio
import logging
import sqlite3
import datetime
import os
from pathlib import Path
from contextlib import asynccontextmanager
from dotenv import load_dotenv

from fastapi import FastAPI, UploadFile, HTTPException
from typing import Dict, Any, Optional
from pydantic import BaseModel
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


Servitor: ServitorServer | None = None


async def _reminder_loop():
    while True:
        await asyncio.sleep(60)
        try:
            await Servitor.check_due_reminders()
        except Exception as e:
            logger.error(f"[API] reminder loop error: {e}", exc_info=DEBUG)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global Servitor
    _ensure_messages_table()
    Servitor = ServitorServer("ServitorServer", CLIENT_IP)
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

    await asyncio.to_thread(_save_message, "user", message)

    queue: asyncio.Queue = asyncio.Queue()

    async def produce():
        """Runs as independent background task — survives client disconnect."""
        full_response = ""
        try:
            async for chunk_type, chunk in Servitor.process_ollama_stream(message):
                await queue.put((chunk_type, chunk))
                if chunk_type == "text":
                    full_response += chunk
        except Exception as e:
            logger.error(f"[API] producer error: {e}", exc_info=DEBUG)
            await queue.put(("error", str(e)))
        finally:
            await queue.put(None)
            if full_response.strip():
                await asyncio.to_thread(_save_message, "assistant", full_response)
                logger.info(f"[API] assistant saved ({len(full_response)} chars)")
                if audio_mode:
                    try:
                        audio_bytes = Servitor.generate_audio(full_response)
                        Servitor.send_audio_bytes(audio_bytes)
                    except Exception as e:
                        logger.error(f"[API] audio error: {e}", exc_info=DEBUG)
            else:
                logger.warning("[API] producer finished with empty response — not saved")

    asyncio.create_task(produce())

    async def event_generator():
        """SSE stream to client. Stops on disconnect; producer keeps running."""
        try:
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=5.0)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue

                if item is None:
                    yield f"data: {json.dumps({'done': True})}\n\n"
                    break

                chunk_type, chunk = item
                if chunk_type == "error":
                    yield f"data: {json.dumps({'error': chunk, 'type': 'error'})}\n\n"
                    yield f"data: {json.dumps({'done': True})}\n\n"
                    break
                yield f"data: {json.dumps({'content': chunk, 'type': chunk_type})}\n\n"
        except GeneratorExit:
            logger.info("[API] client disconnected — producer continues in background")

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


TASK_FIELDS = (
    "title", "description", "due_at", "is_completed",
    "recurrence_type", "recurrence_interval",
    "recurrence_day_of_week", "recurrence_day_of_month", "timezone",
)


class TaskUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    due_at: Optional[str] = None
    is_completed: Optional[bool] = None
    recurrence_type: Optional[str] = None
    recurrence_interval: Optional[int] = None
    recurrence_day_of_week: Optional[int] = None
    recurrence_day_of_month: Optional[int] = None
    timezone: Optional[str] = None


class TaskCreate(BaseModel):
    title: str
    description: Optional[str] = None
    due_at: Optional[str] = None
    recurrence_type: str = "none"
    recurrence_interval: int = 1
    recurrence_day_of_week: Optional[int] = None
    recurrence_day_of_month: Optional[int] = None
    timezone: str = "America/Recife"


def _tasks_conn():
    if not DB_PATH.exists():
        raise HTTPException(503, f"DB not found at {DB_PATH}")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


@app.get("/tasks")
async def list_all_tasks(show_completed: bool = True, limit: int = 200):
    conn = _tasks_conn()
    if show_completed:
        rows = conn.execute(
            "SELECT * FROM tasks ORDER BY is_completed ASC, due_at ASC, id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM tasks WHERE is_completed = 0 ORDER BY due_at ASC, id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    conn.close()
    return {"tasks": [dict(r) for r in rows]}


@app.get("/tasks/{task_id}")
async def get_task_api(task_id: int):
    conn = _tasks_conn()
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    conn.close()
    if row is None:
        raise HTTPException(404, f"Task {task_id} not found")
    return dict(row)


@app.put("/tasks/{task_id}")
async def update_task_api(task_id: int, payload: TaskUpdate):
    fields = {k: v for k, v in payload.model_dump(exclude_unset=True).items() if k in TASK_FIELDS}
    if not fields:
        raise HTTPException(400, "No updatable fields supplied")
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [task_id]
    conn = _tasks_conn()
    cur = conn.execute(f"UPDATE tasks SET {set_clause} WHERE id = ?", tuple(values))
    conn.commit()
    if cur.rowcount == 0:
        conn.close()
        raise HTTPException(404, f"Task {task_id} not found")
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    conn.close()
    logger.info(f"[API] task {task_id} updated fields={list(fields)}")
    return dict(row)


@app.post("/tasks")
async def create_task_api(payload: TaskCreate):
    conn = _tasks_conn()
    created_at = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    cur = conn.execute(
        """INSERT INTO tasks (title, description, created_at, due_at, recurrence_type,
           recurrence_interval, recurrence_day_of_week, recurrence_day_of_month, timezone)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (payload.title, payload.description, created_at, payload.due_at,
         payload.recurrence_type, payload.recurrence_interval,
         payload.recurrence_day_of_week, payload.recurrence_day_of_month, payload.timezone),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (cur.lastrowid,)).fetchone()
    conn.close()
    return dict(row)


@app.delete("/tasks/{task_id}")
async def delete_task_api(task_id: int):
    conn = _tasks_conn()
    cur = conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    conn.commit()
    conn.close()
    if cur.rowcount == 0:
        raise HTTPException(404, f"Task {task_id} not found")
    return {"status": "deleted", "id": task_id}


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
