import json

from fastapi import FastAPI, UploadFile
from typing import Dict, Any
from server import ServitorServer
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

import uvicorn
app = FastAPI()

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

    async def event_generator():
        async for chunk in Servitor.process_ollama_stream(message):
            yield f"data: {json.dumps({'content': chunk})}\n\n"
        yield f"data: {json.dumps({'done': True})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.post("/file_recorded")
async def create_upload_file(my_file: UploadFile):
    print(my_file.filename)
    file = my_file.file
    audio_bytes = await Servitor.process_audio(file)
    Servitor.send_audio_bytes(audio_bytes)
    return {"filename": my_file}


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, log_level="debug")
