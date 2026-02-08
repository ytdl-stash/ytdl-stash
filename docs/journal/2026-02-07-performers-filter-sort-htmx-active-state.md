# Performers Filter/Sort HTMX Active State - Feb 7, 2026

## Overview

Fixed the Performers page so that when you change filter or sort via the link buttons (HTMX), the correct button stays visually active. Previously only the grid was swapped, so the nav’s active state was wrong after a click.

## Root Cause

Filter/sort buttons targeted `#performer-grid`. The HTMX response was only `performers/_card_list.html` (the grid content). The nav (Filter: All / Watched / Not Watched, Sort: Name / Videos / Last checked) was outside the swap target, so it was never re-rendered and `btn-active` did not update.

## Implementation Approach

- Introduced a wrapper `#performers-content` that contains both the nav and the grid.
- Added a partial `performers/_list_content.html` with the nav + grid, used by both the full page and the HTMX response.
- For HTMX requests, the route now returns `_list_content.html` (with `filter`, `sort`, `channels`, `settings`) instead of `_card_list.html`, and the client targets `#performers-content`. One swap updates nav and grid, so the active filter/sort button is correct.

## Changes Made

### Files Created

- `app/templates/performers/_list_content.html` — nav + grid partial; filter/sort buttons use `hx_target='#performers-content'`.

### Files Modified

- `app/templates/performers/list.html` — wrap content in `<div id="performers-content">` and include `_list_content.html`.
- `app/routes/performers.py` — for `HX-Request`, return `performers/_list_content.html` with `filter`, `sort`, `channels`, `settings`.

## Observations

- `_card_list.html` is still used by card-only responses (e.g. after toggle/sync on a single card). The list endpoint is the only one that needed the nav+grid partial.
- Same pattern can be used elsewhere when link-based filters need to stay in sync with HTMX partial updates: target a wrapper that includes the filter UI and the content.
