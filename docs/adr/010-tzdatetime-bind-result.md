# ADR-010: TZDateTime Bind and Result Consistency for SQLite

**Status**: Accepted

## Context

SQLite stores datetimes as text and does not preserve timezone information. When the app compares `Channel.last_checked_at` (from the DB) with `datetime.now(UTC)` in the channel checker, any naive value loaded from SQLite causes `TypeError: can't compare offset-naive and offset-aware datetimes`. The existing `TZDateTime` type used `process_result_value` to attach UTC on read, but in some cases (e.g. pre-existing data or driver behavior) values could still be naive, breaking the scheduler every run.

## Decision

Use a **custom SQLAlchemy TypeDecorator** (`TZDateTime` in `app/models.py`) that:

1. **On bind (write)**: Normalize aware datetimes to UTC and strip `tzinfo` before passing to SQLite, so stored values are consistent.
2. **On result (read)**: Re-attach `timezone.utc` to any naive datetime returned by SQLite so Python always sees an aware datetime.

Application code that compares DB datetimes to `datetime.now(UTC)` may additionally use a small helper (`_ensure_aware`) to defensively normalize any value before comparison, so the system is robust even if the type decorator is bypassed.

## Alternatives Considered

### Rely only on process_result_value
- Already in place; failed to prevent naive values in production.
- Rejected because bind-side normalization was missing and defensive comparison was not applied.

### Store and compare naive datetimes everywhere
- Use `datetime.utcnow()` and never attach tzinfo.
- Rejected because mixing naive and aware elsewhere (e.g. APScheduler) increases the risk of bugs; standardizing on UTC-aware is clearer.

## Consequences

**Positive:**
- Channel checker and any other code comparing DB datetimes to `now(UTC)` no longer raise.
- Writes are consistent; reads are always safe to compare with aware datetimes.
- Single place (TZDateTime) to maintain timezone behavior for all datetime columns.

**Negative:**
- Slight overhead on every datetime read/write (minimal).
- New contributors must use `datetime.now(UTC)` (or equivalent) when creating datetimes for the DB; the type does not auto-attach on bind for naive input (only normalizes aware input).
