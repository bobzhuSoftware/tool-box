"""
Teams Chat History Export Worker — run as a subprocess from server.py.

Approach A: drive the *web* version of Teams (teams.microsoft.com) using the
user's already-signed-in Edge profile (reused from teams_transcript_worker), then
scrape the chat DOM. This gets the *complete* history (Teams loads older messages
from the server as you scroll up), unlike the local LevelDB cache which is partial.

Usage:
    python teams_chat_worker.py list
    python teams_chat_worker.py export <chat_id> <chat_name> <output_path> <start_date> <end_date> <format>

    start_date / end_date are "YYYY-MM-DD" or "" (no filter).
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
import re
import sys
from datetime import datetime

# Force UTF-8 stdout so the server can decode our STATUS/DONE/ERROR lines correctly
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

# Reuse the authenticated-Edge-profile machinery (works on any machine with Edge).
# Modern Edge holds its live cookie DB under an exclusive lock, so the old
# "copy the running profile" trick no longer works. We share the dedicated,
# persistent Edge automation profile with teams_transcript_worker — the user
# signs in once and the session is reused across both features.
from browser_utils import ensure_automation_profile, automation_profile_signed_in  # noqa: E402

TEAMS_URL = "https://teams.microsoft.com/v2/"


def status(msg: str) -> None:
    print(f"STATUS:{msg}", flush=True)


def error(msg: str) -> None:
    print(f"ERROR:{msg}", flush=True)


def done(data: dict) -> None:
    print(f"DONE:{json.dumps(data, ensure_ascii=False)}", flush=True)


# ---------------------------------------------------------------------------
# Browser helpers
# ---------------------------------------------------------------------------
async def _open_teams(p):
    """Launch the dedicated Edge automation profile (shared with the transcript
    worker, signed into once) and open the Teams web app.

    Returns (ctx, page, ok). ``ok`` is True when the Teams app shell loaded, i.e.
    the user is signed in. Tries headless first (silent SSO) and only opens a
    visible window when an interactive sign-in is actually required."""
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
        try:
            await pg.goto(TEAMS_URL, wait_until="domcontentloaded", timeout=45000)
        except Exception:
            pass
        return c, pg

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
        """Poll until the Teams app shell is ready. Returns 'ok'|'needlogin'|'timeout'.

        A transient login.* redirect is part of silent SSO, so it only counts as
        needlogin when a real sign-in field is actually visible."""
        for _ in range(secs):
            current = (pg.url or "").lower()
            if ("login.microsoftonline" in current or "login.live" in current
                    or "login.windows" in current):
                if await _interactive_login_visible(pg):
                    return "needlogin"
                await asyncio.sleep(1)
                continue
            try:
                shell = await pg.evaluate(
                    """() => !!document.querySelector(
                        '[data-tid="me-control-avatar"], [data-tid="app-bar-wrapper"]'
                    )"""
                )
            except Exception:
                shell = False
            if shell:
                return "ok"
            await asyncio.sleep(1)
        return "timeout"

    status("① 正在启动浏览器…")
    ctx, page = await _launch(headless=True)
    status("② 正在打开 Teams 网页版…")
    status("③ 等待 Teams 加载（约需 10–20 秒）…")
    state = await _wait_ready(page, 40)
    if state == "ok":
        return ctx, page, True

    # First time, expired, or silent SSO didn't complete → open a visible window
    # so the user can sign in once (then it's remembered for both workers).
    try:
        await ctx.close()
    except Exception:
        pass
    status("需要登录：正在打开 Edge 窗口，请在该窗口登录 Teams（之后会自动记住）…")
    ctx, page = await _launch(headless=False)
    state = await _wait_ready(page, 180)
    if state == "ok":
        status("登录成功，继续…")
        return ctx, page, True
    return ctx, page, False


async def _goto_chat_list(page) -> None:
    """Make sure the Chat list rail is visible by clicking the Chat app-bar button."""
    chat_nav_selectors = [
        'button[aria-label*="Chat" i]',
        '[data-tid="app-bar-chat"]',
        'button[data-tid="app-bar-chat"]',
        '[aria-label*="聊天"]',
    ]
    for sel in chat_nav_selectors:
        try:
            btn = page.locator(sel).first
            if await btn.count() and await btn.is_visible():
                await btn.click()
                break
        except Exception:
            continue
    # Wait for the chat rail (a Fluent v9 tree) to render.
    try:
        await page.wait_for_selector(
            '[data-tid="simple-collab-dnd-rail"] [role="treeitem"]',
            timeout=15000,
        )
    except Exception:
        pass
    await asyncio.sleep(2)


async def _wait_for_rail(page) -> int:
    """Resiliently wait for the chat rail to populate (it can be slow to fetch the
    chat list from the server, especially under load). Polls for up to ~50s and
    re-clicks the Chat nav periodically. Returns the number of tree items found."""
    async def _count() -> int:
        try:
            return await page.evaluate(
                """() => {
                    const rail = document.querySelector('[data-tid="simple-collab-dnd-rail"]');
                    return rail ? rail.querySelectorAll('[role="treeitem"]').length : 0;
                }"""
            )
        except Exception:
            return 0

    count = 0
    for attempt in range(50):
        count = await _count()
        if count > 0:
            break
        if attempt and attempt % 8 == 0:
            await _goto_chat_list(page)
        await asyncio.sleep(1)
    return count


# ---------------------------------------------------------------------------
# list: enumerate recent chats
# ---------------------------------------------------------------------------
async def list_chats() -> None:
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        ctx, page, ok = await _open_teams(p)
        try:
            if not ok:
                error("Authentication required — 未能登录 Teams。请重试并在弹出的 Edge 窗口中完成登录。")
                return

            status("⑤ 正在打开聊天列表…")
            await _goto_chat_list(page)
            status("⑥ 正在等待聊天列表出现…")

            # Wait resiliently for the chat rail to populate (slow under load).
            count = await _wait_for_rail(page)
            if count == 0:
                error("已登录 Teams，但未能加载到聊天列表。请确认 Edge 中的 Teams 已打开"
                      "并显示聊天，然后重试；若仍为 0，可尝试在 Edge 里切到“聊天”页后再连接。")
                return

            status("⑦ 正在读取聊天列表…")

            # The rail is a Fluent v9 tree (virtualized). Read the visible chat
            # names, scroll down, repeat until no new ones appear. Quick-view
            # entries (Copilot/Mentions/Drafts) are filtered out.
            collected: list[str] = []
            seen: set[str] = set()
            stale = 0
            for _ in range(40):
                names = await page.evaluate(
                    """
                    () => {
                        const rail = document.querySelector('[data-tid="simple-collab-dnd-rail"]');
                        if (!rail) return [];
                        const skip = new Set(['Copilot','Mentions','Drafts','Favorites',
                            'Recent','Pinned','Quick views','收藏','最近','置顶']);
                        const out = [];
                        rail.querySelectorAll('[role="treeitem"]').forEach(el => {
                            const nameEl = el.querySelector('.fui-TreeItemLayout__main span, span[role="text"]');
                            let name = nameEl ? nameEl.textContent.trim() : '';
                            if (!name) name = (el.getAttribute('aria-label') || '').trim();
                            if (!name) return;
                            name = name.slice(0, 100);
                            if (skip.has(name)) return;
                            out.push(name);
                        });
                        return out;
                    }
                    """
                )
                before = len(collected)
                for name in names:
                    if name not in seen:
                        seen.add(name)
                        collected.append(name)
                if len(collected) == before:
                    stale += 1
                    if stale >= 3:
                        break
                else:
                    stale = 0
                    status(f"⑦ 已发现 {len(collected)} 个聊天，继续加载…")
                # Scroll the rail down to reveal more (virtualized) items.
                await page.evaluate(
                    """
                    () => {
                        const rail = document.querySelector('[data-tid="simple-collab-dnd-rail"]');
                        if (!rail) return;
                        let sc = rail;
                        while (sc && sc.scrollHeight <= sc.clientHeight) sc = sc.parentElement;
                        if (sc) sc.scrollTop = sc.scrollHeight;
                    }
                    """
                )
                await asyncio.sleep(0.8)

            # id == name: the rail exposes no stable conversation id, so we open
            # chats later by matching their displayed name.
            chats = [{"id": n, "name": n} for n in collected]
            status(f"✓ 共找到 {len(chats)} 个聊天")
            done({"chats": chats})
        finally:
            await ctx.close()


# ---------------------------------------------------------------------------
# export: scrape one chat's full history
# ---------------------------------------------------------------------------
async def _open_chat(page, chat_id: str, chat_name: str) -> bool:
    """Open a chat by clicking the rail tree item whose name matches chat_name.

    The rail is virtualized, so a target far down the list is not in the DOM until
    scrolled into view. We scroll from the top, progressively rendering items, and
    click the match as soon as it appears (atomic text locator, so indices can't
    drift).
    """
    await _goto_chat_list(page)
    # Resiliently wait for the rail to populate before searching it.
    if await _wait_for_rail(page) == 0:
        return False

    target = chat_name or chat_id
    rail = page.locator('[data-tid="simple-collab-dnd-rail"]')

    async def _find_and_click() -> bool:
        for loc in (
            rail.get_by_text(target, exact=True).first,
            rail.get_by_text(target).first,
        ):
            try:
                if await loc.count():
                    await loc.scroll_into_view_if_needed(timeout=5000)
                    await loc.click()
                    return True
            except Exception:
                continue
        return False

    # Reset to the top of the rail first.
    try:
        await page.evaluate(
            """() => {
                const rail = document.querySelector('[data-tid="simple-collab-dnd-rail"]');
                if (!rail) return;
                let sc = rail;
                while (sc && sc.scrollHeight <= sc.clientHeight) sc = sc.parentElement;
                if (sc) sc.scrollTop = 0;
            }"""
        )
    except Exception:
        pass
    await asyncio.sleep(0.4)

    # Scroll from the top, progressively rendering virtualized items, clicking the
    # target as soon as it appears. Stop when the scroll position stops advancing
    # (we've hit the bottom) rather than on a one-shot "atBottom" guess, which
    # mis-fires while the list is still loading.
    clicked = False
    prev_top = -1
    stalls = 0
    for _ in range(120):
        if await _find_and_click():
            clicked = True
            break
        info = await page.evaluate(
            """() => {
                const rail = document.querySelector('[data-tid="simple-collab-dnd-rail"]');
                if (!rail) return {top: -1};
                let sc = rail;
                while (sc && sc.scrollHeight <= sc.clientHeight) sc = sc.parentElement;
                if (!sc) return {top: -1};
                sc.scrollTop = sc.scrollTop + sc.clientHeight * 0.7;
                return {top: sc.scrollTop};
            }"""
        )
        top = info.get("top", -1)
        if top <= prev_top + 2:
            stalls += 1
        else:
            stalls = 0
        prev_top = top
        await asyncio.sleep(0.4)
        if stalls >= 4:
            # Reached the bottom; final attempt then give up.
            clicked = await _find_and_click()
            break

    if not clicked:
        return False

    # Wait for the message pane to populate.
    try:
        await page.wait_for_selector(
            '[data-tid="chat-pane-message"], [data-tid="message-pane-layout"]',
            timeout=15000,
        )
    except Exception:
        pass
    await asyncio.sleep(3)
    return True


async def _scrape_messages(page, stop_before: str = "") -> list[dict]:
    """Scroll the message pane to the top to load all history, collecting
    messages as we go (the pane virtualizes, so we merge by signature).

    If ``stop_before`` (a 'YYYY-MM-DD' or 'YYYY-MM-DDTHH:mm' local bound) is
    given, stop scrolling once we've loaded a message OLDER than that bound —
    since we scroll newest→oldest, everything on/after it is already loaded, so
    there's no need to keep scrolling through the entire (possibly huge) history."""
    stop_dt = _parse_bound(stop_before, is_end=False)
    collected: dict[str, dict] = {}
    order = 0

    async def grab():
        nonlocal order
        rows = await page.evaluate(
            r"""
            () => {
                const out = [];
                const containers = document.querySelectorAll('[data-tid="chat-pane-message"]');
                const list = containers.length ? containers
                    : document.querySelectorAll('[data-tid="message-pane"] [role="listitem"]');
                const PLACEHOLDER = 'data:image/gif;base64,R0lGOD';
                list.forEach(el => {
                    // The author name lives on the parent chat-pane-item (shown
                    // once per group), not inside each chat-pane-message.
                    const item = el.closest('[data-tid="chat-pane-item"]') || el;
                    let author = '';
                    const a = el.querySelector('[data-tid="message-author-name"]') ||
                              item.querySelector('[data-tid="message-author-name"]');
                    if (a) author = a.textContent.trim();
                    // Prefer the <time datetime> (full ISO, UTC) when present.
                    let ts = '';
                    const t = el.querySelector('time') || item.querySelector('time');
                    if (t) ts = t.getAttribute('datetime') || '';
                    const b = el.querySelector('[data-tid="message-body-content"], [id^="content-"]');
                    if (!b) return;
                    // Every message's body/id encodes its send time in epoch ms,
                    // e.g. id="content-1781876502020". This is far more reliable
                    // than the lazily-rendered <time> element, so use it as the
                    // stable identity AND the timestamp source.
                    let mid = 0;
                    const idm = ((b.id || el.id || '') + '').match(/(\d{12,})/);
                    if (idm) mid = Number(idm[1]);
                    let body = (b.innerText || '').trim();
                    // Reaction summary — the 👍/❤️ chips attached BELOW a message.
                    // Verified DOM: container data-tid="diverse-reaction-summary";
                    // each reaction is a data-tid="diverse-reaction-pill-button"
                    // holding an emoticon <img alt="👍"> plus a leading count in its
                    // text (e.g. "3 Like reactions. 3"). We collect these separately
                    // AND exclude their imgs from the body emoji scan below, or the
                    // reaction emoji would leak into the message text.
                    const rxEl = el.querySelector('[data-tid="diverse-reaction-summary"]');
                    const reactions = [];
                    if (rxEl) {
                        rxEl.querySelectorAll('[data-tid="diverse-reaction-pill-button"]').forEach(pill => {
                            const eimg = pill.querySelector('[data-tid="emoticon-renderer"] img[alt], img[alt]');
                            const emo = eimg ? (eimg.getAttribute('alt') || '').trim() : '';
                            if (!emo) return;
                            const m = (pill.innerText || pill.textContent || '').trim().match(/^(\d+)/);
                            const cnt = m ? Number(m[1]) : 1;
                            reactions.push(cnt > 1 ? (emo + '×' + cnt) : emo);
                        });
                    }
                    // File attachments render as data-tid="file-attachment-grid".
                    // The cards load lazily (filenames aren't reliably in the DOM),
                    // but the aria-label always carries the count.
                    let attachCount = 0;
                    const atEl = el.querySelector('[data-tid="file-attachment-grid"]');
                    if (atEl) {
                        const al = atEl.getAttribute('aria-label') || '';
                        const am = al.match(/(\d+)\s+attachment/i);
                        attachCount = am ? Number(am[1]) : 1;
                    }
                    // Classify <img> elements: emoji (emoticon-renderer) → keep as
                    // their alt char; real pasted images/GIFs → collect src so we
                    // can fetch the bytes; avatars/placeholders → ignore.
                    const emojis = [];
                    const imgSrcs = [];
                    el.querySelectorAll('img').forEach(im => {
                        if (rxEl && rxEl.contains(im)) return;  // belongs to a reaction chip
                        if (atEl && atEl.contains(im)) return;  // attachment thumbnail
                        const tidEl = im.closest('[data-tid]');
                        const tid = tidEl ? tidEl.getAttribute('data-tid') : '';
                        const alt = (im.getAttribute('alt') || '');
                        const altL = alt.toLowerCase();
                        if (tid === 'emoticon-renderer') {
                            if (alt) emojis.push(alt);
                            return;
                        }
                        if (altL.includes('avatar') || altL.includes('profile')) return;
                        const src = im.src || '';
                        if (!src || src.startsWith(PLACEHOLDER)) return;  // not loaded yet
                        if (im.naturalWidth <= 40 && im.naturalHeight <= 40) return; // tiny icon
                        imgSrcs.push(src);
                    });
                    if (emojis.length) {
                        body = (body + ' ' + emojis.join('')).trim();
                    }
                    out.push({ author, ts, mid, body, imgSrcs, reactions, attachCount });
                });
                return out;
            }
            """
        )
        last_author = ""
        new_fetch = []  # (key, src) pairs for images not yet downloaded
        for r in rows:
            author = r.get("author") or last_author
            last_author = author or last_author
            body = r.get("body") or ""
            # Resolve a local-time timestamp: the epoch-ms message id is the most
            # reliable source; fall back to the <time datetime> (UTC ISO).
            mid = r.get("mid") or 0
            ts = _ts_from_mid(mid) or _ts_to_local(r.get("ts") or "")
            # Dedupe by the stable message id when available (avoids the same
            # message being stored twice — once timed, once untimed — which would
            # otherwise bypass date filtering).
            reactions = r.get("reactions") or []
            attach = int(r.get("attachCount") or 0)
            key = f"id:{mid}" if mid else f"{author}|{ts}|{body}"
            if key not in collected:
                collected[key] = {"author": author, "ts": ts, "body": body,
                                  "reactions": reactions, "attach": attach,
                                  "imgs": [], "imgmiss": 0, "_o": order}
                order += 1
            else:
                # Reactions/attachments can render late (after the message fully
                # hydrates), so backfill them on a later pass if we missed them.
                if reactions and not collected[key].get("reactions"):
                    collected[key]["reactions"] = reactions
                if attach and not collected[key].get("attach"):
                    collected[key]["attach"] = attach
            # Queue any real content images for download (once per message, when
            # they've actually loaded). blob: URLs are only valid while the image
            # is in the DOM, so we must fetch the bytes now, during scraping.
            srcs = r.get("imgSrcs") or []
            msg = collected[key]
            if srcs and not msg.get("_imgdone"):
                msg["_imgdone"] = True
                msg["_imgexpect"] = len(srcs)
                for s in srcs:
                    new_fetch.append((key, s))

        # Download newly-seen images to base64 inside the authenticated page
        # (carries the Teams session, so blob:/CDN URLs return real bytes).
        if new_fetch:
            data_uris = await page.evaluate(
                """
                async (srcs) => {
                    const out = [];
                    for (const src of srcs) {
                        try {
                            const r = await fetch(src);
                            if (!r.ok) { out.push(null); continue; }
                            const blob = await r.blob();
                            if (!blob || !blob.size || blob.size > 8000000) { out.push(null); continue; }
                            const uri = await new Promise(res => {
                                const fr = new FileReader();
                                fr.onload = () => res(fr.result);
                                fr.onerror = () => res(null);
                                fr.readAsDataURL(blob);
                            });
                            out.push(uri);
                        } catch (e) { out.push(null); }
                    }
                    return out;
                }
                """,
                [s for (_k, s) in new_fetch],
            )
            for (key, _src), uri in zip(new_fetch, data_uris):
                msg = collected.get(key)
                if not msg:
                    continue
                if uri and isinstance(uri, str) and uri.startswith("data:image"):
                    msg["imgs"].append(uri)
                else:
                    msg["imgmiss"] = msg.get("imgmiss", 0) + 1

    # The message pane renders lazily after the chat opens. Wait until at least
    # one message is actually present (up to ~25s) before scraping; otherwise the
    # first grab finds nothing and the stale counter ends the scrape empty.
    for _ in range(25):
        n = await page.evaluate(
            '() => document.querySelectorAll(\'[data-tid="chat-pane-message"]\').length'
        )
        if n:
            break
        await asyncio.sleep(1)

    # Initial grab, then scroll up repeatedly until history stops growing.
    # Teams virtualizes the list, so scrollHeight can stay constant even while
    # older messages keep loading. Track the message COUNT and the earliest
    # loaded timestamp as the real progress signals, not height alone.
    await grab()
    stale = 0
    prev_count = len(collected)
    prev_earliest = min((m["ts"] for m in collected.values() if m["ts"]), default="")
    for i in range(300):
        height_before = await page.evaluate(
            """
            () => {
                const cands = [
                    document.querySelector('[data-tid="message-pane-list-viewport"]'),
                    document.querySelector('[data-tid="message-pane-list-runway"]'),
                    document.querySelector('[data-tid="message-pane-body"]'),
                ].filter(Boolean);
                let sc = cands[0];
                if (!sc) {
                    const m = document.querySelector('[data-tid="chat-pane-message"]');
                    sc = m ? m.parentElement : null;
                    while (sc && sc.scrollHeight <= sc.clientHeight) sc = sc.parentElement;
                }
                if (!sc) return 0;
                sc.scrollTop = 0;            // jump to top → triggers loading older
                return sc.scrollHeight;
            }
            """
        )
        await asyncio.sleep(1.3)
        await grab()
        height_after = await page.evaluate(
            """
            () => {
                const cands = [
                    document.querySelector('[data-tid="message-pane-list-viewport"]'),
                    document.querySelector('[data-tid="message-pane-list-runway"]'),
                    document.querySelector('[data-tid="message-pane-body"]'),
                ].filter(Boolean);
                let sc = cands[0];
                if (!sc) {
                    const m = document.querySelector('[data-tid="chat-pane-message"]');
                    sc = m ? m.parentElement : null;
                    while (sc && sc.scrollHeight <= sc.clientHeight) sc = sc.parentElement;
                }
                return sc ? sc.scrollHeight : 0;
            }
            """
        )
        cur_count = len(collected)
        cur_earliest = min((m["ts"] for m in collected.values() if m["ts"]), default="")
        progressed = (
            cur_count > prev_count
            or (cur_earliest and (not prev_earliest or cur_earliest < prev_earliest))
            or height_after > height_before
        )
        if progressed:
            stale = 0
        else:
            stale += 1
            if stale >= 6:
                break
        prev_count = cur_count
        prev_earliest = cur_earliest
        # Early stop: once we've scrolled past the requested start time, all
        # newer messages are already loaded — no need to crawl older history.
        if stop_dt is not None:
            earliest = min(
                (m["ts"] for m in collected.values() if m["ts"]),
                default="",
            )
            edt = _msg_dt(earliest)
            if edt is not None and edt < stop_dt:
                break
        if i % 10 == 9:
            status(f"Loaded {len(collected)} messages so far…")

    msgs = sorted(collected.values(), key=lambda m: (m["ts"] or "", m["_o"]))
    for m in msgs:
        m.pop("_o", None)
        m.pop("_imgdone", None)
        m.pop("_imgexpect", None)
    return msgs


def _ts_from_mid(mid: int) -> str:
    """Convert an epoch-ms message id to a local-time ISO string, or '' if invalid.
    Teams encodes the send time in the message id (e.g. content-1781876502020)."""
    if not mid:
        return ""
    try:
        return datetime.fromtimestamp(mid / 1000).strftime("%Y-%m-%dT%H:%M:%S")
    except (ValueError, OSError, OverflowError):
        return ""


def _ts_to_local(ts: str) -> str:
    """Convert a UTC ISO timestamp (e.g. '2026-06-19T13:41:42.020Z') to a local
    naive ISO string, so dates/times match what the user sees in Teams."""
    if not ts:
        return ""
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt.astimezone().strftime("%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return ts


def _msg_date(ts: str) -> str:
    """Return YYYY-MM-DD from an ISO timestamp, or '' if not parseable."""
    if not ts:
        return ""
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", ts)
    if m:
        return m.group(0)
    return ""


def _msg_dt(ts: str):
    """Parse a message's local naive ISO ts into a datetime, or None."""
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", ""))
    except ValueError:
        return None


def _parse_bound(s: str, is_end: bool):
    """Parse a filter bound into a local naive datetime, or None.
    Accepts 'YYYY-MM-DD' (date only) or 'YYYY-MM-DDTHH:mm[:ss]' (with time).
    Date-only bounds expand to the start/end of the day; minute-precision end
    bounds expand to include the whole minute."""
    if not s:
        return None
    s = s.strip().replace(" ", "T")
    try:
        if "T" in s:
            dt = datetime.fromisoformat(s)
            if is_end and len(s) <= 16:  # 'YYYY-MM-DDTHH:mm' → include whole minute
                dt = dt.replace(second=59, microsecond=999999)
            return dt
        d = datetime.strptime(s, "%Y-%m-%d")
        if is_end:
            return d.replace(hour=23, minute=59, second=59, microsecond=999999)
        return d
    except ValueError:
        return None


def _fmt_time(ts: str) -> str:
    """Human-friendly timestamp for display (ts is already local naive ISO)."""
    if not ts:
        return ""
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return ts


def _filter_by_date(messages: list[dict], start: str, end: str) -> list[dict]:
    start_dt = _parse_bound(start, is_end=False)
    end_dt = _parse_bound(end, is_end=True)
    if not start_dt and not end_dt:
        return messages
    out = []
    for m in messages:
        dt = _msg_dt(m.get("ts", ""))
        if dt is None:
            out.append(m)  # keep undated messages
            continue
        if start_dt and dt < start_dt:
            continue
        if end_dt and dt > end_dt:
            continue
        out.append(m)
    return out


def _export_txt(messages: list[dict], chat_name: str, output_path: str) -> None:
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(f"Teams 聊天记录 — {chat_name}\n")
        f.write(f"导出时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"共 {len(messages)} 条消息\n")
        f.write("=" * 60 + "\n\n")
        for m in messages:
            t = _fmt_time(m.get("ts", ""))
            sender = m.get("author") or "(未知)"
            body = m.get("body", "")
            n_img = len(m.get("imgs") or []) + int(m.get("imgmiss") or 0)
            if n_img:
                tag = f"[图片 ×{n_img}]" if n_img > 1 else "[图片]"
                body = (body + " " + tag).strip() if body else tag
            n_at = int(m.get("attach") or 0)
            if n_at:
                at_tag = f"[附件 ×{n_at}]" if n_at > 1 else "[附件]"
                body = (body + " " + at_tag).strip() if body else at_tag
            rx = m.get("reactions") or []
            if rx:
                rx_tag = "[回应: " + " ".join(rx) + "]"
                body = (body + "  " + rx_tag).strip() if body else rx_tag
            prefix = f"[{t}] " if t else ""
            f.write(f"{prefix}{sender}: {body}\n")


def _export_html(messages: list[dict], chat_name: str, output_path: str) -> None:
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>Teams 聊天记录 — {html_mod.escape(chat_name)}</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 820px; margin: 0 auto; padding: 20px; background: #f5f5f7; }}
.header {{ text-align: center; padding: 20px 0; border-bottom: 2px solid #6264a7; margin-bottom: 20px; }}
.header h1 {{ color: #333; margin: 0 0 5px; }}
.header p {{ color: #888; margin: 2px 0; font-size: 14px; }}
.msg {{ margin: 10px 0; }}
.msg .meta {{ font-size: 12px; color: #6264a7; font-weight: 600; margin-bottom: 2px; }}
.msg .meta .time {{ color: #999; font-weight: 400; margin-left: 6px; }}
.msg .bubble {{ background: #fff; border: 1px solid #e3e3ea; border-radius: 8px; padding: 8px 12px; white-space: pre-wrap; word-break: break-word; }}
.msg .bubble img.attach {{ display: block; max-width: 360px; max-height: 360px; margin: 6px 0 2px; border-radius: 6px; border: 1px solid #e3e3ea; }}
.msg .bubble .imgmiss {{ color: #999; font-size: 12px; }}
.msg .bubble .attachfile {{ display: inline-block; margin-top: 6px; padding: 4px 10px; background: #f3f2f1; border: 1px solid #e1dfdd; border-radius: 6px; font-size: 13px; color: #444; }}
.msg .reactions {{ margin-top: 5px; }}
.msg .reactions .chip {{ display: inline-block; background: #eef0fb; border: 1px solid #d6d9f0; border-radius: 12px; padding: 1px 8px; margin: 2px 4px 0 0; font-size: 12px; color: #444; }}
.date-sep {{ text-align: center; margin: 18px 0; font-size: 12px; color: #999; }}
</style>
</head>
<body>
<div class="header">
<h1>{html_mod.escape(chat_name)}</h1>
<p>共 {len(messages)} 条消息</p>
<p>导出时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
</div>
""")
        last_date = ""
        for m in messages:
            ts = m.get("ts", "")
            date_str = _msg_date(ts)
            if date_str and date_str != last_date:
                f.write(f'<div class="date-sep">—— {date_str} ——</div>\n')
                last_date = date_str
            sender = html_mod.escape(m.get("author") or "(未知)")
            t = _fmt_time(ts)
            time_short = t[11:] if len(t) > 11 else ""
            body = html_mod.escape(m.get("body", ""))
            imgs_html = "".join(
                f'<img class="attach" src="{uri}" alt="image">'
                for uri in (m.get("imgs") or [])
            )
            miss = int(m.get("imgmiss") or 0)
            if miss:
                imgs_html += f'<div class="imgmiss">[{miss} 张图片未能保存]</div>'
            n_at = int(m.get("attach") or 0)
            at_html = ""
            if n_at:
                label = f"📎 附件 ×{n_at}" if n_at > 1 else "📎 附件"
                at_html = f'<div class="attachfile">{label}</div>'
            rx = m.get("reactions") or []
            rx_html = ""
            if rx:
                chips = "".join(
                    f'<span class="chip">{html_mod.escape(r)}</span>' for r in rx
                )
                rx_html = f'<div class="reactions">{chips}</div>'
            f.write('<div class="msg">\n')
            f.write(f'<div class="meta">{sender}<span class="time">{html_mod.escape(time_short)}</span></div>\n')
            f.write(f'<div class="bubble">{body}{imgs_html}{at_html}{rx_html}</div>\n')
            f.write('</div>\n')
        f.write("</body>\n</html>\n")


async def export_chat(chat_id: str, chat_name: str, output_path: str,
                      start_date: str, end_date: str, fmt: str) -> None:
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        ctx, page, ok = await _open_teams(p)
        try:
            if not ok:
                error("Authentication required — 未能登录 Teams。请重试并在弹出的 Edge 窗口中完成登录。")
                return

            status(f"Opening chat: {chat_name}…")
            opened = await _open_chat(page, chat_id, chat_name)
            if not opened:
                error(f"Could not open chat '{chat_name}'. It may have been renamed or is not in the recent list.")
                return

            status("Loading message history (scrolling up)…")
            messages = await _scrape_messages(page, stop_before=start_date)
            if not messages:
                error("No messages found in this chat.")
                return

            messages = _filter_by_date(messages, start_date, end_date)
            status(f"Collected {len(messages)} messages, writing {fmt.upper()}…")

            if fmt == "html":
                _export_html(messages, chat_name, output_path)
            else:
                _export_txt(messages, chat_name, output_path)

            done({"count": len(messages), "name": chat_name})
        finally:
            await ctx.close()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main() -> None:
    if len(sys.argv) < 2:
        error("Usage: teams_chat_worker.py list | export <chat_id> <chat_name> <output> <start> <end> <format>")
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd == "list":
        asyncio.run(list_chats())
    elif cmd == "export":
        if len(sys.argv) < 8:
            error("Usage: teams_chat_worker.py export <chat_id> <chat_name> <output> <start> <end> <format>")
            sys.exit(1)
        chat_id = sys.argv[2]
        chat_name = sys.argv[3]
        output_path = sys.argv[4]
        start_date = sys.argv[5]
        end_date = sys.argv[6]
        fmt = sys.argv[7] if sys.argv[7] in ("html", "txt") else "html"
        asyncio.run(export_chat(chat_id, chat_name, output_path, start_date, end_date, fmt))
    else:
        error(f"Unknown command: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    main()
