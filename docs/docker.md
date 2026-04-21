# Docker Setup

## Prerequisites

- Docker 24+ with Compose v2
- A Piper TTS voice model (`.onnx` + `.onnx.json`)
- ~10 GB free disk space

---

## 1. Add your voice model

Drop the files into `voice_models/`:

```
voice_models/
  en_US-ryan-medium.onnx
  en_US-ryan-medium.onnx.json
```

Download models at https://huggingface.co/rhasspy/piper-voices

---

## 2. Configure

```bash
cp api/.env.example api/.env
```

Edit `api/.env` — minimum required values:

```env
VOICE_PATH=/app/voice_models/en_US-ryan-medium.onnx
WAKE_MAC=your:mac:here        # only if using Wake-on-LAN
CLIENT_IP=192.168.0.22        # only if using audio mode (Raspberry Pi)
```

Everything else (Ollama URL, MCP address) is wired automatically between containers.

---

## 3. Build and start

```bash
docker compose up --build
```

First run is slow — Docker builds the Python image (torch is large) and Ollama downloads `lfm2.5-thinking:latest` (~8 GB). Subsequent starts are fast.

Wait for:
```
[ollama] model ready
INFO:     Application startup complete.
```

Then open **http://localhost**.

---

## Services

| Service  | Role | Internal port |
|----------|------|---------------|
| `nginx`  | Serves the UI; proxies `/api/*` to backend | 80 |
| `backend`| FastAPI + LLM agent + TTS | 8000 |
| `mcp`    | MCP tool server (weather, tasks, WoL) | 8001 |
| `ollama` | Ollama inference + model storage | 11434 |

---

## Persistent data

| Volume | Contents |
|--------|---------|
| `ollama-data` | Ollama models |
| `app-data` | SQLite DB (tasks + conversation history) |

`docker compose down` stops containers but keeps volumes. To wipe everything including the model:

```bash
docker compose down -v
```

---

## Useful commands

```bash
# Run in background
docker compose up -d --build

# Follow logs
docker compose logs -f

# Rebuild only one service
docker compose up -d --build backend

# Stop (keep data)
docker compose down
```

---

## Changing the port

Add to a `.env` file at the project root:

```env
HTTP_PORT=8080
```

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `VOICE_PATH not set` | Check `api/.env` has `VOICE_PATH` and `voice_models/` is populated |
| Ollama healthcheck failing | Model is still downloading — `docker compose logs ollama` to watch |
| `Sorry, I encountered an error` | Backend/MCP not ready yet — wait and retry |
| Port 80 in use | Set `HTTP_PORT=8080` in `.env` |
