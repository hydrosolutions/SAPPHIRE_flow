---
status: READY
created: 2026-08-31
plan: 222
title: Pool only where every contributor is present
scope: One change to `combine_ensembles_pooled`, one freshness rule so the corrected absence is visible, and the contract text that describes both. Cut to this size in round 5 after four adversarial rounds grew it to eight tasks.
depends_on: [204]
blocks: []
source: 2026-08-31 — SAPPHIRE-flow-map reported an alternating `_pooled` median at station 2091; diagnosed and reproduced through the production functions
---

# Plan 222 — the pooled sawtooth

## Status

**READY** — owner confirmed 2026-08-31. Cleared for `/implement`.

Five independent Codex rounds. Rounds 1-4 found real defects and
grew the plan from five tasks to eight; **round 5 was a proportionality review and returned TOO BIG
BY 5 TASKS.** This document is the cut version: two code tasks and a doc task.

The findings that were cut were not wrong — they were verified. They were *adjacent*. Each is
recorded under "Cut in round 5" with its evidence so nothing is lost.

## The defect

`combine_ensembles_pooled` concatenates member frames without ever joining on `valid_time`
(`services/forecast_combination.py:39-89`), so the pooled ensemble spans the **union** of its
sources' grids and `_ensemble_points()` summarises each timestamp over whichever rows carry it
(`services/forecast_lab/snapshot.py:245-262`).

Two model families sit on different grids: the NWP pair adopts the delivered forcing's UTC-midnight
buckets (`models/nwp_regression.py:472`, `services/operational_inputs.py:147`), while
`linear_regression_daily` builds `issue_time + (k+1)·step`
(`models/linear_regression_daily.py:164-166`) from a raw wall clock
(`flows/run_forecast_cycle.py:684-690`).

Running the real function over three ensembles on those two grids reproduces every published
symptom on the first attempt:

```
member_count (-> ensemble_size): 92
  2026-08-31 00:00:00+00:00         n_members= 42   median=   854.5
  2026-08-31 18:00:01.851153+00:00  n_members= 50   median=   544.5
  2026-09-01 00:00:00+00:00         n_members= 42   median=   854.5
native_step_seconds on read: 64801
```

`64801` is `18h 0m 1.851s` truncated — the store derives a forecast's step by differencing the first
two distinct valid_times (`store/forecast_store.py:316-321`).

**Never tested because** every fixture in `TestCombineEnsemblesPooled` comes from one helper at
`n_steps=10`, so all models always share a grid
(`tests/unit/services/test_forecast_combination.py:63-70`).

## Decisions

### D2 — pooling semantics (owner-settled)

**A combined forecast for a parameter is published only if at least two models contribute members
for it, and only at the timestamps where every contributor has members.** The contributor set is
the models holding a `MEMBERS` ensemble for that parameter — the existing `skip_non_members` filter
(`services/forecast_combination.py:58-65`). Fewer than two contributors, or an empty intersection,
means no combined forecast for that parameter — not a narrower pool.

**Completeness is counted BOTH ways** *(implement round 2, major)*. A timestamp is complete for a
contributor only when its **row count** AND its `n_unique(member_id)` both equal that contributor's
member count. Comparing unique members alone lets a duplicate `(valid_time, member_id)` row pass:
neither `ForecastEnsemble.from_members` nor the database enforces uniqueness of that pair, and
`_ensemble_points()` then summarises every row (`services/forecast_lab/snapshot.py:251`),
overweighting the duplicated member and restoring the varying denominator this plan exists to
remove.

Rejected: a *minimum contributor count* keeps publishing at a varying denominator; *resampling onto
a common grid* is silent interpolation of a forecast.

### D6 — a single-timestamp intersection must not be PERSISTED

The store fabricates a one-hour `time_step` for a single-timestamp forecast
(`store/forecast_store.py:316-321`), which would publish `native_step_seconds: 3600` — a fresh
instance of the defect this plan removes. Require at least two **uniformly spaced** retained
timestamps **at the persistence boundary**, which is one named reachable place:
`build_combined_forecasts()` (`services/forecast_combination.py:189`), and derive `time_step` from
that verified grid.

**Counting is not enough** *(implement round 2, major)*. The intersection can drop an *interior*
timestamp, leaving e.g. 07:00, 09:00, 10:00 — deltas of 2h then 1h. A count-only floor persists it,
and the store derives the step from the first pair on readback
(`store/forecast_store.py:316-321`), publishing one misleading `native_step_seconds` for an
irregular grid. That is the original defect in a new costume. Non-uniform → do not persist that
parameter. It must **not** live in the shared combine helper, which
`services/skill/combined_skill.py:104-110` also calls and where a one-timestamp ensemble is legal.

### D7 — shipping this takes the combined forecast dark, and that requires T7

The intersection is empty under today's grids, so the cycle stops writing `_pooled` rows for
stations 2009 and 2091 until **Plan 224** re-anchors the daily models. Absence is the accepted,
honest outcome.

**But not writing is not the same as not publishing.** The export fetches with no age constraint
(`services/forecast_lab/db_sources.py:145-152`). Plan 204 named this hazard in its own docstring —
"would keep exporting a stale `_pooled` row forever" (`services/forecast_lab/snapshot.py:419-425`)
— and guarded only the `pooled → primary` strategy switch. This plan creates the case it does not
guard, and without T7 the last sawtooth is served indefinitely, marked `complete`. **That is worse
than the defect being fixed.**

## Phase graph

```json
{
  "phases": [
    {"id": "phase-1", "tasks": ["T3", "T7"], "parallel": true},
    {"id": "phase-2", "tasks": ["T6"], "parallel": false, "depends_on": ["phase-1"]}
  ]
}
```

## Tasks

### T3 — pool on the intersection

In `combine_ensembles_pooled` (`services/forecast_combination.py:39-89`), compute the common
`valid_time` set across the eligible member ensembles, filter each frame to it before the existing
concatenation, and return no entry for a parameter whose intersection is empty or whose contributor
count is below two. Apply D6's two-timestamp floor in `build_combined_forecasts()`.

`services/skill/combined_skill.py:107` calls the same function and inherits the fix.

**Locking tests (red first):**
1. **Disjoint grids** → the parameter is absent from the result. *(Today: a union ensemble with an
   alternating member count.)* This is the reported defect.
2. **Partially overlapping grids** → exactly the overlapping timestamps, every one carrying the full
   member count. Proves intersection rather than all-or-nothing suppression.
3. **Single shared timestamp** → no *persisted* combined forecast (D6), while direct pooling still
   returns the ensemble.

**Must not change:** output for identical grids; any model's `valid_time` construction;
`combine_ensembles_bma`.

### T7 — an absent combined forecast must READ as absent 🔴

**Deploy blocker (D7). This plan is unsafe to ship without it.**

Constrain the combined-forecast fetch so a row that is not from the current publication cycle reads
as absent, returning the existing `CombinedForecastUnavailableSchema`. The unavailable shape already
exists and carries a `reason`, so **no `schema_version` change**.

**The predicate cannot be a timestamp comparison.** A scheduled cycle's issue time is `clock()` at
runtime (`flows/run_forecast_cycle.py:684-690`), so the export cannot reconstruct it; an age bound
deliberately keeps serving a product that no longer exists.

**The marker is the forecast rows themselves — NOT `FORECAST_FRESHNESS`** *(revised after implement
round 2; the heartbeat rule this plan previously prescribed was unsound)*. Select the snapshot-wide

    MAX(forecasts.issued_at) WHERE combination_strategy IS NULL AND issued_at <= data_cutoff_at

and fetch the combined row at **exactly** that `issued_at`.

**Why not the heartbeat.** `FORECAST_FRESHNESS`'s append is **best-effort** — its producer catches
every failure and continues (`flows/run_forecast_cycle.py:775-800`). Cycle A writes the defective
`_pooled` row and its heartbeat; cycle B correctly writes no `_pooled` row but its heartbeat append
fails silently; the newest heartbeat is still A's, so the export pins to A and serves the stale row
forever while the flow reports success. Making the append fail-loud does not help: the export stays
pinned to A regardless. The forecast rows are the authoritative record of what was published, and
need no separate marker that can fail.

**Why `issued_at` and not `nwp_cycle_reference_time`:** the latter is `NULL` for runoff-only models
and can reference an older fallback cycle. `issued_at` is non-null and receives `resolved_cycle_time`
on the station, group, fallback and combined paths (`services/run_station_forecast.py:542`,
`services/run_group_forecast.py:331`).

**Accepted failure semantics** — stated because the previous design failed for want of exactly this:
- *Fails closed.* The first committed ordinary row from cycle B advances the marker, so a cycle that
  writes no combined row yields absence, never a fallback to A. `MAX` stops an out-of-order or
  backfilled write regressing it; `<= data_cutoff_at` stops a future-dated override publishing early.
- *Torn reads are possible and accepted.* Rows commit separately, so the export can see B's ordinary
  row before B's `_pooled` row and briefly report the combined forecast unavailable. It never serves
  A. Closing that window entirely needs cycle-wide transactional publication — disproportionate here,
  and a transient absence is strictly better than indefinitely serving a known-wrong value.
- *Backfill/replay* carries the overridden `cycle_time` as `issued_at`, so a historical replay cannot
  displace a newer scheduled cycle.

**Locking test (red first):** store cycle A's ordinary rows, its `_pooled` row and its heartbeat;
then store a cycle B ordinary row with **no** `_pooled` row and **no** B heartbeat. The snapshot
reports `no_combined_forecast`. This is red against the heartbeat implementation currently on the
branch, which selects A and serves A's stale `_pooled` row.

**Scope, deliberately narrow:** the combined block only. Per-model entries freeze the same way —
`_sapphire_entries` uses the same unconstrained fetch (`services/forecast_lab/snapshot.py:338-341`)
and a stale row can even win `is_primary` (`:344-346`) — but that is a **pre-existing** hazard this
plan does not create, and widening T7 to a uniform snapshot-freshness policy is exactly the
accretion round 5 cut. Recorded below; it needs its own plan.

### T6 — the contract text and the consumer notice

- `docs/spec/forecast-lab-snapshot.md:123-126` is normative and says "latest available". T7 changes
  it to the selected publication cycle: a **behavioural contract change** with no schema bump.
- A note to SAPPHIRE-flow-map, **before the deploy**: `_pooled` will read `no_combined_forecast` for
  2009 and 2091 until Plan 224 lands, and that is deliberate.
- Correct the published diagnosis where round 1 found it wrong: the temporal-jump QC rule would
  **not** have caught this sawtooth (`max_rate: 500.0` against a ~330 m³/s jump). *(Already done in
  the artifact; recorded here so the correction is tracked.)*

## ⛔ Per-run scope — THIS run is a FIXER pass, not a fresh build

**The implementation already exists** on `feat/plan-222-intersection-pooling` (commits `e2689c89`,
`0be3148c`; version 0.1.848; full unit suite green at 4988 passed). `/implement` escalated after two
rounds, stalled at 1 blocker + 2 majors. **Do not rebuild T3 or T7 from scratch and do not revert
those commits.** Fix exactly these three, on top of what is there:

1. 🔴 **BLOCKER — T7's marker.** Replace the `FORECAST_FRESHNESS` heartbeat lookup
   (`services/forecast_lab/db_sources.py:160-176`) with the forecast-row marker specified in T7
   above. The heartbeat is best-effort (`flows/run_forecast_cycle.py:775-800`), so a swallowed
   append leaves the export pinned to the previous cycle and serving its stale `_pooled` row.
2. **MAJOR — interior ragged spacing.** The persistence floor counts timestamps but does not check
   spacing (`services/forecast_combination.py:322`), so an interior drop persists an irregular grid
   with one misleading `native_step_seconds`. Enforce D6's uniform-spacing rule. The existing ragged
   test drops only the FINAL timestamp and therefore misses this — add an interior case
   (t1..t4 with one contributor incomplete only at t2).
3. **MAJOR — duplicate member rows.** The completeness gate compares only `n_unique(member_id)`
   (`services/forecast_combination.py:95-103`), so a duplicate `(valid_time, member_id)` row passes
   and `_ensemble_points()` overweights that member. Enforce D2's both-ways count. Add a duplicate-row
   locking test.

Each of the three needs a locking test proven RED against the code currently on the branch. Nothing
else in this plan is reopened.

## Deployment

- The mini is 56 commits behind and needs a rebuild, not a pull.
- 🪤 `git pull` on the mini replaces the inode behind single-file bind mounts; restart
  worker/ingest/api afterwards or `load_config()` raises `FileNotFoundError`.
- **Verify the disappearance in the EXPORT, not the cycle.** A cycle writing no `_pooled` row while
  the export still renders one is precisely the T7 failure mode, and looks identical to success in
  the flow logs.

## Cut in round 5 — verified findings, deferred not discarded

Round 5 classified each task and returned **TOO BIG BY 5 TASKS**. These were cut. All were verified
against the code across rounds 1-4; none is a false finding.

| Cut | Evidence | Why deferred | Where it goes |
|---|---|---|---|
| **T4** — intersection in the alert pooling path | `services/alert_strategy.py:99-143` | Dead path here **three ways over**: `enable_forecast_alerts = false` (`config.toml:36`), `alert_model_strategy = "primary"` (`:40`), and zero `station_thresholds` live | ⚠️ **Reverses owner-settled D4** ("all three call sites"). Needs its own plan before alerting is enabled |
| **T4b** — unevaluable parameter must not resolve an alert | `services/alert_checker.py:141-143, 346-354` | Existed only because T4 created the hazard. Cutting T4 removes it. **But the pre-existing form is real** — a missing or filtered danger level already yields no results while the parameter is marked evaluated | Own plan, with T4 |
| **T3b** — pooled member-id collision | `services/forecast_combination.py:67-70` vs `adapters/forecast_interface.py:284` | **A live defect** — a 1-based ensemble pooled after a 0-based one loses a member. The alert path does it correctly (`services/alert_strategy.py:113-128`). But intersection can be computed per contributor before concatenation, so the sawtooth fix does not need it | Own plan |
| **T5** — per-parameter `source_model_ids` | `services/forecast_combination.py:237` | A live inaccuracy — one list reused for every parameter, naming non-contributors. Published by the REST API too (`api/routes/api_forecasts.py:75-76`). Does not cause or sustain the grid defect | Own plan |
| **T0** — measure the grids | — | The diagnosis already reproduced the defect and identified the affected stations as 2 of 2 | Dropped |
| **BMA** from D2 and T3 | `services/forecast_combination.py:92-186` | `combine_ensembles_bma` takes the union too, and its member allocation is model-global rather than per-parameter (`:127-143`). Latent: BMA is not deployed | Own plan |
| **Most of T6** | — | Logging taxonomy, `touchpoint-maps.md`, `types-and-protocols.md`, the REST notice tied to T5 | With their tasks |

## Non-goals

- Anchoring any model's `valid_time` — **Plan 224**, which also owns the two defects underneath it
  (the hindcast lookback taking rows not daily buckets, and skill's unresampled observation join).
- Per-point provenance in the v2 contract; QC over combined forecasts; persisting `time_step`;
  backfill.
- A uniform snapshot-wide freshness policy across per-model entries (see T7's scope note).

## Exit gates

- `uv run ruff format` / `uv run ruff check --fix` clean; `uv run pyright` no new errors.
- `uv run pytest tests/unit` — zero failures. The bar is zero.
- Every locking test in T3 and T7 demonstrated **failing against the pre-change code** and passing
  after. Rounds 2, 3 and 4 each caught a test that was claimed red and was green; this gate is why.
- The existing identical-grid test in `TestCombineEnsemblesPooled` still passes, unchanged.
- Version bumped in the commit; hold at PR.

## Round history

Five independent `scripts/codex-review.sh` passes (`gpt-5.6-sol`, read-only), 2026-08-31. Every
blocker and load-bearing major was **verified against the cited code before folding**; three
findings were **rejected** on that basis (two claimed citation drifts, one mis-cited skip-reason
line).

| Round | Verdict | What it changed |
|---|---|---|
| 1 | NEEDS_CHANGES (4 blockers) | Alert-resolution hazard; the one-timestamp `3600` trap; the QC-rule claim corrected to the consumer |
| 2 | NEEDS_CHANGES (3 blockers) | Proved round 1's own repair wrong; surfaced the hindcast-lookback and skill-join defects → **anchoring split to Plan 224** |
| 3 | NEEDS_CHANGES (1 blocker) | Proved the re-cut's premise wrong: absence is invisible without T7 |
| 4 | NEEDS_CHANGES (1 blocker) | T7's per-model exemption was false; its predicate cannot be a timestamp comparison |
| 5 | **TOO BIG BY 5 TASKS** | This cut |

Rounds 1-2 each invalidated the previous round's repair. Rounds 3-5 did not — the pooling design
underneath has survived three consecutive rounds intact, and round 4 explicitly verified the T3
test set as red against current code.

**Round 6 has not run.** T7's publication-cycle predicate is the one substantive item never
reviewed in the form written here.

## Fixer round — post-implementation diff review

Committed diff (`e2689c89`) reviewed against T3/T7 as implemented. 3 majors, 3 minors; all resolved:

- **T3 ragged members** — `combine_ensembles_pooled`'s `valid_time` intersection checked presence
  only, so a contributor with a full member set at some timestamps and a short one at others still
  passed the intersection, reproducing the sawtooth at the ragged timestamp. Fixed: each
  contributor's per-`valid_time` member count is now checked against its own total member count
  first; only *complete* timestamps enter the intersection. Locking test
  `test_ragged_contributor_is_dropped_not_pooled_incomplete` proved red against the pre-fix code.
- **T3 three-contributor coverage** — every T3 test used exactly two eligible models, so an
  implementation intersecting only its first two contributors (the actual incident had three) would
  have passed unnoticed. Added `test_three_contributors_intersect_all_three_not_just_the_first_two`;
  proved red by transiently truncating the intersection loop to `eligible[:2]`.
- **T7 "latest heartbeat"** — `FakePipelineHealthStore.fetch_recent()` returned insertion order
  (`matches[-limit:]`), not PostgreSQL's `checked_at DESC`; every T7 test used exactly one heartbeat,
  so the fake's divergence from production ordering was never exercised. Fixed the fake to sort by
  `checked_at` descending, and added tests with multiple out-of-order heartbeats
  (`TestFetchLatestPublicationCycleTime` in `test_db_sources.py`;
  `test_newer_heartbeat_with_no_row_beats_an_older_heartbeat_with_a_row` and
  `test_newer_row_selected_when_both_cycles_have_one` in `test_snapshot.py`). Both proved red against
  a `records[-1]`-style production mutation, and the snapshot tests separately proved red against the
  old (unsorted) fake with correct production code — the fake fix was independently necessary.
- **Minor: unconditional health-store query** — `build_snapshot()` resolved the publication cycle for
  every strategy, including `PRIMARY`/`CONSENSUS`, which never fetch a combined row. A pipeline-
  health-store failure there would have turned into a snapshot-wide 500 for an unrelated reason.
  Fixed to resolve the cycle only for `POOLED`/`BMA`; locking test
  `test_primary_strategy_never_queries_pipeline_health_store` proved red against the unconditional
  call.
- **Minor: stale skip-reason text** — both `run_forecast_cycle.py` call sites still logged
  `reason="fewer than 2 combinable models"` on a skip, even though the empty-intersection and
  single-timestamp paths (T3/D6) can now skip with `n_models >= 2`. Changed to the generic
  `reason="no_combined_forecast"` at both sites (mechanical, no locking test needed).
- **Minor: recovery-replay documentation gap** — accepted as correct behaviour (consistent with D7),
  not a code change. `docs/spec/forecast-lab-snapshot.md` now has an explicit operator note: a manual
  recovery replay's combined forecast will not surface in the export until the next *scheduled*
  cycle's heartbeat lands, because `FORECAST_FRESHNESS` heartbeats are scheduled-cycle-only.
