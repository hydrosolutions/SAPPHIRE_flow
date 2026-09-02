---
status: DRAFT
created: 2026-08-27
plan: 203
title: Forecast-cycle scaling to the full BAFU set (~170 stations) — measure the two costs that actually grow
scope: One measurement of how NWP extraction and model execution scale with station count, and a decision on whether the 441 s download needs parallelising. No optimisation is authorised by this plan — it exists to make the next plan grounded.
depends_on: []
blocks: []
source: Measured on the mac mini 2026-08-27 while trialling `forecast_combination_strategy = pooled` (PR #214)
note: The filename keeps its original `-to-100-stations` slug so existing references stay valid; the target n is ~170 (`docs/v0-scope.md:9`).
---

# Plan 203 — forecast-cycle scaling to the full BAFU set (~170 stations)

## Status

**DRAFT.** Not for implementation until the owner confirms.

## ⛔ Proportionality — this plan MEASURES, it does not optimise

The owner's target: **the full BAFU station set, 4×/day, comfortably.** That set is **~170
stations** — `docs/v0-scope.md:9` and `docs/architecture-context.md:10` both put v0 at "up to ~170
stations (LINDAS-available BAFU gauges)", so **~170, not a round 100, is the n this plan sizes
for** (an earlier draft used 100, missing 70 of the ~170 gauges — 40 % of the real set). The instinct after
seeing a 7.5-minute cycle is to parallelise. **Do not.** The profile below shows the obvious target
is the wrong one, and the real one is not yet measured. This plan produces two numbers and a
decision.
Reviewers: do not propose `task.map`, async rewrites, worker pools, caching or a queue here — that
belongs to whichever plan T1's numbers justify.

### ⛔ Note to the `/plan` workflow agents (planner AND reviewers) — read before your first round

**This plan is two tasks and must still be two tasks when you converge. Growth is a defect here,
not thoroughness.** This repo has a recorded failure mode where `/plan` over-expands a doc until it
contradicts itself and stalls (that is how Plan 149 died, and Plan 229 needed a dedicated round to
undo its own expansion). Do not repeat it.

**Do NOT add:** new tasks or phases, rollback/migration sections, a test matrix, new files or
modules, CI changes, config flags, or restatements of `CLAUDE.md` conventions back at the plan.
T1 is a **heredoc measurement**, not a committed script — keep it that way.

*Correction (2026-09-02):* an earlier version of this note also forbade the per-task `uv run`
verification line and the closing JSON dependency graph. That was wrong — `docs/workflow.md:19-27`
**requires** both of every plan, and the first `/plan` run correctly flagged the contradiction.
Both are restored below. Keep them; they are required structure, not scope growth.

**Valid blockers are narrow.** Raise one only if: (a) T1's method would not actually produce the
two curves it promises; (b) an exit criterion is not checkable as written; (c) a number or citation
in "What is already measured" is wrong or unsupported by the quoted log line; or (d) the plan
contradicts the repo (wrong path, wrong config key, wrong flow name — cite `file:line`).

**Explicitly OUT of bounds for review:** proposing any optimisation design, arguing the plan should
"also" fix the download, or expanding scope to the items under "Separately worth knowing". Those
are deliberately deferred and their deferral is not a finding.

**Converge fast. A round that finds nothing is a success, not a failed round.** If your only
suggestion is additive, the correct output is "no blockers, no majors".

## What is already measured (mac mini, 2026-08-27T13:29Z, 2 stations)

In the observed **2-station** cycle, the fixed NWP phase was ~95 % of wall clock (441.2 s of
465.5 s) and **that cost does not grow with station count** — it is the same grid every cycle.
Note this is a share of a 2-station cycle: the per-station terms are tiny at n=2 by construction,
so 95 % is not a claim about the cycle at n=170.

| phase | duration | scales with | evidence |
|---|---|---|---|
| NWP download | **441.2 s** | nothing — same grid every cycle | `nwp.fetch_completed duration_ms=441236 file_count=484 total_bytes=2874343206` |
| zarr archive | 7.7 s | nothing | `nwp.archive_completed duration_ms=7679.6 size_bytes=1748057101` |
| basin extraction | **12.95 s / 2 stations → 6.48 s/station** | **stations** | `extraction.completed duration_ms=12951.1 stations_extracted=2` |
| combination + persistence | **~40.5 s** | forecasts *persisted* | 425.0 s (2 persisted) → 465.5 s (12 persisted) |
| model execution | **NOT MEASURED** | — | see below |

**2.87 GB in 441.2 s ≈ 6.5 MB/s**, but note the 441.2 s is **STAC walk + transfer + GRIB parse**,
not a network-download measurement: `meteoswiss_nwp.py` starts the timer before
`_fetch_grib_files()` and parses every GRIB before emitting `nwp.fetch_completed`. So "6.5 MB/s" is
not a throughput figure and cannot by itself decide whether the transfer is worth parallelising.
484 files were fetched against **24 079 `nwp.variable_skipped`** events — those are STAC metadata
items inspected and rejected **before** asset download (filtering is client-side but pre-download),
not variables downloaded and thrown away.

### 🔴 Correction (2026-09-02): model cost is UNMEASURED, not "nearly free"

An earlier version of this plan claimed *"model count is nearly free — going from 1 model to 6 per
station cost +9 % wall clock"*, and derived **~3.3 s/forecast** from the 425.0 s → 465.5 s trial.
**Both are withdrawn.** The trial switched `forecast_combination_strategy` PRIMARY → POOLED, and
**both modes already execute every assigned model**: `run_station_forecast()`
(`src/sapphire_flow/services/run_station_forecast.py`) is a thin wrapper that calls
`run_all_station_forecasts()` and then returns only `multi.results[multi.primary_model_id]`. The
in-code comment at `run_forecast_cycle.py:3102` ("single model with fallback chain") is **stale and
misleading** — it describes intent, not behaviour.

So the trial changed which forecasts were **persisted**, not how many models **ran**. The 1-model
vs 6-model comparison never happened. The +40.5 s is combination/persistence overhead.

Two consequences: (a) the arithmetic was also wrong — 40.5/10 = **4.05 s**, not the 3.375 s the
plan used, because it divided by the *ending* count (12) instead of the *increment* (10); and
(b) **there is no evidence either way on model scaling.** Treat it as an open number. Any claim that
parallelising model execution addresses only ~9 % of the cycle is unsupported.

## The open question this plan answers

Two costs grow, and only one is measured at n=2:

1. **Extraction — 6.48 s/station at n=2.** If that is linear, 170 stations ≈ **1101 s**, which
   would overtake the download and become the dominant cost. If most of it is per-cycle overhead
   (opening the zarr, loading the grid) with a cheap per-basin mask, 170 stations might cost far
   less. **n=2 cannot distinguish these**, and the difference decides whether any work is needed.
   The per-station term is not a cheap mask by construction: `_assign_cells()`
   (`src/sapphire_flow/preprocessing/mesh_basin_extractor.py:190`) runs a point-in-polygon
   `gpd.sjoin(..., predicate="within")` (`:206`) over every grid-cell centroid against every basin
   polygon, and it is recomputed fresh each cycle — `run_forecast_cycle.py:1414-1417` passes a
   freshly built `station_basins` map into `extract()` with no cross-cycle reuse of the assignment.
   So the cost tracks **polygon complexity**, not just basin count (see T1's geometry note).
2. **Model execution — no measurement exists** (see the correction above). It could be a large
   term or a negligible one; the plan does not know, and must not pretend a range.

### Projection — a FLOOR, not an estimate

Assuming extraction is linear — **the pessimistic case for that term, and explicitly not yet
established**:

```
 441 s  STAC walk + transfer + parse   (fixed)
   8 s  archive                        (fixed)
1101 s  extraction                     (170 stations × 6.48 s)
-----
≈ 1550 s ≈ 26 min per cycle   ← FLOOR: excludes ALL per-station work below
     ?    model execution                (unmeasured)
     ?    assembly, artifact load, FI adapter, ensemble fan-out, QC, persistence (unmeasured)
```

**This is a lower bound, not a projection of the cycle.** It counts two fixed costs plus one
measured-at-n=2 term, and omits every other per-station operation a real cycle performs. The true
number is ≥ 26 min and unknown by an unknown margin.

That is precisely why T1 matters, and it is the substantive change from the earlier n=100 draft:
there, the same arithmetic gave ~24 min against a 30-min bar and made T1 look like a formality.
At the real deployment size the **floor alone** is already within ~4 minutes of the bar, before
counting a single unmeasured term. If extraction turns out sub-linear — which the fixed per-cycle
costs inside `extract()` (opening the zarr, materialising `cell_points`,
`mesh_basin_extractor.py:100-105`) make plausible — headroom reappears; if it is linear or worse,
the bar is gone before models are even added.

**Where the 30-minute bar comes from:** it is ~8 % of the 6-hour cadence, i.e. the point at which a
cycle still finishes with >5× headroom before the next one starts. It is a judgement call, not a
measured constraint; T2 may revise it with the owner, but it must not be moved *after* seeing T1's
number.

## Tasks

**T1 — Measure the two scaling curves.** *(No production change.)*
*Scope (in):* run basin extraction against the ALREADY-ARCHIVED zarr
(`/data/nwp_grids/icon_ch2_eps/*.zarr`, no re-download) for **n = 2, 10, 25, 50, 100, 170**, timing
each; separately time `predict` for one model across the same n. Report both curves and the fitted
per-station marginal cost. Per `CLAUDE.md` § Ad-hoc Analyses this is a **heredoc**, not a committed
script.

*Geometry — use REAL basins, do not synthesise.* Extraction cost is driven by polygon complexity
(the `predicate="within"` sjoin at `mesh_basin_extractor.py:206`), so an ad-hoc synthetic polygon
would understate it and the fitted 6.48 s/station would be an artefact of the test data. Draw the
polygons from the production basin store instead — `BasinStore.fetch_all_basins()`
(`src/sapphire_flow/store/basin_store.py:58`) returns the same `Basin` objects
(`src/sapphire_flow/types/basin.py:17`, a shapely MultiPolygon from the delineation package) that
`run_forecast_cycle.py:2504-2509` feeds into `extract()`. If the mini's DB holds fewer than 170
basins, take every distinct real basin first and **top up by duplicating real polygons under
synthetic `StationId`s** — never by generating a shape.
*Known trade-off of that top-up (state it in the T1 write-up):* duplicated polygons preserve the
real vertex-count distribution (the thing that drives cost) but repeat the same footprint, so the
spatial index sees more locality than a genuinely distinct 170-basin set would. Record how many of
the n were distinct, so the verdict can be read with that caveat.

*Why 100 and 170 are measured, not extrapolated:* the largest previously planned sample was n=50,
and a curve that looks linear from 2→50 can still bend before 170 (e.g. RAM pressure from holding
many basin geometries and grid slices at once). Since the polygons are drawn/duplicated in bulk in
the same loop, measuring the target n directly costs almost nothing and removes the extrapolation
risk from the one number T2's decision hinges on.

*Scope (out):* changing extraction, the cycle, or any config; onboarding real stations (T1 READS
existing basins, it does not create any). **Also out: every other per-station operation** — input
assembly, assignment/threshold/baseline reads, artifact lookup and deserialisation, the FI adapter's
validation/conversion, ensemble fan-out, QC, persistence and combination. T1 does not measure these.

*⛔ Exit — what T1 may and may NOT conclude.* T1 produces **two curves** (extraction vs n, and
`predict` vs n, for n = 2, 10, 25, 50, 100, 170), the fitted per-station marginal cost of each, and
a one-paragraph verdict answering exactly one question: **do these two costs grow linearly, and
what do they contribute at n=170?**

T1 **may not** conclude "the 170-station cycle is under 30 min". It does not measure enough of the
cycle to say that, and an earlier version of this exit criterion wrongly claimed it could. The two
honest verdicts are:

- **Decisive:** the two curves *alone* already exceed the 30-min bar at n=170 → the target is
  missed regardless of the unmeasured terms, and T2 proceeds to a follow-up. No further measurement
  needed.
- **Inconclusive:** the two curves come in under the bar → **this does not establish that the cycle
  fits.** The unmeasured per-station work above could still close the gap. T2 must then decide
  whether to accept the risk or commission a second measurement of the full production station unit
  (`run_station_forecast` end-to-end, including persistence).

Record which of the two it is, explicitly, in the write-up.

*Verification:* `uv run python -c "import geopandas, xarray; print('T1 deps ok')"` — T1 itself is a
heredoc analysis run by hand on the mini, not a committed script or test, so there is no suite to
green. This command only confirms the analysis environment resolves before the run.

**T2 — Decide, and only then draft.** If T1 says the target is met at n=170: record that and
**close this plan** — no optimisation work. If extraction is super-linear or the 170-station total
exceeds ~30 min, draft a follow-up naming the specific phase to fix, with T1's numbers as its
baseline. If T1 returns the **inconclusive** verdict, T2's decision is a third option: accept the
unquantified risk and close, or commission the full-station-unit measurement. Note that the ~26 min
**floor** above already sits within ~4 min of the bar before any unmeasured term is counted, so this
branch is *live*, not hypothetical — T2 must wait for T1 rather than pre-judging either way.
*Exit:* an owner decision recorded here.
*Verification:* none — T2 is a decision, not a change. The exit is the recorded decision above.

## Dependency graph

```json
{
  "plan": 203,
  "tasks": [
    {"id": "T1", "depends_on": [], "parallel": false},
    {"id": "T2", "depends_on": ["T1"], "parallel": false}
  ]
}
```

## Separately worth knowing (not in scope, do not fix here)

- **2.87 GB per cycle × 4 = ~11.5 GB/day** of NWP download. Not a wall-clock problem, but it is a
  network and disk cost that grows if the variable allowlist widens, and `nwp_grid_retention_days`
  is 3.
- **24 079 skipped STAC items per fetch.** Filtering is client-side but happens **pre-download** —
  items outside the allowlist are rejected before the asset is fetched, so this is *not* wasted
  bandwidth. (The STAC allowlist tokens are `tot_prec` and `t_2m`; `tp` is the cfgrib short name.)
  What remains unknown is how much of the 441.2 s is pagination vs transfer vs GRIB parse — the
  existing `nwp.stac_walk_completed` timing would separate them. First thing to check IF T2
  concludes work is needed.
- **Download parallelism is unknown.** 484 files at 6.5 MB/s may be serial. Also unmeasured.

## Context

`CLAUDE.md` already names **forecast-cycle parallelisation** as an outstanding v0b/v0c follow-on,
and `task.map` is a recorded v0b remainder. This plan deliberately does **not** assume that is the
right shape. What the profile establishes is that the **fixed** STAC-walk/transfer/parse phase is
the single largest known term, so parallelising *it* is a different question from parallelising
per-station work. It does **not** establish how large model execution is — that is unmeasured (see
the 2026-09-02 correction above), and the earlier "~9 % of the cycle" figure has been withdrawn.

**Deployment context:** the owner intends to onboard the full BAFU station set with a first ML
model on this mac mini. That set is **~170 LINDAS-available BAFU gauges** (`docs/v0-scope.md:9`,
`docs/architecture-context.md:10`), and ~170 is the n this plan sizes for. If the owner's actual
first onboarding is deliberately smaller than the documented v0 ceiling, say so here and T1's n=170
point becomes headroom evidence rather than the bar — but the plan does not assume that.
