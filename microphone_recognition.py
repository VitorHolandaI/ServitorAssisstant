#!/usr/bin/env python3


import os
import time
import speech_recognition as sr
r = sr.Recognizer()

while(1):
    with sr.Microphone() as source:
        print("Say something!")
        audio = r.listen(source)

    try:
        phrase = r.recognize_google(audio)
        print("Google Speech Recognition thinks you said " + phrase)
        if ("guardian" in phrase):
            print("Has guardian inside")
    except sr.UnknownValueError:
        print("Google Speech Recognition could not understand audio")
    except sr.RequestError as e:
        print("Could not request results from Google Speech Recognition service; {0}".format(e))

    time.sleep(1)
