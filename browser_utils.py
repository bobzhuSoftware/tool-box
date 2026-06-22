"""
Shared browser/profile utilities for workers that drive a signed-in Edge session.

Removes hard-coded per-machine paths (e.g. C:\\Users\\<name>\\... and a fixed
"Profile 1") so the Teams workers run on any Windows machine that has Edge
installed.

Profile selection priority (see ``resolve_profile``):
    1. An explicit profile dir passed by the caller.
    2. The ``VT_EDGE_PROFILE`` environment variable (set by server.py from the
       user's saved choice).
    3. Auto-detect a profile that is signed into a Microsoft/work account.
    4. Fall back to "Default".
"""
import json
import os
import shutil
import tempfile


def get_edge_user_data_dir() -> str:
    """Return the Edge 'User Data' directory for the current Windows user.

    Honours the ``VT_EDGE_USER_DATA`` override, otherwise derives the path from
    %LOCALAPPDATA% so it works regardless of the logged-in user name.
    """
    override = os.environ.get("VT_EDGE_USER_DATA")
    if override:
        return override
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser(r"~\AppData\Local")
    return os.path.join(base, "Microsoft", "Edge", "User Data")


def list_edge_profiles() -> list[dict]:
    """Enumerate Edge profiles from 'Local State' -> profile.info_cache.

    Returns a list of ``{"dir", "name", "email"}`` dicts, with the Default
    profile first. Only profiles whose directory exists on disk are returned.
    """
    user_data = get_edge_user_data_dir()
    local_state = os.path.join(user_data, "Local State")
    profiles: list[dict] = []
    try:
        with open(local_state, encoding="utf-8") as f:
            state = json.load(f)
        info_cache = state.get("profile", {}).get("info_cache", {})
        for dir_name, info in info_cache.items():
            if not os.path.isdir(os.path.join(user_data, dir_name)):
                continue
            profiles.append({
                "dir": dir_name,
                "name": info.get("name") or dir_name,
                "email": info.get("user_name") or info.get("gaia_name") or "",
            })
    except (OSError, ValueError):
        pass

    # Fallback: scan the directory if Local State is missing/unreadable.
    if not profiles and os.path.isdir(user_data):
        try:
            for entry in os.scandir(user_data):
                if entry.is_dir() and (entry.name == "Default" or entry.name.startswith("Profile ")):
                    profiles.append({"dir": entry.name, "name": entry.name, "email": ""})
        except OSError:
            pass

    profiles.sort(key=lambda p: (p["dir"] != "Default", p["dir"]))
    return profiles


def _profile_has_cookies(user_data: str, profile_dir: str) -> bool:
    """Cheap signal that a profile has an active session: a non-empty Cookies DB.

    We do not open the (possibly locked) SQLite file while Edge is running;
    presence + size is enough. Real auth validation happens when the worker
    actually loads Teams.
    """
    for rel in (("Network", "Cookies"), ("Cookies",)):
        path = os.path.join(user_data, profile_dir, *rel)
        try:
            if os.path.isfile(path) and os.path.getsize(path) > 0:
                return True
        except OSError:
            continue
    return False


def detect_signed_in_profile(domain_hint: str = "") -> str:
    """Best-effort pick of the Edge profile most likely signed into Teams.

    Priority:
        1. A profile whose account email contains ``domain_hint`` (if provided).
        2. The first profile with any signed-in account email.
        3. The first profile that has a Cookies DB.
        4. "Default".
    """
    profiles = list_edge_profiles()
    if not profiles:
        return "Default"

    hint = (domain_hint or os.environ.get("VT_EDGE_DOMAIN_HINT") or "").lower().strip()
    if hint:
        for p in profiles:
            if hint in (p.get("email") or "").lower():
                return p["dir"]

    for p in profiles:
        if (p.get("email") or "").strip():
            return p["dir"]

    user_data = get_edge_user_data_dir()
    for p in profiles:
        if _profile_has_cookies(user_data, p["dir"]):
            return p["dir"]

    return profiles[0]["dir"]


def resolve_profile(profile: str | None = None, domain_hint: str = "") -> str:
    """Resolve which Edge profile dir to use.

    Order: explicit arg -> VT_EDGE_PROFILE env -> auto-detect -> "Default".
    """
    chosen = (profile or os.environ.get("VT_EDGE_PROFILE") or "").strip()
    if chosen:
        return chosen
    return detect_signed_in_profile(domain_hint)


def copy_profile_to_temp(profile: str | None = None, domain_hint: str = "") -> tuple[str, str]:
    """Copy the chosen Edge profile's auth files into a fresh temp user-data dir
    so Playwright can use it even while Edge is still running.

    Returns ``(temp_user_data_dir, profile_name_to_use)``. The profile is placed
    under the temp dir as "Default" (Playwright's persistent context uses
    whatever profile dir you point it at).
    """
    user_data = get_edge_user_data_dir()
    profile_dir = resolve_profile(profile, domain_hint)
    src_profile = os.path.join(user_data, profile_dir)

    tmp_dir = tempfile.mkdtemp(prefix="edge_pw_")
    dst_profile = os.path.join(tmp_dir, "Default")
    os.makedirs(dst_profile, exist_ok=True)

    # Copy the files that matter for auth (a full profile copy would be slow).
    for fname in ("Cookies", "Network Persistent State", "Preferences",
                  "Secure Preferences", "Local State"):
        src = os.path.join(src_profile, fname)
        if os.path.isfile(src):
            try:
                shutil.copy2(src, os.path.join(dst_profile, fname))
            except OSError:
                pass  # file locked — skip, auth still works via remaining cookies

    # Newer Edge versions (post-2023) store Cookies under a Network/ subdirectory.
    network_src = os.path.join(src_profile, "Network")
    if os.path.isdir(network_src):
        network_dst = os.path.join(dst_profile, "Network")
        os.makedirs(network_dst, exist_ok=True)
        for fname in os.listdir(network_src):
            src = os.path.join(network_src, fname)
            if os.path.isfile(src):
                try:
                    shutil.copy2(src, os.path.join(network_dst, fname))
                except OSError:
                    pass

    # Modern SPAs (e.g. Teams v2) keep their MSAL auth tokens in Local Storage /
    # IndexedDB, NOT in cookies. Without these the app authenticates but then
    # hangs on its splash screen. Copy them too (skip files locked by a running
    # Edge — enough usually comes through to authenticate).
    for dname in ("Local Storage", "IndexedDB", "Session Storage", "Service Worker"):
        src_dir = os.path.join(src_profile, dname)
        if os.path.isdir(src_dir):
            try:
                shutil.copytree(
                    src_dir, os.path.join(dst_profile, dname),
                    dirs_exist_ok=True, ignore_dangling_symlinks=True,
                )
            except (OSError, shutil.Error):
                pass  # partial copy is fine — locked files are skipped

    # Local State lives one level up (user-data-dir root) and holds the key
    # needed to decrypt cookies.
    local_state_src = os.path.join(user_data, "Local State")
    if os.path.isfile(local_state_src):
        try:
            shutil.copy2(local_state_src, os.path.join(tmp_dir, "Local State"))
        except OSError:
            pass

    return tmp_dir, "Default"
