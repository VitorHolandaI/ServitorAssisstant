from fastapi import FastAPI, UploadFile
from ServitorClient import *
import speech_recognition as sr
import time



import uvicorn
app = FastAPI()

Servitor = ServitorClient("ServitorClient", "192.168.0.17",12)


@app.post("/play_file")
async def create_upload_file(my_file: UploadFile):
    print(my_file.filename)
    file = my_file.file
    Servitor.process_audio_recorded(file)
    Servitor.play_audio()
    return {"filename": my_file}


#a queue with the pendfing audi plays should be good, so if its empyt can lister if not block and play... everything async
#async method listening for stuff
#    Servitor.list
#    while True:
#        ServitorClient.listen(sr)
#        ServitorClient.send_audio()
#        time.sleep(1)
#


#other async methods to run

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, log_level="debug")
