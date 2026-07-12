"""Central configuration constants shared across the app."""
import os

# Repository root (…/tool-box), two levels up from app/core/.
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
# On Fly.io set env var DB_PATH=/data/transcripts.db (persisted Volume).
# Locally falls back to <repo>/data/transcripts.db. The `data/` folder at the
# repo root is a junction pointing at OneDrive ProjectData (so the DB is
# backed up via OneDrive and never tracked in git).
DB_PATH = os.environ.get("DB_PATH", os.path.join(REPO_ROOT, "data", "transcripts.db"))

# ---------------------------------------------------------------------------
# Auth / JWT configuration
# ---------------------------------------------------------------------------
# Generate a real secret in production: python -c "import secrets; print(secrets.token_hex(32))"
SECRET_KEY = os.environ.get("SECRET_KEY", "change-me-to-a-random-secret-in-production")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.environ.get("ACCESS_TOKEN_EXPIRE_MINUTES", "1440"))  # 24h
