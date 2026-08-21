"""Native-window desktop app (pywebview): opens the UI in its own window and frees
all memory when you close it.

Model: launch -> start resident backend -> show window. Close the window -> stop the
backend (releasing the Python process and any cached Whisper model). Next launch starts
fresh (fast, since heavy libs are lazy-imported).

Two behaviours make it play nicely with the existing web app (nothing here changes the
React code or the HTTP API):
  * Downloads / external links (``window.open`` and ``target="_blank"`` anchors) are
    routed to the system default browser — download endpoints carry the auth token as a
    query param, so they work in a fresh browser session. Blob ``a[download]`` saves fall
    through to WebView2's native download handler.
  * Closing while a job (transcription / recording / export / model download) is running
    asks for confirmation first, so you can't kill work by accident.

Usage:
    .venv\\Scripts\\pythonw.exe desktop_app.py
"""
import json
import os
import urllib.request

import webview

import app_backend as bk

# Persistent WebView2 profile so login (JWT in localStorage) survives across launches.
_STORAGE = os.path.join(os.path.expanduser("~"), ".vt_webview")

# Bypass any corporate proxy for localhost (proxies otherwise intercept 127.0.0.1).
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

_window = None

# Route new-window/external navigations to the system browser; leave blob downloads native.
_INJECT_JS = r"""
(function(){
  if (window.__vtExternalHooked) return;
  window.__vtExternalHooked = true;
  function ext(u){
    try { window.pywebview.api.open_external(new URL(u, location.href).href); }
    catch(e){ console.error('open_external failed', e); }
  }
  var _open = window.open;
  window.open = function(u){ if (u){ ext(u); return null; } return _open.apply(window, arguments); };
  document.addEventListener('click', function(e){
    var a = e.target && e.target.closest ? e.target.closest('a') : null;
    if (!a) return;
    var href = a.getAttribute('href');
    if (!href) return;
    if (a.getAttribute('download') !== null) return;   // native handler saves blob/data files
    if (a.target !== '_blank') return;
    var abs = new URL(href, location.href).href;
    if (/^https?:/i.test(abs)) { e.preventDefault(); ext(abs); }
  }, true);
})();
"""


class Api:
    def open_external(self, url):
        try:
            import webbrowser
            webbrowser.open(url)
        except Exception:
            pass
        return True


def _is_busy() -> bool:
    try:
        with _opener.open(bk.URL + "/api/app/status", timeout=2) as r:
            return bool(json.load(r).get("busy"))
    except Exception:
        return False


def _on_loaded():
    if _window is not None:
        _window.evaluate_js(_INJECT_JS)


def _on_closing():
    if _is_busy():
        ok = _window.create_confirmation_dialog(
            "任务进行中", "有任务正在运行，关闭窗口会中断它。确定关闭吗？"
        )
        if not ok:
            return False  # abort close, keep window open
    return True


def _on_closed():
    bk.stop_backend()


def main() -> None:
    global _window
    bk.ensure_frontend_built()
    bk.start_backend()  # blocks until the port is up so the window never loads a dead page

    _window = webview.create_window(
        "Video Transcript",
        bk.URL,
        js_api=Api(),
        width=1280,
        height=860,
        min_size=(900, 600),
    )
    _window.events.loaded += _on_loaded
    _window.events.closing += _on_closing
    _window.events.closed += _on_closed

    webview.start(private_mode=False, storage_path=_STORAGE)


if __name__ == "__main__":
    main()
