---
status: SUPERSEDED
created: 2026-09-10
plan: 265
title: Discard the 69 non-uniform _pooled forecasts — an irreversible deletion, with its own runbook
scope: Delete the 69 `_pooled` forecast rows whose `valid_time`s are measured non-uniform, together with their `forecast_values` children, after a restorable backup. ONE operational task with an executable runbook. Explicitly NOT the backfill (Plan 248 T1), NOT the NOT NULL tightening (Plan 248 T3), NOT why `_pooled` stopped (owned elsewhere), NOT the phase/time-grid defect (Plans 252/254/258 — another owner).
depends_on: [248]
blocks: []
source: 2026-09-10 — split out of Plan 248 T2 on the owner's decision, after two independent Codex rounds showed the review cost was concentrated in this deletion and was not converging (round 1: one blocker; round 2: three). A plan document is the wrong home for an irreversible operational procedure.
---

# Plan 265 — discard the 69 non-uniform `_pooled` forecasts

## ⛔ SUPERSEDED by Plan 248's rewrite (owner decision, 2026-09-10) — do not implement from this file

This plan existed to hold an operational runbook for an irreversible deletion. The owner then
clarified that the mac-mini is a **disposable sandbox** while the CODE must be robust. On a
disposable host the 69 rows are cleared by Plan 248 T1's two statements, and none of the machinery
below — manifest, backup-by-restore, `RETURNING` set-equality, pinned expected count — is warranted.

**Read this file for its requirements, not its instructions.** They travelled intact into Plan 248
§ *The operational variant*, which is where they belong: the procedure for a hydromet deployment
whose data IS precious. Nothing here is withdrawn as wrong; it is withdrawn as **not applicable to
this host**.

⚠️ **If a real deployment ever needs this, lift it from Plan 248's operational-variant section, not
from here** — that section also carries the three findings this file predates (backup/deletion state
divergence, the FK-guaranteed orphan check that cannot fail, and the cross-plan cycle that splitting
the deletion created).

## Status

**SUPERSEDED.** Was DRAFT; never implemented. Split out of Plan 248 T2 on 2026-09-10 and retired the
same day.

⛔ **This plan performs the only irreversible step in the 248 family.** It deletes production rows.
Nothing here runs without the owner's explicit word, and the backup gate is a hard precondition, not
a formality.

## Why this is a separate plan

Plan 248 T2 began as a *decision* task ("what is a truthful cadence for a row that has none") and
the owner decided: discard. Converting a decision into a mutation inside the same plan produced a
task that two review rounds could not settle — round 1 found the deletion unspecified, round 2 found
three blockers in the specification that answered round 1, including an ordering fault introduced by
the fix itself.

🔑 **The diagnosis was not "the plan is badly written" but "this is the wrong kind of document."** An
irreversible deletion needs an executable runbook — exact predicates, materialised ID manifests,
`RETURNING` assertions, a restore check — and embedding one in a design document invites a review
round per detail. It gets its own plan so Plan 248's backfill, which two rounds have called correct
and conservative, can proceed on its own merits.

## The decision this inherits (settled — do not re-litigate)

✅ **DISCARD**, decided 2026-09-08. The owner: *"we can discard old forecasts. this is a test
deployment, not the final operational deployment."*

Two alternatives were considered and rejected on grounds that still hold:

- ⛔ **Repair is impossible.** The 69 are multi-phase — a single ensemble whose `valid_time`s do not
  share one offset. No scalar step describes them, so writing `86400` would be a fabrication. The
  codebase already treats this shape as an error: `validate_homogeneous_time_step_and_phase` raises
  `ConfigurationError` (`services/skill/service.py`, Plan 228 D2).
- ⛔ **Do NOT mark them `QC_FAILED`.** QC never ran on these rows and rejected them; they were stored
  *unchecked* (`qc_status = RAW`, hardcoded at the old `forecast_combination.py:404`, which Plan 253
  fixed). Stamping a verdict never reached is the same class of error as fabricating a step.
- ⛔ **Quarantine was tried and withdrawn.** A `NOT VALID` CHECK is still re-evaluated on UPDATE
  against the whole updated row, so it would have frozen these rows against every future status
  transition — `ForecastStore.transition_status` issues exactly such an UPDATE, so a quarantined
  forecast could never move `raw → reviewed → published` again.

⭐ **Why the rows have no honest cadence — the measurement is the binding reason.** These 69 are
*measured* non-uniform and carry no stored product-level declaration. That `_pooled` is a SAP3-side
combination rather than an FI model output is supporting context only: SAP3 builds a combined
ensemble with an inherited scalar step, and the persistence boundary derives a truthful step when the
retained timestamps ARE uniform. A future `_pooled` row on a uniform grid would have an honest step.
It is the non-uniformity of *these* rows that closes the question.

## What is measured (staging, read-only, 2026-09-10 at `0.1.894` / `alembic_version` 0055)

| | value |
|---|---|
| `forecasts` total | 11 083 |
| non-uniform (`n_distinct_gaps > 1`) | **69** |
| ...of which `model_id = '_pooled'` | **69 (all)** |
| ...of which `time_step_seconds IS NULL` | **69 (all)** |
| `time_step_seconds` NULL overall | 7 793 |

🔒 **The population is frozen, and that is why it can be pinned as an ASSERTION.** No `_pooled` row
has been written since 2026-09-04. The count has been 69 across censuses on 09-07, 09-08 and 09-10
while the table grew from 6457 to 11 083. If it has moved when this runs, something changed and a
human must look — which is what step 3 enforces.

## ⛔ Ordering — this runs AFTER Plan 248 T1

🪤 **The frontmatter cannot express this, so read it here.** The relation is TASK-level: this plan
depends on Plan 248 **T1** and is depended on by Plan 248 **T3**. Written as plan-level fields that
would be `depends_on: [248]` + `blocks: [248]` — a cycle, which breaks any graph that consumes these
fields. So `blocks` is empty here, and Plan 248's T3 carries `blocked_by_plan: 265` in its own task
graph instead.

🔴 Plan 248 T1's statement pins `c_expected_non_uniform := 69` and **aborts** if the non-uniform
count differs. If this deletion runs first, T1 finds 0 and refuses. The order is
**248 T1 → 265 → 248 T3**, and it is not optional.

## Task — T1: delete the 69, with a restorable backup

**Outcome:** the 69 non-uniform `_pooled` forecasts and their `forecast_values` children are gone; a
verified, restorable backup of exactly those rows exists; no other row was touched.

**In:** one operational runbook execution against staging.
**Out:** any code change; any schema change (Plan 248 T3); the backfill (Plan 248 T1).

**Pre-change:** 69 rows match the predicate; each has `forecast_values` children;
`forecasts` total is 11 083 (re-measure — this WILL have grown).

### The predicate — ONE definition, used everywhere

A target row is a `forecasts` row satisfying **all three**:

```
model_id = '_pooled'
AND time_step_seconds IS NULL
AND (distinct non-null gaps between its ordered distinct valid_times) > 1
```

🪤 **All three conditions, never fewer.** Earlier drafts used `n_phase > 1` in one place and
`n_distinct_gaps > 1` in another; `n_phase` was never defined. A non-pooled non-uniform row must not
be swept up silently, and a `_pooled` row that has since been stamped must not be either.

### Step 1 — materialise the manifest

Compute the target set and store the exact `forecasts.id` list and the exact
`forecast_values.id` list in a table that survives the transaction (a real table, not `TEMP`, so the
backup step can read it). Record both counts.

⛔ Everything downstream reads the MANIFEST, not a re-evaluated predicate. That is what makes the
backup, the deletion and the verification provably the same rows.

### Step 2 — back up, and prove the backup restores

`pg_dump` (or `COPY … TO`) the manifest's `forecasts` and `forecast_values` rows to a named path on
the backup volume.

🔴 **Then restore it into a scratch schema and compare ID manifests, not counts.** A dump with the
wrong 69 headers and the same number of children satisfies a count check. The gate is: the restored
`forecasts.id` set equals the manifest's, and the restored `forecast_values.id` set equals the
manifest's. Row counts alone are not a backup gate, and this is the one irreversible step here.

### Step 3 — the independent expected-count checkpoint

Compare the manifest count against **69**, the value pinned by three censuses — an expected value
established OUTSIDE this transaction.

🪤 **Comparing a fresh count to another fresh count is circular:** if the population changed from 69
to 70, both sides return 70 and the deletion proceeds — the opposite of "a human must look". The
literal is the point. Abort on any mismatch.

### Step 4 — delete children, then headers, with `RETURNING`

```
BEGIN ISOLATION LEVEL REPEATABLE READ;
```

🪤 `forecast_values.forecast_id` is a plain `sa.ForeignKey("forecasts.id")` with **no**
`ondelete="CASCADE"` (`db/metadata.py`), so deleting headers first raises a foreign-key violation.
Children first, always.

Delete `forecast_values` **by manifest id**, `RETURNING id`; assert the returned set equals the
manifest's child set exactly. Then delete `forecasts` **by manifest id**, `RETURNING id`; assert the
returned set equals the manifest's header set exactly.

⛔ **Assert the RETURNED SETS, not their sizes.** An unqualified
`DELETE FROM forecast_values` followed by deleting the 69 headers leaves zero orphans and the
expected header delta — every size-based postcondition passes while unrelated child data is gone.

### Step 5 — postconditions, in the same transaction

- The returned header ids equal the manifest's, exactly (set equality).
- The returned child ids equal the manifest's, exactly (set equality).
- `forecasts` total fell by exactly the manifest header count **and** `forecast_values` total fell by
  exactly the manifest child count. Both table deltas, not just one.
- Zero rows now match the target predicate.
- 🪤 "No orphaned `forecast_values`" is **not** a postcondition worth writing: the FK guarantees it,
  so it can never fail and cannot detect over-deletion. It was listed as a gate before; it is not one.

Any failure RAISES and rolls back.

### Step 6 — re-census and hand off

Confirm the `time_step_seconds` NULL set now contains only rows Plan 248 T1 backfills, which is what
lets Plan 248 T3 use a plain `SET NOT NULL`. Record the final counts here.

## Exit gates

- The manifest was materialised and both counts recorded before anything was backed up or deleted.
- The backup was **restored** and its ID manifests compared — not merely written and its path noted.
- The expected-count checkpoint compared against the independently pinned 69 and did not abort.
- Children were deleted before headers.
- Both `RETURNING` set-equality assertions passed.
- Both table totals fell by exactly the manifest counts.
- Zero rows match the target predicate afterwards.
- The transaction ran at `REPEATABLE READ` or under a quiet window, so census and mutation share one
  snapshot while the forecast cycle keeps writing.
- Plan 248 T1 has ALREADY RUN (its pinned `c_expected_non_uniform := 69` aborts otherwise).

## Dependency graph

```json
{
  "plan": 265,
  "tasks": [
    {"id": "T1", "depends_on": [], "parallel": false}
  ]
}
```

Cross-plan order: **248 T1 → 265 T1 → 248 T3**.
