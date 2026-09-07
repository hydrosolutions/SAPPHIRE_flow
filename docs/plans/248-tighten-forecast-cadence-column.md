---
status: DRAFT
created: 2026-09-07
plan: 248
title: Backfill and tighten forecasts.time_step_seconds — the half Plan 241 deliberately deferred
scope: Finish the two-release column tightening Plan 241 T4 started. Backfill the MEASURED uniform-daily rows, decide what a truthful cadence is for rows that have none, then tighten the column to NOT NULL. Explicitly NOT the phase/interleaving defect (Plans 245/247), NOT any change to how a cadence is derived at write time.
depends_on: [241]
blocks: []
source: 2026-09-07 — required by Plan 241's own exit gate ("the deferred NOT NULL tightening has a named follow-on plan, or this plan does not close"), and by a live measurement of the staging database taken the same day
---

# Plan 248 — backfill and tighten the forecast cadence column

## Status

**DRAFT.** Created to satisfy Plan 241's exit gate. Not scoped in detail, not reviewed, and
**blocked** until Plan 241 is deployed — the column does not exist in staging yet.

## Why this exists

Plan 241 T4 added `forecasts.time_step_seconds` NULLABLE-FIRST, per `docs/standards/cicd.md`
§Rollback ("additive only: new columns nullable"), following the `station_weather_sources.role`
115a/115c precedent. That is deliberately half of a two-release change. This is the other half.

⛔ Plan 241 explicitly forbids closing while this plan does not exist, precisely because a deferred
tightening with no owner is how a nullable column stays nullable forever.

## What is already measured (staging, 2026-09-07, read-only)

| | |
|---|---|
| forecasts total | 6457 |
| single-timestamp | **0** |
| uniform, gap = 86400 s | 6388 |
| uniform, gap ≠ 86400 s | **0** |
| non-uniform | 69 (ALL `_pooled`) |

Sanity checks run alongside: no forecast headers without values, no NULL `valid_time`s, no duplicate
`(forecast, valid_time, member)` rows, no multi-parameter forecasts. The census denominator is sound.

A backfill of the 6388 uniform-daily rows was **independently reviewed and approved 2026-09-07**,
conditional on a same-transaction preflight using exact interval equality (not rounded epoch
seconds), an asserted target count, and the 69 left NULL. The statement is drafted and unrun. It
changes no published number — for a uniform row it stores exactly what inference already returns.

## The open question this plan must answer

**What is a truthful cadence for a row that has none?** The 69 non-uniform rows are not daily
forecasts with a blemish — they are TWO INTERLEAVED DAILY SERIES, one at exact midnight and one at
`06:00:02.858976`:

```
2026-09-05 00:00:00+00
2026-09-05 06:00:02.858976+00
2026-09-06 00:00:00+00
2026-09-06 06:00:02.858976+00
```

No single scalar describes that. `NOT NULL` can therefore only be reached by inventing a value,
deleting the rows, or quarantining them — and inventing one is the defect Plan 241 exists to remove.

🔴 **This is a symptom, not the disease.** 4182 of 6457 forecasts carry a non-zero PHASE
(microsecond-bearing `valid_time`s from `climatology_fallback`, `persistence_fallback`,
`linear_regression_daily` and `_pooled`). They stay uniform in isolation — a constant offset
preserves the gap — and only collide when pooled across contributors on different phases. **Plans
245 (a time grid is a step AND a phase) and 247 (phase-aware execution) already own that**; this
plan must not duplicate them, and should probably WAIT for them, since what they decide determines
whether these 69 rows get repaired or discarded.

## ⛔ Do not start before

1. Plan 241 is merged AND deployed — the column is absent in staging today.
2. The 245/247 direction is settled, or T2 is explicitly carved out as independent of it.

## Tasks

⚠️ Sketched, NOT scoped. Each needs its In/Out and Pre-change ledger before this leaves DRAFT.

### T1 — backfill the rows whose cadence is measured, not guessed
**Outcome:** every uniform-daily forecast carries its cadence in the column; the non-uniform rows
remain NULL and untouched.
**In:** a one-off staging repair statement (NOT an amendment to migration 0053, which is applied).
**Out:** the 69 non-uniform rows; any change to 0053; any tightening.
**Pre-change:** the column is NULL for all 6457 rows, so every read still goes through inference.
**Verification:** the reviewed statement's own preflight — exact interval equality, asserted
target count of 6388, and a post-update assertion that no non-uniform row was stamped.

### T2 — decide what a truthful cadence is for a row that has none
**Outcome:** a recorded owner decision for the 69 rows: repair, quarantine, or delete.
**In:** this plan document.
**Out:** any code change; the phase defect itself (Plans 245/247).
**Pre-change:** N/A — decision task, no behaviour to fail.
**Verification:** the decision and its rationale are written here, naming which of 245/247 it
depends on.

### T3 — tighten the column, and retire the legacy branch
**Outcome:** `time_step_seconds` is `NOT NULL`; the reader's gap-inference and its fabricated-hour
fallback are deleted, because no row can reach them any more.
**In:** a new migration; `db/metadata.py`; `store/forecast_store.py`.
**Out:** T1's backfill; T2's decision.
**Pre-change:** ⛔ the fabricated-hour branch is REACHABLE today — measured against main, a
one-timestamp forecast stores and reads back at 1:00:00. It may only be deleted once T1 and T2
guarantee no NULL row survives; deleting it earlier raises `IndexError` on a readable row.
**Verification:** zero NULL rows before the migration runs; the Plan 241 regression guard
`test_legacy_one_step_row_still_returns_the_fabricated_hour` is deleted in the SAME change that
deletes the branch it pins.

## Exit gates

```bash
uv run pytest tests/unit
uv run pytest tests/integration
uv run ruff check src tests && uv run ruff format --check src tests
```

- T1's preflight aborted on no surprise, and its target count matched the re-measured census.
- T2's decision is recorded here with its rationale.
- T3 leaves no NULL rows and no fabricated cadence anywhere in the reader.

## Dependency graph

```json
{
  "plan": 248,
  "tasks": [
    {"id": "T1", "depends_on": [], "parallel": false},
    {"id": "T2", "depends_on": [], "parallel": true},
    {"id": "T3", "depends_on": ["T1", "T2"], "parallel": false}
  ]
}
```
