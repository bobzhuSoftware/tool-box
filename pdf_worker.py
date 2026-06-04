"""
Standalone PDF generation worker.
Called as a subprocess to avoid asyncio event loop conflicts on Windows.
Prints STATUS:<message> lines to stdout so the parent can stream progress.
Usage: python pdf_worker.py <url> <output_path>
"""
import os
import shutil
import sqlite3
import sys
import tempfile

# Force UTF-8 output so Chinese/Unicode titles don't crash on Windows (cp1252)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")




def _read_firefox_cookies(url: str) -> list:
    """
    Read cookies for the given URL's domain directly from Firefox's cookies.sqlite.
    Copies the db file first so Firefox doesn't need to be closed.
    """
    from urllib.parse import urlparse

    hostname = urlparse(url).hostname or ""
    parts = hostname.split(".")
    base_domain = ".".join(parts[-2:]) if len(parts) >= 2 else hostname

    ff_root = os.path.expandvars(r"%APPDATA%\Mozilla\Firefox\Profiles")
    if not os.path.isdir(ff_root):
        return []

    ff_profile = None
    try:
        for entry in sorted(os.scandir(ff_root), key=lambda e: e.name):
            if entry.is_dir() and "default-release" in entry.name:
                ff_profile = entry.path
                break
        if ff_profile is None:
            dirs = [e.path for e in os.scandir(ff_root) if e.is_dir()]
            ff_profile = dirs[0] if dirs else None
    except OSError:
        return []

    if not ff_profile:
        return []

    cookies_db = os.path.join(ff_profile, "cookies.sqlite")
    if not os.path.isfile(cookies_db):
        return []

    tmp_db = os.path.join(tempfile.gettempdir(), "vt_ff_cookies.sqlite")
    try:
        shutil.copy2(cookies_db, tmp_db)
    except OSError:
        return []

    cookies = []
    same_site_map = {0: "None", 1: "Lax", 2: "Strict"}
    try:
        conn = sqlite3.connect(tmp_db)
        for host, name, value, path, expiry, is_secure, is_http_only, same_site in conn.execute(
            "SELECT host, name, value, path, expiry, isSecure, isHttpOnly, sameSite "
            "FROM moz_cookies WHERE baseDomain = ?",
            (base_domain,),
        ):
            cookies.append({
                "name": name,
                "value": value,
                "domain": host,
                "path": path,
                "expires": int(expiry),
                "httpOnly": bool(is_http_only),
                "secure": bool(is_secure),
                "sameSite": same_site_map.get(same_site, "Lax"),
            })
        conn.close()
    except Exception:
        pass
    finally:
        try:
            os.unlink(tmp_db)
        except OSError:
            pass

    return cookies


_STEALTH_JS = """
// Remove webdriver flag
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
// Realistic plugins
(function() {
    const arr = [
        {name:'Chrome PDF Plugin',filename:'internal-pdf-viewer',description:'Portable Document Format'},
        {name:'Chrome PDF Viewer',filename:'mhjfbmdgcfjbbpaeojofohoefgiehjai',description:''},
        {name:'Native Client',filename:'internal-nacl-plugin',description:''},
    ];
    Object.defineProperty(navigator, 'plugins', {get: () => arr});
})();
Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
window.chrome = {runtime:{}, loadTimes:()=>{}, csi:()=>{}, app:{}};
// Permissions
const _origQuery = navigator.permissions.query.bind(navigator.permissions);
navigator.permissions.query = p =>
    p.name === 'notifications' ? Promise.resolve({state: Notification.permission}) : _origQuery(p);
// WebGL vendor spoof
const _wp = WebGLRenderingContext.prototype.getParameter;
WebGLRenderingContext.prototype.getParameter = function(p) {
    if (p === 37445) return 'Intel Inc.';
    if (p === 37446) return 'Intel Iris OpenGL Engine';
    return _wp.call(this, p);
};
"""

# CSS injected into X pages to hide login walls and unlock scroll
_X_HIDE_CSS = """
[data-testid="sheetDialog"],
[data-testid="LoginForm"],
[data-testid="signupButton"],
[data-testid="BottomBar"],
div[aria-modal="true"],
div[role="dialog"],
[class*="r-1kb76zh"] {
    display: none !important;
}
html, body {
    overflow: auto !important;
    position: static !important;
}
"""


def _parse_netscape_cookies(cookies_file: str, url: str) -> list:
    """Parse a Netscape-format cookies.txt and return cookies matching the URL's domain."""
    from urllib.parse import urlparse

    hostname = urlparse(url).hostname or ""
    cookies = []
    try:
        with open(cookies_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split("\t")
                if len(parts) < 7:
                    continue
                domain, _include_sub, path, secure, expires, name, value = parts[:7]
                domain_clean = domain.lstrip(".")
                # Match exact host or any sub-domain
                if hostname == domain_clean or hostname.endswith("." + domain_clean):
                    cookies.append(
                        {
                            "name": name,
                            "value": value,
                            "domain": domain,
                            "path": path,
                            "expires": int(expires),
                            "httpOnly": False,
                            "secure": secure.upper() == "TRUE",
                            "sameSite": "Lax",
                        }
                    )
    except (FileNotFoundError, IOError):
        pass
    return cookies


# Clean article CSS — readable, print-friendly
_ARTICLE_CSS = """
* { box-sizing: border-box; }
body {
    margin: 0; padding: 32px 48px;
    font-family: Georgia, "Times New Roman", serif;
    font-size: 16px; line-height: 1.7;
    color: #111; background: #fff; max-width: 800px; margin: 0 auto;
}
h1 { font-size: 2em; line-height: 1.2; margin: 0 0 12px; }
h2 { font-size: 1.5em; margin: 24px 0 8px; }
h3 { font-size: 1.2em; margin: 20px 0 6px; }
p  { margin: 0 0 14px; }
img { max-width: 100%; height: auto; display: block; margin: 16px auto; border-radius: 4px; }
figure { margin: 16px 0; }
figcaption { font-size: 0.85em; color: #555; text-align: center; margin-top: 4px; }
a { color: #1a0dab; text-decoration: none; }
blockquote {
    border-left: 4px solid #ddd; margin: 16px 0;
    padding: 4px 16px; color: #555; font-style: italic;
}
pre, code { font-family: monospace; background: #f5f5f5; padding: 2px 6px; border-radius: 3px; }
pre { padding: 12px; overflow: visible; white-space: pre-wrap; }
hr { border: none; border-top: 1px solid #eee; margin: 24px 0; }
.article-meta { font-size: 0.9em; color: #666; margin-bottom: 24px; }
"""


def _inline_images(page) -> None:
    """Convert all img src to base64 data URLs so the static HTML is self-contained."""
    try:
        page.evaluate("""async () => {
            const imgs = [...document.querySelectorAll('img')];
            await Promise.all(imgs.map(async img => {
                if (!img.src || img.src.startsWith('data:')) return;
                try {
                    const resp = await fetch(img.src, {credentials: 'include'});
                    const blob = await resp.blob();
                    if (blob.size === 0) return;
                    await new Promise(resolve => {
                        const reader = new FileReader();
                        reader.onload = () => { img.src = reader.result; resolve(); };
                        reader.onerror = resolve;
                        reader.readAsDataURL(blob);
                    });
                } catch(e) {}
            }));
        }""")
    except Exception:
        pass


def _inline_images_via_request(page) -> None:
    """
    Inline images using Playwright's request API (Python-level, no CORS restrictions).
    The request shares the browser context's cookies, so authenticated CDN images
    (e.g. X/Twitter's pbs.twimg.com) are fetched correctly.
    """
    import base64

    try:
        srcs = page.evaluate("""() =>
            [...document.querySelectorAll('img')]
                .map(img => img.src)
                .filter(s => s && s.startsWith('http') && !s.startsWith('data:'))
        """)
    except Exception:
        return

    # Deduplicate while preserving order
    seen = set()
    unique_srcs = [s for s in srcs if not (s in seen or seen.add(s))]

    replacements = {}
    for src in unique_srcs:
        try:
            resp = page.request.get(src, timeout=12_000)
            if resp.ok:
                body = resp.body()
                if body:
                    mime = resp.headers.get("content-type", "image/jpeg").split(";")[0].strip()
                    replacements[src] = f"data:{mime};base64,{base64.b64encode(body).decode()}"
        except Exception:
            pass

    if not replacements:
        return

    try:
        page.evaluate("""(map) => {
            document.querySelectorAll('img').forEach(img => {
                if (map[img.src]) img.src = map[img.src];
            });
        }""", replacements)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Image position helpers — used by both the X path and the generic article path
# ---------------------------------------------------------------------------
import re as _re_mod

_tag_re = _re_mod.compile(r'<[^>]+>')
_block_close_re = _re_mod.compile(r'</(p|li|blockquote|h[1-6]|div)>', _re_mod.IGNORECASE)


def _find_html_pos_for_text(html: str, phrase_words: list, from_start: bool) -> int | None:
    """Find the HTML offset just before/after a text phrase, snapped to a block boundary.

    Uses whitespace-flexible regex matching so minor whitespace differences between
    the original DOM and the Readability output don't cause misses.
    """
    plain = _tag_re.sub('', html)
    for n in (min(10, len(phrase_words)), 6, 3):
        if n < 2:
            break
        words = phrase_words[:n] if from_start else phrase_words[-n:]
        words = [w for w in words if w]
        if not words:
            continue
        # Allow any whitespace between words to handle Readability reformatting
        pattern = r'\s+'.join(_re_mod.escape(w) for w in words)
        matches = list(_re_mod.finditer(pattern, plain))
        if not matches:
            continue
        m = matches[0] if from_start else matches[-1]
        target = m.start() if from_start else m.end()
        text_count, html_i = 0, 0
        while html_i < len(html) and text_count < target:
            mt = _tag_re.match(html, html_i)
            if mt:
                html_i = mt.end()
            else:
                html_i += 1
                text_count += 1
        if from_start:
            last = None
            for m2 in _block_close_re.finditer(html, 0, html_i):
                last = m2
            return last.end() if last else 0
        else:
            m2 = _block_close_re.search(html, html_i)
            return m2.end() if m2 else len(html)
    return None


def _pos_by_ratio(html: str, ratio: float) -> int:
    """Return an HTML offset at `ratio` (0–1) through the plain-text, snapped to a block close."""
    plain = _tag_re.sub('', html)
    target = int(len(plain) * max(0.0, min(1.0, ratio)))
    text_count, html_i = 0, 0
    while html_i < len(html) and text_count < target:
        m = _tag_re.match(html, html_i)
        if m:
            html_i = m.end()
        else:
            html_i += 1
            text_count += 1
    m2 = _block_close_re.search(html, html_i)
    return m2.end() if m2 else len(html)


def _insert_image(html: str, anchor_before: str, anchor_after: str,
                  position: float, img_tag: str) -> str:
    """Insert img_tag using anchor text matching, falling back to position ratio."""
    pos = None
    if anchor_before.strip():
        pos = _find_html_pos_for_text(html, anchor_before.split(), from_start=False)
    if pos is None and anchor_after.strip():
        pos = _find_html_pos_for_text(html, anchor_after.split(), from_start=True)
    if pos is None:
        pos = _pos_by_ratio(html, position)
    return html[:pos] + img_tag + html[pos:]


def _collect_page_images(page) -> list:
    """Walk the page DOM, collect meaningful images, and inject marker spans before each one.

    The markers (<span id="vt-img-N">) survive into page.content() and through Readability,
    allowing precise image reinsertion without relying on fragile text matching.
    """
    try:
        return page.evaluate(r"""() => {
            const results = [];
            const seen = new Set();
            const allNodes = [];
            const walker = document.createTreeWalker(
                document.body,
                NodeFilter.SHOW_TEXT | NodeFilter.SHOW_ELEMENT,
                { acceptNode(n) {
                    if (n.nodeType === 1) {
                        const t = n.tagName;
                        if (['SCRIPT','STYLE','NOSCRIPT','NAV','HEADER','FOOTER'].includes(t))
                            return NodeFilter.FILTER_REJECT;
                    }
                    return NodeFilter.FILTER_ACCEPT;
                }}
            );
            let node;
            while ((node = walker.nextNode())) {
                if (node.nodeType === 3) {
                    const t = node.textContent.replace(/\s+/g, ' ');
                    if (t.trim()) allNodes.push({ type: 'text', text: t, node: null });
                } else if (node.nodeType === 1 && node.tagName === 'IMG') {
                    const src = node.currentSrc || node.src || '';
                    const w = node.naturalWidth || 0;
                    const h = node.naturalHeight || 0;
                    if (src && src.startsWith('http') && !seen.has(src)
                            && (w === 0 || w >= 100) && (h === 0 || h >= 80)) {
                        seen.add(src);
                        allNodes.push({ type: 'img', src, node });
                    }
                }
            }
            const totalText = allNodes.filter(n => n.type === 'text')
                                      .reduce((s, n) => s + n.text.length, 0);
            let runningText = 0;
            let imgIdx = 0;
            for (let i = 0; i < allNodes.length; i++) {
                if (allNodes[i].type === 'text') { runningText += allNodes[i].text.length; continue; }
                if (allNodes[i].type !== 'img') continue;
                const idx = imgIdx++;
                // Inject a hidden marker span before this image in the live DOM so it
                // survives into page.content() and (as an inline element) through Readability.
                try {
                    allNodes[i].node.insertAdjacentHTML('beforebegin',
                        `<span id="vt-img-${idx}" style="display:none"></span>`);
                } catch(e) {}
                let before = '', after = '';
                for (let j = i - 1; j >= 0 && before.length < 120; j--) {
                    if (allNodes[j].type === 'text') before = allNodes[j].text + before;
                }
                for (let j = i + 1; j < allNodes.length && after.length < 120; j++) {
                    if (allNodes[j].type === 'text') after += allNodes[j].text;
                }
                results.push({
                    src: allNodes[i].src,
                    idx,
                    anchorBefore: before.trim().slice(-120),
                    anchorAfter: after.trim().slice(0, 120),
                    position: totalText > 0 ? runningText / totalText : 0,
                });
            }
            return results;
        }""")
    except Exception:
        return []


def _download_page_images(page, image_items: list) -> dict:
    """Download images via Playwright's request API (shares browser cookies, no CORS). Returns url->data_uri."""
    import base64
    url_to_data: dict = {}
    unique_srcs = list(dict.fromkeys(item['src'] for item in image_items))
    for src in unique_srcs:
        try:
            resp = page.request.get(src, timeout=12_000)
            if resp.ok:
                body = resp.body()
                if body:
                    mime = resp.headers.get("content-type", "image/jpeg").split(";")[0].strip()
                    url_to_data[src] = f"data:{mime};base64,{base64.b64encode(body).decode()}"
        except Exception:
            pass
    return url_to_data


def _fetch_images_via_page_eval(page, srcs: list, batch_size: int = 10) -> dict:
    """Fetch images using in-page fetch() with full browser credentials.

    Unlike page.request.get(), this runs inside the browser context so it uses
    the browser's real cookie jar, HTTP cache, and correct Referer/Origin headers.
    Works for cached images (no response event fires) and authenticated CDN URLs.
    Processes in batches to avoid overwhelming the server or hitting evaluate timeouts.
    Returns {url: data_uri} for successfully fetched images.
    """
    if not srcs:
        return {}
    all_results = {}
    # Process in batches to avoid timeout and server rate-limiting
    for i in range(0, len(srcs), batch_size):
        batch = srcs[i:i + batch_size]
        try:
            results = page.evaluate("""async (srcs) => {
                const out = {};
                await Promise.all(srcs.map(async (src) => {
                    try {
                        const resp = await fetch(src, {credentials: 'include'});
                        if (!resp.ok) return;
                        const blob = await resp.blob();
                        if (!blob.size) return;
                        const data = await new Promise((resolve, reject) => {
                            const reader = new FileReader();
                            reader.onload = () => resolve(reader.result);
                            reader.onerror = reject;
                            reader.readAsDataURL(blob);
                        });
                        out[src] = data;
                    } catch (e) {}
                }));
                return out;
            }""", batch)
            if results:
                all_results.update(results)
        except Exception:
            pass
    return all_results


def _reinsert_images(article_html: str, image_items: list, url_to_data: dict) -> tuple[str, int]:
    """Insert downloaded images back into Readability-extracted HTML at their original positions.

    Primary strategy: replace the DOM marker span (<span id="vt-img-N">) that was injected
    before each image in the live page, which survives through Readability as an inline element.
    Fallback: anchor-text matching, then position ratio.
    """
    import re as _re
    count = 0
    for item in image_items:
        src = item.get('src', '')
        if src not in url_to_data:
            continue
        img_tag = (
            f'<figure style="margin:24px 0;text-align:center;">'
            f'<img src="{url_to_data[src]}" alt="" '
            f'style="max-width:100%;height:auto;border-radius:6px;">'
            f'</figure>'
        )
        idx = item.get('idx')
        inserted = False
        if idx is not None:
            # Marker-based replacement: most precise
            new_html, n_subs = _re.subn(
                rf'<span\s+id="vt-img-{idx}"[^>]*>\s*</span>',
                img_tag, article_html, count=1, flags=_re.IGNORECASE)
            if n_subs:
                article_html = new_html
                inserted = True
        if not inserted:
            # Fallback: anchor-text / position-ratio matching
            article_html = _insert_image(
                article_html,
                item.get('anchorBefore', ''),
                item.get('anchorAfter', ''),
                item.get('position', 0.0),
                img_tag,
            )
        count += 1
    return article_html, count


def _extract_dom_title(page) -> str:
    """
    Extract the cleanest article title from the live browser page.
    Priority: og:title → twitter:title → first meaningful <h1> → document.title (site-suffix stripped).
    Returns empty string if nothing useful is found.
    """
    try:
        return page.evaluate(r"""() => {
            // 1. Open Graph title — almost always the clean article title
            const og = document.querySelector('meta[property="og:title"]');
            if (og) { const t = (og.getAttribute('content') || '').trim(); if (t.length > 4) return t; }

            // 2. Twitter Card title
            const tw = document.querySelector('meta[name="twitter:title"]');
            if (tw) { const t = (tw.getAttribute('content') || '').trim(); if (t.length > 4) return t; }

            // 3. First <h1> inside likely article containers
            const containers = [
                document.querySelector('article'),
                document.querySelector('main'),
                document.querySelector('[role="main"]'),
                document.body,
            ].filter(Boolean);
            for (const el of containers) {
                const h1s = [...el.querySelectorAll('h1')];
                if (!h1s.length) continue;
                const t = h1s[0].textContent.trim();
                if (t.length > 4) return t;
            }

            // 4. document.title — strip trailing " | Site", " - Site", " – Site", " — Site"
            const raw = document.title.trim();
            const stripped = raw.replace(/\s*[\|–—\-]\s*[^\|–—\-]{2,60}$/, '').trim();
            if (stripped.length > 4) return stripped;

            return raw;
        }""") or ''
    except Exception:
        return ''


def _extract_with_readability(html: str, url: str) -> tuple[str, str]:
    """Return (title, article_html) using readability-lxml."""
    try:
        from readability import Document
        doc = Document(html, url=url)
        return doc.title(), doc.summary(html_partial=False)
    except Exception:
        return "", html


def _render_html_to_pdf(p, html_doc: str, output_path: str, status_fn) -> None:
    """Write html_doc to a temp file and render to PDF via headless Chromium."""
    import re as _re
    html_tmp = output_path + ".html"
    with open(html_tmp, "w", encoding="utf-8") as f:
        f.write(html_doc)
    status_fn("Generating PDF...")
    try:
        browser2 = p.chromium.launch(channel="chrome", headless=True)
    except Exception:
        browser2 = p.chromium.launch(headless=True)
    try:
        ctx2 = browser2.new_context(viewport={"width": 900, "height": 1200})
        page2 = ctx2.new_page()
        page2.goto(
            f"file:///{html_tmp.replace(os.sep, '/')}",
            wait_until="domcontentloaded",
            timeout=30_000,
        )
        try:
            page2.wait_for_load_state("networkidle", timeout=5_000)
        except Exception:
            pass
        page2.pdf(
            path=output_path,
            format="A4",
            print_background=True,
            margin={"top": "0.6in", "bottom": "0.6in", "left": "0.6in", "right": "0.6in"},
        )
    finally:
        ctx2.close()
        browser2.close()
    try:
        os.unlink(html_tmp)
    except OSError:
        pass


def _build_article_html(raw_html: str, page_title: str, url: str, *,
                        image_items: list | None = None,
                        url_to_data: dict | None = None,
                        dom_title: str = '') -> str:
    """Run Readability extraction and return a clean, self-contained HTML document."""
    import re as _re
    from urllib.parse import urlparse as _up

    _, article_html = _extract_with_readability(raw_html, url)
    # Title priority: DOM extraction > readability > page.title() fallback
    title = dom_title or page_title or "Article"

    # Strip any remaining scripts from extracted body
    article_html = _re.sub(r'<script[^>]*>.*?</script>', '', article_html, flags=_re.DOTALL | _re.IGNORECASE)
    article_html = _re.sub(r'<script[^>]*/>', '', article_html, flags=_re.IGNORECASE)

    # Reinsert images at their original positions (Readability strips img tags)
    if image_items and url_to_data:
        article_html, _ = _reinsert_images(article_html, image_items, url_to_data)

    domain = _up(url).hostname or url
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<style>{_ARTICLE_CSS}</style>
</head>
<body>
  <h1>{title}</h1>
  <p class="article-meta">Source: <a href="{url}">{domain}</a></p>
  <hr>
  {article_html}
</body>
</html>"""


def main():
    if len(sys.argv) < 3:
        print("Usage: pdf_worker.py <url> <output_path> [is_x]", file=sys.stderr)
        sys.exit(1)

    url = sys.argv[1]
    output_path = sys.argv[2]
    # Optional 3rd arg: "1" = force X/Twitter article mode
    is_x_override = len(sys.argv) > 3 and sys.argv[3] == "1"

    def status(msg: str):
        print(f"STATUS:{msg}", flush=True)

    cookies_file = os.path.join(os.path.dirname(__file__), "cookies.txt")

    from urllib.parse import urlparse as _urlparse
    from playwright.sync_api import sync_playwright

    hostname = _urlparse(url).hostname or ""
    is_x = is_x_override or hostname in ("x.com", "twitter.com") or hostname.endswith(".x.com") or hostname.endswith(".twitter.com")

    status("Launching browser...")
    with sync_playwright() as p:
        if is_x:
            # ----------------------------------------------------------------
            # For X.com: copy Firefox profile (cookies + localStorage) into a
            # temp dir, then use launch_persistent_context so Playwright's
            # Firefox loads the full authenticated session — no cookie injection
            # needed, this is the closest thing to "just use your Firefox".
            # ----------------------------------------------------------------
            ff_root = os.path.expandvars(r"%APPDATA%\Mozilla\Firefox\Profiles")
            ff_profile = None
            try:
                for entry in sorted(os.scandir(ff_root), key=lambda e: e.name):
                    if entry.is_dir() and "default-release" in entry.name:
                        ff_profile = entry.path
                        break
                if ff_profile is None:
                    dirs = [e.path for e in os.scandir(ff_root) if e.is_dir()]
                    ff_profile = dirs[0] if dirs else None
            except OSError:
                pass

            tmp_prof = os.path.join(tempfile.gettempdir(), "vt_ff_minprof")
            shutil.rmtree(tmp_prof, ignore_errors=True)
            os.makedirs(tmp_prof)

            if ff_profile:
                status(f"Copying Firefox session from: {ff_profile}")
                # Copy cookies + localStorage db files (Firefox is still running — SQLite allows this)
                for fname in [
                    "cookies.sqlite", "cookies.sqlite-wal", "cookies.sqlite-shm",
                    "webappsstore.sqlite", "webappsstore.sqlite-wal", "webappsstore.sqlite-shm",
                    "prefs.js",
                ]:
                    src = os.path.join(ff_profile, fname)
                    if os.path.isfile(src):
                        try:
                            shutil.copy2(src, os.path.join(tmp_prof, fname))
                        except OSError:
                            pass
                status("Session files copied.")
            else:
                status("WARNING: Firefox profile not found.")

            status("Starting Firefox browser...")
            context = p.firefox.launch_persistent_context(
                user_data_dir=tmp_prof,
                headless=True,
                firefox_user_prefs={
                    "dom.webdriver.enabled": False,
                    "useAutomationExtension": False,
                    "privacy.resistFingerprinting": False,
                    # Spoof a real Firefox UA so X.com doesn't flag headless
                    "general.useragent.override": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:136.0) "
                        "Gecko/20100101 Firefox/136.0"
                    ),
                    # Reduce fingerprinting signals
                    "media.navigator.enabled": True,
                    "media.peerconnection.enabled": False,
                    "devtools.debugger.remote-enabled": False,
                    "network.http.referer.XOriginPolicy": 0,
                    "network.http.referer.XOriginTrimmingPolicy": 0,
                },
            )
            context.add_init_script(_STEALTH_JS)
            status("Browser ready. Opening page...")

            page = context.new_page()

            # Passively capture image responses as the browser loads them.
            # Using page.on("response") is more reliable than route interception
            # with Firefox persistent contexts — no request interference at all.
            import base64 as _b64_cap
            import re as _re2
            captured_images: dict = {}  # url -> data-uri

            def _on_response(response):
                try:
                    rurl = response.url
                    if "pbs.twimg.com/media" not in rurl:
                        return
                    if not response.ok:
                        return
                    body = response.body()
                    if body:
                        ct = (response.headers.get("content-type") or "image/jpeg").split(";")[0].strip()
                        captured_images[rurl] = f"data:{ct};base64,{_b64_cap.b64encode(body).decode()}"
                except Exception:
                    pass

            page.on("response", _on_response)

            status("Loading page...")
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            except Exception:
                # X.com challenge/bot detection may block domcontentloaded from
                # firing. Fall back to "commit" (fires as soon as the HTTP
                # response starts) so we can still work with whatever loads.
                status("domcontentloaded timed out — retrying with minimal wait...")
                try:
                    page.goto(url, wait_until="commit", timeout=30_000)
                except Exception:
                    pass  # Proceed with whatever content is in the page

            status("Waiting for content to render...")
            try:
                page.add_style_tag(content=_X_HIDE_CSS)
            except Exception:
                pass
            try:
                page.wait_for_load_state("networkidle", timeout=20_000)
            except Exception:
                pass
            try:
                page.wait_for_selector(
                    "main [role='progressbar']", state="hidden", timeout=30_000
                )
                status("Article content loaded.")
            except Exception:
                status("Progressbar wait timed out, proceeding...")
                page.wait_for_timeout(4_000)

            # First pass: fast scroll to trigger image lazy-loading
            status("Scrolling page to load images...")
            try:
                page.evaluate("""async () => {
                    const h = Math.max(document.body.scrollHeight, 3000);
                    for (let y = 0; y < h; y += 400) {
                        window.scrollTo(0, y);
                        await new Promise(r => setTimeout(r, 60));
                    }
                    window.scrollTo(0, 0);
                    await new Promise(r => setTimeout(r, 800));
                }""")
            except Exception:
                pass

            # Wait for anything triggered by the first scroll
            try:
                page.wait_for_load_state("networkidle", timeout=5_000)
            except Exception:
                pass

            # Second pass: slow scroll that tracks expanding scrollHeight.
            # X articles lazy-load text content as the page grows — the first
            # pass uses a fixed height captured before content was rendered, so
            # the lower half of long articles is never scrolled into view.
            status("Loading full article content (slow scroll)...")
            try:
                page.evaluate("""async () => {
                    let prevHeight = 0;
                    for (let attempt = 0; attempt < 15; attempt++) {
                        const h = document.body.scrollHeight;
                        if (h === prevHeight) break;
                        prevHeight = h;
                        for (let y = 0; y < h; y += 300) {
                            window.scrollTo(0, y);
                            await new Promise(r => setTimeout(r, 200));
                        }
                        await new Promise(r => setTimeout(r, 1500));
                    }
                    window.scrollTo(0, 0);
                    await new Promise(r => setTimeout(r, 500));
                }""")
            except Exception:
                pass

            # Final networkidle to let any last requests settle
            try:
                page.wait_for_load_state("networkidle", timeout=5_000)
            except Exception:
                pass

            # Collect images and inject <span id="vt-img-N"> markers into the live DOM.
            # The markers survive page.content() and Readability extraction, enabling
            # precise marker-based image reinsertion via _reinsert_images.
            status("Collecting image positions...")
            image_items = _collect_page_images(page)
            # Keep only X media images; marker spans for non-media images are harmless.
            image_items = [it for it in image_items if 'pbs.twimg.com/media' in it.get('src', '')]

            # Identify cover images: those that appear before the article title element in
            # the DOM. Readability strips the pre-title area, so covers need special handling
            # (placed above <h1> in the final HTML rather than reinserted via marker).
            try:
                cover_srcs = set(page.evaluate("""() => {
                    const titleEl = document.querySelector('[data-testid="twitter-article-title"]');
                    if (!titleEl) return [];
                    return [...document.querySelectorAll('img')]
                        .filter(img => {
                            const src = img.currentSrc || img.src || '';
                            if (!src.includes('pbs.twimg.com/media')) return false;
                            // DOCUMENT_POSITION_FOLLOWING: titleEl comes after img → img is a cover
                            return !!(img.compareDocumentPosition(titleEl) & Node.DOCUMENT_POSITION_FOLLOWING);
                        })
                        .map(img => img.currentSrc || img.src || '');
                }""") or [])
            except Exception:
                cover_srcs = set()

            cover_items = [it for it in image_items if it.get('src', '') in cover_srcs]
            body_items  = [it for it in image_items if it.get('src', '') not in cover_srcs]

            # For X.com articles page.title() returns a generic "Username on X" tab title.
            # Extraction priority:
            #   1. og:title / twitter:title meta tags (most reliable — set by X to the exact title)
            #   2. Longest h1 inside the article container
            #   3. document.title stripped of the " / X" or " | X" suffix
            x_dom_title = ''
            try:
                x_dom_title = page.evaluate("""() => {
                    // 1. X article title element (most reliable — dedicated test ID)
                    const artTitle = document.querySelector('[data-testid="twitter-article-title"]');
                    if (artTitle) { const t = artTitle.textContent.trim(); if (t.length > 5) return t; }

                    // 2. Open Graph / Twitter Card meta tags
                    const ogTitle = document.querySelector('meta[property="og:title"]');
                    if (ogTitle) { const t = ogTitle.getAttribute('content') || ''; if (t.length > 15) return t; }
                    const twTitle = document.querySelector('meta[name="twitter:title"]');
                    if (twTitle) { const t = twTitle.getAttribute('content') || ''; if (t.length > 15) return t; }

                    // 3. Longest h1 inside the article content area
                    const containers = [
                        document.querySelector('article'),
                        document.querySelector('[data-testid="primaryColumn"]'),
                        document.querySelector('main'),
                        document.body,
                    ].filter(Boolean);
                    for (const container of containers) {
                        const h1s = [...container.querySelectorAll('h1')];
                        if (!h1s.length) continue;
                        const longest = h1s.reduce((a, b) =>
                            a.textContent.trim().length > b.textContent.trim().length ? a : b
                        );
                        const t = longest.textContent.trim();
                        if (t.length > 15) return t;
                    }

                    // 4. document.title — strip trailing " / X" or " | X"
                    const dt = document.title.replace(/\s*[\/|]\s*X\s*$/, '').trim();
                    if (dt.length > 15) return dt;

                    return '';
                }""") or ''
            except Exception:
                x_dom_title = ''

            raw_html = page.content()
            page_title = page.title()

            # Direct DOM extraction — bypasses Readability for X articles.
            # Readability penalises high link-density sections (e.g. cashtag-heavy
            # articles with $MU, $NVDA etc.) and silently drops them.  Pulling the
            # HTML straight from the article container avoids this scoring problem.
            status("Extracting article content from DOM...")
            x_dom_article_html = ''
            try:
                x_dom_article_html = page.evaluate("""() => {
                    // Walk up from the article title marker to a container that holds
                    // both the title and the body (> 500 chars of visible text).
                    const titleEl = document.querySelector(
                        '[data-testid="twitter-article-title"]'
                    );
                    if (!titleEl) return '';

                    let container = titleEl.parentElement;
                    for (let i = 0; i < 20 && container && container !== document.body; i++) {
                        if ((container.innerText || '').trim().length > 500) break;
                        container = container.parentElement;
                    }
                    if (!container || container === document.body) return '';

                    // Clone so we can strip chrome without touching the live DOM.
                    const clone = container.cloneNode(true);
                    const rmSelectors = [
                        'button', '[role="button"]',
                        'nav', '[role="navigation"]',
                        'aside', '[data-testid="sidebarColumn"]',
                        '[data-testid="UserAvatar-Container"]',
                        '[data-testid="placementTracking"]',
                        '[data-testid="DMDrawer"]',
                        'script', 'style', 'noscript',
                    ];
                    for (const sel of rmSelectors) {
                        clone.querySelectorAll(sel).forEach(el => el.remove());
                    }
                    return clone.innerHTML;
                }""") or ''
            except Exception:
                x_dom_article_html = ''

            url_to_data: dict = {}

            status(f"Downloading {len(image_items)} image(s)...")
            # Primary: in-page fetch() — uses Firefox's real cookie jar and HTTP cache.
            # This works even when response events didn't fire (e.g. cached images).
            all_srcs = [item["src"] for item in image_items]
            eval_results = _fetch_images_via_page_eval(page, all_srcs)
            url_to_data.update(eval_results)

            # Secondary: route-captured responses (images loaded fresh during page.goto).
            for item in image_items:
                src = item["src"]
                if src in url_to_data:
                    continue
                if src in captured_images:
                    url_to_data[src] = captured_images[src]
                    continue
                # normalised name= match
                norm_src = _re2.sub(r'[?&]name=[^&]*', '', src)
                for cap_url, cap_data in captured_images.items():
                    if _re2.sub(r'[?&]name=[^&]*', '', cap_url) == norm_src:
                        url_to_data[src] = cap_data
                        break
                    if cap_url.split("?")[0] == src.split("?")[0]:
                        url_to_data[src] = cap_data
                        break

            # Tertiary: page.request.get() for anything still missing
            still_missing = [item["src"] for item in image_items if item["src"] not in url_to_data]
            if still_missing:
                fallback = _download_page_images(page, [{"src": s} for s in still_missing])
                url_to_data.update(fallback)

            status(f"Downloaded {len(url_to_data)}/{len(image_items)} image(s).")

            context.close()

            # Readability extracts clean text (but strips images — we'll re-insert them)
            status("Extracting article text with Readability...")
            title, article_html = _extract_with_readability(raw_html, url)
            # Prefer the DOM-extracted title: Readability and page.title() both return
            # the generic X tab title ("Username on X") for article pages.
            if x_dom_title and len(x_dom_title) > len(title or ''):
                title = x_dom_title
            if not title:
                title = x_dom_title or page_title or "Article"

            # If direct DOM extraction captured significantly more text than Readability
            # (e.g. cashtag-heavy articles where Readability drops link-dense sections),
            # use the DOM version instead.
            if x_dom_article_html:
                _strip_tags = _re2.compile(r'<[^>]+>')
                dom_len  = len(_strip_tags.sub('', x_dom_article_html))
                read_len = len(_strip_tags.sub('', article_html))
                if dom_len > read_len * 1.3:
                    status(f"DOM extraction ({dom_len} chars) > Readability ({read_len} chars) — using DOM version.")
                    article_html = x_dom_article_html

            article_html = _re2.sub(r'<script[^>]*>.*?</script>', '', article_html,
                                    flags=_re2.DOTALL | _re2.IGNORECASE)
            article_html = _re2.sub(r'<script[^>]*/>', '', article_html, flags=_re2.IGNORECASE)

            # Re-insert body images using the marker spans injected by _collect_page_images.
            status("Inserting images into article...")
            article_html, inlined = _reinsert_images(article_html, body_items, url_to_data)
            status(f"Inserted {inlined} body image(s) into article.")

            # Build cover section (placed above the article title — Readability strips this area).
            cover_section = ''
            for item in cover_items:
                src = item.get('src', '')
                if src not in url_to_data:
                    continue
                cover_section += (
                    f'<figure style="margin:0 0 24px;text-align:center;">'
                    f'<img src="{url_to_data[src]}" alt="" '
                    f'style="max-width:100%;height:auto;border-radius:6px;">'
                    f'</figure>'
                )
            if cover_section:
                status(f"Placed {len(cover_items)} cover image(s) above title.")

            from urllib.parse import urlparse as _up2
            domain = _up2(url).hostname or url
            html_doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<style>{_ARTICLE_CSS}</style>
</head>
<body>
  {cover_section}
  <h1>{title}</h1>
  <p class="article-meta">Source: <a href="{url}">{domain}</a></p>
  <hr>
  {article_html}
</body>
</html>"""
            print(f"TITLE:{title}", flush=True)
            _render_html_to_pdf(p, html_doc, output_path, status)

        else:
            # ----------------------------------------------------------------
            # For all other sites: headless Chrome with cookie injection
            # ----------------------------------------------------------------
            try:
                browser = p.chromium.launch(
                    channel="chrome",
                    headless=True,
                    args=["--disable-blink-features=AutomationControlled"],
                )
            except Exception:
                browser = p.chromium.launch(
                    headless=True,
                    args=["--disable-blink-features=AutomationControlled"],
                )
            try:
                context = browser.new_context(
                    viewport={"width": 1280, "height": 900},
                    locale="en-US",
                    timezone_id="America/New_York",
                )
                context.add_init_script(_STEALTH_JS)
                site_cookies = _parse_netscape_cookies(cookies_file, url)
                if site_cookies:
                    context.add_cookies(site_cookies)
                    status(f"Loaded {len(site_cookies)} cookie(s) for this site...")
                page = context.new_page()
                status("Loading page...")
                page.goto(url, wait_until="domcontentloaded", timeout=60_000)
                status("Waiting for content to render...")
                try:
                    page.wait_for_load_state("networkidle", timeout=8_000)
                except Exception:
                    pass
                # Scroll to trigger lazy images
                try:
                    page.evaluate("""async () => {
                        const h = Math.max(document.body.scrollHeight, 3000);
                        for (let y = 0; y < h; y += 300) {
                            window.scrollTo(0, y);
                            await new Promise(r => setTimeout(r, 150));
                        }
                        window.scrollTo(0, 0);
                        await new Promise(r => setTimeout(r, 1000));
                    }""")
                except Exception:
                    pass
                # Wait for lazy-loaded images to finish loading after scroll
                try:
                    page.wait_for_load_state("networkidle", timeout=8_000)
                except Exception:
                    pass
                status("Collecting image positions...")
                image_items = _collect_page_images(page)
                status(f"Downloading {len(image_items)} image(s)...")
                # Primary: in-page fetch() — sends proper Referer and credentials,
                # works with CDNs that have hotlink protection.
                all_srcs = [item["src"] for item in image_items]
                url_to_data = _fetch_images_via_page_eval(page, all_srcs)
                # Fallback: page.request.get() for images that in-page fetch missed
                still_missing = [item for item in image_items if item["src"] not in url_to_data]
                if still_missing:
                    fallback = _download_page_images(page, still_missing)
                    url_to_data.update(fallback)
                status(f"Downloaded {len(url_to_data)} of {len(image_items)} image(s).")
                dom_title = _extract_dom_title(page)
                raw_html = page.content()
                page_title = page.title()
            finally:
                context.close()
                browser.close()

            html_doc = _build_article_html(raw_html, page_title, url,
                                           image_items=image_items, url_to_data=url_to_data,
                                           dom_title=dom_title)
            print(f"TITLE:{dom_title or page_title}", flush=True)
            _render_html_to_pdf(p, html_doc, output_path, status)

    print("DONE", flush=True)


if __name__ == "__main__":
    main()
