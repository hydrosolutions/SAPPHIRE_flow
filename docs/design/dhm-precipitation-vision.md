---
status: VISION
created: 2026-08-12
revised: 2026-08-12
title: DHM precipitation — QC, validation, and the case for operational use
scope: What we do with DHM gauge precipitation: how we quality-control it, what we validate against it, and under what conditions it could ever reach the operational forcing path. Sets direction only — milestones are broken out separately.
related: [143, 152, 153]
---

# DHM precipitation — vision

**Status: VISION.** Owner-aligned via grill-me 2026-08-12; revised the same day after an independent
Codex review (25 findings, 6 blockers). Not a plan. Breaks down into milestones separately.

## Source

`combined_precipitation_37_stations.xlsx`, delivered via the `2026_BSc_precipitation` channel
(`.../SAPPHIRE Flow - DHM Shared/04_students/2026_BSc_precipitation/2nd_meeting/`).
**sha256 `8dc57e4364ef788b022779a42df86918200d1c8dc723948f22657bc70ff98f57`.**
2020-01-01 → 2025-12-31, column 0 `Time (UTC)`, 37 station columns in mm, one sheet.

Sub-hourly data is out of scope per DHM, though 10–15 min data is what should be operationally
available for this project.

**The figures in Findings were produced by exploratory analysis that is not yet version-controlled.
They are provisional.** Milestone **M-A1** exists to re-derive every one of them from a
committed, parameterised script before any of them is relied on. Statistics below are computed on the
on-the-hour subset, JJAS where stated, with a 0.2 mm/h wet threshold where a threshold applies.

## The question

1. **Phase 1 (now) — research/validation.** What QC must and can we do, and what does this data tell
   us about the weather products we actually force our models with?
2. **Phase 2 (gated) — operational.** Can this data ever improve our forcing, and if so how?

Phase 1 does not commit to a Phase 2 design. That ordering is deliberate: undercatch means a
naively-fitted correction would *dry* the forcing of a flood-forecasting system.

## Findings

**M-A1 reproduction status (Plan 170, 2026-08-13, revised after a fixer
round):** the committed pipeline (`scripts/dhm_precip/`) reproduced 26 of 45
statistics exactly, corrected 5, and could not reproduce 14 on unmasked
ON_GRID data (see D6 in the plan and the disposition/evidence record in
`scripts/dhm_precip/expectations.toml` — counts there are parsed directly
from the manifest, not hand-maintained). Per-item notes are inlined below.
The external-citation ban is now lifted **only** for structural facts
(row/column counts, off-grid counts, station geometry) — every
intensity/coherence/climatology figure below stays barred until its own
successor milestone (M-A2, M-A3 or M-A7) lands.

A fixer round after the initial Phase-4a pass closed three gaps: the
3-hourly coherence figure, the modal-intensity Finding and the
leave-one-out tail-prediction-error Finding were quoted below but never
gated against the manifest (the LOO statistic was implemented and
unit-tested but never wired into the runner at all); it also fixed four
correctness bugs found by review (monotonicity checked against a
pre-sorted copy instead of delivery order; the daily/3-hourly coherence
buckets summed incomplete or all-null station-periods as a hard zero
instead of dropping them; per-station coverage divided by the
workbook-wide slot count instead of each station's own reporting span; the
modal-intensity statistic was wet-threshold-restricted even though this
Finding's own quoted range sits below that threshold). Two of those bug
fixes changed a previously-recorded `withdrawn_unreproducible` evidence
number (the daily coherence figure moved from 0.136 to 0.185; both remain
non-reproducing either way) — see `expectations.toml`'s per-entry
`method_comparison` for detail.

### Inventory
- **26 usable stations, not 37.** Eleven columns are empty across the entire file: Kathmandu Airport
  (AWOS), Dhankuta_AWS, Okhaldhunga_AWS, Chautara, Salleri, Sarmathang, Mai Pokhari, Gaighat,
  Dharan Bazar, Gaida (Kankai), Madi Kalyanpur.
- Median 21 stations reporting per hour.
- **No coordinates, elevation, DHM station IDs, instrument metadata or QC flags.** Missing values
  conflate "genuinely absent" with "removed by DHM QC".
- **The station-selection mechanism is unknown.** Every network-wide statement below is conditional
  on an unknown selection process and must not be generalised to "Nepal" until it is explained.

### Time axis — PARTIALLY RESOLVED 2026-08-13 (M-D3, via DHM)
- 55,379 rows against 52,597 clean hourly slots. Monotonic, no duplicates.
- **3,350 rows sit off the hourly grid** (minute ∈ {1–7, 10, 15, 30, 45, 55}), 0.6 % of observations,
  mostly Lukla and Udayapur Gadhi.
  **ANSWERED** — DHM (Sunny Maharjan, Senior Meteorologist, relayed by the student team, 2026-08-13):
  these sub-hourly timestamps are **processing errors**, to be **flagged and excluded**, not treated as
  valid readings. This retrospectively validates the `ON_GRID` view (D6): restricting to `minute == 0`
  was the correct call, not merely a declared convention. It also settles the Lukla sentinel count —
  the 46th sentinel sits on an off-grid row, so **45 is simply correct**, not just "this pipeline's
  number under its own method".
- **The accumulation convention is ANSWERED: period-ending.** A timestamp of 16:00 UTC denotes
  precipitation accumulated 15:00 → 16:00 UTC (same source).
  **This matters more than it appears: ERA5-Land is *also* period-ending.** The two therefore align
  directly, and the ±1 h phase uncertainty that blocked all diurnal analysis is **removed** — no offset
  correction is required at the M-A6 comparison.
- **STILL OPEN — sum vs mean.** Whether an hourly value is the *sum* of the 10-minute intervals or an
  independent measurement is not yet answered; a follow-up is with DHM. This is the question that
  changes totals by a **factor**.
  **Scope of the remaining block, precisely:** a sum-vs-mean error rescales every value by a constant,
  so it affects **magnitudes** (totals, intensity quantiles, mass fractions) but **not** normalised
  **shape** (diurnal profiles, wet/dry timing, between-station profile correlation). Diurnal *shape*
  results are therefore unblocked by the period-ending answer; magnitude results are not.

### Two *reporting* populations
| Group | Reported resolution | Stations | JJAS wet-hour fraction | q99.9 |
|---|---|---|---|---|
| **A** | 0.01 mm | Syangboche, Humde, Ghorepani, Lukla, Olangchunggola, Lete | 0.14–0.55 | 4.8–19.5 mm/h |
| **B** | 0.2 mm | the other 20 | 0.08–0.33 | 13–33 mm/h |

**M-A1 (Plan 170):** Group A's wet-hour fraction and q99.9, and Group B's q99.9, are
`withdrawn_unreproducible` — computed on unmasked ON_GRID data they read [0.080, 0.373],
[9.99, 41.91] mm/h and [33.02, 77.58] mm/h respectively. Group B's wet-hour fraction reproduces
exactly. The unmasked maxima are consistent with candidate defects still in the population (M-A1
builds no exclusion mask): Sindhuli Madhi's own q99.9 (72.2 mm/h) directly reflects its stuck-high
block below. See `expectations.toml` ids `prec_group_a_wet_hour_fraction`, `prec_group_a_q999`,
`prec_group_b_q999`.

Group A's modal non-zero value is 0.03–0.06 mm/h. **Reported decimal granularity is not proof of
instrument type** — it could equally reflect a different processing chain, unit conversion or
averaging step. "Weighing gauge vs tipping bucket" is a *hypothesis to confirm with DHM*, not a finding.
**M-A1: reproduces exactly** (0.03–0.06 mm/h) once the population is non-zero JJAS values with no
wet-threshold restriction — the vision's own range sits below the 0.2 mm/h wet floor, so a
wet-restricted population could never have reproduced it. See `expectations.toml` id
`prec_group_a_modal_intensity`.

Sub-0.1 mm hours are 22–34 % of Group A's wet hours but only **0.8–2.1 % of its recorded mass**. That
bounds the contribution of the noise floor *to the recorded total*; it does **not** establish that
Group A totals are accurate overall, which is a separate question requiring instrument metadata.
**M-A1:** the mass-fraction range is `withdrawn_unreproducible` — [0.43, 1.95] %
(`expectations.toml` id `prec_group_a_subthreshold_mass_fraction`); the floor is close but the
minimum (Ghorepani) sits below 0.8 %, tracking the same unmasked-extreme station as its own q99.9.

**Confound:** Group A is simultaneously the 0.01 mm-reporting subset *and* the high-altitude subset.
Reporting precision and altitude cannot be separated in this sample. No conclusion may attribute an
effect to one rather than the other.

### Defects that survived DHM QC
- **Sentinels**: Lukla carries 46 values of `-9999999.0`. **M-A1 (corrected):** the ON_GRID-scoped
  count (D7's declared population) is 45 — one sentinel sits at an off-grid timestamp
  (2022-02-01 08:15). 46 is still correct as a raw, any-minute count; 45 is this pipeline's number
  under its own declared method. See `expectations.toml` id `defect_lukla_sentinel_count`.
- **Stuck-high sensor**: Sindhuli Madhi, 2025-08-03 → 08-08, every hour pinned at ~72 mm →
  1,728.4 mm/day for four consecutive days, 8,642 mm in 120 hours. **M-A1: reproduces exactly**
  (120 hours, 8,642.2 mm) once the run-detection tolerance allows for sensor noise around the
  pinned value (72.0/72.2/72.4 mm) rather than requiring one bit-exact repeated value.
- **Long zero runs — CANDIDATE false zeros, not adjudicated.** Longest consecutive `0.0` run during
  monsoon: Aiselukhark 52.5 days, Nagarkot_AWS 36.9 d, Lete 35.5 d, Pakhribas 23.4 d, Simara 13.5 d,
  Kanyam 13.2 d, Biratnagar 12.2 d. A clogged or disconnected gauge is one explanation; QC-removed
  data written as zero, logger defaults, and station relocation are others. **They require
  adjudication before being called false zeros** — which the students' track does, and ours deliberately
  does not: we discard the periods wholesale instead.
- Consequence either way: Khumaltar 2023 totals 294 mm vs 1,504 mm in 2024, while passing a naive
  ≥85 % coverage filter. **Coverage % is not a usable quality filter for precipitation.**

Genuine extremes cross-validate: Tarahara 438 mm and Kanyam 403 mm on the same day, 2021-10-19 — the
documented mid-October 2021 eastern Nepal flood. This establishes that real daily totals reach
~400 mm. It does **not** establish a physical hourly ceiling; QC bounds must come from regional
extreme-value literature, not from this sample's maxima.

### Missingness is not wet-biased *(re-tested after review)*
Initially computed with an endogenous wet indicator. **Re-tested with the station under test excluded
from the regional wet indicator: median ratio 1.01, max 1.55, one station above 1.5.** Conclusion
holds — telemetry loss is roughly rain-independent. The bias risk lives in the zero runs, not the gaps.
**M-A1 (`withdrawn_unreproducible`):** reproduces median 0.73, max 1.21 over the 26 usable stations
— the qualitative conclusion (ratio near 1, no strong wet-bias) is directionally consistent, but the
regional-wet-indicator threshold is a declared method choice (D8b) the original analysis did not
record and it is not recoverable. See `expectations.toml` ids `clim_missingness_ratio_median`,
`clim_missingness_ratio_max`.

### Winter precipitation — open question, not a diagnosis
DJF share of annual total: Syangboche (3,780 m) 2.7 %, Lete 3.8 %, Ghorepani 5.4 %; but Humde 20.3 %
and Olangchunggola 17.6 %. Solid-precipitation loss in unheated gauges is *one* explanation.
East–west and windward–leeward winter climatology gradients across Nepal are large enough to produce
the same pattern. **Cannot be resolved without instrument metadata and coordinates.**
**M-A1 (`withdrawn_unreproducible`):** Humde's DJF share reproduces at 24.0 %, not 20.3 % — a ratio
of two unmasked sums, sensitive to the same unresolved axis/defect issues as the other provisional
statistics. See `expectations.toml` id `clim_djf_share_humde`.

### Structure — corrected after review
- **Spatial coherence, network-wide and undistanced**: median inter-station r, JJAS — hourly 0.05,
  3-hourly 0.09, daily 0.28. Computed across *all* station pairs with no distance stratification
  (we have no coordinates), so this describes the network as a whole and **says little about
  near-neighbour coherence**. Whether neighbour-based gap-filling is viable is *open* until pairs
  can be binned by separation distance. **M-A1 (Plan 170, coordinates now landed via M-D2):** the
  hourly figure reproduces exactly (0.047 → rounds to 0.05); the 3-hourly figure also reproduces
  exactly (0.085 → rounds to 0.09); the daily figure is `withdrawn_unreproducible` (reproduces at
  0.185, not 0.28 — daily aggregation of unmasked hourly values is far more sensitive to candidate
  defects than hourly comparison; the exact daily number moved from an earlier 0.136 to 0.185 after
  a fixer round fixed the daily bucket silently treating incomplete/all-null station-days as a
  recorded zero instead of dropping them, but neither reproduces 0.28). **Distance-stratified**
  coherence is now available and supersedes the "no coordinates" caveat: within 25 km, hourly
  r = 0.208, decaying to r = 0.030 beyond 200 km; daily r = 0.415 within 25 km, decaying to r = 0.146
  beyond 200 km — confirming near-neighbour coherence is real but the network is too sparse to
  exploit it directly (median nearest-neighbour distance 27 km, only 7 of 325 pairs closer than
  25 km). See `expectations.toml` ids `coh_hourly_r_undistanced`, `coh_3h_r_undistanced`,
  `coh_daily_r_undistanced`, `coh_hourly_r_within_25km`, `coh_hourly_r_beyond_200km`,
  `coh_daily_r_within_25km`, `coh_daily_r_beyond_200km`, and Plan 170 §"What changed since the DRAFT".
- **Intensity distribution — the earlier "r = 0.998, universal shape" claim was WRONG and is
  withdrawn.** Pearson correlation between quantile vectors is near-1 by construction (verified:
  exponential vs Pareto r = 0.943, exponential vs lognormal r = 0.950). Proper scale-normalised tests:

  | Test | Result |
  |---|---|
  | Shape ratio q99/q50 across stations | **8.5 → 55.3** (6.5× spread) |
  | CV of shape ratio | 0.11 @ q60 → 0.24 @ q80 → **0.40 @ q99** |
  | Leave-one-out prediction of held-out q99 from median × pooled ratio | median abs error 12.3 %, **range −49 % to +241 %**, only 65 % of stations within ±25 % |

  **M-A1 (`withdrawn_unreproducible`):** the shape-ratio range reproduces at its minimum (8.5) but
  not its maximum (60.0, not 55.3) — it inherits the same unmasked-q99 sensitivity as the
  intensity-quantile Findings above, since the ratio's numerator IS q99. The CV likewise reproduces
  at 0.45, not 0.40. See `expectations.toml` ids `prec_shape_ratio_range`, `prec_shape_ratio_cv_q99`.
  **The leave-one-out prediction-error figures were wired into the runner in a fixer round** (the
  statistic itself was implemented and unit-tested from the start, but never called from the
  pipeline or gated against the manifest — a real gap in the first Phase-4a pass, closed here):
  the median absolute error and the min/max range are all `withdrawn_unreproducible` (reproduces at
  15.6 % median abs error, range −57.3 % to +225.9 %, inheriting the same unmasked-q99 sensitivity);
  the within-±25 % fraction reproduces exactly at 65.4 % → rounds to 65 %. See `expectations.toml`
  ids `prec_loo_median_abs_error`, `prec_loo_min_error`, `prec_loo_max_error`,
  `prec_loo_within_25pct_fraction`.

  So: the distribution **body is moderately transferable; the tail is not**. For a flood system the
  tail is the operative part. A one-parameter scale model for extreme intensity is **not supported**.
- **Diurnal profile**: median between-station Pearson r = 0.347 (10th pct −0.206). This statistic
  mixes phase, amplitude and profile sharpness and does not isolate phase. It is **suggestive of
  weak transferability, not evidence of it** — and it rests on an unresolved time axis (above), so
  it cannot be relied on at all until the accumulation convention is confirmed. **M-A1
  (`withdrawn_unreproducible`):** reproduces at 0.29, not 0.347 — same unmasked-aggregation
  sensitivity as the daily coherence figure above, and independently still unreliable pending the
  accumulation-convention resolution named here. See `expectations.toml` id `coh_diurnal_median_r`.

## Literature grounding (added 2026-08-13)

A targeted sweep after M-A1. **Our elevation-dependent diurnal result is established science, not a
novel finding** — which is reassuring for the pipeline and, more importantly, changes what M-A6 should
expect from ERA5-Land. Full texts read for all four papers cited below.

### The elevation gradient is confirmed by three independent sources

| Source | Method | Result |
|---|---|---|
| Nepal J. Sci. Tech. (TRMM PR, 1998–2010, 0.05°) | satellite radar | Lesser Himalaya **~2,000–2,200 m** → afternoon–evening peak; southern margin **~500–700 m** → early-morning peak |
| Watters et al. (2021), via Hunt et al. (2022) | GPM-IMERG | early-morning maximum over the Himalaya with a cross-slope gradient — **earlier maximum at higher altitudes** |
| Hunt et al. (2022) | GPM-IMERG + CMORPH | foothill band peaks **0300 IST**; within it the **southern (lower) boundary peaks 1–2 h later than the northern** |

All three give the direction our data gives: **higher = earlier**. Our hills (1,200–2,150 m) peak
22–02 local; our Terai (67–223 m) peaks 03–08 local. Barros et al. (2000), over Nepal specifically,
add that the cycle is bimodal but **in valleys only the early-morning peak is present**.

Season matters, and the JJAS restriction was right: the pre-monsoon (MAM) has an *afternoon* maximum,
the monsoon (JJA) a midnight–early-morning one.

### The mechanism, and why elevation is the predictor

Hunt et al. (2022) separate two modes: a late-afternoon **convective** peak (~1700 IST, enhanced by
anabatic upslope flow) and a stronger early-morning **katabatic** peak (~0200 IST, nocturnal downslope
flow converging with the background monsoon circulation, strengthened when the monsoon trough is
active). **Our elevation gradient is best read as the elevation-dependence of which mode dominates** —
hills convective, lowlands katabatic. Hunt's own latitude stratification finds the cycle's amplitude
*and* the relative magnitude of the nocturnal peak both decrease with elevation. The NJST result that
*"the morning precipitation moves southward in the mature monsoon season"* supplies the propagation.

Elevation is therefore not an arbitrary correlate but a **proxy for position along a propagating
overnight system** — which is why it predicts phase and horizontal distance does not.

### One mechanism explains BOTH of our structural findings

NJST decomposes the two regimes by character, not just timing: *"early-morning rainfall over foothill
regions … is a consequence of strong conditional rain rate whereas afternoon to midnight rainfall
maxima over LH regions is a consequence of relatively very high frequency of rainfall."*

So the lowland early-morning peak is **intensity-driven** (fewer, harder events) and the hill evening
peak is **frequency-driven**. That predicts heavier tails at low elevation — which is what we measure
(Terai q99 ≈ 29–38 mm/h against 6–15 mm/h at high altitude). **Our intensity gradient and our diurnal
gradient are the same physical structure**, not two separate results.

### ERA5's foothill phase error is far larger than the global figure

The global number often quoted is ~2 h early. **In the Himalayan foothills it approaches 12 h.**
Hunt et al. (2022): the reanalyses *"extend the tropical convection signal northward over the
foothills, placing a mid-afternoon peak along much of the region, at odds with the two satellite-based
datasets. Early-morning peaks are only present, for both reanalyses, towards the far northwest and at
small, isolated locations along the very southern boundary."* Observations put the foothill peak at
0300 IST.

Three points make this structural rather than incidental:
- **ERA5 and IMDAA suffer the same error** despite different underlying models (ECMWF IFS vs Met
  Office UM) and different assimilated data — a failure of parametrised convection, not a vendor quirk.
- **Norris et al. (2017)**: below 3 km the cycle is bimodal (0600 and 2100 LT), above 3 km unimodal
  (1500 LT), and a **parametrised-convection run cannot capture the nocturnal 0600 peak at lower
  elevations**. Moving to 2.8 km explicit convection fixed it; further refinement added little.
- ERA5 gets the Indo-Gangetic Plain (1500 IST) approximately right — the failure is specific to the
  orographic band where nearly all 26 of our stations sit.

### Consequences for this programme

1. **M-A6 must treat a diurnal phase error as expected model behaviour**, never as evidence about
   gauge quality. Finding it confirms the literature; it says nothing about the gauges.
2. **D6 is sharpened.** ERA5 *over-estimates* precipitation over the Himalaya while gauges
   *under-catch*. Both errors push the same way, so "ERA5 is wetter than the gauge" is **doubly**
   uninformative about which is right.
3. **The "inherit timing from the NWP" Phase-2 option is now disfavoured** — see the milestone doc.
   Inheriting a 12-hour-displaced diurnal cycle would import a large systematic error.
4. **IMERG caveat**: Hunt notes GPM-IMERG *"performance falls at higher elevations or when quantifying
   extreme precipitation events"* — relevant to D4's adjudication idea, though our own track dropped
   IMERG when wholesale zero-run removal was chosen.
5. **Our QC finds defects published work would likely miss.** Adhikari et al. (2025) screened outliers
   by *neighbour corroboration* (>100 mm/h "without supporting nearby station evidence") — but our
   measured median nearest-neighbour distance is 27 km with hourly r ≈ 0.05–0.24, so corroboration is
   weak in Nepal; and they excluded stations with >20 % missing, the coverage filter Khumaltar 2023
   shows to be inadequate. **Neither rule catches a stuck-zero run or a stuck-high block.** Published
   Nepal hourly statistics may carry the same defects.
6. **DHM operates ≥63 hourly AWS.** Adhikari et al. used 63, noting a "recent expansion of the AWS
   network by DHM". We received 37 columns, 26 usable — worth raising alongside M-D3.

### Where our data does NOT fit the literature

**Group A above ~2,500 m — HALF RESOLVED by M-A10 (2026-08-18).** Norris et al. predict unimodal,
peaking 1500 LT above 3 km. Olangchunggola peaks 03 UTC and Lukla 02 UTC (≈ 08–09 local), matching
neither the literature nor our own hill band.

**Lukla's 02 UTC peak was a QC artefact and is now retired** — see the M-A10 record in
`dhm-precipitation-milestones.md`. It was 6 sentinel values of −9999999 at 02–03 UTC normalised over an
UNMASKED profile (which drives the grand mean negative and flips the normalisation's sign). Under the
M-A3 mask Lukla peaks at **16 UTC ≡ 21 NPT**, within 2 h of Pyramid AWS3's independent 23 NPT.

**Olangchunggola's 03 UTC ≡ 08 NPT peak REMAINS UNRESOLVED**, and is neither a sentinel artefact (it
carries zero JJAS sentinels) nor noise-floor contamination (the peak is immovable across the 0.0 / 0.1 /
0.2 mm ablation ladder). It has no co-located Pyramid station, so M-A10 cannot adjudicate it. The
high-altitude morning peak is therefore a **single-station** open question, not a Group A pattern — the
earlier "both are the most noise-contaminated stations (54–55 % wet-hour fractions)" framing conflated
two stations with different causes. This is awkward: it is the band that matters most for Dudh Koshi
runoff.

### References

- Hunt, K. M. R., Turner, A. G., and Schiemann, R. K. H.: Katabatic and convective processes drive two
  preferred peaks in the precipitation diurnal cycle over the Central Himalaya, *Q. J. R. Meteorol.
  Soc.*, 148(745), 1731–1751, doi:10.1002/qj.4275, 2022. (CC BY)
- Adhikari, S., et al.: Analyzing extreme precipitation during the prolonged summer monsoon of 2022 in
  Nepal: insights from hourly observational data, *J. Inst. Sci. Tech.*, 30(1), 179–188,
  doi:10.3126/jist.v30i1.70014, 2025.
- Dawadi, B., et al.: Diurnal cycle of precipitation and extremes in Nepal, *J. Inst. Sci. Tech.*
  (IMERG, 2015–2021). Monsoon diurnal peak ~0.65 mm/h around midnight, minimum ~0.2 mm/h late morning;
  monsoon extremes exceed 15 mm/h; intensity concentrated in mid- and low-elevation central/eastern
  Nepal.
- *Spatial Variations in the Diurnal Pattern of Precipitation over Nepal Himalayas*, *Nepal J. Sci.
  Tech.* (TRMM PR, 1998–2010).
- Norris, J., et al. (2017) and Barros, A. P., et al. (2000) — as cited within Hunt et al. (2022);
  not read in full.

### ⭐ CO-LOCATED INDEPENDENT GAUGES — the Pyramid network (added 2026-08-18)

**Salerno et al. (2025), ESSD 17, 4293** — the **Pyramid Meteorological Network**: 7 in-situ AWS in the
Khumbu (Dudh Koshi), **hourly**, 1994–2023, **CC BY 4.0**, on Zenodo (`10.5281/zenodo.15211352`) with
**no login** (the mountaingenius geoportal asks for registration; it is not needed — Zenodo carries the
same Level-1 data). Precipitation (`RR`) exists at 5 of the 7; timestamps are **NPT (UTC+5:45)**.
Level-1 files are raw and **ungapfilled**.

**Why this matters more than any gridded product we have surveyed: it is INDEPENDENT of both sides of
our comparison** (no reanalysis, no satellite, no DHM input — "ERA5" appears nowhere in the paper),
and **two of its stations are effectively co-located with two of our four problem high-altitude
stations**:

| Our DHM station | Pyramid station | Separation |
|---|---|---|
| Lukla Airport (27.69, 86.73, 2,860 m) | AWS3 Lukla (27.70, 86.72, 2,660 m) | **~1.4 km** |
| Syangboche Airport (27.817, 86.717, 3,700 m) | AWS5 Namche (27.80, 86.71, 3,570 m) | **~1.9 km** |

⇒ **This gives the track its first genuine GAUGE-vs-GAUGE comparison.** Every prior option compared a
gauge against a satellite or reanalysis field; no such product can settle whether a gauge is wrong.

#### ⚠️ The Group A high-altitude diurnal result now looks like an ARTEFACT, not physics

Computed directly from the Level-1 files (JJAS, all years; **independently recomputed by the
orchestrator, not taken on report**):

| Station | Peak hours (NPT) | Minimum | Normalised at 07, 08 |
|---|---|---|---|
| AWS3 Lukla 2,660 m | **22, 23, 00** | hour 10 | **0.29, 0.28** |
| AWS5 Namche 3,570 m | **23, 00, 22** | hour 11 | 0.36, 0.18 |

**~~Our DHM Lukla peaks at 02 UTC ≡ 07:45 NPT — squarely inside Pyramid Lukla's diurnal MINIMUM~~ —
WITHDRAWN 2026-08-18, the figure was a QC artefact.** The 02 UTC peak is reproducible ONLY on unmasked
data: Lukla carries 6 sentinel values of −9999999 at 02 UTC (5) and 03 UTC (1) in JJAS, which drag the
grand mean to −2,499,750 mm; normalising against a negative mean flips the sign, so the most-contaminated
hour reports as the largest positive "peak" (+20 normalised at 02 UTC) while genuine rain at 16 UTC
(+472 mm) reports as −0.00. Under the M-A3 mask Lukla peaks at **16 UTC ≡ 21 NPT** — 2 h from Pyramid
AWS3's 23 NPT, i.e. **agreement, not anti-phase**.

**The H1 reading it supported is correspondingly NOT supported.** M-A10's first real run finds the
nocturnal peak immovable across the whole ablation ladder at both co-located pairs — the opposite of the
signature noise-floor contamination would leave. The formal verdict is INDETERMINATE (D5 adequacy: five
monsoon seasons cannot pin a peak hour to ±2 h), so this is recorded as *evidence against H1*, not as a
settled refutation of it. See the M-A10 record for the per-pair numbers.

**⚠️ One reported figure did NOT survive checking, recorded so it is not propagated:** the
investigating agent gave Pyramid Lukla's wet-hour fraction as 11.7 % against our 54–55 %. Recomputed
over **JJAS** at the 0.2 mm threshold it is **33.1 %** — the agent appears to have used the full annual
record. The gap is real but far softer than reported, so **the wet-fraction argument is weak; the
diurnal-phase argument is the load-bearing one.**

#### Norris et al.'s 3 km threshold does not hold in the Khumbu

The section above treats *"below 3 km bimodal 0600/2100 LT; above 3 km unimodal 1500 LT"* as the
literature baseline our data failed to match. In the Khumbu, **nocturnal peaking persists at 3,570 m
and 4,260 m**, and the afternoon mode co-emerges only at **5,035 m** (where amplitude also collapses,
max/min ≈ 3 against 10–16 lower down). ⇒ **The baseline itself is wrong for this valley.** Our data
not matching it was never, on its own, evidence that our data were wrong.

#### ⛔ NOT a correction source — D6 is REINFORCED, not relaxed

The obvious temptation is to use co-located independent gauges to correct our own. **Do not.** The
paper is candid that *"the main weakness of this data network is the lack of heated rain gauges"*.
**Pyramid's gauges are unheated, so they undercatch snow in the same direction and for the same reason
as DHM's.** Correcting toward them would **not** remove undercatch — it would substitute one
undercatching gauge for another *while appearing authoritative*, which is worse than no correction at
all. And the flood-safety hazard is unchanged: ERA5-Land over-estimates over the Himalaya while both
gauge networks under-catch, so fitting a downward correction toward either injects a **dry bias into a
flood-forecasting system**.
Its ≤20 % snow-underestimate figure is **inherited from Salerno et al. (2015), not re-measured here** —
it is not a transfer function and must not be used as one.
⇒ **Use Pyramid as a referee on SHAPE and TIMING** — normalised diurnal profiles, wet-hour fraction,
seasonality — **where undercatch largely cancels. Never as a magnitude reference.** [[D6]] [[D9]] stand.

**Corroboration and a remaining open question.** Pyramid's DJF share of annual precipitation (Lukla
3.2 %, Namche 2.7 %, Pheriche 1.4 %, Pyramid 0.9 %) matches our Syangboche 2.7 % and Lete 3.8 %,
leaving **Humde 20.3 % and Olangchunggola 17.6 % as the outliers**. But those two sit in Manang and
Kanchenjunga, not the Khumbu, so a real regional gradient remains a live alternative — this **narrows**
the question rather than closing it.

**Caveats on the source itself:** no instrument make/model, orifice height or wind exposure is given in
either the paper or the README, which caps how far any quantitative bias attribution can go; and
station longitudes disagree between the paper's table and the README for South Col and Changri Nup
(they appear transposed). Neither of those two carries precipitation, so nothing above is affected,
but the metadata warrants a check before any spatial use.

## Decisions

| # | Decision | Rationale |
|---|---|---|
| **D1** | **Two phases: validation/research first, operational use as a gated second question.** | Undercatch means a correction fitted now would dry a flood-forecasting system. |
| **D2** | **Validate against ERA5-Land first**, not IFS. | Hourly, continuous, spans the full record, already subscribed. We hold no IFS forecast archive back to 2020 and the gateway offers no point-level access. |
| **D2a** | **Do NOT claim this directly quantifies Plan 152's OOD-forcing risk.** | aquacast is *trained* on ERA5-Land and would be *run* on ERA5-Land, so an ERA5-Land bias vs gauges partially cancels rather than propagating. Gauge-vs-ERA5 bias is evidence about ERA5-Land, not directly about aquacast's input-distribution risk. The two questions are related but distinct. |
| **D3** | **Scale-stratified, metric-matched comparison.** Monthly/seasonal → bias magnitude and elevation dependence. Daily → wet-day frequency, intensity distribution, categorical skill. Hourly → climatological composites. | A 9 km cell in the Khumbu spans >2,000 m of relief. |
| **D3a** | Hourly matched pairs may be **computed and reported, but never interpreted as model error** without an explicit representativeness-error decomposition alongside. | A categorical ban would suppress the operationally relevant quantity. The danger is misattribution, not the statistic itself. |
| **D4** | **Zero-run adjudication uses a satellite QPE. ERA5-Land is NEVER a QC input.** Use **IMERG Early/Late (satellite-only)**, not IMERG-Final. | Using ERA5 to clean the reference and then judging ERA5 by it manufactures agreement. IMERG-**Final** incorporates GPCC gauge analysis and is therefore *not* independent of the gauge network; Early/Late are satellite-only. Used strictly as a daily wet/dry discriminator — it underestimates in high mountains and is weak on snowfall, never a magnitude reference, and its own miss rate must be characterised before it adjudicates anything. |
| **D5** | **Harmonise to a common 0.2 mm/h detection floor for all frequency statistics; keep unthresholded values for mass statistics.** | The two populations have incomparable reporting granularity. Any pooled wet-day frequency or POD/FAR compares reporting chains, not climates. |
| **D6** | **No numeric undercatch correction. Season-stratify and carry a signed caveat — scoped correctly.** | Transfer functions are gauge-type-specific; we know neither gauge type nor wind speed. **The sign constraint applies to catch efficiency on POST-QC data only: for a correctly-functioning gauge, catch ≤ true precipitation.** It is *not* a universal bound on reported values — this sample contains a gauge reporting 1,728 mm/day. Stated correctly: *the constraint is on CATCH EFFICIENCY of a correctly-functioning gauge, never on the observed total.* **⛔ CORRECTED 2026-08-19: an earlier wording here — 'a post-QC gauge total is a lower bound on true precipitation' — OVERREACHED and is withdrawn.** Our QC is a physical-impossibility gate, not an outlier filter (Plan 173 D4: `value_max = 200.0` mm/h, chosen to be unreachable rather than discriminating), so an isolated spurious value ≤200 mm/h survives and can push a post-QC total ABOVE truth. Post-QC removes the sentinels and the SUSTAINED stuck-high blocks — it does not make a total a bound. **REINFORCED 2026-08-18:** the co-located Pyramid AWS (see Literature grounding) are **also unheated** and undercatch in the SAME direction, so they are **not** a correction source — using them would swap one undercatching gauge for another while looking authoritative. Referee on SHAPE and TIMING only, never on magnitude. |
| **D7** | **Phase 1 characterises; it commits to no correction design and pre-selects no Phase-2 hypothesis.** *(D7 and D8 merged after review.)* | The earlier preference for temporal disaggregation rested on the withdrawn r = 0.998 result. With the tail shown to be non-transferable (−49 % to +241 %), neither temporal nor spatial correction is established as viable by this data. Phase 1 measures both and decides afterwards. |
| **D8** | **Time-boxed research, judgement call at the end.** No pre-registered thresholds. | Genuinely exploratory work on a thesis cycle. *Accepted risk: with no stated bar, a null result is harder to declare.* |
| **D9** | **Safety constraint, independent of D8:** no precipitation-derived correction reaches the operational forcing path without a hydrological test showing it does not degrade discharge forecast skill — evaluated on high-flow events, not on all-flow averages. | D8 governs the *research* gate, which the owner chose to leave to judgement. This is a separate *deployment* constraint. Better agreement with gauges does not imply better runoff forcing, and an all-flow average can improve while flood performance degrades. |
| **D10** | **Durable output: QC rules + regression fixtures + a versioned QC'd dataset — held in a research data folder, NOT onboarded.** | The defects are properties of the delivered file. Whether they recur live is **unverified** — the operational API, cadence and encoding are unknown, so this is an expectation, not a fact (see R7). |
| **D11** | **Do not block on DHM metadata.** Ask whoever assembled the file first; send DHM the formal request in parallel. | Most QC and per-station work proceeds without coordinates. |

## QC design

Today precipitation has only **daily** rules in `config/qc_rules.py` (`range_check` 0–500 mm,
`gross_outlier` k=5) — nothing at sub-daily step.

| Defect | Approach | Repo reality |
|---|---|---|
| Sentinels (`-9999999`) | `range_check` at hourly step | Config only. Bounds must be sourced from regional extreme-value literature, **not** from this sample's maxima |
| Stuck-high (Sindhuli 72 mm × 120 h) | `frozen_sensor` **excluding zero values** | **Code change, not config.** `_apply_frozen_sensor` (`services/qc.py:92`) accepts only `tolerance` and `min_consecutive`; it has no value-based exclusion. Without one it would fire on every legitimate dry spell |
| Long zero runs | New rule: run length vs the station's own seasonal dry-spell climatology, adjudicated by IMERG Early/Late | New rule. Detection floor: gross runs caught, 3–10 day runs not |
| Sub-tip noise (Group A) | 0.2 mm/h floor, frequency statistics only | Never applied to mass statistics |
| Off-grid timestamps | Normalise with recorded provenance | Blocked on the accumulation convention |
| — | **Do NOT reuse `gross_outlier` for precipitation** | `_apply_gross_outlier` (`services/qc.py:197`) is a symmetric `\|value − mean\| > k·std` test against a climatological baseline. On a zero-inflated, right-skewed variable it flags real heavy rain and never flags zeros |

Flag, do not delete. `QcStatus` provides `RAW / QC_PASSED / QC_FAILED / QC_SUSPECT / MISSING`, which
maps onto but is not identical to the WMO-168 vocabulary (good / suspect / erroneous / missing).

## Repo capability gaps

Prerequisites for any Phase-2 operational use. *Corrected after review — the onboarding gap was
overstated.*

1. **No weather-station observation ingest.** `flows/ingest_observations.py:461-462` fetches only
   `RIVER` and `LAKE`. This is the real gap.
2. **Onboarding already handles weather stations** — `services/onboarding.py:794` branches on
   `StationKind.WEATHER` and skips model assignment for them. *Not* a gap.
3. **No DHM precipitation adapter**; DHM's operational precipitation API is unconfirmed.
4. **No sub-daily precipitation QC rules**, and `frozen_sensor` needs a code change (above).
5. **No gauge-catch-efficiency guidance in `docs/standards/wmo.md`** — WMO-168 Vol I Ch.6 and
   WMO-SPICE absent from the inventory.
6. **No point-level NWP access** — `adapters/recap_gateway.py` fetches per registered polygon and
   prefilters to basin averages; no lat/lon or grid-cell path.

## Ownership — superseded by the milestone decomposition

**An earlier revision of this section listed a research programme as "student-led; we specify and
consume". That is superseded.** The owner has since set the scope: **we run a full independent
track**, and the BSc students run a parallel thesis on the same data whose output is **corroboration,
never a dependency**.

The authoritative breakdown is **`docs/design/dhm-precipitation-milestones.md`**. Where the two
documents disagree, the milestone document wins.

Two decisions above are narrowed by that scope change:

| Decision | Status under the independent track |
|---|---|
| **D4** (IMERG adjudication of zero runs) | **Applies to the students' track, not ours.** We drop candidate zero-run periods **wholesale without adjudication**, so we need no independent arbiter. IMERG remains available if the comparison turns out to need it |
| **D8** (time-boxed, judgement call) | Governs the thesis. Our track is paced by our own milestones and ends at an explicit Phase-2 GO/NO-GO decision node (`M-DEC`) |

Wholesale zero-run removal carries a cost this document must record: the retained sample is
**missing-not-at-random**, and identical masking of ERA5-Land makes the *pairing* consistent without
recovering unconditional behaviour. Wet-day frequency, FAR, CSI, unconditional intensity
distributions and diurnal means all stay biased and are reportable only as conditional-on-retention
estimands. See the milestone document's Rule 1.

Similarly, **D3a is downgraded there**: one point gauge against one ERA5 cell cannot empirically
*decompose* representativeness error from model error. It can only be *characterised* — via
extraction-operator sensitivity, station-to-grid elevation difference, within-cell topographic spread
and neighbouring-cell variability.

## Relation to other work

- **Plan 152 (aquacast)** — see D2a. Related but not a direct de-risking.
- **Plan 153 (multi-resolution)** — DHM precipitation is hourly/sub-hourly.
- **Plan 143 (DHM onboarding)** — targets river gauges; the weather-ingest gap is not in its scope.
