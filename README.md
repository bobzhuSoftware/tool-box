# Video Transcript Generator

A web app that generates transcripts from YouTube videos using [yt-dlp](https://github.com/yt-dlp/yt-dlp) and [OpenAI Whisper](https://github.com/openai/whisper). Built with React + Vite (frontend) and FastAPI (backend).

## Prerequisites

- **Python 3.10+**
- **Node.js 18+**
- **FFmpeg** — must be installed and on your PATH
  - Windows: `winget install FFmpeg` or download from https://ffmpeg.org/download.html

## Setup

**One-click setup (recommended)** — creates a `.venv` virtual environment and installs all dependencies:

```bash
npm run setup
```

This runs:
1. `python -m venv .venv` — creates an isolated Python environment in the project folder
2. `.venv\Scripts\python.exe -m pip install -r requirements.txt` — installs Python packages into the venv
3. `npm install` inside `frontend/`

Or set up manually:

```bash
# Create and activate the virtual environment
python -m venv .venv
.venv\Scripts\activate   # Windows PowerShell / CMD

# Install Python dependencies into the venv
pip install -r requirements.txt

# Install frontend Node dependencies
cd frontend && npm install
```

## Running

> **Prerequisite:** Run `npm run setup` once to create the `.venv` virtual environment before starting.

**One-click start (recommended):**

```bash
npm run dev
```

This starts both the backend (port 8000) and frontend (port 5173) simultaneously using `concurrently`.

**Or start separately in two terminals:**

```bash
# Terminal 1 — Start the backend (port 8000)
.venv\Scripts\python.exe -m uvicorn server:app --reload

# Terminal 2 — Start the frontend (port 5173)
cd frontend
npm run dev
```

**Other commands:**

```bash
npm run build    # Build frontend for production
npm run start    # Start backend only (production)
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

## Discord Chat Export

Exports a Discord server channel's chat history as a styled HTML file. No desktop client or bot API key required — only a browser session is needed.

### How to Get Your Discord Token

1. Open **discord.com** in your browser and log in
2. Navigate to the channel you want to export
3. Press `F12` to open DevTools
4. Switch to the **Network** tab
5. Click anywhere in Discord to trigger a network request
6. Click any request in the list (e.g. one named `messages` or `channels`)
7. In the **Headers** section, find the `Authorization` field and copy its value

The token looks like: `MTExxx...` (a long string of characters).

### How to Get the Channel URL

Just copy the URL from the browser address bar while viewing the channel:

```
https://discord.com/channels/<guild_id>/<channel_id>
```

No need to manually extract the channel ID — paste the full URL and it is parsed automatically.

### Usage

1. Open the app and select **Discord 聊天记录导出**
2. Paste your Discord token into the Token field
3. Paste the channel URL into the URL field
4. Optionally set a message limit (leave blank to export all messages)
5. Click **开始导出** and wait for completion
6. Click **下载 HTML 文件** to save the export

> **Security note:** The token is sent only to this local server and is never stored persistently or forwarded to any third party. To invalidate a token, log out of all devices in Discord's account settings.
