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
import socket
r = sr.Recognizer()



def receive_file(file_name, client_socket):
    with open(file_name, 'wb') as file:
        while True:
            data = client_socket.recv(4096)
            if not data:
                break
            file.write(data)

    print(f"File '{file_name}' received successfully.")
    #client_socket.close()

def tts(talk):
    client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    client_socket.connect(('172.22.168.86', 12345))
    client_socket.sendall(talk.encode('utf-8'))
    receive_file('audio.wav', client_socket)
    time.sleep(2)


    try:
        command = 'sox audio.wav robot_voice.wav overdrive 50 gain -n -10.0 gain -3.0 reverb 50 30 60 10 0 0'

#        command = 'sox audio.wav robot_voice.wav overdrive 10 echo 0.8 0.88 60 0.4 chorus 0.7 0.9 55 0.4 0.25 2 -s'
        subprocess.Popen(command, shell=True, executable="/bin/bash")
        print(f"Convert worked")
    except subprocess.CalledProcessError as e:
        print(f"Error running TTS: {e}")

    time.sleep(1)
    playsound('robot_voice.wav')
    os.remove('audio.wav')

def stt():

    while(1):
        with sr.Microphone() as source:
            print("Say something!")
            audio = r.listen(source)
        try:
            phrase = r.recognize_google(audio)
            print("Google Speech Recognition thinks you said " + phrase)
            if ("Migos" in phrase or "migos" in phrase):
                phrase = phrase.replace('Migos', '')
                result = ollama_talker(phrase)
                model_name="tts_models/multilingual/multi-dataset/xtts_v2",
                tts(result)
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
            host='http://172.22.168.86:11434',
            headers={'x-some-header': 'some-value'}
)
    system_prompt = "You are now a warhammer 40k MAGOs, use the same persolnality as one showing curiosity for cience in all manners ,also only need short reponses, you are like a magos from a library from teh imperium and answeer all questioes "
    response = client.chat('llama3.2:3b', 
                messages=[
                    {'role': 'system', 'content': system_prompt},
                    {'role': 'user', 'content': phrase}
                ])

    responseString = re.sub(r'<think>.*?</think>\n*','',response.message.content,flags=re.DOTALL)
    print(responseString)
    return responseString

stt()
#ollama_talker("what is the mars forge?")
