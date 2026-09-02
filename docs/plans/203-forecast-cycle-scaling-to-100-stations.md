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

In the observed **2-station** cycle the fixed NWP phase dominated wall clock, and **that cost does
not grow with station count** — it is the same grid every cycle. The per-station terms are tiny at
n=2 by construction, so its share here says nothing about the cycle at n=170.

| phase | duration | scales with | evidence |
|---|---|---|---|
| NWP STAC walk + transfer + parse | **441.2 s** | nothing — same grid every cycle | `nwp.fetch_completed duration_ms=441236 file_count=484 total_bytes=2874343206` |
| zarr archive | 7.7 s | nothing | `nwp.archive_completed duration_ms=7679.6 size_bytes=1748057101` |
| basin extraction | **12.95 s / 2 stations → 6.48 s/station** | **stations** | `extraction.completed duration_ms=12951.1 stations_extracted=2` |
| model execution | **NOT MEASURED** | — | see the correction below |

### 🔴 The 425.0 s / 465.5 s pair is UNUSABLE — do not derive anything from it

An earlier version of this plan read the pair `425.0 s (2 forecasts) → 465.5 s (12)` as a cycle
total before and after, and attributed the +40.5 s first to model execution and then (in a later
revision) to combination/persistence overhead. **Both attributions are withdrawn — the second was
no better founded than the first.**

The two numbers do not reconcile with the rest of the table: **the NWP phase alone is 441.2 s,
which is longer than the 425.0 s supposedly-complete earlier cycle.** A cycle cannot be shorter
than one of its own phases. So the pair either measures something narrower than a full cycle, or
the two runs had materially different NWP phases — the source logs do not say which. Until that is
resolved, +40.5 s is an **unexplained delta between two runs of unestablished composition**, and no
per-forecast or per-phase cost may be derived from it.

The arithmetic was independently wrong too: the plan computed 40.5/12 = 3.375 s by dividing by the
*ending* count rather than the increment (40.5/10 = 4.05 s). Both figures are void regardless,
because the delta has no established cause.

**2.87 GB in 441.2 s ≈ 6.5 MB/s**, but note the 441.2 s is **STAC walk + transfer + GRIB parse**,
not a network-download measurement: `meteoswiss_nwp.py` starts the timer before
`_fetch_grib_files()` and parses every GRIB before emitting `nwp.fetch_completed`. So "6.5 MB/s" is
not a throughput figure and cannot by itself decide whether the transfer is worth parallelising.
484 files were fetched against **24 079 `nwp.variable_skipped`** events — those are STAC metadata
items inspected and rejected **before** asset download (filtering is client-side but pre-download),
not variables downloaded and thrown away.

### 🔴 Correction (2026-09-02): model cost is UNMEASURED, not "nearly free"

An earlier version of this plan claimed *"model count is nearly free — going from 1 model to 6 per
station cost +9 % wall clock"*. **Withdrawn.** The trial that produced it switched
`forecast_combination_strategy` PRIMARY → POOLED, and **both modes already execute every assigned
model**: `run_station_forecast()` (`src/sapphire_flow/services/run_station_forecast.py:814`) is a
thin wrapper that calls `run_all_station_forecasts()` and returns only
`multi.results[multi.primary_model_id]` (`:839`); that runner iterates every assignment with **no
break on success** (`:605`). The in-code comment at `run_forecast_cycle.py:3101` ("single model with
fallback chain") is **stale and misleading** — it describes intent, not behaviour. 🪤 Anyone reading
that file will be misled by it.

So the 1-model vs 6-model comparison never happened: model count was identical in both runs.
**There is therefore no evidence either way on model scaling** — treat it as an open number, neither
cheap nor expensive. Any claim that parallelising model execution addresses only ~9 % of the cycle
is unsupported. (What the delta between those two runs *does* represent is separately unusable —
see the section above.)

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

### One SCENARIO — not a floor, not an estimate

**If** extraction scales linearly from its n=2 point — a specific assumption this plan explicitly
does not yet accept — then:

```
 441 s  STAC walk + transfer + parse   (fixed)
   8 s  archive                        (fixed)
1101 s  extraction                     (170 × 6.48 s — ASSUMES linearity)
-----
≈ 1550 s ≈ 26 min per cycle   ← this scenario only; excludes everything below
     ?    model execution                (unmeasured)
     ?    assembly, artifact load, FI adapter, ensemble fan-out, QC, persistence (unmeasured)
```

**This is neither a lower nor an upper bound.** It is not a floor: if extraction is *sub*-linear —
which this plan holds open as plausible — the true total is **below** 26 min. It is not an estimate
either: it omits every other per-station operation, so it could equally land far above. It is one
point in a range whose width is currently unknown, and it is shown only to demonstrate that the
question is live.

That is what changed from the earlier n=100 draft. There, the same arithmetic gave ~24 min against
a 30-min bar and made T1 look like a formality. At the real deployment size, **one plausible
scenario already lands within ~4 minutes of the bar before a single unmeasured term is counted** —
so the measurement can genuinely go either way. If extraction proves sub-linear — which the
per-cycle work inside `extract()` that does not repeat per basin (materialising `cell_points` from
the already-open dataset, `mesh_basin_extractor.py:101`) makes plausible — headroom reappears; if
it is linear or worse, the bar is in doubt before models are even added.

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
existing basins, it does not create any). **Also out — every other per-station operation:** input
assembly, assignment/threshold/baseline reads, model-state reads, artifact lookup and
deserialisation, coverage/input-quality processing, ensemble fan-out, forecast construction,
rating-curve binding, QC, alert checking, persistence and combination
(`run_station_forecast.py:357`, `:519`; `run_forecast_cycle.py:3143`, `:3587`). T1 does not measure
any of these.

*Note on what "time `predict`" includes.* For a model discovered through the FI adapter, the public
`predict()` **already performs the adapter's validation and conversion** internally
(`adapters/forecast_interface.py:939`, `:955`; `services/model_registry.py:92`). So that work is
**in** T1's second curve by construction — it is not on the excluded list above, and T1 must not
claim to have isolated "pure" model runtime. Name the exact model measured, and state whether it
came through the FI adapter, so the curve can be read correctly.

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
  whether to accept the risk or commission a second measurement covering the rest of the per-station
  path — `run_station_forecast()` through to the flow's persistence and combination step, which sits
  outside that function (`run_forecast_cycle.py:3143`), so a second measurement would have to span
  both.

Record which of the two it is in the write-up, using **exactly one** of the literal strings
`T1 VERDICT: DECISIVE` or `T1 VERDICT: INCONCLUSIVE` — T2's verification command checks for
precisely one of these, so the wording is load-bearing, not cosmetic.

*Verification:* `uv run python -c "import geopandas, xarray; print('T1 deps ok')"` — T1 itself is a
heredoc analysis run by hand on the mini, not a committed script or test, so there is no suite to
green. This command only confirms the analysis environment resolves before the run.

**T2 — Decide, and only then draft.** T2 consumes exactly the two verdicts T1 is permitted to
return — it must **not** ask T1 whether "the target is met", because T1 cannot establish that:

- **T1 returned DECISIVE** (the two measured curves alone exceed the bar at n=170) → the target is
  missed regardless of the unmeasured terms. Draft a follow-up naming the specific phase to fix,
  with T1's curves as its baseline.
- **T1 returned INCONCLUSIVE** (the two curves come in under the bar) → this does **not** mean the
  cycle fits. The owner chooses: accept the unquantified remainder and close the plan, or commission
  the second measurement of the rest of the per-station path described in T1's exit.

Either way the decision is the owner's and is recorded here. Note the ~26 min scenario above sits
within ~4 min of the bar before any unmeasured term is counted, so neither branch is hypothetical —
T2 must wait for T1 rather than pre-judging.
*Exit:* an owner decision recorded here, naming which verdict T1 returned.
*Verification:* `uv run python -c "import pathlib,sys; t=pathlib.Path('docs/plans/203-forecast-cycle-scaling-to-100-stations.md').read_text(); sys.exit(0 if ('T1 VERDICT: DECISIVE' in t) ^ ('T1 VERDICT: INCONCLUSIVE' in t) else 1)"`
— passes only once T1's write-up has recorded exactly one of the two permitted verdicts in this
doc, which is the precondition for T2's decision being meaningful.

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
