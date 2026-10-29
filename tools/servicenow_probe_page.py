"""Read-only probe: open a ServiceNow page with a saved login identity and record every /api/now/ call.

Used to find out what data a page loads, e.g. the filter behind a Next Experience saved-list link
(.../list/params/list-id/<id>/tiny-id/<tiny>): its `getTinyListLayout` GraphQL response carries
`query` (an encoded query), `listTitle` and `tableLabel`. See docs/servicenow/condition-composer.md.

Usage (from the repo root, with the project's venv):
    python tools/servicenow_probe_page.py <url> [--identity-dir <dir>] [--out calls.json] [--wait 25]

--identity-dir is the identity folder that holds `edge/` and `session.json`, normally
%LOCALAPPDATA%\\VideoTranscript\\servicenow\\<user id>\\<identity id>. When omitted, the only identity
with a saved session is used. The page is only opened, nothing is changed in ServiceNow.
Opens a visible Edge window if the saved login has expired. Don't run it while the app is
refreshing counts with the same identity.
"""
import argparse
import asyncio
import json
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

_BODY_LIMIT = 200_000


def _find_identity_dir() -> str:
    root = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser(r"~\AppData\Local"), "VideoTranscript", "servicenow")
    found = []
    if os.path.isdir(root):
        for user in os.listdir(root):
            user_dir = os.path.join(root, user)
            if os.path.isdir(user_dir):
                found += [os.path.join(user_dir, d) for d in os.listdir(user_dir) if os.path.isfile(os.path.join(user_dir, d, "session.json"))]
    if len(found) != 1:
        sys.exit(f"Found {len(found)} identities with a saved session under {root}; pass --identity-dir.")
    return found[0]


def _operation(entry: dict) -> str:
    m = re.search(r'"operationName"\s*:\s*"([^"]+)"', entry.get("post") or "")
    return m.group(1) if m else ""


def _summarize(calls: list[dict]) -> None:
    print(f"\n{len(calls)} /api/now/ calls:")
    for i, c in enumerate(calls, 1):
        path = re.sub(r"^https://[^/]+", "", c["url"])[:110]
        print(f"  {i:>2} {c['method']:<4} {c['status']} {_operation(c):<28} {path}")
    for c in calls:
        if _operation(c) != "nowRecordListConnected" or not c.get("body"):
            continue
        try:
            layout = json.loads(c["body"])["data"]["GlideListLayout_Query"]["getTinyListLayout"]
        except (ValueError, KeyError, TypeError):
            continue
        print("\nTiny list found:")
        print("  listTitle :", layout.get("listTitle"))
        print("  tableLabel:", layout.get("tableLabel"))
        print("  query     :", layout.get("query"))


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("url")
    ap.add_argument("--identity-dir")
    ap.add_argument("--out", default="servicenow_probe.json")
    ap.add_argument("--wait", type=int, default=25, help="seconds to keep recording after the page opens")
    args = ap.parse_args()

    identity = args.identity_dir or _find_identity_dir()
    os.environ["VT_EDGE_AUTOMATION_DIR"] = os.path.join(identity, "edge")
    os.environ["VT_SN_SESSION_FILE"] = os.path.join(identity, "session.json")
    os.chdir(REPO)
    import servicenow_worker as w
    from playwright.async_api import async_playwright

    calls: list[dict] = []

    async def record(resp) -> None:
        if "/api/now/" not in resp.url:
            return
        entry = {"url": resp.url, "status": resp.status, "method": resp.request.method}
        try:
            entry["post"] = resp.request.post_data
            entry["body"] = (await resp.text())[:_BODY_LIMIT]
        except Exception as e:
            entry["error"] = str(e)
        calls.append(entry)

    automation_dir = w.ensure_automation_profile()
    w._prepare_profile(automation_dir)
    async with async_playwright() as p:
        session = await w._ensure_login(p, automation_dir, args.url)
        if not session:
            sys.exit("Login timed out.")
        ctx, page, _holder = session
        try:
            page.on("response", lambda r: asyncio.create_task(record(r)))
            await w._open(page, args.url)
            await asyncio.sleep(args.wait)
            print("final url:", page.url)
            await w._save_session(ctx)
        finally:
            await ctx.close()

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(calls, f, ensure_ascii=False, indent=1)
    print(f"saved {args.out}")
    _summarize(calls)


if __name__ == "__main__":
    asyncio.run(main())
