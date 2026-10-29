"""
ServiceNow count worker — run as a subprocess from the server.

Opens ServiceNow list URLs in an Edge automation profile (sign in once, incl. 2FA; the
session is then reused) and returns the record count of each list.

Usage:
    python servicenow_worker.py count <url> [<url> ...]
    python servicenow_worker.py count --file <json file with a list of urls>
    python servicenow_worker.py login

Environment (set per login identity by the server):
    VT_EDGE_AUTOMATION_DIR   Edge user-data dir for this identity
    VT_SN_SESSION_FILE       cookie backup file for this identity

Progress is written to stdout as:
    STATUS:<message>
    STAGE:check|window|working             (check: silent check of the saved login; window: user must
                                            sign in in the visible Edge window; working: signed in)
    RESULT:{"url","count","source"[,"unconfirmed"]}   (count: one per URL, as soon as it is read;
                                            unconfirmed: REST said 0 and the page could not confirm it)
    IDENTITY:{"user_name","display_name"}  (who the session is logged in as)
    DONE:{...}                             (count: {"results":[...]}; login: {"user_name",...})
    ERROR:<message>
"""
import asyncio
import io
import json
import os
import re
import shutil
import sys
import time
from urllib.parse import parse_qs, unquote, urlparse

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

from browser_utils import (  # noqa: E402
    _robust_copy, ensure_automation_profile, get_automation_user_data_dir,
)

LOGIN_WAIT_SECS = 300
HEADLESS_SSO_SECS = 30
HOME_URL = "https://dsv.service-now.com/now/nav/ui/home"

_LEGACY_SESSION_FILE = os.path.join(
    os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "VideoTranscript", "servicenow-session.json"
)
_COOKIE_DOMAINS = ("dsv.com", "service-now.com", "microsoftonline.com")


def status(msg: str) -> None:
    print(f"STATUS:{msg}", flush=True)


def stage(name: str) -> None:
    print(f"STAGE:{name}", flush=True)


def error(msg: str) -> None:
    print(f"ERROR:{msg}", flush=True)


def done(data: dict) -> None:
    print(f"DONE:{json.dumps(data, ensure_ascii=False)}", flush=True)


def _classic_target(url: str) -> str:
    """Return '<table>_list.do[?params]' from a classic list URL, or ''.

    Handles /now/nav/ui/classic/params/target/<double-encoded target>, nav_to.do?uri=..., and
    a direct https://host/<table>_list.do?sysparm_query=... link.
    """
    p = urlparse(url)
    m = re.search(r"/target/(.+)$", unquote(p.path))
    if m:
        return m.group(1)
    if p.path.endswith("/nav_to.do"):
        return parse_qs(p.query).get("uri", [""])[0]
    if re.search(r"[^/]+_list\.do$", p.path):
        return p.path.rsplit("/", 1)[1] + (f"?{p.query}" if p.query else "")
    return ""


def parse_list_url(url: str) -> dict:
    """Extract table / encoded query / list title from a Next Experience simplelist URL or a
    classic list URL (<table>_list.do?sysparm_query=...)."""
    path = urlparse(url).path
    m = re.search(r"/simplelist/([^/]+)", path)
    if m:
        q = re.search(r"/query/(.+)$", path)
        t = re.search(r"/list-title/([^/]+)", path)
        return {
            "table": m.group(1),
            "query": unquote(q.group(1)) if q else "",
            "title": unquote(t.group(1)) if t else "",
        }

    target = _classic_target(url)
    tm = re.match(r"/?([A-Za-z0-9_]+)_list\.do(?:\?(.*))?$", target)
    if tm:
        query = parse_qs(tm.group(2) or "").get("sysparm_query", [""])[0]
        return {"table": tm.group(1), "query": query, "title": ""}
    return {"table": "", "query": "", "title": ""}


def _on_servicenow(url: str) -> bool:
    u = (url or "").lower()
    host = urlparse(u).netloc
    # auth_redirect.do is the transient hop that bounces to the SSO, not a signed-in page.
    return (host.endswith("service-now.com") and "/login.do" not in u
            and "/sso/" not in u and "/auth_redirect.do" not in u)


# Read the count straight from ServiceNow's aggregate API using the page's own session.
_JS_STATS = """async ({table, query, token}) => {
    const qs = new URLSearchParams({sysparm_count: 'true'});
    if (query) qs.set('sysparm_query', query);
    const headers = {'Accept': 'application/json'};
    if (token) headers['X-UserToken'] = token;
    const r = await fetch(`/api/now/stats/${table}?${qs.toString()}`, {headers, credentials: 'same-origin'});
    const text = await r.text();
    return {status: r.status, text};
}"""

_JS_IDENTITY = """async (token) => {
    const headers = {'Accept': 'application/json'};
    if (token) headers['X-UserToken'] = token;
    const r = await fetch('/api/now/ui/user/current_user', {headers, credentials: 'same-origin'});
    if (!r.ok) return null;
    const u = (await r.json()).result || {};
    return {user_name: u.user_name || '', display_name: u.user_display_name || ''};
}"""

# Classic list (inside the gsft_main frame): the list table carries the total row count.
_JS_CLASSIC = """() => {
    const t = document.querySelector('table[grand_total_rows]');
    if (t) {
        const n = parseInt(t.getAttribute('grand_total_rows'), 10);
        if (!isNaN(n)) return n;
    }
    const m = document.body && document.body.innerText.match(/\\d[\\d,]*\\s*to\\s*\\d[\\d,]*\\s*of\\s*([\\d,]+)/i);
    if (m) return parseInt(m[1].replace(/,/g, ''), 10);
    return null;
}"""

# Fallback: find the count badge after the list title, piercing shadow roots.
_JS_DOM = """(title) => {
    const els = [];
    const walk = (root) => {
        for (const el of root.querySelectorAll('*')) {
            els.push(el);
            if (el.shadowRoot) walk(el.shadowRoot);
        }
    };
    walk(document);
    const own = (el) => Array.from(el.childNodes).filter(n => n.nodeType === 3)
        .map(n => n.textContent.trim()).join(' ').trim();
    const i = els.findIndex(el => title && own(el).includes(title));
    if (i < 0) return null;
    for (let j = i; j < Math.min(els.length, i + 25); j++) {
        const t = own(els[j]);
        if (/^\\d+$/.test(t)) return parseInt(t, 10);
    }
    return null;
}"""


def _session_file() -> str:
    # Edge drops session-only cookies (Keycloak/ADFS/ServiceNow) on exit, so keep our own copy.
    return os.environ.get("VT_SN_SESSION_FILE") or _LEGACY_SESSION_FILE


# Browser-level sign-in state. Edge's "force sign-in" policy shows a sign-in page (which breaks
# Playwright: "Browser window not found") whenever a profile has no account, and a headless run
# signs a fresh profile out. Site cookies/storage are deliberately NOT cloned.
_SIGNIN_FILES = [
    ("Default", "Preferences"), ("Default", "Secure Preferences"),
    ("Default", "Web Data"), ("Default", "Web Data-journal"), ("Local State",),
]


def _has_account(user_data_dir: str) -> bool:
    try:
        with open(os.path.join(user_data_dir, "Default", "Preferences"), encoding="utf-8") as f:
            return bool(json.load(f).get("account_info"))
    except (OSError, ValueError):
        return False


def _shared_automation_dir() -> str:
    """The Edge automation profile shared with the Teams tools (ignoring this identity's override)."""
    saved = os.environ.pop("VT_EDGE_AUTOMATION_DIR", None)
    try:
        return get_automation_user_data_dir()
    finally:
        if saved is not None:
            os.environ["VT_EDGE_AUTOMATION_DIR"] = saved


def _prepare_profile(slot_dir: str, force: bool = False) -> None:
    """Give an identity profile the browser sign-in state of the shared automation profile."""
    if _has_account(slot_dir) and not force:
        return
    template = _shared_automation_dir()
    if os.path.abspath(template) == os.path.abspath(slot_dir) or not _has_account(template):
        return
    for rel in _SIGNIN_FILES:
        src = os.path.join(template, *rel)
        if os.path.isfile(src):
            os.makedirs(os.path.dirname(os.path.join(slot_dir, *rel)), exist_ok=True)
            _robust_copy(src, os.path.join(slot_dir, *rel))
    one_auth = os.path.join(template, "OneAuth")
    if os.path.isdir(one_auth):
        shutil.copytree(one_auth, os.path.join(slot_dir, "OneAuth"), dirs_exist_ok=True)


async def _launch(p, automation_dir: str, headless: bool):
    # Modern headless via arg (legacy flag crashes persistent contexts);
    # --profile-directory=Default shares the session with the other Edge workers.
    args = ["--no-first-run", "--no-default-browser-check", "--profile-directory=Default",
            "--disable-blink-features=AutomationControlled"]
    if headless:
        args.append("--headless=new")
    ctx = await p.chromium.launch_persistent_context(
        user_data_dir=automation_dir, channel="msedge", headless=False, args=args,
        ignore_default_args=["--enable-automation"],
    )
    page = ctx.pages[0] if ctx.pages else await ctx.new_page()
    # Present as a normal Edge: with a "HeadlessChrome" UA, ADFS rejected an already-valid session.
    cdp = await ctx.new_cdp_session(page)
    ua = (await cdp.send("Browser.getVersion"))["userAgent"].replace("HeadlessChrome", "Chrome")
    await cdp.send("Emulation.setUserAgentOverride", {"userAgent": ua})
    holder = {"token": None}

    def on_request(req):
        tok = req.headers.get("x-usertoken")
        if tok:
            holder["token"] = tok

    page.on("request", on_request)
    await _restore_session(ctx)
    return ctx, page, holder


async def _save_session(ctx) -> None:
    try:
        cookies = [c for c in await ctx.cookies() if any(d in c["domain"] for d in _COOKIE_DOMAINS)]
        path = _session_file()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cookies, f)
    except Exception as e:
        status(f"保存登录会话失败：{e}")


async def _restore_session(ctx) -> None:
    try:
        with open(_session_file(), encoding="utf-8") as f:
            cookies = json.load(f)
    except Exception:
        return
    now = time.time()
    live = [c for c in cookies if c.get("expires", -1) == -1 or c["expires"] > now]
    if live:
        try:
            await ctx.add_cookies(live)
        except Exception as e:
            status(f"恢复登录会话失败：{e}")


async def _open(page, url: str) -> None:
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
    except Exception:
        pass  # redirects to SSO may abort the first navigation


async def _wait_servicenow(page, secs: int) -> bool:
    for _ in range(secs):
        if _on_servicenow(page.url):
            return True
        await asyncio.sleep(1)
    return False


async def _get_token(page, holder: dict):
    """Wait for the page's session token (X-UserToken) so in-page REST calls are accepted."""
    for _ in range(30):
        token = await _page_token(page) or holder["token"]
        if token:
            return token
        await asyncio.sleep(1)
    return None


async def _page_token(page):
    try:
        return await page.evaluate("() => window.g_ck || null")
    except Exception:
        return None


async def _identity(page, holder, tries: int = 30):
    """Read the signed-in user. Right after login the captured token can be a stale pre-login one
    (-> 401), so re-read the token from the page and retry until the session is usable."""
    for _ in range(tries):
        token = await _page_token(page) or holder["token"]
        try:
            ident = await page.evaluate(_JS_IDENTITY, token)
        except Exception:
            ident = None
        if ident:
            return ident
        await asyncio.sleep(1)
    return None


async def _ensure_login(p, automation_dir: str, first_url: str):
    """Return (ctx, page, holder) once signed in to ServiceNow, or None on login timeout."""
    # Headless needs a saved session and the browser sign-in state, otherwise it would fail or sign
    # the profile out; in those cases go straight to the visible window. A visible run leaves the
    # sign-in state in a shape headless Edge rejects, so refresh it from the shared profile first.
    if os.path.isfile(_session_file()):
        _prepare_profile(automation_dir, force=True)
    if os.path.isfile(_session_file()) and _has_account(automation_dir):
        stage("check")
        ctx, page, holder = await _launch(p, automation_dir, headless=True)
        await _open(page, first_url)
        status("正在检查 ServiceNow 登录状态…")
        if await _wait_servicenow(page, HEADLESS_SSO_SECS):
            stage("working")
            return ctx, page, holder
        await ctx.close()

    stage("window")
    status("需要登录：正在打开 Edge 窗口…")
    ctx, page, holder = await _launch(p, automation_dir, headless=False)
    await _open(page, first_url)
    if not await _wait_servicenow(page, LOGIN_WAIT_SECS):
        await ctx.close()
        return None
    stage("working")
    status("登录成功，继续…")
    await asyncio.sleep(3)
    await _save_session(ctx)
    return ctx, page, holder


async def _capture_list_count(resp, captured: list) -> None:
    """Record the total of every Next Experience list the page loads.

    The list loads through GraphQL (operation nowRecordListConnected); layoutQuery.count is the
    number the page shows, and it honours conditions the REST stats API cannot read (e.g. sys_tags).
    """
    if "/api/now/graphql" not in resp.url:
        return
    try:
        req = json.loads(resp.request.post_data or "")
        body = await resp.json()
        reqs = req if isinstance(req, list) else [req]
        bodies = body if isinstance(body, list) else [body]
        for rq, bd in zip(reqs, bodies):
            if rq.get("operationName") != "nowRecordListConnected":
                continue
            count = bd["data"]["GlideListLayout_Query"]["getListLayout"]["layoutQuery"]["count"]
            variables = rq.get("variables") or {}
            captured.append({"table": variables.get("table"), "query": variables.get("query") or "", "count": int(count)})
    except Exception:
        return


async def _count_one(page, holder: dict, url: str) -> dict:
    info = parse_list_url(url)
    if not info["table"]:
        return {"url": url, "count": None, "source": None, "error": "URL 无法解析表名（需要 simplelist 或经典 _list.do 链接）"}

    captured: list = []
    handler = None
    if info["title"]:
        handler = lambda r: asyncio.create_task(_capture_list_count(r, captured))  # noqa: E731
        page.on("response", handler)
    try:
        return await _count_page(page, holder, url, info, captured)
    finally:
        if handler:
            page.remove_listener("response", handler)


async def _count_page(page, holder: dict, url: str, info: dict, captured: list) -> dict:
    await _open(page, url)
    if not await _wait_servicenow(page, 60):
        return {"url": url, "count": None, "source": None, "error": "未能进入 ServiceNow 页面（可能需要重新登录）"}

    token = await _get_token(page, holder)

    rest_count = None
    rest_err = ""
    try:
        res = await page.evaluate(
            _JS_STATS, {"table": info["table"], "query": info["query"], "token": token}
        )
        if res["status"] == 200:
            rest_count = int(json.loads(res["text"])["result"]["stats"]["count"])
            if rest_count > 0:
                return {"url": url, "count": rest_count, "source": "rest"}
            # The stats API silently returns 0 when the query uses a field the API user can't read
            # (e.g. sys_tags), so a 0 is re-checked against what the page itself shows.
            status("REST 返回 0，用页面再确认…")
        else:
            status(f"REST 统计接口返回 {res['status']}，改用页面读取…")
            rest_err = f"REST 返回 {res['status']}"
    except Exception as e:
        status(f"REST 统计失败（{e}），改用页面读取…")
        rest_err = f"REST 失败：{e}"

    n = await _read_page_count(page, info, captured, tries=30 if rest_count is None else 15)
    if n is not None:
        return {"url": url, "count": n, "source": "dom"}
    if rest_count is not None:
        # A REST 0 the page could not confirm may be wrong (e.g. an unreadable sys_tags condition).
        return {"url": url, "count": rest_count, "source": "rest", "unconfirmed": rest_count == 0}
    return {"url": url, "count": None, "source": None, "error": rest_err or "无法读取数量"}


async def _read_page_count(page, info: dict, captured: list, tries: int):
    """Read the count the page itself shows: classic lists (no title) via the gsft_main frame;
    Next Experience lists from the page's own GraphQL response, else the badge next to the title."""
    for _ in range(tries):
        n = None
        try:
            if info["title"]:
                same_table = [c for c in captured if c["table"] == info["table"]]
                exact = [c for c in same_table if c["query"] == info["query"]]
                if exact or same_table:
                    return (exact or same_table)[-1]["count"]
                n = await page.evaluate(_JS_DOM, info["title"])
            else:
                for fr in page.frames:
                    if fr.name == "gsft_main" or "_list.do" in fr.url:
                        n = await fr.evaluate(_JS_CLASSIC)
                        if n is not None:
                            break
        except Exception:
            n = None
        if n is not None:
            return n
        await asyncio.sleep(1)
    return None


async def cmd_count(urls: list[str]) -> None:
    from playwright.async_api import async_playwright

    automation_dir = ensure_automation_profile()
    _prepare_profile(automation_dir)
    async with async_playwright() as p:
        status("正在准备…")
        session = await _ensure_login(p, automation_dir, urls[0])
        if not session:
            error("登录超时，请重试并在弹出的 Edge 窗口中完成登录")
            return
        ctx, page, holder = session
        try:
            results = []
            for i, url in enumerate(urls, 1):
                status(f"正在读取 ({i}/{len(urls)})…")
                res = await _count_one(page, holder, url)
                results.append(res)
                print(f"RESULT:{json.dumps(res, ensure_ascii=False)}", flush=True)

            ident = await _identity(page, holder, tries=5)
            if ident:
                print(f"IDENTITY:{json.dumps(ident, ensure_ascii=False)}", flush=True)
            await _save_session(ctx)
            done({"results": results})
        finally:
            try:
                await ctx.close()
            except Exception:
                pass


async def cmd_login() -> None:
    from playwright.async_api import async_playwright

    automation_dir = ensure_automation_profile()
    _prepare_profile(automation_dir)
    async with async_playwright() as p:
        status("正在准备…")
        session = await _ensure_login(p, automation_dir, HOME_URL)
        if not session:
            error("登录超时，请重试并在弹出的 Edge 窗口中完成登录")
            return
        ctx, page, holder = session
        try:
            status("正在读取用户信息…")
            ident = await _identity(page, holder)
            if not ident:
                error("已登录，但无法读取当前用户信息")
                return
            print(f"IDENTITY:{json.dumps(ident, ensure_ascii=False)}", flush=True)
            await _save_session(ctx)
            done(ident)
        finally:
            try:
                await ctx.close()
            except Exception:
                pass


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        if cmd == "login":
            asyncio.run(cmd_login())
        elif cmd == "count" and len(sys.argv) >= 3:
            if sys.argv[2] == "--file":
                with open(sys.argv[3], encoding="utf-8") as f:
                    urls = json.load(f)
            else:
                urls = sys.argv[2:]
            if not urls:
                error("没有需要读取的链接")
                sys.exit(2)
            asyncio.run(cmd_count(urls))
        else:
            error("Usage: servicenow_worker.py count <url>... | count --file <json> | login")
            sys.exit(2)
    except Exception as e:
        error(f"{type(e).__name__}: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
