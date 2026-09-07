# DHM precipitation — M-A12 GraphCast precipitation screening (Plan 240)

**Plan 240 T1-T2. Written 2026-09-04 against a live pull from the NOAA Open Data bucket
`noaa-oar-mlwp-data`, JJAS 2022-2025, `GRAP_v100_GFS` (GraphCast v1, GFS-initialised).** Answers: is an
AI weather model's archived precipitation product better or worse than the ECMWF IFS control forecast on
the axis that decides a flood peak — event magnitude and timing against the same 26 DHM gauges?

**Owner decides (M-DEC). This document does not.** ⛔ This is one archived operational product against
another, both GFS/ECMWF-initialised differently (D7) — it licenses no "AI vs physics" claim.

## Regenerate

```
$ cd /Users/bea/Documents/GitHub/sapphire-ma6   # or SAPPHIRE_flow — data/dhm_precip is shared
DHM_PRECIP_XLSX=data/dhm_precip/combined_precipitation_37_stations.xlsx \
  uv run python -m scripts.dhm_precip.graphcast_acquire --seasons 2022 2023 2024 2025
# ⛔ ONE model version per comparison — the driver REFUSES a pooled run (D2),
# and REFUSES any run whose two sides do not share identical (station, season)
# support (D6; `--allow-support-intersection` is the explicit, never-default opt-out):
DHM_PRECIP_XLSX=data/dhm_precip/combined_precipitation_37_stations.xlsx \
  uv run python -m scripts.dhm_precip.graphcast_ifs_compare --seasons 2022 2023 2024
DHM_PRECIP_XLSX=data/dhm_precip/combined_precipitation_37_stations.xlsx \
  uv run python -m scripts.dhm_precip.graphcast_ifs_compare --seasons 2025
```

Retrieval takes ~25-35 min (488 forecasts, ~5 GB, network-bound — see T1 below). GraphCast points data:
`data/dhm_precip/graphcast/points/tigge_station_series_jjas{2022..2025}.parquet` (gitignored). IFS
control uses the existing production tree `data/dhm_precip/tigge/points/` unchanged.

## T1 — measured, then stopped (2026-09-04)

Probed one forecast: `GRAP_v100_GFS/2022/0601/GRAP_v100_GFS_2022060100_f000_f240_06.nc`.

- **Bucket layout** (measured): `noaa-oar-mlwp-data` holds `AURO_v100_{GFS,IFS}`, `FOUR_v100_GFS`,
  `FOUR_v200_{GFS,IFS}`, `GRAP_v100_{GFS,IFS}`, `PANG_v100_{GFS,IFS}`. Confirmed independently (T1):
  `FOUR_v200_GFS` and `PANG_v100_GFS` carry no precipitation variable (`{latitude, level, longitude,
  msl, r/q, sp, t, t2, tcwv, time, u, u10, u100, v, v10, v100, z}` — no `apcp`/`tp`); only `GRAP_*`
  does. GraphCast archive starts 2022-01 (Nov/Dec 2021 partially present, excluded — JJAS 2022 is the
  first full monsoon season).
- **Publication schedule** (measured, corrects the plan's draft "2 runs/day"): **four** inits/day
  (00/06/12/18Z), not two. D1 uses only 00Z regardless.
- **Whole-file bytes**: 9,330,969,593 (9.33 GB) per forecast — one variable's worth of every lead
  0-240 h, 11 variables total. **Projected for 488 forecasts: ~4.55 TB.** Not attempted.
- **`xarray.open_dataset` (h5netcdf) bytes**: measured ~1.31 GB and ~60-70 s just to open one file
  (CF-decodes every variable's metadata before any data is touched) — ~130x the targeted route below,
  for zero additional information this plan needs.
- **Targeted route (chosen)**: open with `h5py` directly against the `apcp` dataset only, then one
  contiguous slice across the four needed time-steps (`dset[1:5]`, HDF5 chunk size `(1, 721, 1440)`,
  gzip + shuffle — h5py's own filter pipeline decodes both correctly). Measured: **10,157,690 bytes
  (~10.16 MB), 22 HTTP range requests, ~5 s**, real values sane (`apcp` min/max over the study box:
  -0.000135 m / 0.009417 m — the small negative is numerical noise, D4). **Projected for 488
  forecasts: ~4.96 GB.** Chosen route; ~920x smaller than a whole-file download.
- **Semantics, read off the real file's own attributes (D4)**: `apcp` — `long_name` "6-hr accumulated
  precipitation", `units` "m". This is already a genuine 6-hour **period-ending** total (unlike TIGGE's
  cumulative-from-init `tp`, which `tigge_ifs.py` deaccumulates) — **no deaccumulation step**,
  `valid_time = init_time + ending_lead_hours` directly, and the only conversion is metres → mm
  (× 1000). Global attrs confirming the D2 pin: `model_name=GraphCast`, `model_version=v1`,
  `initialization_model=GFS`.
- **Grid** (measured): regular 0.25°, latitude descending 90..-90 (721 points), longitude ascending
  0..359.75 (1440 points) — not TIGGE's irregular reduced-Gaussian point cloud. D5's nearest-cell
  operator (`tigge_ifs.nearest_point_index`, reused) runs against this grid's flattened mesh.
- **Disk**: 914 GiB free at measurement time — the ~5 GB projection is not a constraint.
- **Exactly one forecast retrieved during T1** (the probe above); no bulk retrieval, no extraction to
  the Plan-238 contract happened in T1 — that is T2.

### Route rejected, and why

Neither a whole-file download (~4.55 TB projected, and no additional information over the targeted
route) nor `xarray.open_dataset` (~1.3 GB/forecast just to open — CF-decodes 10 unused variables) is
proportionate for a 4-timestep, 1-variable, 26-point extraction. `scripts/dhm_precip/graphcast_acquire.py`
implements the targeted `h5py` route.

## T2 — retrieval, extraction, comparison

`scripts/dhm_precip/graphcast_acquire.py` retrieves the 488 pinned 00Z forecasts (JJAS 2022-2025, leads
6/12/18/24 h), extracts to the 26 DHM gauge points (D5), converts units and clips negative noise to zero
(D4), and writes the SAME seasonal-Parquet contract `ifs_event_timing.load_ifs_series` already reads
(`station, init_time_utc, ending_lead_hours, valid_time_utc, tigge_mm`) — D3's ordinary boundary
adaptation, no estimator change.

`scripts/dhm_precip/graphcast_ifs_compare.py` runs the **unmodified** `ifs_event_timing.build_cells` /
`report` twice — once against the GraphCast tree, once against the production IFS tree — asserts the
station-season support before ranking (D6) and prints both reports with the D7 initialisation confound
stated in its header.

⛔ **D6's support assertion fails closed.** If the two products' actual `(station, season)` support is
not **identical**, the driver **refuses to rank** and names the differing cells, because a cell missing
on one side is indistinguishable in the output from a difference in model skill. Comparing on the
*intersection* is available only behind an explicit `--allow-support-intersection` opt-in, which is
never the default and which prints the accepted asymmetry above the reports. Both runs reported here
pass the identical-support assertion (74 cells for v1, 21 for v3).

### 🔴 The archive changes model version mid-record — the seasons must never be pooled

The 2025 season tripped the original identity pin. Diagnosed by comparing 2024-09-30 against
2025-06-01 **directly from the bucket**: the physics is identical (`units='m'`,
`long_name='6-hr accumulated precipitation'`, shape `(41, 721, 1440)` float32, grid 90→−90 / 0→359.75,
`forecast_hour_step=6`, first/last hour 0/240, `initialization_model='GFS'`, `Conventions='CF-1.8'`).
Three attributes moved:

| attribute | 2024 | 2025 | reading |
|---|---|---|---|
| `model_name` | `GraphCast` | `graphcast` | case only — **cosmetic** |
| `model_version` | `v1` | *absent* | not a model identity; unusable as a pin |
| `version` | `1_2023-10-14` | `3_2025-02-20` | 🔴 **GraphCast v1 → v3 — a REAL model change** |

The original pin `('GraphCast', 'v1', 'GFS')` caught this **for the wrong reason** — only because
`model_version` went absent. It did not watch `version` at all, so a file that kept `model_version='v1'`
while `version` moved would have pooled two model versions **silently**. ⇒ `model_name` is now compared
case-insensitively, `model_version` is no longer pinned, `initialization_model` stays strict, and
`version` is **recorded on every row**, required **constant within** a season and allowed to differ
**between** seasons. The comparison driver refuses any run whose seasons span more than one version.

### Result — each version group against its OWN IFS control

⛔ Model version is **perfectly confounded with year** (v3 exists only in 2025), so the headline is each
group's skill **relative to the IFS control on the same seasons**, never the raw GraphCast numbers.
Increment = observed − null mean; the ratio is GraphCast's increment over IFS's on the same events.

⚠️ **Two denominators, and they are not the same number for the two products.** `events searched` is
the `missed` denominator; `matched` is the `exact` / `within ±6 h` denominator. The two products do
**not** search an identical event count at every window: `build_cells` drops an event that cannot
raise `min_candidates=3` forecast candidates inside the window, and at ±12 h GraphCast falls short on
six events the IFS control still searches. The per-window searched count for **each** product is
therefore given below rather than one figure for both.

⚠️ **`exact` and `within ±6 h` are higher-is-better; `missed` is lower-is-better**, so a *negative*
`missed` increment is the model beating its null. Sign alone does not say which product is ahead.

**v1 — JJAS 2022/2023/2024**, 26 stations, 74 station-seasons (identical station-season support both
sides, asserted before ranking):

| window | events searched (GC / IFS) | matched (GC / IFS) | `exact` incr. (GC / IFS) | `within ±6 h` incr. (GC / IFS) | `missed` incr. (GC / IFS) | ±6 h ratio |
|---|---|---|---|---|---|---|
| ±12 h | **1031** / 1037 | 548 / 686 | +0.042 / +0.086 | +0.058 / +0.122 | −0.143 / −0.205 | 0.48 |
| ±24 h | 1037 / 1037 | 640 / 785 | +0.065 / +0.072 | +0.111 / +0.142 | −0.145 / −0.175 | 0.78 |
| ±36 h | 1037 / 1037 | 690 / 855 | **+0.067** / +0.064 | +0.107 / +0.143 | −0.136 / −0.162 | 0.74 |
| ±48 h | 1037 / 1037 | 734 / 909 | **+0.062** / +0.055 | +0.102 / +0.130 | −0.131 / −0.157 | 0.79 |

**v3 — JJAS 2025**, 21 stations, 21 station-seasons (identical station-season support both sides,
asserted before ranking):

| window | events searched (GC / IFS) | matched (GC / IFS) | `exact` incr. (GC / IFS) | `within ±6 h` incr. (GC / IFS) | `missed` incr. (GC / IFS) | ±6 h ratio |
|---|---|---|---|---|---|---|
| ±12 h | 367 / 367 | 174 / 206 | +0.054 / +0.091 | +0.060 / +0.123 | −0.117 / −0.142 | 0.49 |
| ±24 h | 367 / 367 | 201 / 248 | +0.047 / +0.076 | +0.079 / +0.136 | −0.111 / −0.116 | 0.58 |
| ±36 h | 367 / 367 | 220 / 268 | +0.041 / +0.085 | +0.060 / +0.158 | **−0.096** / −0.090 | 0.38 |
| ±48 h | 367 / 367 | 238 / 291 | +0.029 / +0.076 | +0.042 / +0.140 | **−0.092** / −0.092 | 0.30 |

(**Bold** marks the four window×statistic cells where GraphCast is *ahead of* the IFS control. At v3
±48 h the two `missed` increments are −0.09210 and −0.09183 — a separation of 0.0003, which is noise
at this sample size, not a result.)

**Reading.** In **both** groups GraphCast beats its own whole-day-shift null on every statistic —
positively on `exact` and `within ±6 h`, negatively on the lower-is-better `missed`. Against the IFS
control it is behind on the statistics that matter for a flood peak, but **not uniformly**, so the
claim is stated at the precision the numbers support:

- **`within ±6 h` increment: GraphCast is lower than the IFS control at every window in both groups**
  — 8 of 8 window×group cells, the largest gap at ±12 h (ratio 0.48 / 0.49).
- **Matched events: GraphCast matches fewer at every window in both groups** — 8 of 8 (61.7 % vs
  75.7 % at ±24 h for v1; 54.8 % vs 67.6 % for v3).
- `exact window` increment: GraphCast is lower in 6 of 8 cells. ⛔ It is **higher** — better — at v1
  ±36 h (+0.067 vs +0.064) and ±48 h (+0.062 vs +0.055).
- `missed` increment: GraphCast is the weaker reduction in 6 of 8 cells. ⛔ It is **slightly stronger**
  at v3 ±36 h (−0.09591 vs −0.09046) and ±48 h (−0.09210 vs −0.09183).

⛔ **The v1/v3 difference is NOT distinguishable from a year effect**, and no claim of model change —
in either direction — is supportable:
- **n = 1 season** for v3. Interannual variability on this track is large: the measured IFS offset
  spans ~9 h across six seasons.
- The v3 group carries **1/3 the events** (367 vs 1037), so its sampling noise is ~1.7× wider.
- The groups do **not** rest on the same stations: 2025's 21 stations are a strict **subset** of the
  v1 group's 26 (absent: Bharatpur Airport, Ghorepani, Olangchunggola, Rajbiraj Airport, Sindhuli
  Madhi). So version, year **and** station composition all move together.
- D6's identical-support assertion holds **within** each group — it cannot hold between them.

⇒ What the two groups jointly support is the **narrow** finding: **GraphCast has a lower `within ±6 h`
timing increment and matches fewer gauge events than the IFS control, at every search window, in both
version-group comparisons.** ⛔ That is **two comparisons — one pooled over three v1 seasons and one
single v3 season — not four independent per-season results**, and it is not a whole-of-model verdict:
GraphCast leads on `exact window` at the two widest v1 windows. ⛔ Whether v3 is better or worse than
v1 is **not answerable from this archive**.

### Provenance

Plan 240, READY 2026-09-04 after an independent Codex review (NEEDS-CHANGES, cut the acquisition to
half its original size and named four correctness items — folded) and a confirming pass
(READY-TO-BUILD). Implemented and measured against the live bucket the same day; covered by
`tests/unit/scripts/test_graphcast_acquire.py` (27 tests, no network — every extraction test opens a
REAL in-memory HDF5 fixture through h5py's own gzip+shuffle filter pipeline, never a mock) and
`tests/unit/scripts/test_graphcast_ifs_compare.py` (9 tests pinning D6's fail-closed support
assertion, including the default policy itself).

A **third** independent Codex review — the first this implementation had, the `implement` workflow
having returned `reviews: {claude: null, codex: null}` — returned NEEDS-CHANGES on the written
conclusion and on two code paths, all folded 2026-09-04: the conclusion above was overstated (see the
per-statistic breakdown, which now names the four cells where GraphCast leads); D6's support check
*printed* an asymmetry and continued, and now refuses; and the acquisition opened the first requested
initialisation directly, so one absent file at the head of the run aborted every season instead of
being recorded as the gap D6 says it is (`probe_grid` now walks to the first available forecast). The
review's own suggested replacement wording — "lower `exact` and `±6 h` increments … in both
comparisons" — was **checked against the recomputed report rather than adopted**, and is itself too
strong: GraphCast's `exact window` increment is *higher* than the control's at v1 ±36 h and ±48 h.

The 2025 season was retrieved 2026-09-04 (122/122 forecasts, **0 gaps**, 12,688 rows,
`version=3_2025-02-20`). 2022 and 2023 are complete (12,688 rows each); 2024 has 12,480 rows with two
recorded gaps, **2024-08-26** and **2024-08-28** — recorded as gaps, never filled. 2022-2024 were
**not** re-retrieved for the version label: a metadata-only pass read each of their 364 source files'
HDF5 global attributes (no `apcp` chunks) through the same production pin function, and all 364
independently declare `1_2023-10-14`.
