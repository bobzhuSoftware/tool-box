"""
Standalone PDF generation worker.
Called as a subprocess to avoid asyncio event loop conflicts on Windows.
Prints STATUS:<message> lines to stdout so the parent can stream progress.
Usage: python pdf_worker.py <url> <output_path>
"""
import sys


def main():
    if len(sys.argv) != 3:
        print("Usage: pdf_worker.py <url> <output_path>", file=sys.stderr)
        sys.exit(1)

    url = sys.argv[1]
    output_path = sys.argv[2]

    def status(msg: str):
        print(f"STATUS:{msg}", flush=True)

    from playwright.sync_api import sync_playwright

    status("Launching browser...")
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--disable-blink-features=AutomationControlled"])
        try:
            page = browser.new_page(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                )
            )
            status("Loading page...")
            # Use "load" instead of "networkidle" — SPAs like X/Twitter never
            # become fully idle, so "networkidle" always times out.
            page.goto(url, wait_until="load", timeout=60_000)

            status("Waiting for content to render...")
            # Give JS-heavy pages a moment to finish rendering
            try:
                page.wait_for_load_state("networkidle", timeout=5_000)
            except Exception:
                pass  # Non-fatal — proceed even if still loading

            status("Generating PDF...")
            page.pdf(
                path=output_path,
                format="A4",
                print_background=True,
                margin={
                    "top": "0.5in",
                    "bottom": "0.5in",
                    "left": "0.5in",
                    "right": "0.5in",
                },
            )
        finally:
            browser.close()

    print("DONE", flush=True)


if __name__ == "__main__":
    main()
