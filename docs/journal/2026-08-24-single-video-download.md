# 2026-08-24 — Download a single video by URL (no channel)

## Why
The app was channel-only: `Video.channel_id` is NOT NULL and videos were created
solely by channel scans, so a one-off video could not be added at all. Pasting a
video URL into Add Channel silently produced a useless 0-video channel. Sites with
no channel extractor (Redtube, YouPorn) were entirely unreachable.

## Approach: a hidden sentinel channel, not a nullable FK
Making `channel_id` nullable would need a SQLite table rebuild (no `ALTER COLUMN`,
no Alembic in this repo — every past migration has been additive `ADD COLUMN`), and
`create_all()` would silently leave existing installs with the old NOT NULL, so
every insert would fail at runtime.

Instead singles attach to a lazily-created hidden channel
(`app/singles.py`), reusing the YTDLM orphan-channel trick:

- URL `ytdl-stash://singles` — **non-HTTP on purpose**. The channel checker only
  considers channels whose URL starts with `http(s)://`, so it is never scanned.
  `enabled=False` is a second line of defence.
- `min_duration_seconds` / `max_video_age_days` stay NULL, so the pipeline's filter
  blocks no-op — an explicitly requested video is never skipped for being too short
  or too old.
- No `stash_studio_id`, so `_apply_metadata_and_sync` omits the studio and the Stash
  URL scraper fills it in instead. This is the desired behavior for singles.

Zero schema change, zero pipeline changes for the download path itself. Retry,
redownload, stop, resync, the FIFO download processor and every video template were
already channel-agnostic or null-guarded.

## Flow
`+ Add Video` (navbar) → `GET /videos/add-modal` → paste URL →
`POST /videos/preview` (yt-dlp metadata) → confirm → `POST /videos` creates the row
`status="pending"` → the download processor picks it up on its next 30s tick.

The wizard partials render inside **either** modal, so every `hx-target` is
`closest .cr-modal-body` (a class now on both modal body divs) rather than a fixed
id. That is what makes the Add Channel handoff work: when a channel preview fails,
step 1 offers "Add as single video instead", which posts `/videos/preview` into the
already-open Add Channel modal. The reverse handoff exists too — a creator page
pasted into Add Video offers "Add as channel instead".

Success keeps the modal open with a link to `/videos/{id}`: a full `/videos` page
load defaults to `status="synced"`, so a fresh pending row would otherwise be
invisible.

## Two things the live test caught (both would have shipped broken)

**1. `site_video_id` is not the same value on both sides — dedup would have failed.**
The plan assumed yt-dlp's `id` is stable between flat (channel scan) and non-flat
(single) extraction. It is not, because the *scan* stores `_derive_video_id`'s
**URL fallback** whenever a flat entry carries no `id`:

| site | channel scan stores | single extraction returns |
|---|---|---|
| PornHub | `http://www.pornhub.com/view_video.php?viewkey=68e1672f7e6c4` | `68e1672f7e6c4` |
| xHamster | the full video URL | `xhgdoTn` |
| xvideos | `uodcufm7c56` | `uodcufm7c56` (matches) |

So adding a single first and the creator's channel later would have downloaded the
same video twice on two of the three confirmed sites. The scan's existing secondary
URL/title dedup does not help: it is scoped to *that channel's* videos, and a single
lives in the singles channel.

Fix: `process_channel_scan` now also skips entries whose **URL** is already tracked
**in the singles channel** (`skipped N already added as single videos` in the scan
log). The URL is stable across extraction modes — verified identical on all three
sites — whereas the ID form is extractor-dependent. Verified end to end: single added
first, then a full `pinkloving` scan created 176 videos and correctly skipped the
177th.

Scoping the skip to *singles* rather than "any other channel" matters: a video parked
in the YTDLM orphan channel must still be adopted by its real channel's scan, and a
broader skip would strand it there permanently (there is a regression test for this).

**2. The preview must not require downloadable formats.**
The first version raised `No video formats found!` on **4 of 4** xHamster videos, so
no xHamster single could ever be added. A metadata-only preview has no business
demanding formats, so extraction now sets `ignore_no_formats_error: True`. All four
then previewed fine with title, duration and thumbnail. (This is the same error the
2026-08-21 journal hit during the channel-metadata fallback.)

**3. `noplaylist` does not stop a URL that is *only* a playlist.**
A creator page pasted into Add Video hung for minutes: yt-dlp extracted all 176
videos before the "this is a playlist" check could reject it. Adding
`extract_flat: "in_playlist"` + `playlistend: 1` bounds that to one flat entry —
rejection is now **1.1s**, while real single videos still extract fully in ~1s.

**4. The dedup gap ran in both directions.**
The pipeline fix above covers single-then-channel. The mirror case — a video already
in the library from a scan, then pasted into Add Video — failed too: the scan stores
`http://…` while a browser yields `https://…`, so the exact-match check missed.
`_find_tracked_video` now compares a small set of scheme/`www.` variants against
**both** `Video.url` and `Video.site_video_id` (the scan writes the URL into both on
the sites where they diverge).

**5. The reverse handoff swapped into a hidden dialog.**
A closed `<dialog>` is still in the DOM, so the channel partials' hard-coded
`hx-target="#add-channel-modal-body"` resolved happily while the channel wizard was
running *inside the video modal* — the user clicked and nothing appeared to happen.
All three channel partials now use `closest .cr-modal-body` too, and the
`closeAddChannelModal` listener closes whichever modal is open.

## Sentinel hygiene
Excluded from the channels grid, bulk edit and the dashboard channel count; **kept**
in the videos-page channel dropdown, where "Single Videos" is a useful filter. Its
name in the videos table links to `/videos?channel_id=…`, and `GET /channels/{id}`
redirects there too, so its scan/sync/relink controls are unreachable rather than
merely unlinked. `DELETE /channels/{id}` refuses it with a 400 — `Channel.videos`
cascades `all, delete-orphan`, so deleting it would wipe every single video at once.

`get_or_create_singles_channel` holds an `asyncio.Lock` around its select-then-insert:
`Channel.url` has no unique constraint, so two concurrent adds would otherwise each
create their own sentinel and split the singles across them.

`videos.url` gained an index (`ix_videos_url`, added to `_migrate_videos_columns`
since `create_all(checkfirst=True)` will not add an index to an existing table) —
both the add-video lookups and the scan's singles dedup query that column, and
unlike `site_video_id` it was not previously indexed.

## Files
- `app/singles.py` (new) — sentinel constants, `get_or_create_singles_channel`
- `app/downloader.py` — `extract_single_video_metadata` + async wrapper
- `app/routes/videos.py` — `GET /add-modal`, `POST /preview`, `POST ""` (registered
  above `GET /{video_id}`, or the int path param 422s them)
- `app/pipeline.py` — cross-channel URL dedup in `process_channel_scan`
- `app/templates/videos/_add_{modal,step1,step2,success}.html` (new)
- `app/routes/channels.py`, `app/routes/dashboard.py`, `app/main.py`,
  `base.html`, `channels/_add_step{1,2}.html`, `videos/_table_body.html`
