---
status: READY
---
# Protected reference append: serialize concurrent appends of the same id

HIGH RISK under `AGENTS.md` (data integrity, protected reference path). MVP, no production: smallest change only.

## Problem

`PgProtectedReferenceWriter.append_reference_evidence`
(`src/sapphire_flow/store/rating_reference_store.py:~251-263`) inserts with
`ON CONFLICT (id) DO NOTHING`. `rating_reference_proofs` also has a second unique
constraint, `uq_rating_reference_scope` on `(id, tenant_id, station_id, rating_curve_id)`
(`db/metadata.py:2831-2837`, migration 0068). Postgres only swallows a conflict on the named
arbiter. Two transactions appending the identical evidence at the same instant can hit the
scope constraint instead, and the second raises `UniqueViolation` where idempotent success
was intended.

Evidence (2026-11-26): `tests/integration/store/test_assumed_reference_store.py::
TestReferenceAppendTransactions::test_concurrent_appends_serialize_identity[False]` failed
once on CI (`integration (rest)`, PR #676) with exactly this error and reproduced locally on a
PostGIS container: 3 failures across two loops of 150 runs. A single run passes. No data is
corrupted: the losing append rolls back with an error. It needs concurrent appends of the same
reference id (owner-only path), so production impact is rare; CI impact is a roughly 1-2%
flake of one shard.

## Decision (owner, 2026-11-26)

Fix it now with the smallest change.

## Change

In `append_reference_evidence`, after the isolation and owner checks and the content
agreement check, and BEFORE the insert, take a transaction-scoped advisory lock keyed on the
evidence id on the caller's connection:
`SELECT pg_advisory_xact_lock(hashtextextended(:id, 0))` with `:id` the id as text. The lock
is released at the caller's commit or rollback, so the "never commits" contract is unchanged.
The second writer waits, then sees the committed row: `ON CONFLICT (id) DO NOTHING` skips,
and the existing fetch-and-compare returns the id for identical content or raises the existing
`ValueError` for different content. No change to the schema, constraints or migrations. Do not
drop `uq_rating_reference_scope` (probably a composite foreign-key target).

Key: bind `str(evidence.id)` (a UUID object does not bind as text; add `CAST(:id AS text)` if
psycopg complains) and copy the idiom already used at `daily_forecast_store.py:372` and
`:529`; prefix the key with `"rating_reference_proofs:"` so it cannot coincide with those
unrelated keys. Add a source comment: the lock must precede the INSERT.

Lock scope: per evidence id, so unrelated appends do not block each other (a hash collision
could only cause a spurious wait, never wrong results). Callers that
append the same id twice inside one transaction re-acquire the same lock harmlessly
(advisory xact locks are reentrant within a session).

## Tests

Red first: loop `test_concurrent_appends_serialize_identity` (both `conflicting=False` and
`conflicting=True`) on the PostGIS testcontainer BEFORE the change and record the failure
count (expect a few failures in 150-300 runs), then the same loop AFTER the change (expect
0 in at least 300 runs). Add a deterministic regression test (no threads, no sleeps) using
`isolated_committed_db_engine` (`tests/integration/committed_database.py:14`): commit the seed,
hold ONLY the advisory lock on connection A (`engine.begin()` plus `SELECT
pg_advisory_xact_lock(hashtextextended(<same key>, 0))`), then on connection B run the append
with `SET LOCAL lock_timeout='300ms'` and assert `OperationalError` (lock not available).
A blocked or timed-out append proves the lock is HONOURED, not that it comes BEFORE the
INSERT, so also attach a connection-local SQLAlchemy execution listener on B and assert that
NO insert into `rating_reference_proofs` was issued before the timeout (a lock placed after
the INSERT would fail this). Assert the row is absent after B rolls back; commit A and rerun B:
it must succeed. Mutations: removing the lock, and moving it after the INSERT, must each fail
the test. Do not combine this with the existing test's `statement_timeout`. The
300-run loop above remains the stress check.
Run the whole `tests/integration/store/test_assumed_reference_store.py` and any other test
that calls the writer (grep `append_reference_evidence`) plus the neighbouring rating
reference store tests.

## Out of scope

Dropping or changing constraints, the bare `ON CONFLICT DO NOTHING` alternative, changes to
the read path, callers, or the READ COMMITTED requirement.

## Review and merge

Independent Claude and Codex plan reviews before READY; independent patch reviews; ruff,
changed-module types, the rating-reference tests above; full CI. Owner merges.
