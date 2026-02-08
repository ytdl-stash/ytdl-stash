# Fix Post-Sync Scene Re-Sync 400 Error - February 7, 2026

## Overview

Fixed a non-fatal 400 Bad Request error during post-sync scene re-sync from Stash GraphQL API (observed on Video 4212). Also improved HTTP error diagnostics across all Stash API calls.

## Implementation Approach

Two changes:

1. **Remove unused `cover` field** from `_FIND_SCENE_BY_ID_QUERY`. The `cover` field was requested from Stash but never consumed by the `_resync_scene_from_stash()` function or any other caller. This field may have been removed/renamed in newer Stash versions, causing the 400 rejection.

2. **Include response body in HTTP error messages**. The `_query()` method previously discarded the Stash response body on non-2xx responses, making it impossible to determine the exact cause of 400 errors. Now includes up to 500 chars of the response body in the raised `RuntimeError`.

## Changes Made

### Files Modified

- `app/stash_client.py`
  - Removed `cover` from `_FIND_SCENE_BY_ID_QUERY` GraphQL query
  - Enhanced `_query()` HTTP error handling to include response body text (truncated to 500 chars) for diagnostics

## Observations

- The `cover` field was only referenced in the query string and a comment — no downstream code consumed it.
- Stash returns HTTP 400 (not a GraphQL-level error) when the query string references a field that doesn't exist in the schema, which is why the existing GraphQL error handling didn't catch it.
- The `_FIND_SCENES_QUERY` (path-based lookup) does not include `cover`, so it was unaffected.

## Next Steps (Future Considerations)

- If 400 errors recur, the improved diagnostics will now show the response body, making root cause identification trivial.
- Consider periodically auditing GraphQL queries against the running Stash schema version.
