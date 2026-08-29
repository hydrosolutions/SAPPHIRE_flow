---
status: READY
created: 2026-08-29
plan: 216
title: M-A11 — re-evaluate the diurnal timing error against the ECMWF IFS forecast, not ERA5-Land
scope: Establish whether the ~12 h monsoon diurnal phase error measured against ERA5-Land is also present in the ECMWF IFS forecast, using TIGGE via ECDS — the only source overlapping the 2020-2025 gauge record. Ends in a screening verdict. NOT a correction, NOT a bias correction, NOT an operational feed, NOT a multi-centre study.
depends_on: [184, 193, 205, 209]
blocks: [M-DEC]
source: docs/design/dhm-precipitation-phase2-recommendation.md § 2 (Use 1)
---

# Plan 216 — M-A11 IFS diurnal timing re-evaluation

## Status

**READY.** Owner confirmed 2026-08-29, after two independent review rounds (over-engineering + slim).

## ⛔ PROPORTIONALITY IS BINDING

**Three actions: retrieve, compare, recommend.** The comparison machinery already exists
(`ma6_pairs.py`, `ma7_profiles.py`, `data/dhm_precip/figures/era5-timing/era5_gauge_timing_figure.py`)
and is reused. No new framework, abstraction layer, config surface or file format. **Adding length is a cost.**

## ⛔ PER-RUN SCOPE (binding)

- **A worktree carries no gitignored files.** `data/` is ignored, so a fresh worktree has none of this
  track's inputs. **First:** `mkdir -p <wt>/data/dhm_precip && cp /Users/bea/Documents/GitHub/SAPPHIRE_flow/data/dhm_precip/* <wt>/data/dhm_precip/`
  (workbook, `station_coordinates.csv`, `era5_land_provenance.json`). Without it the loader fails with
  a typed error that reads like a regression.
- ✅ **Network: ECDS only** (`https://ecds.ecmwf.int/api`), credentials from `~/.cdsapirc`. Volume is
  small — the 9-step sample was 6.9 KB, so a JJAS season is a few MB. No projection gate needed.
  ⛔ **Never call the Copernicus CDS** — a wrong root once triggered a live ERA5-Land download on this
  track.
- ✅ Write raw and extracted TIGGE under **`data/dhm_precip/tigge/`** (gitignored).
  ⛔ Never write under `data/dhm_precip/era5_land*`; ⛔ never delete anything under any `points/` tree.
- **Iterate on `tests/unit/scripts/` (~75 s)**, not the full unit suite (~8 min) — running the full
  suite as an iteration loop has stalled eight subagents on this track. Full suite **once** at the end,
  measuring your own baseline.
- **Hold at PR.** Every code commit bumps patch (`uv run bump-my-version bump patch`) folded into the
  real commit. Stage by explicit path, never `git add -A`. Never merge, never tag on a branch.

## Why this plan exists

M-A9's Use 1 (diurnal timing correction) is **PROMISING, NOT DEPLOYABLE**, and **we measured the wrong
product**. ERA5-Land is a reanalysis whose `total_precipitation` descends from a **frozen 2016-era IFS
convection cycle**; the operational forcing is current IFS, and convective timing is exactly what
intervening cycles change. So we hold a large, robust phase error in a product we do not fly, and no
measurement of the one we do.

- **If IFS shares the error** → finer-resolution correction work is justified.
- **If IFS appears aligned** → the proposed ~12 h correction is not.

## Decisions

- **D1 — The blocker is OVERLAP, not archive depth.** The gauge record ends **2025-12-31 12:00**; the
  recap gateway API (rolling ~2 months) and the S3 bucket (2026-03-01 → 2026-05-28) both begin in 2026,
  so **neither has a single hour to pair against**. TIGGE runs from **October 2006**, covering all of
  2020–2025. ⛔ Do not re-propose the local sources unless DHM supplies 2026 gauge data.

- **D2 — One monsoon season: JJAS 2025.** TIGGE makes six years reachable, which changes the constraint
  from availability to effort. One JJAS is 122 days ≈ 3,000 station-days — far more than a **~12 h**
  displacement needs. ⛔ Retrieve one season, answer the question, *then* decide whether more buys
  anything. A six-year pull before the first result is now the *easy* mistake.

- **D3 — Stratify by LEAD TIME.** Early leads inherit the analysis; later leads relax onto the model's
  attractor. ⛔ Pooling would average a real lead-dependence into one misleading number.

- **D4 — TIGGE's 6-hourly step is a TEMPORAL-RESOLUTION BOUND of ±3 h — not a statistical uncertainty.**
  4 points/day separates alignment from near-antiphase (gauge ≈00:45 NPT vs model ≈13:00 NPT fall in
  different 6 h bins) but pins phase no finer than ±3 h. ⇒ **This plan screens; it cannot design a
  correction.** ⛔ Report the bound beside every result: an *unresolved* error must never be read as
  *no* error.

- **D5 — Reuse the phase estimator; RETIRE the season-year bootstrap and its 24-bin test.** Same
  pairing, normalisation (each station's cycle sums to 100 %), circular lag estimators, bands, NPT
  display, and sign convention (**positive = model later than gauge**) — so the IFS and ERA5-Land
  numbers are comparable by construction. 🔴 **But three things in
  `era5_gauge_timing_figure.py:244–290` break on one season, and they are separate code paths:** the
  year resampling (`:249`, `:271`) would draw a one-element `[2025]` bootstrap that resamples the same
  season and reports **false zero uncertainty**; the adequacy thresholds (`:282`) exclude stations with
  < 3 season-years or < 3,000 hours, which drops **every** station; and `nb.min() == 0` (`:275`)
  discards any replicate with an empty bin, while 6-hourly data populates only **4 of the 24** bins.
  ⇒ **Retire bootstrap uncertainty, the arc exclusion and the 24-bin completeness test for this
  screen**, and say so in the output. The **±3 h resolution bound (D4) dominates any interval a
  bootstrap could produce here**, so the interval would add no information while inviting a false-zero
  reading. Report point estimates with their `n`. ⛔ Fixing only the thresholds is the trap: it leaves
  the bootstrap silently degenerate.

- **D6 — TIGGE source contract. Every property below was MEASURED 2026-08-29, not read.**
  - **Route:** `cdsapi` → `https://ecds.ecmwf.int/api`, dataset **`tigge-forecasts`**. Migrated to ECDS
    **2026-05-27**, so ⛔ pre-migration recipes are dead. An existing Copernicus CDS key works. **Two**
    acceptances are required, both seen as hard 403s: ECDS *Terms of use* and the *TIGGE licence*.
  - **Grid** 0.5°, lat 31.0→26.0, lon 80.0→89.0 (11×19) — exactly `STUDY_AREA`.
  - **Steps 6-hourly**, initialised **00/12 UTC only**.
  - **`stepType: accum`** — accumulated from forecast start (GRIB attribute *and* monotonicity across
    all 9 steps). Deaccumulate.
  - 🔴 **Units are `kg m**-2` = MILLIMETRES. ERA5-Land and gateway `tp` are METRES.** A missing ×1000
    reads as a plausible wet bias, not a crash. **Assert the unit from the file's own attribute; never
    infer it from the source name.**
  - ⛔ **Research only: a measured ~48 h embargo** (today−1 d fails; −2 d and −3 d succeed, reproduced).
    Nothing here may become an operational dependency, for any centre — this is not a licence artefact.
  - **Attribution** to ECMWF and acknowledgement of TIGGE are licence conditions on any output.

- **D7 — The gauge timezone rides along; it does not gate.** `Time (UTC)` is corroborated three ways
  (2,415 stamps at exactly `:15` — what an on-the-hour NPT stamp becomes under −5:45; DHM's
  PERIOD_ENDING answer; the Pyramid cross-check) but unproven. An NPT reading shifts every offset
  uniformly **+6 h** while leaving the **between-band contrast invariant**. Report both readings.

## Tasks

Three tasks, three phases.

### T1 — retrieve one season (depends: nothing)
**In:** ECMWF **control (`type: cf`) only** `tp` for **JJAS 2025** over `STUDY_AREA`, via the D6 route.
⛔ **Control-only is deliberate** — this is a phase screening, and pooling or averaging 51 members
would change the estimand without being specified anywhere. Perturbed members are out of scope.
- Deaccumulate to 6-hourly increments; ⛔ **assert `kg m**-2` from the file attribute** and convert once.
- Extract to the 26 station points with the **same nearest-cell operator** the ERA5-Land work locked.

**Out:** any comparison; any other centre; any date outside JJAS 2025.
**Verify:** the series is complete on its own 6-hourly axis with gaps carried, never filled; a stated
station-day count; and the unit assertion has a test that **fails against a metres-valued file** — a
1000× error is the one defect here that would look like a result.

### T2 — the comparison (depends: T1)
**In:** run the timing comparison with TIGGE-IFS in ERA5-Land's place, per D5, stratified by lead time
(D3), reporting the ±3 h bound (D4) and both timezone readings (D7).
🔴 **A lead band is a COMPLEMENTARY STEP SET covering all four 6-hourly clock positions, never a single
exact lead.** With 00/12 UTC initialisations, one exact lead samples only **two** clock positions per
day — too few to fit a diurnal cycle at all. Define e.g. `D+1 = steps 24/30/36/42` from the 00 UTC run
(valid 00/06/12/18 the next day), `D+2 = 48/54/60/66`, and so on. ⛔ Where two initialisations yield a
forecast at the same valid time, take the **most recent initialisation within the band**,
deterministically — never both, never an average.
🔴 **Aggregate the GAUGES into matching complete 6-hour period-ending windows before pairing.**
`ma6_pairs.py:707` joins on exact timestamps and the existing figure bins 24 *hourly* values; pairing a
6-hour IFS total against one hourly gauge value would be wrong. ⛔ A window with any missing gauge hour
is dropped, never partially summed.
**Out:** any correction; any magnitude or bias claim — **phase only**.
**Verify:** the ERA5-Land figures regenerate unchanged from the same code path (proving reuse, not a
fork); every number carries its `n` station-days and lead band.

### T3 — the recommendation (depends: T2)
**In:** state whether the ECMWF IFS forecast shares ERA5-Land's phase error, at what magnitude, with
what lead-dependence, on how many station-days.
⛔ **Bound the verdict to what ±3 h supports.** A repeated half-day displacement justifies **GO on
finer-resolution correction work**; apparent alignment justifies **NO-GO on the proposed ~12 h
correction**. ⛔ It does **NOT** support "no timing correction is needed" — D4 forbids reading an
unresolved error as no error. "The sample is too thin to say" is a permitted conclusion.
**Out:** implementing anything.
**Verify:** every figure traces to T2's output or a named command, and carries the ECMWF attribution
and TIGGE acknowledgement (D6).

```json
{
  "phases": [
    {"id": "phase-1", "tasks": ["T1"], "parallel": false},
    {"id": "phase-2", "tasks": ["T2"], "parallel": false, "depends_on": ["phase-1"]},
    {"id": "phase-3", "tasks": ["T3"], "parallel": false, "depends_on": ["phase-2"]}
  ]
}
```

## Exit

A measured answer to "does the ECMWF IFS forecast share ERA5-Land's ~12 h monsoon phase error?", with
its lead-dependence, its ±3 h resolution bound and its station-day count; and a screening verdict
bounded by D4. **Owner decides (M-DEC).**

## Non-goals

Implementing any timing or bias correction · any magnitude claim (phase only) · any operational
dependency (D6: 48 h embargo) · **any centre other than ECMWF** · perturbed members · a multi-year
climatology (D2) · a second implementation of the diurnal comparison (D5) · elevation-band weights,
the DHM 2026 gauge request, and the free-alternatives escalation ladder — all of which belong to
whatever M-DEC authorises, not to this measurement.
