---
status: DRAFT
created: 2026-08-29
plan: 216
title: M-A11 — re-evaluate the diurnal timing error against operational IFS, not ERA5-Land
scope: Establish whether the ~12 h monsoon diurnal phase error measured against ERA5-Land is also present in the ECMWF IFS forecast, using TIGGE via ECDS — the only source that overlaps the 2020-2025 gauge record. Optionally corroborate across the six CC BY 4.0 centres. Ends in a GO/NO-GO on building a sub-daily timing correction. NOT a correction implementation, NOT a bias correction, NOT an IMERG comparison, NOT an operational feed.
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

- **D1 — ⛔ THE BLOCKER IS OVERLAP, NOT ARCHIVE DEPTH. Measured 2026-08-29.**
  The gauge record ends **2025-12-31 12:00**. Every locally-held IFS source begins in 2026:

  | source | coverage | overlap with gauges |
  |---|---|---|
  | recap gateway API | rolling ~2 months (≈2026-06-29 →) | **none** |
  | S3 `recap-data-gateway`, `area=60_25_90_45` | 2026-03-01 → 2026-05-28 | **none** |
  | **TIGGE (ECDS)** | **October 2006 → today−2 d** | **✅ all of 2020–2025** |

  ⇒ **Neither local source can answer this question at all** — not for lack of data volume but
  because there is nothing to pair against. An earlier revision of this plan said 62 gateway days
  would suffice; that was wrong, and it was wrong for a reason a single date check would have caught.
  ⛔ Do not re-propose the gateway or the S3 bucket as the comparison source **unless DHM supplies
  2026 gauge data** (see D11).

  *(S3 notes, measured: the `area=` partitions are **W_S_E_N** — `60_25_90_45` = lon 60–90, lat 25–45,
  which does contain the study box; `cf` was renamed `fc` on **2026-05-13**, so they are one product,
  not two; and `area=60_25_90_45` is frozen at 2026-05-28 because current data lands in the
  `…p0_…`-style partition.)*

- **D2 — START with ONE monsoon season even though TIGGE now offers six.** D6 makes 2020–2025
  reachable, which changes the constraint from *availability* to *effort*. It does not change the
  question: whether a **~12 h** displacement is present. One JJAS is 122 days ≈ 3,000 station-days,
  far more than that needs. ⛔ **Retrieve one season (JJAS 2025), answer the question, and only then
  decide whether more years buy anything.** A six-year pull before the first result is the main
  over-reach risk here, and it is now the *easy* mistake to make rather than an impossible one.

- **D3 — Stratify by LEAD TIME; a forecast's diurnal cycle is not one thing.** Early leads inherit the
  analysis; later leads relax onto the model's own attractor, so phase error typically grows with lead.
  ⛔ Pooling all leads would average a real lead-dependence into a single misleading number. Report per
  lead-day band (e.g. D+1, D+2–3, D+4–5), and state which leads the operational cycle actually consumes.

- **D4 — TIGGE's 6-hourly step IS the detection floor, and it bounds what this plan can conclude.**
  4 points/day resolves a **~12 h** displacement (gauge peak ≈00:45 NPT and model peak ≈13:00 NPT fall
  in clearly different 6 h bins) but pins phase only to **±3 h**. ⇒ **This plan can answer the
  screening question — "is there a large phase error?" — and CANNOT design a correction against it.**
  ⛔ Report the floor beside every result so "no error found" is never read as "no error resolvable".
  If the answer is GO, the fine structure comes from the 3-hourly S3 archive (8 points/day), which by
  then needs 2026 gauge data (D11) — a separate plan.

- **D5 — Reuse the existing comparison, do not rebuild it.** Same pairing (`ma6_pairs.PairedSeries`,
  commonly-retained hours only), same normalisation (each station's 24 h sums to 100 %), same circular
  lag estimators, same JJAS definition, same elevation bands, same NPT display, same sign convention
  (**positive = model later than gauge**). ⛔ A second implementation of the same statistic would make
  the ERA5-Land and IFS numbers incomparable, which is the entire point of the exercise.
  🔴 **UNITS DIFFER BY 1000× ACROSS THE SOURCES AND THE MISTAKE IS SILENT.** ERA5-Land `tp` = **m**;
  gateway IFS `tp` = **m**; **TIGGE `tp` = `kg m**-2` = mm** (measured, D6). All three are
  *accumulated*, so deaccumulation is shared — but a missing ×1000 would read as a plausible wet bias,
  not a crash. **Assert the unit at read time from the file's own attribute; never infer it from the
  source name.**

- **D6 — TIGGE is the PRIMARY source, and it is PROVEN — every property below was measured, not read.**
  Retrieved 2026-08-29: ECMWF control `tp`, **2025-07-15** (monsoon, **inside** the gauge record),
  over the exact study box.
  - **Route:** `cdsapi` → `https://ecds.ecmwf.int/api`, dataset **`tigge-forecasts`**. TIGGE migrated
    to ECDS on **2026-05-27**, so ⛔ **every pre-migration recipe is dead**. An existing Copernicus CDS
    key works. **Two** separate acceptances are required and both were hit as hard 403s: the ECDS
    *Terms of use* **and** the dataset's own *TIGGE licence*.
  - **Grid:** 0.5°, lat 31.0→26.0, lon 80.0→89.0 (11×19) — lands exactly on `STUDY_AREA`.
  - **Steps: 6-hourly**, initialised **00/12 UTC only**. ⇒ 4 points/day against the 3-hourly (8/day)
    product we fly.
  - ⛔ **`stepType: accum`** — accumulated from forecast start (confirmed by the GRIB attribute *and*
    by monotonicity across all 9 steps). Deaccumulate as ERA5-Land required.
  - 🔴 **UNITS ARE `kg m**-2`, i.e. MILLIMETRES — NOT metres.** ERA5-Land `tp` and the gateway's IFS
    `tp` are both **m**. Mixing them without conversion is a silent **1000×** error that would read as
    a plausible bias rather than an obvious break. **Assert the unit at read time; never infer it.**

- **D6a — ⛔ TIGGE IS A RESEARCH ARCHIVE. IT CAN NEVER BE AN OPERATIONAL FEED. Embargo measured.**
  Requests on 2026-08-29: **today−1 d FAILS**; **today−2 d and today−3 d succeed**. ⇒ a **~48 h
  embargo**, reproduced twice.
  A forecast delivered two days after issue is worthless for flood warning. ⛔ This holds for **every**
  centre including ECMWF's own CC BY 4.0 data, so it is not a licensing artefact. **Nothing in this
  plan may become an operational dependency.**

- **D6b — Multi-centre corroboration runs on the SIX CC BY 4.0 centres. No NC data, anywhere.**
  Licence split (owner-confirmed 2026-08-29): **CC BY 4.0** — ECMWF, DWD, ECCC, KMA, NCEP, UKMO
  (commercial use permitted). **CC BY-NC 4.0** — BoM, CMA, CPTEC, **IMD**, JMA, MF, **NCMRWF**.
  ⇒ The scientifically strongest arm needs no NC data at all: if the ~12 h phase error appears across
  **ECMWF + NCEP + UKMO + DWD** — independent models, different convection schemes — that upgrades
  "ECMWF has a phase problem" to "**parameterized convection has a phase problem over the Nepal
  hills**", unencumbered. If one centre gets the nocturnal peak *right*, that is more valuable still:
  it proves the phase is attainable and points at what fixes it.
  ⛔ **Do NOT add CC BY-NC data (IMD, NCMRWF, …) to the data gateway.** The gateway is operational
  infrastructure inside a commercially contracted deliverable; NC data there is a compliance liability
  in exactly the wrong place — and per D6a it would be operationally useless regardless. Research
  folder only, as ERA5-Land already is. ⛔ Operational access to Indian forecasts is an
  institutional/bilateral matter with IMD or NCMRWF, **not** something TIGGE can deliver.
  ⚠️ Each origin needs its **own** detection floor stated (D4): per-centre resolution and step spacing
  differ.

- **D6c — Attribution is a deliverable, not a courtesy.** Both licences require crediting the data
  provider and acknowledging TIGGE in any written output. ⇒ Any figure or document built on this
  carries the attribution and the TIGGE acknowledgement. ⛔ A figure without it is a defect.

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

- **D11 — Ask DHM for 2026 gauge data, in parallel; it does not gate this plan.** The local archives
  (gateway API, S3) are unusable here only because the gauge record stops **2025-12-31**. A 2026
  extract would make both immediately useful — at **3-hourly** resolution, twice TIGGE's — and would
  cover JJAS 2026. ⇒ Raise it with M-D3's other asks. ⛔ **Do not block on it**: TIGGE answers the
  screening question without it.

## Tasks

Three tasks, three phases. `$ENV` abbreviates the gateway-authenticated environment.

### T1 — retrieve one season from TIGGE (depends: nothing)
**In:** ECMWF (`origin: ecmf`) control **and** perturbed `tp` for **JJAS 2025** over `STUDY_AREA`,
via `cdsapi` → `https://ecds.ecmwf.int/api`, dataset `tigge-forecasts`. The route, grid, steps,
accumulation and units are already **measured** (D6) — reuse them, do not re-probe.
- Deaccumulate to 6-hourly increments; ⛔ **assert `kg m**-2` from the file attribute** and convert
  once, explicitly (D5).
- Extract to the 26 station points with the **same nearest-cell operator** the ERA5-Land work locked
  (`era5_extract.py`), so the two are comparable by construction.
- Record per granule: origin, type, run date/time, step, and the observed unit.

**Out:** any comparison; any other centre; any date outside JJAS 2025 (D2).
**Verify:** the retrieved series is complete on its own 6-hourly axis with gaps carried, never filled;
a stated station-day count; and the unit assertion has a test that **fails against a metres-valued
file**. ⛔ A 1000× error is the one defect here that would look like a result.

### T2 — the comparison (depends: T1)
**In:** run the M-A7/timing comparison with TIGGE-IFS in ERA5-Land's place, per D5, stratified by lead
time (D3), reporting the **±3 h detection floor** (D4) and both timezone readings (D9). Same 26
stations, same JJAS, same bands, same estimators, same sign convention.
**Optional corroboration arm (D6b):** repeat for **NCEP, UKMO, DWD, ECCC, KMA** — all CC BY 4.0.
⛔ No CC BY-NC centre (IMD, NCMRWF, …) enters the gateway or any operational path; research folder
only, and each origin carries its **own** detection floor.
**Out:** any correction; any magnitude or bias claim — **phase only**.
**Verify:** the ERA5-Land figures regenerate unchanged from the same code path (proving reuse, not a
fork); every reported number carries its `n` station-days, its lead band and its origin.

### T3 — the recommendation (depends: T2)
**In:** state whether the ECMWF IFS forecast shares ERA5-Land's phase error, at what magnitude, with
what lead-dependence, on how many station-days, and — if the corroboration arm ran — whether it is
ECMWF-specific or common across centres. Then state the consequence for M-A9 Use 1: with condition (1)
supported and condition (2) sourced from the gateway's elevation bands (D8), does the evidence support
**building** a timing correction, or does it close the question?
⛔ **"IFS does not share the error, so no correction is needed" is a permitted and valuable
conclusion.** So is "the sample is too thin to say". ⛔ And whatever the result, **nothing here becomes
operational** (D6a: 48 h embargo).
**Out:** implementing anything.
**Verify:** every figure traces to T2's output or a named command, and carries the **TIGGE
acknowledgement and provider attribution** both licences require (D6c).

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
