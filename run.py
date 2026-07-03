from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
import threading
import time
from urllib.error import URLError
from urllib.request import urlopen
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


def find_running_setup_order(host: str, preferred_port: int, max_port: int) -> int | None:
    for port in range(preferred_port, max_port + 1):
        if port_is_available(host, port):
            continue
        if is_setup_order_server(host, port):
            return port
    return None


def is_setup_order_server(host: str, port: int) -> bool:
    health_url = f"http://{host}:{port}/api/health"
    try:
        with urlopen(health_url, timeout=0.5) as response:
            if response.status != 200:
                return False
            payload = json.loads(response.read().decode("utf-8"))
            return payload.get("app") == "Setup Order"
    except (OSError, URLError, ValueError, TimeoutError):
        pass

    root_url = f"http://{host}:{port}/"
    try:
        with urlopen(root_url, timeout=0.8) as response:
            if response.status != 200:
                return False
            html = response.read(4096).decode("utf-8", errors="ignore")
    except (OSError, URLError, TimeoutError):
        return False
    return "Setup Order" in html


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
            f"Missing dependencies for Python: {sys.executable}. "
            "Run install.bat first, or run: "
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
        running_port = find_running_setup_order(args.host, args.port, args.max_port)
        if running_port is not None:
            url = f"http://{args.host}:{running_port}"
            print("")
            print("Setup Order is already running.")
            print(f"Open: {url}")
            print("")
            if not args.no_open:
                webbrowser.open(url)
            return 0
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
