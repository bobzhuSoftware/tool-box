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


_SRT_TS_RE = re.compile(
    r'(\d{1,2}:\d{2}:\d{2}[.,]\d{1,3})\s*-->\s*(\d{1,2}:\d{2}:\d{2}[.,]\d{1,3})'
)


def _parse_srt(content: str) -> list[dict]:
    """
    Parse a SubRip (.srt) subtitle file into Whisper-compatible segment dicts.

    Returns: [{"start": float, "end": float, "text": str}, ...]

    Blocks are separated by blank lines. Each block is: an optional numeric
    index line, a ``HH:MM:SS,mmm --> HH:MM:SS,mmm`` timing line, then one or
    more text lines. HTML tags are stripped.
    """
    def ts_to_sec(ts: str) -> float:
        ts = ts.replace(',', '.')
        h, m, s = ts.split(':')
        return int(h) * 3600 + int(m) * 60 + float(s)

    segments: list[dict] = []
    blocks = re.split(r'\n\s*\n', content.replace('\r\n', '\n').replace('\r', '\n'))
    for block in blocks:
        lines = [ln for ln in block.split('\n') if ln.strip() != '']
        if not lines:
            continue
        # Locate the timing line (usually line 0 or 1).
        ts_idx = None
        ts_match = None
        for idx, ln in enumerate(lines):
            m = _SRT_TS_RE.search(ln)
            if m:
                ts_idx, ts_match = idx, m
                break
        if ts_match is None:
            continue
        text = re.sub(r'<[^>]+>', '', ' '.join(lines[ts_idx + 1:])).strip()
        if not text:
            continue
        segments.append({
            "start": ts_to_sec(ts_match.group(1)),
            "end": ts_to_sec(ts_match.group(2)),
            "text": text,
        })

    return segments


def _parse_plaintext(content: str) -> list[dict]:
    """
    Turn a plain-text file into pseudo-segments (no real timing).

    Paragraphs are separated by blank lines; if there is only one block the
    whole file becomes a single segment. All timestamps are 0 because plain
    text carries no timing information.
    """
    blocks = [b.strip() for b in re.split(r'\n\s*\n', content) if b.strip()]
    if not blocks:
        stripped = content.strip()
        blocks = [stripped] if stripped else []
    return [{"start": 0.0, "end": 0.0, "text": b} for b in blocks]


def parse_subtitle(content: str, ext: str) -> list[dict]:
    """
    Dispatch subtitle parsing by file extension.

    Supported: ``.vtt`` (WebVTT), ``.srt`` (SubRip), ``.txt`` (plain text).
    Unknown extensions fall back to plain-text handling.

    Returns raw segment dicts with float ``start``/``end`` seconds — feed the
    result through ``merge_segments`` to get display-ready chunks.
    """
    ext = (ext or "").lower()
    if ext == ".vtt":
        return _parse_vtt(content)
    if ext == ".srt":
        return _parse_srt(content)
    return _parse_plaintext(content)


# ---------------------------------------------------------------------------
# Teams / speaker-aware WebVTT handling
# ---------------------------------------------------------------------------
# Microsoft Teams meeting transcripts are WebVTT files that carry the speaker
# inside a ``<v Speaker Name>...</v>`` voice tag and use UUID cue identifiers.
_VTT_VOICE_OPEN_RE = re.compile(r'<v\s+([^>]*)>', re.IGNORECASE)
_VTT_CUE_ID_RE = re.compile(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/\d+-\d+\s*$',
    re.IGNORECASE,
)
_VTT_CUE_TS_RE = re.compile(
    r'(\d{1,2}):(\d{2}):(\d{2})[.,](\d{1,3})\s*-->\s*'
    r'(\d{1,2}):(\d{2}):(\d{2})[.,](\d{1,3})'
)


def _parse_vtt_with_speakers(content: str) -> list[dict]:
    """Parse a Teams-style WebVTT (with ``<v Speaker>`` tags) line-by-line.

    Each cue is returned as a separate raw segment carrying the speaker name
    verbatim (including any org suffix) in a dedicated ``speaker`` field; the
    ``text`` field holds only the utterance. Returns raw segment dicts with
    float ``start``/``end`` seconds — feed them through
    ``_merge_speaker_segments`` to combine consecutive same-speaker cues.
    """
    lines = content.replace('\r\n', '\n').replace('\r', '\n').split('\n')
    segments: list[dict] = []
    i, n = 0, len(lines)

    while i < n:
        line = lines[i]
        m = _VTT_CUE_TS_RE.search(line)
        if not m:
            i += 1
            continue
        start = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3)) + int(m.group(4).ljust(3, '0')) / 1000
        end = int(m.group(5)) * 3600 + int(m.group(6)) * 60 + int(m.group(7)) + int(m.group(8).ljust(3, '0')) / 1000
        i += 1

        # Collect the cue's text lines (stop at a blank line, the next cue id,
        # or the next timestamp line).
        parts: list[str] = []
        while i < n:
            t = lines[i].strip()
            if not t:
                i += 1
                break
            if '-->' in t or _VTT_CUE_ID_RE.match(t):
                break
            parts.append(t)
            i += 1

        block = ' '.join(parts).strip()
        if not block:
            continue

        voice = _VTT_VOICE_OPEN_RE.search(block)
        speaker = voice.group(1).strip() if voice else ''
        body = re.sub(r'<[^>]+>', '', block).strip()
        if not body:
            continue
        segments.append({"start": start, "end": end, "speaker": speaker, "text": body})

    return segments


def _merge_speaker_segments(
    raw_segments: list[dict],
    min_words: int = 40,
    max_words: int = 60,
) -> list[dict]:
    """Merge cues into longer chunks by word count, with inline speaker labels.

    Cues are ordered by start time, then packed purely by word count (like a
    normal transcript) so time spans are long and uniform regardless of how
    often the speaker changes. Within a chunk a ``Speaker:`` label is inserted
    only when the speaker changes (always for the chunk's first utterance). A
    speaker's turn may span a chunk boundary — the reader sees it continues from
    the last-named speaker. Returns dicts with float ``start``/``end`` seconds.

    Flushes when the word count reaches ``min_words`` at a sentence boundary, or
    unconditionally at ``max_words``.
    """
    SENTENCE_END = re.compile(r'[.?!。？！…]+["\')\]]?\s*$')

    ordered = sorted(raw_segments, key=lambda s: s["start"])

    merged: list[dict] = []
    buf: list[dict] = []
    buf_words = 0

    def render(cues: list[dict]) -> str:
        parts: list[str] = []
        prev_speaker: str | None = None
        for c in cues:
            sp = c.get("speaker", "")
            body = c["text"].strip()
            parts.append(f"{sp}: {body}" if sp and sp != prev_speaker else body)
            prev_speaker = sp
        return " ".join(parts).strip()

    def flush():
        nonlocal buf, buf_words
        if not buf:
            return
        merged.append({
            "start": buf[0]["start"],
            "end": buf[-1]["end"],
            "text": render(buf),
        })
        buf = []
        buf_words = 0

    for seg in ordered:
        buf.append(seg)
        buf_words += _count_words(seg["text"])
        at_boundary = bool(SENTENCE_END.search(seg["text"].strip()))
        if (buf_words >= min_words and at_boundary) or buf_words >= max_words:
            flush()

    flush()
    return merged


def build_subtitle_segments(content: str, ext: str) -> tuple[list[dict], str]:
    """Turn an uploaded subtitle file into display-ready segments.

    Returns ``(segments, full_text)`` where each segment carries string
    ``HH:MM:SS`` ``start``/``end`` timestamps.

    Teams-style WebVTT (containing ``<v Speaker>`` voice tags) is handled with a
    speaker-aware parser: cues are packed into ~40-60 word chunks (long, uniform
    time spans like a normal transcript) with an inline ``Speaker:`` label
    inserted whenever the speaker changes. Every other subtitle is parsed and
    then merged into ~40-60 word chunks.
    """
    ext = (ext or "").lower()
    if ext == ".vtt" and _VTT_VOICE_OPEN_RE.search(content):
        raw = _parse_vtt_with_speakers(content)
        merged = _merge_speaker_segments(raw)
        segments = [
            {
                "start": format_timestamp(s["start"]),
                "end": format_timestamp(s["end"]),
                "text": s["text"],
            }
            for s in merged
        ]
        full_text = "\n".join(s["text"] for s in merged)
        return segments, full_text

    raw = parse_subtitle(content, ext)
    segments = merge_segments(raw)
    full_text = " ".join(s["text"].strip() for s in raw)
    return segments, full_text


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
