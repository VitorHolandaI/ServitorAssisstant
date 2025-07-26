import requests

url = "http://127.0.0.1:8000/file_recorded"
files = {'my_file': open('robot_voice.wav', 'rb')}
res = requests.post(url, files=files)
