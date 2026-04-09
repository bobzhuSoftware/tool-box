# Video Transcript Generator

A web app that generates transcripts from YouTube videos using [yt-dlp](https://github.com/yt-dlp/yt-dlp) and [OpenAI Whisper](https://github.com/openai/whisper). Built with React + Vite (frontend) and FastAPI (backend).

## Prerequisites

- **Python 3.10+**
- **Node.js 18+**
- **FFmpeg** — must be installed and on your PATH
  - Windows: `winget install FFmpeg` or download from https://ffmpeg.org/download.html

## Setup

```bash
# Install Python dependencies
pip install -r requirements.txt

# Install frontend dependencies
cd frontend
npm install
```

## Running

Open two terminals:

```bash
# Terminal 1 — Start the backend (port 8000)
python -m uvicorn server:app --reload

# Terminal 2 — Start the frontend (port 5173)
cd frontend
npm run dev
```

Then open **http://localhost:5173** in your browser.

## Usage

1. Paste a YouTube link into the input field
2. Choose a Whisper model size and optionally set a language
3. Click **Transcribe** and wait for processing
4. View the transcript and download as a `.txt` file

## CLI Usage

You can also use the CLI directly:

```bash
python transcribe.py "https://www.youtube.com/watch?v=VIDEO_ID"
python transcribe.py "https://www.youtube.com/watch?v=VIDEO_ID" -m medium -l en
```

## YouTube Authentication (Bot Detection Fix)

If you see a "Sign in to confirm you're not a bot" error, YouTube is blocking yt-dlp. Fix it with one of these options:

**Option A — Export a `cookies.txt` file (recommended)**

1. Install the [Get cookies.txt LOCALLY](https://chromewebstore.google.com/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc) extension in Chrome/Edge
2. Log in to YouTube
3. Click the extension and export cookies for `youtube.com`
4. Save the file as `cookies.txt` in the project root (next to `server.py`)

The backend will detect and use it automatically on the next request.

**Option B — Use browser cookies directly (no file needed)**

Set the `YOUTUBE_COOKIES_BROWSER` environment variable to your browser name before starting the server:

```bash
# PowerShell
$env:YOUTUBE_COOKIES_BROWSER = "chrome"   # or "firefox", "edge", "brave"
python -m uvicorn server:app --reload
```

```bash
# CMD / bash
set YOUTUBE_COOKIES_BROWSER=chrome
python -m uvicorn server:app --reload
```

> Option A is more reliable. Option B requires the browser to be closed or may need elevated permissions on some systems.

## Whisper Model Sizes

| Model  | Parameters | Speed   | Accuracy |
|--------|-----------|---------|----------|
| tiny   | 39M       | Fastest | Lower    |
| base   | 74M       | Fast    | Good     |
| small  | 244M      | Medium  | Better   |
| medium | 769M      | Slow    | Great    |
| large  | 1550M     | Slowest | Best     |

Start with `base` for quick results. Use `medium` or `large` for production-quality transcripts.
