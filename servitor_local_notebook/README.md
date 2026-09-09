# servitor_local_notebook

The half of the Servitor that only makes sense on a machine with a microphone,
a screen and a GPU. Split out so the deployment in the VM -- which serves the
sessions, the history and the web UI -- can be read and changed without
stepping through code it will never run.

What lives here:

| Path | What it is |
| --- | --- |
| `ear/` | The wake-word listener: Vosk gate, Whisper transcription, Piper/Kokoro speech, and the agent it talks to |
| `ov_chat.py` | The OpenVINO chat model, run on the laptop's iGPU |
| `mcp/desktop/` | Dictation into the focused window (`:8004`) |
| `mcp/browser/` | Opening sites and YouTube searches (`:8005`) |
| `mcp/media/` | Playback control over MPRIS (`:8006`) |
| `mcp/youtube/` | What is new on followed channels (`:8007`) |
| `omarchy/` | The Quickshell bar widget and the listening overlay |
| `mcp_host.py` | Serves the four above **and** the three shared ones |

What stays under `api/`: the FastAPI server, the sessions, the agent client,
and the three MCP servers that need nothing but a network -- general,
dev-activity and Nextcloud.

## Which way the dependencies point

This module imports from `api/`; `api/` does not import from here, with one
deliberate exception. `client2.py` reaches for `servitor_local_notebook.ov_chat`
inside the `backend == "openvino"` branch, so the VM never touches it -- it
runs `SERVITOR_LLM_BACKEND=ollama` and the import never happens.

## Running it

Both source roots have to be importable -- the repository root for this module,
`api/` for what it depends on. `scripts/servitor-ear` already sets that up:

    scripts/servitor-ear daemon                        # the listener
    python -m servitor_local_notebook.mcp_host         # all seven MCP servers

In the VM, `python -m mcp_module.host` from `api/` serves the three that run
anywhere, and nothing here is started at all.
