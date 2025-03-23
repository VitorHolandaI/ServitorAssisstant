#!/usr/bin/env python3


import os
import re
import ollama
from ollama import Client
from playsound import playsound
import time
import torch
from TTS.api import TTS
import subprocess
import speech_recognition as sr
r = sr.Recognizer()

def tts(talk,model_name,output_path):
    print("Now in tts")
    device = "cuda" if torch.cuda.is_available() else "cpu"
    tts = TTS("tts_models/multilingual/multi-dataset/xtts_v2").to(device)

    tts.tts_to_file(
      text=talk,
      speaker="Craig Gutsy",
      language="en",
      file_path="./audio.wav"
    )


    try:
        command = 'sox audio.wav robot_voice.wav overdrive 10 echo 0.8 0.88 60 0.4 chorus 0.7 0.9 55 0.4 0.25 2 -s'
        subprocess.Popen(command, shell=True, executable="/bin/bash")
        print(f"Convert worked")
    except subprocess.CalledProcessError as e:
        print(f"Error running TTS: {e}")


    playsound('./robot_voice.wav')
    os.remove('./audio.wav')
    print("now exiting")



def stt():
    while(1):
        with sr.Microphone() as source:
            print("Say something!")
            audio = r.listen(source)
        try:
            phrase = r.recognize_google(audio)
            print("Google Speech Recognition thinks you said " + phrase)
            if ("Migos" in phrase or "migos" in phrase):
                result = ollama_talker(phrase)
                model_name="tts_models/multilingual/multi-dataset/xtts_v2",
                tts(result,model_name,"./audio.wav")
                time.sleep(1)

        except sr.UnknownValueError:
            print("Google Speech Recognition could not understand audio")
        except sr.RequestError as e:
            print("Could not request results from Google Speech Recognition service; {0}".format(e))
        time.sleep(1)


def ollama_talker(phrase):
    print("this was the phrase ")
    print(phrase)
    client = Client(
            host='http://192.168.0.12:11434',
            headers={'x-some-header': 'some-value'}
)
    system_prompt = "You are now a warhammer 40k MAGOs, use the same persolnality as one showing curiosity for cience in all manners ,also only need short reponses"
    response = client.chat('deepseek-r1:14b', 
                messages=[
                    {'role': 'system', 'content': system_prompt},
                    {'role': 'user', 'content': phrase}
                ])

    responseString = re.sub(r'<think>.*?</think>\n*','',response.message.content,flags=re.DOTALL)
    print(responseString)
    return responseString

stt()
#ollama_talker("what is the mars forge?")
