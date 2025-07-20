#!/usr/bin/env python3
import socket
import speech_recognition as sr
import RPi.GPIO as GPIO
import time
import os
from playsound3 import playsound
import subprocess


class ServitorClient:
    recognizer = None
    name = "default-name"
    server: str = "ip"
    pwm: GPIO.PWM = None

    def __init__(self, name, server_ip, gpio_number):
        """
        Constructor function initializing the Servitor Client class

        :param name str: the name of the client
        :param server_ip str: the servitor server that hosts the llms etc
        """
        self.name = name
        self.server = server_ip
        self.recognizer = sr.Recognizer()
        self.set_led_pin(gpio_number)

    def led_on_low(self):
        """
        Sets led pwm to low light
        """
        self.pwm.start(10)

    def led_off(self):
        """
        Sets led pwm to low light
        """
        self.pwm.start(0)

    def led_on_high(self):
        """
        Sets led pwm to High light
        """
        self.pwm.start(100)

    def set_led_pin(self, pin):
        """
        Set led pin mode and creates the pwm.
        to be used.
        """
        if self.pwm is None:
            print("ITS NONE")
            GPIO.cleanup()
            GPIO.setmode(GPIO.BCM)
            GPIO.setup(pin, GPIO.OUT)
            self.pwm = GPIO.PWM(pin, 1000)

    def process_audio(self, audio):
        """
        function to call process audio functions

        :param audio str: the audio name file
        """
        audio_name = 'audio.wav'
        with open(audio_name, 'wb') as f:
            f.write(audio)

        self.send_audio()

    def send_audio(self):
        """
        Function to send and audio file to the remote or local server..
        This function creates a socket connection to the server to use port 8080
        and connect it will send the audio file .wav to the server to be
        processed there.
        For documenting: the file is read and send over as a binary file,
        a block size of 4096 after sending it it closes the connection.
        """
        try:
            host = self.server
            port = 8080
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.connect((host, port))
            filename = 'audio.wav'

        except Exception as e:
            print(f"[ERROR] {e}")
            start_fuc()

#        host = self.server
#        port = 8080
#        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
#        sock.connect((host, port))
#        filename = 'audio.wav'

        try:
            with open(filename, 'rb') as fi:  # Open the file in binary mode
                data = fi.read(4096)  # Read data in chunks of 4KB
                while data:
                    sock.send(data)
                    data = fi.read(4096)
                print("All sent")
                fi.close()
        except IOError:
            print('You entered an invalid filename! Please enter a valid name')

        print(f'Successfully sent {filename}')
        sock.close()

    def receive_audio(self):
        """
        This function receives a audio file back after sending it to processing it
        on the remote server, this creates a socket listening on all interfaces and on
        port 8080 will receve the file and write it as audio1 that later will be used
        to be transformed and played
        """
        host = '0.0.0.0'
        port = 8080
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind((host, port))
        sock.listen(1)

        connex = sock.accept()
        print("Receiving audio file to play")
        data = b''
        while True:
            packet = connex[0].recv(4096)
            if not packet:
                break
            data += packet

        audio_file = f'audio1.wav'

        with open(audio_file, 'wb') as fi_o:
            fi_o.write(data)
        connex[0].close()
        sock.close()
        # self.play_audio()

    def play_audio(self):
        """
        Function to play the specific audio file.
        """
        try:
            command = 'sox audio1.wav robot_voice.wav' + \
                ' overdrive 50 gain -n -10.0 gain -3.0 reverb 50 30 60 10 0 0'
            subprocess.Popen(command, shell=True, executable="/bin/bash")
            print("Convert worked")
        except subprocess.CalledProcessError as e:
            print(f"Error running convert of audio {e}")
        time.sleep(1)
        playsound('robot_voice.wav')
        os.remove('audio.wav')

    def listen(self, sr):
        """
        Function for the agent to lister to the audio input
        from the microphone

        :param sr speech_recognition module: used to listen to the microphone
        """
        with sr.Microphone() as source:
            print("Speak !")
            self.led_on_low()
            audio = self.recognizer.listen(source, phrase_time_limit=10)
            wav_data = audio.get_wav_data()
            self.led_off()
            self.process_audio(wav_data)


servitor_uno = ServitorClient("Servitor1", "192.168.0.17", 12)


def start_fuc():
    """Start function."""
    print("on func")
    while True:
        servitor_uno.listen(sr)
        print("on func2")
        servitor_uno.receive_audio()
        servitor_uno.play_audio()
        time.sleep(1)


start_fuc()

