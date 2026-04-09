import argparse
import os
import sys
import tempfile

import whisper
import yt_dlp


_COOKIES_FILE = os.path.join(os.path.dirname(__file__), "cookies.txt")
_COOKIES_BROWSER = os.environ.get("YOUTUBE_COOKIES_BROWSER")  # e.g. "chrome", "firefox"


def download_audio(video_url: str, output_dir: str) -> str:
    """Download audio from a YouTube video and return the file path."""
    output_template = os.path.join(output_dir, "audio.%(ext)s")
    ydl_opts = {
        "format": "bestaudio/best",
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }
        ],
        "outtmpl": output_template,
        "quiet": False,
        "no_warnings": False,
    }
    if os.path.isfile(_COOKIES_FILE):
        ydl_opts["cookiefile"] = _COOKIES_FILE
    elif _COOKIES_BROWSER:
        ydl_opts["cookiesfrombrowser"] = (_COOKIES_BROWSER,)

    print(f"Downloading audio from: {video_url}")
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([video_url])

    audio_path = os.path.join(output_dir, "audio.mp3")
    if not os.path.exists(audio_path):
        raise FileNotFoundError("Audio download failed — file not found.")
    return audio_path


def transcribe_audio(audio_path: str, model_name: str, language: str | None) -> dict:
    """Transcribe an audio file using Whisper and return the result."""
    print(f"Loading Whisper model: {model_name}")
    model = whisper.load_model(model_name)

    print("Transcribing audio (this may take a while)...")
    options = {}
    if language:
        options["language"] = language
    result = model.transcribe(audio_path, **options)
    return result


def format_timestamp(seconds: float) -> str:
    """Convert seconds to HH:MM:SS format."""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def save_transcript(result: dict, output_path: str, include_timestamps: bool) -> None:
    """Save transcript to a text file."""
    with open(output_path, "w", encoding="utf-8") as f:
        if include_timestamps:
            for segment in result["segments"]:
                start = format_timestamp(segment["start"])
                end = format_timestamp(segment["end"])
                text = segment["text"].strip()
                f.write(f"[{start} -> {end}]  {text}\n")
        else:
            f.write(result["text"].strip())
            f.write("\n")
    print(f"Transcript saved to: {output_path}")


def main():
    parser = argparse.ArgumentParser(description="Generate transcript from a YouTube video.")
    parser.add_argument("url", help="YouTube video URL")
    parser.add_argument(
        "-m",
        "--model",
        default="base",
        choices=["tiny", "base", "small", "medium", "large"],
        help="Whisper model size (default: base). Larger = more accurate but slower.",
    )
    parser.add_argument(
        "-o",
        "--output",
        default=None,
        help="Output file path (default: transcript.txt in current directory)",
    )
    parser.add_argument(
        "-l",
        "--language",
        default=None,
        help="Language code (e.g. en, zh, de). Auto-detected if not specified.",
    )
    parser.add_argument(
        "--no-timestamps",
        action="store_true",
        help="Output plain text without timestamps",
    )
    args = parser.parse_args()

    output_path = args.output or "transcript.txt"

    with tempfile.TemporaryDirectory() as tmp_dir:
        audio_path = download_audio(args.url, tmp_dir)
        result = transcribe_audio(audio_path, args.model, args.language)

    save_transcript(result, output_path, include_timestamps=not args.no_timestamps)

    detected_lang = result.get("language", "unknown")
    print(f"Detected language: {detected_lang}")
    print("Done!")


if __name__ == "__main__":
    main()
