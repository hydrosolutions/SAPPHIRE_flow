---
status: DRAFT
created: 2026-08-31
plan: 222
title: A combined forecast must stand on one grid — calendar-day anchoring, then intersection pooling
scope: Anchor the daily models' valid_times to the daily observation bucket grid they already predict on (Phase 1), then make every pooling site publish only where all contributors are present (Phase 2). No contract change, no backfill, no change to the forecast cycle's structure.
depends_on: [204]
blocks: []
source: 2026-08-31 — SAPPHIRE-flow-map reported an alternating `_pooled` median at station 2091; diagnosed as a disjoint-grid pooling artifact and reproduced through the production functions
---

# Plan 222 — the pooled sawtooth

## Status

**DRAFT** — awaiting owner READY. All four design decisions are settled below (owner,
2026-08-31). D1 was the last open fork and the owner chose **(a) anchor to the last observed
daily bucket**; T0 still measures the staleness distribution, but now to size the consequence
rather than to make the choice.

The diagnosis this plan acts on is published to the map consumer at
`https://claude.ai/code/artifact/7416ea1c-f884-49ae-bc37-7ce4bcc542ad`. The consumer has asked
that no published value change before the semantics are agreed — Phase 2 is that agreement made
executable, and **T6 tells them what became observable**.

## ⛔ Proportionality

**This plan changes production forecast behaviour.** That is unavoidable — the reported defect
*is* a behaviour — but it bounds what belongs here. Two phases, six tasks, no new abstraction.

**In scope for findings:** the D1–D5 contract shape is wrong or ambiguous; a cited fact is false;
an acceptance criterion does not lock its behaviour; a locking test would pass against the buggy
code; the change breaks a consumer we have not named.

**Explicitly out of scope — do not propose, and reject if proposed:**

- The additive per-point provenance fields (`contributor_model_ids`, `contributor_member_count`).
  The map team has not agreed the semantics yet, and the `extra="forbid"` question
  (`api/forecast_lab_schemas.py:91-95`) is unanswered. That is a later plan.
- Running `ForecastOutputQualityChecker` on combined forecasts. Real gap, recorded in Non-goals,
  separate plan.
- Replacing the store's derive-`time_step`-by-differencing (`store/forecast_store.py:316-321`).
  Under this plan it yields the right answer again; hardening it is separate.
- Backfill or recomputation of stored forecasts.
- Any change to `NwpRegression` / `NwpRainfallRunoff` timestamps. They are already correct and are
  the grid everything else moves onto.
- Smoothing, interpolation or resampling of any published series. The consumer forbade it and so
  do we.

## The defect — reproduced through the production functions

`combine_ensembles_pooled()` concatenates member frames with offset member ids and never joins on
`valid_time` (`services/forecast_combination.py:39-89`). The pooled ensemble therefore spans the
**union** of its sources' grids, and `_ensemble_points()` summarises each timestamp over whichever
rows carry it (`services/forecast_lab/snapshot.py:245-262`).

Two model families anchor differently:

| Model | valid_time construction | Grid |
|---|---|---|
| `NwpRegression`, `NwpRainfallRunoff` | adopts the delivered forcing timestamps (`models/nwp_regression.py:472`), which are UTC-midnight buckets (`services/operational_inputs.py:147`) | `00:00:00Z` |
| `LinearRegressionDaily` | `issue_time + (i+1) * time_step` (`models/linear_regression_daily.py:164-166`) | `18:00:01.851153Z` |
| `PersistenceFallback` | same construction (`models/persistence_fallback.py:92`) | issue-anchored |
| `ClimatologyFallback` | same construction (`models/climatology_fallback.py:145`) | issue-anchored |

`issue_time` is the raw wall clock: a scheduled cycle passes `clock()` through unchanged
(`flows/run_forecast_cycle.py:684-690`).

Running the real `combine_ensembles_pooled()` over three ensembles on those two grids reproduces
every published symptom on the first attempt:

```
member_count (-> ensemble_size): 92
forecast_horizon_steps: 10          # 5 + 5 union, not 5

  2026-08-31 00:00:00+00:00         n_members= 42   median=   854.5
  2026-08-31 18:00:01.851153+00:00  n_members= 50   median=   544.5
  2026-09-01 00:00:00+00:00         n_members= 42   median=   854.5
  2026-09-01 18:00:01.851153+00:00  n_members= 50   median=   544.5

native_step_seconds on read: 64801
horizon_end                : 2026-09-04 18:00:01.851153+00:00
```

`64801` is `18h 0m 1.851s` truncated — the store derives a forecast's step by differencing the
first two distinct `valid_time` values (`store/forecast_store.py:316-321`); step is never
persisted.

## The finding that sets the plan's shape

**`LinearRegressionDaily`'s timestamps are wrong independently of pooling.** It trains and predicts
on midnight-bucketed daily discharge — `group_by_dynamic("timestamp", every="1d")`
(`services/training_data.py:106-110`) feeding `past_targets`
(`services/operational_inputs.py:551`). Its step-*k* output is *the k-th daily mean after the last
observed day*: a calendar-day quantity, labelled with a wall-clock instant.

**Intersection alone is therefore not enough.** With `00:00:00Z` against `18:00:01.851153Z` the
intersection is empty, so intersection-only would take the combined forecast dark for every station
that has both families. Anchoring is what makes the invariant satisfiable; intersection is what
keeps it honest afterwards. Both, in that order, in one plan.

**Anchoring is a no-op everywhere except the operational path.** Hindcast issue times come from
`_issue_times(period_start, ...)` walking by `time_step` from a `period_start` that defaults to
midnight (`services/hindcast.py:101-111`, `flows/run_hindcast.py:166`), so `issue_time + k*step`
is *already* calendar midnight there. Skill scoring joins forecasts to observations by **exact
timestamp** (`services/skill/service.py:92-95`) — which is precisely why the operational forecasts
have never been scoreable against observations, and why anchoring moves the operational path onto
the semantics hindcast and training already assume. This is the fact that de-risks Phase 1.

## Impact

- **Published**: 2 of 2 stations with a combined forecast (2009, 2091). Every station acquires it
  as it crosses two non-fallback models — below that, `build_combined_forecasts()` returns an empty
  list (`services/forecast_combination.py:202-204`).
- **Danger classification**: the map classifies on the median peak, and the `00:00` points are
  systematically the high ones, so the peak lands on a mixed-pool timestamp every time. Marker
  colour is currently set by which models happened to share a timestamp.
- **Alerting**: the same unaligned concatenation feeds exceedance (`services/alert_strategy.py:99-143`),
  taken as the maximum over lead times of a per-timestamp member fraction
  (`services/alert_strategy.py:33-51`), over a varying denominator. The adequacy gate compares the
  **pool total** against `min_operational_ensemble_size` (`services/alert_checker.py:181`), so a
  point standing on 42 members is checked as though it stood on 92. No alert has fired — the
  deployment holds zero thresholds — but the defect is upstream of thresholds, not downstream.
- **Skill**: `combined_skill.py:107` pools hindcasts through the same function, so any CRPSS on
  `_pooled` would score the artifact. Inherits the Phase 2 fix at no cost.
- **Never caught because**: combined rows are stored with `qc_status=RAW` and never run through
  `ForecastOutputQualityChecker` (`flows/run_forecast_cycle.py:2872-2886`), and the temporal-jump
  rule walks per-timestamp medians and flags exactly this shape
  (`services/forecast_qc.py:184-196`). The one check that would have caught it is the one the
  pooled row skips.
- **Never tested because**: every fixture in `TestCombineEnsemblesPooled` comes from one helper at
  `n_steps=10`, so all models always share a grid (`tests/unit/services/test_forecast_combination.py:63-70`).

## Decisions

### D1 — what the daily models anchor to (owner-settled: (a))

**Chosen: (a) anchor to the last observed daily bucket.** `valid_time[k] = last_bucket + k·step`,
where `last_bucket` is the final `timestamp` in `past_targets`. Midnight by construction, because
`past_targets` is midnight-bucketed.

The rejected alternative was **(b) anchor to the issue day**: `midnight(issue_date) + k·step`.

| | (a) last observed bucket | (b) issue day |
|---|---|---|
| Semantically true under stale observations | yes | no — claims day *D+1* for a value rolled forward from a 3-day-old reading |
| Coincides with the NWP grid | only when observations are fresh | always |
| Combined horizon under staleness | shortens honestly (intersection handles it) | stays 5 days, silently misaligned |
| Matches hindcast/skill semantics | yes | only when fresh |

**Why (a) is recommended.** The point of this plan is to stop publishing things that are not true.
Under (b) a staleness of two days relabels every step by two days, and because skill scoring joins
on exact timestamps it would score the wrong pairs — invisibly, since hindcasts never run stale.
(a) makes the operational path mean what the hindcast path already means. The cost is that the
combined horizon shortens when observations lag, which the intersection rule expresses correctly
and which `observation_staleness_hours` already records on the forecast row.

**Consequence the implementer must not design around.** Under (a) the combined horizon is
`5 - staleness_days` where the NWP grid starts at `issue_date + 1`. At two days' staleness the
combined forecast is three days, not five. That is the correct behaviour and T3's intersection
expresses it; it is **not** a reason to pad, extrapolate or fall back to (b). T0 measures how often
this bites so the deploy is not surprised by it.

### D2 — pooling semantics (owner-settled)

**A combined forecast for parameter P is published only if at least two models contribute members
for P, and only at the timestamps where every one of those contributing models has members.**

Precisely: the contributor set for P is the models holding a `MEMBERS` ensemble for P (the existing
`skip_non_members` filter at `forecast_combination.py:58-65` already defines this). If that set has
fewer than two members, P gets no combined forecast. Otherwise the published grid is the
**intersection** of the contributors' `valid_time` sets. An empty intersection yields no combined
forecast for P — not a narrower pool.

Rejected: *minimum contributor count* keeps publishing at a varying denominator, narrowing the
artifact without removing it. *Resampling onto a common grid* is silent interpolation of a forecast.

Note this also closes a second instance of the same defect: today a parameter carried by only one
model is published as `_pooled` regardless, because the `>= 2` gate at
`forecast_combination.py:202-204` counts **results**, not per-parameter contributors.

### D3 — `source_model_ids` becomes the actual contributor set (owner-settled)

Today it is `list(combinable_results.keys())` (`forecast_combination.py:237`) — the same list on
every parameter, naming models that contributed nothing to that one. It becomes the per-parameter
contributor set from D2.

**This is an observable change to a published field.** It can only shrink or stay equal, never
grow, and it moves from an overstatement to a fact. T6 tells the consumer before it ships.

### D4 — all three pooling sites (owner-settled)

`combine_ensembles_pooled`, `combine_ensembles_bma` (`forecast_combination.py:92-186`, identical
defect, not currently deployed but latent the moment BMA is switched on), and `_pool_ensembles`
(`services/alert_strategy.py:99-143`). One shared helper, three call sites — a forecast pooling on
the intersection while alerting pools on the union would be incoherent.

### D5 — the fallbacks are anchored too

`PersistenceFallback` and `ClimatologyFallback` never reach pooling: they emit `QUANTILES` and are
excluded from `combinable_results` (`services/run_station_forecast.py:128-131`). Their timestamps
are mislabelled in exactly the same way and they are published per-model in the export, so D1
applies to them. This is inclusion for uniformity of the invariant, not for pooling.

## Phase graph

```json
{
  "phases": [
    {"id": "phase-0", "tasks": ["T0"], "parallel": false},
    {"id": "phase-1", "tasks": ["T1", "T2"], "parallel": true, "depends_on": ["phase-0"]},
    {"id": "phase-2", "tasks": ["T3", "T4", "T5"], "parallel": false, "depends_on": ["phase-1"]},
    {"id": "phase-3", "tasks": ["T6"], "parallel": false, "depends_on": ["phase-2"]}
  ]
}
```

## Tasks

### T0 — measure the real grids before changing any model (read-only)

Against the live mini DB, for every station with a stored forecast in the last 7 cycles: the
distinct `valid_time` grid per `model_id`, the observation staleness at issue, and the size of the
D2 intersection had it been applied. **No writes, no deploy, no interruption of a running flow.**

This exists because the repo has been wrong four times in one day about operational numbers by
reasoning instead of measuring. Phase 1 changes production model output; it does not start until
the grids are measured rather than derived from code.

**Exit:** a recorded table of per-model grids and the intersection size per station, plus the
observed distribution of `last_bucket` relative to `issue_date` — which, under the settled D1(a),
sizes how much shorter the combined horizon becomes in practice.

### T1 — anchor the daily models to the daily bucket grid

`LinearRegressionDaily`, `PersistenceFallback`, `ClimatologyFallback`: replace
`inputs.issue_time + (step+1) * inputs.time_step` with the D1 construction.

**Locking test (red first):** a `predict()` at an issue time of `18:00:01.851153` with
midnight-bucketed `past_targets` returns `valid_time`s that are all exactly midnight. Against
current code this fails on the first element, and it must — a test that passes both before and
after locks nothing.

**Constraint:** `ClimatologyFallback` derives `doy` from `valid_time`
(`models/climatology_fallback.py:146`), so anchoring shifts which day-of-year row is selected.
That is a correction, not a regression — but it must be asserted explicitly, not left implicit.

**Must not change:** `NwpRegression` / `NwpRainfallRunoff` timestamps; the horizon step count; any
`ForecastEnsemble` field other than the `valid_time` column.

### T2 — snap the scheduled cycle's issue time

`_resolve_cycle_time` (`flows/run_forecast_cycle.py:684-690`) returns `clock()` verbatim for a
scheduled run. Round to the nearest hour.

`issued_at` and `horizon_end` are both published, and under D1(a) the sub-second no longer reaches
`valid_time` — but it still reaches `issued_at`, which the consumer named. Rounding is safe because
every window helper already anchors on `.date()` (`models/nwp_regression.py:618`,
`services/operational_inputs.py:228`).

**Locking test:** `17:59:59.9` and `18:00:01.85` both resolve to `18:00:00`. The first case is the
one that matters — a naive floor sends it to `17:00` and shifts the whole cycle an hour early.

**Must not change:** an explicit `--cycle-time` argument, which is already exact and must pass
through untouched.

### T3 — intersection in `combine_ensembles_pooled` and `combine_ensembles_bma`

One shared helper computing the D2 contributor set and intersection; both functions call it. Return
no entry for a parameter with fewer than two contributors or an empty intersection.

**Locking tests (red first), each failing against current code:**
1. Two models on disjoint grids → the parameter is absent from the result. *(Today: a union
   ensemble with an alternating member count.)*
2. Two models on partially overlapping grids → exactly the overlapping timestamps, and every
   timestamp carries the full member count. *(Today: the union.)*
3. A parameter carried by only one model → absent. *(Today: published as `_pooled`.)*
4. Identical grids → byte-identical to today's output. **This is the regression guard**; the common
   case must not move.

### T4 — intersection in the alert pooling path

Same helper in `_pool_ensembles` (`services/alert_strategy.py:99-143`).

**Locking test:** `PooledEnsembleStrategy.evaluate()` over two models on disjoint grids does not
produce an exceedance drawn from a single-model timestamp. Assert the exceedance denominator, not
just the probability — the probability can coincide by accident.

**Open for the implementer to answer, not to design around:** `_ensemble_size_adequate` sums
`member_count` across models (`services/alert_checker.py:181`). After T4 the pooled ensemble is
rectangular, so the sum equals the per-point count and the gate becomes correct without being
touched. Confirm that with a test rather than by reading it.

### T5 — `source_model_ids` reports actual contributors

Per D3. `build_combined_forecasts` currently computes one list for all parameters
(`forecast_combination.py:237`); it becomes per-parameter, from the contributor set T3 already
computes.

**Locking test:** a model contributing to `discharge` but not to a second parameter appears in the
first forecast's `source_model_ids` and not the second's.

### T6 — docs, and tell the consumer

- `docs/spec/forecast-lab-snapshot.md`: state the pooling invariant — a combined forecast stands on
  one grid, every point carries every contributor, `ensemble_size` is the count behind every point.
- `docs/touchpoint-maps.md`: the combination subsystem's must-not-change contract.
- A short note to SAPPHIRE-flow-map covering: the invariant now enforced; that `source_model_ids`
  may shrink (D3); that a combined forecast may be **absent** where one was previously published,
  and that absence is the correct signal; and that `native_step_seconds`, `ensemble_size` and
  `horizon_end` are correct again as a consequence rather than by direct fix.

## Deployment

Changes production forecast behaviour, so it does not ride along with anything else.

- The mini is **56 commits behind** and needs a rebuild, not a pull.
- 🪤 `git pull` on the mini replaces the inode behind single-file bind mounts; restart
  worker/ingest/api after pulling or `load_config()` raises `FileNotFoundError` against a host file
  that looks fine.
- **Watch the first forecast cycle after deploy.** The success criterion is that 2009 and 2091
  publish a combined forecast on a single grid with a constant member count — not merely that the
  cycle completes.
- D1(a) shortens the combined horizon by one day per day of observation staleness. That is correct
  behaviour, and T0 must have quantified it **before** deploy rather than it being diagnosed after.

## Non-goals

- Per-point provenance in the v2 contract (blocked on the consumer, and on the `extra="forbid"`
  question).
- Running forecast QC over combined forecasts — a real gap, recorded here, separately planned.
- Persisting `time_step` on the forecast row instead of deriving it on read.
- Backfilling or recomputing stored forecasts.
- The Flow Map's OPERATIONAL mode, thresholds, or CRPSS. Gated elsewhere on the Plan 111 G1 licence.

## Exit gates

- `uv run ruff format` / `uv run ruff check --fix` clean.
- `uv run pyright` — no new errors against the ratchet.
- `uv run pytest tests/unit` — zero failures. The bar is zero; any failure is real.
- Every locking test in T1–T5 demonstrated **failing against the pre-change code** and passing
  after. A locking test that passes both ways is a defect in the test, not evidence.
- T3 test 4 (identical grids → unchanged output) passes, proving the common case did not move.
- Version bumped in the commit; hold at PR.
