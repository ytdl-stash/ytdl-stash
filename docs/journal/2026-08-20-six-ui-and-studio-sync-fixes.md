# 2026-08-20 — Six fixes: htmx retry wipeout, thumbnails, sorting, badge overlap, studio sync regression

## 1. Retry/Stop/Redownload/Re-sync wiped the /videos index (htmx hx-target inheritance)
Action routes return a **self-polling** status badge (`poll_status_badge=True`). The badge had
`hx-get`/`hx-trigger`/`hx-swap="outerHTML"` but **no `hx-target`** — and `hx-target` is an
*inherited* attribute in htmx. On `/videos` the badge sits inside the list auto-refresh wrapper
(`hx-target="#video-list-content"`), so ~2s after clicking Retry the badge's own poll resolved its
target to the whole list container and replaced it with a single `<span>`. The id then no longer
existed, so every later poll/filter/pagination request died with `htmx:targetError`.

**Fix:** `hx-target="this"` in both polling branches of `videos/_status_badge.html`.
Also: the four action routes now accept `?detail=1` and return the badge through a shared
`_status_badge_response()` helper (progress + channel + detail layout), so acting on the video
detail page no longer degrades the badge to the compact table variant. The detail-layout buttons in
`components/_video_actions.html` pass `?detail=1`.

Lesson for future htmx partials: any self-polling fragment that can be swapped into arbitrary pages
must carry `hx-target="this"` (or the page wrapper needs `hx-disinherit`). See docs/patterns/htmx.md.

## 2. Broken thumbnail glyph in Pipeline Activity with multiple items
`components/_video_thumbnail.html` picked img-vs-placeholder purely from DB state, with no
`onerror` anywhere. An `importing` video already has `stash_scene_id`, so its thumb pointed at
`{stash_url}/scene/{id}/screenshot` — which 404s until Stash generates the screenshot (and
`stash_url` may not be browser-reachable at all). Single fresh items have no scene id → placeholder
→ looked fine, which is why it only showed with several items in flight.

**Fix:** the placeholder now always renders; the `<img>` overlays it and `onerror="this.remove()"`
reveals the placeholder on failure. The panel's 3s poll re-attempts naturally once the screenshot exists.

## 3. Channels page not fully alphabetical
SQLite BINARY collation: `ORDER BY channels.name` is case-sensitive, so `Zebra` < `apple`.
**Fix:** `order_by(func.lower(Channel.name), Channel.name)` in `channels.py` (list + bulk-edit
loader) and `videos.py` (channel filter dropdown + `channel_asc` sort).
Known leftovers (deliberate): a newly added channel is OOB-appended to the grid bottom and a rename
keeps its slot until the next reload.

## 4. Status sub-status "strikethrough" overlap (IMPORTING / SYNCING METADATA)
The phase sub-status is a second DaisyUI badge under the status pill. DaisyUI badges have a hard
`height: var(--size)` (badge-sm = 20px) and our theme uppercases + letter-spaces them; in the
squeezed Status column the phase wrapped to two lines inside the 20px pill, overflowing through the
pill border — reading as struck-through text colliding with the pill above.
**Fix (style.css):** `.badge { height: auto; min-height: var(--size, 1.5rem); }` — wrapped labels
grow the pill. App-wide hardening, no template changes.

## 5. Jobs page RUNNING badge overlapping the Schedule input
`table-fixed` + Status column `w-20` (80px); uppercase RUNNING measures ~99px and painted over the
interval input. **Fix:** `w-28` on the Status th/td.

## 6. Studio sync silently broken since v0.35.1 (studios created without image/URL/details)
Regression: commit `1c1c320` (v0.35.1) changed `_enrich_channel_and_get_description` to return a
`(description, source_image_url)` tuple — the second element added precisely because performer sync
overwrites `channel.performer_image_url` with an ApiKey-protected Stash URL that
`download_image_data_uri` cannot fetch — **but never updated the caller**. Effects:
- `channel_description` became a tuple → every `studioCreate`/`studioUpdate` including `details`
  sent a JSON array for a GraphQL String → mutation error → swallowed by the broad
  `except` in `sync_channel_studio` → **no studio created via studio sync, no gap-fill on existing
  studios**. Studios still appeared because the scene-scrape path creates bare name-only studios.
- Even ignoring that, the create call used `channel.performer_image_url` (the un-downloadable Stash
  URL) instead of the yt-dlp thumbnail.

**Fix (studio_sync.py):** unpack the tuple; pass `image_url=source_image_url or
channel.performer_image_url` on create; `_push_to_stash` gains a `source_image_url` param and
prefers it for the image gap-fill. Verified with a mocked-client test: creation receives the channel
URL, the source thumbnail, and string details; gap-fill pushes urls/image/details.

## Housekeeping
- Rebuilt the committed `app/static/app.css` (was stale since v0.38.2 — `animate-pulse` was missing
  locally; Docker builds always recompile, so prod only lacked the pulse).
- Dead-class fixes: `card-compact` → `card-sm` (DaisyUI v5), `flex-shrink-0` → `shrink-0`
  (Tailwind v4) in `_active_downloads.html`, `videos/detail.html`, `channels/_add_step2.html`.

## Follow-up (same day): studio still bare after v0.45.1 — HTML-as-image poisoned the mutation
Real-world logs (channel 61, "Theluxxs") showed the tuple fix working (`fields: ['name', 'urls',
'image']`) but `studioCreate` failing with
`processing image: unsupported image content type "text/html; charset=utf-8"`:
the yt-dlp thumbnail URL returned an HTML bot-check page, `_url_to_data_uri` labeled unknown
content types as `image/jpeg` (fallback), Stash sniffed the real bytes and rejected — and because
name/urls/image ride in one mutation, the **URL was lost and the channel never got linked**. The
bare studio the user saw came from the concurrent scene-scrape path; the Sync Studio retry then
failed identically on the gap-fill `studioUpdate`.

Fixes (stash_client.py + studio_sync.py):
- `_url_to_data_uri` now sniffs magic bytes (jpeg/png/gif/webp/bmp/avif) and returns **None** for
  anything that isn't a real image (header is trusted only for SVG). Protects performer sync too.
- `download_image_data_uri` sends the Stash ApiKey when the URL is on our own Stash host, so the
  channel's performer image is usable as a studio image.
- Studio image is now a candidate list: yt-dlp source thumbnail first, then
  `channel.performer_image_url`.
- Create/gap-fill are image-resilient: if Stash still rejects a mutation containing `image`, it is
  retried once without it — name/urls/details always land.
- `find_or_create_studio_by_url` never lets a gap-fill failure prevent returning the found id —
  linking the channel takes priority over enriching the studio.
- `_push_to_stash` now delegates to `_gap_fill_studio_url_image_details` (same semantics, one
  implementation).

Existing bare studios self-heal: clicking **Sync Studio** finds the studio by name, gap-fills
URL + image, and links the channel.

## Feature (same day): job status scrobbler
Floating background-activity widget, bottom-right on every page (`base.html`).
- Shell `.cr-scrob` in base.html is never swapped, so open/closed state survives polling; the inner
  content polls `GET /jobs/scrobbler` every 3s and swaps only itself (`hx-target="this"`).
- Pill precedence: running (pulsing blue, e.g. "Channel Check · 2↓ 47%") > error (red) >
  paused (amber) > idle ("All quiet"). Panel lists only active things: running jobs with elapsed
  time, job errors (truncated, full text in tooltip), up to 4 in-flight pipeline videos with
  progress bars / phase text, "+N more", pause chips, links to /jobs and /videos.
- New: `JobInfo.started_at` (set in `_run_tracked` and eager manual trigger) for elapsed display.
- Route `GET /jobs/scrobbler` in `app/routes/jobs.py`; partial
  `components/_scrobbler.html`; `cr-scrob-*` styles in style.css. Hidden ≤768px.

## Bug found while testing it: channel checker died after first failed scan
`_do_check_all_channels` captured `channel.id` per iteration, but a failed scan's `db.rollback()`
expires every loaded instance — the next iteration's attribute access lazy-loads synchronously →
`MissingGreenlet` ("greenlet_spawn has not been called") → the whole job aborted, skipping all
remaining channels. Fix: capture `due_ids` up front and `await db.get(Channel, id)` inside the
try (refreshes expired instances properly). One bad channel no longer kills the rest of the run.
