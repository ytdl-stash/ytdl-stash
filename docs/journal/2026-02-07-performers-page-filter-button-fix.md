# Performers Page Filter Button Fix - Feb 7, 2026

## Overview

Fixed the Performers page not loading. The page failed to render because the filter/sort buttons used invalid Jinja2 include syntax that did not pass variables to the `_filter_button.html` partial.

## Root Cause

Jinja2's `include` tag does not support passing variables as named parameters (e.g. `{% include "..." url=..., label=... %}`). The performers list template used that unsupported syntax, so `url`, `label`, `is_active`, `hx_target`, etc. were undefined when `_filter_button.html` rendered, causing a template error.

## Implementation Approach

Wrapped each filter button include in a `{% with %}` block to set the variables before including, matching the pattern used for `_back_link.html` (see docs/patterns/ui.md).

## Changes Made

### Files Modified

- `app/templates/performers/list.html` — replaced six invalid include blocks with `{% with %}...{% include %}...{% endwith %}`
- `docs/patterns/ui.md` — corrected the filter button example to show the valid `{% with %}` pattern

## Observations

- The project docs already documented the correct pattern for `_back_link.html` ("Passing variables to includes: Jinja2's include does not support variable=value syntax. Use {% with %} blocks"). The filter button example in ui.md incorrectly showed the invalid syntax.
- The filter button component was added earlier the same day (2026-02-07-filter-button-component.md); the bug was introduced in that refactor.
