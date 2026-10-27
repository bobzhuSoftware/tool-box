"""
ServiceNow count worker — run as a subprocess from the server.

Opens ServiceNow list URLs in the shared Edge automation profile (sign in once,
incl. 2FA; the session is then reused) and returns the record count of each list.

Usage:
    python servicenow_worker.py count <url> [<url> ...]
    python servicenow_worker.py count --file <json file with a list of urls>

Progress is written to stdout as:
    STATUS:<message>
    RESULT:{"url","count","source"}                      (one per URL, as soon as it is read)
    DONE:{"results":[{"url","count","source"}]}   (count is null when it failed)
    ERROR:<message>
"""
import asyncio
import io
import json
import os
import re
import sys
import time
from urllib.parse import quote, unquote, urlparse

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

from browser_utils import ensure_automation_profile  # noqa: E402

LOGIN_WAIT_SECS = 300
HEADLESS_SSO_SECS = 30

# Edge drops session-only cookies (Keycloak/ADFS/ServiceNow) when it closes, so keep our own copy.
SESSION_COOKIE_FILE = os.path.join(
    os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "VideoTranscript", "servicenow-session.json"
)
_COOKIE_DOMAINS = ("dsv.com", "service-now.com", "microsoftonline.com")


def status(msg: str) -> None:
    print(f"STATUS:{msg}", flush=True)


def error(msg: str) -> None:
    print(f"ERROR:{msg}", flush=True)


def done(data: dict) -> None:
    print(f"DONE:{json.dumps(data, ensure_ascii=False)}", flush=True)


def parse_list_url(url: str) -> dict:
    """Extract table / encoded query / list title from a Next Experience simplelist URL."""
    path = urlparse(url).path
    m = re.search(r"/simplelist/([^/]+)", path)
    table = m.group(1) if m else ""
    q = re.search(r"/query/(.+)$", path)
    t = re.search(r"/list-title/([^/]+)", path)
    return {
        "table": table,
        "query": unquote(q.group(1)) if q else "",
        "title": unquote(t.group(1)) if t else "",
    }


def _on_servicenow(url: str) -> bool:
    u = (url or "").lower()
    host = urlparse(u).netloc
    return host.endswith("service-now.com") and "/login.do" not in u and "/sso/" not in u


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
    # ADFS skips silent Windows sign-in (WIA) for a "HeadlessChrome" UA and shows a password form.
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
        os.makedirs(os.path.dirname(SESSION_COOKIE_FILE), exist_ok=True)
        with open(SESSION_COOKIE_FILE, "w", encoding="utf-8") as f:
            json.dump(cookies, f)
    except Exception as e:
        status(f"保存登录会话失败：{e}")


async def _restore_session(ctx) -> None:
    try:
        with open(SESSION_COOKIE_FILE, encoding="utf-8") as f:
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


async def _count_one(page, holder: dict, url: str) -> dict:
    info = parse_list_url(url)
    if not info["table"]:
        return {"url": url, "count": None, "source": None, "error": "URL 不是 simplelist 格式，无法解析表名"}

    await _open(page, url)
    if not await _wait_servicenow(page, 60):
        return {"url": url, "count": None, "source": None, "error": "未能进入 ServiceNow 页面（可能需要重新登录）"}

    # Wait for the page to start talking to the API so the session token is known.
    token = None
    for _ in range(30):
        token = holder["token"]
        if not token:
            try:
                token = await page.evaluate("() => window.g_ck || null")
            except Exception:
                token = None
        if token:
            break
        await asyncio.sleep(1)

    try:
        res = await page.evaluate(
            _JS_STATS, {"table": info["table"], "query": info["query"], "token": token}
        )
        if res["status"] == 200:
            count = int(json.loads(res["text"])["result"]["stats"]["count"])
            return {"url": url, "count": count, "source": "rest"}
        status(f"REST 统计接口返回 {res['status']}，改用页面读取…")
    except Exception as e:
        status(f"REST 统计失败（{e}），改用页面读取…")

    # DOM fallback: wait for the badge to render.
    for _ in range(30):
        try:
            n = await page.evaluate(_JS_DOM, info["title"])
        except Exception:
            n = None
        if n is not None:
            return {"url": url, "count": n, "source": "dom"}
        await asyncio.sleep(1)
    return {"url": url, "count": None, "source": None, "error": "无法读取数量"}


async def cmd_count(urls: list[str]) -> None:
    from playwright.async_api import async_playwright

    automation_dir = ensure_automation_profile()
    async with async_playwright() as p:
        status("正在启动浏览器…")
        ctx, page, holder = await _launch(p, automation_dir, headless=True)
        try:
            await _open(page, urls[0])
            status("正在检查 ServiceNow 登录状态…")
            ok = await _wait_servicenow(page, HEADLESS_SSO_SECS)
            if not ok:
                await ctx.close()
                status("需要登录：正在打开 Edge 窗口，请在该窗口完成登录（含 2FA），之后会自动记住…")
                ctx, page, holder = await _launch(p, automation_dir, headless=False)
                await _open(page, urls[0])
                ok = await _wait_servicenow(page, LOGIN_WAIT_SECS)
                if not ok:
                    error("登录超时，请重试并在弹出的 Edge 窗口中完成登录")
                    return
                status("登录成功，继续…")
                await asyncio.sleep(3)
                await _save_session(ctx)

            results = []
            for i, url in enumerate(urls, 1):
                status(f"正在读取 ({i}/{len(urls)})…")
                res = await _count_one(page, holder, url)
                results.append(res)
                print(f"RESULT:{json.dumps(res, ensure_ascii=False)}", flush=True)
            await _save_session(ctx)
            done({"results": results})
        finally:
            try:
                await ctx.close()
            except Exception:
                pass


def main() -> None:
    if len(sys.argv) < 3 or sys.argv[1] != "count":
        error("Usage: servicenow_worker.py count <url> [<url> ...] | count --file <json>")
        sys.exit(2)
    try:
        if sys.argv[2] == "--file":
            with open(sys.argv[3], encoding="utf-8") as f:
                urls = json.load(f)
        else:
            urls = sys.argv[2:]
        if not urls:
            error("没有需要读取的链接")
            sys.exit(2)
        asyncio.run(cmd_count(urls))
    except Exception as e:
        error(f"{type(e).__name__}: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
