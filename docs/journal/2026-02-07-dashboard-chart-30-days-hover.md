# Dashboard chart: 30 days + hover and styling - Feb 7, 2026

## Overview

Reduced the "Downloads by day" chart from 90 to 30 days, added clear hover on each datapoint (tooltip + visible point), and applied Chart.js styling improvements.

## Implementation Approach

- Backend: same query, shorter date range (29 days ago through today = 30 days) and 30 labels/values.
- Frontend: Chart.js options for tooltip (date as title, "N downloads" as label), point hover (radius 6, border), rounded line caps/joins, subtle grid, no axis borders.

## Changes Made

### Files Modified

- **app/routes/dashboard.py** — Chart window 90 → 30 days: `start_date` uses `timedelta(days=29)`, loop `range(30)`.
- **app/templates/dashboard.html** — Card/aria text "last 30 days"; chart dataset: `borderCapStyle`/`borderJoinStyle` `'round'`, `borderWidth: 2`, `pointHoverRadius: 6`, hover point colors; `interaction.mode: 'nearest'`; tooltip callbacks for title (date) and label ("N downloads"); grid and tick styling.
