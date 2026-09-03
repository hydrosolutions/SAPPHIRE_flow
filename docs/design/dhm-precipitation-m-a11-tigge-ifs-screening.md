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

⚠️ **THIS SECTION IS THE JJAS 2025 READING. Its MAGNITUDES ARE SUPERSEDED (2026-08-31, Plan 220).**
Six seasons show the mid band spanning ~9 h on the preregistered branch, with 2025 near the middle by
chance. ⛔ **Every "~5.5-6.5 h"-style figure below describes 2025 alone and must not be quoted as IFS's
displacement.** What survives is the **direction** (model too early in mid/high bands, low band
unresolved from zero in 2025). See the multi-season section for the standing result.

1. **The mid and high bands are NOT aligned.** Every lead in both bands is resolved well beyond the
   ±3 h bound — a ~5.5-6.5 h same-direction offset is roughly 2x the finest lag D4 permits
   distinguishing from zero. IFS shares ERA5-Land's DIRECTION of error (model too early) in the same
   two bands ERA5-Land flagged.
2. **The MAGNITUDE is smaller than ERA5-Land's, not the same.** Mid band −5.78 to −6.47 h;
   high band −5.52 to −6.52 h (non-monotonic across D+1-D+3, so not a trend).
   ⚠️ **An earlier revision expressed these as "~40-45%" and "~46-55%" of ERA5-Land's. Those ratios are
   RETRACTED** — they are single-season, and six seasons show no stable fraction (Plan 220). A drift toward the reanalysis
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
   — the measured displacement is real, resolved and repeated, but smaller than ERA5-Land's.
   ⚠️ **An earlier revision put it at "roughly 40-50% of ERA5-Land's magnitude" from JJAS 2025 alone;
   six seasons show no single ratio holds** (Plan 220). ⛔ The direction survives; the fraction does not.

## Verdict (bounded by D4)

⚠️ **SUPERSEDED IN PART, 2026-08-31 (Plan 220).** This verdict was drawn from **JJAS 2025
alone**. Six seasons now show the mid-band offset spanning ~9 h (−2.31 h to −11.48 h on the
preregistered branch; still 8.22 h under a fixed four-station panel), with 2025 near the
middle by chance. ⛔ **The magnitude statements below no longer hold and must not be used to
size anything** — see the multi-season section. The NO-GO stands and is strengthened.

**NO-GO on deploying the originally proposed ~12 h correction as designed** — IFS's displacement differs
from ERA5-Land's in every season measured, so ERA5-Land's correction would misapply to IFS forcing.
⛔ **The over-correction is NOT quantified here**: an earlier revision put it at "~6-9 h" from the 2025
season alone, and the six-season spread shows that figure was an artefact of which year was drawn.

**GO on further MEASUREMENT — not on constructing a correction** — ⛔ an earlier revision said "calibrated
to IFS itself", which presumes IFS has a single displacement to calibrate against. **It does not**, on
this evidence. What is supported is more measurement (finer than 6-hourly, more stations, branch-robust
aggregation); what is **not** supported is building a correction from these numbers. The underlying
observation still stands — the
mid/high-band error is real (well beyond the ±3 h bound), repeated across three correlated lead strata
(D+1/D+2/D+3 pool overlapping initialisations and the same stations, so they are not independent
samples — the repetition still shows the same displacement is not a one-lead artefact, but it does not
multiply as three independent confirmations would), and in a consistent direction. That supports
**finer-resolution MEASUREMENT** of those two bands — ⛔ **not** work to determine a correction, and not
a correction designed from this measurement, neither of which D4's ±3 h bound nor the six-season spread
can support (Plan 220: there is no single displacement to calibrate against). The low band is
unresolved from zero at every lead measured, so any correction there is on much weaker ground and should
not simply mirror the upper bands' sign.

This conclusion does **not** extend to "no timing correction is needed" (D4) — the error is resolved
and repeated in every season measured, so it is not "no error". ⚠️ **An earlier revision quantified it
as "~5.5-6.5 h" from JJAS 2025 alone; that figure is superseded** — across six seasons the displacement
is real in direction but has **no single magnitude** (Plan 220).

## Attribution

Contains modified Copernicus / ECMWF data. Attribution: ECMWF. Acknowledgement: contains modified data
from the TIGGE archive (`tigge-forecasts`, ECDS). ⛔ Research use only per D6's measured ~48 h embargo
— this is a screening result, not an operational dependency, for any centre.

## Non-goals (unchanged from the plan)

No correction was implemented. No magnitude or bias claim is made — phase only. No centre other than
ECMWF was pulled. No perturbed members. No multi-year climatology. Elevation-band weights, the DHM 2026
gauge request, and the free-alternatives escalation ladder all belong to whatever M-DEC authorises next,
not to this measurement.

---

## M-A11b addendum — multi-season 2020-2025 (Plan 220)

**Retrieved and measured 2026-08-31.** Closes prerequisite 2 of this document's own verdict above: one
season cannot show interannual variability, and the independent review that read the single-season
result flagged that a multi-year check could move it by "potentially several hours or a sign change."
**It did.** This is more data through the exact same reviewed pipeline — same estimator, same amplitude
gate (`MIN_HARMONIC_AMPLITUDE = 0.05`), same `[-18,+6)` branch, same elevation and lead bands (D5) — not
a new measurement, a correction, or a climatology fit (the "no multi-year climatology" non-goal above is
about trend-fitting across years, which this is not: every season is its own independent point
estimate, plus one available-case pooled aggregate, D3).

**Code:** `TIGGE_YEAR`'s hard-coded default is deleted from `tigge_ifs.py`/`tigge_gauge_timing.py` (D1);
`--year` is now a required CLI argument on both scripts, and `tigge_gauge_timing`'s old
`--tigge-points` file selector is gone — a single `--year` reproduces the single-season report, more
than one triggers the per-year + pooled report below. The 244-run JJAS schedule is now the *expected*
count, not a required one (D2): a missing run would be recorded as a named gap on the file's
attribution sidecar rather than rejecting the season — moot here, since every retrieved season came back
complete.

**Regression check (D6):** re-running JJAS 2025 alone through the parameterised code
(`--year 2025 --skip-retrieve`, i.e. reusing the exact same GRIB Plan 216 downloaded) reproduces the
published table above EXACTLY — same 9 lag cells, same `n`, same amplitudes. The parameterisation is
inert, as required before any new data was fetched.

**Retrieval completeness — all six seasons, all complete.** Five ECDS requests (2020-2024; 2025 was
already on disk) each returned the full **244/244** expected initialisations, **zero missing inits**,
zero extras, zero duplicates. There is no completeness caveat to carry on any cell below — the year-to-
year differences that follow are not an artefact of gappy retrieval.

**2020 station coverage — thinner than every other year, as flagged in the plan.** The high band
(≥ 2,000 m) rests on **2 stations** in 2020, against 6-8 in every other year (even thinner than the
plan's own pre-registered count of 3 — the phase estimator's per-station gates, not just data
availability, decide final inclusion). Every 2020 high-band number below carries that hedge (D4): treat
it as the least reliable row in the table, not a like-for-like point against the other five years.

### Per-year and pooled phase offset (gauge timestamps as labelled, D7 primary reading)

Positive = model later than gauge, same convention as the single-season table above. `n(st)` is the
station count contributing to that cell (station-equal median, D5); the full window/station-day counts
and both D7 readings are in `data/dhm_precip/tigge/points/tigge_gauge_timing_offsets_multiseason.csv`
(gitignored, regenerable — see Regenerate-multi below). Every cell below is `status = ok`.

#### D+1

| season | low n(st) | low lag | mid n(st) | mid lag | high n(st) | high lag |
|---|---|---|---|---|---|---|
| 2020 | 8 | -3.65 h | 6 | -2.31 h | **2** | **+0.47 h** |
| 2021 | 9 | -1.85 h | 6 | -11.48 h | 7 | -9.44 h |
| 2022 | 9 | -1.42 h | 8 | -10.12 h | 8 | -6.75 h |
| 2023 | 9 | -4.38 h | 8 | -3.97 h | 7 | -7.94 h |
| 2024 | 7 | -1.40 h | 7 | -6.76 h | 7 | -6.40 h |
| 2025 | 8 | -1.34 h | 9 | -6.47 h | 6 | -5.52 h |
| **pooled** | 9 | -2.14 h | 8 | -9.23 h | 8 | -7.30 h |

#### D+2

| season | low n(st) | low lag | mid n(st) | mid lag | high n(st) | high lag |
|---|---|---|---|---|---|---|
| 2020 | 8 | -2.69 h | 6 | -8.17 h | **2** | **-0.96 h** |
| 2021 | 9 | -1.63 h | 8 | -10.30 h | 7 | -9.91 h |
| 2022 | 9 | -2.55 h | 8 | -9.85 h | 8 | -7.10 h |
| 2023 | 9 | -4.01 h | 8 | -3.81 h | 7 | -5.76 h |
| 2024 | 6 | -2.80 h | 7 | -9.51 h | 7 | -6.87 h |
| 2025 | 7 | -0.39 h | 9 | -5.78 h | 6 | -5.63 h |
| **pooled** | 9 | -2.40 h | 8 | -9.13 h | 8 | -7.42 h |

#### D+3

| season | low n(st) | low lag | mid n(st) | mid lag | high n(st) | high lag |
|---|---|---|---|---|---|---|
| 2020 | 8 | -2.53 h | 6 | **+0.37 h** | **2** | **-7.91 h** |
| 2021 | 9 | -2.04 h | 8 | -11.50 h | 7 | -9.09 h |
| 2022 | 9 | -1.27 h | 8 | -9.70 h | 8 | -6.73 h |
| 2023 | 9 | -3.39 h | 8 | -5.19 h | 7 | -7.97 h |
| 2024 | 7 | -2.34 h | 6 | -12.80 h | 7 | -6.67 h |
| 2025 | 8 | -0.68 h | 9 | -6.15 h | 5 | -6.52 h |
| **pooled** | 9 | -1.80 h | 8 | -9.93 h | 8 | -7.42 h |

**Pooled ≠ average of the per-year cells** — by construction (D3): `run_multi_season` concatenates the
six seasons' raw paired windows per (lead band, elevation band) and reruns the unchanged station-equal
estimator once on the union, so a station's pooled cycle is built from every window it contributed
across all six years, then the pooled figure is the median across stations. It is not a mean or median
of the six point lags above — e.g. D+1 mid's six per-year values average to about -6.9 h, but the pooled
figure is -9.23 h, closer to the higher-coverage 2021/2022 seasons whose stations dominate the 8-station
pooled set.

### Reading the result — the offset is NOT stable across years

**The independent review's flagged risk is confirmed: on the preregistered branch the mid band moves
by close to 10 hours across six seasons, and the high band's thinnest year returns a reproducible
point-estimate sign change that is unresolved from zero.**

⚠️ **All figures below are on the PREREGISTERED same-day branch `[-18,+6)`.** The cross-station median is **branch-dependent**: 2021's D+1 mid is **−11.48 h** here but **+3.26 h** under shortest-arc aggregation, while 2023 is −3.97 h under both. ⛔ The extremes are therefore **not** branch-invariant and must not be quoted as physical magnitudes. *(They are not a simple wrap — −11.48 and −3.97 are 7.51 h apart and do not straddle the cut — but the aggregate still moves with the convention.)*

🔬 **The spread survives a fixed-membership control.** Re-running D+1 mid on only the **four
stations retained in all six seasons** gives −2.31, −9.79, −10.53, −4.95, −5.43, −5.62 h — still an
**8.22 h range**. ⇒ Changing station membership is a confounder but **not the whole explanation**;
equally, this still cannot be called *pure* IFS-cycle variability.

1. **Mid band (1,000-2,000 m) is the most volatile.** D+1 ranges from **-2.31 h (2020)** to
   **-11.48 h (2021)** — a **9.2 h spread**, with no monotonic trend (2022 nearly matches 2021's extreme,
   2023 drops back to -3.97 h, 2024/2025 sit in between). D+3 2020's mid-band value is **+0.37 h** —
   indistinguishable from zero and of the opposite sign to every other year's mid-band reading — ⛔ a
   **reproducible point-estimate sign change, unresolved from zero**, not a demonstrated reversal. The
   2025-only screening this document opened with (mid ≈ -5.8 to -6.5 h, "40-55% of ERA5-Land's -14.4 h")
   sits roughly in the MIDDLE of this range, not at either end — a single season understated how far the
   **branch-selected** year-to-year spread reaches. ⛔ **Do not read a correction magnitude off these
   numbers**: had 2021/2022 been the season drawn, the same procedure would have reported a figure close
   to ERA5-Land's own — which shows the estimate is season-dependent, **not** that any of these values
   sizes a correction. The spread is measured on the preregistered branch and the aggregate is
   branch-dependent (above).
2. **High band (≥ 2,000 m) is directionally more consistent in the four full-coverage years
   (2021-2024: -6.4 to -9.9 h) but 2020's thin 2-station cell breaks that pattern** — near zero or
   positive at D+1/D+2 (+0.47 h, -0.96 h) before swinging to -7.91 h at D+3. Given D4's hedge (n=2), this
   reads as a genuinely thin, noisy cell rather than a reliable third data point for the high band's own
   interannual spread — but it is also not simply discardable (D4: include, hedge, do not exclude), and
   it is the only year that could NOT be characterised as "high band, consistently negative."
3. **Low band stays closest to zero across every year, but is not uniformly inside the ±3 h bound the
   way the 2025-only reading suggested.** 2023 crosses it at every lead (-4.38/-4.01/-3.39 h); 2020 is
   at or just past it at D+1 (-3.65 h). 2021/2022/2024/2025 stay inside. The low band's own
   interannual spread (roughly -0.4 to -4.4 h on the preregistered branch) is **small next to the
   mid/high bands' spread** and never approaches their magnitude or reverses sign. ⛔ Like every spread
   in this section it is branch-selected, not a physical magnitude.
4. **Pooled numbers track the higher-coverage years, not a simple average** — see above. Read the pooled
   row as an available-case aggregate weighted toward whichever stations have the most years of surviving
   data, never as "the" multi-year answer in place of the per-year spread.

### What this does and does not settle

**Answered, on the preregistered branch:** the M-A9/M-A11 question "is IFS's mid/high-band diurnal
timing error a single, stable number close to a third-to-half of ERA5-Land's" is answered **no** — it is a number that moves by
several hours between seasons on the preregistered branch, including one year whose point estimate is
near-zero and sign-changed but **unresolved from zero at n=2 under the ±3 h bound**, in the
thinnest-coverage band.

⛔ **What this does NOT settle, and this document does not advance:** how any IFS-calibrated correction
should be constructed. **This analysis cannot determine a single correction magnitude** — the spread is
branch-dependent in aggregate, rests on 2–9 stations per cell, and carries a ±3 h resolution bound.
⛔ Do not read the options for building one out of these numbers; that is later work under whatever
M-DEC authorises. The bounded conclusion is exactly two sentences: **2025 is not universal, and a single
correction magnitude is not determinable from this.**

**Not settled — the other three prerequisites the original independent verdict named remain open:**
estimator/branch sensitivity, station representativeness (the 2020 vs. 2022+ coverage gap is itself
part of this), and finer-than-6-hourly forcing. This section closes prerequisite 2 only. **It does not
by itself authorise retraining** (Plan 220's own exit condition) — if anything, the confirmed
interannual spread argues for MORE caution before committing to any single-point IFS correction, not
less.

### Regenerate (multi-season)

```
$ cd /Users/bea/Documents/GitHub/sapphire-ma6   # or SAPPHIRE_flow — data/dhm_precip is shared
DHM_PRECIP_XLSX=data/dhm_precip/combined_precipitation_37_stations.xlsx \
  uv run python -m scripts.dhm_precip.tigge_ifs --year 2020   # repeat per year 2020-2024; --skip-retrieve for 2025
DHM_PRECIP_XLSX=data/dhm_precip/combined_precipitation_37_stations.xlsx \
  uv run python -m scripts.dhm_precip.tigge_gauge_timing --year 2020 2021 2022 2023 2024 2025
```

Writes `data/dhm_precip/tigge/points/tigge_gauge_timing_offsets_multiseason.csv` (gitignored) — every
(season x lead band x elevation band x reading) cell, `season` one of `"2020"`..`"2025"`/`"pooled"`,
plus its attribution sidecar. Single-season `--year 2025` alone still reproduces the original published
`tigge_gauge_timing_offsets.csv` unchanged (D6).

---

## 🔴 M-A11c — the climatological phase error is NOT the event-timing error (2026-09-03)

⚠️ **Read this before quoting any number above in a runoff-forecasting context.** Everything earlier in
this document measures the **climatological mean diurnal-cycle phase** — a season-long average over
*all* hours. **Runoff responds to individual storms, not to average days**, and the two quantities are
different. This section measures the second one for the first time.

**Method.** IFS control `tp` from the **00Z init at ending leads 6/12/18/24 h** — four *consecutive*
6-hourly windows per day, so the stitched series is continuous at a defined D+1 lead. Gauge summed to
the same period-ending 6-hourly windows (all 6 hours required). An **event** is a gauge 6-h total
≥ q0.90 of that station's wet 6-h totals, declustered to ≥24 h. For each event, the offset is
`argmax(IFS in ±24 h) − t0`; if the IFS maximum is `< 0.5 ×` the event threshold it is counted
**missed**. JJAS 2020–2025, 26 stations, **1,189 matched events**.

🔴 **Scored against a NO-SKILL NULL**, without which none of it is interpretable: the IFS series is
circularly shifted by whole days, which **preserves IFS's own diurnal climatology exactly** and destroys
only the day-to-day correspondence. That is precisely "right climatology, zero event skill".

| statistic | null (10 shifts) | observed | verdict |
|---|---|---|---|
| median offset | **0.000** (range 0, 0) | **0.000** | **INSIDE the null — carries NO information** |
| within ±6 h | 0.330 (0.311–0.352) | **0.445** | above null |
| within ±12 h | 0.547 | 0.603 | above null |
| events **missed** | 0.488 | **0.311** | above null |
| IQR | 24.6 h | 18.0 h | above null, but ⛔ see below |

**Window sweep** (±12/24/36/48 h): the IQR simply **tracks the search window** — 12→12, 24→18, 36→30,
48→42 h. ⛔ **Do not quote an event-timing IQR as a property of IFS**; it describes the window. The
*increment over the null* is what is window-independent: **+0.109 to +0.126** at every width tested.

### What this establishes

**IFS carries real but modest sub-daily event-timing skill.** Given a significant gauge storm it places
its own peak in the correct 6-hour window **44.5 %** of the time against a **33.0 %** climatological
baseline, and detects **68.9 %** of events against a **51.2 %** baseline. Median IFS/gauge event
magnitude is **0.45–0.92** — systematic under-prediction of storm size.

Turned around: **>6 h out more than half the time**, ~a third of significant events missed entirely.
⇒ **Usable for daily volume. Not adequate on its own for sub-daily PEAK timing in a fast-responding
catchment** — but ⛔ "IFS is unsuitable" **overstates it**: the skill is real, and a routing model with
damping may still extract value. Suitability is a relation between this spread and the **catchment
concentration time**, not a property of IFS.

### Reconciliation with the climatological result — plausible, NOT yet tested

The large climatological phase error and the absence of a systematic *event* shift can coexist: the
climatological cycle averages over all hours and is driven by how often and how much it rains through
the day, including frequent small amounts near local noon (the parameterised-convection signature),
whereas the event statistic uses only the top decile of wet windows. A model can be badly displaced on
the average day while placing the largest storms without systematic bias.
🔴 **This reconciliation is untested.** The falsifiable prediction is that restricting the phase
calculation to *event* windows should shrink the offset, and restricting the event statistic to *small*
events should reveal it. Until that is run, treat it as a hypothesis.

### 🪤 Two of the author's own errors, recorded so they are not repeated

1. **A median offset of 0 was reported as "no systematic bias".** It is the **null expectation** — a
   uniform no-skill distribution in a symmetric window also has median 0. The null measured exactly
   0.000. ⛔ The median discriminates nothing.
2. **The first version filtered `ending_lead_hours == 24`**, giving only 2 windows/day (00Z, 12Z, 12 h
   apart) rather than a continuous 6-hourly series — half the events then *structurally* could not have
   a zero offset, manufacturing a spurious "IQR 24 h / 32 % within ±6 h". ⇒ **Always verify a
   constructed forecast series is gap-free before measuring timing on it.**

### Limits

⚠️ **Point-versus-areal**: this compares a point gauge to an IFS cell for patchy convective rain, while
the gateway serves **basin averages** (`docs/architecture-context.md:617`). ⇒ Probably a **LOWER bound**
on basin-average timing skill — unquantified. The station pairs sharing a cell (Kirtipur/Khumaltar share
an IMERG cell; Ilam/Kanyam share an IFS cell) could bound the representativeness floor; not yet done.
⚠️ **6-hourly archive, 3-hourly operational feed** — the gateway serves 3-hourly and offers **no
point-level access** (`dhm-precipitation-vision.md:440`), so a finer point-scale measurement is not
currently possible; a decisive version needs basin-average verification from the gateway itself.
⚠️ **Control forecast only**, not the 51-member ensemble.
🔴 **UNREVIEWED by an independent model.** Codex was unavailable (HTTP 404, two attempts, `codex-review.sh`
correctly reported "NO usable verdict") and the fallback reviewer failed on a 529. The null and the
window sweep were computed by the same author who made the two errors above.

### Regenerate

```
DHM_PRECIP_XLSX=data/dhm_precip/combined_precipitation_37_stations.xlsx \
  uv run python <scratch>/event_timing.py   # per-station offsets
DHM_PRECIP_XLSX=... uv run python <scratch>/null_test.py   # the null + window sweep
```
⚠️ Both scripts are scratch, **not tracked**. Promote them to `scripts/dhm_precip/` before this section
is relied on for a decision.
