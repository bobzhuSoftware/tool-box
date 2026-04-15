# ── Stage 1: Build React frontend ──────────────────────────────────────────
FROM node:20-alpine AS frontend-builder

WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ── Stage 2: Python backend ─────────────────────────────────────────────────
FROM python:3.11-slim

# Install ffmpeg (required by yt-dlp + whisper)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install CPU-only PyTorch first to significantly reduce image size (~800MB vs ~2.5GB)
# openai-whisper depends on torch; we pin the CPU build to avoid pulling CUDA
RUN pip install --no-cache-dir \
    torch --index-url https://download.pytorch.org/whl/cpu

# Install remaining Python dependencies
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend source
COPY server.py transcribe.py ./

# Copy compiled frontend from Stage 1
COPY --from=frontend-builder /app/frontend/dist ./frontend/dist

# Pre-create the data directory (Fly.io Volume will be mounted here)
RUN mkdir -p /data

EXPOSE 8000

# Run with host 0.0.0.0 so Fly.io's proxy can reach the container
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]
