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

**DRAFT** — awaiting owner READY. **Round 1 of independent Codex review returned NEEDS_CHANGES
(4 blockers, 8 majors); every blocker and every load-bearing major was verified against the cited
code and folded.** See "Round 1 — what the review changed" at the foot of this document.

The owner chose D1 **(a) anchor to the last observed day**. The review proved the *construction*
originally specified for (a) was broken — it trusted `past_targets` to be pre-bucketed, which is
false in hindcast — so (a) now anchors by **truncation**, D1(a′). The owner's choice stands; the
mechanism was repaired.

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
first two distinct `valid_time` values (`store/forecast_store.py:316-321`). Step is not persisted
on the forecast row at all: `time_step` appears in `db/metadata.py` only on `model_assignments`
and `group_model_assignments` (`db/metadata.py:1008`, `db/metadata.py:1036`), never on the forecast
tables. *(Round 1: the derivation lines alone did not establish the absence; the metadata does.)*

## The finding that sets the plan's shape

**`LinearRegressionDaily`'s timestamps are wrong independently of pooling.** It trains and predicts
on midnight-bucketed daily discharge — `group_by_dynamic("timestamp", every="1d")`
(`services/training_data.py:106-110`) feeding `past_targets`
(`services/operational_inputs.py:551`). Its step-*k* output is *the k-th daily mean after the last
observed day*: a calendar-day quantity, labelled with a wall-clock instant.

**Intersection alone is therefore not enough.** With `00:00:00Z` against `18:00:01.851153Z` the
intersection is empty, so intersection-only would take the combined forecast dark on every
**scheduled** cycle. *(Round 1 correction: "every station, always" was overstated. A
midnight-**exact** issue time — an explicit `--cycle-time`, or a hindcast — keeps the issue-day NWP
bucket (`services/operational_inputs.py:228-236`) and overlaps four of the five daily-model
timestamps. No scheduled cycle is midnight-exact, because `clock()` carries the wall clock, so the
conclusion holds for production; the absolute did not.)* Anchoring is what makes the invariant satisfiable; intersection is what
keeps it honest afterwards. Both, in that order, in one plan.

**The anchor must be computed, not read** *(corrected in round 1 — the original claim here was
false and load-bearing)*. The first draft argued anchoring was a no-op in hindcast because hindcast
issue times are midnight (`services/hindcast.py:101-111`, `flows/run_hindcast.py:166`). That is
true of the *issue time* and irrelevant to the *anchor*: hindcast passes **raw, unresampled
observations** as `past_targets` (`services/hindcast.py:201`, `station_data = StationInputData(past_targets=obs_df, ...)`),
where the operational path resamples (`services/operational_inputs.py:551`). A hindcast
`past_targets` can therefore end at 23:50, and anchoring to that row verbatim would push
hindcast daily models **off** the midnight grid — breaking the exact-timestamp observation join
that skill scoring depends on (`services/skill/service.py:92-95`) and emptying the hindcast
pooling intersection.

D1(a′) resolves this by truncating to the daily bucket rather than trusting the input to be
bucketed. Under truncation both paths land on the same grid, and hindcast genuinely is unaffected —
but as a *consequence of the construction*, not as a property of the input.

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
  `ForecastOutputQualityChecker` (`flows/run_forecast_cycle.py:2872-2886`). *(Round 1 correction:
  the first draft called the temporal-jump rule "the one check that would have caught it". It would
  not have. The rule fires above `max_rate: 500.0` for daily discharge
  (`config/forecast_qc_rules.py:121-126`) and the observed jump is ~330 m³/s. The QC bypass is real
  and worth recording; the claim that it would have caught this defect was false, and has been
  corrected to the consumer as well.)*
- **Never tested because**: every fixture in `TestCombineEnsemblesPooled` comes from one helper at
  `n_steps=10`, so all models always share a grid (`tests/unit/services/test_forecast_combination.py:63-70`).

## Decisions

### D1 — what the daily models anchor to (owner-settled: (a); construction repaired in round 1)

**Chosen: (a′) anchor to the daily bucket CONTAINING the last observation.**
`valid_time[k] = truncate_to_step(last_observation_timestamp) + k·step`.

The truncation is the whole point and is **not** an implementation detail the implementer may
optimise away. Two verified facts make it mandatory:

- Hindcast `past_targets` are raw observations, never resampled (`services/hindcast.py:201`), so
  the final row is an arbitrary wall-clock reading.
- The operational resampler is **not** unconditional: it returns the frame unchanged when it holds
  fewer than two rows, or when the median gap already looks daily
  (`services/training_data.py:66-87`). A single degraded-data observation at 07:20 stays at 07:20.

Reading the final `past_targets` row verbatim — the first draft's construction — is therefore
wrong in both paths. Truncation is correct in both.

The rejected alternative was **(b) anchor to the issue day**: `midnight(issue_date) + k·step`.

| | (a) last observed bucket | (b) issue day |
|---|---|---|
| Semantically true under stale observations | yes | no — claims day *D+1* for a value rolled forward from a 3-day-old reading |
| Coincides with the NWP grid | only when observations are fresh | always |
| Combined horizon under staleness | shortens honestly (intersection handles it) | stays 5 days, silently misaligned |
| Matches hindcast/skill semantics | yes, **given truncation** | only when fresh |

**Why (a) is recommended.** The point of this plan is to stop publishing things that are not true.
Under (b) a staleness of two days relabels every step by two days, and because skill scoring joins
on exact timestamps it would score the wrong pairs — invisibly, since hindcasts never run stale.
(a) makes the operational path mean what the hindcast path already means. The cost is that the
combined horizon shortens when observations lag, which the intersection rule expresses correctly
and which `observation_staleness_hours` already records on the forecast row.

**Consequence the implementer must not design around.** Under (a′) the combined horizon is
`5 - staleness_days` where the NWP grid starts at `issue_date + 1`. At two days' staleness the
combined forecast is three days, not five. That is the correct behaviour and T3's intersection
expresses it; it is **not** a reason to pad, extrapolate or fall back to (b). T0 measures how often
this bites so the deploy is not surprised by it.

**Backdated points are a real hazard and T1 must exclude them** *(round 1, major)*. With stale
observations the early steps of `last_bucket + k·step` fall at or before `issued_at`. Nothing
downstream rejects them: alerting maximises exceedance over every point regardless of lead time
(`services/alert_strategy.py:39-51`), and the export renders their past calendar days and marks
them complete. **T1 therefore drops any step whose `valid_time <= issued_at`** rather than emitting
a forecast about the past. This shortens the horizon further under staleness, consistent with the
paragraph above.

**What D1 does NOT get for free** *(round 1, major)*. The first draft justified the shortened
horizon by saying `observation_staleness_hours` already records it on the forecast row. False for
the combined row: `build_combined_forecasts` hard-codes `observation_staleness_hours=None`
(`services/forecast_combination.py:254`). The shortened horizon is currently **unexplained** in the
published document. Populating it is deliberately out of scope here (it is a provenance change,
and provenance is the consumer's open contract question) — but T6 must say so plainly rather than
implying a field carries the explanation.

### D2 — pooling semantics (owner-settled)

**A combined forecast for parameter P is published only if at least two models contribute members
for P, and only at the timestamps where every one of those contributing models has members.**

Precisely: the contributor set for P is the models holding a `MEMBERS` ensemble for P (the existing
`skip_non_members` filter at `forecast_combination.py:58-65` already defines this). If that set has
fewer than two members, P gets no combined forecast. Otherwise the published grid is the
**intersection** of the contributors' `valid_time` sets. An empty intersection yields no combined
forecast for P — not a narrower pool.

Three qualifications, all from round 1:

- **BMA's contributor set is narrower** (major). `combine_ensembles_bma` drops zero- and
  negative-weight models *before* sampling (`forecast_combination.py:100-103`). A zero-weight model
  holding a `MEMBERS` ensemble must neither constrain the intersection nor appear in D3
  provenance. The contributor set is therefore "eligible after the strategy's own filter", which
  is the `skip_non_members` set for POOLED and the post-weight set for BMA.
- **Timestamps are necessary but not sufficient** (major). `ForecastEnsemble` validates only global
  unique member and timestamp counts (`types/ensemble.py:60-73`); ragged coverage — a contributor
  present at a timestamp with only some of its members — passes validation and would still vary the
  denominator. The invariant is **rectangularity**: every contributor supplies its full member set
  at every retained timestamp. Retain only timestamps satisfying that, and assert it directly.
- **An intersection of exactly one timestamp must not be published** (blocker). The store derives
  `time_step` by differencing the first two valid_times and falls back to `timedelta(hours=1)` when
  there is only one (`store/forecast_store.py:316-321`), so a single-timestamp combined forecast
  would publish `native_step_seconds: 3600` — a fresh instance of the defect this plan exists to
  remove. Require **at least two** retained timestamps. This keeps the store's derivation, which
  the Proportionality section forbids touching, out of scope and correct.

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
grow, and it moves from an overstatement to a fact. T6 tells the consumers before it ships —
**plural**: the Forecast Lab export *and* the standard forecast REST API, which publishes the same
field (`api/routes/api_forecasts.py:75-76`, `api/schemas.py:120`) and which round 1 caught the
first draft omitting.

### D4 — all three pooling sites (owner-settled)

`combine_ensembles_pooled`, `combine_ensembles_bma` (`forecast_combination.py:92-186`, identical
defect, not currently deployed but latent the moment BMA is switched on), and `_pool_ensembles`
(`services/alert_strategy.py:99-143`). One shared helper, three call sites — a forecast pooling on
the intersection while alerting pools on the union would be incoherent.

### D5 — the fallbacks are anchored too, and climatology's VALUES change with them

`PersistenceFallback` and `ClimatologyFallback` never reach pooling: they emit `QUANTILES` and are
excluded from `combinable_results` (`services/run_station_forecast.py:128-131`). Their timestamps
are mislabelled in exactly the same way and they are published per-model in the export, so D1
applies to them. This is inclusion for uniformity of the invariant, not for pooling.

**`ClimatologyFallback`'s `value` column necessarily changes too** *(round 1, blocker — the first
draft forbade exactly this in T1 while requiring it in D5)*. It selects each step's quantiles by
the day-of-year *of the proposed `valid_time`* (`models/climatology_fallback.py:145-148`), so
moving the timestamp moves the climatological day it reads. That is a **correction** — the model
was reading the day-of-year of a mislabelled instant — and T1 asserts it explicitly rather than
tolerating it silently. `PersistenceFallback` is unaffected in value: it repeats the last
observation and does not roll through elapsed days.

## Phase graph

```json
{
  "phases": [
    {"id": "phase-0", "tasks": ["T0"], "parallel": false},
    {"id": "phase-1", "tasks": ["T1"], "parallel": false, "depends_on": ["phase-0"]},
    {"id": "phase-2", "tasks": ["T3", "T4", "T4b", "T5"], "parallel": false, "depends_on": ["phase-1"]},
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

**Locking tests (red first).** *(Round 1: the original single assertion — "all timestamps are
midnight" — was too weak. It also passes issue-day anchoring and an off-by-one `last_bucket + step`,
so it would not have locked D1(a′) at all.)*

1. **Exact first timestamp**, not merely midnight: `predict()` at issue `18:00:01.851153` with a
   last observation on day *D* returns `valid_time[0] == midnight(D) + 1 day` exactly, and the full
   series at daily spacing.
2. **Truncation path**: a `past_targets` whose final row is `23:50` (the raw hindcast shape,
   `services/hindcast.py:201`) yields the same grid as one ending at midnight. This is the
   assertion that would have caught the first draft's construction.
3. **Single-row `past_targets`** at a non-midnight instant still yields a midnight grid — the
   resampler no-ops below two rows (`services/training_data.py:66-67`), so truncation is the only
   thing standing between a degraded feed and an off-grid forecast.
4. **Backdated steps dropped**: with observations stale by two days, no returned `valid_time` is
   `<= issued_at`.
5. **`ClimatologyFallback` day-of-year shift asserted explicitly** — the `value` column changes,
   and the test states the new expected day rather than tolerating whatever comes out.

**Must not change:** `NwpRegression` / `NwpRainfallRunoff` timestamps. *(The first draft also
forbade any change to a `ForecastEnsemble` field other than `valid_time`. Round 1 showed that
contradicts D5 — climatology's `value` must change — so the prohibition is now scoped to the NWP
models' timestamps, which is what it was actually protecting.)*

### T2 — CUT in round 1. Do not implement.

The first draft rounded the scheduled cycle's issue time to the nearest hour
(`flows/run_forecast_cycle.py:684-690`), justified by the claim that "every window helper already
anchors on `.date()`". **That claim is false and the task was unsafe.**

`_drop_backdated_and_cap` filters NWP buckets on `valid_time >= issue_time` — an exact comparison,
not a date one — and its docstring spells out the consequence
(`services/operational_inputs.py:225-236`): at a non-midnight issue the UTC-midnight issue-day
bucket sorts *before* `issue_time` and is correctly dropped, while at a midnight-**exact**
`issue_time` it sorts *at* `issue_time` and is kept. Rounding a `00:00:01.85` cycle back to
`00:00:00` therefore **admits a whole extra day of NWP forcing and shifts the NWP models' output
grid by one day** — violating this plan's own "must not change `NwpRegression` timestamps"
prohibition, in the one task that claimed to be cosmetic.

It is also unnecessary. Under D1(a′) the sub-second never reaches `valid_time`, which was the
defect. It remains on `issued_at`, where it is arguably **correct**: that is genuinely when the
cycle was issued. The consumer flagged it as a symptom of the grid problem, and the grid problem is
fixed without it.

Phase 1 is therefore T1 alone.

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
   case must not move. It is the one test in this plan **exempt from red-first** — it is supposed to
   pass before and after, and the exit gate is worded to match *(round 1: the first draft's
   universal red-first gate contradicted this test's purpose)*.
5. **Ragged coverage**: two models sharing every timestamp, but one supplying only half its members
   at one of them → that timestamp is excluded. Timestamp-set intersection alone does not catch
   this (`types/ensemble.py:60-73` validates only global counts), so a fixture of complete
   ensembles cannot prove the invariant.
6. **Single shared timestamp** → no combined forecast, per D2's two-timestamp floor.

### T4 — intersection in the alert pooling path

Same helper in `_pool_ensembles` (`services/alert_strategy.py:99-143`).

**Locking test:** `PooledEnsembleStrategy.evaluate()` over two models on disjoint grids returns no
exceedance results, with thresholds set so that current code *does* return one from a single-model
timestamp. *(Round 1: the first draft asked for an assertion on the exceedance denominator.
`ExceedanceResult` exposes no such field (`types/domain.py:218-228`), so that assertion could only
have been written by reaching into internals — which the repo's testing philosophy forbids. The
no-result assertion is observable from the public API and is red against current code.)*

**Open for the implementer to answer, not to design around:** `_ensemble_size_adequate` sums
`member_count` across models (`services/alert_checker.py:181`). After T4 the pooled ensemble is
rectangular, so the sum equals the per-point count and the gate becomes correct without being
touched. Confirm that with a test rather than by reading it.

### T4b — an unevaluable parameter must not resolve an active alert 🔴

**This task exists because T4 creates the hazard, and it is the most dangerous finding in the
review.** `check_station_alerts` adds a parameter to `evaluated_parameters` **before** calling
`strategy.evaluate()` (`services/alert_checker.py:141-143`). Resolution then fires for any active
alert whose configured parameters are all "evaluated" and not exceeded
(`services/alert_checker.py:346-354`).

Today an empty result from `evaluate()` cannot happen for a reason other than "not exceeded". After
T4 it can: an empty grid intersection returns no results. That would be read as **"evaluated, not
exceeded"** and would **resolve a live flood alert** on a station whose models simply failed to
agree on a grid. Silent, and in the wrong direction.

Distinguish "evaluated and not exceeded" from "could not be evaluated", and mark the parameter
evaluated only in the first case.

**Locking test (red first):** a station with an active forecast alert, whose pooled models are on
disjoint grids, still has that alert active after `check_station_alerts`. Against current code plus
T4 alone, the alert is resolved.

**Must not change:** resolution behaviour when the parameter genuinely was evaluated and genuinely
was not exceeded. That path is correct and is the common one.

### T5 — `source_model_ids` reports actual contributors

Per D3. `build_combined_forecasts` currently computes one list for all parameters
(`forecast_combination.py:237`); it becomes per-parameter, from the contributor set T3 already
computes.

**Locking test:** with **three** models — two carrying both parameters and one carrying only
`discharge` — the `discharge` forecast's `source_model_ids` names all three and the second
parameter's names two. *(Round 1: with only two models the second parameter would fall below D2's
two-contributor floor and produce no forecast at all, leaving nothing to assert. The fixture shape
is load-bearing, so it is stated here rather than left to the implementer.)*

### T6 — docs, observability, and tell the consumers

- `docs/spec/forecast-lab-snapshot.md`: state the pooling invariant — a combined forecast stands on
  one grid, every point carries every contributor, `ensemble_size` is the count behind every point.
- `docs/touchpoint-maps.md`: the combination subsystem's must-not-change contract.
- **Distinguish the skip reasons in the log** *(round 1, minor)*. `build_combined_forecasts`
  returns `[]` for "fewer than two combinable results", "fewer than two contributors for this
  parameter", and "empty or too-small intersection" alike, and the caller cannot tell them apart.
  The deployment watch below depends on telling them apart, so emit the actual reason.
- A short note to SAPPHIRE-flow-map covering: the invariant now enforced; that `source_model_ids`
  may shrink (D3); that a combined forecast may be **absent** where one was previously published,
  and that absence is the correct signal; that the horizon shortens with observation staleness and
  that **no published field currently explains why** (D1, `observation_staleness_hours` is `None`
  on combined rows); and that `native_step_seconds`, `ensemble_size` and `horizon_end` are correct
  again as a consequence rather than by direct fix.
- Correct the published diagnosis where round 1 found it wrong: the temporal-jump QC rule would
  **not** have caught this sawtooth at the default `max_rate` of 500 m³/s.
- The REST API consumer (`api/routes/api_forecasts.py:75-76`) is told about D3 as well.

## Deployment

Changes production forecast behaviour, so it does not ride along with anything else.

- The mini is **56 commits behind** and needs a rebuild, not a pull.
- 🪤 `git pull` on the mini replaces the inode behind single-file bind mounts; restart
  worker/ingest/api after pulling or `load_config()` raises `FileNotFoundError` against a host file
  that looks fine.
- **Watch the first forecast cycle after deploy.** The success criterion is that 2009 and 2091
  publish a combined forecast on a single grid with a constant member count — not merely that the
  cycle completes. If a station publishes nothing, the log must say **which** of D2's three
  conditions caused it (T6).
- **T4b is a safety gate, not a nicety.** Do not deploy T4 without it: the deployment currently
  holds zero thresholds, so the alert-resolution hazard is dormant, but the ordering makes it live
  the moment thresholds are written.
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
- Every locking test in T1, T3 (tests 1-3, 5, 6), T4, T4b and T5 demonstrated **failing against
  the pre-change code** and passing after. A locking test that passes both ways is a defect in the
  test, not evidence.
- **One stated exemption:** T3 test 4 (identical grids → unchanged output) is a regression guard and
  passes both before and after by design. It is the only exemption; any other test that passes
  before the change is a defect. *(Round 1: the first draft's universal red-first gate contradicted
  this test.)*
- Version bumped in the commit; hold at PR.

## Round 1 — what the independent review changed

`scripts/codex-review.sh`, `gpt-5.6-sol`, read-only sandbox, 2026-08-31. Verdict
**NEEDS_CHANGES**: 4 blockers, 8 majors, 6 minors. Every blocker and every load-bearing major was
**verified against the cited code before folding** — a finding is a claim, not a fact.

**Blockers, all confirmed:**

| Finding | Verified at | Resolution |
|---|---|---|
| Anchoring is *not* a hindcast no-op — hindcast `past_targets` are raw, unresampled observations | `services/hindcast.py:201` | D1 becomes (a′), anchoring by **truncation** rather than by reading the final row |
| `ClimatologyFallback`'s `value` must change with its `valid_time`, contradicting T1's own prohibition | `models/climatology_fallback.py:145-148` | D5 states it; T1 asserts the day-of-year shift; the prohibition rescoped to NWP timestamps |
| An empty intersection would **resolve a live flood alert** | `services/alert_checker.py:141-143, 346-354` | New task **T4b**, gated as a deploy blocker |
| A one-timestamp intersection publishes `native_step_seconds: 3600` | `store/forecast_store.py:316-321` | D2 requires ≥2 retained timestamps — keeps the forbidden store change out of scope |

**Majors folded:** T2 cut entirely (rounding the issue time shifts the NWP grid by a day —
`services/operational_inputs.py:225-236`); D1 can emit backdated points, so T1 now drops
`valid_time <= issued_at`; `observation_staleness_hours` is `None` on combined rows so it explains
nothing; BMA's contributor set is the post-weight set; rectangularity, not timestamp intersection,
is the real invariant; T1 and T4's original assertions locked nothing; the "dark for every station"
absolute was overstated.

**Minors folded:** the temporal-jump QC rule would **not** have caught this (`max_rate: 500.0`
against a ~330 m³/s jump) — corrected here *and* in the published consumer diagnosis; the REST API
is a second `source_model_ids` consumer; T5's fixture needs three models; T3 test 4 is exempt from
red-first; the `time_step`-never-persisted claim needed the metadata, not the read path.

**Not folded:** the reviewer's citation for the skip-reason finding
(`flows/run_forecast_cycle.py:2898-2903`) points at `store_forecast_failed`, not the combination
gate. The underlying observability gap is real and is folded into T6; the line reference is not.

**Net effect on scope:** one task removed (T2), one added (T4b), no change to the two-phase shape.
The plan is smaller and the invariant is sharper. Round 2 has not run.
