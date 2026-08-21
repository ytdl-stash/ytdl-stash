# --- Stage 1: compile Tailwind + DaisyUI to a static stylesheet ---
# We do this at build time so the UI ships pre-compiled CSS instead of running
# Tailwind's in-browser JIT build from a CDN at runtime (FOUC + offline-fragile).
FROM node:20-slim AS assets
WORKDIR /assets
COPY package.json package-lock.json ./
RUN npm ci
# Tailwind scans the templates (via @source) for the utility classes in use.
COPY app/static/src/ app/static/src/
COPY app/templates/ app/templates/
RUN npm run build:css

# --- Stage 2: runtime ---
FROM python:3.12-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# Install latest yt-dlp nightly (overrides stable from requirements.txt)
RUN pip install --no-cache-dir -U "yt-dlp @ https://github.com/yt-dlp/yt-dlp-nightly-builds/releases/latest/download/yt-dlp.tar.gz"

ARG APP_VERSION=dev
RUN echo "$APP_VERSION" > /app/VERSION

COPY app/ app/
# Bundled yt-dlp extractor plugins (channel support for sites yt-dlp only has
# single-video extractors for). Discovered via sys.path — see
# app/ytdlp_patches.py:_register_bundled_plugins.
COPY yt_dlp_plugins/ yt_dlp_plugins/
# Overwrite the committed app.css with a freshly compiled one so it always
# matches the templates in this build.
COPY --from=assets /assets/app/static/app.css app/static/app.css

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
