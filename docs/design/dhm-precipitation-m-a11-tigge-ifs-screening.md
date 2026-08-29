# DHM precipitation — M-A11 IFS diurnal timing re-evaluation (Plan 216 T3)

**Plan 216 T1-T3. Written 2026-08-29 against a live TIGGE (ECDS) pull, JJAS 2025, ECMWF control
forecast only.** Answers the question M-A9 §2 (Use 1) flagged but could not answer: does the
**operational ECMWF IFS forecast** share ERA5-Land's ~12 h monsoon diurnal phase error, or was that
error an artefact of measuring a reanalysis whose convection scheme is frozen at a 2016-era IFS cycle?

**Owner decides (M-DEC). This document does not.**

## Regenerate

```
$ cd /Users/bea/Documents/GitHub/sapphire-ma6   # or SAPPHIRE_flow — data/dhm_precip is shared
DHM_PRECIP_XLSX=data/dhm_precip/combined_precipitation_37_stations.xlsx \
  uv run python -m scripts.dhm_precip.tigge_ifs --skip-retrieve   # ~5 min without --skip-retrieve
DHM_PRECIP_XLSX=data/dhm_precip/combined_precipitation_37_stations.xlsx \
  uv run python -m scripts.dhm_precip.tigge_gauge_timing
```

Raw and extracted data: `data/dhm_precip/tigge/` (gitignored). Retrieved 2026-08-29, 17.8 MB,
`data/dhm_precip/tigge/raw/tigge_ecmwf_cf_tp_jjas2025.grib`. Every number below is in
`data/dhm_precip/tigge/points/tigge_gauge_timing_offsets.csv`.

## D6 correction — the grid is not what the plan assumed

The plan's D6 stated a regular 0.5° lat/lon grid (11×19 cells). **Measured against the live API
2026-08-29: wrong.** `tigge-forecasts` on ECDS has no `grid` interpolation input at all (confirmed
against the process's own JSON Schema — `origin/year/month/day/time/level_type/variable/
forecast_type/leadtime_hour/level_value/data_format/area`, no `grid`). What comes back is the
model's **native reduced Gaussian grid** — measured `GRIB_gridType: reduced_gg`, `GRIB_N: 640`,
1,597 irregularly spaced points inside `STUDY_AREA`. Every other D6 property was confirmed exactly as
written: dataset `tigge-forecasts`, `origin=ecmwf`, `type=cf`, `param 228228` (`tp`), 6-hourly steps
from **00/12 UTC only** (the live constraints file lists `time: ["00:00", "12:00"]` for every
`(ecmwf, control_forecast, total_precipitation)` combination in 2025), `stepType=accum` with
continuous accumulation from forecast start (no daily reset, unlike ERA5-Land), units `kg m**-2`
(millimetres). The nearest-cell extraction operator (great-circle argmin) is unaffected by the grid
correction — it never assumed row/col registration. See `scripts/dhm_precip/tigge_ifs.py` module
docstring for the full record.

## What was measured

**Retrieval (T1):** control forecast only, `tp`, JJAS 2025, 00/12 UTC inits (244 inits), leads 0-90 h
by 6 h (16 steps), `STUDY_AREA` (31/80/26/89), deaccumulated to 6-hourly period-ending increments,
extracted to all 26 gauge stations by nearest grid point. 95,160 (station, init, lead) rows,
3,276 station-days (distinct station x calendar-date pairs across the full retrieved lead range).

**Comparison (T2):** lead-stratified (D3) into three COMPLETE bands (all four 6-hourly clock
positions) — `D+1` (steps 24/30/36/42), `D+2` (48/54/60/66), `D+3` (72/78/84/90). There is no `D+0`:
no window ends at lead 0, so a same-day band can only ever cover 3 of 4 clock positions. Where two
initialisations both reach a band's exact steps at the same valid time, the more recent initialisation
is kept (never both, never averaged). Gauges aggregated into complete 6-hour period-ending windows —
any window missing one of its 6 hourly gauge values is dropped, never partially summed. Phase
estimated with M-A6's own harmonic-phase estimator (`harmonic_phase_h`/`same_day_branch`, imported
unmodified from `data/dhm_precip/figures/era5-timing/era5_gauge_timing_figure.py`), reused by placing
each band's four active clock-hour totals into an otherwise-zero 24-length share vector — mathematically
identical to running the same first-harmonic transform on the 4 points directly. Per D5, the
season-year bootstrap and 24-bin completeness test are RETIRED for this screen (a one-season, 4-bin
input degenerates both into false precision or false exclusion); every number below is a point estimate
with its own `n`, and the **±3 h resolution bound (D4) applies to every cell** — 6-hourly data cannot
place a lag more precisely than that, and an unresolved error is never read as no error.

### Primary reading (gauge timestamps as labelled, D7)

Positive = model later than gauge — same sign convention as M-A9's ERA5-Land table, so the two are
directly comparable.

| lead band | `< 1,000 m` (n stations) | `1,000-2,000 m` (n stations) | `≥ 2,000 m` (n stations) |
|---|---|---|---|
| D+1 | **−3.03 h** (8, n=2,940) | **−6.29 h** (9, n=3,498) | **−5.13 h** (6, n=2,168) |
| D+2 | **−1.36 h** (8, n=2,915) | **−6.42 h** (9, n=3,479) | **−5.11 h** (6, n=2,155) |
| D+3 | **−0.32 h** (8, n=2,888) | **−6.83 h** (9, n=3,461) | **−5.15 h** (6, n=2,140) |

**For reference, M-A9's ERA5-Land numbers** (JJAS 2020-2025, hourly, same sign convention,
`< 1,000 / 1,000-2,000 / ≥ 2,000 m` bands): **+0.6 h / −14.4 h / −11.9 h**.

### D7 alternate reading — gauge labels are really NPT (−6 h shift)

Reported alongside, never gating (D7): the mid and high bands land near zero (**−0.73 to −1.22 h** mid,
**+0.32 to +0.49 h** high) — consistent with, though not identical to, the uniform +6 h shift the same
hypothesis produces on ERA5-Land's between-band contrast. The low band does **not** behave as a clean
rotation (`+5.73 h` at D+1 collapsing to `−17.86 h`/`−16.95 h` at D+2/D+3) — expected on real, gappy
6-hourly data: the two window alignments (ending at V vs. V+6) do not admit the identical set of
complete windows, so a pure bin-rotation is only an idealisation here, not an exact prediction.

## Reading the result

1. **The mid and high bands are NOT aligned.** Every lead in both bands is resolved well beyond the
   ±3 h bound — a ~5-7 h same-direction offset is roughly 2x the finest lag D4 permits distinguishing
   from zero. IFS shares ERA5-Land's DIRECTION of error (model too early) in the same two bands ERA5-
   Land flagged.
2. **The MAGNITUDE is smaller than ERA5-Land's, not the same.** Mid band ~44-47% of ERA5-Land's
   −14.4 h; high band ~43-43% of ERA5-Land's −11.9 h, essentially flat across D+1-D+3 (unlike mid,
   which grows mildly with lead: −6.29 → −6.83 h). This is the expected direction for D3's "early
   leads inherit the analysis, later leads relax onto the model's attractor" — but three days of lead
   is not enough sample to say whether the mid-band trend would continue toward ERA5-Land's number at
   longer leads (this plan's D2 scope is one season at leads to D+3; that question is open, not
   answered here).
3. **The low band trends toward alignment with lead** (−3.03 → −0.32 h), consistent with ERA5-Land's
   own near-zero low-band number (+0.6 h).
4. **Neither pole of the plan's own decision rule applies cleanly.** This is not "apparent alignment"
   (mid/high bands are clearly non-zero) and it is not "a repeated half-day (~12 h) displacement" either
   — the measured displacement is real, resolved, and repeated, but at roughly half ERA5-Land's
   magnitude.

## Verdict (bounded by D4)

**NO-GO on deploying the originally proposed ~12 h correction as designed** — IFS's own measured
displacement in the two affected bands is roughly half that magnitude; applying ERA5-Land's larger
correction to IFS forcing would systematically over-correct.

**GO on finer-resolution correction work, calibrated to IFS itself rather than to ERA5-Land** — the
mid/high-band error is real (well beyond the ±3 h bound), repeated across three independent lead
bands, and in a consistent direction. A correction designed from THIS measurement (not from M-A9's
ERA5-Land numbers) is now evidence-supported for those two bands. The low band's trend toward zero
with lead means any correction there is on much weaker ground and should not simply mirror the upper
bands' sign.

This conclusion does **not** extend to "no timing correction is needed" (D4) — a ~5-7 h, well-resolved,
repeated error is not "no error", even though it is smaller than first thought.

## Attribution

Contains modified Copernicus / ECMWF data. Attribution: ECMWF. Acknowledgement: contains modified data
from the TIGGE archive (`tigge-forecasts`, ECDS). ⛔ Research use only per D6's measured ~48 h embargo
— this is a screening result, not an operational dependency, for any centre.

## Non-goals (unchanged from the plan)

No correction was implemented. No magnitude or bias claim is made — phase only. No centre other than
ECMWF was pulled. No perturbed members. No multi-year climatology. Elevation-band weights, the DHM 2026
gauge request, and the free-alternatives escalation ladder all belong to whatever M-DEC authorises next,
not to this measurement.
