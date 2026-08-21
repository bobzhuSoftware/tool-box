"""System-tray launcher: keeps the FastAPI backend resident so the web UI opens instantly.

Alternative to the native-window app (``desktop_app.py``). Starts the backend once
(no Vite), serves the pre-built React frontend, and lives in the system tray. Click
the tray icon to open the UI in your browser; the backend stays warm in the background.

Usage:
    .venv\\Scripts\\pythonw.exe desktop_launcher.py
"""
import threading
import webbrowser

from PIL import Image, ImageDraw
import pystray

import app_backend as bk


def open_ui() -> None:
    if not bk.backend_running():
        bk.start_backend()
    webbrowser.open(bk.URL)


# ---------------------------------------------------------------------------
# Tray icon
# ---------------------------------------------------------------------------
def _make_icon() -> Image.Image:
    img = Image.new("RGB", (64, 64), (30, 30, 46))
    d = ImageDraw.Draw(img)
    d.rectangle([14, 18, 50, 46], outline=(120, 200, 255), width=3)
    d.polygon([(28, 26), (28, 38), (40, 32)], fill=(120, 200, 255))
    return img


def _on_open(icon, item):  # noqa: ARG001
    threading.Thread(target=open_ui, daemon=True).start()


def _on_restart(icon, item):  # noqa: ARG001
    def _restart():
        bk.stop_backend()
        bk.start_backend()
    threading.Thread(target=_restart, daemon=True).start()


def _on_quit(icon, item):  # noqa: ARG001
    bk.stop_backend()
    icon.stop()


def main() -> None:
    bk.ensure_frontend_built()
    # Warm the backend on launch so the first "打开界面" is instant.
    threading.Thread(target=bk.start_backend, daemon=True).start()

    menu = pystray.Menu(
        pystray.MenuItem("打开界面", _on_open, default=True),
        pystray.MenuItem("重启后端", _on_restart),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("退出", _on_quit),
    )
    icon = pystray.Icon("video_transcript", _make_icon(), "Video Transcript", menu)
    icon.run()


if __name__ == "__main__":
    main()
