---
status: COMPLETE
created: 2026-08-27
plan: 203
title: Forecast-cycle scaling to the full BAFU set (148 stations) — measure the two costs that actually grow
scope: One measurement of how basin extraction and model execution scale with station count, and an owner decision on whether any follow-up work is warranted. No optimisation is authorised by this plan — it exists to make the next plan grounded. Explicitly NOT in scope: any decision about parallelising the 441.2 s NWP phase — the body establishes that figure is composite (STAC walk + transfer + parse) and cannot support such a decision.
depends_on: []
blocks: []
source: Measured on the mac mini 2026-08-27 while trialling `forecast_combination_strategy = pooled` (PR #214)
note: The filename keeps its original `-to-100-stations` slug so existing references stay valid; the target n is 148 — what is actually onboarded on the mini (measured 2026-09-02); the v0 ceiling is ~170 (`docs/v0-scope.md:9`).
---

# Plan 203 — forecast-cycle scaling to the onboarded BAFU set (148 stations)

## Status

**COMPLETE 2026-09-04.** T1 measured in production; T2 decided — target met, plan closed.

## ⛔ Proportionality — this plan MEASURES, it does not optimise

The owner's target: **the full BAFU station set, 4×/day, comfortably.** Measured on the mini
2026-09-02, that set is **148 stations** — 148 basins, 148 stations, all 148 carrying model
assignments. The documented v0 ceiling is ~170 (`docs/v0-scope.md:9`,
`docs/architecture-context.md:10`), but 148 is what is actually onboarded, so **148 is the n this
plan sizes for** and every sample point is real — no synthetic or duplicated geometry is needed.
(An earlier draft used a round 100, well short of the real set.) The instinct after
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
n=2 by construction, so its share here says nothing about the cycle at n=148.

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

1. **Extraction — 6.48 s/station at n=2.** If that is linear, 148 stations ≈ **959 s**, which
   would overtake the fixed NWP phase and become the dominant cost. If most of it is instead
   per-*call* overhead that does not repeat per basin — `extract()` is handed an already-open
   dataset and begins by materialising its coordinates (`mesh_basin_extractor.py:51`, `:101`), it
   does **not** open the zarr — with a cheap per-basin mask on top, then 148 stations might cost far
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
 959 s  extraction                     (148 × 6.48 s — ASSUMES linearity)
-----
≈ 1408 s ≈ 23.5 min per cycle   ← this scenario only; excludes everything below
     ?    model execution                (unmeasured)
     ?    assembly, artifact load, FI adapter, ensemble fan-out, QC, persistence (unmeasured)
```

**This is neither a lower nor an upper bound.** It is not a floor: if extraction is *sub*-linear —
which this plan holds open as plausible — **this term** is smaller than shown (though the total need
not be, since the omitted work only adds). It is not an estimate either: it omits every other
per-station operation, so it could equally land far above. It is one
point in a range whose width is currently unknown, and it is shown only to demonstrate that the
question is live.

That is what changed from the earlier n=100 draft. There, the same arithmetic gave ~24 min against
a 30-min bar and made T1 look like a formality. At the real deployment size, **one plausible
scenario already lands within ~4 minutes of the bar before a single unmeasured term is counted** —
so the measurement can genuinely go either way. Sub-linear extraction — made plausible by the work
inside `extract()` that does **not** repeat per basin (materialising `cell_points` from the
already-open dataset it is handed, `mesh_basin_extractor.py:101`) — would lower *this* term, but it
does **not** imply the true total falls below 23.5 min: the unmeasured per-station work can only add,
and its size is unknown. Conversely, if extraction is linear or worse the bar is in doubt before
models are even counted. Neither direction settles the cycle; that is the point of measuring.

**Where the 30-minute bar comes from:** it is ~8 % of the 6-hour cadence, i.e. the point at which a
cycle still finishes with >5× headroom before the next one starts. It is a judgement call, not a
measured constraint; T2 may revise it with the owner, but it must not be moved *after* seeing T1's
number.

## Tasks

**T1 — Measure the two scaling curves.** *(No production change.)*
*Scope (in):* run basin extraction against the ALREADY-ARCHIVED zarr
(`/data/nwp_grids/icon_ch2_eps/*.zarr`, no re-download) for **n = 2, 10, 25, 50, 100, 148**, timing
each; separately time `predict` for one model across the same n. Report both curves and the fitted
per-station marginal cost. Per `CLAUDE.md` § Ad-hoc Analyses this is a **heredoc**, not a committed
script.

*Geometry — use REAL basins, do not synthesise.* Extraction cost is driven by polygon complexity
(the `predicate="within"` sjoin at `mesh_basin_extractor.py:206`), so an ad-hoc synthetic polygon
would understate it and the fitted 6.48 s/station would be an artefact of the test data. Draw the
polygons from the production basin store instead — `BasinStore.fetch_all_basins()`
(`src/sapphire_flow/store/basin_store.py:58`) returns the same `Basin` objects
(`src/sapphire_flow/types/basin.py:17`, a shapely MultiPolygon from the delineation package) that
`run_forecast_cycle.py:2504-2509` feeds into `extract()`. The mini holds **148 real basins**
(measured 2026-09-02), which is exactly the target n, so **every sample point uses real geometry**
and no duplication or synthesis is required. If a future run finds fewer, top up by duplicating
real polygons under synthetic `StationId`s — never by generating a shape — and record how many of
the n were distinct.

*Why 148 is measured, not extrapolated:* the largest previously planned sample was n=50, and a
curve that looks linear from 2→50 can still bend by 148 (e.g. RAM pressure from holding many basin
geometries and grid slices at once). Measuring the target n directly costs almost nothing and
removes the extrapolation risk from the one number T2's decision hinges on.

*Model for the predict curve:* use **`linear_regression_daily`** — 143 of the 148 stations have a
trained station-scoped artifact (measured 2026-09-02; `climatology_fallback` also has 143,
`nwp_rainfall_runoff` 142). For the 5 stations without one, reuse another station's artifact and
say so in the write-up; that is a 3 % effect on the curve.

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
`predict` vs n, for n = 2, 10, 25, 50, 100, 148), the fitted per-station marginal cost of each, and
a one-paragraph verdict answering exactly one question: **do these two costs grow linearly, and
what do they contribute at n=148?**

T1 **may not** conclude "the 148-station cycle is under 30 min". It does not measure enough of the
cycle to say that, and an earlier version of this exit criterion wrongly claimed it could. The two
honest verdicts are:

**The comparison is against the KNOWN SUBTOTAL, not the curves alone.** The fixed phases already
cost **448.9 s** (441.2 s NWP + 7.7 s archive), which is ~7.5 min of the 30-min bar before any
per-station work. So the quantity to compare is `448.9 s + extraction(148) + predict(148)`. An
earlier version of this rule compared the two curves in isolation, which would wrongly return
INCONCLUSIVE whenever the curves fit inside 30 min but the subtotal does not.

- **DECISIVE:** `448.9 s + extraction(148) + predict(148)` **already exceeds** the 30-min bar → the
  target is missed regardless of the unmeasured terms, since those can only add. T2 proceeds to a
  follow-up; no further measurement needed.
- **INCONCLUSIVE:** that subtotal comes in **under** the bar → **this does not establish that the
  cycle fits.** The unmeasured per-station work could still close the remaining gap. T2 must then
  decide whether to accept the unquantified remainder or commission a second measurement covering
  the rest of the per-station path — `run_station_forecast()` through to the flow's persistence and
  combination step, which sits outside that function (`run_forecast_cycle.py:3143`), so a second
  measurement would have to span both. Report the remaining headroom in seconds, so T2 can judge how
  much unmeasured work would have to exist to consume it.

Record the outcome in the write-up as **a line of its own, at the start of the line**, reading
exactly `T1 VERDICT: DECISIVE` or `T1 VERDICT: INCONCLUSIVE` — nothing else on that line. T2's
verification command matches line-anchored, so the placement is load-bearing, not cosmetic (these
same strings appear in this paragraph as prose and must not count).

*Verification:* `uv run python -c "import geopandas, xarray; print('T1 deps ok')"` — T1 itself is a
heredoc analysis run by hand on the mini, not a committed script or test, so there is no suite to
green. This command only confirms the analysis environment resolves before the run.

**T2 — Decide, and only then draft.** T2 consumes exactly the two verdicts T1 is permitted to
return — it must **not** ask T1 whether "the target is met", because T1 cannot establish that:

- **T1 returned DECISIVE** (`448.9 s + extraction(148) + predict(148)` **exceeds** the bar) → the
  target is missed regardless of the unmeasured terms, which can only add. Draft a follow-up naming
  the specific phase to fix, with T1's curves as its baseline.
- **T1 returned INCONCLUSIVE** (that same subtotal comes in **under** the bar) → this does **not**
  mean the cycle fits. The owner chooses: accept the unquantified remainder and close the plan, or
  commission the second measurement of the rest of the per-station path described in T1's exit.
  T1's reported headroom in seconds is the input to that judgement.

Either way the decision is the owner's and is recorded here. Note the ~23.5 min scenario above sits
within ~4 min of the bar before any unmeasured term is counted, so neither branch is hypothetical —
T2 must wait for T1 rather than pre-judging.
*Exit:* an owner decision recorded here as a line of its own beginning `T2 DECISION:`, naming which
verdict T1 returned and what the owner chose.

### T2 DECISION — recorded 2026-09-04

T2 DECISION: accept the unquantified remainder and CLOSE. T1 returned INCONCLUSIVE; the owner accepts that rather than commissioning a second measurement of the rest of the per-station path.

**Measured in production** (148 stations, not a projection): full end-to-end cycles of **25.5 and
26.7 min** against the 30-minute bar — target met with ~11 % margin, ~10 % duty cycle against the
6-hour cadence. Extraction is linear at **7.78 s/station** (the plan projected 6.48, so 20 %
optimistic), 1151.6 s at n=148.

**Accepted explicitly:** the ~3.3 min of margin is thin, and the per-station work T1 did not measure
(input assembly, artifact load, FI adapter, ensemble fan-out, QC, persistence) sits inside it,
unquantified. If station or model count grows materially, revisit rather than assume this still holds.

**Carried forward:** the zarr/replay path is **~14x faster** than production (0.55 vs 7.78 s/station),
so any future scaling measurement MUST use the GRIB path — a replay-path number is not evidence about
production.
*Verification:* `uv run python -c "import pathlib,re,sys; t=pathlib.Path('docs/plans/203-forecast-cycle-scaling-to-100-stations.md').read_text(); v=re.findall(r'(?m)^T1 VERDICT: (?:DECISIVE|INCONCLUSIVE)$', t); d=re.findall(r'(?m)^T2 DECISION: \S', t); sys.exit(0 if len(v)==1 and len(d)==1 else 1)"`
— matches **line-anchored**, so the same strings appearing in prose above do not count. Passes only
once T1 has recorded exactly one verdict line **and** T2 has recorded exactly one decision line,
which together are T2's exit. (An earlier version tested substring presence anywhere in the file;
because both verdict strings occur in this doc's own prose it could never pass — it was
unsatisfiable, not merely pending.)


## T1 RESULT — measured on the mac mini 2026-09-02/03

T1 VERDICT: INCONCLUSIVE

Run in production, not synthetically: the 111 `onboarding` stations were flipped to
`operational` on 2026-09-02 (owner-authorised), taking the cycle from 37 to 148 stations, and
the next cycles measured themselves.

### The two curves

**Extraction (production, GRIB-backed — the real path).** One call per cycle:

| stations | duration | per station | source |
|---|---|---|---|
| 37 | 272.6-304.8 s | ~7.6 s | cycles 2026-09-02 00/06/12 |
| **148** | **1151.6 s** | **7.78 s** | `extraction.completed duration_ms=1151557.8 stations_extracted=148 stations_skipped=0`, 2026-09-03 06:25 |

**Linear, and the plan's projection was close** — it assumed 6.48 s/station (959 s at n=148);
the truth is 7.78 s/station (1152 s), 20 % higher. 0 stations skipped, so all 148 real basins
extracted cleanly.

**Model `predict`: still not measured separately.** It is subsumed in the full-cycle number below,
which turned out to be available directly and made the separate curve unnecessary.

### 🔴 The zarr path is NOT representative — quantified

The zarr/replay measurement this plan authorised gives **0.55 s/station** (82.9 s at n=148,
clean straight line over n=2,10,25,50,100,148). Production on the same code and the same
ICON-CH2-EPS grid gives **7.78 s/station**. **The replay path is ~14x faster.**

This confirms, by measurement, the review finding recorded as a scope caveat: `extract()` receives
an already-open zarr in replay but a freshly-parsed GRIB-backed dataset in the live cycle, and the
backend dominates. **Any future scaling work must measure the GRIB path.** A zarr-path number is
not evidence about production.

### Why the verdict is INCONCLUSIVE, and what is known anyway

By this plan's own rule the comparison is `448.9 s + extraction(148) + predict(148)` against the
30-minute bar. That subtotal is `448.9 + 1151.6 = 1600.5 s = 26.7 min` -- **under** the bar, which
is the INCONCLUSIVE branch: the measured terms fit, but T1 does not measure enough of the cycle to
claim the whole thing fits.

**However, production answered the bigger question directly, which T1 was barred from inferring.**
Full end-to-end cycle wall-clock at 148 stations, from Prefect:

| cycle | stations | duration |
|---|---|---|
| 2026-09-03T00 | 148 | **25.5 min** |
| 2026-09-03T06 | 148 | **26.7 min** |
| (2026-09-02T00/06/12, before the flip) | 37 | 10.7 / 12.0 / 12.7 min |

So a full 148-station cycle runs **25.5-26.7 min against a 30-minute bar** -- it fits, with about
**3.3 min (11 %) of margin**, and a ~10 % duty cycle against the 6-hour cadence. Going 37 -> 148
stations (4x) roughly **doubled** cycle wall-clock (12 -> 26 min), consistent with extraction being
linear while the 448.9 s fixed phase is not.

This is direct evidence, not a projection -- but it is **not** what T1 was authorised to produce,
so the verdict above stays INCONCLUSIVE and T2 owns what to do with it.

### 🪤 Found while measuring — NOT caused by the flip

Cycles at 12:00 and 18:00 UTC end in `forecast_cycle.nwp_fetch_failed_aborting` after ~7 min,
having logged `nwp.cycle_fallback_used fallback_reason=too_recent`. They report COMPLETED at flow
level but produce no forecasts. **This predates the station flip** (2026-09-01T18 took 6.0 min the
same way, at 37 stations), and is the known NWP-latency issue that Plan 213 (105 -> 180 min) exists
to fix -- that PR is implemented but HELD. Roughly half of all cycles are affected. Not in this
plan's scope; recorded so the 26.7 min figure is read correctly (it is the cost of a cycle that
actually runs).

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
model on this mac mini. As measured 2026-09-02 that set is **148 onboarded stations**, against a
documented v0 ceiling of ~170 (`docs/v0-scope.md:9`, `docs/architecture-context.md:10`). The plan
sizes for the real 148. If the deployment later grows toward the ceiling, T1's n=148
point becomes headroom evidence rather than the bar — but the plan does not assume that.

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
