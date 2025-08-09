# Servitor Helper Assistant

## Overview
This project uses a combination of natural language processing (NLP) and text-to-speech (TTS) technologies to create a voice assistant. The system listens to your voice, processes the input using an LLM provided by Ollama, and generates a response using Pyttsx3. The audio output is then sent to a remote client, in this case a Raspberry Pi.

## Functionality
1. **Voice Input**: The system listens for voice commands using a speech recognition library (currently Vosk with Python).
2. **Processing**: The input voice is processed using an LLM provided by Ollama.
3. **Response Generation**: The processed input is used to generate a response, which is then converted to audio using Pyttsx3.
4. **Audio Output**: The generated audio file is sent to the remote client (Raspberry Pi).
5. **Client-Side Processing**: On the Raspberry Pi, the audio file is further processed using SoX command and played using a media player.

## Technical Details
* Programming Language: Python
* Speech Recognition Library: Vosk
* Text-to-Speech Library: Pyttsx3
* LLM Provider: Ollama
* Client Device: Raspberry Pi
* Audio Processing Tool: SoX


Well the llamma wrote this up here, and i did some editing it does a good job.
