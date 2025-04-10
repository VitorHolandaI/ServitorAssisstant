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

server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
server_socket.bind(('0.0.0.0', 12345))
server_socket.listen(1)
print("Server listening on port 12345...")
client_socket, client_address = server_socket.accept()
print(f"Connection from {client_address}")

def send_file(file_name, client_socket):
    try:
        with open(file_name, 'rb') as file:
            while chunk := file.read(4096):
                client_socket.sendall(chunk)
        print(f"File '{file_name}' sent successfully.")
        client_socket.close()
    except FileNotFoundError:
        print(f"Error: {file_name} not found.")
        client_socket.sendall(b'Error: File not found.')

def tts(talk):
    print("Now in tts")
    device = "cuda" if torch.cuda.is_available() else "cpu"
    tts = TTS("tts_models/multilingual/multi-dataset/xtts_v2").to(device)

    tts.tts_to_file(
      text=talk,
      speaker="Craig Gutsy",
      language="en",
      file_path="./audio.wav"
    )

    return 0


#send the audio file 
while True:
    client_message = client_socket.recv(1024).decode('utf-8')
    file_name = 'audio.wav'
    tts(client_message)
    send_file(file_name, client_socket)
    time.sleep(1)
    client_socket, client_address = server_socket.accept()
    
