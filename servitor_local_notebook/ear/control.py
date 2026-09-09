"""Unix-socket control plane for the ear.

The bar widget is a QML process that cannot import Python. It talks to the
daemon the same way `vitor.perfo` talks to its collector: it spawns a command
that streams newline-delimited JSON on stdout, and runs a short command to
change something. This module is both ends of that.
"""
from __future__ import annotations

import json
import logging
import os
import socket
import socketserver
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

COMMANDS = ("status", "stream", "toggle", "on", "off")


def socket_path() -> Path:
    runtime = os.getenv("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    return Path(runtime) / "servitor-ear.sock"


class _Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        raw = self.rfile.readline()
        command = raw.decode("utf-8", "replace").strip().lower() or "status"
        ear = self.server.ear  # type: ignore[attr-defined]

        if command not in COMMANDS:
            self._send({"error": f"unknown command: {command}"})
            return

        if command == "toggle":
            ear.toggle()
        elif command == "on":
            ear.set_enabled(True)
        elif command == "off":
            ear.set_enabled(False)

        if command == "stream":
            self._stream(ear)
        else:
            self._send(ear.snapshot())

    def _stream(self, ear) -> None:
        """Hand every state change to the widget until it goes away."""
        pending: list[dict] = []
        wakeup = threading.Event()

        def on_change(payload: dict) -> None:
            pending.append(payload)
            wakeup.set()

        ear.subscribe(on_change)
        try:
            while True:
                wakeup.wait(timeout=5.0)
                wakeup.clear()
                if pending:
                    while pending:
                        self._send(pending.pop(0))
                else:
                    # Keepalive, and a resync for a widget that missed one.
                    self._send(ear.snapshot())
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            ear.unsubscribe(on_change)

    def _send(self, payload: dict) -> None:
        self.wfile.write((json.dumps(payload) + "\n").encode("utf-8"))
        self.wfile.flush()


class _Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, path: Path, ear):
        self.ear = ear
        super().__init__(str(path), _Handler)


def serve(ear, path: Path | None = None) -> _Server:
    path = path or socket_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # A socket left behind by a crashed daemon would block bind(); it is ours
    # to clear, and only ever inside our own runtime directory.
    if path.exists():
        path.unlink()
    server = _Server(path, ear)
    path.chmod(0o600)
    threading.Thread(target=server.serve_forever, name="ear-control", daemon=True).start()
    logger.info(f"[Ear] control socket at {path}")
    return server


def request(command: str, path: Path | None = None, stream: bool = False):
    """Client side. Yields decoded JSON lines from the daemon."""
    path = path or socket_path()
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.connect(str(path))
        client.sendall((command + "\n").encode("utf-8"))
        with client.makefile("r", encoding="utf-8") as reader:
            for line in reader:
                line = line.strip()
                if not line:
                    continue
                yield json.loads(line)
                if not stream:
                    return
