import json
import asyncio
import sqlite3
import datetime
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, UploadFile
from typing import Dict, Any
from server import ServitorServer
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

import uvicorn

DB_PATH = Path(__file__).parent.parent / "data" / "tasks.db"


def _save_message(role: str, content: str):
    conn = sqlite3.connect(DB_PATH)
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    conn.execute(
        "INSERT INTO messages (role, content, created_at) VALUES (?, ?, ?)",
        (role, content, now)
    )
    conn.commit()
    conn.close()


async def _reminder_loop():
    while True:
        await asyncio.sleep(60)
        try:
            await Servitor.check_due_reminders()
        except Exception as e:
            print(f"[Reminder] Error: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(_reminder_loop())
    yield
    task.cancel()


app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows all origins
    allow_credentials=True,
    allow_methods=["*"],  # Allows all methods
    allow_headers=["*"],  # Allows all headers
)


Servitor = ServitorServer("ServitorServer", "192.168.0.22")


@app.post("/receive_message")
async def receive_text(data: Dict[str, Any]):
    message = data.get("message", "No message provided")
    agent_message = await Servitor.process_ollama(message)
    return {"response": f"Message received {agent_message}"}


@app.post("/stream_message")
async def stream_message(data: Dict[str, Any]):
    message = data.get("message", "No message provided")
    audio_mode = data.get("audio", False)

    async def event_generator():
        _save_message("user", message)
        full_response = ""
        async for chunk in Servitor.process_ollama_stream(message):
            full_response += chunk
            yield f"data: {json.dumps({'content': chunk})}\n\n"
        yield f"data: {json.dumps({'done': True})}\n\n"

        if full_response.strip():
            _save_message("assistant", full_response)

        if audio_mode and full_response.strip():
            audio_bytes = Servitor.generate_audio(full_response)
            Servitor.send_audio_bytes(audio_bytes)

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
    return {"status": "cleared"}


@app.get("/check_reminders")
async def check_reminders():
    reminded = await Servitor.check_due_reminders()
    return {"reminded": reminded}


@app.post("/file_recorded")
async def create_upload_file(my_file: UploadFile):
    print(my_file.filename)
    file = my_file.file
    audio_bytes = await Servitor.process_audio(file)
    if audio_bytes is None:
        return {"status": "ignored", "reason": "short or noise input"}
    Servitor.send_audio_bytes(audio_bytes)
    return {"filename": my_file}


if __name__ == "__main__":
    uvicorn.run("ServerApi:app", host="0.0.0.0", port=8000, log_level="debug")
