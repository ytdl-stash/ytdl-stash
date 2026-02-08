# Videos Page Status Legend - Feb 7, 2026

## Overview

Added a collapsible status legend to the Videos page to explain all video statuses (pending, downloading, cancelling, downloaded, importing, imported, synced, failed, skipped, cancelled).

## Implementation Approach

- Created `app/templates/videos/_status_legend.html` as a reusable partial using DaisyUI collapse/details
- Each status shows its badge (matching `_status_badge.html` styling) plus a short description
- Legend is collapsible to keep the page compact

## Changes Made

### Files Created

- `app/templates/videos/_status_legend.html`

### Files Modified

- `app/templates/videos/list.html` — include the legend between the filter form and active downloads
