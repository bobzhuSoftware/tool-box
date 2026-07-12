"""Browser profile selection (Edge for Teams, Firefox for Web→PDF X/Twitter)."""
import os

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core.auth import require_user
from app.core.db import User
from app.core.settings import _get_user_setting, _set_user_setting

router = APIRouter()


# ---------------------------------------------------------------------------
# Edge profile selection (shared by Teams Transcript + Teams Chat)
# ---------------------------------------------------------------------------
class EdgeProfileSelectRequest(BaseModel):
    dir: str


@router.get("/api/edge-profiles")
def get_edge_profiles(user: User = Depends(require_user)):
    """List the Edge profiles available on this machine plus the user's saved
    choice (falling back to auto-detection)."""
    import browser_utils
    profiles = browser_utils.list_edge_profiles()
    saved = _get_user_setting(user.id, "edge_profile")
    valid_dirs = {p["dir"] for p in profiles}
    selected = saved if saved in valid_dirs else browser_utils.resolve_profile()
    return {"profiles": profiles, "selected": selected}


@router.post("/api/edge-profiles/select")
def select_edge_profile(req: EdgeProfileSelectRequest, user: User = Depends(require_user)):
    """Persist the user's chosen Edge profile directory."""
    choice = req.dir.strip()
    if not choice:
        raise HTTPException(status_code=400, detail="Profile dir is required")
    _set_user_setting(user.id, "edge_profile", choice)
    return {"ok": True, "selected": choice}


# ---------------------------------------------------------------------------
# Firefox profile selection (used by Web→PDF X/Twitter mode)
# ---------------------------------------------------------------------------
class FirefoxProfileSelectRequest(BaseModel):
    dir: str


@router.get("/api/firefox-profiles")
def get_firefox_profiles(user: User = Depends(require_user)):
    """List the Firefox profiles on this machine plus the user's saved choice
    (falling back to auto-detection)."""
    import browser_utils
    profiles = browser_utils.list_firefox_profiles()
    saved = _get_user_setting(user.id, "firefox_profile")
    valid_dirs = {p["dir"] for p in profiles}
    if saved in valid_dirs:
        selected = saved
    else:
        resolved = browser_utils.resolve_firefox_profile()
        selected = os.path.basename(resolved.rstrip("\\/")) if resolved else ""
    return {"profiles": profiles, "selected": selected}


@router.post("/api/firefox-profiles/select")
def select_firefox_profile(req: FirefoxProfileSelectRequest, user: User = Depends(require_user)):
    """Persist the user's chosen Firefox profile directory."""
    choice = req.dir.strip()
    if not choice:
        raise HTTPException(status_code=400, detail="Profile dir is required")
    _set_user_setting(user.id, "firefox_profile", choice)
    return {"ok": True, "selected": choice}
