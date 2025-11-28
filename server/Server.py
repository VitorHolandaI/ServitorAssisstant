import re
from io import BytesIO
import requests
import time
import socket
import pyttsx3
import wave
from piper import SynthesisConfig
from piper import PiperVoice
from ollama import Client
import speech_recognition as sr


class ServitorServer:
    """
    Class Fot the servitor server responsible for calling the llm and processing audio, that is geting what.
    the user said passing that back to the llm getting the response generating audio with that response
    and sending it back.
    """

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
        response = client.chat('llama3.2:1b', keep_alive=0,
                               messages=[
                                   {'role': 'system', 'content': system_prompt},
                                   {'role': 'user', 'content': talk}
                               ])

        responseString = re.sub(r'<think>.*?</think>\n*', '',
                                response.message.content, flags=re.DOTALL)
        return responseString

    def process_audio(self, audio_file):
        """
        Audio to process the audio,it reads the send file from the client.

        then tries to recognize it by using for now, vosk local api
        prob going to change for whisper from openai?, and later
        asks the user query for the llm that responds a text, and this
        text will be transformed back to audio.
        """

        print("Process audio func")
        r = sr.Recognizer()

        with sr.AudioFile(audio_file) as source:
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
        voice = PiperVoice.load(
            "/full_path/voice_models/en_US-ryan-medium.onnx")
        bytes_audio = BytesIO()

        syn_config_1 = SynthesisConfig(
            volume=0.1,
            length_scale=2.0,
            noise_scale=0.5,
            noise_w_scale=1.0,
            normalize_audio=False,
        )

        with wave.open(bytes_audio, "wb") as wav_file:
            voice.synthesize_wav(talk, wav_file, syn_config=syn_config_1)

        audio_bytes = bytes_audio.getvalue()

        return audio_bytes

    def send_audio_bytes(self, audio_bytes):
        """
        Function to send and audio file to the remote or local server..
        This function creates a socket connection to the server to use port 8080
        and connect it will send the audio file .wav to the server to be
        processed there.
        For documenting: the file is read and send over as a binary file,
        a block size of 4096 after sending it it closes the connection.
        """
        url = f"http://{self.client_ip}:8000/play_file"
        byte_file = BytesIO(audio_bytes)

        files = {'my_file': byte_file}

        res = requests.post(url, files=files)

        print(res)

    def send_audio_recorded(self):
        """

        """
        url = f"http://{self.client_ip}:8000/play_file"
        files = {'my_file': open('audio2.wav', 'rb')}
        res = requests.post(url, files=files)
        print(res)

    def send_audio_normal(self, audio_file):
        """

        """
        url = f"http://{self.client_ip}:8000/file_recorded"
        files = {'my_file': open('audio3.wav', 'rb')}
        res = requests.post(url, files=files)
        print(res)
