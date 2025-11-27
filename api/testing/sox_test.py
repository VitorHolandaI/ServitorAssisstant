import soundfile as sf
import numpy as np
import sounddevice
import sox
# sample rate in Hz
sample_rate = 44100
# generate a 1-second sine tone at 440 Hz
y = np.sin(2 * np.pi * 440.0 * np.arange(sample_rate * 1.0) / sample_rate)
# create a transformer
tfm = sox.Transformer()
# shift the pitch up by 2 semitones
tfm.pitch(2)
# transform an in-memory array and return an array
y_out = tfm.build_array(input_array=y, sample_rate_in=sample_rate)

#No lugar de url open bota os bytes de antes!!!
#data, samplerate = sf.read(io.BytesIO(urlopen(url).read()))
#data vai ser um array numpy que pode ento ser modificado pelo wrapper do sox
#com o sample rante dado

# transform an in-memory array and return an arra:wq
y
y_out = tfm.build_array(input_array=y, sample_rate_in=sample_rate)


sounddevice.play(y_out,sample_rate)



# create an output file with a different sample rate
#SAO OS EFEITOS QUE DEVEM SER APLICADOS


tfm = sox.Transformer()
tfm.overdrive(50)
tfm.gain(gain_db=-10.0, normalized=True)
tfm.gain(gain_db=-3.0)
tfm.reverb(reverberance=50, hf_damping=30, room_scale=60, pre_delay=10)
#
No server deve-se ler os bytes de chegada processar com o whisper, passar pelo llm transformar em audio o texto como bytes criar arquivo como fileObjectIO mandar de volta para o cliente

O Client por sua vez pega o audio transforma em numpy array faz o tratamento e da play no numpy

