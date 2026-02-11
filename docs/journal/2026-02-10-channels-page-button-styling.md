# Channels Page Button Styling - February 10, 2026

## Overview

Restyled the channels page for a cleaner header and more informative card buttons. Consolidated four header action buttons into a single "Bulk Actions" dropdown, changed the Watch/Unwatch button to display current status with color coding, improved Check Now visibility, and dimmed unwatched channel cards.

## Changes Made

### Files Modified

- **`app/templates/channels/list.html`** -- Replaced four standalone header buttons (Retry All Skipped, Re-sync All, Bulk Edit, Check All Now) with a single DaisyUI "Bulk Actions" dropdown. The "+ Add Channel" button remains standalone. All HTMX attributes and IDs preserved.

- **`app/templates/channels/_card.html`** -- Three changes:
  1. Watch/Unwatch button now shows current state: "Watched" (green `btn-success`) or "Unwatched" (red `btn-error btn-outline`) instead of the action label.
  2. Check Now button changed from `btn-ghost` to `btn-outline` for better visibility.
  3. Card wrapper gains `opacity-50` when the channel is not enabled, visually dimming unwatched channels.

## Observations

- The HTMX toggle swaps the entire card (`outerHTML`), so toggling watch status immediately updates both the button color/label and the card opacity in one response.
- No CSS changes needed; all styling uses existing DaisyUI and Tailwind utility classes.
