# Code Overview

## What it does

ServitorAssistant is a local AI assistant with a Warhammer 40k Magos personality. You type a message in the browser, the backend sends it to a local LLM, and the response streams back in real time. It can also speak the response aloud to a Raspberry Pi.

---

## Project layout

```
api/
  ServerApi.py              — HTTP API (FastAPI)
  server/Server.py          — LLM logic, TTS, speech recognition
  mcp_module/
    stremable_http/
      stream2.py            — MCP tool server (weather, tasks, WoL)
      client2.py            — LangChain agent that calls the LLM + tools
  ClientApi.py              — Runs on the Raspberry Pi, plays audio

front/src/
  App.tsx                   — React chat UI
  App.css                   — Dark Adeptus Mechanicus theme

data/tasks.db               — SQLite database (tasks + conversation history)
voice_models/               — Piper TTS voice files (.onnx)
```

---

## How a message flows

1. **Browser** sends POST `/api/stream_message` with the user text.
2. **ServerApi.py** saves the message to the DB, then kicks off an async producer task.
3. **Server.py** loads conversation history from the DB and calls the LangChain agent.
4. **client2.py** connects to the MCP tool server, builds the message list, and streams tokens from Ollama (`lfm2.5-thinking:latest`).
5. Tokens are put on an `asyncio.Queue`. The SSE response reads from that queue and sends `data:` events to the browser every chunk.
6. When done, the full response is saved to the DB. If audio mode is on, it's also sent to the Raspberry Pi.

The producer task runs independently — if the browser disconnects mid-stream the response still finishes and gets saved.

---

## Key components

### ServerApi.py — FastAPI app
- `POST /stream_message` — main chat endpoint, returns SSE stream
- `GET /conversation` — loads chat history from DB
- `DELETE /conversation` — clears history
- Uses a keepalive ping every 5 s so the browser never times out on slow responses

### Server.py — ServitorServer class
- Loads Piper TTS voice model on startup
- `process_ollama_stream()` — state machine that separates `Thinking...` blocks from the actual response
- `_load_history()` — fetches last 200 messages, truncates to ~112K chars (fits in the 32K token context window)
- `generate_audio()` / `send_audio_bytes()` — TTS → sends WAV to the Pi

### stream2.py — MCP tool server
Tools exposed to the LLM:
**Weather**
| Tool | Description |
|------|-------------|
| `get_forecast` | Current weather. No args = Campina Grande. Pass `latitude`/`longitude` for another city. |

**Tasks** — all backed by SQLite (`data/tasks.db`)
| Tool | Description |
|------|-------------|
| `create_task` | New task with optional `due_at`, recurrence, timezone |
| `list_tasks` | List pending tasks (pass `show_completed=true` for all) |
| `get_task` | Single task by ID |
| `update_task` | Update any field of an existing task |
| `complete_task` | Mark done; auto-creates next occurrence if recurring |
| `delete_task` | Permanently delete a task |

**System**
| Tool | Description |
|------|-------------|
| `wake_on_lan` | Sends WoL magic packet to `WAKE_MAC` via UDP broadcast |

**Math**
| Tool | Description |
|------|-------------|
| `add_numbers` | `a + b` |
| `subtract_numbers` | `a - b` |
| `multiply_numbers` | `a × b` |
| `divide_numbers` | `a ÷ b` (errors on zero) |

**Meta**
| Tool | Description |
|------|-------------|
| `help` | Full tool reference |
| `task_help` | Task-specific reference with examples |
| `default_response` | Fallback when no tool matches — tells model to answer directly |

### client2.py — LangChain agent
- Connects to the MCP server via streamable HTTP
- Wraps `ChatOllama` with `create_react_agent` (LangGraph)
- `get_response_stream()` — async generator that yields text chunks, skipping tool-call noise
- `keep_alive=-1` keeps the model loaded in GPU memory between requests

### App.tsx — React frontend
- Streams SSE chunks and appends them to the message in real time
- `type: "thinking"` chunks go into a collapsible **Cogitating…** block
- Loads conversation history on mount so context survives page refresh
- 10-minute abort timeout so slow models never hang the tab

---

## Configuration

All config lives in `api/.env` (copy from `api/.env.example`):

| Variable | Purpose |
|----------|---------|
| `SERVER_IP` | IP of this machine (used for MCP address) |
| `OLLAMA_HOST` | Ollama URL (default `http://127.0.0.1:11434`) |
| `CLIENT_IP` | Raspberry Pi IP for audio |
| `VOICE_PATH` | Path to the `.onnx` Piper voice model |
| `WAKE_MAC` | MAC address for Wake-on-LAN |
| `DEBUG` | Set `true` for verbose logs |

Frontend config in `front/.env`:

| Variable | Purpose |
|----------|---------|
| `VITE_SERVER_IP` | Backend IP for local dev |
| `VITE_API_BASE` | Full API base URL (overrides `VITE_SERVER_IP`; set to `/api` in Docker) |

---

## Running locally (no Docker)

```bash
cp api/.env.example api/.env   # fill in your values
./start.sh                     # starts MCP server, API, and frontend
```

Open **http://localhost:5173**.

For verbose logs:
```bash
./start-debug.sh
```
