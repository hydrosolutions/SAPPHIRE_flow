---
status: DRAFT
created: 2026-08-29
plan: 216
title: M-A11 — re-evaluate the diurnal timing error against operational IFS, not ERA5-Land
scope: Establish whether the ~12 h monsoon diurnal phase error measured against ERA5-Land is also present in the ECMWF IFS forcing SAPPHIRE actually flies, using the recap gateway's own IFS archive where it reaches and a documented external archive where it does not. Ends in a GO/NO-GO on building a sub-daily timing correction. NOT a correction implementation, NOT a bias correction, NOT an IMERG comparison.
depends_on: [184, 193, 205, 209]
blocks: [M-DEC]
source: docs/design/dhm-precipitation-phase2-recommendation.md § 2 (Use 1)
---

# Plan 216 — M-A11 IFS diurnal timing re-evaluation

## Status

**DRAFT.** Not for implementation until the owner confirms.

## ⛔ PROPORTIONALITY IS BINDING

**This is a measurement, not a system.** The comparison machinery already exists
(`ma6_pairs.py`, `ma7_profiles.py`, `data/dhm_precip/figures/era5-timing/era5_gauge_timing_figure.py`)
and is reused, not rebuilt. No new framework, abstraction layer, config surface or file format. If a
decision fits in a sentence it must not become a module. **Adding length is a cost.**

## Why this plan exists

M-A9's Use 1 (elevation-banded diurnal timing correction) is **PROMISING, NOT DEPLOYABLE**. Two
conditions block it, and both have moved:

- **Condition (1) — a stable margin-versus-upper shape difference.** Now supported by two independent
  methods (M-A7's station-equal peak hour in UTC; the 2026-08-29 timing analysis by circular lag in
  NPT). Terai +0.6 h, `1,000–2,000 m` −14.4 h, `≥2,000 m` −11.9 h, stable to ≲1.5 h across five
  sensitivity axes.
- **Condition (2) — band weights from a source other than the profiles being weighted.** The owner has
  identified the source: **elevation bands can be requested from the data gateway**
  (`SpatialType.ELEVATION_BAND`, `types-and-protocols.md:148`; HRU products per Plan 117/120).

⛔ **But a third problem has surfaced that outranks both: we measured the wrong product.**

**ERA5-Land is a reanalysis. The operational forcing is IFS.** ERA5-Land's `total_precipitation` is
interpolated from ERA5, whose convection scheme is a **frozen 2016-era IFS cycle**; operational IFS has
advanced many cycles since, and convective timing is exactly what those cycles change. So the position
today is: **a large, robust, well-characterised phase error in a product we do not fly, and no
measurement at all of the one we do.**

Everything downstream branches on this and cannot be settled by argument:

- **If IFS shares the error** → a timing correction is worth building, and Use 1 is the route.
- **If IFS has largely fixed it** → the correction is wasted work and the M-A9 guardrail alone suffices.

## Decisions

- **D1 — Probe the recap gateway's own IFS archive FIRST, and prefer it over any external source.**
  `EcmwfApiLike.ifs_forecast(variable, run_date, hru_code, ifs_type, member)`
  (`types-and-protocols.md:2048`) takes a **`run_date`**, so retrospective requests are expressible.
  Measured properties: **3-hourly**, ~15 d horizon, cycles **00/06/12/18 UTC**, `tp` in **m**, ~2–3 d
  ingestion lag. ⛔ **Archive DEPTH has never been probed** — every gateway probe on this project tested
  recent dates only. It is the single cheapest fact that determines this plan's shape.
  ⇒ This is the **exact product we fly**, on our own HRUs, with no regridding and no second licence.
  Nothing external can match that fidelity.

- **D2 — One monsoon season is the target, NOT a multi-year climatology.** ⛔ Do not let "archive"
  imply 2020–2025. The question is whether a **~12 h** phase displacement is present. JJAS is 122 days;
  at 26 stations that is ~3,000 station-days, far more than a stable diurnal cycle needs for an effect
  of this size. **Even ~40 days may be decisive.** Scoping this as a six-year retrospective would turn
  a cheap measurement into a data-engineering project and is the main over-reach risk here.

- **D3 — Stratify by LEAD TIME; a forecast's diurnal cycle is not one thing.** Early leads inherit the
  analysis; later leads relax onto the model's own attractor, so phase error typically grows with lead.
  ⛔ Pooling all leads would average a real lead-dependence into a single misleading number. Report per
  lead-day band (e.g. D+1, D+2–3, D+4–5), and state which leads the operational cycle actually consumes.

- **D4 — 3-hourly is the operational resolution; state what it can and cannot resolve.** Eight points
  per day resolves a ~12 h displacement comfortably; it cannot resolve a 1–2 h one. ⛔ Report the
  **detection floor** alongside the result, so "no error found" is never confused with "no error
  resolvable". A 6-hourly external source (4 points/day) has a coarser floor still — see D6.

- **D5 — Reuse the existing comparison, do not rebuild it.** Same pairing (`ma6_pairs.PairedSeries`,
  commonly-retained hours only), same normalisation (each station's 24 h sums to 100 %), same circular
  lag estimators, same JJAS definition, same elevation bands, same NPT display, same sign convention
  (**positive = model later than gauge**). ⛔ A second implementation of the same statistic would make
  the ERA5-Land and IFS numbers incomparable, which is the entire point of the exercise.
  ⚠️ IFS `tp` is **m** and accumulated; ERA5-Land needed deaccumulation and a 01 UTC reset. **Verify
  IFS's accumulation convention on the data before trusting either** — Plan 184 D7's precedent.

- **D6 — External archives are the FALLBACK, ranked, and only if D1's probe comes up short.**
  1. **TIGGE via the ECMWF Data Store (ECDS).** ECMWF ENS from **October 2006**, 13 centres, ~3.5 PB,
     free for research with registration. ⛔ **Access migrated from the old Web-API to ECDS/CDS-API** —
     any pre-2024 recipe is stale. ⚠️ **The precipitation time step is UNVERIFIED and is the deciding
     property**: surface fields are commonly 6-hourly, which halves the detection floor of D4. **Probe
     it before committing.**
  2. **ECMWF Open Data** (`open-data.ecmwf.int`, AWS mirror `ecmwf-forecasts`). Real-time, **rolling
     window**; retention is described as rolling and is **not documented as a fixed period**.
     ⇒ **Unsuitable for retrospective**, usable only for forward accumulation.
  3. **MARS full archive** — licensed, not open. Out of scope unless the owner already holds access.
  ⛔ Record which source produced every number. A figure that mixes gateway IFS and TIGGE ENS without
  saying so is a defect.

- **D7 — Forward accumulation is the guaranteed floor.** The Nepal 12300 feed has ingested IFS daily
  since **2026-08-20** (51 members, 3-hourly, ~14.75 d). Whatever the archive probe finds, this keeps
  growing through the current monsoon. ⇒ Even the worst case yields a thin but real answer by end of
  September 2026; it simply is not available *today*.

- **D8 — Elevation-band weights are requested from the gateway, and are the ONLY new input.**
  `SpatialType.ELEVATION_BAND` already exists; HRU/band products come from the gateway (Plans 117/120).
  ⇒ This closes M-A9 Use 1's condition (2) **independently of the profiles being weighted**, which is
  precisely what that condition demanded. ⛔ Do not derive weights from M-A7's profiles under any
  circumstance — that was the original defect.

- **D9 — The gauge-timezone question rides along, it does not gate.** `Time (UTC)` is corroborated three
  ways (2,415 stamps at exactly `:15`, which is what an on-the-hour NPT stamp becomes under −5:45; DHM's
  PERIOD_ENDING answer at M-D3; the Pyramid AWS cross-check) but **not proven**. An NPT reading shifts
  every offset uniformly **+6 h** while leaving the **between-band contrast invariant**. ⇒ Report both
  readings; do not block on DHM's answer.

- **D10 — This plan ends in a GO/NO-GO, not a correction.** Exit is a recommendation with a measured
  basis. ⛔ **No correction is implemented here**, whatever the result. Building one is a separate plan
  that only M-DEC authorises.

## Tasks

Three tasks, three phases. `$ENV` abbreviates the gateway-authenticated environment.

### T1 — probe the archives (depends: nothing)
**In:** measure, do not assume.
- **Gateway depth:** call `ifs_forecast(tp, run_date=…, hru_code=…)` at increasing depth — a recent
  date, then JJAS 2026, 2025, 2024, 2023, 2022, 2020 — and record for each: HTTP/typed outcome, whether
  data materialises, the step interval actually returned, member availability, and units.
  ⛔ **Never hit CDS.** ⛔ Read-only; write nothing to the gateway.
- **TIGGE (only if the gateway falls short):** confirm via ECDS the **precipitation time step**,
  horizontal resolution, date coverage, and the licence/registration required.
- **Band weights:** confirm the gateway serves elevation-band geometry/areas for the DHM HRUs (D8).

**Out:** any comparison, any figure.
**Verify:** a probe report table — source × date × available × step × units — every cell from a real
call, none from documentation. ⛔ A documented capability is not a measured one; this track has been
wrong about gateway behaviour from documentation **twice** (the 31-day lying probe, and IFS
"available at run time").

### T2 — the comparison (depends: T1)
**In:** run the M-A7/timing comparison with IFS in ERA5-Land's place, per D5, stratified by lead time
(D3), reporting the detection floor (D4) and both timezone readings (D9). Same 26 stations, same JJAS,
same bands, same estimators. Report per band: gauge peak hour, IFS peak hour, circular lag, `n`
station-days, and the **source of every number** (D6).
**Out:** any correction; any magnitude/bias claim — this is phase only.
**Verify:** the ERA5-Land figures regenerate unchanged from the same code path (proving the machinery
was reused, not forked), and the IFS numbers carry their `n` and lead-band. ⛔ An IFS peak hour without
its lead band is a defect.

### T3 — the recommendation (depends: T2)
**In:** state whether operational IFS shares ERA5-Land's phase error, at what magnitude, with what
lead-dependence, and on how many station-days. Then state the consequence for M-A9 Use 1: with
condition (1) supported and condition (2) now sourced from the gateway (D8), does the evidence support
**building** a timing correction — or does it close the question?
⛔ **"IFS does not share the error, so no correction is needed" is a permitted and valuable
conclusion.** So is "the sample is still too thin to say".
**Out:** implementing anything.
**Verify:** every figure traces to T2's output or a named command; the recommendation states what would
have to be true, as M-A9 did.

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

A measured answer to "does the IFS forcing we fly share ERA5-Land's ~12 h monsoon phase error?", with
its lead-dependence, its detection floor, its station-day count and its source named per number; plus a
GO/NO-GO recommendation on building the M-A9 Use 1 timing correction. **Owner decides (M-DEC).**

## Non-goals

Implementing any timing or bias correction · any magnitude/total claim (this is phase only) · IMERG
(Plan 211, and its retrieval is unauthorised) · a multi-year IFS climatology (D2) · a second
implementation of the diurnal comparison (D5) · resolving the gauge timezone with DHM (D9) · changing
ERA5-Land's role as training forcing.
