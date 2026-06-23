"""Audio recording worker for the ToolKit app.

Records the system's full audio output (WASAPI loopback of the default render
device), optionally mixing in the default microphone. Invoked as a subprocess
by server.py.

    python audio_record_worker.py record --output PATH [--mic-output PATH]
        Records until the line "STOP" is received on stdin (or stdin is
        closed), then finalizes the WAV file(s) and prints:
        -> DONE:{"seconds": float, "bytes": int, "mic": bool, "peak": int}

Protocol — line-prefixed messages on stdout:
    STATUS:<msg>   progress / state ("RECORDING" once capture actually starts)
    DONE:<json>    success result
    ERROR:<msg>    failure
"""
import argparse
import array
import json
import sys
import threading
import time
import wave


def emit(prefix: str, msg: str) -> None:
    sys.stdout.write(f"{prefix}:{msg}\n")
    sys.stdout.flush()


def _peak16(data: bytes, current: int) -> int:
    """Return the larger of `current` and the peak |amplitude| of 16-bit PCM
    `data` (audioop was removed in Python 3.13, so use array's C-level min/max)."""
    if not data:
        return current
    a = array.array("h")
    n = len(data) - (len(data) % 2)
    if n <= 0:
        return current
    a.frombytes(data[:n])
    if not a:
        return current
    return max(current, abs(min(a)), max(a))


# ---------------------------------------------------------------------------
# Recording — system loopback via PyAudioWPatch
# ---------------------------------------------------------------------------
def record_system(out_path: str, stop_event: threading.Event,
                  mic_out: str | None = None, mic_err: list | None = None,
                  ready_event: threading.Event | None = None,
                  max_seconds: float | None = None,
                  max_bytes: int | None = None) -> tuple[float, int, int]:
    import pyaudiowpatch as pyaudio

    p = pyaudio.PyAudio()
    mic_thread = None
    try:
        wasapi = p.get_host_api_info_by_type(pyaudio.paWASAPI)
        default_out = p.get_device_info_by_index(wasapi["defaultOutputDevice"])
        loopback = None
        if default_out.get("isLoopbackDevice"):
            loopback = default_out
        else:
            for dev in p.get_loopback_device_info_generator():
                if default_out["name"] in dev["name"]:
                    loopback = dev
                    break
        if loopback is None:
            raise OSError("No WASAPI loopback device found")

        channels = int(loopback["maxInputChannels"]) or 2
        rate = int(loopback["defaultSampleRate"])
        chunk = 2048

        # Capture the microphone concurrently on the *same* PyAudio instance —
        # two separate PortAudio instances conflict and silence the loopback.
        if mic_out:
            mic_thread = threading.Thread(
                target=record_mic, args=(mic_out, stop_event, mic_err if mic_err is not None else []),
                kwargs={"pa": p}, daemon=True)
            mic_thread.start()

        wf = wave.open(out_path, "wb")
        wf.setnchannels(channels)
        wf.setsampwidth(2)  # paInt16
        wf.setframerate(rate)

        stream = p.open(format=pyaudio.paInt16, channels=channels, rate=rate,
                        frames_per_buffer=chunk, input=True,
                        input_device_index=loopback["index"])
        total_bytes = 0
        peak = 0
        emit("STATUS", "RECORDING")
        if ready_event is not None:
            ready_event.set()
        start_ts = time.time()
        try:
            while not stop_event.is_set():
                data = stream.read(chunk, exception_on_overflow=False)
                wf.writeframes(data)
                total_bytes += len(data)
                peak = _peak16(data, peak)
                # Safety caps: auto-stop a runaway recording so it always
                # finalizes into a downloadable file instead of recording
                # forever / filling the disk.
                if max_seconds and (time.time() - start_ts) >= max_seconds:
                    emit("STATUS", "MAX_DURATION_REACHED")
                    stop_event.set()
                    break
                if max_bytes and total_bytes >= max_bytes:
                    emit("STATUS", "MAX_SIZE_REACHED")
                    stop_event.set()
                    break
        finally:
            stream.stop_stream()
            stream.close()
            wf.close()
        return time.time() - start_ts, total_bytes, peak
    finally:
        if mic_thread is not None:
            stop_event.set()
            mic_thread.join(timeout=10)
        p.terminate()


# ---------------------------------------------------------------------------
# Recording — microphone (default input device) via PyAudioWPatch
# ---------------------------------------------------------------------------
def record_mic(out_path: str, stop_event: threading.Event, err_out: list, pa=None) -> None:
    """Record the default microphone to a WAV until stop_event is set.

    Failures are non-fatal: they are appended to err_out and the main capture
    continues without the mic track. When ``pa`` is supplied the caller's
    PyAudio instance is reused (required when also recording system loopback —
    two separate PortAudio instances conflict); otherwise a private instance is
    created and terminated here.
    """
    import pyaudiowpatch as pyaudio

    own_pa = pa is None
    p = pa
    stream = None
    wf = None
    try:
        if p is None:
            p = pyaudio.PyAudio()
        info = p.get_default_input_device_info()
        channels = min(int(info.get("maxInputChannels") or 1), 2) or 1
        rate = int(info.get("defaultSampleRate") or 44100)
        chunk = 1024

        wf = wave.open(out_path, "wb")
        wf.setnchannels(channels)
        wf.setsampwidth(2)  # paInt16
        wf.setframerate(rate)

        stream = p.open(format=pyaudio.paInt16, channels=channels, rate=rate,
                        frames_per_buffer=chunk, input=True,
                        input_device_index=int(info["index"]))
        emit("STATUS", "MIC_RECORDING")
        while not stop_event.is_set():
            data = stream.read(chunk, exception_on_overflow=False)
            wf.writeframes(data)
    except Exception as exc:
        err_out.append(str(exc))
        emit("STATUS", f"麦克风录制失败：{exc}")
    finally:
        try:
            if stream is not None:
                stream.stop_stream()
                stream.close()
        except Exception:
            pass
        try:
            if wf is not None:
                wf.close()
        except Exception:
            pass
        try:
            if own_pa and p is not None:
                p.terminate()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Command
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


def cmd_record(out_path: str, mic_out: str | None,
               max_seconds: float | None = None, max_bytes: int | None = None) -> None:
    stop_event = threading.Event()
    threading.Thread(target=_stdin_stop_watcher, args=(stop_event,), daemon=True).start()

    mic_err: list = []
    seconds, total, peak = record_system(
        out_path, stop_event, mic_out, mic_err,
        max_seconds=max_seconds, max_bytes=max_bytes)
    mic_ok = bool(mic_out) and not mic_err

    emit("DONE", json.dumps({
        "seconds": round(seconds, 2), "bytes": total, "mic": mic_ok, "peak": peak}))


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    rec = sub.add_parser("record")
    rec.add_argument("--output", required=True)
    rec.add_argument("--mic-output", dest="mic_output", default=None)
    rec.add_argument("--max-seconds", dest="max_seconds", type=float, default=None)
    rec.add_argument("--max-bytes", dest="max_bytes", type=int, default=None)
    args = parser.parse_args()

    try:
        if args.command == "record":
            cmd_record(args.output, args.mic_output,
                       max_seconds=args.max_seconds, max_bytes=args.max_bytes)
    except Exception as exc:
        emit("ERROR", str(exc))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
