---
status: DRAFT
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

**DRAFT — rewritten 2026-09-09 after the owner corrected the framing.** Not for
implementation until the owner sets READY.

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
| `training_data.py:413` | training | **no — never calls the above** |

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

**D5 — which parameters are filled: precipitation and temperature, and no others.** This
is settled by measurement, not preference: those are the only two parameters in
`weather_forecasts`. `relative_sunshine_duration`, `temperature_min` and `temperature_max`
have no forecast counterpart and their past legs stay short. **This fully covers
`cmal_small`, which declares precipitation and temperature only** — the pilot in Plan 262
is not partially served.

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
   `forcing_recent_steps` with a default of **2** (`config/deployment.py:78`). Measured
   against today's data the newest two buckets are never present, so that fires on every
   forecast, permanently. A warning that is always on hides the real ones.
4. **It is what stands between the deep-learning pilot and a real forecast** (Plan 262).

## Tasks

### T1 — assemble the past leg from the freshest source at each step

**Outcome.** In both operational assemblers, the past forcing frame reaches the issue
time: reanalysis where published, control-member forecast values from the shortest-lead
covering cycle beyond it, concatenated in memory before the existing resample.

**In.** One shared helper, called from `operational_inputs.py` and `track_assembly.py`
immediately after `fetch_reanalysis` returns and **before** `resample_to_time_step` — the
same two places Plan 239 T1a corrected, whose past-leg blocks are identical today. Read
the newest reanalysis timestamp per parameter, read forecast values covering
`[newest_reanalysis, issue_time)`, keep one value per `(parameter, valid_time)` by
**maximum `cycle_time`** (D3), restrict to `member_id = 0` (D4), concatenate, and let
T1a's existing resample and per-variable aggregation handle the rest.

**The read path already exists and has no production caller.**
`WeatherForecastStore.fetch_lookback` (`protocols/stores.py:298`,
`store/weather_forecast_store.py:68`) queries by `valid_time` range across **all** cycles
— exactly this shape — and is called today only from
`tests/integration/store/test_weather_forecast_store.py`. Use it rather than adding a
method.

⚠️ **It needs parameter and member filters, and that is a correctness-of-cost item, not
an optimisation.** Measured for station 2009 over 2026-09-07 → 2026-09-09 13:00Z:

| query | rows |
|---|---|
| `fetch_lookback` as it stands today (no filters) | **47,964** |
| the same window filtered to `member_id = 0` | **2,284** |

A 21× difference, **per station, per cycle** — 7.1M rows against 338k across 148
stations. Add optional `parameters` and `member_id` arguments to `fetch_lookback` on both
the Protocol and the implementation; the existing integration test pins the unfiltered
behaviour, so both paths stay covered.

**Out.** `_PRIORITY_CHAINS` (`hybrid_reanalysis_factories.py:42-54`) — those chains are
shared with training and hindcast, and touching them is what created the risk D2 removes.
The future leg. Interior gaps. Any write to any store.

**Verification.** A unit test per assembler: given a reanalysis frame ending N steps short
and stored forecasts covering the remainder, the assembled `past_dynamic` reaches the
issue time, carries the **freshest** covering cycle's value where cycles overlap, and
carries the control member. Plus one asserting a parameter with no forecast counterpart
(`relative_sunshine_duration`) is left short rather than filled with something else.

**Pre-change.** RED, and it must fail for the missing tier rather than a missing symbol:
assert today that the assembled `past_dynamic`'s last timestamp is short of the issue
time by exactly the reanalysis lag, with forecast rows present in the store that cover the
difference. A test failing on an absent helper proves only that the helper is unwritten.

### T2 — prove the historical paths did not move

**Outcome.** Hindcast and training frames are byte-identical to before T1.

**In.** One test per historical assembler (`hindcast.py`, `training_data.py`). This is the
whole of what an earlier design needed a leakage subsystem for.

**Out.** Any change to those assemblers.

**Verification.** The two tests pass, and they fail if the helper is wired into either
historical path.

**Pre-change.** N/A — these tests must pass both before and after T1; that is the point.

### T3 — settle Plan 239 T1b's `recent_steps`, and verify the label is written at all

**Outcome.** `forcing_recent_steps` has a default chosen against re-measured data — quiet
in normal operation, loud when the source genuinely stalls — and a forecast whose past leg
was forecast-filled says so.

**In.** Re-measure the tail after T1, choose the default, and record the number here.

⚠️ **Verify, do not assume, that the input-quality label is written.** Measured
2026-09-09: **every one of the 4,248 forecasts issued in the last three days has
`input_quality` NULL**, including the 06:00Z cycle, against a host running 0.1.894 — which
contains T1b. The write path exists (`run_station_forecast.py:620`), so either the deploy
landed after that cycle or the flag is not reaching the row. This plan's exit gate 4
depends on the answer. Establish it before relying on the mechanism; if the label is not
being written, that is a **finding to report, not a defect for this plan to fix** — record
it against a new plan.

**Out.** Building any new quality mechanism.

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

1. The operational past forcing leg reaches the issue time for precipitation and
   temperature, verified on the mini against a real cycle.
2. Where cycles overlap, the value used is the freshest covering cycle's control member —
   asserted by test, not by inspection.
3. Hindcast and training frames are byte-identical to before, proven by test, not by
   argument.
4. Nothing is written to any store by this plan.
5. A forecast whose past leg was forecast-filled says so — or T3 records, in writing, that
   the label mechanism is not currently writing and why.
6. `forcing_recent_steps`'s default is chosen against re-measured data, not against
   today's permanently-short tail.
7. `uv run pytest tests/unit && uv run pytest tests/integration` pass; ruff and pyright no
   worse than the recorded ratchet.

## Deferred (explicitly not this plan)

Interior gap-filling; a new reanalysis source; changing what is ingested; back-filling
stored historical rows; using more than the control member on the past leg; extending the
fill to parameters with no forecast counterpart; and any change to Nepal's existing recap
forecast-fill, which already works.
