"""Whisper model cache — avoids reloading the same model on every request."""
import threading

import whisper

_whisper_models: dict[str, whisper.Whisper] = {}
_whisper_lock = threading.Lock()


def _get_whisper_model(model_name: str) -> whisper.Whisper:
    """Return a cached Whisper model, loading it on first use."""
    if model_name not in _whisper_models:
        with _whisper_lock:
            if model_name not in _whisper_models:  # double-check after acquiring lock
                _whisper_models[model_name] = whisper.load_model(model_name)
    return _whisper_models[model_name]
