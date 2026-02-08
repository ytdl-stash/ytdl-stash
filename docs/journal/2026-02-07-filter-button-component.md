# Filter Button Component - Feb 7, 2026

## Overview

Created a reusable filter/toggle button partial and refactored the Performers page to use consistent `join` + `btn-active` styling, matching the Videos pagination pattern. Ensures filter buttons have clear active/inactive states and a cohesive grouped appearance.

## Implementation Approach

- Added `app/templates/components/_filter_button.html` as a reusable partial for link-based filter/toggle buttons
- Refactored Performers list filter and sort controls to use `join` groups and the new partial
- Switched from `btn-primary`/`btn-ghost` to `btn-active` for the selected state (consistent with DaisyUI join patterns)

## Changes Made

### Files Created

- `app/templates/components/_filter_button.html` — partial accepting `url`, `label`, `is_active`, `hx_target`, optional `tooltip`, `tooltip_classes`

### Files Modified

- `app/templates/performers/list.html` — wrap Filter and Sort buttons in `join` divs, use `_filter_button` partial
- `docs/patterns/ui.md` — add "Filter/Toggle button groups" section documenting the pattern and partial

## Challenges Encountered

None. Used manual URL construction (`/performers?filter=...&sort=...`) instead of `url_for` to avoid route-name dependencies.

## Observations

- Videos pagination already used `join` + `btn-active`; Performers now matches that pattern
- The `components/` folder is a new cross-cutting location for shared partials
