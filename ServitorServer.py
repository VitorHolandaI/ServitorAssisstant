import socket
import time
import speech_recognition as sr
from TTS.api import TTS
import re
from ollama import Client
import torch


class ServitorServer:

    def __init__(self, name, client_ip):
        """
        Initializer function takes the name and the ip adress
        from the client that plays the audios
        :param name str: the name of the servitor
        :param client_ip str: ip adress of the client
        """
        self.name = name
        self.client_ip = client_ip

    def process_ollama(self, talk: str):
        """
        This function right now talks to the local ollama.

        :param talk: a string with query of the user.
        """
        print(f"this was the phrase {talk}")
        client = Client(
            host='http://127.0.0.1:11434',
            headers={'x-some-header': 'some-value'}
        )
        system_prompt = "You are now a warhammer 40k MAGOs,use the same persolnality as one" +\
            "showing " +\
            "curiosity for cience in all manners ,also only need short reponses," +\
            " you are like a magos from " + \
            "a library from teh imperium and answeer all questioes "
        response = client.chat('llama3.2:3b',
                               messages=[
                                   {'role': 'system', 'content': system_prompt},
                                   {'role': 'user', 'content': talk}
                               ])

        responseString = re.sub(r'<think>.*?</think>\n*', '',
                                response.message.content, flags=re.DOTALL)
        return responseString

    def process_audio(self):
        """
        Audio to process the audio,it reads the send file from the client
        then tries to recognize it by using for now, vosk local api
        prob going to change for whisper from openai?, and later
        asks the user query for the llm that responds a text, and this
        text will be transformed back to audio.
        """

        print("Process audio func")
        AUDIO_FILE = "audio.wav"  # or .flac, .aiff, etc.
        r = sr.Recognizer()

        with sr.AudioFile(AUDIO_FILE) as source:
            audio = r.record(source)

        try:
            talk = r.recognize_vosk(audio)
            print("Process audi vosk")
        except sr.UnknownValueError:
            print("Vosk could not understand audio")
        except sr.RequestError as e:
            print(f"Could not request results from Vosk; {e}")

        print(f'u said{talk}')
        talk = self.process_ollama(talk)

        print("Now in tts")
        device = "cuda" if torch.cuda.is_available() else "cpu"
        tts = TTS("tts_models/multilingual/multi-dataset/xtts_v2").to(device)

        tts.tts_to_file(
            text=talk,
            speaker="Craig Gutsy",
            language="en",
            file_path="./audio2.wav"
        )

        return 0

    def send_audio(self):
        """
        Funcion to send the processed audio over the client that will play it
        opens a socker connection to the client and port 8080, and then closes
        the conection.

        """
        client_ip = self.client_ip
        host = client_ip
        port = 8080
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect((host, port))
        filename = 'audio2.wav'
        try:
            with open(filename, 'rb') as fi:  # Open the file in binary mode
                data = fi.read(4096)  # Read data in chunks of 4KB
                while data:
                    sock.send(data)
                    data = fi.read(4096)
                fi.close()
        except IOError:
            print('You entered an invalid filename! Please enter a valid name')

        sock.close()
        print(f'Successfully sent {filename}')

    def receive_audio(self):
        """
        Function to receive the first audio file to identify the user query.
        keps listening on port 8080 receive write it out then closes the socket
        """
        host = "0.0.0.0"
        port = 8080
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind((host, port))
        sock.listen(1)

        connex = sock.accept()
        print("Established conn")

        data = b''  # data is binary so anythign can be received if its in binary data raw that its

        while True:
            # keep reading the data ntil it ends...
            packet = connex[0].recv(4096)
            if not packet:
                break
            data += packet

        audio_file = f'audio.wav'

        with open(audio_file, 'wb') as fi_o:
            fi_o.write(data)
        connex[0].close()
        sock.close()
        print("Wrote audio clossing socket")


servitorServer = ServitorServer("Serveruno", "192.168.0.11")


while True:
    servitorServer.receive_audio()
    servitorServer.process_audio()
    servitorServer.send_audio()
    time.sleep(1)
