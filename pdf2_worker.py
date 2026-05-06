"""
PDF2 worker: extract article text + images with Readability, generate clean PDF.
Usage: python pdf2_worker.py <url> <output_path>
"""
import os
import sys
import tempfile


def status(msg: str):
    print(f"STATUS:{msg}", flush=True)


# Clean article CSS — simple, readable, print-friendly
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
    """Convert all img src to base64 data URLs while still in the browser."""
    try:
        page.evaluate("""async () => {
            const imgs = [...document.querySelectorAll('img')];
            await Promise.all(imgs.map(async img => {
                if (!img.src || img.src.startsWith('data:')) return;
                try {
                    const resp = await fetch(img.src, {credentials: 'include', mode: 'no-cors'});
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


def _extract_with_readability(html: str, url: str) -> tuple[str, str]:
    """Return (title, article_html) using readability-lxml."""
    try:
        from readability import Document
        doc = Document(html, url=url)
        return doc.title(), doc.summary(html_partial=False)
    except Exception:
        return "Article", html


def main():
    if len(sys.argv) < 3:
        print("Usage: pdf2_worker.py <url> <output_path>", file=sys.stderr)
        sys.exit(1)

    url = sys.argv[1]
    output_path = sys.argv[2]

    from playwright.sync_api import sync_playwright

    status("Launching browser...")
    with sync_playwright() as p:
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
            )

            # Inject cookies from cookies.txt for authenticated sites
            cookies_file = os.path.join(os.path.dirname(__file__), "cookies.txt")
            from urllib.parse import urlparse
            hostname = urlparse(url).hostname or ""
            try:
                from pdf_worker import _parse_netscape_cookies
                site_cookies = _parse_netscape_cookies(cookies_file, url)
                if site_cookies:
                    context.add_cookies(site_cookies)
                    status(f"Loaded {len(site_cookies)} cookie(s)...")
            except Exception:
                pass

            page = context.new_page()
            status("Loading page...")
            page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            try:
                page.wait_for_load_state("networkidle", timeout=8_000)
            except Exception:
                pass

            # Scroll to trigger lazy images
            status("Loading images...")
            try:
                page.evaluate("""async () => {
                    const h = Math.max(document.body.scrollHeight, 2000);
                    for (let y = 0; y < h; y += 400) {
                        window.scrollTo(0, y);
                        await new Promise(r => setTimeout(r, 60));
                    }
                    window.scrollTo(0, 0);
                    await new Promise(r => setTimeout(r, 500));
                }""")
            except Exception:
                pass

            # Inline images as base64 before grabbing HTML
            status("Inlining images...")
            _inline_images(page)

            raw_html = page.content()
            page_title = page.title()
            context.close()
            browser.close()
        except Exception:
            context.close()
            browser.close()
            raise

    # Extract article with Readability
    status("Extracting article content...")
    title, article_html = _extract_with_readability(raw_html, url)
    if not title:
        title = page_title or "Article"

    # Strip scripts from extracted HTML
    import re
    article_html = re.sub(r'<script[^>]*>.*?</script>', '', article_html, flags=re.DOTALL | re.IGNORECASE)
    article_html = re.sub(r'<script[^>]*/>', '', article_html, flags=re.IGNORECASE)

    # Build clean HTML document
    from urllib.parse import urlparse as _up
    domain = _up(url).hostname or url
    html_doc = f"""<!DOCTYPE html>
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

    # Write temp HTML
    html_tmp = output_path + ".html"
    with open(html_tmp, "w", encoding="utf-8") as f:
        f.write(html_doc)

    # Render to PDF with headless Chromium
    status("Generating PDF...")
    with sync_playwright() as p2:
        try:
            browser2 = p2.chromium.launch(channel="chrome", headless=True)
        except Exception:
            browser2 = p2.chromium.launch(headless=True)
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

    print("DONE", flush=True)


if __name__ == "__main__":
    main()
