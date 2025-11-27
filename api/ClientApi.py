from fastapi import FastAPI, UploadFile
from client import ServitorClient
import threading
import uvicorn






app = FastAPI()
Servitor = ServitorClient("ServitorClient", "192.168.0.14", 12)


def listen_to_microphone():
    Servitor.listen()


threading.Thread(target=listen_to_microphone, daemon=True).start()


@app.post("/play_file")
async def create_upload_file(my_file: UploadFile):
    print(my_file.filename)
    file = my_file.file
    print(f"tipo do arquivo {type(file)}")
    file = file.read()

    Servitor.process_audio(file)  # escreve como audio.wav
    Servitor.play_audio()  # pega o audio original e modifica com sox e da play
    return {"filename": my_file}


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, log_level="debug")
