"""Command line for the ear.

    servitor-ear daemon    run the listener (systemd --user unit)
    servitor-ear stream    newline JSON of every state change (the bar widget)
    servitor-ear toggle    turn listening on or off
    servitor-ear status    print the current state once
"""
from __future__ import annotations

import argparse
import json
import logging
import signal
import threading

from ear import assistant, control
from ear.ear import EarConfig, ServitorEar


def _daemon() -> int:
    config = EarConfig.from_env()
    responder = assistant.build(config)
    ear = ServitorEar(config, responder=responder)
    control.serve(ear)
    if responder is not None:
        # Compiling on the NPU takes seconds the first time. Do it now so the
        # first "hey oracle" is not the request that pays for it.
        print("[ear] warming models...", flush=True)
        responder.warm()
    ear.start()

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    print(f"[ear] listening for {config.wake_phrase!r}", flush=True)
    stop.wait()
    ear.stop()
    return 0


def _client(command: str, stream: bool) -> int:
    try:
        for payload in control.request(command, stream=stream):
            print(json.dumps(payload), flush=True)
    except (FileNotFoundError, ConnectionRefusedError):
        # The widget renders this instead of vanishing when the daemon is down.
        print(json.dumps({"state": "off", "enabled": False, "detail": "daemon not running"}), flush=True)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="servitor-ear", description=__doc__)
    parser.add_argument("command", choices=("daemon", *control.COMMANDS))
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    if args.command == "daemon":
        return _daemon()
    return _client(args.command, stream=args.command == "stream")


if __name__ == "__main__":
    raise SystemExit(main())
