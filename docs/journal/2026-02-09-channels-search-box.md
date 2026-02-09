# Channels Search Box - February 9, 2026

## Overview

Added a search box to the channels list page so users can quickly find a channel/performer by name. The search filters the card grid in real-time using HTMX with a 300ms debounce.

## Implementation Approach

- **Server-side filtering**: Added a `search` query parameter to the `GET /channels` route. When provided, it applies a case-insensitive `ILIKE` filter on `Channel.name`.
- **HTMX input**: A `<input type="search">` in the nav bar fires an HTMX GET on `input changed delay:300ms`, replacing the `#channels-content` div with filtered results.
- **Preserved composability**: Search works alongside existing filter (All/Watched/Not Watched) and sort (Name/Videos/Last checked) controls. The `search` term is threaded through all filter/sort URLs so clicking a filter button while searching keeps the search active.
- **Clear button**: A ✕ button appears when a search is active, resetting to the full list.
- **Empty state**: When search yields no results, a "No channels matching…" message is shown.

## Changes Made

### Files Modified

- `app/routes/channels.py` — Added `search: str = ""` parameter to `list_channels`; applies `Channel.name.ilike(...)` filter; passes `search` in template context.
- `app/templates/channels/_list_content.html` — Added search input with HTMX attributes; appended `&search=` to all filter/sort URLs so the term persists; added empty-state message.

## Trade-offs

- Search is server-side (DB round-trip per keystroke, debounced at 300ms) rather than client-side JS filtering. This keeps the architecture consistent with the existing HTMX filter/sort pattern and scales to large channel lists without loading all data into the DOM.
- `ILIKE` with leading `%` prevents index usage, but channel counts are expected to stay small (hundreds, not millions), so this is fine.

## Testing Notes

- Type in the search box → cards filter after 300ms pause.
- Click Filter/Sort while searching → search term persists.
- Click ✕ → search clears and full list reappears.
- Native browser search-clear (× icon inside `type="search"`) also triggers the `search` event, resetting results.
