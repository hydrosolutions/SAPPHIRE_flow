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
`data/dhm_precip/tigge/points/tigge_gauge_timing_offsets.csv`, **regenerated 2026-08-29 (21:44) against
the fixed D5 per-station estimator** (see next section) — the CSV committed alongside this doc's first
draft used a pooled-mass aggregate a review round found violated D5's own "each station's cycle sums to
100%" rule; every table and reading below is from the corrected run, not the first one.

**T2 Verify's "the ERA5-Land figures regenerate unchanged from the same code path" is satisfied by
CODE IDENTITY, not a fresh figure render:** `scripts/dhm_precip/diurnal_phase.py` is now the one
TRACKED module both this screen and `era5_gauge_timing_figure.py` import their phase estimator from
(`tests/unit/scripts/test_tigge_gauge_timing.py::TestCleanCheckoutImport` locks
`tigge_gauge_timing.band_of is diurnal_phase.band_of`, an object-identity check strictly stronger than
an output diff — two different functions can happen to agree on one figure's numbers, but cannot both
be the literal same object unless they are). `era5_gauge_timing_figure.py` itself was not re-run as
part of this fixer round: it lives under the gitignored `data/` tree (an M-A6 output artefact in a
sibling checkout, outside this repo), nothing in the D5 refactor changes its inputs or its own code
(only how it imports four helper functions it previously defined by dynamic path-load), and re-running
it exercises no code this repo tracks or tests. Its own ERA5-Land numbers are therefore unaffected by
this fix and are not restated here.

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
estimated with M-A6's own harmonic-phase estimator (`harmonic_phase_h`/`same_day_branch`), now shared
through the TRACKED `scripts/dhm_precip/diurnal_phase.py` module — imported by BOTH this screen's
`tigge_gauge_timing.py` and `era5_gauge_timing_figure.py` so they run the exact same code path (D5),
never a fork or a gitignored-file import a fresh checkout (or CI) cannot resolve. Each band's four
active clock-hour totals go into an otherwise-zero 24-length share vector — mathematically identical to
running the same first-harmonic transform on the 4 points directly — but D5 also pins the
**station-equal** aggregate: each station's own cycle is normalised independently (so it sums to 100%
regardless of how many windows it contributed) and its own lag computed from that; every reported figure
below is the MEDIAN across stations, never a pool of raw mass summed across stations first (which would
let whichever station contributed the most windows dominate). Per D5, the season-year bootstrap and
24-bin completeness test are RETIRED for this screen (a one-season, 4-bin input degenerates both into
false precision or false exclusion); every number below is a point estimate with its own `n` — both the
window count and the **distinct (station, valid-date) station-day count**, reported separately since a
station can contribute more than one window per calendar day — and the **±3 h resolution bound (D4)
applies to every cell**: 6-hourly data cannot place a lag more precisely than that, and an unresolved
error is never read as no error.

### Primary reading (gauge timestamps as labelled, D7)

Positive = model later than gauge — same sign convention as M-A9's ERA5-Land table, so the two are
directly comparable. `n` is windows / station-days.

| lead band | `< 1,000 m` (n stations) | `1,000-2,000 m` (n stations) | `≥ 2,000 m` (n stations) |
|---|---|---|---|
| D+1 | **−1.51 h** (8, n=2,940 / 842) | **−5.33 h** (9, n=3,498 / 956) | **−5.16 h** (6, n=2,168 / 620) |
| D+2 | **−0.29 h** (8, n=2,915 / 835) | **−5.17 h** (9, n=3,479 / 951) | **−5.29 h** (6, n=2,155 / 616) |
| D+3 | **−0.96 h** (8, n=2,888 / 828) | **−5.72 h** (9, n=3,461 / 946) | **−5.68 h** (6, n=2,140 / 612) |

**For reference, M-A9's ERA5-Land numbers** (JJAS 2020-2025, hourly, same sign convention,
`< 1,000 / 1,000-2,000 / ≥ 2,000 m` bands): **+0.6 h / −14.4 h / −11.9 h**.

### D7 alternate reading — gauge labels are really NPT (−6 h shift)

Reported alongside, never gating (D7): the mid and high bands land near zero (**−0.31 to −1.07 h** mid,
**−0.53 to +0.45 h** high) — consistent with, though not identical to, the uniform +6 h shift the same
hypothesis produces on ERA5-Land's between-band contrast. The low band does **not** behave as a clean
rotation (`+2.22 h` at D+1 to `−4.30 h`/`−6.47 h` at D+2/D+3) — expected on real, gappy 6-hourly data:
the two window alignments (ending at V vs. V+6) do not admit the identical set of complete windows, so a
pure bin-rotation is only an idealisation here, not an exact prediction.

## Reading the result

1. **The mid and high bands are NOT aligned.** Every lead in both bands is resolved well beyond the
   ±3 h bound — a ~5-6 h same-direction offset is roughly 2x the finest lag D4 permits distinguishing
   from zero. IFS shares ERA5-Land's DIRECTION of error (model too early) in the same two bands ERA5-
   Land flagged.
2. **The MAGNITUDE is smaller than ERA5-Land's, not the same.** Mid band ~36-40% of ERA5-Land's
   −14.4 h (−5.17 to −5.72 h, roughly flat across D+1-D+3); high band ~43-48% of ERA5-Land's −11.9 h
   (−5.16 to −5.68 h, mildly increasing with lead). This is the expected direction for D3's "early
   leads inherit the analysis, later leads relax onto the model's attractor" — but three days of lead
   is not enough sample to say whether either trend would continue toward ERA5-Land's number at
   longer leads (this plan's D2 scope is one season at leads to D+3; that question is open, not
   answered here).
3. **The low band is not resolved beyond zero at any lead** (−1.51, −0.29, −0.96 h — every value is
   inside the ±3 h D4 bound), consistent with ERA5-Land's own near-zero low-band number (+0.6 h): this
   screen cannot distinguish the low band from no error at all, at any of the three leads measured.
4. **Neither pole of the plan's own decision rule applies cleanly.** This is not "apparent alignment"
   (mid/high bands are clearly non-zero) and it is not "a repeated half-day (~12 h) displacement" either
   — the measured displacement is real, resolved, and repeated, but at roughly a third to a half of
   ERA5-Land's magnitude.

## Verdict (bounded by D4)

**NO-GO on deploying the originally proposed ~12 h correction as designed** — IFS's own measured
displacement in the two affected bands is roughly a third to a half of that magnitude; applying
ERA5-Land's larger correction to IFS forcing would systematically over-correct.

**GO on finer-resolution correction work, calibrated to IFS itself rather than to ERA5-Land** — the
mid/high-band error is real (well beyond the ±3 h bound), repeated across three correlated lead strata
(D+1/D+2/D+3 pool overlapping initialisations and the same stations, so they are not independent
samples — the repetition still shows the same displacement is not a one-lead artefact, but it does not
multiply as three independent confirmations would), and in a consistent direction. A correction designed
from THIS measurement (not from M-A9's ERA5-Land numbers) is now evidence-supported for those two bands.
The low band is unresolved from zero at every lead measured, so any correction there is on much weaker
ground and should not simply mirror the upper bands' sign.

This conclusion does **not** extend to "no timing correction is needed" (D4) — a ~5-6 h, well-resolved,
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
