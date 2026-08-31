---
status: DRAFT
created: 2026-08-31
plan: 222
title: A combined forecast must stand on one grid — intersection pooling
scope: Pooling publishes only where every contributor is present, at every one of the three pooling call sites, plus the member-id remap that makes that guarantee real, plus the export freshness rule (T7) without which absence is invisible. Anchoring the daily models is SPLIT OUT to Plan 224. No SCHEMA change and no backfill — but T7 IS a behavioural contract change to the Forecast Lab export, and is documented as one.
depends_on: [204]
blocks: []
source: 2026-08-31 — SAPPHIRE-flow-map reported an alternating `_pooled` median at station 2091; diagnosed as a disjoint-grid pooling artifact and reproduced through the production functions
---

# Plan 222 — the pooled sawtooth

## Status

**DRAFT** — awaiting owner READY. **Two rounds of independent Codex review; round 2 returned
NEEDS_CHANGES (3 blockers, 8 majors) and did not converge.** The non-convergence was structural,
not local: the anchoring half of the original plan turned out to be entangled with three
pre-existing production defects it never set out to fix.

**The plan was therefore RE-CUT, not ground through a third round** (owner, 2026-08-31). This plan
is now the **intersection half only** — which round 2 largely validated. Anchoring, and the three
defects it depends on, move to **Plan 224**.

### ⚠️ Shipping this alone takes the combined forecast dark — ONLY IF T7 lands with it

With today's disjoint grids the intersection is empty, so the cycle stops **writing** new `_pooled`
rows for stations 2009 and 2091 until Plan 224 lands. Absence is the intended, accepted outcome:
the map's danger classification stops being driven by a pooling artifact, and the alert-resolution
hazard below is closed before thresholds are ever written.

**But not writing is not the same as not publishing** *(round 3, blocker)*. The export fetches the
combined forecast with `fetch_latest_forecast()`, which has **no age or cycle constraint**
(`services/forecast_lab/db_sources.py:145-152`). Plan 204 identified this hazard in its own
docstring — "would keep exporting a stale `_pooled` row forever"
(`services/forecast_lab/snapshot.py:419-425`) — and guarded only the case where the *strategy*
switches away from `pooled`. This plan creates the case it does not guard: the strategy stays
`pooled` while production stops, the gate passes, and **the last sawtooth is served indefinitely,
marked `complete` in daily alignment.**

That is worse than either outcome previously weighed — worse than a wrong-but-updating value, and
worse than absence. **T7 exists to make absence actually absent, and this plan must not ship
without it.**


## ⛔ Proportionality

**This plan changes production forecast behaviour.** That is unavoidable — the reported defect
*is* a behaviour — but it bounds what belongs here. **Eight tasks (T0, T3b, T3, T4, T4b, T5, T7,
T6), one shared intersection helper, and no change to any model's output.** *(Round 4, minor: this
said "five tasks, no new abstraction" and was never updated through the re-cut and three folds.)*

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
- **Any change to any model's `valid_time` construction.** That is Plan 224's entire subject. This
  plan takes the grids as it finds them and only decides what may be *combined* from them.
- The three pre-existing defects Plan 224 owns: the hindcast lookback taking rows rather than daily
  buckets, the skill observation join, and `LinearRegressionDaily`'s issue-anchored timestamps.
  **One exception, and it is in scope here**: the pooled member-id collision (T3b), because D2's
  rectangularity guarantee is unenforceable without it.
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

## Why this plan no longer contains the anchoring fix

The original plan paired anchoring with intersection because the intersection is empty under
today's grids, so intersection alone takes the product dark. That remains true. What changed is the
cost of the other half.

Two review rounds established that anchoring is **not** the cheap fix it appeared to be. It rests
on paths that are already broken:

- `LinearRegressionDaily._extract_discharge()` takes the **last seven rows**, not seven daily
  buckets (`models/linear_regression_daily.py:42-49` — the error text says "need 7 rows").
  Operationally `past_targets` is resampled so rows equal days; in hindcast it is not
  (`services/hindcast.py:201`), so the model runs on roughly seventy minutes of history while
  declaring a seven-day autoregressive window. Relabelling its output cannot fix that.
- Skill scoring joins forecasts to an **unresampled** observation lookup
  (`services/skill/service.py:338`), so a daily-mean forecast is scored against the instantaneous
  midnight reading regardless of what any timestamp says.

Both are live defects today and neither is this plan's to fix. Anchoring cannot be made correct
without them, so it moves to **Plan 224** with its own measurement and its own review. This plan
ships the invariant that makes the artifact impossible, and accepts the outage in the meantime.
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
  `_pooled` would score the artifact. Inherits T3 and T3b at no cost.
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

### D1 — MOVED to Plan 224

Anchoring the daily models is no longer part of this plan. Plan 224 owns it, together with the two
pre-existing defects it depends on. Nothing in D2-D4 below assumes any particular grid.

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
  **Rectangularity is unenforceable until T3b lands** — see there.
- **An intersection of exactly one timestamp must not be PERSISTED** (blocker). The store derives
  `time_step` by differencing the first two valid_times and falls back to `timedelta(hours=1)` when
  there is only one (`store/forecast_store.py:316-321`), so a single-timestamp combined forecast
  would publish `native_step_seconds: 3600` — a fresh instance of the defect this plan exists to
  remove. Require at least two retained timestamps **for the stored forecast only**; see D6 for why
  this must not bind alerting or skill.

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

### D5 — MOVED to Plan 224

The fallbacks emit `QUANTILES` and are excluded from `combinable_results`
(`services/run_station_forecast.py:128-131`), so they never reach pooling and nothing in this plan
touches them. *(Round 2 also showed the original D5 was wrong in both directions: persistence
values DO change with a retimed grid because spread scales with the step index
(`models/persistence_fallback.py:93`), and climatology values do NOT change when the date is
unchanged (`models/climatology_fallback.py:145-148`). Plan 224 inherits the corrected statement.)*

### D6 — the two-timestamp floor binds the STORED forecast only

*(Round 2, major.)* Round 1 added a floor of two retained timestamps because the store fabricates a
one-hour `time_step` for a single-timestamp forecast (`store/forecast_store.py:316-321`). That
reasoning is **specific to store readback** and was wrongly generalised across all three call
sites.

Alerting evaluates exceedance directly from an in-memory ensemble and never round-trips the store
(`services/alert_strategy.py:39-51`); a single future timestamp is a perfectly valid thing to
evaluate, and a model horizon of one is legal (`types/model.py:288`). The same holds for combined
skill (`services/skill/combined_skill.py:104`).

**The floor therefore applies to T3 (the persisted combined forecast) and NOT to T4 (alerting) or
the skill path.** Applying it everywhere would suppress valid evaluation.

**Where the floor LIVES is part of the decision** *(round 3, major)*. It must **not** sit inside the
shared combine function, because `combined_skill.py:104-110` and the alert path call through the
same code and would inherit it silently. It belongs at the persistence boundary — the caller that
stores the combined forecast. Two **positive** regression tests pin this: a one-timestamp pooled
ensemble still yields an alert exceedance evaluation, and still yields a combined skill hindcast.
Without them the floor migrates into the shared path on the next refactor and nothing notices.

## Phase graph

```json
{
  "phases": [
    {"id": "phase-0", "tasks": ["T0"], "parallel": false},
    {"id": "phase-1", "tasks": ["T3b"], "parallel": false, "depends_on": ["phase-0"]},
    {"id": "phase-2", "tasks": ["T3", "T4", "T4b", "T5", "T7"], "parallel": false, "depends_on": ["phase-1"]},
    {"id": "phase-3", "tasks": ["T6"], "parallel": false, "depends_on": ["phase-2"]}
  ]
}
```

## Tasks

### T0 — measure the real grids (read-only)

Against the live mini DB, for every station with a stored forecast in the last 7 cycles: the
distinct `valid_time` grid per `model_id`, and the size of the D2 intersection had it been applied.
**No writes, no deploy, no interruption of a running flow.**

This exists because the repo has been wrong four times in one day about operational numbers by
reasoning instead of measuring, and because this plan's accepted consequence — the combined
forecast going dark — must be a *measured* prediction before deploy, not an inferred one.

**Exit:** a recorded table of per-model grids and intersection size per station, and an explicit
statement of which stations will stop publishing a combined forecast. *(Round 3, major: this task
previously also carried an observation-staleness measurement. That belongs to Plan 224 and has been
removed — this plan changes no model's output, so staleness does not affect it.)*

### T3b — stop the pooled member-id collision 🔴

**A pre-existing production defect, found in round 2, and a prerequisite for D2's rectangularity.**

`combine_ensembles_pooled` adds a running offset to the **original** member ids
(`services/forecast_combination.py:67-70`) instead of remapping them to a contiguous range. Member
ids are not uniformly based: FI trajectories are 1-based
(`adapters/forecast_interface.py:284`, `range(1, num_samples + 1)`) while the native models are
0-based. Pool a 1-based ensemble followed by a 0-based one and the ranges overlap at the boundary —
two members collapse into one id, and the pool silently loses a member.

The alert path already does this correctly, remapping to sequential ids through a join
(`services/alert_strategy.py:113-128`). Two implementations of one operation; the pooled one is
wrong.

**`combine_ensembles_bma` does NOT need this fix** *(round 3, minor — round 2's fold said it did)*.
BMA assigns `global_offset + new_id` where `new_id` enumerates the sampled members
(`services/forecast_combination.py:161-169`), so its ids are already sequential and collision-free.
Rewriting it would broaden the change with no defect to fix. **T3b touches
`combine_ensembles_pooled` only.**

**Why it gates D2:** rectangularity is a statement about member counts per timestamp. While ids can
collide, a rectangular input can produce a non-rectangular pool, and no assertion downstream can
tell the difference.

**Locking tests (red first):**
1. Pool a 1-based 21-member ensemble with a 0-based 21-member ensemble; the result has 42 distinct
   member ids. Against current code it has 41.
2. **Identity, not just cardinality** *(round 3, major)*. Each output member id maps to exactly one
   input `(contributor, original_member_id)` across **every** timestamp. A time-varying permutation
   keeps the count at 42 while splicing different trajectories into each exported member series —
   which the REST API publishes per member (`api/routes/api_forecasts.py:34-38`) — so a count
   assertion alone does not lock the requirement this task states.

**Must not change:** the pooled values themselves, or member identity within a single contributor.

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
6. **Single shared timestamp** → no combined forecast, per D2's two-timestamp floor (T3 only — D6).
7. **BMA zero-weight model does not constrain the grid** *(round 2, major — round 1 stated the
   post-weight contributor rule but locked it nowhere)*. A zero-weight model on a *mismatching*
   grid must neither shrink the intersection nor appear in `source_model_ids`. Without this case a
   pre-weight intersection passes every other test.
8. **BMA's two-contributor minimum is post-weight** *(round 4, major)*. Two models pass the
   pre-gate count at `forecast_combination.py:202-204` but only **one** carries a positive weight
   (`forecast_combination.py:98-121`) → no combined forecast. Test 7 can be satisfied with two
   positive contributors and so never exercises the minimum; current code emits a one-contributor
   BMA forecast here. This locks the count, not the allocation — BMA's member allocation stays in
   Non-goals.

### T4 — intersection in the alert pooling path

Same helper in `_pool_ensembles` (`services/alert_strategy.py:99-143`).

**Locking test:** `PooledEnsembleStrategy.evaluate()` over two models on disjoint grids returns no
exceedance results, with thresholds set so that current code *does* return one from a single-model
timestamp. *(Round 1: the first draft asked for an assertion on the exceedance denominator.
`ExceedanceResult` exposes no such field (`types/domain.py:218-228`), so that assertion could only
have been written by reaching into internals — which the repo's testing philosophy forbids. The
no-result assertion is observable from the public API and is red against current code.)*

**Second locking test** *(round 2, major; assertion specified in round 3)*: two models sharing every
timestamp but with **ragged member coverage** at one of them. The assertion must be on the
**resulting exceedance probability**, computed against the known fixed denominator — not merely
that a result came back or that nothing raised. A test that only obtains a result passes the
current union implementation and locks nothing *(round 3, major)*.

**Scope note (D6):** T4 must **not** inherit T3's two-timestamp floor. Alerting evaluates from an
in-memory ensemble and a single future timestamp is valid to evaluate.

**⚠️ Accepted consequence, now documented rather than discovered** *(round 3, major)*: on disjoint
grids T4 produces no exceedance results at all, so **no new alert can be RAISED** either — T4b only
protects alerts that already exist. That is a forecast-alert blackout for any station whose pooled
models disagree on a grid. It is dormant twice over in this deployment — zero thresholds are
configured, and `alert_model_strategy = "primary"` (`config.toml:40`), so the pooled alert path is
not even reached — but it becomes live the moment either changes. **Plan 224 is the fix**; recording
it here is what keeps it from being rediscovered as an incident.

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

*(Round 3, minor: round 2's fold claimed an empty result was **new** under T4. It is not — a missing
or filtered danger level already yields no results while the parameter is marked evaluated,
`services/alert_checker.py:65-68`, `services/alert_strategy.py:224-228`. The per-level repair below
therefore fixes a **pre-existing** hazard as well as the one T4 introduces, which strengthens the
case for it.)* After T4 the empty-result case also arises from an empty grid intersection. That would be read as **"evaluated, not
exceeded"** and would **resolve a live flood alert** on a station whose models simply failed to
agree on a grid. Silent, and in the wrong direction.

Distinguish "evaluated and not exceeded" from "could not be evaluated", and record evaluation only
in the first case.

**Completeness is per (parameter, danger level), not per parameter** *(round 2, major)*. A
parameter can yield a result for one level and none for another — the effective danger-level list
is not necessarily every configured level (`services/alert_strategy.py:224`,
`services/alert_checker.py:284`), and resolution is decided per level
(`services/alert_checker.py:336-354`). Parameter-level state, which round 1 proposed, still permits
a DL2 alert to resolve on the strength of a DL1 result. Track the granularity resolution actually
uses.

**Locking tests (red first):**
1. A station with an active forecast alert whose pooled models are on disjoint grids still has that
   alert active after `check_station_alerts`. Choose thresholds so current code genuinely resolves
   it, or the test is not red *(round 2)*.
2. **The legitimate path still resolves** — a pooled station that IS evaluable and is NOT exceeded
   resolves its active alert, driven through `check_station_alerts()` rather than
   `_process_results()` directly, which is how the existing coverage reaches it
   (`tests/unit/services/test_alert_checker.py:731`). Without it, test 1 alone accepts a degenerate
   fix that simply never marks pooled parameters evaluated *(round 2, major)*.
   **This is a REGRESSION GUARD and is exempt from red-first** — current code already resolves in
   this situation, so it passes before and after *(round 3, major: round 2 mislabelled it as a
   red-first locking test, and the exit gate demanded it be red)*.
3. A parameter evaluable at one danger level and not another does not resolve the alert at the
   level it could not evaluate.

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

### T7 — an absent combined forecast must READ as absent 🔴 *(deploy blocker)*

**Round 3 blocker. This plan is unsafe to ship without it.**

`fetch_latest_forecast_for_model` delegates to `fetch_latest_forecast()` with no age or cycle
constraint (`services/forecast_lab/db_sources.py:145-152`). Plan 204 gated the *strategy* to stop a
stale `_pooled` row leaking after a `pooled → primary` switch
(`services/forecast_lab/snapshot.py:419-425`), but that gate does not fire when the strategy stays
`pooled` and the cycle simply stops producing rows — which is exactly what D2 causes.

Constrain the combined fetch so a row that is not from the current forecast cycle is treated as
absent, returning the existing `CombinedForecastUnavailableSchema` rather than a stale document.
This is additive within the v2 contract — the unavailable shape already exists and already carries
a `reason` — so **no `schema_version` change**, consistent with the consumer's four pinned literals.

**Locking test (red first):** with a stored `_pooled` row from an earlier cycle and no row from the
current one, the export renders the combined block **unavailable**. Against current code it renders
the stale row as `available`, and daily alignment marks those days `complete`.

**Must not change:** the per-model entries, which are fetched the same way but *are* being written
every cycle. Round 3 did not find them at risk, and widening this to every fetch is out of scope.

**Open for the implementer:** the exact freshness predicate — same-cycle equality against the
resolved cycle time, versus an age bound. Same-cycle is preferred as it needs no new tunable, but
the export runs asynchronously from the cycle and must not flap during a run.

### T6 — docs, observability, and tell the consumers

- `docs/spec/forecast-lab-snapshot.md`: state the pooling invariant — a combined forecast stands on
  one grid, every point carries every contributor, `ensemble_size` is the count behind every point.
- `docs/touchpoint-maps.md`: the combination subsystem's must-not-change contract.
- `docs/architecture-context.md:131` and `docs/spec/types-and-protocols.md:4209` *(round 2, minor)*
  — both still describe only the global two-model gate and an undifferentiated `source_model_ids`.
  `types-and-protocols.md` is authoritative for implementation, so leaving it stale would actively
  mislead the next change.
- **Distinguish the skip reasons in the log** *(round 1, minor)*. `build_combined_forecasts`
  returns `[]` for "fewer than two combinable results", "fewer than two contributors for this
  parameter", and "empty or too-small intersection" alike, and the caller cannot tell them apart.
  The deployment watch below depends on telling them apart, so emit the actual reason.
- A note to SAPPHIRE-flow-map, **sent before the deploy, not after**, covering: the invariant now
  enforced; that `_pooled` will read `no_combined_forecast` for 2009 and 2091 until Plan 224 lands,
  and that this is deliberate rather than an outage; that `source_model_ids` may shrink when it
  returns (D3); and that `native_step_seconds`, `ensemble_size` and `horizon_end` become correct as
  a consequence of the invariant rather than by direct fix.
- **`docs/spec/forecast-lab-snapshot.md:123-126`** — normative, currently "latest available".
  T7 changes it to "the selected publication cycle". Behavioural contract change; document it and
  tell the consumer explicitly *(round 4, major)*.
- Correct the published diagnosis where round 1 found it wrong: the temporal-jump QC rule would
  **not** have caught this sawtooth at the default `max_rate` of 500 m³/s.
- The REST API consumer (`api/routes/api_forecasts.py:75-76`) is told about D3 as well.

## Deployment

Changes production forecast behaviour, so it does not ride along with anything else.

- The mini is **56 commits behind** and needs a rebuild, not a pull.
- 🪤 `git pull` on the mini replaces the inode behind single-file bind mounts; restart
  worker/ingest/api after pulling or `load_config()` raises `FileNotFoundError` against a host file
  that looks fine.
- **Expect the combined forecast to DISAPPEAR for 2009 and 2091**, and verify it in the *export*,
  not merely in the cycle. A cycle that writes no `_pooled` row while the export still renders one
  is precisely the T7 failure mode, and it looks identical to success from the flow logs.
- **Watch the first forecast cycle after deploy.** Where a station publishes nothing, the log must
  say **which** of D2's conditions caused it (T6) — an unexplained absence is indistinguishable from
  a regression.
- **T4b is a safety gate, not a nicety.** Do not deploy T4 without it: the deployment currently
  holds zero thresholds, so the alert-resolution hazard is dormant, but the ordering makes it live
  the moment thresholds are written.
- Plan 224 is what restores the combined forecast. Until it lands the consumer sees
  `no_combined_forecast` — provided T7 shipped. *(Round 3, major: this bullet previously carried an
  observation-staleness consequence that belongs entirely to Plan 224. Removed.)*

## Non-goals

- Per-point provenance in the v2 contract (blocked on the consumer, and on the `extra="forbid"`
  question).
- Running forecast QC over combined forecasts — a real gap, recorded here, separately planned.
- Persisting `time_step` on the forecast row instead of deriving it on read.
- Backfilling or recomputing stored forecasts.
- **Anchoring any model's `valid_time`, and the three pre-existing defects it depends on** — Plan
  224. Named here so the split is explicit rather than an omission.
- **BMA's model-global member allocation** *(round 3, major — a fourth pre-existing defect)*.
  `combine_ensembles_bma` computes `counts` once per model and reuses it for every parameter
  (`services/forecast_combination.py:127-143`), so a positive-weight model carrying only
  `discharge` still consumes its allocation for `water_level`, leaving that forecast undersized
  against the documented 100 members (`docs/architecture-context.md:133`). Real, but BMA is not
  deployed and this is an allocation defect, not a grid defect. **Recorded here so it is not lost;
  it needs its own plan.**
- The Flow Map's OPERATIONAL mode, thresholds, or CRPSS. Gated elsewhere on the Plan 111 G1 licence.

## Exit gates

- `uv run ruff format` / `uv run ruff check --fix` clean.
- `uv run pyright` — no new errors against the ratchet.
- `uv run pytest tests/unit` — zero failures. The bar is zero; any failure is real.
- Every locking test in T3b, T3 (tests 1-3, 5, 6, 7), T4, T4b (tests 1 and 3), T5 and T7
  demonstrated **failing against the pre-change code** and passing after. A locking test that passes both ways is a defect in the
  test, not evidence.
- **Two stated exemptions**, both regression guards that pass before and after by design:
  T3 test 4 (identical, collision-free grids → unchanged output) and T4b test 2 (the legitimate
  resolution path). Any *other* test that passes before the change is a defect. *(Round 1 caught
  the first; round 3 caught the second, which round 2 had mislabelled as red-first.)*
- D6's two positive regression tests (one-timestamp alert evaluation, one-timestamp combined skill)
  also pass before and after — they exist to stop the floor migrating into the shared path.
- Version bumped in the commit; hold at PR.

## Round history

Two independent `scripts/codex-review.sh` passes (`gpt-5.6-sol`, read-only sandbox, 2026-08-31).
Every blocker and load-bearing major was **verified against the cited code before folding** — a
finding is a claim, not a fact — and two were rejected on that basis.

### Round 1 — NEEDS_CHANGES (4 blockers, 8 majors)

Findings that survive into this plan: the alert-resolution hazard (now T4b); the one-timestamp
`native_step_seconds: 3600` trap (now D2 + D6); BMA's post-weight contributor set; rectangularity
as the real invariant; the REST API as a second `source_model_ids` consumer
(`api/routes/api_forecasts.py:75-76`); and the correction that the temporal-jump QC rule would
**not** have caught this sawtooth (`max_rate: 500.0` against a ~330 m³/s jump) — corrected here and
in the published consumer diagnosis.

Findings that moved to Plan 224 with the anchoring work: the hindcast `past_targets` blocker, the
`ClimatologyFallback` value contradiction, backdated points, and `observation_staleness_hours`.

### Round 2 — NEEDS_CHANGES (3 blockers, 8 majors) → RE-CUT

Round 2 returned as many findings as round 1. It did not converge, and the reason was structural:
every surviving blocker sat in the anchoring half, on top of pre-existing defects.

**What it proved about the anchoring half — and why that half left this plan:**

- D1(a′), round 1's repair, did **not** work. `_extract_discharge()` takes the last seven *rows*,
  not seven daily buckets (`models/linear_regression_daily.py:42-49`); hindcast passes raw
  observations (`services/hindcast.py:201`), so the model runs on ~70 minutes of history.
  Relabelling cannot fix a lookback.
- Skill scoring joins to an unresampled observation lookup (`services/skill/service.py:338`), so
  D1's justification — that anchoring aligns operational with skill semantics — was false.
- T1 tests 2 and 4 passed current code, and an issue-day-anchored implementation passed tests 1-4,
  so the suite did not distinguish (a′) from (b) at all.
- The `valid_time <= issued_at` rule contradicted the NWP convention, which treats equality at
  midnight as future (`services/operational_inputs.py:225-236`).
- D5 was wrong in both directions on the fallbacks.

**What it changed in THIS half:** the member-id collision (now T3b, a live defect); the
two-timestamp floor wrongly generalised beyond the store (now D6); T4b's parameter-level
granularity being too coarse for level-specific resolution; T4b's test admitting a degenerate fix;
and the BMA and ragged-denominator qualifications being stated but locked by no test.

**Rejected after verification** — two findings that did not survive checking:

| Claim | Why rejected |
|---|---|
| Citations `run_forecast_cycle.py:684-690` and `:2872-2886` have drifted | Both correct in the working tree **and** at HEAD: `_resolve_cycle_time` is at 684-690 and returns `clock()`; `build_combined_forecasts` is at 2872 |
| Round 1's skip-reason citation `:2898-2903` | Points at `store_forecast_failed`, not the combination gate. The observability gap is real and is in T6; the reference was not |

### Round 3 — NEEDS_CHANGES (1 blocker, 7 majors, 4 minors)

Run against the re-cut plan, and asked specifically to trace what happens downstream when
`_pooled` stops existing. It found that **the premise of the re-cut was wrong**.

- **BLOCKER: shipping this alone does not take `_pooled` dark — it freezes it.** The export fetches
  with no age constraint (`services/forecast_lab/db_sources.py:145-152`), so the last sawtooth is
  served indefinitely and marked `complete`. Plan 204 named this hazard in its own docstring and
  guarded only the strategy-switch case (`services/forecast_lab/snapshot.py:419-425`); this plan
  creates the case it does not guard. **T7 is the answer, and this plan cannot ship without it.**
- Majors: the alert-coverage blackout (not merely resolution) is now documented at T4 rather than
  left to be discovered; D6's floor needed a *location* and positive tests, not just a scope
  statement; T3b's cardinality test did not lock member identity; T4's ragged test had no
  observable assertion; T4b test 2 was mislabelled red-first when it is a regression guard; and a
  stale anchoring consequence had survived the re-cut in T0 and Deployment.
- A **fourth pre-existing defect** surfaced and was deliberately NOT taken: BMA's model-global
  member allocation. Recorded in Non-goals.

**Trajectory across the three rounds** — blockers 4 → 3 → 1, and round 3's "checks out" list now
covers the core design: the member-id collision is real, T4b's per-level granularity is correct,
T3 tests 1-3/5/6 and T5 are red, and the Proportionality reconciliation holds. What keeps failing
is **test specification precision**, not design. Rounds 1 and 2 each invalidated the previous
round's repair; round 3 did not — it invalidated a *premise* instead, and the design underneath
survived.

### Round 4

Has not run. T7, the D6 relocation, and three rewritten assertions are new and unreviewed.
