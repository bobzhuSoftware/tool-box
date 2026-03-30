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
uvicorn server:app --reload

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

## Whisper Model Sizes

| Model  | Parameters | Speed   | Accuracy |
|--------|-----------|---------|----------|
| tiny   | 39M       | Fastest | Lower    |
| base   | 74M       | Fast    | Good     |
| small  | 244M      | Medium  | Better   |
| medium | 769M      | Slow    | Great    |
| large  | 1550M     | Slowest | Best     |

Start with `base` for quick results. Use `medium` or `large` for production-quality transcripts.
