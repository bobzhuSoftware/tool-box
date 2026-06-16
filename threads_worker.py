"""
Threads Video Download Worker — run as a subprocess from server.py.

Threads is a JS-rendered SPA and yt-dlp has no extractor for it, so we drive
a headless Chromium via Playwright, sniff video network responses, then
download every distinct video the post contains (handles single posts and
carousels).

Usage:
    python threads_worker.py <threads_url> <output_dir>

stdout protocol (one message per line):
    STATUS:<message>
    PROGRESS:<message>
    DONE:<json_data>
    ERROR:<message>
"""
import io
import json
import os
import re
import sys
import time
from urllib.parse import urlparse, urlunparse

sys.stdout = io.TextIOWrapper(
    sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
)

import requests  # noqa: E402
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout  # noqa: E402


UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


def status(msg: str) -> None:
    print(f"STATUS:{msg}", flush=True)


def progress(msg: str) -> None:
    print(f"PROGRESS:{msg}", flush=True)


def done(data: dict) -> None:
    print(f"DONE:{json.dumps(data, ensure_ascii=False)}", flush=True)


def error(msg: str) -> None:
    print(f"ERROR:{msg}", flush=True)
    sys.exit(1)


def _normalise_video_key(url: str) -> str:
    """Group different bitrate variants of the same clip together by path."""
    p = urlparse(url)
    return urlunparse((p.scheme, p.netloc, p.path, "", "", ""))


def _is_video_url(url: str, content_type: str | None) -> bool:
    if content_type and content_type.startswith("video/"):
        return True
    lower = url.lower().split("?", 1)[0]
    return lower.endswith(".mp4") or lower.endswith(".mov") or lower.endswith(".m4v")


def collect_video_urls(page_url: str, settle_seconds: float = 5.0) -> tuple[list[str], dict]:
    """Render the page in headless Chromium and return (video_urls, post_meta)."""
    found: dict[str, dict] = {}  # key -> {"url": str, "size": int}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            context = browser.new_context(
                user_agent=UA,
                viewport={"width": 1280, "height": 900},
                locale="en-US",
            )
            page = context.new_page()

            def on_response(resp):
                try:
                    ct = resp.headers.get("content-type", "")
                    url = resp.url
                    if not _is_video_url(url, ct):
                        return
                    key = _normalise_video_key(url)
                    try:
                        size = int(resp.headers.get("content-length", "0") or 0)
                    except ValueError:
                        size = 0
                    existing = found.get(key)
                    if existing is None or size > existing["size"]:
                        found[key] = {"url": url, "size": size}
                except Exception:  # noqa: BLE001
                    pass

            page.on("response", on_response)

            status("打开浏览器并加载页面…")
            try:
                page.goto(page_url, wait_until="domcontentloaded", timeout=45000)
            except PWTimeout:
                error("页面加载超时")
                return [], {}

            try:
                page.wait_for_selector("video, [role='article']", timeout=15000)
            except PWTimeout:
                pass

            status("等待视频数据加载…")

            # Poll until network has been quiet for `settle_seconds`,
            # while nudging carousels forward so all videos get fetched.
            deadline = time.time() + settle_seconds + 12
            stable_since: float | None = None
            last_count = -1
            while time.time() < deadline:
                try:
                    nxt = page.query_selector("button[aria-label*='Next' i]")
                    if nxt:
                        nxt.click(timeout=500)
                except Exception:  # noqa: BLE001
                    pass

                page.evaluate("window.scrollBy(0, 200)")
                page.wait_for_timeout(700)

                count = len(found)
                if count != last_count:
                    last_count = count
                    stable_since = time.time()
                elif stable_since and time.time() - stable_since >= settle_seconds and count > 0:
                    break

            meta: dict = {}
            try:
                meta["title"] = page.title() or ""
            except Exception:  # noqa: BLE001
                meta["title"] = ""

            context.close()
        finally:
            browser.close()

    urls = [v["url"] for v in sorted(found.values(), key=lambda v: -v["size"])]
    return urls, meta


def download_file(url: str, dest: str, label: str) -> int:
    headers = {"User-Agent": UA, "Referer": "https://www.threads.com/"}
    with requests.get(url, headers=headers, stream=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length", "0") or 0)
        downloaded = 0
        last_pct = -1
        last_emit = 0.0
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                f.write(chunk)
                downloaded += len(chunk)
                if total:
                    pct = int(downloaded * 100 / total)
                    now = time.time()
                    if pct != last_pct and now - last_emit > 0.2:
                        last_pct = pct
                        last_emit = now
                        mb = downloaded / 1024 / 1024
                        progress(f"{label}: {pct}% — {mb:.2f} MB")
        return downloaded


def main() -> None:
    if len(sys.argv) < 3:
        error("usage: threads_worker.py <url> <output_dir>")

    url = sys.argv[1].strip()
    out_dir = sys.argv[2]

    if not url:
        error("URL 不能为空")
    if not re.search(r"threads\.(net|com)", url, flags=re.IGNORECASE):
        error("URL 不是有效的 Threads 链接（应包含 threads.net 或 threads.com）")

    os.makedirs(out_dir, exist_ok=True)
    status(f"解析 Threads 链接: {url}")

    try:
        video_urls, meta = collect_video_urls(url)
    except Exception as e:  # noqa: BLE001
        error(f"加载页面失败: {e}")
        return

    if not video_urls:
        error("未在该 Threads 链接中找到视频（可能是图片帖、私密内容或需要登录）")
        return

    status(f"发现 {len(video_urls)} 个视频，开始下载…")

    files: list[str] = []
    for idx, vurl in enumerate(video_urls, 1):
        fname = f"video_{idx:02d}.mp4"
        dest = os.path.join(out_dir, fname)
        label = f"视频 {idx}/{len(video_urls)}"
        try:
            size = download_file(vurl, dest, label)
        except Exception as e:  # noqa: BLE001
            error(f"下载第 {idx} 个视频失败: {e}")
            return
        if size <= 0:
            error(f"第 {idx} 个视频内容为空")
            return
        files.append(fname)
        status(f"{label} 下载完成 ({size / 1024 / 1024:.2f} MB)")

    raw_title = meta.get("title") or "threads_video"
    # Page titles look like "@user on Threads: ..." — keep it short
    title = raw_title.split(" on Threads")[0].strip() or "threads_video"

    done({
        "title": title,
        "uploader": "",
        "files": files,
        "count": len(files),
    })


if __name__ == "__main__":
    main()
