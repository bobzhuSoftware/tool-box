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
import re
import shutil
import tempfile


def _robust_copy(src: str, dst: str) -> bool:
    """Copy ``src`` to ``dst``, even when the source is locked by a running
    browser (e.g. Edge keeps its ``Cookies`` SQLite file open *for writing*).

    A plain ``shutil.copy2`` opens the source denying write-sharing, so it fails
    with "being used by another process" while Edge is open — which silently
    leaves the temp profile with no cookies and forces a re-login. On Windows we
    retry by opening the file with ``FILE_SHARE_READ|WRITE|DELETE`` via
    ``CreateFileW`` so the read can coexist with Edge's open handle.

    Returns ``True`` on success, ``False`` if the file could not be copied.
    """
    try:
        shutil.copy2(src, dst)
        return True
    except OSError:
        pass

    if os.name != "nt":
        return False

    import ctypes
    from ctypes import wintypes

    GENERIC_READ = 0x80000000
    FILE_SHARE_READ = 0x1
    FILE_SHARE_WRITE = 0x2
    FILE_SHARE_DELETE = 0x4
    OPEN_EXISTING = 3
    FILE_ATTRIBUTE_NORMAL = 0x80
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    CreateFileW = ctypes.windll.kernel32.CreateFileW
    CreateFileW.restype = wintypes.HANDLE
    CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]

    handle = CreateFileW(
        src, GENERIC_READ,
        FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
        None, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, None,
    )
    if not handle or handle == INVALID_HANDLE_VALUE:
        return False

    try:
        import msvcrt
        fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
        # fd now owns the handle; closing the stream closes it.
        with os.fdopen(fd, "rb", closefd=True) as fsrc, open(dst, "wb") as fdst:
            shutil.copyfileobj(fsrc, fdst)
        return True
    except OSError:
        try:
            ctypes.windll.kernel32.CloseHandle(handle)
        except Exception:
            pass
        return False



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


def get_automation_user_data_dir() -> str:
    """Return a dedicated, persistent Edge 'user data' dir for automation.

    Modern Edge holds its ``Cookies`` SQLite file with an *exclusive* lock while
    running, so copying a live profile's cookies (the old approach) no longer
    works. Instead we drive a separate, persistent Edge profile that the user
    signs into once; the session is then reused on every subsequent run while
    their normal Edge stays open and untouched.

    The automation profile is scoped to the *selected* Edge profile (via the
    ``VT_EDGE_PROFILE`` env var). Without this scoping a single shared session
    is reused for every account, so whichever account was signed into first
    "wins" and switching profiles in the UI has no effect. Keying the directory
    on the chosen profile gives each account its own persistent session.

    Honours the ``VT_EDGE_AUTOMATION_DIR`` override.
    """
    override = os.environ.get("VT_EDGE_AUTOMATION_DIR")
    if override:
        os.makedirs(override, exist_ok=True)
        return override
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser(r"~\AppData\Local")
    path = os.path.join(base, "VideoTranscript", "edge-automation")

    # Scope to the selected Edge profile so each account keeps a separate
    # signed-in session instead of silently reusing the first one used.
    profile_key = (os.environ.get("VT_EDGE_PROFILE") or "").strip()
    if profile_key:
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", profile_key)
        path = os.path.join(path, safe)

    os.makedirs(path, exist_ok=True)
    return path



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
            _robust_copy(src, os.path.join(dst_profile, fname))

    # Newer Edge versions (post-2023) store Cookies under a Network/ subdirectory.
    network_src = os.path.join(src_profile, "Network")
    if os.path.isdir(network_src):
        network_dst = os.path.join(dst_profile, "Network")
        os.makedirs(network_dst, exist_ok=True)
        for fname in os.listdir(network_src):
            src = os.path.join(network_src, fname)
            if os.path.isfile(src):
                _robust_copy(src, os.path.join(network_dst, fname))

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
        _robust_copy(local_state_src, os.path.join(tmp_dir, "Local State"))

    return tmp_dir, "Default"


def automation_profile_signed_in() -> bool:
    """True if the dedicated automation profile already has a cookie DB (i.e. the
    user has signed in there at least once)."""
    auto = get_automation_user_data_dir()
    for rel in (("Default", "Network", "Cookies"), ("Default", "Cookies")):
        path = os.path.join(auto, *rel)
        try:
            if os.path.isfile(path) and os.path.getsize(path) > 0:
                return True
        except OSError:
            continue
    return False


def ensure_automation_profile(profile: str | None = None, domain_hint: str = "") -> str:
    """Prepare the persistent Edge automation profile and return its user-data dir.

    A *bare* Edge profile triggers the first-run/welcome experience (which loads
    portal.office.com in a helper web-contents and crashes Playwright with
    "Browser window not found"). To avoid that we seed the automation profile
    once with the user's real-profile **config** files — ``Preferences``,
    ``Secure Preferences`` and the root ``Local State`` — which mark first-run as
    done and carry the cookie-encryption key. The exclusively-locked ``Cookies``
    DB is deliberately *not* copied: the user signs in once in a visible window
    and the session is then persisted here for reuse.

    Seeding is skipped entirely once the profile has its own cookies.
    """
    auto = get_automation_user_data_dir()
    if automation_profile_signed_in():
        return auto

    user_data = get_edge_user_data_dir()
    src_profile = os.path.join(user_data, resolve_profile(profile, domain_hint))
    dst_profile = os.path.join(auto, "Default")
    os.makedirs(dst_profile, exist_ok=True)

    for fname in ("Preferences", "Secure Preferences", "Network Persistent State"):
        src = os.path.join(src_profile, fname)
        if os.path.isfile(src):
            _robust_copy(src, os.path.join(dst_profile, fname))

    local_state_src = os.path.join(user_data, "Local State")
    if os.path.isfile(local_state_src):
        _robust_copy(local_state_src, os.path.join(auto, "Local State"))

    return auto



# ---------------------------------------------------------------------------
# Firefox profile utilities (used by the Web→PDF X/Twitter mode)
#
# Firefox stores its profile list in profiles.ini (not a JSON Local State like
# Edge), and does not record account emails. We instead surface each profile's
# name and whether its cookies.sqlite already holds an x.com / twitter.com login
# so the user can tell which profile is signed into X.
# ---------------------------------------------------------------------------
def get_firefox_profiles_root() -> str:
    """Return the Firefox 'Profiles' directory for the current Windows user.

    Honours the ``VT_FIREFOX_PROFILES`` override, otherwise derives it from
    %APPDATA% so it works regardless of the logged-in user name.
    """
    override = os.environ.get("VT_FIREFOX_PROFILES")
    if override:
        return override
    appdata = os.environ.get("APPDATA") or os.path.expanduser(r"~\AppData\Roaming")
    return os.path.join(appdata, "Mozilla", "Firefox", "Profiles")


def _firefox_profile_has_x_login(profile_path: str) -> bool:
    """True if the profile's cookies.sqlite holds an x.com/twitter.com cookie.

    Copies the DB **plus its -wal/-shm sidecars** first, because a running
    Firefox keeps recent logins in the write-ahead log that hasn't been merged
    into the main file yet — without them a fresh login looks absent. Matching is
    done on the ``host`` column (Firefox dropped the older ``baseDomain`` column),
    using suffix matches so unrelated hosts like ``twittervideodownloader.com``
    don't count. Any failure is treated as "unknown" (False) and never raises.
    """
    import sqlite3

    db = os.path.join(profile_path, "cookies.sqlite")
    if not os.path.isfile(db):
        return False
    tmpdir = tempfile.mkdtemp(prefix="vt_ff_xcheck_")
    try:
        for suffix in ("", "-wal", "-shm"):
            src = db + suffix
            if os.path.isfile(src):
                try:
                    shutil.copy2(src, os.path.join(tmpdir, "cookies.sqlite" + suffix))
                except OSError:
                    pass
        conn = sqlite3.connect(os.path.join(tmpdir, "cookies.sqlite"))
        try:
            row = conn.execute(
                "SELECT 1 FROM moz_cookies WHERE "
                "host = 'x.com' OR host LIKE '%.x.com' "
                "OR host = 'twitter.com' OR host LIKE '%.twitter.com' LIMIT 1"
            ).fetchone()
        finally:
            conn.close()
        return row is not None
    except Exception:
        return False
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def list_firefox_profiles() -> list[dict]:
    """Enumerate Firefox profiles from profiles.ini.

    Returns ``[{"dir", "name", "has_x"}]`` where ``dir`` is the profile folder
    name under the Profiles root. Profiles already signed into X are listed
    first, then default-release, then the rest.
    """
    import configparser

    root = get_firefox_profiles_root()
    ini = os.path.join(os.path.dirname(root), "profiles.ini")
    profiles: list[dict] = []
    seen: set[str] = set()

    cfg = configparser.ConfigParser()
    try:
        cfg.read(ini, encoding="utf-8")
        for sec in cfg.sections():
            if not sec.startswith("Profile"):
                continue
            path = cfg.get(sec, "Path", fallback="").replace("/", os.sep)
            if not path:
                continue
            is_rel = cfg.get(sec, "IsRelative", fallback="1") == "1"
            full = os.path.join(os.path.dirname(root), path) if is_rel else path
            folder = os.path.basename(full.rstrip("\\/"))
            if not os.path.isdir(full) or folder in seen:
                continue
            seen.add(folder)
            profiles.append({
                "dir": folder,
                "name": cfg.get(sec, "Name", fallback=folder),
                "has_x": _firefox_profile_has_x_login(full),
            })
    except (OSError, configparser.Error):
        pass

    # Fallback: scan the Profiles directory if profiles.ini is missing/unreadable.
    if not profiles and os.path.isdir(root):
        try:
            for entry in os.scandir(root):
                if entry.is_dir() and entry.name not in seen:
                    profiles.append({
                        "dir": entry.name,
                        "name": entry.name,
                        "has_x": _firefox_profile_has_x_login(entry.path),
                    })
        except OSError:
            pass

    profiles.sort(key=lambda p: (
        not p["has_x"],
        "default-release" not in p["dir"],
        p["dir"],
    ))
    return profiles


def resolve_firefox_profile(profile: str | None = None) -> str:
    """Resolve which Firefox profile to use; returns its absolute path ("" if none).

    Order: explicit arg -> VT_FIREFOX_PROFILE env -> a profile signed into X ->
    a "default-release" profile -> the first available profile.
    """
    root = get_firefox_profiles_root()
    chosen = (profile or os.environ.get("VT_FIREFOX_PROFILE") or "").strip()
    if chosen:
        cand = chosen if os.path.isabs(chosen) else os.path.join(root, chosen)
        if os.path.isdir(cand):
            return cand

    profiles = list_firefox_profiles()
    for p in profiles:
        if p["has_x"]:
            return os.path.join(root, p["dir"])
    for p in profiles:
        if "default-release" in p["dir"]:
            return os.path.join(root, p["dir"])
    return os.path.join(root, profiles[0]["dir"]) if profiles else ""
