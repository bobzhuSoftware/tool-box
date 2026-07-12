"""Legacy entry point. The real app now lives in ``app/main.py``.

Kept so existing ``server:app`` references (package.json, Dockerfile,
start-dev.ps1, README) keep working. New code should use ``app.main:app``.
"""
from app.main import app

__all__ = ["app"]

