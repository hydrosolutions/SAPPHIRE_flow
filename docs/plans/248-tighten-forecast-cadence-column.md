---
status: DRAFT
created: 2026-09-07
plan: 248
title: Tighten forecasts.time_step_seconds to NOT NULL — a sandbox cleanup, then the constraint that ships
scope: TWO tasks, split on whether the artifact SHIPS. T1 clears the legacy NULL rows on the disposable mac-mini sandbox — throwaway, sandbox-only, no rigor warranted. T2 is the shipped change and gets full rigor: a migration to NOT NULL, retiring the reader's nullable-column branch, and its tests. Explicitly NOT the careful backfill (preserved below as the OPERATIONAL variant, for a deployment whose data is precious), NOT the deletion runbook (same), NOT the phase/interleaving defect (Plans 252/254/258 — another owner), NOT how a cadence is derived at write time.
depends_on: [241]
blocks: []
source: 2026-09-07 — required by Plan 241's own exit gate ("the deferred NOT NULL tightening has a named follow-on plan, or this plan does not close"). REWRITTEN 2026-09-10 after three independent Codex rounds, on the owner's clarification that the mac-mini is a disposable sandbox while the CODE must be robust.
---

# Plan 248 — tighten the forecast cadence column

## Status

**DRAFT.** Awaiting owner READY.

⚠️ **REWRITTEN 2026-09-10.** Three independent Codex rounds (1 blocker, then 3, then 3) were spent
hardening a careful backfill and an irreversible deletion against a database the owner then
clarified is **disposable**. The rounds were not wasted — their findings are preserved in
§ The operational variant — but the plan they hardened was the wrong plan for this host.

🔑 **The owner's framing, which decides everything below:** *"the current deployment on the mac-mini
is a disposable sandbox but the code we build should be robust… once we deploy for a hydromet, the
data is precious. as long as we're on the sandbox, we try out."*

## 🔑 Read this first — the sandbox/operational split

This plan does two different KINDS of work, and conflating them is what cost three review rounds.

| | T1 — clear the NULLs | T2 — the constraint |
|---|---|---|
| artifact | a throwaway SQL run | a migration + reader change |
| target | the mac-mini sandbox only | ships to Nepal v1, Oct 2026 |
| if it goes wrong | rebuild the sandbox | a hydromet service reads wrong data |
| rigor warranted | **none beyond "children first"** | **full: tests, migration test, review, pyright** |

⛔ **T1's shortcut is SANDBOX-ONLY and must never be run against an operational deployment.** On a
host whose data is precious, clearing legacy rows by deleting them is not available — you backfill
them instead, under the guards in § The operational variant. A future session reading "just delete
the NULLs" and running it against DHM's database would destroy a hydromet record. That is the whole
reason the operational variant is preserved rather than deleted.

## Why this exists

Plan 241 added `forecasts.time_step_seconds` as a NULLABLE integer and deliberately deferred the
tightening, naming this plan as the follow-on. Until the column is `NOT NULL` the reader must keep a
branch that INFERS a cadence for legacy rows — and inference is what Plan 241 existed to remove.
A tightening with no owner is how a nullable column stays nullable forever.

## What is measured (staging, read-only, 2026-09-10 at `0.1.894` / `alembic_version` 0055)

| | value |
|---|---|
| `forecasts` total | 11 083 |
| `time_step_seconds` NULL | **7 793** |
| `time_step_seconds` stamped | 3 290 |
| └ of the NULLs: uniform-daily | 7 724 |
| └ of the NULLs: non-uniform (all `_pooled`) | 69 |
| `forecast_values` children of `_pooled` rows | 31 740 |

⭐ **The deployed writer works, and the NULL cohort is CLOSED.** The NULL count has been exactly
7 793 across censuses on 09-08 and 09-10 while the table grew from 7 793 to 11 083 — every row
written since the Plan 241 deploy carries a cadence, and no legacy row has been touched.

🔒 **`forecast_values` is the ONLY table referencing `forecasts.id`** (`db/metadata.py`; verified
2026-09-10). Hindcasts and skill scores live in separate tables, so nothing outside these two tables
is affected by anything this plan does. That is what makes T1 two statements rather than a cascade.

## T1 — clear the legacy NULL rows (⚠️ SANDBOX ONLY, throwaway)

**Outcome:** no `forecasts` row has `time_step_seconds IS NULL`, so T2's `SET NOT NULL` has nothing
to trip over.

**In:** one ad-hoc SQL run against the mac-mini.
**Out:** any code change; any schema change (T2); any operational host (see the variant below).

**Pre-change:** 7 793 rows are NULL (re-measure — the total grows ~1 336/day, but the NULL cohort
has not moved).

```sql
-- Plan 248 T1 — SANDBOX ONLY. Clears the legacy NULL cohort so the column can be tightened.
-- Children first: forecast_values.forecast_id is a plain ForeignKey with NO ondelete=CASCADE,
-- so deleting headers first raises a foreign-key violation.
BEGIN;

DELETE FROM forecast_values
 WHERE forecast_id IN (SELECT id FROM forecasts WHERE time_step_seconds IS NULL);

DELETE FROM forecasts
 WHERE time_step_seconds IS NULL;

-- The only assertion worth making here: nothing is left for T2 to trip over.
DO $$
DECLARE v_null int;
BEGIN
    SELECT count(*) INTO v_null FROM forecasts WHERE time_step_seconds IS NULL;
    IF v_null <> 0 THEN
        RAISE EXCEPTION 'still % NULL rows — T2 SET NOT NULL would fail', v_null;
    END IF;
END $$;

COMMIT;
```

**Verification:** the assertion above, plus `count(*) WHERE time_step_seconds IS NULL = 0`.

**What this deliberately does NOT do**, and why that is correct HERE and wrong on an operational
host: no backup, no ID manifest, no restore check, no `RETURNING` set-equality assertions, no pinned
expected count, no `REPEATABLE READ`, no rollback rehearsal. Every one of those exists to protect
data. This data is disposable and the forecast cycle refills the table at ~1 336 rows/day.

⚠️ **Accepted consequences:** the sandbox loses most of its operational forecast history until the
cycle refills. The API and Forecast Lab will show a thin record meanwhile. The 69 `_pooled` rows go
too — the only surviving record of what `_pooled` wrote before it went dark on 2026-09-04 (owner
decision 2026-09-08: *"we can discard old forecasts. this is a test deployment"*, reconfirmed
2026-09-10). Skill scores are computed from hindcasts and are unaffected.

## T2 — the constraint and the reader retirement (SHIPS — full rigor)

**Outcome:** `time_step_seconds` is `NOT NULL`, and the reader's entire nullable-column branch —
both the gap-inference path and the single-timestamp fabricated-hour path — is deleted.

🔑 **This is the part that reaches a hydromet service.** `NOT NULL` is the point: it makes an
invariant the code already maintains unrepresentable in the schema, so no future write can
reintroduce an inferred cadence. Same parse-don't-validate principle `CLAUDE.md` applies to types,
applied to the database.

**In:**
- a new migration, **chained from the head current at implementation time** (`0055` as of
  2026-09-10, pinned as `_RELEASE_B_HEAD` in `tests/unit/db/test_alembic_head_release_b.py`) —
  update that pin in the SAME change, because two migrations sharing a `down_revision` give alembic
  two heads and break every upgrade;
- `db/metadata.py`;
- `store/forecast_store.py` — the whole `stored_time_step_seconds is None` branch (`:388-405`);
- `tests/integration/store/test_forecast_store_time_step.py` — **BOTH** tests, not one: the
  multi-step legacy test and the one-step test share the helper at `:115` that writes NULL by
  UPDATE, and that UPDATE starts failing the moment the constraint lands. Deleted in the same commit
  as the branch, never before it and never "fixed" afterwards by relaxing the constraint;
- a DB-backed migration test (upgrade applies, downgrade reverses, a NULL insert is rejected).

**Out:** T1's cleanup; the phase column (Plan 254 T7); how a cadence is derived at write time.

**Pre-change:** an INSERT with `time_step_seconds = NULL` succeeds today; after the migration it must
be rejected by the database, not merely by application code.

**Verification:** zero NULL rows counted in-transaction before the constraint is added (not assumed
from T1 having run); the constraint rejects a NULL insert; neither reader branch remains; the two
legacy tests are gone; the head pin matches the new migration; `uv run pyright`; the full suite.

## 📦 The operational variant — what to do when the data IS precious

⛔ **Do NOT run T1 above on a hydromet deployment.** Preserved here is the approach three Codex
rounds produced for exactly that case. It is not dead work; it is the operational procedure, and it
should be lifted into its own plan when a real deployment needs it.

**Instead of deleting the legacy rows, BACKFILL the ones whose cadence is measurable.** The rows
whose `valid_time`s are uniformly spaced have a truthful step that can be recovered; only genuinely
non-uniform rows have none.

The statement below was reviewed across three rounds. Its guards are the point:

- it asserts STRUCTURE and derives its target in-transaction, never pinning a row count that drifts;
- it matches the reader exactly, grouping by `(forecast_id, valid_time)` because the reader dedups
  members before measuring the gap;
- it pins the non-uniform count as an ASSERTION so a changed population aborts rather than proceeds;
- it runs at `REPEATABLE READ` so the census and the mutation share one snapshot while the cycle
  keeps writing — ⚠️ which admits a `40001` serialization failure, so the runbook needs a
  retry-the-whole-run policy and `ON_ERROR_STOP`;
- every assertion RAISES, aborting the transaction.

⛔ **Rehearse it under `ROLLBACK` against the real target state before running it for real**, and
against a NON-ZERO already-stamped count — the 2026-09-08 rehearsal ran at `already_stamped = 0` and
so never exercised that path.

```sql
-- Plan 248 T1 — backfill forecasts.time_step_seconds for MEASURED uniform-daily rows.
--
-- Stores exactly what the reader's inference already returns for these rows, so it
-- changes no published number. Non-uniform rows are left NULL deliberately (Plan 265).
--
-- Run as a single transaction at REPEATABLE READ, so the census and the mutation share one
-- snapshot while the forecast cycle keeps writing. Every assertion RAISES, aborting the whole thing.

BEGIN ISOLATION LEVEL REPEATABLE READ;

-- Match the reader exactly: it dedups members via .unique() before measuring the gap,
-- so the census must group by (forecast_id, valid_time), not count member rows.
CREATE TEMP TABLE t_cadence ON COMMIT DROP AS
WITH vt AS (
    SELECT forecast_id, valid_time
    FROM forecast_values
    GROUP BY forecast_id, valid_time
),
gaps AS (
    SELECT forecast_id,
           valid_time - lag(valid_time) OVER (PARTITION BY forecast_id ORDER BY valid_time) AS gap
    FROM vt
)
SELECT f.id,
       count(g.gap)          AS n_gaps,
       count(DISTINCT g.gap) AS n_distinct_gaps,
       max(g.gap)            AS gap
FROM forecasts f
LEFT JOIN gaps g ON g.forecast_id = f.id AND g.gap IS NOT NULL
GROUP BY f.id;

CREATE INDEX ON t_cadence (id);
ANALYZE t_cadence;

DO $$
DECLARE
    -- Single source of truth: comparison AND message read this, so a future
    -- change cannot leave the error text disagreeing with the guard.
    c_expected_non_uniform CONSTANT int := 69;
    v_total             int;
    v_classified        int;
    v_already_stamped   int;
    v_single            int;
    v_uniform_daily     int;
    v_uniform_non_daily int;
    v_non_uniform       int;
    v_updated           int;
    v_null_after        int;
    v_nonuniform_stamped int;
BEGIN
    SELECT count(*) INTO v_total FROM forecasts;
    SELECT count(*) INTO v_classified FROM t_cadence;
    SELECT count(*) INTO v_already_stamped FROM forecasts WHERE time_step_seconds IS NOT NULL;

    SELECT count(*) INTO v_single            FROM t_cadence WHERE n_gaps = 0;
    SELECT count(*) INTO v_uniform_daily     FROM t_cadence WHERE n_distinct_gaps = 1 AND gap =  interval '1 day';
    SELECT count(*) INTO v_uniform_non_daily FROM t_cadence WHERE n_distinct_gaps = 1 AND gap <> interval '1 day';
    SELECT count(*) INTO v_non_uniform       FROM t_cadence WHERE n_distinct_gaps > 1;

    RAISE NOTICE 'census: total=% classified=% already_stamped=% single=% uniform_daily=% uniform_non_daily=% non_uniform=%',
        v_total, v_classified, v_already_stamped, v_single, v_uniform_daily, v_uniform_non_daily, v_non_uniform;

    -- STRUCTURAL preflight. Deliberately NOT a hardcoded row count: the cycle keeps
    -- running, so any literal total is stale the moment it is written. These hold
    -- regardless of how many forecasts exist.
    IF v_classified <> v_total THEN
        RAISE EXCEPTION 'classification lost rows: % classified vs % forecasts', v_classified, v_total;
    END IF;
    IF v_single <> 0 THEN
        RAISE EXCEPTION 'single-timestamp forecasts appeared (%); they have no derivable cadence — re-scope before running', v_single;
    END IF;
    IF v_uniform_non_daily <> 0 THEN
        RAISE EXCEPTION 'uniform non-daily forecasts appeared (%); this statement only backfills daily — re-scope', v_uniform_non_daily;
    END IF;
    IF v_single + v_uniform_daily + v_uniform_non_daily + v_non_uniform <> v_total THEN
        RAISE EXCEPTION 'classes do not partition the table';
    END IF;

    -- The ONE count assertion worth pinning, and it is drift-proof for the right
    -- reason: pooled writes stopped 2026-09-04, so this population is frozen at 69.
    -- If pooled resumes, this aborts — the correct outcome, because Plan 265's deletion
    -- is scoped to the 69 that exist now, not to rows written after this measurement.
    IF v_non_uniform <> c_expected_non_uniform THEN
        RAISE EXCEPTION 'non-uniform population moved: expected %, found % — pooled writes may have resumed; STOP and re-measure',
            c_expected_non_uniform, v_non_uniform;
    END IF;

    UPDATE forecasts f
       SET time_step_seconds = extract(epoch FROM c.gap)::int
      FROM t_cadence c
     WHERE c.id = f.id
       AND c.n_distinct_gaps = 1
       AND c.gap = interval '1 day'
       AND f.time_step_seconds IS NULL;
    GET DIAGNOSTICS v_updated = ROW_COUNT;

    -- Target computed in the SAME transaction from the SAME temp table, so it cannot
    -- drift between measuring and writing.
    IF v_updated <> v_uniform_daily - v_already_stamped THEN
        RAISE EXCEPTION 'updated % rows, expected % (uniform_daily % minus already_stamped %)',
            v_updated, v_uniform_daily - v_already_stamped, v_uniform_daily, v_already_stamped;
    END IF;

    -- No non-uniform row may carry a stamp.
    SELECT count(*) INTO v_nonuniform_stamped
      FROM forecasts f JOIN t_cadence c ON c.id = f.id
     WHERE c.n_distinct_gaps > 1 AND f.time_step_seconds IS NOT NULL;
    IF v_nonuniform_stamped <> 0 THEN
        RAISE EXCEPTION 'stamped % non-uniform rows — must remain NULL', v_nonuniform_stamped;
    END IF;

    -- Every remaining NULL must be non-uniform, and nothing else.
    SELECT count(*) INTO v_null_after FROM forecasts WHERE time_step_seconds IS NULL;
    IF v_null_after <> v_non_uniform THEN
        RAISE EXCEPTION 'after backfill % rows remain NULL but only % are non-uniform', v_null_after, v_non_uniform;
    END IF;

    -- Every stamped value must satisfy 0053's check constraint and be a real cadence.
    IF EXISTS (SELECT 1 FROM forecasts WHERE time_step_seconds IS NOT NULL AND time_step_seconds <> 86400) THEN
        RAISE EXCEPTION 'a stamped value is not 86400';
    END IF;

    RAISE NOTICE 'OK: updated % rows; % remain NULL (all non-uniform)', v_updated, v_null_after;
END $$;

COMMIT;
```

**And for rows with no measurable cadence**, an operational host cannot simply delete them either.
The requirements three rounds established, none of which T1 above needs:

1. ONE predicate, defined once, used everywhere (an earlier draft used an undefined `n_phase` in one
   place and `n_distinct_gaps > 1` in another).
2. A **materialised ID manifest**, read by every later step instead of re-evaluating the predicate —
   that is what makes backup, deletion and verification provably the same rows.
3. A backup proven by **RESTORE and full-row comparison**, not row counts: a dump with the wrong
   rows and the right count passes a count gate. IDs alone are not enough either — correct IDs with
   stale non-ID fields also pass.
4. An expected-count checkpoint against an **independently established** value. Comparing two fresh
   counts is circular: if the population moved from 69 to 70, both sides return 70 and it proceeds.
5. **Children before headers** — the FK does not cascade.
6. `DELETE … RETURNING` with **set-equality** assertions, not size assertions: an unqualified child
   delete followed by deleting the intended headers leaves zero orphans AND the expected header
   delta, so every size-based postcondition passes while unrelated data is destroyed.
7. 🪤 "No orphaned `forecast_values`" is NOT a postcondition worth writing — the FK guarantees it, so
   it can never fail and cannot detect over-deletion.
8. Backup and deletion must observe the **same state**. If a target row transitions
   `raw → reviewed` between the backup and the delete, the newer version is deleted by the same id,
   every id and delta check passes, and the backup restores the older row.

## Exit gates

- T1: `count(*) FROM forecasts WHERE time_step_seconds IS NULL` is **0** on the sandbox.
- T2's migration chains from the head current at implementation time, and the head pin is updated in
  the same change.
- T2 counts zero NULL rows IN-TRANSACTION before adding the constraint — it does not infer that from
  T1 having run.
- An INSERT with `time_step_seconds = NULL` is rejected by the DATABASE.
- Neither reader NULL path remains in `store/forecast_store.py`.
- Both legacy tests in `test_forecast_store_time_step.py` are deleted in the same commit as the
  branch they pin.
- The DB-backed migration test passes upgrade, downgrade and NULL-rejection.
- `uv run pyright` passes the ratchet; the full suite passes after the final code change.
- T2 received one independent Claude and one independent Codex review of the DIFF.

## Dependency graph

```json
{
  "plan": 248,
  "tasks": [
    {"id": "T1", "depends_on": [], "parallel": false},
    {"id": "T2", "depends_on": ["T1"], "parallel": false}
  ]
}
```

## Review history

- **Round 1** (Codex, 2026-09-09) — 1 blocker, 3 major, 3 minor. The blocker: T2 decided "discard"
  but no task owned the deletion, so the graph left 69 NULLs and the tightening would have failed.
- **Round 2** (Codex, 2026-09-10) — 3 blockers, one of them introduced by the round-1 fix, which
  converted a decision task into a mutation without changing its ordering.
- **Round 3** (Codex, 2026-09-10) — 3 blockers, all introduced by splitting the deletion into its
  own plan (a cross-plan dependency cycle, backup/deletion state divergence, and a handoff gate that
  had lost its teeth). 5 of 8 earlier findings confirmed closed.
- **Rewrite** (2026-09-10) — the owner clarified that the host is a disposable sandbox while the code
  must be robust. That removed more findings than the three rounds did, because most of them
  protected data that does not need protecting. Plan 265 (the split-out deletion) is retired as
  SUPERSEDED; its requirements survive above as the operational variant.
