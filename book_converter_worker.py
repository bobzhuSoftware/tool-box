"""
Book format conversion worker.
Prints STATUS:<message> lines to stdout for progress streaming.
Final line is DONE or ERROR:<message>.

Usage:
    python book_converter_worker.py <input_path> <output_path> <direction>
    direction: 'epub2pdf' | 'pdf2epub'
"""
import os
import sys
import tempfile
import re
import base64
import shutil
import subprocess

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def log(msg: str) -> None:
    print(f"STATUS:{msg}", flush=True)


# ---------------------------------------------------------------------------
# EPUB → PDF  provider chain: Calibre (local) → CloudConvert → Zamzar
# ---------------------------------------------------------------------------

CLOUDCONVERT_API_KEY = os.environ.get("CLOUDCONVERT_API_KEY", "")
ZAMZAR_API_KEY = os.environ.get("ZAMZAR_API_KEY", "")


def _resolve_calibre_candidate(custom: str) -> str | None:
    """Resolve a user-supplied Calibre location.

    ``custom`` may point directly at the ``ebook-convert`` executable or at the
    Calibre install directory that contains it.
    """
    custom = custom.strip().strip('"')
    if not custom:
        return None
    if os.path.isfile(custom):
        return custom
    if os.path.isdir(custom):
        exe_name = "ebook-convert.exe" if sys.platform == "win32" else "ebook-convert"
        cand = os.path.join(custom, exe_name)
        if os.path.isfile(cand):
            return cand
    return None


def _find_ebook_convert(custom_path: str | None = None) -> str | None:
    """Return path to ebook-convert executable, or None if not found.

    Resolution order: explicit ``custom_path`` / ``VT_CALIBRE_PATH`` env (a file
    or install dir) -> PATH -> common install locations.
    """
    custom = custom_path or os.environ.get("VT_CALIBRE_PATH", "")
    resolved = _resolve_calibre_candidate(custom) if custom else None
    if resolved:
        return resolved
    exe = shutil.which("ebook-convert")
    if exe:
        return exe
    if sys.platform == "win32":
        candidates = [
            r"C:\Program Files\Calibre2",
            r"C:\Program Files (x86)\Calibre2",
            os.path.expanduser(r"~\calibre-portable\Calibre"),
            os.path.expanduser(r"~\calibre-portable2\Calibre"),
            os.path.expanduser(r"~\AppData\Local\Calibre2"),
        ]
        for d in candidates:
            path = os.path.join(d, "ebook-convert.exe")
            if os.path.isfile(path):
                return path
    return None


def _epub_to_pdf_calibre(input_path: str, output_path: str, ebook_convert: str) -> None:
    cmd = [
        ebook_convert, input_path, output_path,
        "--paper-size", "a4",
        "--pdf-page-margin-top", "72",
        "--pdf-page-margin-bottom", "72",
        "--pdf-page-margin-left", "60",
        "--pdf-page-margin-right", "60",
        "--pdf-default-font-size", "20",
        "--pdf-mono-font-size", "17",
        "--preserve-cover-aspect-ratio",
        "--pdf-sans-family", "Noto Sans",
        "--pdf-serif-family", "Noto Serif",
        "--chapter-mark", "pagebreak",
        "--pdf-add-toc",
    ]
    xvfb_run = shutil.which("xvfb-run")
    if xvfb_run and sys.platform != "win32":
        cmd = [xvfb_run, "--auto-servernum", "--server-args=-screen 0 1024x768x24"] + cmd

    log("Using Calibre ebook-convert…")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired:
        raise ValueError("转换超时（超过10分钟），EPUB 文件可能过大或过于复杂。")

    if result.stdout:
        for line in result.stdout.split("\n"):
            line = line.strip()
            if line and ("%" in line or "Converting" in line or "Output" in line):
                log(line[:100])

    if result.returncode != 0:
        err = (result.stderr.strip() or result.stdout.strip()).split("\n")
        raise ValueError(f"Calibre 转换失败：\n{chr(10).join(err[-5:])}")

    if not os.path.exists(output_path):
        raise ValueError("转换完成但未生成 PDF 文件。")


def _epub_to_pdf_cloudconvert(input_path: str, output_path: str) -> None:
    import urllib.request
    import urllib.parse
    import json as _json
    import time

    log("Using CloudConvert API…")
    headers = {
        "Authorization": f"Bearer {CLOUDCONVERT_API_KEY}",
        "Content-Type": "application/json",
    }

    # 1. Create job: upload → convert → export
    job_body = _json.dumps({
        "tasks": {
            "upload-file": {"operation": "import/upload"},
            "convert-file": {
                "operation": "convert",
                "input": "upload-file",
                "input_format": "epub",
                "output_format": "pdf",
                "engine": "calibre",
                "options": {
                    "page_size": "a4",
                    "font_size": 14,
                },
            },
            "export-file": {
                "operation": "export/url",
                "input": "convert-file",
            },
        }
    }).encode()

    req = urllib.request.Request(
        "https://api.cloudconvert.com/v2/jobs",
        data=job_body,
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        job_data = _json.loads(resp.read())

    job_id = job_data["data"]["id"]
    upload_task = next(t for t in job_data["data"]["tasks"] if t["name"] == "upload-file")
    upload_url = upload_task["result"]["form"]["url"]
    upload_params = upload_task["result"]["form"]["parameters"]

    # 2. Upload file via multipart form
    log("Uploading EPUB to CloudConvert…")
    boundary = "----FormBoundary" + os.urandom(8).hex()
    body_parts = []
    for k, v in upload_params.items():
        body_parts.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n"
            .encode()
        )
    with open(input_path, "rb") as f:
        file_data = f.read()
    fname = os.path.basename(input_path)
    body_parts.append(
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{fname}\"\r\nContent-Type: application/epub+zip\r\n\r\n"
        .encode() + file_data + f"\r\n--{boundary}--\r\n".encode()
    )
    upload_body = b"".join(body_parts)
    upload_req = urllib.request.Request(
        upload_url,
        data=upload_body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    with urllib.request.urlopen(upload_req, timeout=60) as r:
        r.read()

    # 3. Poll for completion
    log("Converting… (this may take a moment)")
    for attempt in range(60):
        time.sleep(5)
        poll_req = urllib.request.Request(
            f"https://api.cloudconvert.com/v2/jobs/{job_id}",
            headers=headers,
        )
        with urllib.request.urlopen(poll_req, timeout=30) as r:
            status_data = _json.loads(r.read())
        job_status = status_data["data"]["status"]
        if job_status == "finished":
            break
        elif job_status == "error":
            raise ValueError("CloudConvert 转换失败，请检查 EPUB 文件是否有效。")
        if attempt % 6 == 0:
            log(f"Still converting… ({(attempt + 1) * 5}s)")
    else:
        raise ValueError("CloudConvert 转换超时（5分钟）。")

    # 4. Download result
    log("Downloading converted PDF…")
    export_task = next(t for t in status_data["data"]["tasks"] if t["name"] == "export-file")
    download_url = export_task["result"]["files"][0]["url"]
    with urllib.request.urlopen(download_url, timeout=60) as r:
        with open(output_path, "wb") as f:
            f.write(r.read())


def _epub_to_pdf_zamzar(input_path: str, output_path: str) -> None:
    import urllib.request
    import urllib.parse
    import json as _json
    import base64 as _b64
    import time

    log("Using Zamzar API…")
    auth = _b64.b64encode(f"{ZAMZAR_API_KEY}:".encode()).decode()
    auth_header = {"Authorization": f"Basic {auth}"}

    # 1. Submit job
    boundary = "----FormBoundary" + os.urandom(8).hex()
    fname = os.path.basename(input_path)
    with open(input_path, "rb") as f:
        file_data = f.read()

    body = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"target_format\"\r\n\r\npdf\r\n"
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"source_file\"; filename=\"{fname}\"\r\nContent-Type: application/epub+zip\r\n\r\n"
    ).encode() + file_data + f"\r\n--{boundary}--\r\n".encode()

    submit_req = urllib.request.Request(
        "https://api.zamzar.com/v1/jobs",
        data=body,
        headers={**auth_header, "Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    log("Uploading EPUB to Zamzar…")
    with urllib.request.urlopen(submit_req, timeout=60) as r:
        job_data = _json.loads(r.read())
    job_id = job_data["id"]

    # 2. Poll for completion
    log("Converting… (this may take a moment)")
    for attempt in range(60):
        time.sleep(5)
        poll_req = urllib.request.Request(
            f"https://api.zamzar.com/v1/jobs/{job_id}",
            headers=auth_header,
        )
        with urllib.request.urlopen(poll_req, timeout=30) as r:
            status_data = _json.loads(r.read())
        status = status_data.get("status")
        if status == "successful":
            break
        elif status == "failed":
            raise ValueError("Zamzar 转换失败，请检查 EPUB 文件是否有效。")
        if attempt % 6 == 0:
            log(f"Still converting… ({(attempt + 1) * 5}s)")
    else:
        raise ValueError("Zamzar 转换超时（5分钟）。")

    # 3. Download result
    log("Downloading converted PDF…")
    file_id = status_data["target_files"][0]["id"]
    dl_req = urllib.request.Request(
        f"https://api.zamzar.com/v1/files/{file_id}/content",
        headers=auth_header,
    )
    with urllib.request.urlopen(dl_req, timeout=60) as r:
        with open(output_path, "wb") as f:
            f.write(r.read())


def epub_to_pdf(input_path: str, output_path: str) -> None:
    import zipfile

    # Pre-validate: EPUB must be a valid ZIP archive
    if not zipfile.is_zipfile(input_path):
        raise ValueError(
            "该文件不是有效的 EPUB 文件（EPUB 本质上是 ZIP 压缩包，"
            "此文件无法被识别为合法的 ZIP 结构）。"
            "请检查文件是否损坏，或尝试用电子书阅读器打开确认其完整性。"
        )

    # Provider chain: Calibre → CloudConvert → Zamzar
    ebook_convert = _find_ebook_convert()

    if ebook_convert:
        _epub_to_pdf_calibre(input_path, output_path, ebook_convert)
    elif CLOUDCONVERT_API_KEY:
        _epub_to_pdf_cloudconvert(input_path, output_path)
    elif ZAMZAR_API_KEY:
        _epub_to_pdf_zamzar(input_path, output_path)
    else:
        raise ValueError(
            "没有可用的转换引擎。\n"
            "请满足以下任一条件：\n"
            "  1. 安装 Calibre（https://calibre-ebook.com）\n"
            "  2. 设置环境变量 CLOUDCONVERT_API_KEY\n"
            "  3. 设置环境变量 ZAMZAR_API_KEY"
        )

    if not os.path.exists(output_path):
        raise ValueError("转换完成但未生成 PDF 文件，请检查 EPUB 文件是否有效。")

    file_size = os.path.getsize(output_path)
    log(f"PDF generated successfully ({file_size / 1024 / 1024:.1f} MB)")


# ---------------------------------------------------------------------------
# PDF → EPUB  (via PyMuPDF text extraction + ebooklib)
# ---------------------------------------------------------------------------
def pdf_to_epub(input_path: str, output_path: str) -> None:
    import fitz  # PyMuPDF
    import ebooklib
    from ebooklib import epub
    import uuid as _uuid

    log("Opening PDF…")
    try:
        doc = fitz.open(input_path)
    except Exception as e:
        raise ValueError(
            f"PDF 文件无法打开：{e}。"
            "请确认文件是完整的 PDF，且未设置打开密码。"
        ) from e

    if doc.is_encrypted:
        doc.close()
        raise ValueError(
            "该 PDF 文件已加密，无法提取内容。"
            "请先用 PDF 工具移除密码保护后再转换。"
        )

    total = len(doc)
    if total == 0:
        doc.close()
        raise ValueError("该 PDF 文件没有任何页面内容。")
    log(f"Total pages: {total}")

    meta = doc.metadata or {}
    title = (meta.get("title") or "").strip() or os.path.splitext(os.path.basename(input_path))[0]
    author = (meta.get("author") or "").strip()

    book = epub.EpubBook()
    book.set_identifier(_uuid.uuid4().hex)
    book.set_title(title)
    book.set_language("en")
    if author:
        book.add_author(author)

    chapters: list[epub.EpubHtml] = []
    PAGES_PER_CHAPTER = 5

    for start in range(0, total, PAGES_PER_CHAPTER):
        end = min(start + PAGES_PER_CHAPTER, total)
        chapter_num = start // PAGES_PER_CHAPTER + 1
        log(f"Processing pages {start + 1}–{end}…")

        body_parts: list[str] = []
        for pn in range(start, end):
            page = doc[pn]
            # Use "blocks" for better paragraph detection
            blocks = page.get_text("blocks")
            body_parts.append(
                f'<p class="page-marker">— Page {pn + 1} —</p>'
            )
            for block in blocks:
                # block: (x0, y0, x1, y1, text, block_no, block_type)
                if len(block) >= 5 and block[6] == 0:  # type 0 = text
                    text = block[4].strip()
                    if text:
                        # Preserve line breaks within a block as <br/> but
                        # treat the whole block as one paragraph
                        safe = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                        lines = safe.split("\n")
                        body_parts.append("<p>" + "<br/>".join(lines) + "</p>")

        chapter_content = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<!DOCTYPE html>\n'
            '<html xmlns="http://www.w3.org/1999/xhtml">\n'
            f'<head><title>Chapter {chapter_num}</title>\n'
            '<style>'
            'body{font-family:serif;line-height:1.6;margin:1em 2em;}'
            '.page-marker{color:#999;font-size:0.75em;text-align:center;'
            'border-top:1px solid #ddd;margin:1em 0 0.5em;}'
            '</style></head>\n'
            f'<body><h2>Chapter {chapter_num} '
            f'<span style="font-size:0.65em;color:#666;">'
            f'(pp. {start + 1}–{end})</span></h2>\n'
            + "".join(body_parts)
            + "\n</body></html>"
        )

        c = epub.EpubHtml(
            title=f"Chapter {chapter_num}",
            file_name=f"ch{chapter_num:04d}.xhtml",
            lang="en",
        )
        c.content = chapter_content.encode("utf-8")
        book.add_item(c)
        chapters.append(c)

    doc.close()

    log("Building EPUB structure…")
    book.toc = [epub.Link(c.file_name, c.title, c.id) for c in chapters]
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = ["nav"] + chapters

    epub.write_epub(output_path, book)
    log("EPUB written successfully.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    if len(sys.argv) != 4:
        print("ERROR:Usage: book_converter_worker.py <input> <output> <epub2pdf|pdf2epub>")
        sys.exit(1)

    input_path, output_path, direction = sys.argv[1], sys.argv[2], sys.argv[3]

    try:
        if direction == "epub2pdf":
            epub_to_pdf(input_path, output_path)
        elif direction == "pdf2epub":
            pdf_to_epub(input_path, output_path)
        else:
            print(f"ERROR:Unknown direction '{direction}'")
            sys.exit(1)
        print("DONE", flush=True)
    except Exception as exc:
        print(f"ERROR:{exc}", flush=True)
        sys.exit(1)
