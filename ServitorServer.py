import socket

host = '0.0.0.0'
port = 8080
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.bind((host, port))
sock.listen(1)

connex = sock.accept()
print("Established conn")

data = b''  # data is binary so anythign can be received if its in binary data raw that its

while True:
    # keep reading the data ntil it ends...
    packet = connex[0].recv(4096)
    if not packet:
        break
    data += packet

audio_file = f'audio.wav'

with open(audio_file, 'wb') as fi_o:
    fi_o.write(data)

connex[0].close()
sock.close()
