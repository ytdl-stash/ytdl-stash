# Fix Stash enum normalization & add missing performer fields - February 9, 2026

## Overview

Fixed a bug where performer scrape+resync failed because the Stash GraphQL API
received mixed-case enum values (e.g. `"Female"`) but expects uppercase values
(e.g. `"FEMALE"`). Audited all enum fields and also added two missing performer
fields (`circumcised`, `penis_length`) to the scraper and gap-fill logic.

## Implementation Approach

1. Refactored enum normalization in `update_performer` to loop over a set of
   known enum fields (`gender`, `circumcised`) and uppercase them defensively.
2. Added the same normalization in `apply_scraped_performer`'s gap-fill loop.
3. Added `circumcised` and `penis_length` to the GraphQL queries that were
   missing them (`_FIND_PERFORMER_BY_ID_QUERY`, `_SCRAPE_PERFORMER_URL_QUERY`).
4. Added `circumcised` to the gap-fill string fields list and `penis_length`
   as a String→Float coercion (same pattern as `height`/`weight`).

## Changes Made

### Files Modified

- `app/stash_client.py`
  - `_FIND_PERFORMER_BY_ID_QUERY`: added `circumcised`, `penis_length` fields.
  - `_SCRAPE_PERFORMER_URL_QUERY`: added `circumcised`, `penis_length` fields.
  - `update_performer`: refactored single `gender` normalization into a loop
    over `("gender", "circumcised")`. Updated docstring.
  - `apply_scraped_performer`: added `circumcised` to `_gap_fill_fields`,
    broadened enum normalization to cover both `gender` and `circumcised`,
    added `penis_length` String→Float coercion block. Updated docstring.

## Stash Enum Fields Audit

| Enum Type        | Valid Values                                                                 | Normalized? |
|------------------|------------------------------------------------------------------------------|-------------|
| `GenderEnum`     | `MALE`, `FEMALE`, `TRANSGENDER_MALE`, `TRANSGENDER_FEMALE`, `INTERSEX`, `NON_BINARY` | Yes |
| `CircumisedEnum` | `CUT`, `UNCUT`                                                               | Yes         |

Scene and studio mutations have no enum-typed fields.

## Observations

- The Stash `ScrapedPerformer` type returns enum fields as plain `String`,
  not as their enum types. This means scrapers can return mixed-case values
  that must be uppercased before being sent to mutation inputs.
- Defense-in-depth: normalization is applied both at the scrape-apply layer
  and at the generic `update_performer` method so all callers benefit.
