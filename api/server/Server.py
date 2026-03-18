
import os
import re
import json
import wave
import sqlite3
import datetime
import requests
from io import BytesIO
from pathlib import Path
from piper import PiperVoice
from dotenv import load_dotenv
import speech_recognition as sr
from piper import SynthesisConfig
from mcp_module.stremable_http.client2 import llm_mcp_client

DB_PATH = Path(__file__).parent.parent.parent / "data" / "tasks.db"

load_dotenv()
voice_path = os.getenv('VOICE_PATH')
print(voice_path)


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
        self.agent = ""
        self.voice = PiperVoice.load(
            "/home/vitor/git/ServitorAssisstant/voice_models/en_US-ryan-medium.onnx")
        self.initial_agent()

    def initial_agent(self):
        self.base_prompt = (
            "You are now a warhammer 40k MAGOs, use the same personality as one, "
            "showing curiosity for science in all manners. Only need short responses. "
            "You are like a magos from a library from the imperium and answer all questions. "
            "When the user asks to create a task with a relative time like 'today', 'tomorrow', "
            "'at 5pm', you MUST use the current date/time provided below to calculate the exact "
            "due_at value in 'YYYY-MM-DD HH:MM:SS' format."
        )

        agent_mcp = llm_mcp_client(
            mcp_addresses=["http://localhost:8000/mcp"],
            model_name="llama3.2:1b",
            model_address="http://127.0.0.1:11434",
            system_prompt=self.base_prompt
        )
        self.agent = agent_mcp

    def get_prompt_with_time(self):
        now = datetime.datetime.now()
        return (
            f"{self.base_prompt}\n\n"
            f"CURRENT DATE AND TIME: {now.strftime('%Y-%m-%d %H:%M:%S')} "
            f"({now.strftime('%A')}), Timezone: America/Recife"
        )

    async def process_ollama(self, talk: str):
        """
        This function right now talks to the local ollama.

        :param talk: a string with query of the user.
        """
        print(f"this was the phrase {talk}")
        # nao tem custom message ainda
        response = await self.agent.get_response(talk, system_prompt=self.get_prompt_with_time())

        # not the best thing here refact refact refact
        responeString = ""
        if response is None:  # HELL THE CHECK OF NONE
            responseString = "Some error Occurred"
        else:
            response = response['messages'][-1].content
            responseString = re.sub(r'<think>.*?</think>\n*', '',
                                    response, flags=re.DOTALL)

        print(f"THIS WAS WHAT THE MODEL RESPONDED {responseString} ")
        return responseString

    async def process_ollama_stream(self, talk: str):
        print(f"this was the phrase (stream) {talk}")
        inside_think = False
        buffer = ""

        async for chunk in self.agent.get_response_stream(talk, system_prompt=self.get_prompt_with_time()):
            buffer += chunk

            while buffer:
                if inside_think:
                    end_idx = buffer.find("</think>")
                    if end_idx != -1:
                        buffer = buffer[end_idx + len("</think>"):]
                        inside_think = False
                        if buffer.startswith("\n"):
                            buffer = buffer[1:]
                    else:
                        break
                else:
                    start_idx = buffer.find("<think>")
                    if start_idx != -1:
                        if start_idx > 0:
                            yield buffer[:start_idx]
                        buffer = buffer[start_idx + len("<think>"):]
                        inside_think = True
                    else:
                        if "<" in buffer and not buffer.endswith(">"):
                            break
                        yield buffer
                        buffer = ""

    def generate_audio(self, text: str) -> bytes:
        """Generate audio bytes from text using Piper TTS."""
        bytes_audio = BytesIO()

        syn_config_1 = SynthesisConfig(
            volume=0.1,
            length_scale=1.0,
            noise_scale=0.5,
            noise_w_scale=1.0,
            normalize_audio=False,
        )

        with wave.open(bytes_audio, "wb") as wav_file:
            self.voice.synthesize_wav(text, wav_file, syn_config=syn_config_1)

        return bytes_audio.getvalue()

    async def process_audio(self, audio_file):
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

        talk = ""
        try:
            raw = r.recognize_vosk(audio)
            print(f"Vosk raw: {raw}")
            parsed = json.loads(raw)
            talk = parsed.get("text", "").strip()
        except sr.UnknownValueError:
            print("Vosk could not understand audio")
            return None
        except sr.RequestError as e:
            print(f"Could not request results from Vosk; {e}")
            return None

        print(f'u said: {talk}')

        if len(talk) < 10 or len(talk.split()) < 3:
            print(f"Short/noise input ({len(talk)} chars, {len(talk.split())} words), skipping")
            return None

        talk = await self.process_ollama(talk)

        return self.generate_audio(talk)

    async def check_due_reminders(self):
        """Check DB for tasks matching current hour:minute and send audio reminder."""
        if not DB_PATH.exists():
            print("[Reminder] No DB found, skipping")
            return []

        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        now = datetime.datetime.now()
        current_time = now.strftime('%Y-%m-%d %H:%M')
        tasks = conn.execute(
            "SELECT * FROM tasks WHERE is_completed = 0 AND due_at IS NOT NULL AND strftime('%Y-%m-%d %H:%M', due_at) = ?",
            (current_time,)
        ).fetchall()
        conn.close()

        if not tasks:
            return []

        reminded = []
        for task in tasks:
            title = task['title']
            desc = task['description'] or ''
            print(f"[Reminder] Task now: {title}")

            prompt = (
                f"ALERT: You must remind the user about a scheduled task that is due RIGHT NOW. "
                f"The task is: '{title}'. "
                f"{'Description: ' + desc + '. ' if desc else ''}"
                f"Give a short urgent reminder in character as a Magos. Keep it under 3 sentences."
            )

            response = await self.process_ollama(prompt)
            audio_bytes = self.generate_audio(response)
            self.send_audio_bytes(audio_bytes)
            reminded.append({"id": task['id'], "title": title})

        return reminded

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

