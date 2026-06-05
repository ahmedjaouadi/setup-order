from __future__ import annotations

import argparse
import socket
import subprocess
import sys
import threading
import time
import webbrowser


def port_is_available(host: str, port: int) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind((host, port))
            return True
    except OSError:
        return False


def find_port(host: str, preferred_port: int, max_port: int) -> int:
    for port in range(preferred_port, max_port + 1):
        if port_is_available(host, port):
            return port
    raise RuntimeError(
        f"No available port found between {preferred_port} and {max_port}."
    )


def open_browser_later(url: str) -> None:
    def worker() -> None:
        time.sleep(1.5)
        webbrowser.open(url)

    threading.Thread(target=worker, daemon=True).start()


def ensure_uvicorn_available() -> None:
    try:
        import uvicorn  # noqa: F401
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Missing dependencies. Run install.bat first, or run: "
            "python -m pip install -r requirements.txt"
        ) from exc


def main() -> int:
    parser = argparse.ArgumentParser(description="Start Setup Order locally.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--max-port", type=int, default=8020)
    parser.add_argument("--dev", action="store_true", help="Enable auto reload.")
    parser.add_argument("--no-open", action="store_true", help="Do not open browser.")
    args = parser.parse_args()

    try:
        ensure_uvicorn_available()
        port = find_port(args.host, args.port, args.max_port)
    except RuntimeError as exc:
        print(f"\nERROR: {exc}\n", file=sys.stderr)
        return 1

    url = f"http://{args.host}:{port}"
    command = [
        sys.executable,
        "-m",
        "uvicorn",
        "app.main:app",
        "--host",
        args.host,
        "--port",
        str(port),
    ]
    if args.dev:
        command.append("--reload")

    print("")
    print("Setup Order is starting...")
    print(f"Open: {url}")
    print("Stop: Ctrl+C")
    print("")

    if not args.no_open:
        open_browser_later(url)

    return subprocess.call(command)


if __name__ == "__main__":
    raise SystemExit(main())

