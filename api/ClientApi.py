import asyncio
import threading
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, UploadFile

try:
    from .client import ServitorClient
except ImportError:
    from client import ServitorClient


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.servitor = ServitorClient("ServitorClient", "192.168.0.14", 12)
    servitor = app.state.servitor
    # Mic listener is blocking I/O (sr.Microphone) — needs a thread
    threading.Thread(target=servitor.listen, daemon=True).start()
    # Reminder loop is async — runs as a FastAPI background task
    task = asyncio.create_task(servitor.check_reminders_loop())
    yield
    task.cancel()
    servitor.cleanup()


app = FastAPI(lifespan=lifespan)


@app.post("/play_file")
async def create_upload_file(my_file: UploadFile):
    servitor = app.state.servitor
    print(my_file.filename)
    file = await my_file.read()
    print(f"tipo do arquivo {type(file)}")

    processed_audio, sample_rate = await asyncio.to_thread(servitor.process_audio, file)
    await asyncio.to_thread(servitor.play_audio, processed_audio, sample_rate)
    return {"filename": my_file.filename}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="debug")
