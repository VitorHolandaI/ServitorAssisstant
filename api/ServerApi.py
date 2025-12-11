from fastapi import FastAPI, UploadFile
from server import ServitorServer

import uvicorn
app = FastAPI()

Servitor = ServitorServer("ServitorServer", "192.168.0.22")
#Servitor.initial_agent()


@app.post("/file_recorded")
async def create_upload_file(my_file: UploadFile):
    print(my_file.filename)
    file = my_file.file
    audio_bytes = await Servitor.process_audio(file)
    Servitor.send_audio_bytes(audio_bytes)
    return {"filename": my_file}


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, log_level="debug")
