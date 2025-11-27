#!/usr/bin/env python3
import requests
import socket
import speech_recognition as sr
import RPi.GPIO as GPIO
import time
import os
from playsound3 import playsound
import subprocess
from io import BytesIO


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
        #fazer lazy loading do processamento etc para dps dar play
        """
        function to call process audio functions

        :param audio str: the audio name file
        """
        audio_name = 'audio.wav'
        with open(audio_name, 'wb') as f:
            f.write(audio)

    def process_audio2(self, audio):
        """
        function to call process audio functions

        :param audio str: the audio name file
        """
        audio_name = 'audio2.wav'
        with open(audio_name, 'wb') as f:
            f.write(audio)

    def send_audio(self):
        """
        Function to send and audio file to the remote or local server..
        This function creates a socket connection to the server to use port 8080
        and connect it will send the audio file .wav to the server to be
        processed there.
        For documenting: the file is read and send over as a binary file,
        a block size of 4096 after sending it it closes the connection.
        """
        url = f"http://{self.server}:8000/file_recorded"
        files = {'my_file': open('audio2.wav', 'rb')}
        res = requests.post(url, files=files)
        print(res)

    def send_audio_bytes(self, audio_bytes):
        """
        Function to send and audio file to the remote or local server..
        This function creates a socket connection to the server to use port 8080
        and connect it will send the audio file .wav to the server to be
        processed there.
        For documenting: the file is read and send over as a binary file,
        a block size of 4096 after sending it it closes the connection.
        """
        url = f"http://{self.server}:8000/file_recorded"
        byte_file = BytesIO(audio_bytes)

        files = {'my_file': byte_file}

        res = requests.post(url, files=files)
        print(res)

    def play_audio(self):
        """
        Function to play the specific audio file.
        """
        try:
            command = 'sox audio.wav robot_voice.wav' + \
                ' overdrive 50 gain -n -10.0 gain -3.0 reverb 50 30 60 10 0 0'
            subprocess.Popen(command, shell=True, executable="/bin/bash")
            print("Convert worked")
        except subprocess.CalledProcessError as e:
            print(f"Error running convert of audio {e}")
        time.sleep(1)
        playsound('robot_voice.wav')
        os.remove('audio.wav')

    def listen(self):
        """
        Function for the agent to lister to the audio input
        from the microphone

        :param sr speech_recognition module: used to listen to the microphone
        """
        while True:
            with sr.Microphone(device_index=2) as source:
                print("Adjusting To noise")
                self.recognizer.adjust_for_ambient_noise(source)
                self.led_on_low()
                audio = self.recognizer.listen(source, phrase_time_limit=10)
                print("Speak2!")
                wav_data = audio.get_wav_data()
                print("Speak3!")
                self.led_off()
                # its aredy bytes mf self.process_audio2(wav_data)
                print("Speak4!")
                self.send_audio_bytes(wav_data)
                print("Speak5!")
                time.sleep(10)
