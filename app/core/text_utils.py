"""Pure text / subtitle helpers (no DB or network dependencies)."""
import re
import unicodedata

try:
    import zhconv
    _has_zhconv = True
except ImportError:
    _has_zhconv = False


def to_simplified(text: str, language: str) -> str:
    """Convert Traditional Chinese to Simplified if language is Chinese."""
    if _has_zhconv and (language.startswith('zh') or language in ('yue', 'chinese', 'cantonese')):
        return zhconv.convert(text, 'zh-hans')
    return text


def sanitize_filename(title: str) -> str:
    """Strip characters that are invalid in filenames and limit length."""
    title = re.sub(r'[\\/:*?"<>|]', '', title)
    title = re.sub(r'\s+', ' ', title).strip()
    return title[:80] or "transcript"


def format_timestamp(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _ts_to_seconds(ts: str) -> int:
    """Convert 'HH:MM:SS' timestamp string back to total seconds."""
    parts = ts.split(':')
    return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])


def _count_words(text: str) -> int:
    """Count words in a language-aware way (CJK chars count individually)."""
    cjk = sum(1 for c in text if unicodedata.east_asian_width(c) in ('W', 'F'))
    if cjk > len(text) * 0.3:
        return cjk
    return len(text.split())


def _parse_vtt(content: str) -> list[dict]:
    """
    Parse a WebVTT subtitle file into Whisper-compatible segment dicts.

    Returns: [{"start": float, "end": float, "text": str}, ...]

    Handles:
    - YouTube timing tags like <c>, <00:00:00.000>
    - HTML tags
    - Consecutive duplicate cue blocks (YouTube auto-caption overlap)
    """
    # Strip YouTube timing/colour tags and HTML
    content = re.sub(r'<[^>]+>', '', content)

    cue_re = re.compile(
        r'(\d{1,2}:\d{2}:\d{2}[.,]\d{3})\s*-->\s*(\d{1,2}:\d{2}:\d{2}[.,]\d{3})[^\n]*\n'
        r'((?:(?!\d{1,2}:\d{2}:\d{2}).*\n?)*)',
        re.MULTILINE,
    )

    def ts_to_sec(ts: str) -> float:
        ts = ts.replace(',', '.')
        parts = ts.split(':')
        h, m, s = int(parts[0]), int(parts[1]), float(parts[2])
        return h * 3600 + m * 60 + s

    segments: list[dict] = []
    prev_text: str = ''
    for m in cue_re.finditer(content):
        start_s = ts_to_sec(m.group(1))
        end_s   = ts_to_sec(m.group(2))
        text    = m.group(3).strip()
        if not text or text == prev_text:
            continue
        # Skip WebVTT NOTE blocks and header lines
        if text.upper().startswith('NOTE') or text.upper().startswith('WEBVTT'):
            continue
        segments.append({"start": start_s, "end": end_s, "text": text})
        prev_text = text

    return segments


def merge_segments(
    raw_segments: list[dict],
    min_words: int = 40,
    max_words: int = 60,
) -> list[dict]:
    """
    Merge short Whisper segments into semantically coherent chunks.
    Flushes when word count reaches min_words AND the segment ends with
    sentence-ending punctuation, or unconditionally at max_words.
    """
    SENTENCE_END = re.compile(r'[.?!。？！…]+\s*$')

    merged: list[dict] = []
    buf: list[dict] = []
    buf_words = 0

    for seg in raw_segments:
        text = seg["text"].strip()
        buf.append(seg)
        buf_words += _count_words(text)

        at_boundary = bool(SENTENCE_END.search(text))
        if (buf_words >= min_words and at_boundary) or buf_words >= max_words:
            merged.append({
                "start": format_timestamp(buf[0]["start"]),
                "end": format_timestamp(buf[-1]["end"]),
                "text": " ".join(s["text"].strip() for s in buf),
            })
            buf = []
            buf_words = 0

    if buf:
        merged.append({
            "start": format_timestamp(buf[0]["start"]),
            "end": format_timestamp(buf[-1]["end"]),
            "text": " ".join(s["text"].strip() for s in buf),
        })

    return merged


_VTT_TS_RE = re.compile(
    r'^(\d{1,2}):(\d{2}):(\d{2})(?:[.,](\d{1,3}))?\s*-->\s*'
    r'(\d{1,2}):(\d{2}):(\d{2})(?:[.,](\d{1,3}))?'
)


def _split_vtt_into_chunks(vtt_text: str, chunk_secs: int) -> list[str]:
    """Split a cleaned WebVTT string into N-second windows.

    Returns a list of VTT-formatted strings (each with its own WEBVTT header).
    Windows are anchored to the first cue's start time so the first chunk
    spans [t0, t0+chunk_secs), the next [t0+chunk_secs, t0+2*chunk_secs), etc.
    """
    lines = vtt_text.splitlines()
    cues: list[tuple[float, str]] = []  # (start_sec, "ts_line\ntext_lines...")

    i = 0
    # Skip lines up to and including the WEBVTT header.
    while i < len(lines) and not lines[i].strip().startswith("WEBVTT"):
        i += 1
    if i < len(lines):
        i += 1

    while i < len(lines):
        line = lines[i].strip()
        if not line:
            i += 1
            continue
        m = _VTT_TS_RE.match(line)
        if not m:
            # Stray line — skip
            i += 1
            continue
        h, mm, ss = int(m.group(1)), int(m.group(2)), int(m.group(3))
        ms = int(m.group(4) or 0)
        start_sec = h * 3600 + mm * 60 + ss + ms / 1000.0
        ts_line = line
        i += 1
        text_lines: list[str] = []
        while i < len(lines) and lines[i].strip():
            text_lines.append(lines[i])
            i += 1
        cue_block = ts_line + "\n" + "\n".join(text_lines)
        cues.append((start_sec, cue_block))

    if not cues:
        return ["WEBVTT\n\n" + vtt_text.strip() + "\n"]

    chunks: list[list[str]] = []
    current: list[str] = []
    chunk_start_sec = cues[0][0]
    for start_sec, block in cues:
        if current and (start_sec - chunk_start_sec) >= chunk_secs:
            chunks.append(current)
            current = []
            chunk_start_sec = start_sec
        current.append(block)
    if current:
        chunks.append(current)

    return ["WEBVTT\n\n" + "\n\n".join(blocks) + "\n" for blocks in chunks]
