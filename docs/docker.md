# Running ServitorAssistant with Docker

## Prerequisites

- [Docker](https://docs.docker.com/get-docker/) 24+
- [Docker Compose](https://docs.docker.com/compose/install/) v2 (included with Docker Desktop)
- A Piper TTS voice model file (`.onnx` + `.onnx.json`)
- ~10 GB free disk space (Python deps + Ollama model)

---

## 1. Clone and enter the repo

```bash
git clone <repo-url>
cd ServitorAssisstant
git checkout feat/docker-containerization
```

---

## 2. Add your voice model

Place your Piper voice model files inside `voice_models/`:

```
voice_models/
  en_US-ryan-medium.onnx
  en_US-ryan-medium.onnx.json
```

> Download models from https://huggingface.co/rhasspy/piper-voices

---

## 3. Configure environment (optional)

The only value you may want to override is the Raspberry Pi client IP (for audio playback). Create a `.env` file at the project root:

```bash
# .env  (project root — read by docker compose)
CLIENT_IP=192.168.0.22   # IP of your Raspberry Pi; omit if not using audio mode
HTTP_PORT=80             # Host port for the web UI (default: 80)
```

Everything else (Ollama address, MCP address, API base URL) is wired automatically between containers.

---

## 4. Build and start

```bash
docker compose up --build
```

The **first run** takes longer because:

1. Docker builds the Python image (~several minutes, torch is large)
2. The Ollama container downloads `lfm2.5-thinking:latest` (~8 GB)

Subsequent starts skip both steps (layers and model are cached).

---

## 5. Open the UI

Once you see `[ollama] model ready` and the backend logs `Application startup complete`, open:

```
http://localhost
```

(or `http://<server-ip>` if accessing from another device on the LAN)

---

## Services overview

| Service  | What it does                                      | Internal port |
|----------|---------------------------------------------------|---------------|
| `nginx`  | Serves the React UI; proxies `/api/*` to backend  | 80 (→ host)   |
| `backend`| FastAPI server (LLM agent, TTS, conversation DB)  | 8000          |
| `mcp`    | FastMCP tool server (weather, tasks, math)        | 8001          |
| `ollama` | Ollama inference server + model storage           | 11434         |

---

## Persistent data

| Volume       | Contents                                  |
|--------------|-------------------------------------------|
| `ollama-data`| Ollama models — survives `docker compose down` |
| `app-data`   | SQLite DB (`tasks.db`) — conversation history and tasks |

> `docker compose down` stops containers but **keeps both volumes**.
> To wipe everything including the model: `docker compose down -v`

---

## Useful commands

```bash
# Start in background
docker compose up -d --build

# Follow logs for all services
docker compose logs -f

# Follow only backend logs
docker compose logs -f backend

# Restart a single service (e.g. after editing backend code)
docker compose up -d --build backend

# Stop everything (keep volumes)
docker compose down

# Stop and delete all data including the Ollama model
docker compose down -v
```

---

## Enabling debug logging

Set `DEBUG=true` for the backend service in your `.env`:

```bash
DEBUG=true
```

Then restart:

```bash
docker compose up -d backend
```

---

## Audio mode (Raspberry Pi)

Audio mode sends TTS audio to your Raspberry Pi. Make sure:

1. `CLIENT_IP` is set to your Pi's IP in `.env`
2. The Pi is running `api/ClientApi.py` on port `8000`

The backend container sends audio over HTTP to `CLIENT_IP:8000/play_file` — the Pi must be reachable from the host network. Since the backend container uses `network_mode: bridge` (default), the Pi IP must be routable from the Docker host.

---

## Changing the Ollama model

Edit `docker-compose.yml` and set the `OLLAMA_MODEL` environment variable on the `ollama` service:

```yaml
ollama:
  environment:
    OLLAMA_MODEL: llama3.2:latest
```

Also update `model_name` in `api/server/Server.py` to match, then rebuild:

```bash
docker compose up -d --build backend
```

---

## Troubleshooting

**Backend fails to start with `VOICE_PATH not set`**
→ The `voice_models/` directory is not mounted or the `.onnx` file is missing. Check `docker compose logs backend`.

**`ollama` healthcheck keeps failing**
→ Model download is in progress. Run `docker compose logs ollama` to watch the pull. Wait for `model ready` before expecting responses.

**Frontend shows `Sorry, I encountered an error`**
→ Backend or MCP is not ready yet. Check `docker compose logs backend mcp` and wait for startup to complete.

**Port 80 already in use**
→ Set `HTTP_PORT=8080` (or any free port) in `.env`, then `docker compose up -d`.
