# Glossary

Key terms and concepts used throughout the ytdl-stash project.

---

## Application Concepts

### Channel
A source of videos to monitor. Represents a user/model/channel page on a video site (e.g., a specific user's upload page on PornHub or XVideos). Stored in the `channels` table. Each channel has a URL, a display name, a site identifier, and a configurable check interval.

### Video
A single video discovered on a channel. Stored in the `videos` table. Tracks the video through its entire lifecycle from discovery to Stash sync. Identified uniquely by `site_video_id` (the video ID assigned by the source site).

### Pipeline
The orchestration logic in `app/pipeline.py` that takes a video from "pending" through download, oshash computation, Stash scan trigger, scene matching, and metadata application. See [data-flow.md](data-flow.md) for the complete flow.

### Status Lifecycle
The progression of a video's `status` field:
- `pending` -- Discovered, awaiting download.
- `downloading` -- Download in progress.
- `downloaded` -- Download complete, oshash computed.
- `importing` -- Stash scan triggered, waiting for scene to appear.
- `synced` -- Scene found in Stash, metadata applied.
- `failed` -- Error at any stage. Retryable.

### Performer Sync
The process of linking a ytdl-stash channel to a Stash performer. When a channel (subscription) is added, the app searches Stash for a performer whose URL matches the channel URL. If found, the existing performer is linked. If not found, a new performer is created in Stash with metadata (name, URL, avatar) from the tube site. The link is stored as `stash_performer_id` on the `Channel` model. See `app/performer_sync.py`.

### Performer Browser
A UI page (`/performers`) that displays all performers discovered across subscribed tube site channels. Shows each performer's name, site, avatar, subscription status (watched/not watched), and Stash link status. Allows toggling subscriptions and manually syncing performers to Stash.

### Watched / Not Watched
In the context of the Performer Browser, a performer is "watched" if their channel has `enabled=True` (actively being scanned and downloaded). "Not watched" means the channel exists but is disabled — the performer is known but not being actively monitored.

---

## External Systems

### Stash
A self-hosted web application for organizing and managing media. It provides a GraphQL API for creating/reading/updating performers, studios, tags, and scenes. ytdl-stash uses this API to apply metadata to downloaded videos. [Stash GitHub](https://github.com/stashapp/stash).

### Stash Performer
A record in Stash representing a performer. Performers have metadata including `name`, `urls` (list of profile URLs), `image` (avatar), and various biographical fields. ytdl-stash links channels to Stash performers by matching the channel URL against the performer's URL list.

### Stash Scene
A record in Stash representing a single video file. Scenes have metadata (title, performers, studio, date, tags, URLs) and are identified by file fingerprints (oshash, md5, phash). ytdl-stash creates scenes by triggering a metadata scan, then updates them with rich metadata.

### yt-dlp
A command-line and Python library tool for downloading videos from hundreds of websites. Fork of youtube-dl with active development. ytdl-stash uses it as a Python library for both channel scanning and video downloading. [yt-dlp GitHub](https://github.com/yt-dlp/yt-dlp).

---

## Technical Terms

### oshash (OpenSubtitles Hash)
A fast file hashing algorithm used by Stash to fingerprint video files. Reads only the first and last 64KB of a file plus the file size, making it O(1) regardless of file size. Produces a 16-character hex string. Used to match downloaded files to Stash scenes. See [ADR-004](adr/004-oshash-scene-matching.md).

### info_dict
The metadata dictionary returned by yt-dlp's `extract_info()` method. Contains all available metadata for a video: title, uploader, upload date, duration, thumbnails, tags, categories, cast, description, file format, and more. The raw info_dict is stored in the `metadata_json` column for reference.

### extract_flat
A yt-dlp option that lists videos on a channel/playlist without resolving full metadata for each one. Returns a shallow list of video stubs with `id`, `title`, `url`. Used for fast channel scanning.

### site_video_id
The unique identifier assigned to a video by the source site (e.g., `ph5f3a1b2c3d4e` for PornHub, `video12345` for XVideos). Stored in the `videos` table with a unique constraint. Used to avoid re-downloading already-known videos.

### GraphQL
A query language for APIs. Stash uses GraphQL as its API layer. Queries and mutations are sent as POST requests to `{stash_url}/graphql` with a JSON body containing the `query` string and `variables` object.

### HTMX
A JavaScript library that adds dynamic behavior to HTML via attributes (`hx-get`, `hx-post`, `hx-swap`, etc.). Allows server-rendered HTML to have SPA-like interactivity without writing JavaScript. Used throughout the ytdl-stash UI.

### Pico CSS
A minimal, classless CSS framework that provides clean default styling for semantic HTML elements. Used as the base styling for the ytdl-stash UI. No CSS classes needed for basic elements -- just use proper HTML tags.

### APScheduler
Advanced Python Scheduler. A Python library for scheduling periodic tasks. ytdl-stash uses `AsyncIOScheduler` to run channel checks and download processing on configurable intervals. Runs in-process with FastAPI.

### Lifespan
FastAPI's mechanism for running code at application startup and shutdown. Implemented as an async context manager. Code before `yield` runs at startup; code after `yield` runs at shutdown. Used to initialize the database and start/stop the scheduler.

### Dependency Injection (DI)
FastAPI's pattern for providing objects (database sessions, settings, etc.) to route handlers. Declared via `Depends()` in function signatures. FastAPI manages creation, caching, and cleanup of dependencies.

---

## File Paths (Inside Container)

| Path | Purpose |
|------|---------|
| `/app/` | Application root (WORKDIR) |
| `/app/app/` | Python package |
| `/app/data/` | SQLite database, persistent via volume |
| `/app/data/ytdl-stash.db` | SQLite database file |
| `/downloads/` | Downloaded video files, shared with Stash |
| `/app/cookies.txt` | Optional cookies file (bind mounted) |
