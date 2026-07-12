"""Application entry point: builds the FastAPI app and mounts every router.

The ASGI app is exposed as ``app.main:app``. ``server.py`` re-exports it so the
legacy ``server:app`` entry point (package.json / Dockerfile / start-dev.ps1)
keeps working.
"""
import os

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.core.auth import router as auth_router
from app.routers.audio import router as audio_router
from app.routers.excel import router as excel_router
from app.routers.book import router as book_router
from app.routers.copilot_chat import router as copilot_chat_router
from app.routers.discord import router as discord_router
from app.routers.pdf import router as pdf_router
from app.routers.profiles import router as profiles_router
from app.routers.screen import router as screen_router
from app.routers.sessions import router as sessions_router
from app.routers.teams_chat import router as teams_chat_router
from app.routers.teams_transcript import router as teams_transcript_router
from app.routers.threads import router as threads_router
from app.routers.transcribe import router as transcribe_router
from app.routers.wechat import router as wechat_router

# Repo root (one level up from the app/ package), where frontend/dist lives.
_REPO_ROOT = os.path.dirname(os.path.dirname(__file__))

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI()
app.include_router(auth_router)
app.include_router(audio_router)
app.include_router(excel_router)
app.include_router(book_router)
app.include_router(copilot_chat_router)
app.include_router(discord_router)
app.include_router(pdf_router)
app.include_router(profiles_router)
app.include_router(screen_router)
app.include_router(sessions_router)
app.include_router(teams_chat_router)
app.include_router(teams_transcript_router)
app.include_router(threads_router)
app.include_router(transcribe_router)
app.include_router(wechat_router)


# ---------------------------------------------------------------------------
# Serve React frontend (production build)
# Must be registered AFTER all /api/* routes
# ---------------------------------------------------------------------------
_FRONTEND_DIST = os.path.join(_REPO_ROOT, "frontend", "dist")
if os.path.isdir(_FRONTEND_DIST):
    app.mount(
        "/assets",
        StaticFiles(directory=os.path.join(_FRONTEND_DIST, "assets")),
        name="assets",
    )

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):  # noqa: ARG001
        """Catch-all: return index.html so React Router handles client-side nav."""
        return FileResponse(
            os.path.join(_FRONTEND_DIST, "index.html"),
            headers={"Cache-Control": "no-store"},
        )
