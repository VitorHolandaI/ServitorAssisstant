#!/usr/bin/env python3
import asyncio
import threading
import time
from io import BytesIO

import requests
import sounddevice
import soundfile as sf
import sox
import RPi.GPIO as GPIO
import speech_recognition as sr


class ServitorClient:
    recognizer = None
    name = "default-name"
    server: str = "ip"

    def __init__(self, name, server_ip, gpio_number):
        """
        Constructor function initializing the Servitor Client class

        :param name str: the name of the client
        :param server_ip str: the servitor server that hosts the llms etc
        """
        self.name = name
        self.server = server_ip
        self.recognizer = sr.Recognizer()
        self.pwm = None
        self.led_pin = gpio_number
        self._playback_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._mic_pause_until = 0.0
        self._playback_generation = 0
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
            GPIO.setwarnings(False)
            if GPIO.getmode() != GPIO.BCM:
                GPIO.setmode(GPIO.BCM)
            GPIO.setup(pin, GPIO.OUT)
            self.pwm = GPIO.PWM(pin, 1000)

    def cleanup(self):
        self._stop_event.set()
        if self.pwm is not None:
            try:
                self.pwm.stop()
            except Exception:
                pass
            self.pwm = None
        GPIO.cleanup()

    def _mark_playback_active(self, cooldown_seconds: float) -> None:
        with self._playback_lock:
            self._playback_generation += 1
            self._mic_pause_until = max(
                self._mic_pause_until,
                time.monotonic() + cooldown_seconds,
            )

    def _mark_playback_finished(self, cooldown_seconds: float) -> None:
        with self._playback_lock:
            self._playback_generation += 1
            self._mic_pause_until = max(
                self._mic_pause_until,
                time.monotonic() + cooldown_seconds,
            )

    def _snapshot_playback_state(self) -> tuple[int, float]:
        with self._playback_lock:
            return self._playback_generation, self._mic_pause_until

    def _speaker_recently_active(self) -> bool:
        _, pause_until = self._snapshot_playback_state()
        return time.monotonic() < pause_until

    def _wait_until_listen_allowed(self) -> None:
        while not self._stop_event.is_set():
            _, pause_until = self._snapshot_playback_state()
            remaining = pause_until - time.monotonic()
            if remaining <= 0:
                return
            time.sleep(min(remaining, 0.25))

    def _capture_microphone_audio(self) -> bytes | None:
        """
        Capture one utterance from the microphone when the speaker is idle.

        Example:
            wav_data = client._capture_microphone_audio()
        """
        self._wait_until_listen_allowed()
        if self._stop_event.is_set():
            return None

        playback_generation, _ = self._snapshot_playback_state()
        with sr.Microphone(device_index=1) as source:
            print("Adjusting To noise")
            self.recognizer.adjust_for_ambient_noise(source, duration=4)
            self.led_on_low()
            try:
                audio = self.recognizer.listen(
                    source,
                    timeout=1,
                    phrase_time_limit=10,
                )
            finally:
                self.led_off()

        current_generation, _ = self._snapshot_playback_state()
        if current_generation != playback_generation or self._speaker_recently_active():
            print("[Listen] Discarding mic capture because speaker playback was active.")
            return None

        return audio.get_wav_data()

    def process_audio(self, audio):
        # audio em bytes
        # fazer lazy loading do processamento etc para dps dar play
        # audio em bytes traduzindo em np array modificado e retornado para dar play
        """
        function to call process audio functions

        :param audio str: the audio name file
        """

        data, samplerate = sf.read(BytesIO(audio))
        # Use sox to process the audio
        tfm = sox.Transformer()
        tfm.overdrive(50)
        tfm.gain(gain_db=-10.0, normalize=True)
        tfm.gain(gain_db=-3.0)
        tfm.reverb(reverberance=50, high_freq_damping=30,
                   room_scale=60, pre_delay=10)

        audio_numpy_array = tfm.build_array(
            input_array=data, sample_rate_in=samplerate)

        return (audio_numpy_array, samplerate)

    def play_audio(self, processed_audio, sample_rate):
        """
        Function to play the specific audio file.
        """
        cooldown_seconds = 1.5
        self._mark_playback_active(cooldown_seconds)
        try:
            sounddevice.play(processed_audio, sample_rate)
            sounddevice.wait()
        except Exception as e:
            print(f"Error running play of audio {e}")
        finally:
            self._mark_playback_finished(cooldown_seconds)

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
        url = f"http://{self.server}:8001/file_recorded"
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
        url = f"http://{self.server}:8001/file_recorded"
        byte_file = BytesIO(audio_bytes)
        files = {'my_file': byte_file}

        try:
            res = requests.post(url, files=files, timeout=30)
            res.raise_for_status()
            print(res)
        except Exception as e:
            print(f"[SendAudio] Failed to send audio: {e}")

    async def check_reminders_loop(self):
        """Background async loop that pings the server every 60s to check for due tasks."""
        import asyncio
        while True:
            try:
                url = f"http://{self.server}:8001/check_reminders"
                res = await asyncio.to_thread(requests.get, url, timeout=30)
                res.raise_for_status()
                data = res.json()
                reminded = data.get("reminded", [])
                if reminded:
                    print(f"[Reminder] Server sent reminders for: {[t['title'] for t in reminded]}")
            except Exception as e:
                print(f"[Reminder] Error checking reminders: {e}")
            await asyncio.sleep(60)

    def listen(self):
        """
        Function for the agent to lister to the audio input
        from the microphone

        :param sr speech_recognition module: used to listen to the microphone
        """
        while not self._stop_event.is_set():
            try:
                wav_data = self._capture_microphone_audio()
                if wav_data is None:
                    continue
                print("Speak4!")
                self.send_audio_bytes(wav_data)
                print("Speak5!")
                time.sleep(10)
            except sr.WaitTimeoutError:
                continue
            except Exception as e:
                print(f"[Listen] Error: {e}, retrying in 5s...")
                time.sleep(5)
