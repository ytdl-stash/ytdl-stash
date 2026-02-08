# Global UI Components - Feb 7, 2026

## Overview

Extracted repeated UI patterns across templates into reusable components in `app/templates/components/`, reducing duplication and ensuring consistent styling and behavior.

## Implementation Approach

Created seven new components following the plan in `.cursor/plans/global_ui_components_refactor_*.plan.md`:

1. Back link — breadcrumb-style "← Back to X" navigation
2. Loading button — disabled button with spinner for in-progress actions
3. Video thumbnail — Stash screenshot / thumbnail_url / placeholder block
4. Collapse + th_tooltip macros — DaisyUI collapse and table header with tooltip
5. Status badge filter — Jinja2 filter mapping status to badge class
6. Video action buttons — Detail, Stop, Retry, Re-sync, Delete buttons

## Changes Made

### Files Created

- `app/templates/components/_back_link.html`
- `app/templates/components/_loading_button.html`
- `app/templates/components/_video_thumbnail.html`
- `app/templates/components/_video_actions.html`
- `app/templates/components/_macros.html` (collapse, th_tooltip macros)

### Files Modified

- `app/main.py` — added `status_badge_class` filter and `status_badge_class` function
- `app/templates/videos/detail.html` — back link, thumbnail, loading button, video actions
- `app/templates/performers/detail.html` — back link
- `app/templates/channels/add.html` — back link
- `app/templates/videos/_table_body.html` — thumbnail, loading button, video actions
- `app/templates/videos/_table_body_performer.html` — loading button, video actions
- `app/templates/videos/_active_downloads.html` — thumbnail, loading button, video actions
- `app/templates/videos/_status_badge.html` — use status_badge_class filter
- `app/templates/videos/_status_legend.html` — collapse macro, status_badge_class filter
- `app/templates/videos/list.html` — collapse macro for Active downloads
- `app/templates/dashboard.html` — thumbnail, th_tooltip for Synced at
- `app/templates/settings.html` — th_tooltip for table headers
- `docs/patterns/ui.md` — added "Reusable components" section documenting all new components

## Challenges Encountered

- **Jinja2 include syntax:** The `{% include "..." with context video=video, ... %}` syntax is invalid — Jinja2 does not support named parameters on `include`. Fixed by wrapping includes in `{% with var=value, ... %}` blocks so the included template receives variables from the surrounding context. Macros require `{% from %}...{% call %}...{% endcall %}`; the th_tooltip macro is only suitable for actual `<th>` elements, not stat titles in dashboard (those remain inline).

## Observations

- Video action component supports three layouts: `table` (includes Detail link), `detail` (no Detail, Re-sync label differs, Delete has no hx-target), `active` (Stop/Stopping only)
- Status badge mapping centralized in Python filter; both `_status_badge.html` and `_status_legend.html` use it
- Dashboard stat titles use divs with tooltip, not th; left as-is per plan

## Testing Notes

Manual verification: navigate Videos list, detail page, Performers detail, Channels add, Dashboard, Settings. Verify HTMX actions (Stop, Retry, Re-sync, Delete), thumbnails, back links, collapse behavior, and status legend badge colors.
