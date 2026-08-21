# 2026-08-21 — Channel support for xvideos and xHamster pornstar pages

## Symptom
Adding non-PornHub creator pages failed. Production logs showed:

```
Channel metadata extraction failed for https://www.xvideos.com/primalfetish:
  ERROR: Unsupported URL: https://www.xvideos.com/primalfetish
Channel metadata extraction failed for https://xhamster.com/pornstars/polly-yangs:
  ERROR: Unsupported URL: https://xhamster.com/pornstars/polly-yangs
```

## Root cause
Nothing in the app is site-specific — `scan_channel()` just calls yt-dlp, which
picks an extractor by URL. The gap is upstream:

- **xvideos**: yt-dlp has `XVideosIE` (single video) and `XVideosQuickiesIE`,
  but **no channel/profile playlist extractor at all** — confirmed against
  yt-dlp master, so upgrading yt-dlp would not have helped.
- **xhamster**: `XHamsterUserIE` matches only `/users/` and `/creators/`.
  `/pornstars/<name>` has no extractor. (This is why
  `xhamster.com/creators/fuckslave` scanned fine — 46 entries every 6h — while
  the pornstar URL failed.)

## Fix
Ship yt-dlp **plugin extractors** with the app rather than scraping in app code,
so scanning/downloading/metadata all stay on the normal yt-dlp path and the
app's existing cookie/User-Agent/impersonate settings apply automatically:

- `yt_dlp_plugins/extractor/ytdlstash.py`
  - `xvideos:channel` — accepts bare slug and every creator path form. Uses the
    site's JSON listing `/{kind}/{slug}/videos/new/{page}` (the server routes on
    the slug alone, so any prefix works; we retry across forms). Yields
    `video.<eid>` URLs back to `XVideosIE`, with title/duration/thumbnail, plus
    the profile avatar as a playlist thumbnail.
  - `xhamster:pornstar` — paginates `/pornstars/<slug>/<n>`, parsing the same
    `video-thumb__image-container` anchors upstream's user extractor uses
    (titles come from `aria-label`), avatar from the page's `window.initials`
    JSON at `infoComponent.pornstarTop.thumbUrl`.
- `app/ytdlp_patches.py:_register_bundled_plugins()` — puts the repo root on
  `sys.path` so yt-dlp's namespace-package discovery finds the plugins
  regardless of cwd/launcher, reusing the existing pre-YoutubeDL chokepoint.
- `Dockerfile` — `COPY yt_dlp_plugins/ yt_dlp_plugins/` (it previously copied
  only `app/`, so the plugins would have been missing from the image).

## Gotchas hit while building this
- **Plugin extractors are *prepended* to yt-dlp's lookup**
  (`load_plugins` → `merge_dicts(regular_classes, ...)`). The first draft of the
  bare-slug xvideos pattern hijacked `https://www.xvideos.com/lili_love#quickies/...`
  from `XVideosQuickiesIE`. Fixed with negative lookaheads (`#quickies`,
  `video[.\d]`, reserved site sections) and a routing test asserting video URLs
  still resolve to the upstream extractors.
- **Missing avatar costs a slow retry**: `extract_channel_metadata()` falls back
  to non-flat extraction with `playlistend=1` when name *or* thumbnail is
  missing, which fully extracts a video — and raised
  `No video formats found!` on one xHamster video. Supplying an avatar
  thumbnail removes the fallback entirely.
- Past its last page xHamster answers **404 but still renders ~30 unrelated
  "related" thumbs**, so pagination stops on the failed fetch, with a
  no-new-ids check as a second backstop.
- xHamster lazy-loads most grid images (only ~12/46 have `<img src>`), so entry
  thumbnails are partial by design; Stash screenshots replace them after import.

## Verification
- Routing: `xvideos:channel` / `xhamster:pornstar` claim creator URLs while
  `XVideosIE`, `XVideosQuickiesIE`, `XHamsterIE`, `XHamsterUserIE` and the
  PornHub extractors keep theirs (compared against a `YTDLP_NO_PLUGINS=1`
  baseline — no upstream routing changed).
- Live through `app.downloader`: xvideos `primalfetish` → **247 entries**
  (matches the site's `nb_videos`), name "Primal Fetish" + avatar; xhamster
  `polly-yangs` → **379 unique entries**, name "Polly Yangs" + avatar.
- Regression: `xhamster.com/creators/fuckslave` → 46 entries (identical to
  production), `pornhub.com/model/pinkloving/videos` → 176 entries.
- Packaging: copied exactly what the Dockerfile copies into a clean tree and ran
  a scan from a foreign cwd — plugins loaded, 247 entries.
- Add Channel wizard (`POST /channels/preview`) returns the right name and
  avatar for both URLs; the avatar also feeds the Stash studio image.

## Still unsupported
Redtube/YouPorn creator pages have no yt-dlp channel extractor *and* no simple
listing endpoint; not attempted. Single videos from those sites still work.
