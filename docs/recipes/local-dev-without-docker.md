# Recipe: Local Development Without Docker

How to run ytdl-stash directly on your machine for faster development iteration.

---

## Prerequisites

- Python 3.12+
- ffmpeg installed and in PATH
- A running Stash instance (for testing Stash integration)

---

## Setup

### 1. Create a virtual environment

```bash
python -m venv .venv

# Windows (PowerShell)
.\.venv\Scripts\Activate.ps1

# Linux/macOS
source .venv/bin/activate
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Set environment variables

```bash
# Windows (PowerShell)
$env:YTDL_STASH_URL = "http://localhost:9999"
$env:YTDL_STASH_API_KEY = ""
$env:YTDL_DOWNLOAD_DIR = "./downloads"
$env:YTDL_DATA_DIR = "./data"

# Linux/macOS
export YTDL_STASH_URL="http://localhost:9999"
export YTDL_STASH_API_KEY=""
export YTDL_DOWNLOAD_DIR="./downloads"
export YTDL_DATA_DIR="./data"
```

Or create a `.env` file (pydantic-settings reads it automatically if `python-dotenv` is installed):
```
YTDL_STASH_URL=http://localhost:9999
YTDL_STASH_API_KEY=
YTDL_DOWNLOAD_DIR=./downloads
YTDL_DATA_DIR=./data
```

### 4. Create data directories

```bash
mkdir data downloads
```

### 5. Run the app

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

- `--reload` enables auto-restart when Python files change.
- App is at `http://localhost:8000`.
- OpenAPI docs at `http://localhost:8000/docs`.

---

## Development Workflow

1. Edit Python files in `app/`.
2. Uvicorn auto-reloads on save (with `--reload`).
3. Edit templates in `app/templates/` -- reload the browser (Jinja2 reads templates fresh on each request in debug mode).
4. SQLite DB is in `./data/ytdl-stash.db`. Delete it to reset.

---

## Differences from Docker

| Aspect | Docker | Local |
|--------|--------|-------|
| Data directory | `/app/data` (volume mount) | `./data` |
| Download directory | `/downloads` (shared volume) | `./downloads` |
| Stash URL | `http://host.docker.internal:9999` | `http://localhost:9999` |
| ffmpeg | Installed in image | Must be in PATH |
| Port | 8282 (external) -> 8000 (internal) | 8000 directly |

---

## Resetting the Database

```bash
# Delete and restart -- init_db() recreates tables
rm data/ytdl-stash.db
# Restart uvicorn
```

---

## Running Specific Modules

Test the downloader in isolation:
```python
python -c "
from app.downloader import scan_channel
results = scan_channel('https://example.com/channel')
for v in results:
    print(v['id'], v['title'])
"
```

Test the Stash client:
```python
python -c "
import asyncio
from app.stash_client import StashClient
client = StashClient('http://localhost:9999')
print(asyncio.run(client.health_check()))
"
```
