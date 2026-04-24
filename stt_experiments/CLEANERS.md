# Cleaners — referência técnica

Cada cleaner recebe `in_wav`, escreve `out_wav` (mono s16le). Todos operam
em `float32` internamente via `cleaners/_io.py`. Ordem de aplicação na
chain importa: filtros lineares comutam entre si, não-lineares (gate,
normalize, trim) não.

Unidades usadas abaixo:
- **dBFS** — dB relativo ao fullscale (0 dBFS = pico máximo do int16).
- **Nyquist** — metade da taxa de amostragem (sr/2). Filtros só podem
  cortar abaixo dela.
- **STFT** — Short-Time Fourier Transform: corta o sinal em janelas
  curtas sobrepostas e aplica FFT em cada uma. Saída é matriz
  `[frames, freqs]` com magnitude + fase.

---

## passthrough

Copia o áudio re-codificando para mono s16le. Nenhum DSP. Serve de
**baseline** para comparar espectros antes/depois.

**No espectro:** idêntico ao original (só muda formato de arquivo).

---

## highpass — passa-altas Butterworth 80 Hz

```python
sos = butter(N=4, Wn=80.0, btype="highpass", fs=sr, output="sos")
sosfiltfilt(sos, data)
```

- **O que faz:** atenua tudo abaixo de 80 Hz.
- **Ordem 4** = rolloff de 24 dB/oitava. Em 40 Hz já derrubou ~24 dB.
- **`sosfiltfilt`** = filtragem zero-phase (aplica forward + backward).
  Não distorce fase, mas *duplica* a ordem efetiva (ordem 8 real).
- **Matar o quê:** hum de rede (50/60 Hz e harmônicos baixos), rumble
  de mesa, pop de microfone, vibração estrutural.
- **Por que 80 Hz:** fundamental da voz masculina começa ~85 Hz,
  feminina ~165 Hz. Corte em 80 preserva voz inteira.

**No espectro (matplotlib):** olhar faixa 0-150 Hz. Antes: energia
concentrada em 50/60 Hz. Depois: vale profundo abaixo de 80 Hz, voz
intacta acima.

---

## bandpass_voice — passa-faixa 300–3400 Hz

```python
butter(N=4, Wn=[300, 3400], btype="bandpass", fs=sr, output="sos")
```

- **O que faz:** mantém só 300–3400 Hz (banda telefônica G.711).
- **Matar o quê:** hum grave + chiado agudo (hiss > 3.4 kHz) + qualquer
  ruído de alta frequência (ventoinha, interferência USB switching).
- **Custo:** remove formantes altas (F3, F4 costumam estar em 2.5–4
  kHz) e fricativas (/s/, /f/, /sh/ têm energia em 4–8 kHz). Voz fica
  "telefônica".
- **Quando usar:** ambiente muito ruidoso onde perder inteligibilidade
  alta é aceitável.

**No espectro:** retângulo de energia entre 300 e 3400 Hz, zero fora.
Fricativas "somem" comparadas ao original.

---

## preemphasis — filtro de primeira ordem

```python
y[n] = x[n] - 0.97 * x[n-1]
```

- **O que faz:** realça agudos proporcionalmente (+6 dB/oitava acima de
  ~1.6 kHz).
- **Por que existe:** voz humana cai ~-6 dB/oitava naturalmente
  (espectro "rosa"). Preemphasis compensa, deixa espectro mais plano
  — MFCCs e features ASR funcionam melhor em sinal plano.
- **Padrão em ASR:** Kaldi, HTK, Vosk internamente já fazem isso em
  parte. Aplicar externamente pode dobrar o efeito.
- **Custo:** amplifica hiss junto.

**No espectro:** curva geral inclina pra cima. Agudos mais visíveis,
graves proporcionalmente menores.

---

## trim_silence — corte de silêncio nas bordas

```python
librosa.effects.trim(data, top_db=30.0)
```

- **O que faz:** remove silêncio no início e fim até encontrar amostra
  com energia > (pico − 30 dB).
- **Não toca** silêncio no meio da fala.
- **Por que:** Vosk às vezes alucina palavras curtas em padding
  silencioso (sobretudo noise residual). Encurtar = menos alucinação.
- **Cuidado:** se o `spectral_gate` roda depois, ele usa os primeiros
  0.3 s como perfil de ruído — se você já tirou o silêncio, o perfil
  vira voz. **Ordem correta: spectral_gate ANTES de trim_silence.**

**No espectro:** waveform mais curto, bordas "apertadas" no início da
fala.

---

## spectral_gate — subtração espectral

Implementação em `cleaners/spectral_gate.py`. Passos:

1. **Estima ruído:** primeiros 0.3 s assumidos como silêncio. Calcula
   STFT (janela 1024, hop 256, Hanning), magnitude média por bin de
   frequência → `noise_mag`.
2. **STFT do sinal completo:** magnitude + fase.
3. **Subtração:** `cleaned_mag = max(mag - 1.5 * noise_mag, 0.02 * mag)`
   - `1.5` = fator de oversubtração (mais agressivo que subtração pura).
   - `0.02 * mag` = floor espectral. Evita "musical noise" (bips
     chirpy) que aparece quando zera bins completamente.
4. **iSTFT:** reconstrói sinal com magnitude limpa + fase original.

- **Matar o quê:** hiss branco estacionário (self-noise do mic), zumbido
  constante de ventoinha, ruído de fundo fixo.
- **Não mata:** ruídos transientes (batidas, cliques, fala de terceiros)
  — esses não estão no perfil.
- **Pré-requisito crítico:** primeiros 0.3 s DEVEM ser silêncio (sem
  voz). Se tiver voz ali, o cleaner subtrai voz em vez de ruído.
- **Custo de RAM:** STFT aloca array `[n_frames, 513]` complex64. Em
  áudio longo (minutos) pode ser dezenas de MB.

**No espectro:** compara bin-a-bin. Bins estacionários (ruído de fundo)
caem ~−20 a −30 dB. Bins com voz praticamente intactos. Pode aparecer
floor irregular (musical noise) se oversubtração for agressiva demais.

---

## normalize — peak normalize para −1 dBFS

```python
target = 10 ** (-1/20)  # ≈ 0.891
data *= target / max(abs(data))
```

- **O que faz:** escala o sinal inteiro para que o maior pico fique em
  −1 dBFS.
- **Não altera dinâmica** (diferente de compressor/AGC). Apenas multiplica
  por uma constante.
- **Por que último na chain:** filtros podem reduzir amplitude
  (highpass tira energia grave, gate atenua). Normalize no fim garante
  sinal audível sem clipar.
- **Headroom de 1 dB** (não −0 dBFS): evita clipping em re-encoding ou
  downstream.

**No espectro:** formato idêntico, amplitude geral sobe até pico tocar
−1 dBFS. Waveform parece "mais gordo".

---

# Chain `chain_denoise`

Ordem: `highpass > spectral_gate > normalize`.

1. `highpass` tira hum e rumble primeiro → perfil de ruído que o gate
   vai estimar fica só com hiss real, não contaminado por 60 Hz
   gigante.
2. `spectral_gate` ataca o hiss estacionário usando os 0.3 s iniciais.
3. `normalize` recupera volume perdido e garante pico em −1 dBFS.

Motivo da ordem: filtros lineares (highpass) antes de operações
estatísticas (gate) produzem perfil de ruído mais limpo. Normalize
sempre por último pra não amplificar ruído que o gate ia matar.

---

# Plotando com matplotlib

Template mínimo pra comparar espectros:

```python
import matplotlib.pyplot as plt
import numpy as np
from stt_experiments.cleaners._io import load_mono

def plot_spectrogram(wav_path, title, ax):
    data, sr = load_mono(wav_path)
    spec, freqs, times, _ = ax.specgram(
        data, NFFT=1024, Fs=sr, noverlap=512,
        scale="dB", cmap="magma", vmin=-100, vmax=-20,
    )
    ax.set_title(title)
    ax.set_ylabel("Hz")
    ax.set_xlabel("s")
    ax.set_ylim(0, 8000)

fig, axes = plt.subplots(4, 1, figsize=(10, 12), sharex=True)
plot_spectrogram("samples/rec.wav",                  "raw",          axes[0])
plot_spectrogram("samples/rec__highpass.wav",        "highpass",     axes[1])
plot_spectrogram("samples/rec__spectral_gate.wav",   "spectral_gate",axes[2])
plot_spectrogram("samples/rec__chain_denoise.wav",   "chain_denoise",axes[3])
plt.tight_layout()
plt.savefig("/tmp/compare.png", dpi=120)
```

**O que observar:**
- Linhas horizontais em 50/60/120 Hz → hum. Somem com highpass.
- Névoa uniforme em toda altura → hiss. Escurece com spectral_gate.
- Harmônicos verticais da voz (formantes) → devem permanecer nítidos
  em todos os estágios. Se sumirem, cleaner está agressivo demais.
- Banda de frequências ativa: voz vive em 80–4000 Hz principalmente.
  Acima disso em geral é ruído/fricativas.

Para ver diferença de energia por frequência (não tempo), use FFT média:

```python
from numpy.fft import rfft, rfftfreq

def avg_spectrum(wav_path):
    data, sr = load_mono(wav_path)
    n = len(data)
    mag = np.abs(rfft(data))
    freqs = rfftfreq(n, 1/sr)
    db = 20 * np.log10(mag + 1e-12)
    return freqs, db

plt.figure(figsize=(10, 4))
for label, path in [("raw", "samples/rec.wav"),
                    ("denoise", "samples/rec__chain_denoise.wav")]:
    f, db = avg_spectrum(path)
    plt.semilogx(f, db, label=label, alpha=0.7)
plt.xlim(20, 8000); plt.xlabel("Hz"); plt.ylabel("dB"); plt.legend()
plt.savefig("/tmp/avg_spectrum.png", dpi=120)
```

Curva média mostra direto quanto de dB foi removido em cada faixa.
