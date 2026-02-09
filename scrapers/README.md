# Stash performer scrapers for ytdl-stash

This directory contains Stash-compatible YAML scrapers that add **performer-by-URL** support for sites where the [Community Scrapers](https://github.com/stashapp/CommunityScrapers) only provide scene scrapers.

When you add a channel in ytdl-stash (e.g. an xHamster creator or xVideos channel URL), the app asks Stash to scrape that URL for performer metadata. If a scraper matches the URL, Stash returns bio data (name, image, gender, country, etc.) and ytdl-stash applies it to the Stash performer. These files make that work for xHamster and xVideos.

## Files

| File | Site | Purpose |
|------|------|---------|
| `Xhamster-Performer.yml` | xHamster | Scrape creator/pornstar profile pages (`/creators/`, `/pornstars/`) |
| `Xvideos-Performer.yml` | xVideos | Scrape channel pages (`/pornstar-channels/`, `/model-channels/`, `/channels/`, `/amateur-channels/`) |

## Installation

Stash must load these scrapers so that `scrapePerformerURL` can match channel URLs. Choose one method.

### Option 1: Copy into Stash’s scrapers directory

1. Find your Stash scrapers path (e.g. `~/.stash/scrapers` or the path set in Stash **Settings → Configuration**).
2. Copy the `.yml` files from this directory into that folder.
3. In Stash, go to **Settings → Metadata Providers** and click **Reload Scrapers**.

### Option 2: Mount this directory in Docker

If Stash runs in Docker, mount this repo’s `scrapers` folder into the container’s scrapers directory. Example (adjust service name and path to match your setup):

```yaml
services:
  stash:
    volumes:
      - ./scrapers:/root/.stash/scrapers/ytdl-stash:ro
```

Then reload scrapers in Stash as above.

## Coexistence with Community Scrapers

These YAMLs are **additive**. They only define `performerByURL`. Install them **alongside** the community scrapers (e.g. `Xhamster.yml`, `Xvideos.yml`) which provide scene scraping. Stash will use the community scrapers for scenes and these for performer URLs when the URL matches.

## Contributing upstream

If you improve these scrapers, consider opening a PR to [stashapp/CommunityScrapers](https://github.com/stashapp/CommunityScrapers) to add `performerByURL` to the existing `Xhamster.yml` and `Xvideos.yml` so everyone benefits.
