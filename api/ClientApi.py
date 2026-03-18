import asyncio
import threading
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, UploadFile

from client import ServitorClient

Servitor = ServitorClient("ServitorClient", "192.168.0.14", 12)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Mic listener is blocking I/O (sr.Microphone) — needs a thread
    threading.Thread(target=Servitor.listen, daemon=True).start()
    # Reminder loop is async — runs as a FastAPI background task
    task = asyncio.create_task(Servitor.check_reminders_loop())
    yield
    task.cancel()


app = FastAPI(lifespan=lifespan)


@app.post("/play_file")
async def create_upload_file(my_file: UploadFile):
    print(my_file.filename)
    file = my_file.file
    print(f"tipo do arquivo {type(file)}")
    file = file.read()

    processed_audio, sample_rate = Servitor.process_audio(file)
    Servitor.play_audio(processed_audio, sample_rate)
    return {"filename": my_file}


if __name__ == "__main__":
    uvicorn.run("ClientApi:app", host="0.0.0.0", port=8000, log_level="debug")
