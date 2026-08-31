# DHM precipitation — M-A11 IFS diurnal timing re-evaluation (Plan 216 T3)

**Plan 216 T1-T3. Written 2026-08-29 against a live TIGGE (ECDS) pull, JJAS 2025, ECMWF control
forecast only; numbers corrected and regenerated 2026-08-30 (see Regenerate).** Answers the question M-A9 §2 (Use 1) flagged but could not answer: does the
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
`data/dhm_precip/tigge/raw/tigge_ecmwf_cf_tp_jjas2025.grib`. The second command writes two CSVs:
the published matrix `data/dhm_precip/tigge/points/tigge_gauge_timing_offsets.csv` (every aggregate
number below) and `…/tigge_station_amplitudes.csv` (the per-station amplitudes and inclusion decisions
behind **Identifiability**; see the sweep command there). The matrix was **regenerated 2026-08-30
(13:11)** after two corrections independent reviews found:

1. **The D5 normalisation.** The screen normalised each clock hour's RAW TOTAL, while M-A6 divides each
   clock-hour total by its OWN OBSERVATION COUNT and normalises the resulting cycle of hourly MEANS.
   That is a different estimator, not a scale factor: with gappy coverage it weights each clock position
   by how many windows survived there.
2. **First-harmonic identifiability.** The estimator took the ANGLE of the first harmonic without ever
   checking its MAGNITUDE, and the published table carried no amplitude diagnostic. See
   **Identifiability** below; this moved two cells.

All three earlier tables (the first draft's pooled-mass aggregate, the 2026-08-29 21:44 per-station
rerun, and the 2026-08-30 07:50 normalisation rerun) are superseded. ⚠️ The 07:50 run's `n` was
identical to the 21:44 run's in every as-labelled cell — but an identical `n` shows an unchanged sample
SIZE, not row identity, and that round also changed the finite filtering and the contributor selection,
so `n` alone would not license a "normalisation-only" reading. **Row identity was therefore measured**
(2026-08-30): running the 21:44 estimator (`4d6160fd:scripts/dhm_precip/tigge_gauge_timing.py`,
`gauge_shift_hours=0`) beside the current one over the same inputs, the retained `(station, valid_time)`
key sets are IDENTICAL in all three lead bands — D+1 8,606 / D+2 8,549 / D+3 8,489 rows, zero rows on
either side only. The finite filter dropped nothing on this data, and the 07:50 movement was the
normalisation alone.

**What T2 Verify's "the ERA5-Land figures regenerate unchanged from the same code path" does and does
not cover.** `scripts/dhm_precip/diurnal_phase.py` is the one TRACKED module holding the phase
estimator. The TRACKED consumer is identity-tested:
`tests/unit/scripts/test_tigge_gauge_timing.py::TestCleanCheckoutImport` locks
`tigge_gauge_timing.band_of is diurnal_phase.band_of` — an object-identity check strictly stronger than
an output diff. ⚠️ **That test says nothing about `era5_gauge_timing_figure.py`**, which lives under the
gitignored `data/` tree: no test and no CI job in this repo can reach it, so no claim here is
CI-verified for it. It does import the same functions from the same module, and the two stale local
`band_of`/`npt_label` definitions that were silently SHADOWING those imports (a Python name-resolution
trap: the later `def` wins, so the file was quietly back on its own copies) have been deleted — but
only a human re-running that script can confirm it stays that way. It was not re-run here, and its own
ERA5-Land numbers are unaffected by this fix (nothing in the D5 refactor changes its inputs).

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
The opened file is gated on its OWN attributes before any of this runs, so a stale or partial
`--skip-retrieve` re-use cannot be screened as a season: the exact 244-init JJAS schedule (no
duplicate, none missing), `GRIB_dataType == cf` (⛔ never a perturbed member), `GRIB_stepType ==
accum`, `GRIB_centre == ecmf`, the 6-hourly 0-90 h lead axis, `kg m**-2` units, and step 0 == 0
(accumulation really does run from forecast start). The real file passes all of them.

**Comparison (T2):** lead-stratified (D3) into three COMPLETE bands (all four 6-hourly clock
positions) — `D+1` (steps 24/30/36/42), `D+2` (48/54/60/66), `D+3` (72/78/84/90). There is no `D+0`:
no window ends at lead 0, so a same-day band can only ever cover 3 of 4 clock positions. Where two
initialisations both reach a band's exact steps at the same valid time, the more recent initialisation
is kept (never both, never averaged). Gauges aggregated into complete 6-hour period-ending windows —
any window missing one of its 6 hourly gauge values is dropped, never partially summed. Phase
estimated with M-A6's own harmonic-phase estimator (`harmonic_phase_h`/`same_day_branch`), now shared
through the TRACKED `scripts/dhm_precip/diurnal_phase.py` module rather than a gitignored-file import a
fresh checkout (or CI) cannot resolve. This screen's `tigge_gauge_timing.py` is identity-tested against
that module; `era5_gauge_timing_figure.py` imports the same module but lives outside the repo tree and
outside CI, so nothing here verifies its code path (see the ⚠️ above — that is the one statement of
scope; it is not repeated below). Each band's four
active clock-hour bins go into an otherwise-zero 24-length share vector — mathematically identical to
running the same first-harmonic transform on the 4 points directly — as M-A6's own **hourly MEANS**:
each clock hour's total is divided by that clock hour's OWN observation count before the cycle is
normalised, so a clock position does not gain weight merely by retaining more windows. D5 also pins the
**station-equal** aggregate: each station's own cycle is normalised independently (so it sums to 100%
regardless of how many windows it contributed) and its own lag computed from that; every reported figure
below is the MEDIAN across stations, never a pool of raw mass summed across stations first (which would
let whichever station contributed the most windows dominate). A station contributes only if its
surviving pairs occupy **all four** clock positions — one to three cannot fit a diurnal cycle at all —
and a non-finite value on either side (a carried T1 gap) is dropped at the pairing boundary and excluded
from every `n`. A station also contributes only if BOTH its cycles have an identified phase at all
(**Identifiability**, below). The CSV carries the COMPLETE 3 lead bands x 3 elevation bands x 2 readings
matrix with a `status` per cell — `ok`, `no_paired_windows`, `insufficient_clock_coverage`,
`no_precipitation_mass` or `phase_unidentifiable` — so a combination nothing supports is visible as such
rather than an absent row. ⚠️ Its 18 rows are **9 ANALYSIS CELLS x 2 D7 readings**: `run_all_bands`
builds ONE pairing per lead band and the D7 reading merely rotates those cycles, so counting 18 would
double-count every measurement. All 9 analysis cells are `ok` on this season. Per D5, the season-year
bootstrap and 24-bin completeness test are RETIRED for this screen (a one-season, 4-bin input
degenerates both into false precision or false exclusion); every number below is a point estimate with its own `n` — both the
window count and the **distinct (station, valid-date) station-day count**, reported separately since a
station can contribute more than one window per calendar day — and the **±3 h resolution bound (D4)
applies to every cell**: 6-hourly data cannot place a lag more precisely than that, and an unresolved
error is never read as no error.

### Identifiability (the amplitude gate)

`harmonic_phase_h` returns the ANGLE of the first harmonic and says nothing about its MAGNITUDE. With
four active bins that harmonic is the vector `(w0 - w12, w6 - w18)` over shares summing to 1, so its
magnitude **R** is literally the joint opposite-bin contrast as a fraction of the cycle's mass. Equal
opposite bins give R = 0 — an undefined phase — and `np.angle(0)` returns 0.0, i.e. a plausible-looking
00:00 phase for a cycle that has none. A mass check does not catch this: such a cycle can carry full
mass. With only four bins that is a live risk, so R is now **gated and published**.

**The floor is `MIN_HARMONIC_AMPLITUDE = 0.05`** (`scripts/dhm_precip/tigge_gauge_timing.py`). ⛔ It is
a **DECLARED CONVENTION** — not a stability guarantee, not a worst-case bound, and not a significance
level (D5 retired the bootstrap, so no interval is claimed here and none is implied).

⚠️ **An earlier revision argued the floor from geometry. That argument was wrong and is withdrawn.**
Corrected: an **orthogonal** perturbation of magnitude R rotates the phase by **exactly 45°** (D4's
±3 h bound), not *past* it, and the **smallest unrestricted** perturbation reaching the 45° ray is
**`R / √2` ≈ 0.71 R** — its distance to that ray. R scales the available room proportionally, but **no
threshold on R bounds the rotation an arbitrary perturbation can produce**; geometry licenses no floor.

What does justify 0.05 is the **observed separation** plus the **sweep**: band medians sit at
R = 0.18-0.38, the two rejected station cycles at R ≈ 0.03, the nearest RETAINED cycle at 0.056 — a
real but narrow gap (⚠️ not an order of magnitude at station level: four retained cycles sit between
0.056 and 0.092), and doubling the floor to 0.10 drops exactly those four while leaving the reading
unchanged (**Sensitivity**, below). A station cycle below the floor on either side is reported
`phase_unidentifiable` and contributes nothing; a cell no station supports carries the status of the
furthest gate its stations reached.

**Every number in this section is reproducible per station**, not just as a band median. The
`Regenerate` command above also writes `data/dhm_precip/tigge/points/tigge_station_amplitudes.csv` —
one row per (lead band, station) cycle, 69 on this season, with `gauge_harmonic_amplitude`,
`tigge_harmonic_amplitude`, `min_harmonic_amplitude`, `included`, `status` and
`lag_hours_same_day_branch` (one row per CYCLE, not per D7 reading: `np.roll` moves the angle, not the
magnitude). The sweep re-runs the whole screen at another floor, into `_minamp` filenames that can
never overwrite the published ones:

```
DHM_PRECIP_XLSX=data/dhm_precip/combined_precipitation_37_stations.xlsx \
  uv run python -m scripts.dhm_precip.tigge_gauge_timing --min-amplitude 0.10
# -> data/dhm_precip/tigge/points/tigge_station_amplitudes_minamp0.1.csv
# -> data/dhm_precip/tigge/points/tigge_gauge_timing_offsets_minamp0.1.csv
```

**Measured on this season** (69 (lead band x station) cycles): gauge R spans 0.107-0.618, TIGGE R
0.027-0.441. At the 0.05 floor **2 of 69 station-cycles are unidentifiable** — Bharatpur Airport at D+2
(TIGGE R = 0.027) and Humde Airport at D+3 (R = 0.029) — which moved two published cells: **D+2 low
−0.20 h → −0.39 h** (8 → 7 stations, n 2,915/835 → 2,572/726) and **D+3 high −6.11 h → −6.52 h** (6 → 5
stations, n 2,140/612 → 1,899/546), plus their two D7 twins. **No cell became unidentifiable**, no cell
changed sign, and every band statistic still rests on ≥ 5 stations, so the verdict below is unchanged.

**Sensitivity (declared, not tuned; regenerate with the `--min-amplitude 0.10` command above).** At a
stricter 0.10 floor, 4 further station-cycles drop: two from D+1 low (whose median does not move at
all), one from D+3 low (−0.68 → −1.88 h, still inside the ±3 h bound and still unresolved from zero)
and one from D+3 mid (−6.15 → −6.09 h). Every other cell is unchanged and all 9 analysis cells remain
`ok`, so the reading of the result is the same at either floor.

### Primary reading (gauge timestamps as labelled, D7)

Positive = model later than gauge — same sign convention as M-A9's ERA5-Land table, so the two are
directly comparable. `n` is windows / station-days; **R** is the median first-harmonic amplitude
(gauge / IFS) of the contributing stations, against the 0.05 identifiability floor.

| lead band | `< 1,000 m` | `1,000-2,000 m` | `≥ 2,000 m` |
|---|---|---|---|
| D+1 | **−1.34 h** — 8 st, n=2,940 / 842, R 0.28 / 0.20 | **−6.47 h** — 9 st, n=3,498 / 956, R 0.26 / 0.21 | **−5.52 h** — 6 st, n=2,168 / 620, R 0.38 / 0.28 |
| D+2 | **−0.39 h** — 7 st, n=2,572 / 726, R 0.30 / 0.28 | **−5.78 h** — 9 st, n=3,479 / 951, R 0.26 / 0.25 | **−5.63 h** — 6 st, n=2,155 / 616, R 0.38 / 0.25 |
| D+3 | **−0.68 h** — 8 st, n=2,888 / 828, R 0.28 / 0.18 | **−6.15 h** — 9 st, n=3,461 / 946, R 0.26 / 0.30 | **−6.52 h** — 5 st, n=1,899 / 546, R 0.37 / 0.23 |

Every cell is `ok`. (R is the median over stations that each already passed the floor, so it exceeds
it by construction; what the column shows is HOW FAR above, and it is well above.) Superseded runs, for
the record — pre-normalisation-fix (same cells): D+1 −1.51 / −5.33 / −5.16 h, D+2 −0.29 / −5.17 /
−5.29 h, D+3 −0.96 / −5.72 / −5.68 h; pre-amplitude-gate, the two cells it moved: D+2 low −0.20 h
(8 st) and D+3 high −6.11 h (6 st). No cell changed sign in either correction.

**For reference, M-A9's ERA5-Land numbers** (JJAS 2020-2025, hourly, same sign convention,
`< 1,000 / 1,000-2,000 / ≥ 2,000 m` bands): **+0.6 h / −14.4 h / −11.9 h**.

### D7 alternate reading — gauge labels are really NPT (+6 h rotation)

⛔ **HISTORICAL SENSITIVITY ONLY, as of 2026-08-31.** ✅ **RESOLVED 2026-08-31 — DHM confirmed the timestamps are UTC and PERIOD-ENDING.** The as-labelled reading is correct; the NPT alternative is dead. This section is kept
because the rotation is still computed and published, and because its branch-cut behaviour
documents the estimator — **but the hypothesis it tests is settled, and nothing below qualifies
the primary result any more.**

Reported alongside, never gating (D7). This reading is now a **circular rotation of the very same
contributing cycles**, not a second pairing: the windows and every `n` are identical to the primary
reading, cell for cell (2,940 / 3,498 / 2,168 at D+1, and so on). An earlier revision rebuilt the
pairing at the V+6 alignment instead, which admitted a slightly different set of complete windows
(2,941 / 3,502 / 2,171) — a changed sample, which is exactly what D7 forbids.

Under the rotation **every per-station CIRCULAR offset moves by exactly +6 h modulo 24, by
construction** (the gauge share vector is `np.roll`ed; nothing else moves). The reported band figure,
however, is an ARITHMETIC median (`np.median`) of same-day-branch REPRESENTATIVES on `[-18, +6)` —
half-open at the TOP, since `%` returns `[0, 24)` so a raw +6 h lag maps to −18 h — and that is not
rotation-equivariant: as soon as a station's shifted offset crosses the branch cut it
re-enters the median from the other end. So a band number need neither shift by +6 h nor stay put.
Measured here, only three of the nine cells do shift by exactly +6 h — high at D+1 (**+0.48 h**), mid at
D+2 (**+0.22 h**) and high at D+3 (**−0.52 h**); the other six differ, D+3 low most visibly
(**−6.56 h**, i.e. −6 h ≡ +18 h rather than +6 h). That is a property of the branch cut and the choice
of estimator, not of the sample. Plan 216 D7's original wording — "shifts every offset uniformly +6 h
while leaving the between-band contrast invariant" — was mathematically wrong about the reported
statistic and has been amended in the plan; the primary reading remains the one the verdict is drawn
from.

## Reading the result

1. **The mid and high bands are NOT aligned.** Every lead in both bands is resolved well beyond the
   ±3 h bound — a ~5.5-6.5 h same-direction offset is roughly 2x the finest lag D4 permits
   distinguishing from zero. IFS shares ERA5-Land's DIRECTION of error (model too early) in the same
   two bands ERA5-Land flagged.
2. **The MAGNITUDE is smaller than ERA5-Land's, not the same.** Mid band ~40-45% of ERA5-Land's
   −14.4 h (−5.78 to −6.47 h, non-monotonic across D+1-D+3, so not a trend); high band ~46-55% of
   ERA5-Land's −11.9 h (−5.52 to −6.52 h, mildly increasing with lead). A drift toward the reanalysis
   number at longer leads is the direction D3 anticipates ("early leads inherit the analysis, later
   leads relax onto the model's attractor") — but three days of lead, on one season, is not enough to
   say whether it continues (this plan's D2 scope is one season at leads to D+3; that question is
   open, not answered here).
3. **The low band is not resolved beyond zero at any lead** (−1.34, −0.39, −0.68 h — every value is
   well inside the ±3 h D4 bound), consistent with
   ERA5-Land's own near-zero low-band number (+0.6 h): this screen cannot distinguish the low band
   from no error at all, at any of the three leads measured.
4. **Neither pole of the plan's own decision rule applies cleanly.** This is not "apparent alignment"
   (mid/high bands are clearly non-zero) and it is not "a repeated half-day (~12 h) displacement" either
   — the measured displacement is real, resolved, and repeated, but at roughly 40-50% of ERA5-Land's
   magnitude.

## Verdict (bounded by D4)

**NO-GO on deploying the originally proposed ~12 h correction as designed** — IFS's own measured
displacement in the two affected bands is roughly 40-55% of that magnitude; applying ERA5-Land's
larger correction to IFS forcing would systematically over-correct, by ~6-9 h.

**GO on finer-resolution correction work, calibrated to IFS itself rather than to ERA5-Land** — the
mid/high-band error is real (well beyond the ±3 h bound), repeated across three correlated lead strata
(D+1/D+2/D+3 pool overlapping initialisations and the same stations, so they are not independent
samples — the repetition still shows the same displacement is not a one-lead artefact, but it does not
multiply as three independent confirmations would), and in a consistent direction. That supports
**finer-resolution work to DETERMINE an IFS-calibrated correction** for those two bands — ⛔ not a
correction designed from this measurement itself, which D4's ±3 h bound cannot support. The low band is
unresolved from zero at every lead measured, so any correction there is on much weaker ground and should
not simply mirror the upper bands' sign.

This conclusion does **not** extend to "no timing correction is needed" (D4) — a ~5.5-6.5 h,
well-resolved, repeated error is not "no error", even though it is smaller than ERA5-Land's.

## Attribution

Contains modified Copernicus / ECMWF data. Attribution: ECMWF. Acknowledgement: contains modified data
from the TIGGE archive (`tigge-forecasts`, ECDS). ⛔ Research use only per D6's measured ~48 h embargo
— this is a screening result, not an operational dependency, for any centre.

## Non-goals (unchanged from the plan)

No correction was implemented. No magnitude or bias claim is made — phase only. No centre other than
ECMWF was pulled. No perturbed members. No multi-year climatology. Elevation-band weights, the DHM 2026
gauge request, and the free-alternatives escalation ladder all belong to whatever M-DEC authorises next,
not to this measurement.
