from fastapi import FastAPI, UploadFile
from ServitorServer import *
import speech_recognition as sr

import uvicorn
app = FastAPI()

Servitor = ServitorServer("ServitorServer", "192.168.0.22")


@app.post("/file_recorded")
async def create_upload_file(my_file: UploadFile):
    print(my_file.filename)
    file = my_file.file
    Servitor.process_audio(file)
    Servitor.send_audio_recorded()
    return {"filename": my_file}


#other async methods to run

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, log_level="debug")
