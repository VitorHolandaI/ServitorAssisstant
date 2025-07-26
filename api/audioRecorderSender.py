import io
import socket
import requests
import speech_recognition as sr

r = sr.Recognizer()

with sr.Microphone() as source:
    print("Say something!")
    audio = r.listen(source)

with open("audio.wav", "wb") as f:
    f.write(audio.get_wav_data())



host = '192.168.0.7'
port = 8080
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.connect((host, port))

filename = 'audio.wav'
try:
    with open(filename, 'rb') as fi:  # Open the file in binary mode
        data = fi.read(4096)  # Read data in chunks of 4KB
        while data:
            sock.send(data)
            data = fi.read(4096)
        fi.close()
except IOError:
    print('You entered an invalid filename! Please enter a valid name')

print(f'Successfully sent {filename}')
