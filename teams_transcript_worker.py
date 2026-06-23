"""
Teams Transcript Worker — run as a subprocess from server.py.

Usage:
    python teams_transcript_worker.py <url> <output_vtt_path>

Progress is written to stdout as:
    STATUS:<message>
    DONE:<display_name>
    ERROR:<message>
"""
import asyncio
import io
import json
import re
import sys
from urllib.parse import urlparse, quote, unquote

# Force UTF-8 stdout so the server can decode our STATUS/DONE/ERROR lines correctly
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

# Profile/auth handling lives in browser_utils so it works on any machine that
# has Edge installed. Modern Edge exclusively locks its live cookie DB, so we
# drive a dedicated persistent automation profile instead of copying the user's
# running session.
from browser_utils import (  # noqa: E402
    ensure_automation_profile,
    automation_profile_signed_in,
)

# ---- UUID cue-identifier pattern ----------------------------------------
# Matches lines like: aca214ba-5200-4ec1-8bbe-66c314ec0a0e/119-0
_VTT_CUE_ID_RE = re.compile(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/\d+-\d+\s*$',
    re.IGNORECASE,
)


def clean_vtt(vtt_text: str) -> str:
    """
    Rebuild a WebVTT string into clean format:
      - Remove UUID cue-identifier lines
      - Remove stray blank lines inside cue blocks (between timestamp and text)
      - Exactly one blank line between cue blocks
    Result: WEBVTT header + blank line + (timestamp\\ntext\\n\\n) * N
    """
    lines = vtt_text.splitlines()

    # Collect non-empty, non-UUID lines while tracking structure
    output_cues = []   # each item: (timestamp_line, text_line)
    i = 0

    # Skip WEBVTT header line(s)
    while i < len(lines) and not lines[i].strip().startswith("WEBVTT"):
        i += 1
    i += 1  # skip "WEBVTT" itself

    while i < len(lines):
        line = lines[i].strip()

        # Skip blank lines between blocks
        if not line:
            i += 1
            continue

        # Skip UUID cue identifiers
        if _VTT_CUE_ID_RE.match(line):
            i += 1
            continue

        # Timestamp line (contains -->)
        if "-->" in line:
            timestamp = line
            i += 1
            # Gather text lines that follow (skip any blank lines mixed in)
            text_parts = []
            while i < len(lines):
                tline = lines[i].strip()
                if not tline:
                    # A blank line ends this cue block
                    i += 1
                    break
                if "-->" in tline or _VTT_CUE_ID_RE.match(tline):
                    # Next cue started without blank separator — don't consume
                    break
                text_parts.append(tline)
                i += 1
            if text_parts:
                output_cues.append((timestamp, " ".join(text_parts)))
            continue

        # Anything else — skip
        i += 1

    result_lines = ["WEBVTT", ""]
    for timestamp, text in output_cues:
        result_lines.append(timestamp)
        result_lines.append(text)
        result_lines.append("")  # one blank line separator

    return "\n".join(result_lines) + "\n"


def mp4_url_to_stream_url(mp4_url: str) -> str:
    parsed = urlparse(mp4_url)
    path = parsed.path
    host = f"{parsed.scheme}://{parsed.netloc}"
    parts = path.split("/")
    site_root = "/" + "/".join(parts[1:3])
    encoded_parts = []
    for part in parts:
        if part == "":
            continue
        decoded = unquote(part)
        encoded = quote(decoded, safe="-.")
        encoded = encoded.replace("_", "%5F")
        encoded_parts.append(encoded)
    encoded_path = "%2F" + "%2F".join(encoded_parts)
    return f"{host}{site_root}/_layouts/15/stream.aspx?id={encoded_path}"


def status(msg: str):
    print(f"STATUS:{msg}", flush=True)


def _resolve_input_url(url: str) -> str:
    """Normalise any URL variant the user might paste:
    - AccessDenied page  → extract the Source= query param (the real mp4/stream URL)
    - plain mp4 URL      → convert to stream.aspx
    - stream.aspx URL    → use as-is
    """
    from urllib.parse import urlparse as _up, parse_qs as _pqs, unquote as _uq
    parsed = _up(url)
    # Handle AccessDenied redirect pages
    if "accessdenied" in parsed.path.lower():
        qs = _pqs(parsed.query)
        source = qs.get("Source", [""])[0]
        if source:
            url = _uq(source)
            parsed = _up(url)
    # Strip referrer/web query params to get clean mp4 URL
    if parsed.path.lower().endswith(".mp4"):
        url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
    if "stream.aspx" in url:
        return url
    return mp4_url_to_stream_url(url)


async def run(url: str, output_path: str):
    stream_url = _resolve_input_url(url)

    status(f"Connecting to SharePoint Stream…")

    from playwright.async_api import async_playwright

    transcript_meta: dict = {}
    captured_api_urls: list = []

    # Copy profile to temp dir so Edge can stay open
    status("Preparing browser session…")

    # Modern Edge holds its live cookie DB under an *exclusive* lock, so the old
    # "copy the running profile" trick no longer works. Instead we use a
    # dedicated, persistent Edge automation profile that the user signs into
    # once; the session is reused on every subsequent run while their normal
    # Edge stays open. We try headless first (Windows SSO often signs in
    # silently) and only open a visible window when interactive login is needed.
    automation_dir = ensure_automation_profile()

    def _on_login_host(u: str) -> bool:
        u = (u or "").lower()
        return ("login.microsoftonline.com" in u or "login.live.com" in u
                or "login.windows.net" in u or "msauth" in u
                or "/_forms/default.aspx" in u)

    try:
        async with async_playwright() as p:

            async def on_response(response):
                rurl = response.url
                # Broad filter: any JSON API response that might contain transcript info
                is_api = ("_api" in rurl or "api/" in rurl) and "content" not in rurl.split("?")[0]
                is_transcript_url = "transcript" in rurl.lower()
                if is_api or is_transcript_url:
                    captured_api_urls.append(rurl[:200])
                    try:
                        ct = response.headers.get("content-type", "")
                        if "json" not in ct and "octet" not in ct:
                            return
                        body = await response.body()
                        if len(body) < 50:
                            return
                        try:
                            data = json.loads(body)
                        except Exception:
                            return
                        # Standard path: media.transcripts[]
                        transcripts = data.get("media", {}).get("transcripts", [])
                        if transcripts and not transcript_meta:
                            transcript_meta.update(transcripts[0])
                            return
                        # Alternative: transcripts at top level (value array)
                        if isinstance(data.get("value"), list):
                            for item in data["value"]:
                                if item.get("@odata.type", "").endswith("transcript") or \
                                   "temporaryDownloadUrl" in item or \
                                   ("displayName" in item and item.get("languageTag")):
                                    if not transcript_meta:
                                        transcript_meta.update(item)
                                    return
                        # Alternative: direct transcript object
                        if "temporaryDownloadUrl" in data and "displayName" in data:
                            if not transcript_meta:
                                transcript_meta.update(data)
                    except Exception:
                        pass

            async def _interactive_login_visible(pg) -> bool:
                # A genuinely required sign-in shows a username/password field.
                for sel in ('input[type="password"]', 'input[name="loginfmt"]',
                            'input[name="passwd"]', '#i0116', '#i0118'):
                    try:
                        if await pg.locator(sel).first.is_visible(timeout=500):
                            return True
                    except Exception:
                        continue
                return False

            async def _no_access_in_page(pg) -> bool:
                # SharePoint renders an in-page "You don't have access" notice on
                # the SAME stream.aspx URL when the signed-in account lacks
                # permission (e.g. the wrong Edge profile/tenant was chosen). The
                # URL never changes to an accessdenied page, so we must look at the
                # rendered text to catch it instead of silently failing later with
                # a misleading "no transcript found".
                try:
                    txt = await pg.evaluate(
                        "() => (document.body ? document.body.innerText : '').slice(0, 4000)"
                    )
                except Exception:
                    return False
                low = (txt or "").lower()
                phrases = (
                    "you don't have access", "you do not have access",
                    "request access", "don't have permission",
                    "do not have permission", "需要访问权限", "没有访问权限",
                    "无权访问", "请求访问权限",
                )
                return any(p in low for p in phrases)

            async def _wait_silent(pg, secs: int) -> str:
                """Headless settle check. Returns 'ok' | 'accessdenied' | 'needlogin'.

                SharePoint Stream does a silent SSO bounce, so a transient login
                redirect is not a failure — only an actual interactive prompt (or
                never leaving the login host) means we must sign in.
                """
                for _ in range(secs):
                    u = pg.url
                    if "accessdenied" in u.lower():
                        return "accessdenied"
                    if transcript_meta:
                        return "ok"
                    if _on_login_host(u):
                        if await _interactive_login_visible(pg):
                            return "needlogin"
                        await asyncio.sleep(1)
                        continue
                    if "sharepoint.com" in u.lower() or "stream.aspx" in u.lower():
                        return "ok"
                    await asyncio.sleep(1)
                return "needlogin" if _on_login_host(pg.url) else "ok"

            async def _wait_login(pg, secs: int) -> str:
                """Wait (headed) for the user to finish signing in.

                Returns 'ok' | 'accessdenied' | 'timeout'. A login host is treated
                as "still working" so the user has time to authenticate.
                """
                for _ in range(secs):
                    u = pg.url
                    if "accessdenied" in u.lower():
                        return "accessdenied"
                    if transcript_meta:
                        return "ok"
                    if not _on_login_host(u) and ("sharepoint.com" in u.lower() or "stream.aspx" in u.lower()):
                        return "ok"
                    await asyncio.sleep(1)
                return "timeout"

            async def _launch(headless: bool):
                # Edge's legacy --headless mode crashes persistent contexts
                # ("Browser window not found"), so request the modern headless
                # engine via an arg and keep Playwright's own flag off.
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
                c.on("response", on_response)
                try:
                    await pg.goto(stream_url, wait_until="domcontentloaded", timeout=30000)
                except Exception:
                    pass
                return c, pg

            async def _try_launch(headless: bool):
                try:
                    return await _launch(headless=headless)
                except Exception as e:
                    status(f"Edge launch issue ({'headless' if headless else 'window'}): {str(e)[:80]}")
                    return None, None

            status("Loading recording page…")
            ctx = None
            page = None
            state = "needlogin"

            # If we've signed in before, try silently (headless) first.
            if automation_profile_signed_in():
                ctx, page = await _try_launch(headless=True)
                if ctx is not None:
                    status("Verifying sign-in…")
                    state = await _wait_silent(page, 25)
                    if state not in ("ok", "accessdenied"):
                        try:
                            await ctx.close()
                        except Exception:
                            pass
                        ctx = page = None

            # First time, expired, or headless failed → open a visible window so
            # the user can complete sign-in once (then it's remembered).
            if state not in ("ok", "accessdenied"):
                status("需要登录：正在打开 Edge 窗口，请在该窗口完成登录（之后会自动记住）…")
                ctx, page = await _try_launch(headless=False)
                if ctx is None:
                    print("ERROR:Could not open Edge for sign-in. Please retry.", flush=True)
                    return
                state = await _wait_login(page, 180)
                if state == "ok":
                    status("登录成功，继续获取字幕…")

            if state == "accessdenied":
                print("ERROR:Access denied — you may not have permission to view this recording.", flush=True)
                await ctx.close()
                return
            if state != "ok":
                print("ERROR:Authentication required — sign-in was not completed in the Edge window. Please retry and finish signing in.", flush=True)
                await ctx.close()
                return

            # The page that loaded during the auth bounce is often in a
            # half-rendered state: in the headless silent path the first goto
            # happens BEFORE sign-in completes, so the SharePoint Stream player
            # never hydrates (and its transcript API never fires) even though the
            # URL ends up back on stream.aspx. Always re-open the recording URL on
            # the now-authenticated session so the player loads cleanly.
            status("Reloading recording page…")
            try:
                await page.goto(stream_url, wait_until="domcontentloaded", timeout=30000)
            except Exception:
                pass

            # Try to trigger transcript loading by clicking transcript/CC buttons
            status("Looking for transcript panel…")
            try:
                # Wait for the page to stabilize
                await page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass

            # The recording page can load successfully (URL stays on stream.aspx)
            # yet show an in-page "You don't have access" notice when the signed-in
            # account lacks permission. Detect that now and fail with a clear,
            # actionable message instead of the misleading "no transcript found".
            if not transcript_meta and await _no_access_in_page(page):
                print("ERROR:无权限访问该录制 — 当前 Edge profile 登录的账号没有此录制的访问权限。"
                      "请在设置中选择能打开该录制的 Edge profile（账号），或向录制所有者申请访问权限。",
                      flush=True)
                await ctx.close()
                return

            # Try various selectors that SharePoint Stream uses for transcript buttons
            transcript_selectors = [
                'button[aria-label*="Transcript" i]',
                'button[aria-label*="transcript" i]',
                'button[data-automationid*="transcript" i]',
                'button[title*="Transcript" i]',
                '[role="tab"][aria-label*="Transcript" i]',
                'button:has-text("Transcript")',
                'button[aria-label*="字幕" i]',
                'button[aria-label*="CC" i]',
                'button[aria-label*="captions" i]',
                '[data-automation-id*="transcript" i]',
            ]
            clicked = False
            # Retry button search up to 3 times with increasing waits
            for attempt in range(3):
                for sel in transcript_selectors:
                    try:
                        btn = page.locator(sel).first
                        if await btn.is_visible(timeout=2000):
                            await btn.click()
                            clicked = True
                            status("Clicked transcript button, waiting for data…")
                            await asyncio.sleep(3)
                            break
                    except Exception:
                        continue
                if clicked or transcript_meta:
                    break
                # Wait before retrying
                await asyncio.sleep(2)

            if not clicked and not transcript_meta:
                # Try clicking the "more actions" or panel toggle as fallback
                try:
                    # Some versions have a side panel toggle
                    panel_btn = page.locator('[aria-label*="panel" i], [aria-label*="details" i]').first
                    if await panel_btn.is_visible(timeout=1000):
                        await panel_btn.click()
                        await asyncio.sleep(2)
                except Exception:
                    pass

            status("Waiting for transcript metadata…")
            for i in range(30):
                await asyncio.sleep(1)
                if transcript_meta:
                    break
                if i % 10 == 9:
                    status(f"Still waiting… ({i+1}s)")

            # Fallback: extract driveId/itemId from captured URLs and call transcript API directly
            if not transcript_meta:
                status("Trying direct transcript API query…")
                # Extract drive and item IDs from captured API URLs
                drive_item_re = re.compile(r'/drives/([^/]+)/items/([^/?]+)')
                drive_id = None
                item_id = None
                base_url = None
                for u in captured_api_urls:
                    m = drive_item_re.search(u)
                    if m:
                        drive_id = m.group(1)
                        item_id = m.group(2)
                        # Extract base URL (protocol + host + path prefix before /drives/)
                        idx = u.index('/drives/')
                        # Find the API root (e.g. https://host/_api_cached/v2.1)
                        base_url = u[:idx]
                        break

                if drive_id and item_id and base_url:
                    status(f"Found drive/item IDs, querying transcript API…")
                    try:
                        api_result = await page.evaluate("""
                            async (args) => {
                                const { baseUrl, driveId, itemId } = args;
                                // Try multiple API patterns for transcript discovery
                                const urls = [
                                    `${baseUrl}/drives/${driveId}/items/${itemId}?$select=id,name,media&$expand=media`,
                                    `${baseUrl}/drives/${driveId}/items/${itemId}?select=id,name,media&expand=media`,
                                ];
                                for (const url of urls) {
                                    try {
                                        const resp = await fetch(url, { credentials: 'include' });
                                        if (resp.ok) {
                                            const data = await resp.json();
                                            const transcripts = data?.media?.transcripts;
                                            if (transcripts && transcripts.length > 0) {
                                                return transcripts[0];
                                            }
                                        }
                                    } catch(e) {}
                                }
                                return null;
                            }
                        """, {"baseUrl": base_url, "driveId": drive_id, "itemId": item_id})
                        if api_result and isinstance(api_result, dict):
                            transcript_meta.update(api_result)
                            status("Got transcript metadata via direct API call")
                    except Exception as e:
                        status(f"Direct API query failed: {e}")
                else:
                    status("Could not extract drive/item IDs from captured URLs")

            # Second fallback: try scrolling/interacting to trigger lazy API calls
            if not transcript_meta:
                status("Attempting to trigger transcript load via UI interaction…")
                try:
                    # Click anywhere on the video player to ensure it's active
                    player = page.locator('video, [class*="player" i], [class*="Player" i]').first
                    if await player.is_visible(timeout=2000):
                        await player.click()
                        await asyncio.sleep(1)
                except Exception:
                    pass

                # Try right-click context menu or kebab menu
                try:
                    kebab = page.locator('[aria-label*="More" i], [aria-label*="更多" i], [class*="kebab" i]').first
                    if await kebab.is_visible(timeout=1000):
                        await kebab.click()
                        await asyncio.sleep(1)
                        # Look for transcript option in menu
                        menu_item = page.locator('[role="menuitem"]:has-text("Transcript"), [role="menuitem"]:has-text("字幕")').first
                        if await menu_item.is_visible(timeout=1000):
                            await menu_item.click()
                            await asyncio.sleep(3)
                except Exception:
                    pass

                # Final wait
                for i in range(10):
                    await asyncio.sleep(1)
                    if transcript_meta:
                        break

            if not transcript_meta:
                # Log diagnostic info for debugging
                if captured_api_urls:
                    status(f"Debug: captured {len(captured_api_urls)} API responses but none contained transcript data")
                    for u in captured_api_urls[:5]:
                        status(f"  API: {u}")
                else:
                    status("Debug: no API responses were captured at all — page may not have loaded correctly")
                print("ERROR:No transcript found for this recording. It may not have been transcribed.", flush=True)
                await ctx.close()
                return

            display_name = transcript_meta.get("displayName", "transcript.json")
            lang = transcript_meta.get("languageTag", "")
            size = transcript_meta.get("size", 0)
            status(f"Found transcript: {display_name} ({lang}, {size // 1024} KB)")

            download_url = transcript_meta.get("temporaryDownloadUrl", "")
            if not download_url:
                print("ERROR:Transcript metadata found but no download URL available.", flush=True)
                await ctx.close()
                return

            status("Downloading transcript content…")
            try:
                result = await page.evaluate(f"""
                    async () => {{
                        const resp = await fetch({json.dumps(download_url)}, {{credentials: 'include'}});
                        const status = resp.status;
                        const text = await resp.text();
                        return {{status, text, size: text.length}};
                    }}
                """)
                http_status = result["status"]
                vtt_text = result["text"]
                size_bytes = result["size"]
            except Exception as e:
                print(f"ERROR:Download failed: {e}", flush=True)
                await ctx.close()
                return

            if http_status != 200 or size_bytes < 10:
                print(f"ERROR:Server returned HTTP {http_status}. You may not have access to this recording.", flush=True)
                await ctx.close()
                return

            # Clean UUID cue identifiers
            cleaned = clean_vtt(vtt_text)
            status(f"Cleaned VTT ({size_bytes // 1024} KB)")

            with open(output_path, "w", encoding="utf-8") as f:
                f.write(cleaned)

            # Derive a safe filename from displayName (strip .json suffix)
            base = re.sub(r"\.json$", "", display_name, flags=re.IGNORECASE)
            safe_name = re.sub(r'[\\/:*?"<>|]', "_", base)

            print(f"DONE:{json.dumps({'name': safe_name, 'lang': lang})}", flush=True)
            await ctx.close()
    finally:
        # The automation profile is persistent (so the sign-in is remembered);
        # nothing to clean up here.
        pass


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("ERROR:Usage: teams_transcript_worker.py <url> <output_path>", flush=True)
        sys.exit(1)
    asyncio.run(run(sys.argv[1], sys.argv[2]))
