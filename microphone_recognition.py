#!/usr/bin/env python3


import os
import re
import ollama
from ollama import Client
#from playsound import playsound
import time
#import torch
#from TTS.api import TTS

#import speech_recognition as sr
#r = sr.Recognizer()
#
def tts(speak):
    device = "cuda" if torch.cuda.is_available() else "cpu"
    tts = TTS("tts_models/multilingual/multi-dataset/xtts_v2").to(device)
    wav = tts.tts(text=speak, speaker_wav=",/audio.wav", language="en")
    playsound('./audio.wav')
    os.remove('./audio.wav')

def sst():
    while(1):
        with sr.Microphone() as source:
            print("Say something!")
            audio = r.listen(source)
        try:
            phrase = r.recognize_google(audio)
            print("Google Speech Recognition thinks you said " + phrase)
            if ("Magos" in phrase or "mago" in phrase):
                result = ollama_talker(phrase)
                tts(result)
                time.sleep(1000)

        except sr.UnknownValueError:
            print("Google Speech Recognition could not understand audio")
        except sr.RequestError as e:
            print("Could not request results from Google Speech Recognition service; {0}".format(e))
        time.sleep(1)


def ollama_talker(phrase):
    client = Client(
            host='http://192.168.0.12:11434',
            headers={'x-some-header': 'some-value'}
)
    system_prompt = "You are now a warhammer 40k MAGOs, use the same persolnality as one showing curiosity for cience in all manners"
    response = client.chat('deepseek-r1:14b', 
                messages=[
                    {'role': 'system', 'content': system_prompt},
                    {'role': 'user', 'content': phrase}
                ])

    responseString = re.sub(r'<think>.*?</think>\n*','',response.message.content,flags=re.DOTALL)
    return responseString

