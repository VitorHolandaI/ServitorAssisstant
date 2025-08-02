import requests

url = "http://192.168.0.17:8000/file_recorded"
files = {'my_file': open('robot_voice.wav', 'rb')}
res = requests.post(url, files=files)
