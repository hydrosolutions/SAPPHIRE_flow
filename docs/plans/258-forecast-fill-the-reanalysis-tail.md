---
status: DRAFT
created: 2026-09-08
plan: 258
title: Past forcing is permanently ~2.5 days short, and the values that would fill it are already stored
scope: Concatenate FORECAST values onto past forcing IN MEMORY, in the two operational assemblers only, covering the span between the newest reanalysis and the issue time. NOTHING IS STORED. NOT a new data source, NOT a change to what is ingested, NOT a new store column, NOT gap-filling of interior holes.
depends_on: [239]
blocks: []
source: 2026-09-08 — measured while validating Plan 239 T1b. The owner then stated the intent directly: "weather history is a bit behind. we know about that and we fill the gap with forecasts in that period. if this is not yet implemented, make a plan."
---

# Plan 258 — the reanalysis tail is never filled, though the values are already here

## Status

**DRAFT.** Not for implementation until the owner confirms.

## ⛔ DO NOT OVER-ENGINEER — binding on this plan AND on every reviewer

1. **"No findings" is a complete and welcome review.** Do not manufacture findings.
2. A finding must name a **CONCRETE DEFECT** with `file:line`.
3. **No new data source, no new store, no new ingest, and NOTHING PERSISTED.** The values already
   exist in `weather_forecasts`. This plan concatenates them in memory at read time.
4. **Do not widen to interior gap-filling.** This is the TAIL only — the contiguous span between
   the newest reanalysis and the issue time. Holes in the middle of the history are out of scope.
5. **Adding length is a cost. Prefer DELETING to adding.**

## What we measured (2026-09-08, on the mac mini, not inferred)

| fact | value |
|---|---|
| newest `historical_forcing` valid_time, ALL parameters | 2026-09-06 00:00Z |
| how far behind "now" that is | **2.49 days** |
| stations holding the 2-days-ago bucket | 147 |
| stations holding the **1-day-ago** bucket | **0** |
| typical store lag (`created_at - valid_time`) | 2.1–4.1 days |
| `weather_forecasts` rows already covering 09-05 → 09-08 | **6,463,086** |

The live tail IS working — `meteoswiss_rprelimd` is what serves recent precipitation. It is simply
MeteoSwiss's own publication lag: the preliminary product is ~2.5 days behind, and the definitive
one further still. This is normal, not a fault.

**The material to fill the gap is already on the host.** 6.46 million forecast rows span exactly the
missing days. Nothing new needs fetching.

## Why this matters now

Two separate consequences, and the second is the one that motivated the measurement:

1. **Models read a truncated history.** A model declaring past precipitation gets its lookback
   window minus the last ~2.5 days — silently, because a shorter frame is still a valid frame.
2. **It makes any recency-based quality signal useless.** Plan 239 T1b labels a forecast DEGRADED
   when a recent past-forcing bucket is missing. Measured against today's data that fires on
   **every forecast, permanently**, because the newest two buckets are never present. A warning
   that is always on hides the real ones. **T1b cannot ship a meaningful `recent_steps` default
   until this is settled** — that is the direct dependency.

## The precedent, and the guard it already carries

Nepal already has forecast-fill: `RecapGatewayReanalysisAdapter` admits reanalysis rows and DROPS
IFS forecast-fill rows on the reanalysis path
(`tests/unit/adapters/test_recap_gateway.py::TestReanalysisLeakageGuard`, Plan 082 Task 3B item 3).
**That guard is the load-bearing part of this plan, not the fill.**

## The decision is MADE — fill the gap (owner, 2026-09-08)

> "what we need operationally is to gap fill with forecasts. if that is bad for model training or
> not. there is no operational way around this."

**This plan does the fill. It is not a proposal to weigh.** An earlier draft presented the
training/hindcast risk as though it were an objection; it is not — there is no operational
alternative, and the risk is a detail to handle WHILE doing it, not a reason to hesitate.

## NOTHING IS STORED — the fill is concatenated on the fly (owner, 2026-09-08)

> "we don't need to store the gap fill. we can concatenate it on the fly for the forecasts."

**This removes the plan's main risk instead of managing it.** An earlier draft proposed a third tier
in `_PRIORITY_CHAINS`, which is shared by training, hindcast AND live — so it needed a caller-scoped
switch plus a guard to keep forecast values out of the historical paths.

Concatenating in memory makes that **impossible by construction**. Verified 2026-09-08 — four
separate assemblers, and neither historical one calls an operational one:

| assembler | route | sees the fill? |
|---|---|---|
| `operational_inputs.py:534` | live | YES — fill here |
| `track_assembly.py:157` | live, per-track | YES — fill here |
| `hindcast.py:151` | hindcast | **no — never calls the above** |
| `training_data.py:413` | training | **no — never calls the above** |

Why the risk was real and is now gone:

- **Training** on forecast values would teach the model the forecast's biases, not the weather's.
- **Hindcast** filled with forecasts issued AFTER its issue time is look-ahead leakage, and every
  skill score from it would be flattering and wrong.

Neither can occur if the rows exist only inside an operational assembler's frame and are never
written. The guard shrinks from a subsystem to one test asserting the historical paths are
unchanged.

## D1 — where the fill belongs

**In the two operational assemblers, right after `fetch_reanalysis` returns** — the same two places
Plan 239 T1a corrected (`operational_inputs.py`, `track_assembly.py`). Read the newest reanalysis
timestamp, read forecast values covering `[that, issue_time)` from `weather_forecasts`, concatenate,
resample as T1a already does.

**NOT** a tier in `_PRIORITY_CHAINS` (`hybrid_reanalysis_factories.py:42-54`). Those chains are
shared with training and hindcast; touching them is what created the risk this design removes.

## D2 — say so on the forecast

Nothing is stored, so there is no row to tag. What DOES need to survive is that the operator can see
the tail was forecast-filled rather than measured — which is an input-quality flag, the machinery
Plan 239 T1b just added (`InputQualityCategory.FORCING`). One flag, `PARTIAL`, naming the filled
span. **No new component.**

## Open questions the owner owns

- **Q1:** which forecast cycle fills a given day — the most recent cycle covering it, or the cycle
  that was current at that day's start? The second is more faithful to what was knowable; the first
  is more accurate. **Recommendation: most recent covering cycle**, since the operational goal is
  the best estimate of what happened, not a reconstruction of past knowledge.
- **Q2:** ensemble forecasts have many members. Fill with the control member, or the ensemble mean?
  **Recommendation: control**, matching how the deterministic reanalysis it replaces behaves.
- **Q3:** does the fill apply to every parameter, or only those whose chain has a live tail today?

## Phases

### T1 — concatenate the tail, in memory, in the two operational assemblers

Read the newest reanalysis timestamp, pull forecast values covering the remaining span, concatenate,
let T1a's existing resample handle the rest. Nothing written anywhere.

### T2 — prove the historical paths did not move

One test per historical assembler (`hindcast.py`, `training_data.py`) asserting their frames are
byte-identical before and after T1. This is the whole of what used to be a leakage subsystem.

### T3 — settle Plan 239 T1b's `recent_steps`

With the tail filled, re-measure and choose a default that is quiet in normal operation and loud
when the source genuinely stalls. **Blocked on T1** — choosing it before then would encode today's
permanent-degradation into the config.

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

1. The operational path's past forcing reaches the issue time.
2. Hindcast and training frames are byte-identical to before, proven by test — not by argument.
3. Nothing is written to any store by this plan.
4. A forecast whose tail was filled says so, via the existing input-quality flag.
5. Plan 239 T1b's `recent_steps` default is chosen against re-measured data, not against today's
   permanently-short tail.

## Deferred (explicitly not this plan)

Interior gap-filling; a new reanalysis source; changing what is ingested; back-filling historical
rows already stored; and any change to Nepal's existing recap forecast-fill, which already works.
