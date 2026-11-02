"""Entry point of the backend process started by the Electron app (or by hand for development).

    agent4sci-server --port 0 --token <secret> [--demo] [--exit-on-stdin-close]

Prints one JSON line ``{"event": "ready", "port": N}`` once the socket is bound.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import secrets
import socket
import sys
import threading


def _watch_stdin() -> None:
    """Exit when the parent (Electron) closes our stdin — i.e. when the app quits or crashes."""
    try:
        while sys.stdin.read(1024):
            pass
    except Exception:
        pass
    os._exit(0)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="agent4sci-server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=int(os.environ.get("A4S_PORT", "0")))
    parser.add_argument("--token", default=os.environ.get("A4S_TOKEN"))
    parser.add_argument("--demo", action="store_true", help="offline demo model (no API key needed)")
    parser.add_argument("--no-token", action="store_true", help="disable auth (development only)")
    parser.add_argument("--exit-on-stdin-close", action="store_true")
    parser.add_argument("--data-dir", default=None)
    args = parser.parse_args(argv)

    if args.data_dir:
        os.environ["A4S_DATA_DIR"] = args.data_dir
    if args.demo:
        os.environ["A4S_FAKE_LLM"] = "1"
    token = None if args.no_token else (args.token or secrets.token_urlsafe(24))

    import uvicorn

    from ..core.envfix import apply_system_proxy
    from .app import build_app

    apply_system_proxy()  # before any HTTP client exists: apps started from Finder / Start menu have no proxy env

    app = build_app(token)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((args.host, args.port))
    port = sock.getsockname()[1]
    ready = {"event": "ready", "port": port}
    if not args.token and token:
        ready["token"] = token  # generated here, so tell the developer
    print(json.dumps(ready), flush=True)

    if args.exit_on_stdin_close:
        threading.Thread(target=_watch_stdin, daemon=True).start()

    config = uvicorn.Config(app, log_level=os.environ.get("A4S_LOG_LEVEL", "warning"), ws="websockets-sansio")
    server = uvicorn.Server(config)
    asyncio.run(server.serve(sockets=[sock]))


if __name__ == "__main__":
    main()
