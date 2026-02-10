# Fix Stash enum normalization & validation - February 9, 2026

## Overview

Fixed bugs where performer scrape+resync failed because scraped enum values
were rejected by the Stash GraphQL API:

1. **Case mismatch** — scrapers return mixed-case strings (e.g. `"Female"`)
   but Stash expects uppercase enum values (e.g. `"FEMALE"`).
2. **Invalid values** — some scrapers return values outside the valid enum set
   (e.g. `"OTHER"` for gender) which Stash rejects outright.

Also added two missing performer fields (`circumcised`, `penis_length`) to the
scraper queries and gap-fill logic.

## Implementation Approach

1. Added module-level validation sets (`_VALID_GENDERS`, `_VALID_CIRCUMCISED`)
   and a `_ENUM_VALIDATORS` dict mapping field names to their valid value sets.
2. In `update_performer`: uppercase the value, check against the valid set,
   and `del` the field (with a warning log) if invalid.
3. In `apply_scraped_performer`: same validation — `continue` past invalid
   values so they're never added to the updates dict.
4. Added `circumcised` and `penis_length` to `_FIND_PERFORMER_BY_ID_QUERY`,
   `_SCRAPE_PERFORMER_URL_QUERY`, and the gap-fill logic.

## Changes Made

### Files Modified

- `app/stash_client.py`
  - Added `_VALID_GENDERS`, `_VALID_CIRCUMCISED`, `_ENUM_VALIDATORS` constants.
  - `_FIND_PERFORMER_BY_ID_QUERY`: added `circumcised`, `penis_length`.
  - `_SCRAPE_PERFORMER_URL_QUERY`: added `circumcised`, `penis_length`.
  - `update_performer`: replaced simple `.upper()` with validate+uppercase+drop.
  - `apply_scraped_performer`: replaced simple `.upper()` with
    validate+uppercase+skip. Added `circumcised` to gap-fill fields and
    `penis_length` String→Float coercion block.

## Stash Enum Fields Audit

| Enum Type        | Valid Values                                                                 |
|------------------|------------------------------------------------------------------------------|
| `GenderEnum`     | `MALE`, `FEMALE`, `TRANSGENDER_MALE`, `TRANSGENDER_FEMALE`, `INTERSEX`, `NON_BINARY` |
| `CircumisedEnum` | `CUT`, `UNCUT`                                                               |

Scene, studio, and tag mutations have no enum-typed fields.

## Observations

- The Stash `ScrapedPerformer` type returns enum fields as plain `String`,
  not as their enum types. Scrapers can return arbitrary values like `"OTHER"`
  that are not in the Stash enum.
- Defense-in-depth: validation is applied both at the scrape-apply layer
  and at the generic `update_performer` method so all callers benefit.
- Invalid values are logged at WARNING level for visibility but do not
  block the rest of the update from proceeding.
