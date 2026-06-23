"""Screen recording worker for the ToolKit app.

Captures a single WINDOW's video via the Windows Graphics Capture (WGC) API —
the same mechanism OBS uses, so it works for hardware-accelerated / occluded
windows (Chrome, Teams/webview2, UWP) that GDI capture would render black —
and mixes in the system's full audio output (WASAPI loopback) plus an optional
microphone, muxing everything into a single MP4 with ffmpeg.

Invoked as a subprocess by server.py.

    python screen_record_worker.py list-windows
        Enumerate capturable top-level windows ->
        DONE:{"windows":[{"hwnd":int,"pid":int,"title":str,"name":str}, ...]}

    python screen_record_worker.py record --hwnd N --output PATH
            [--mic-output PATH] [--fps 30]
        Records the given window until the line "STOP" arrives on stdin (or
        stdin closes), then muxes video + audio and prints:
        -> DONE:{"seconds":float,"bytes":int,"mic":bool,"width":int,
                 "height":int,"fps":int}

Protocol — line-prefixed messages on stdout:
    STATUS:<msg>   progress / state ("RECORDING" once capture actually starts)
    DONE:<json>    success result
    ERROR:<msg>    failure
"""
import argparse
import ctypes
import ctypes.wintypes as wintypes
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time


def emit(prefix: str, msg: str) -> None:
    sys.stdout.write(f"{prefix}:{msg}\n")
    sys.stdout.flush()


# Make GetClientRect return physical pixels so the fallback window size matches
# the WGC frame size on high-DPI displays.
try:
    ctypes.windll.user32.SetProcessDPIAware()
except Exception:
    pass


# ---------------------------------------------------------------------------
# Window enumeration (ctypes / user32) — pick a window to record
# ---------------------------------------------------------------------------
_user32 = ctypes.windll.user32
_dwmapi = ctypes.windll.dwmapi
_kernel32 = ctypes.windll.kernel32

GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
DWMWA_CLOAKED = 14
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def _window_exe_name(pid: int) -> str:
    """Return the process executable base name for a pid (best effort)."""
    h = _kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(512)
        size = wintypes.DWORD(len(buf))
        if _kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return os.path.basename(buf.value)
        return ""
    finally:
        _kernel32.CloseHandle(h)


def list_windows() -> list[dict]:
    """Enumerate visible, titled, non-cloaked top-level windows."""
    results: list[dict] = []
    own_pid = os.getpid()

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _enum(hwnd, _lparam):
        if not _user32.IsWindowVisible(hwnd):
            return True
        # Skip windows owned by another window (dialogs/popups).
        if _user32.GetWindow(hwnd, 4):  # GW_OWNER
            return True
        length = _user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        _user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value.strip()
        if not title or title == "Program Manager":
            return True
        # Skip tool windows (floating palettes, etc.).
        ex_style = _user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        if ex_style & WS_EX_TOOLWINDOW:
            return True
        # Skip DWM-cloaked windows (e.g. background UWP apps).
        cloaked = wintypes.DWORD(0)
        _dwmapi.DwmGetWindowAttribute(
            hwnd, DWMWA_CLOAKED, ctypes.byref(cloaked), ctypes.sizeof(cloaked))
        if cloaked.value:
            return True
        pid = wintypes.DWORD(0)
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == own_pid:
            return True
        results.append({
            "hwnd": int(hwnd),
            "pid": int(pid.value),
            "title": title,
            "name": _window_exe_name(pid.value),
        })
        return True

    _user32.EnumWindows(_enum, 0)
    return results


def cmd_list_windows() -> None:
    emit("DONE", json.dumps({"windows": list_windows()}))


def _client_size(hwnd: int) -> tuple[int, int]:
    """Return the (width, height) of a window's client area in pixels."""
    rect = wintypes.RECT()
    if _user32.GetClientRect(hwnd, ctypes.byref(rect)):
        return rect.right - rect.left, rect.bottom - rect.top
    return 0, 0


# ---------------------------------------------------------------------------
# Video capture (Windows Graphics Capture) + ffmpeg encode
# ---------------------------------------------------------------------------
class _VideoRecorder:
    """Captures one window via WGC and pipes raw BGRA frames to ffmpeg at a
    fixed frame rate (repeating the last frame when the window is static)."""

    def __init__(self, hwnd: int, out_path: str, fps: int):
        self.hwnd = hwnd
        self.out_path = out_path
        self.fps = max(1, min(int(fps), 60))
        self._lock = threading.Lock()
        self._latest = None            # most recent BGRA ndarray (h, w, 4)
        self._first = threading.Event()
        self.width = 0
        self.height = 0
        self._frame_w = 0              # size reported by the first WGC frame
        self._frame_h = 0
        self._capture_ctl = None
        self._ff = None
        self._encoder_thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._closed = threading.Event()   # WGC reported the window closed
        self._frames_written = 0

    def _on_frame(self, frame, _ctrl) -> None:
        # The native buffer is only valid during the callback -> copy it.
        arr = frame.frame_buffer.copy()
        with self._lock:
            self._latest = arr
        if not self._first.is_set():
            self._frame_w = frame.width
            self._frame_h = frame.height
            self._first.set()

    def start(self) -> None:
        from windows_capture import WindowsCapture

        cap = WindowsCapture(cursor_capture=True, draw_border=None,
                             window_hwnd=self.hwnd)
        # WindowsCapture.event() only accepts functions literally named
        # on_frame_arrived / on_closed, so assign the handlers directly.
        cap.frame_handler = self._on_frame
        # A "closed" event must NOT stop encoding — WGC can fire it spuriously
        # while other COM/WASAPI components initialize. Just note it; the encoder
        # keeps emitting the last frame until stop() is called explicitly.
        cap.closed_handler = self._closed.set

        self._capture_ctl = cap.start_free_threaded()

        # Use the window's client rect as the authoritative frame size: it's
        # stable and (with DPI awareness) matches the WGC frame size, whereas
        # the very first WGC frame can be a transient wrong size. Fall back to
        # waiting for a real frame only if the client rect can't be read.
        cw, ch = _client_size(self.hwnd)
        if cw <= 0 or ch <= 0:
            if self._first.wait(timeout=4):
                cw, ch = self._frame_w, self._frame_h
            else:
                self.stop()
                raise OSError("无法获取窗口尺寸（窗口可能已最小化或已关闭）")
        self.width = cw - (cw % 2)
        self.height = ch - (ch % 2)

        ff = shutil.which("ffmpeg") or "ffmpeg"
        cmd = [
            ff, "-y",
            "-f", "rawvideo", "-pixel_format", "bgra",
            "-video_size", f"{self.width}x{self.height}",
            "-framerate", str(self.fps),
            "-i", "-",
            "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            self.out_path,
        ]
        self._ff = subprocess.Popen(
            cmd, stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        self._encoder_thread = threading.Thread(target=self._encode_loop, daemon=True)
        self._encoder_thread.start()

    def _encode_loop(self) -> None:
        import numpy as np
        import cv2

        interval = 1.0 / self.fps
        next_t = time.perf_counter()
        w, h = self.width, self.height
        while not self._stop.is_set():
            now = time.perf_counter()
            if now < next_t:
                time.sleep(min(next_t - now, interval))
                continue
            next_t += interval
            if next_t < now - interval:      # fell behind -> resync
                next_t = now + interval
            with self._lock:
                arr = self._latest
            if arr is None:
                # No real frame yet (static window): emit a black frame so the
                # timeline keeps advancing and the output starts immediately.
                arr = np.zeros((h, w, 4), dtype=np.uint8)
                arr[:, :, 3] = 255
            if arr.shape[0] != h or arr.shape[1] != w:
                arr = cv2.resize(arr, (w, h))
            if not arr.flags["C_CONTIGUOUS"]:
                arr = np.ascontiguousarray(arr)
            try:
                self._ff.stdin.write(arr.tobytes())
                self._frames_written += 1
            except (BrokenPipeError, OSError):
                break

    def stop(self) -> float:
        """Stop capture + encoding, finalize the video file. Returns video seconds."""
        self._stop.set()
        if self._encoder_thread is not None:
            self._encoder_thread.join(timeout=10)
        if self._ff is not None:
            try:
                self._ff.stdin.close()
            except Exception:
                pass
            try:
                self._ff.wait(timeout=30)
            except Exception:
                try:
                    self._ff.kill()
                except Exception:
                    pass
        if self._capture_ctl is not None:
            try:
                self._capture_ctl.stop()
                self._capture_ctl.wait()
            except Exception:
                pass
        return self._frames_written / float(self.fps)


# ---------------------------------------------------------------------------
# Record command
# ---------------------------------------------------------------------------
def _stdin_stop_watcher(stop_event: threading.Event) -> None:
    """Set stop_event when 'STOP' arrives on stdin or stdin is closed."""
    try:
        for line in sys.stdin:
            if line.strip().upper() == "STOP":
                break
    except Exception:
        pass
    stop_event.set()


def _mux(video_path: str, audio_path: str, mic_path: str | None,
         out_path: str) -> None:
    """Mux the video-only file with the system-audio (and optional mic) WAVs."""
    ff = shutil.which("ffmpeg") or "ffmpeg"
    has_mic = bool(mic_path and os.path.isfile(mic_path) and os.path.getsize(mic_path) > 1024)
    cmd = [ff, "-y", "-i", video_path, "-i", audio_path]
    if has_mic:
        cmd += ["-i", mic_path,
                "-filter_complex",
                "[1:a][2:a]amix=inputs=2:duration=longest:dropout_transition=0:normalize=0[a]",
                "-map", "0:v", "-map", "[a]"]
    else:
        cmd += ["-map", "0:v", "-map", "1:a"]
    cmd += ["-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart", out_path]
    cp = subprocess.run(cmd, capture_output=True, text=True)
    if cp.returncode != 0 or not os.path.isfile(out_path):
        raise OSError(f"ffmpeg 合成失败：{cp.stderr[-300:]}")


def cmd_record(hwnd: int, out_path: str, mic_out: str | None, fps: int) -> None:
    import audio_record_worker as audio
    # Load the heavy capture extension (which pulls in OpenCV) BEFORE starting
    # the stdin watcher thread: importing it while another thread is blocked
    # reading sys.stdin deadlocks the import until input arrives.
    from windows_capture import WindowsCapture  # noqa: F401

    stop_event = threading.Event()
    threading.Thread(target=_stdin_stop_watcher, args=(stop_event,), daemon=True).start()

    tmp_dir = tempfile.mkdtemp(prefix="vt_screen_")
    video_tmp = os.path.join(tmp_dir, "video.mp4")
    audio_tmp = os.path.join(tmp_dir, "system.wav")

    # 1) Start the window video capture + encoder.
    video = _VideoRecorder(hwnd, video_tmp, fps)
    video.start()

    # 2) Start audio capture in a background thread; wait until it actually
    #    begins reading so the audio/video start points line up.
    audio_ready = threading.Event()
    mic_err: list = []
    audio_result: dict = {}

    def _run_audio():
        try:
            seconds, total, peak = audio.record_system(
                audio_tmp, stop_event, mic_out, mic_err, ready_event=audio_ready)
            audio_result.update(seconds=seconds, bytes=total, peak=peak)
        except Exception as exc:  # noqa: BLE001
            audio_result["error"] = str(exc)
            audio_ready.set()

    audio_thread = threading.Thread(target=_run_audio, daemon=True)
    audio_thread.start()
    audio_ready.wait(timeout=10)

    emit("STATUS", "RECORDING")

    # 3) Record until STOP.
    stop_event.wait()

    # 4) Finalize video + audio, then mux.
    emit("STATUS", "正在合成视频…")
    video_seconds = video.stop()
    audio_thread.join(timeout=15)

    mic_ok = bool(mic_out) and not mic_err and os.path.isfile(mic_out) \
        and os.path.getsize(mic_out) > 1024

    _mux(video_tmp, audio_tmp, mic_out if mic_ok else None, out_path)

    for p in (video_tmp, audio_tmp):
        try:
            os.unlink(p)
        except OSError:
            pass
    try:
        os.rmdir(tmp_dir)
    except OSError:
        pass

    emit("DONE", json.dumps({
        "seconds": round(video_seconds, 2),
        "bytes": os.path.getsize(out_path) if os.path.isfile(out_path) else 0,
        "mic": mic_ok,
        "width": video.width,
        "height": video.height,
        "fps": video.fps,
    }))


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list-windows")
    rec = sub.add_parser("record")
    rec.add_argument("--hwnd", required=True, type=int)
    rec.add_argument("--output", required=True)
    rec.add_argument("--mic-output", dest="mic_output", default=None)
    rec.add_argument("--fps", type=int, default=30)
    args = parser.parse_args()

    try:
        if args.command == "list-windows":
            cmd_list_windows()
        elif args.command == "record":
            cmd_record(args.hwnd, args.output, args.mic_output, args.fps)
    except Exception as exc:  # noqa: BLE001
        emit("ERROR", str(exc))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
