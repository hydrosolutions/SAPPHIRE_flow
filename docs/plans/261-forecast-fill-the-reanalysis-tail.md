---
status: READY
created: 2026-09-08
revised: 2026-09-09
plan: 261
title: Operational forcing is one continuous series — assemble it from the freshest source at each step
scope: Assemble the operational PAST forcing leg from the freshest available source at each step — reanalysis where MeteoSwiss has published, the shortest-lead stored forecast beyond it — in the two operational assemblers only, IN MEMORY. NOTHING IS STORED. NOT a new data source, NOT a change to what is ingested, NOT a new store column, NOT gap-filling of interior holes, NOT a change to the future leg.
depends_on: [239]
blocks: [262]
source: 2026-09-08 — measured while validating Plan 239 T1b. Reframed 2026-09-09 on the owner's statement of standard operational practice; every number below re-measured against the mac mini the same day.
---

# Plan 261 — the operational forcing series, assembled from the freshest source at each step

## Status

**READY — set by the owner 2026-09-09**, after the framing correction and two independent
review passes (Claude + Codex) whose findings are folded below.

### What the rewrite changed, and why

The first revision called this a *tail fill*: a patch for a gap, scoped around an
inconvenience in MeteoSwiss's publication schedule. The owner rejected that framing:

> "what we typically do for operational forecasting is, we concatenate reanalysis data
> with stale forecasts and with operational forecasts to get a complete time series of
> past to future forcing for the model."

That is not the same plan with a better name. It changes three things:

1. **Status.** This is how an operational forcing series is *supposed* to be assembled,
   not a workaround. It therefore belongs ahead of the models that consume it, not
   behind them — hence `blocks: [262]`, which the first revision did not carry.
2. **Shape.** The unit of design is one continuous series per variable, from the start of
   the lookback to the end of the horizon, sourced from the freshest thing available at
   each step. "Reanalysis" and "forecast" are tiers within that series, not two separate
   subsystems meeting at an awkward join.
3. **Scope of the open questions.** All three of the first revision's open questions were
   really one question — *what does "freshest" mean* — and the owner has now answered it.
   They are closed below, not carried.

### Review round 2 (post-rewrite) — what it changed

Two independent passes, one Claude and one Codex, run 2026-09-09 on the rewrite. **Both
independently returned the SAME blocker and the SAME two majors**; every finding was
verified against the cited code before folding.

- **BLOCKER — the seam was wrong twice over.** The fill ran to `issue_time` (re-creating
  the partial-trailing-bucket defect Plan 239 T1a exists to prevent) and started ON the
  newest reanalysis timestamp (double-counting that bucket). T1's mechanism is rewritten.
- **MAJOR — the contract propagation was invisible.** `assemble_assignment_inputs` has
  neither the store nor the source; the fake, the authoritative spec and the touchpoint
  map all need edits. Now named in T1.
- **MAJOR — exit gate 5 had no owning task.** Resolved by owner decision D6: deleted.
- Codex additionally found that this plan **reverses a documented must-not-change
  contract** (`docs/touchpoint-maps.md:247`). See the section below — that omission was
  the most serious thing either pass caught, because the plan silently contradicted an
  invariant the repo states in prose and suggests a regression test for.

### Review round 3 (2026-09-10) — the PLAN was reviewed, and it was directing a defect

An independent pass over this document, after T1/T2 shipped. One BLOCKER, two MAJORs,
four MINORs; all verified against the code before folding.

**The BLOCKER was in the plan, not the code.** T1 step 3 said "keep one row per
`(parameter, valid_time)` by maximum `cycle_time`" — which selects a run's own first step
whenever a timestamp is a cycle stamp, and for de-accumulated precipitation that step is a
structural zero. The implementation caught it on its third review round; the plan text
still said the opposite, so anyone re-implementing from this document would have rebuilt
the ~17% under-read. Step 3 now carries the rule and its own warning section.

Two of the plan's own claims were false as written and are corrected in place rather than
annotated: the recency flag does **not** fire on every forecast (only one deployed model
declares past forcing at all), and D5's "only two parameters in `weather_forecasts`" is
true of the Swiss route only — Nepal's snow writes to the same store.

The mechanism steps were also rewritten to say what a correct fill does: **per-series**
anchors and **tail-only** merging, not a frame-wide anchor and "reanalysis precedence",
which as written permitted interior gap-filling this plan forbids.

## ⛔ DO NOT OVER-ENGINEER — binding on this plan AND on every reviewer

1. **"No findings" is a complete and welcome review.** Do not manufacture findings.
2. A finding must name a **CONCRETE DEFECT** with `file:line`.
3. **No new data source, no new store, no new ingest, and NOTHING PERSISTED.** Every
   value this plan uses is already in `weather_forecasts`.
4. **Do not widen to interior gap-filling.** This is the span between the newest
   published reanalysis and the issue time. Holes in the middle of the history are out
   of scope.
5. **Do not touch the future leg.** It works. This plan changes the past leg only.
6. **Adding length is a cost. Prefer DELETING to adding.**

## What the code does today — measured, not inferred

There is **no forecast tier on the past side anywhere in the system.** The operational
assembler builds two independent legs that meet at the issue time:

| leg | source | file |
|---|---|---|
| past | reanalysis **only**, via the MeteoSwiss priority chains | `operational_inputs.py:676-712`, `track_assembly.py:346-377` (identical blocks) |
| future | exactly **one** cycle (`cycle_time=cycle_time`), backdated buckets **dropped** | `operational_inputs.py:715-757` → `build_future_dynamic_frame` → `_filter_and_cap_daily_records` |

So the past leg ends wherever MeteoSwiss stops publishing, the future leg starts at the
issue time, and the current cycle's own backdated hours are discarded rather than used.
The span between them is served by nothing.

The interface handing the model its inputs reflects the same split: one `past_known`
series and one `future_known` series per variable
(`adapters/forecast_interface.py:1480-1512`). The model concatenates them into the single
sequence it actually consumes. **The continuous series the owner describes is already the
consuming shape** — it is only the past leg's sourcing that is incomplete.

## 🔴 This plan REVERSES a documented must-not-change contract

`docs/touchpoint-maps.md:247` states, as a must-not-change contract for this subsystem:

> **no imputation** — missing operational-input values are gated (`max_nan`), never
> imputed / interpolated / filled

and `:272` suggests a regression test asserting "that missing operational data is *gated,
not filled*". **This plan fills.** That is the owner's decision (D1) and it is the right
one operationally, but it is a reversal of a stated invariant and must be recorded as one
rather than slipped in.

The reversal is **narrow, and the narrowness is what makes it safe**: it applies to the
PAST FORCING leg of the two OPERATIONAL assemblers only. `max_nan` still gates values,
nothing is imputed or interpolated (every filled value is a real stored forecast, never a
derived one), the historical paths are untouched, and nothing is persisted. T1 must update
that contract text to say exactly this — leaving `:247` as written would make the repo
assert two contradictory things.

## The material is all on the host — measured 2026-09-09

| fact | value |
|---|---|
| newest `historical_forcing` valid_time, **all five** MeteoSwiss products | 2026-09-07 00:00Z (~2.4 d behind) |
| NWP cycles retained | **169**, 2026-07-03 06Z → 2026-09-09 00Z, 6-hourly, 148 stations |
| horizon per cycle | 5 days (121 hourly steps × 21 members) |
| cycles covering 2026-09-07 / -08 / -09 | **21 / 22 / 21** |
| freshest cycle covering each gap day | one issued **on that day** — lead ≈ 0 |
| NWP parameters available | **precipitation, temperature — and nothing else** |

**A forecast at ~zero lead is the best available estimate of what happened.** That is why
this is standard practice rather than a compromise: for the days in question we are not
choosing between a measurement and a guess, we are choosing between a short-lead forecast
and *no value at all*.

## The decisions, closed

**D1 — the fill is decided, not proposed** (owner, 2026-09-08).

> "what we need operationally is to gap fill with forecasts. if that is bad for model
> training or not. there is no operational way around this."

**D2 — nothing is stored; concatenate in memory** (owner, 2026-09-08).

> "we don't need to store the gap fill. we can concatenate it on the fly for the
> forecasts."

This removes the plan's main risk instead of managing it. Verified 2026-09-08 — four
assemblers, and neither historical one calls an operational one:

| assembler | route | sees the fill? |
|---|---|---|
| `operational_inputs.py:534` | live | YES — fill here |
| `track_assembly.py:157` | live, per-track | YES — fill here |
| `hindcast.py:151` | hindcast | **no — never calls the above** |
| `training_data.py:420` | training | **no — never calls the above** |

Training on forecast values would teach the model the forecast's biases rather than the
weather's; a hindcast filled with forecasts issued after its own issue time is look-ahead
leakage that flatters every skill score derived from it. Neither can occur if the values
exist only inside an operational assembler's frame and are never written. **The guard
shrinks from a subsystem to one test per historical assembler.**

The precedent already in the repo: Nepal's `RecapGatewayReanalysisAdapter` admits
reanalysis rows and DROPS IFS forecast-fill rows on the reanalysis path
(`tests/unit/adapters/test_recap_gateway.py::TestReanalysisLeakageGuard`, Plan 082 Task
3B item 3). **That guard is the load-bearing precedent, not the fill.**

**D3 — which run fills a given past step: the freshest cycle covering it** (owner,
2026-09-09). Not the cycle current at that step's start. The operational goal is the best
estimate of what happened, not a reconstruction of what was knowable; measured above, the
freshest covering cycle is typically one issued that same day.

**D4 — which member fills it: the control run** (owner, 2026-09-09). The past frame has
**no member dimension** — `raw_forcing_to_dataframe` produces one series per variable and
`_past_input_series` reads one column — so the fill must collapse to a single value
regardless; that part is structural. Choosing the control over the ensemble mean keeps
the joined series consistent in character across the seam: the reanalysis it continues is
a single deterministic trace, and an ensemble mean is an average of 21 possible weathers
rather than one coherent one.

**D5 — which parameters are filled: precipitation and temperature, and no others.** Those
are the only two parameters in `weather_forecasts` **on the MeteoSwiss route**.

⚠️ **Corrected 2026-09-10 — this is a decision, not merely a measurement.** The Nepal route
writes `swe`/`snow_depth`/`snowmelt` into the SAME store
(`flows/run_forecast_cycle.py:1611` → `store_weather_forecasts`), with `member_id=None` —
which is also how a deterministic control run is marked. Without an explicit parameter
allowlist a model declaring past snow would receive forecast-filled snow on a route this
plan defers. The implementation therefore carries one; the "settled by measurement"
framing was wrong. `relative_sunshine_duration`, `temperature_min` and `temperature_max`
have no forecast counterpart and their past legs stay short. **This fully covers
`cmal_small`, which declares precipitation and temperature only** — the pilot in Plan 262
is not partially served.

**D6 — no fill-provenance signal on the forecast** (owner, 2026-09-09). Asked how a
forecast should show that its history was completed from forecasts rather than measured,
the owner answered:

> "we ship what data we have and the model takes care of how it uses it."

So the "says so" outcome and its exit gate are **deleted**, not deferred. Both review
passes independently found that gate had no owning task; this closes it by decision rather
than by construction. ⚠️ The consequence, recorded so it is not rediscovered as a surprise:
after a successful fill there are no missing buckets, `assess_past_forcing_gaps` returns
`None` (`services/input_quality.py:190-191`), and a filled forecast is **indistinguishable
from an all-reanalysis one**. A silently stalled MeteoSwiss feed therefore shows no signal
on the forecast; it remains visible only in the ingest path's own monitoring.

## Why this matters now

1. **Every model reads a truncated history, silently.** A model declaring past
   precipitation gets its lookback minus the last ~2.4 days, and a shorter frame is still
   a valid frame — nothing raises.
2. **It reaches models as a shape failure, not a data failure.** A missing tail yields
   **fewer rows, not nulls**, so the `max_nan=0` gate passes untouched
   (`adapters/forecast_interface.py:1029-1038` counts nulls and NaNs in the frame it was
   given). Per CLAUDE.md, `max_nan` is a pre-`predict` NaN gate only and shape shortfalls
   are the model's responsibility — so the model returns `ModelFailure`, and the operator
   sees a failed forecast whose stated cause is the model's input validation rather than
   an unfilled forcing series.
3. **It makes any recency-based quality signal useless.** Plan 239 T1b ships
   `forcing_recent_steps` with a default of **2** (`config/deployment.py:78`). With the
   newest reanalysis at 2026-09-07 00:00Z and a daily anchor, the most recent expected
   bucket (2026-09-08) is missing while 2026-09-07 is present — **one** bucket, not two
   (an earlier revision said two). One missing recent bucket is enough to fire it.

   ⚠️ **Corrected 2026-09-10 — the flag does NOT fire on every forecast.** `past_forcing_flags`
   iterates `data_requirements.past_dynamic_features`
   (`services/run_station_forecast.py:462-472`, `services/run_group_forecast.py:329-336`),
   and an empty set yields no flags. `linear_regression_daily`, `climatology_fallback` and
   `persistence_fallback` all declare `past_dynamic_features=frozenset()`; `NwpRegression`
   and `NwpRainfallRunoff` declare only their own target history, which the FI adapter
   routes to the target channel, not the forcing one
   (`adapters/forecast_interface.py:673-675`). **Only `SeasonalPrecipRunoffRegression`
   overrides `_extra_past_known` (`models/nwp_regression.py:765`)** — plus `cmal_small`
   under Plan 262. Measured on the staging host the same day: of 1,337 forecasts in 20 h,
   **zero** carry a `forcing` flag; all 1,337 are DEGRADED from `warm_up` instead. So the
   quality label IS saturated — by a different cause — and this motivation is narrower
   than it was written.
4. **It is what stands between the deep-learning pilot and a real forecast** (Plan 262).

## Tasks

### T1 — assemble the past leg from the freshest source at each step

**Outcome.** In both operational assemblers, the past forcing frame covers every complete
bucket of the aligned lookback window: reanalysis where MeteoSwiss has published,
control-member forecast values for the buckets after it, joined in memory.

**The mechanism — RESAMPLE EACH SOURCE SEPARATELY, THEN MERGE.** Both review passes
independently rejected the earlier "concatenate raw, then resample" wording, for two
distinct reasons that compound:

1. The reanalysis frame is already **daily**; NWP rows are **hourly**. Concatenating them
   into one frame and resampling once sums a daily precipitation total together with 24
   hourly increments in the same bucket.
2. The window is **not** `[…, issue_time)`. It is
   `aligned_lookback_bounds(issue_time, lookback_steps, time_step)`
   (`operational_inputs.py:573`, `track_assembly.py:265`), whose `end =
   floor_to_time_step(issue_time, time_step)` (`training_data.py:281`) deliberately
   EXCLUDES the in-progress bucket. Filling to `issue_time` appends a partial bucket that
   `resample_to_time_step` then presents as a whole one — exactly the defect Plan 239 T1a
   exists to prevent — and leaves `past_dynamic` one row longer than `past_targets`.

So, in order:

1. Resample the reanalysis frame as today.
2. Read control-member forecast rows up to `past_targets_end`, never `issue_time`,
   starting after **each series' OWN last measured bucket** — per series, not frame-wide,
   because the products publish independently and a frame-wide anchor under-fills every
   series that lags the others. (Revised: an earlier revision said
   `newest_reanalysis_bucket`, which was safe only because all five products happened to
   stop at the same stamp on 2026-09-09.)
3. Keep one row per `(parameter, valid_time)` by **maximum `cycle_time`** (D3) — but
   ⛔ **never a run's own first step for a de-accumulated parameter**
   (`valid_time == cycle_time`); fall through to the next-freshest covering cycle. See
   the warning below: this is the single most dangerous line in the plan.
4. Resample those to the model's declared step with the model's own declared aggregation.
5. Merge **tail-only, per series**: fill only buckets strictly after that series' own last
   measured bucket. (Revised: an earlier revision said "reanalysis precedence — a bucket
   the reanalysis already holds is never overwritten", which as stated permits writing
   into a bucket present as a row with a NULL value — interior gap-filling, which this
   plan forbids.)
6. Fill a bucket **only when it holds every step of the source's native grid**; otherwise
   leave it absent. A partly-covered bucket would resample to a silently low precipitation
   total with no null, which neither `max_nan`
   (`adapters/forecast_interface.py:1029-1038`) nor the T1b gap flag would see.
   ⚠️ The cadence must be **DECLARED per `nwp_source`** (`icon_ch2_eps` = 1 h), never
   inferred from the rows being validated — inference is circular: a bucket holding hours
   0,2,…,22 reads as a 2-hourly source, "completes" at 12 steps, and yields half the
   precipitation. **A source with no declared cadence is not filled**, and says so.

### ⛔ The lead-0 trap — read this before touching step 3

Precipitation is stored **de-accumulated**: `adapters/meteoswiss_nwp.py:186` pads by one
and diffs, so a run's first output is `tp(0)` itself, and ICON's `tp(0)` is zero because
accumulation starts at the run's start. That step **carries no interval** — it is not "no
rain in that hour".

"Maximum `cycle_time`" selects exactly that step for every `valid_time` that IS a cycle
stamp (00/06/12/18Z) — four of every twenty-four hourly increments. The bucket still holds
a complete grid and no null, so the completeness rule, `max_nan` and the gap flag all
pass: **every filled daily precipitation total silently under-reads by ~17%**.

Measured on the staging host 2026-09-10: **96,726 of 96,726** stored lead-0 precipitation
rows are exactly 0.0, against a 0.1037 mean at leads 1–5 h. Temperature at lead 0 averages
15.91 °C — it is converted K→°C and nothing else, so **the skip applies to de-accumulated
parameters only**; discarding a run's first temperature reading would throw away the
freshest real value.

**In — the read path exists; its filters do not.**
`WeatherForecastStore.fetch_lookback` (`protocols/stores.py:299`,
`store/weather_forecast_store.py:68`) queries by `valid_time` across **all** cycles —
exactly this shape — and its only callers are
`tests/integration/store/test_weather_forecast_store.py:148,161` and the fake. Use it
rather than adding a method.

⚠️ **It needs parameter and member filters, and that is a correctness-of-cost item, not an
optimisation.** Measured for station 2009 over 2026-09-07 → 2026-09-09 13:00Z:

| query | rows |
|---|---|
| `fetch_lookback` as it stands today (no filters) | **47,964** |
| the same window filtered to `member_id = 0` | **2,284** |

A 21× difference, **per station, per cycle** — 7.1M rows against 338k across 148 stations.

**In — the contract propagation, which the first revision hid.** "One shared helper called
from both" understates the change. `assemble_station_operational_inputs` already receives
`weather_forecast_store` and `nwp_source` (`operational_inputs.py:542-543`);
**`assemble_assignment_inputs` receives neither** (`track_assembly.py:157-171` — the module
never mentions either). The past-leg *blocks* are identical; the enclosing functions are
not. T1 therefore also covers:

- two new parameters on the public `assemble_assignment_inputs`, and its production call
  site (`flows/run_forecast_cycle.py:1915`) plus its ~12 test call sites;
- `FakeWeatherForecastStore.fetch_lookback` (`tests/fakes/fake_stores.py:484`), which must
  grow the same filters as the Protocol;
- `docs/spec/types-and-protocols.md`, the authoritative Protocol specification;
- `docs/touchpoint-maps.md:247` and `:272`, whose "no imputation" contract this plan
  narrowly reverses — see the section above. Leaving them unedited would make the repo
  assert two contradictory things.

**Out.** `_PRIORITY_CHAINS` (`hybrid_reanalysis_factories.py:42-54`) — shared with training
and hindcast; touching them is what created the risk D2 removes. The future leg. Interior
gaps. Any write to any store.

**Verification.** Per assembler: given a reanalysis frame ending N buckets short and stored
forecasts covering the remainder, the assembled `past_dynamic` covers the full aligned
window, ends on **`past_targets_end`** (never later), has the **same height as
`past_targets`**, carries the value of the freshest covering cycle **that has a usable step** where cycles
overlap,
and leaves the last reanalysis bucket's value **unchanged**. Plus:

- an **off-midnight seam test** — a 06Z cycle for a daily model — asserting numerically
  that no partial bucket is appended and the seam bucket is not double-counted;
- a partly-covered bucket is left absent, not filled low;
- a parameter with no forecast counterpart (`relative_sunshine_duration`) is left short.

**Pre-change.** RED, and it must fail for the missing tier rather than a missing symbol:
assert today that the assembled `past_dynamic` is short of `past_targets_end` by exactly
the reanalysis lag, with forecast rows present in the store covering the difference. A test
failing on an absent helper proves only that the helper is unwritten.

### T2 — prove the historical paths did not move

**Outcome.** Hindcast and training frames are byte-identical to before T1.

**In.** One test per historical assembler (`hindcast.py`, `training_data.py`). This is the
whole of what an earlier design needed a leakage subsystem for.

**Out.** Any change to those assemblers.

**Verification.** The two tests pass, and they fail if the helper is wired into either
historical path.

**Pre-change.** N/A — these tests must pass both before and after T1; that is the point.

### T3 — settle Plan 239 T1b's `recent_steps`

**Outcome.** `forcing_recent_steps` has a default chosen against re-measured data — quiet
in normal operation, loud when the source genuinely stalls.

**In.** After T1 is deployed: confirm on the host that the past leg now reaches
`past_targets_end` for a real cycle (exit gate 1), then choose the default and record the
number here.

⚠️ **Measure the RESIDUAL, not the filled series.** A successfully filled series has no
missing buckets at all — this plan says so at D6 — so it cannot discriminate a
`recent_steps` of 1 from 5. The population that can is the cycles where the fill
**declines**: an incomplete native grid, no covering cycle, or a source with no declared
cadence. Those are what `forcing_recent_steps` must stay quiet about in normal operation
and fire on when the source genuinely stalls.

⚠️ **Verify, do not assume, that the input-quality label is written at all.** Measured
2026-09-09: **every one of the 4,248 forecasts issued in the last three days has
`input_quality` NULL**, including the 06:00Z cycle, on a host running 0.1.894 — which
contains T1b. The write path exists (`run_station_forecast.py:620`), so either the deploy
landed after that cycle or the flag is not reaching the row. If the label is not being
written, that is a **finding to report, not a defect for this plan to fix** — record it
against a new plan.

**Out.** Building any new quality mechanism. Any fill-provenance signal (D6).

**Verification.** The chosen default, and the measurement it rests on, written into this
plan. The NULL question answered either way, in writing.

**Pre-change.** N/A — measurement and configuration.

```json
{
  "phases": [
    {"id": "T1", "parallel": false, "depends_on": []},
    {"id": "T2", "parallel": false, "depends_on": ["T1"]},
    {"id": "T3", "parallel": false, "depends_on": ["T1"]}
  ]
}
```

## Exit gates

1. The operational past forcing leg covers every complete bucket of the aligned lookback
   window for precipitation and temperature, ending on `past_targets_end` and with the
   same height as `past_targets`. (The live-host confirmation belongs to T3, which is the
   only task that runs after deployment — an earlier revision left it here, owned by no
   task, which is exactly the defect that retired the old gate 5.)
2. Where cycles overlap, the value used is the control member of the freshest covering
   cycle **that carries a usable step** — never a run's own first step for a
   de-accumulated parameter; and every bucket a series already measured is unchanged.
   Asserted by test, including an off-midnight seam case and a lead-0 case, not by
   inspection.
3. Hindcast and training frames are byte-identical to before, proven by test, not by
   argument.
4. Nothing is written to any store by this plan.
5. `forcing_recent_steps`'s default is chosen against re-measured data, not against
   today's permanently-short tail.
6. `docs/touchpoint-maps.md`'s "no imputation" contract states the narrow reversal, and
   `docs/spec/types-and-protocols.md` carries the new `fetch_lookback` filters.
7. `uv run pytest tests/unit && uv run pytest tests/integration` pass; ruff and pyright no
   worse than the recorded ratchet.

## Deferred (explicitly not this plan)

Interior gap-filling; a new reanalysis source; changing what is ingested; back-filling
stored historical rows; using more than the control member on the past leg; extending the
fill to parameters with no forecast counterpart; and any change to Nepal's existing recap
forecast-fill, which already works.

**The Nepal route itself is deferred, explicitly.** `ifs_ecmwf` is stored verbatim by the
recap Gateway (`adapters/recap_gateway.py:613` — no resampling) on a lead-dependent
3-hourly-then-6-hourly grid, so no single native step describes it and this fill does not
run there. Declaring that grid, and filling Nepal's past leg, belongs to whichever plan
takes it on. (An earlier revision deferred only the Gateway's own upstream fill, which is
a different thing, and said nothing about this fill running on the Nepal route.)
