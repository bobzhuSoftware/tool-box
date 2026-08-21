"""Shared backend lifecycle: start/stop the resident FastAPI server.

Both the tray launcher (``desktop_launcher.py``) and the native-window app
(``desktop_app.py``) import this so there is a single source of truth for how
the backend process is spawned, health-checked and torn down.
"""
import os
import socket
import subprocess
import sys
import threading
import time

HOST = "127.0.0.1"
PORT = int(os.environ.get("VT_PORT", "8000"))
URL = f"http://{HOST}:{PORT}"

_REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
_VENV_PYTHON = os.path.join(_REPO_ROOT, ".venv", "Scripts", "python.exe")
PYTHON = _VENV_PYTHON if os.path.isfile(_VENV_PYTHON) else sys.executable
FRONTEND_DIST = os.path.join(_REPO_ROOT, "frontend", "dist")

# CREATE_NO_WINDOW keeps the backend console hidden on Windows.
_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

_backend_proc: subprocess.Popen | None = None
_lock = threading.Lock()


def port_open() -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.4)
        return s.connect_ex((HOST, PORT)) == 0


def ensure_frontend_built() -> None:
    """Build the frontend once if no dist exists (the backend serves it statically)."""
    if os.path.isdir(FRONTEND_DIST):
        return
    print("frontend/dist not found — building once (this happens only the first time)...")
    subprocess.run(["npm", "run", "build"], cwd=_REPO_ROOT, shell=(os.name == "nt"), check=True)


def backend_running() -> bool:
    return (_backend_proc is not None and _backend_proc.poll() is None) or port_open()


def start_backend() -> None:
    global _backend_proc
    with _lock:
        if backend_running():
            return
        _backend_proc = subprocess.Popen(
            [PYTHON, "-m", "uvicorn", "server:app", "--host", HOST, "--port", str(PORT)],
            cwd=_REPO_ROOT,
            creationflags=_NO_WINDOW,
        )
    # Wait until the port accepts connections so the first UI load never fails.
    for _ in range(120):  # up to ~30s to cover a cold start
        if port_open():
            return
        time.sleep(0.25)


def stop_backend() -> None:
    global _backend_proc
    with _lock:
        if _backend_proc is not None and _backend_proc.poll() is None:
            _backend_proc.terminate()
            try:
                _backend_proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                _backend_proc.kill()
        _backend_proc = None
    # Wait for the socket to be released so an immediate restart can re-bind the port.
    for _ in range(40):
        if not port_open():
            return
        time.sleep(0.1)
