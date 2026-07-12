"""Cookie resolution for yt-dlp (YouTube/Bilibili) + at-rest secret encryption."""
import http.cookiejar
import os
import tempfile

from app.core.config import REPO_ROOT, SECRET_KEY
from app.core.settings import _get_user_setting

# ---------------------------------------------------------------------------
# Cookie resolution (YouTube bot-detection bypass)
# ---------------------------------------------------------------------------
_COOKIES_FILE = os.path.join(REPO_ROOT, "cookies.txt")
_COOKIES_BROWSER = os.environ.get("YOUTUBE_COOKIES_BROWSER")  # e.g. "chrome", "firefox"

# Browsers yt-dlp can read cookies from directly (used by the per-user picker).
_SUPPORTED_COOKIE_BROWSERS = ("chrome", "edge", "firefox", "brave", "chromium", "opera", "vivaldi")


# JavaScript runtimes yt-dlp uses to solve YouTube's signature / n-challenge.
# Without one, YouTube returns only storyboard images and downloads fail with
# "Requested format is not available". Deno is yt-dlp's built-in default; we also
# enable Node (commonly installed) as a fallback so it works on machines without
# Deno. Requires the yt-dlp-ejs scripts (installed via the yt-dlp[default] extra).
# Override with VT_JS_RUNTIMES, e.g. "deno,node" or "node:/path/to/node".
def _parse_js_runtimes(spec: str) -> dict:
    """Parse a 'name[:path],name[:path]' spec into yt-dlp's {name: {config}} dict."""
    runtimes: dict = {}
    for entry in spec.split(","):
        entry = entry.strip()
        if not entry:
            continue
        name, _, path = entry.partition(":")
        name = name.strip().lower()
        path = path.strip()
        if name:
            runtimes[name] = {"path": path} if path else {}
    return runtimes


_JS_RUNTIMES = _parse_js_runtimes(os.environ.get("VT_JS_RUNTIMES", "deno,node"))


def _secret_fernet():
    """Build a Fernet cipher from SECRET_KEY for encrypting stored cookies.

    Returns None if the cryptography backend is unavailable, in which case the
    caller falls back to plaintext storage (same as discord_token).
    """
    try:
        import base64
        import hashlib
        from cryptography.fernet import Fernet

        key = base64.urlsafe_b64encode(hashlib.sha256(SECRET_KEY.encode()).digest())
        return Fernet(key)
    except Exception:
        return None


def _encrypt_secret(text: str) -> str:
    """Encrypt sensitive text for at-rest storage (prefixed 'enc:')."""
    f = _secret_fernet()
    if f is None:
        return text
    try:
        return "enc:" + f.encrypt(text.encode("utf-8")).decode("ascii")
    except Exception:
        return text


def _decrypt_secret(stored: str | None) -> str:
    """Inverse of _encrypt_secret; tolerates legacy plaintext values."""
    if not stored:
        return ""
    if not stored.startswith("enc:"):
        return stored  # legacy plaintext
    f = _secret_fernet()
    if f is None:
        return ""
    try:
        return f.decrypt(stored[4:].encode("ascii")).decode("utf-8")
    except Exception:
        return ""


def _jar_from_text(text: str):
    """Parse pasted Netscape-format cookies.txt into a read-only cookie jar.

    Returns a MozillaCookieJar (with save disabled) or None if the text is not
    valid Netscape cookie data.
    """
    if not text or ("# Netscape" not in text and "\t" not in text):
        return None
    tmp = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8")
    try:
        tmp.write(text)
        tmp.close()
        jar = http.cookiejar.MozillaCookieJar()
        jar.load(tmp.name, ignore_discard=True, ignore_expires=True)
        jar.save = lambda *a, **kw: None  # never write back
        return jar
    except (OSError, http.cookiejar.LoadError):
        return None
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass


def _parse_browser_spec(spec: str) -> tuple:
    """Turn 'chrome' or 'chrome:Default' into yt-dlp's cookiesfrombrowser tuple."""
    browser, _, profile = spec.partition(":")
    return (browser.strip().lower(), profile.strip() or None, None, None)


def _summarize_cookies(text: str) -> dict:
    """Return non-sensitive metadata about a cookies.txt blob for the UI."""
    jar = _jar_from_text(text)
    if jar is None:
        return {"cookie_count": 0, "domains": []}
    domains = sorted({c.domain.lstrip(".") for c in jar})
    return {"cookie_count": len(list(jar)), "domains": domains}


def _apply_cookies(ydl_opts: dict, user_id: str | None = None) -> None:
    """Inject cookie configuration into yt-dlp options if available.

    Resolution order:
      1. The user's pasted cookies.txt (encrypted per-user setting).
      2. The user's chosen local browser (per-user setting).
      3. Legacy global cookies.txt in the project root.
      4. Legacy global YOUTUBE_COOKIES_BROWSER env var.

    Pasted cookies are loaded into a MozillaCookieJar with its save method
    disabled so yt-dlp can read but never write them (avoids Windows
    Permission Denied on write-back).
    """
    # Enable JS runtimes so yt-dlp can solve YouTube's n-challenge / signature
    # (otherwise only storyboard images are returned and audio extraction fails).
    if _JS_RUNTIMES:
        ydl_opts.setdefault("js_runtimes", {k: dict(v) for k, v in _JS_RUNTIMES.items()})

    # 1 & 2: per-user configuration
    if user_id:
        text = _decrypt_secret(_get_user_setting(user_id, "yt_cookies"))
        if text:
            jar = _jar_from_text(text)
            if jar is not None:
                ydl_opts["cookiejar"] = jar
                return
        browser = _get_user_setting(user_id, "yt_cookies_browser")
        if browser:
            ydl_opts["cookiesfrombrowser"] = _parse_browser_spec(browser)
            return

    # 3 & 4: legacy global fallbacks (keep existing deployments working)
    if os.path.isfile(_COOKIES_FILE):
        try:
            jar = http.cookiejar.MozillaCookieJar()
            jar.load(_COOKIES_FILE, ignore_discard=True, ignore_expires=True)
            # Disable save so yt-dlp can never trigger a write
            jar.save = lambda *a, **kw: None
            ydl_opts["cookiejar"] = jar
        except OSError:
            pass  # Skip cookies if we can't read the file
    elif _COOKIES_BROWSER:
        ydl_opts["cookiesfrombrowser"] = (_COOKIES_BROWSER,)
