# Videos Page Layout Tweaks - Feb 7, 2026

## Overview

Adjusted the Videos page layout: active downloads section is now collapsible (default open), the filter section was moved below active downloads (since it does not filter active downloads), and filter spacing was tightened.

## Changes Made

### Files Modified

- [app/templates/videos/list.html](app/templates/videos/list.html)
  - Wrapped active downloads in `<details open>` with DaisyUI collapse styling
  - Reordered: Active downloads (collapsible) first, then filter form, then status legend, then video list
  - Filter form: `mb-4` → `mb-2`, grid `gap-4` → `gap-x-4 gap-y-1`
