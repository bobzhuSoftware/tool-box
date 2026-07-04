"""
Microsoft 365 Copilot Chat Export Worker — run as a subprocess from server.py.

Drives the *web* version of Microsoft 365 Copilot (m365.cloud.microsoft / BizChat)
using the user's already-signed-in Edge automation profile (shared with the Teams
workers — sign in once, reused everywhere), navigates to a single conversation URL,
scrolls the message pane to the top to load the full history, scrapes every
user prompt + Copilot response, and writes the result to HTML or TXT.

Why scrape the DOM instead of the Graph API: the official
``aiInteractionHistory: getAllEnterpriseInteractions`` endpoint only supports an
*application* permission (``AiEnterpriseInteraction.Read.All``) that needs tenant
admin consent and can read every user in the tenant — so it is effectively
unavailable for an individual exporting their own chat. Driving the signed-in
web session is the practical path, mirroring teams_chat_worker.py.

Usage:
    python copilot_chat_worker.py export <conversation_url> <output_path> <format>

    format is "html" or "txt".

Progress is written to stdout as:
    STATUS:<message>
    DONE:<json_data>
    ERROR:<message>
"""
import asyncio
import html as html_mod
import io
import json
import sys
from datetime import datetime

# Force UTF-8 stdout so the server can decode our STATUS/DONE/ERROR lines correctly
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

# Reuse the authenticated-Edge-profile machinery (works on any machine with Edge).
from browser_utils import ensure_automation_profile  # noqa: E402

# The Microsoft 365 Copilot web app. Conversations live at
#   https://m365.cloud.microsoft/chat/conversation/<id>
COPILOT_HOME = "https://m365.cloud.microsoft/chat"


def status(msg: str) -> None:
    print(f"STATUS:{msg}", flush=True)


def error(msg: str) -> None:
    print(f"ERROR:{msg}", flush=True)


def done(data: dict) -> None:
    print(f"DONE:{json.dumps(data, ensure_ascii=False)}", flush=True)


# ---------------------------------------------------------------------------
# Browser helpers (mirrors teams_chat_worker._open_teams)
# ---------------------------------------------------------------------------
async def _open_copilot(p, url: str):
    """Launch the dedicated Edge automation profile and open the Copilot URL.

    Returns (ctx, page, ok). ``ok`` is True when the Copilot app shell loaded
    (i.e. the user is signed in). Tries headless first (silent SSO) and only
    opens a visible window when an interactive sign-in is actually required."""
    automation_dir = ensure_automation_profile()

    async def _launch(headless: bool):
        # Edge's legacy --headless crashes persistent contexts, so request the
        # modern headless engine via an arg and keep Playwright's flag off.
        args = ["--no-first-run", "--no-default-browser-check"]
        if headless:
            args.append("--headless=new")
        c = await p.chromium.launch_persistent_context(
            user_data_dir=automation_dir,
            channel="msedge",
            headless=False,
            args=args,
        )
        pg = c.pages[0] if c.pages else await c.new_page()
        return c, pg

    async def _navigate(pg, target_url: str) -> None:
        """Navigate to target_url using 'commit' (fires as soon as the URL is
        committed, before the full page load) so we don't block for 45 s on
        Microsoft's multi-step auth redirect chain."""
        try:
            await pg.goto(target_url, wait_until="commit", timeout=60000)
        except Exception:
            pass

    async def _interactive_login_visible(pg) -> bool:
        for sel in ('input[type="password"]', 'input[name="loginfmt"]',
                    'input[name="passwd"]', '#i0116', '#i0118'):
            try:
                if await pg.locator(sel).first.is_visible(timeout=400):
                    return True
            except Exception:
                continue
        return False

    async def _wait_ready(pg, secs: int) -> str:
        """Poll until Copilot is ready. Returns 'ok'|'needlogin'|'timeout'.

        Strategy: if we're on the right M365/Copilot domain and the body has
        some children, count it as ready and let the scraper do the rest.
        A transient login.* redirect is part of silent SSO — only count it as
        needlogin when a real sign-in field is actually visible."""
        for i in range(secs):
            current = (pg.url or "").lower()
            # Still on a login/auth redirect?
            if any(h in current for h in ("login.microsoftonline", "login.live",
                                           "login.windows", "msauth")):
                if await _interactive_login_visible(pg):
                    return "needlogin"
                await asyncio.sleep(1)
                continue
            # On the Copilot / M365 domain — check if the SPA shell has rendered.
            if any(h in current for h in ("m365.cloud.microsoft", "microsoft365.com",
                                           "copilot.microsoft.com", "substrate.office.com")):
                try:
                    ready = await pg.evaluate(
                        "() => document.body ? document.body.children.length > 2 : false"
                    )
                    if ready:
                        return "ok"
                except Exception:
                    pass
                # After 25 s on the right domain, proceed anyway — let scraper try.
                if i >= 25:
                    return "ok"
                await asyncio.sleep(1)
                continue
            # Any other non-auth page — proceed optimistically.
            if current and not any(h in current for h in ("about:blank", "about:newtab")):
                return "ok"
            await asyncio.sleep(1)
        return "timeout"

    status("① 正在启动浏览器…")
    ctx, page = await _launch(headless=True)
    status("② 正在打开 Microsoft 365 Copilot 对话…")
    await _navigate(page, url)
    status("③ 等待页面加载（约需 10–20 秒）…")
    state = await _wait_ready(page, 40)
    if state == "ok":
        return ctx, page, True

    # First time, expired, or silent SSO didn't complete → open a visible window
    # so the user can sign in once (then it's remembered across all workers).
    try:
        await ctx.close()
    except Exception:
        pass
    status("需要登录：正在打开 Edge 窗口，请在该窗口登录 Microsoft 365（之后会自动记住）…")
    ctx, page = await _launch(headless=False)
    await _navigate(page, url)
    state = await _wait_ready(page, 180)
    if state == "ok":
        status("登录成功，继续…")
        return ctx, page, True
    return ctx, page, False


# ---------------------------------------------------------------------------
# Scraping
# ---------------------------------------------------------------------------
# Candidate selectors for a single conversation turn, tried in order. The first
# one that matches >= 1 element wins. Microsoft mangles CSS class names, so we
# rely on stable-ish data attributes / roles. If a future UI change breaks
# extraction, inspect the page with F12 DevTools and add the new selector here.
_TURN_SELECTORS = [
    '[data-tid*="chat-message" i]',
    '[data-testid*="chat-message" i]',
    '[data-tid*="conversation-message" i]',
    '[data-tid*="message-group" i]',
    '[data-testid*="message" i]',
    '[data-tid*="message" i]',
    '[role="listitem"]',
]

# UI chrome that innerText drags in (buttons, labels). Lines exactly matching one
# of these — case-insensitive — are dropped from a turn's text.
_UI_NOISE = {
    "copy", "edit", "copilot", "you", "good response", "bad response",
    "like", "dislike", "more", "regenerate", "stop", "send", "share",
    "复制", "编辑", "赞", "踩", "重新生成", "停止", "发送", "分享",
    "good response.", "bad response.", "copy code", "复制代码",
}


async def _scrape_conversation(page) -> list[dict]:
    """Scroll the message pane to the top to load the entire conversation, then
    extract each turn as {role, text}. Copilot conversations strictly alternate
    user → assistant, so when an explicit role can't be detected we fall back to
    index parity (turn 0 = the user's first prompt)."""

    # 1) Wait until at least one turn-like element is present (up to ~25s).
    async def _turn_count() -> int:
        return await page.evaluate(
            """(sels) => {
                for (const s of sels) {
                    const n = document.querySelectorAll(s).length;
                    if (n) return n;
                }
                return 0;
            }""",
            _TURN_SELECTORS,
        )

    for _ in range(25):
        if await _turn_count():
            break
        await asyncio.sleep(1)

    # 2) Find the scrollable conversation pane and crawl upward to load history.
    #    Copilot lazy-loads older turns when the pane is scrolled to the top.
    async def _scroll_to_top() -> int:
        return await page.evaluate(
            """(sels) => {
                // Locate a turn, then walk up to the nearest scrollable ancestor.
                let el = null;
                for (const s of sels) { el = document.querySelector(s); if (el) break; }
                if (!el) return 0;
                let sc = el.parentElement;
                while (sc && sc.scrollHeight <= sc.clientHeight + 4) sc = sc.parentElement;
                if (!sc) return 0;
                sc.scrollTop = 0;
                return sc.scrollHeight;
            }""",
            _TURN_SELECTORS,
        )

    prev_count = await _turn_count()
    stale = 0
    for i in range(120):
        await _scroll_to_top()
        await asyncio.sleep(1.1)
        cur = await _turn_count()
        if cur > prev_count:
            stale = 0
        else:
            stale += 1
            if stale >= 5:
                break
        prev_count = cur
        if i % 8 == 7:
            status(f"已加载约 {cur} 个对话块，继续向上滚动…")

    # 3) Extract turns. Returns [{role, text}] in document order.
    rows = await page.evaluate(
        r"""(args) => {
            const [sels, noise] = args;
            const noiseSet = new Set(noise);

            // Pick the first selector that actually matches something.
            let nodes = [];
            for (const s of sels) {
                const found = Array.from(document.querySelectorAll(s));
                if (found.length) { nodes = found; break; }
            }
            // Drop nested matches: keep only outermost turn blocks so a turn and
            // its inner body aren't both emitted.
            nodes = nodes.filter(n => !nodes.some(o => o !== n && o.contains(n)));

            const clean = (raw) => raw.split('\n')
                .map(l => l.replace(/\s+$/g, ''))
                .filter(l => {
                    const t = l.trim();
                    if (!t) return true;            // keep blank lines (paragraph breaks)
                    return !noiseSet.has(t.toLowerCase());
                })
                .join('\n')
                .replace(/\n{3,}/g, '\n\n')
                .trim();

            const detectRole = (el) => {
                // Explicit signals first.
                const attrs = ['data-author-role', 'data-message-author-role',
                               'data-author', 'data-tid', 'aria-label', 'aria-roledescription'];
                for (const a of attrs) {
                    const v = (el.getAttribute(a) || '').toLowerCase();
                    if (!v) continue;
                    if (v.includes('copilot') || v.includes('assistant') ||
                        v.includes('ai') || v.includes('bot') || v.includes('response')) return 'assistant';
                    if (v.includes('user') || v.includes('you') || v.includes('prompt') ||
                        v.includes('human')) return 'user';
                }
                return '';
            };

            const out = [];
            nodes.forEach(el => {
                const text = clean(el.innerText || '');
                if (!text) return;
                out.push({ role: detectRole(el), text });
            });
            return out;
        }""",
        [_TURN_SELECTORS, sorted(_UI_NOISE)],
    )

    # Fallback: if structured extraction found nothing, dump the main region's
    # text so the export is never silently empty.
    if not rows:
        raw = await page.evaluate(
            """() => {
                const main = document.querySelector('main, [role="main"]') || document.body;
                return (main.innerText || '').trim();
            }"""
        )
        if raw:
            return [{"role": "", "text": raw}]
        return []

    # Assign roles. Honor any explicit detections; fill the rest by alternation
    # anchored on the first detected role (or 'user' for turn 0).
    turns = []
    for idx, r in enumerate(rows):
        role = r.get("role") or ""
        if not role:
            role = "user" if idx % 2 == 0 else "assistant"
        turns.append({"role": role, "text": r.get("text", "")})
    return turns


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
def _role_label(role: str) -> str:
    return "Copilot" if role == "assistant" else "你"


def _export_txt(turns: list[dict], title: str, output_path: str) -> None:
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(f"Microsoft 365 Copilot 对话记录\n")
        if title:
            f.write(f"标题: {title}\n")
        f.write(f"导出时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"共 {len(turns)} 条消息\n")
        f.write("=" * 60 + "\n\n")
        for t in turns:
            f.write(f"【{_role_label(t['role'])}】\n")
            f.write(t.get("text", "").strip() + "\n\n")


def _export_html(turns: list[dict], title: str, output_path: str) -> None:
    safe_title = html_mod.escape(title or "Microsoft 365 Copilot 对话")
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>{safe_title}</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 820px; margin: 0 auto; padding: 20px; background: #f5f5f7; }}
.header {{ text-align: center; padding: 20px 0; border-bottom: 2px solid #0f6cbd; margin-bottom: 20px; }}
.header h1 {{ color: #333; margin: 0 0 5px; font-size: 20px; }}
.header p {{ color: #888; margin: 2px 0; font-size: 14px; }}
.turn {{ margin: 14px 0; }}
.turn .meta {{ font-size: 12px; font-weight: 600; margin-bottom: 4px; }}
.turn.user .meta {{ color: #0f6cbd; }}
.turn.assistant .meta {{ color: #6a4ea3; }}
.turn .bubble {{ border-radius: 10px; padding: 10px 14px; white-space: pre-wrap; word-break: break-word; line-height: 1.55; }}
.turn.user .bubble {{ background: #e7f1fb; border: 1px solid #cfe2f6; }}
.turn.assistant .bubble {{ background: #fff; border: 1px solid #e3e3ea; }}
</style>
</head>
<body>
<div class="header">
<h1>{safe_title}</h1>
<p>Microsoft 365 Copilot · 共 {len(turns)} 条消息</p>
<p>导出时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
</div>
""")
        for t in turns:
            role = "assistant" if t.get("role") == "assistant" else "user"
            label = html_mod.escape(_role_label(role))
            body = html_mod.escape(t.get("text", "").strip())
            f.write(f'<div class="turn {role}">\n')
            f.write(f'<div class="meta">{label}</div>\n')
            f.write(f'<div class="bubble">{body}</div>\n')
            f.write('</div>\n')
        f.write("</body>\n</html>\n")


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------
async def export_conversation(url: str, output_path: str, fmt: str) -> None:
    from playwright.async_api import async_playwright

    if not url.lower().startswith("http"):
        # Allow pasting just the conversation id.
        url = f"{COPILOT_HOME}/conversation/{url.strip()}"

    async with async_playwright() as p:
        ctx, page, ok = await _open_copilot(p, url)
        try:
            if not ok:
                error("Authentication required — 未能登录 Microsoft 365。请重试并在弹出的 Edge 窗口中完成登录。")
                return

            # Make sure we're actually on the conversation (login may have redirected
            # back to the Copilot home rather than the specific conversation URL).
            try:
                current = (page.url or "").lower()
                if "conversation" not in current:
                    status("正在跳转到目标对话…")
                    await page.goto(url, wait_until="commit", timeout=60000)
                    await asyncio.sleep(5)
            except Exception:
                pass

            status("正在读取对话内容（向上滚动加载全部历史）…")
            turns = await _scrape_conversation(page)
            if not turns:
                error("未能在该对话中找到任何消息。请确认链接正确、且你有权限访问该对话。")
                return

            # Try to read the conversation title from the page (best-effort).
            try:
                title = await page.evaluate(
                    """() => {
                        const t = document.title || '';
                        return t.replace(/\\s*[-|–]\\s*Microsoft.*$/i, '').trim();
                    }"""
                )
            except Exception:
                title = ""

            status(f"共收集到 {len(turns)} 条消息，正在写入 {fmt.upper()}…")
            if fmt == "html":
                _export_html(turns, title, output_path)
            else:
                _export_txt(turns, title, output_path)

            done({"count": len(turns), "title": title})
        finally:
            await ctx.close()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main() -> None:
    if len(sys.argv) < 2:
        error("Usage: copilot_chat_worker.py export <conversation_url> <output> <format>")
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd == "export":
        if len(sys.argv) < 5:
            error("Usage: copilot_chat_worker.py export <conversation_url> <output> <format>")
            sys.exit(1)
        url = sys.argv[2]
        output_path = sys.argv[3]
        fmt = sys.argv[4] if sys.argv[4] in ("html", "txt") else "html"
        asyncio.run(export_conversation(url, output_path, fmt))
    else:
        error(f"Unknown command: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    main()
