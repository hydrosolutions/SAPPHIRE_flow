---
status: DRAFT
created: 2026-08-18
revised: 2026-08-18
plan: 184
title: M-A6 — gauge vs ERA5-Land comparison
scope: Compare the QC-masked DHM gauge series against ERA5-Land extracted at the same 26 stations, scale- and season-stratified, reporting named estimands with their retained-hour counts. Explicitly NOT IMERG (M-A5b), NOT the diurnal profile (M-A7), NOT the co-located Pyramid adjudication (M-A10), NOT any correction design, NOT annualisation.
depends_on: [173, 174]
blocks: [M-A8, M-A9]
source: docs/design/dhm-precipitation-milestones.md
---

# Plan 184 — M-A6 gauge vs ERA5-Land

## Status
**DRAFT.** Not for implementation until the owner confirms.

## This is the point of the track

Everything so far was preparation: M-A1 a reproducible baseline, M-A2 a canonical hourly axis, M-A3 the
QC mask, M-A4 the ERA5-Land acquisition, M-A5 the point extraction. **All inputs now exist on disk** —
six annual `hourly_mm` products (verified 2026-08-18: 365/366 accumulation days per year) and a
published extraction bundle for all 26 stations.

## What makes this hard, and what the plan must not do

**D1 — Estimands are NAMED, never implied.** Under an MNAR mask "monthly bias" is undefined: a
difference of masked sums is a *conditional accumulated difference*, and a ratio is unstable when
retained gauge precipitation is small. Report exactly:
- **matched-hour mean difference**
- **conditional accumulated difference**
- **wet-hour conditional intensity bias**

Frequency and categorical scores are reported **only** as conditional-on-retention estimands (vision
Rule 1). **No annualisation, ever.**

**D2 — The mask applies IDENTICALLY to both sides at pairing time.** The M-A3 mask is the gauge side's;
ERA5-Land is unmasked by M-A5's D4. Pairing is where they meet, and a comparison that masks one side
only is not a comparison — it measures selection. Every statistic carries its **common-retained-hour
count** and each side's own retention.

**D3 — Never report an hourly matched pair as the headline.** Representativeness error at one gauge vs
one 0.1° cell (~110 km²) dominates at hourly scale. Scale-stratify: report at the aggregation levels
where the comparison is meaningful, and state the scale beside every number.

**D4 — Warm season quantitative; cold-season high-altitude QUALITATIVE ONLY** (vision D6). Undercatch is
50–100 % for snow, so a winter "bias" at 3,700 m measures the gauge, not the model.

**D5 — Representativeness is CHARACTERISED, not decomposed.** One point against one cell cannot separate
grid-representativeness from model error. Characterise via M-A5's operator-sensitivity envelope, the
station-to-grid elevation difference, and neighbouring-cell variability — and label it a
characterisation. *(This downgrades vision D3a's wording, deliberately.)*

**D6 — Every result is SIGNED.** A post-QC gauge total is a **lower bound** on true precipitation. Each
figure carries that caveat, its named estimand, and the retained fraction it rests on.

## D7 — The elevation mismatch is now measured, and it predicts the answer

From M-A5's real run (`station_grid_elevation.csv`, 2026-08-18): ERA5-Land's model orography sits
**far above** our high stations — Syangboche **3,700 m vs 4,447 m (+747 m)**, Humde **3,401 m vs
4,700 m (+1,299 m)**. A 0.1° cell cannot resolve a valley, so the model's terrain is a smoothed
ridge-and-valley average.

⇒ **Expect a systematic, elevation-dependent discrepancy, and report it as such** rather than as a
station-by-station curiosity. This is also why D4's cold-season exclusion matters: a model whose terrain
is 1.3 km too high will phase precipitation as snow where the gauge sees rain.

**⚠️ Both datum fields are `UNKNOWN` on the station side** — DHM never stated one — so the mismatch is a
**datum-unreconciled difference** and must be labelled as one, not quoted as a precise altitude error.

## D8 — The within-cell pair (Plan 174 D6 handoff)

Kirtipur and Khumaltar fall in **one** ERA5-Land cell, 4.33 km apart, 30 m different in elevation, so
ERA5 returns an identical series for both. Their gauge-side disagreement is the only empirical
representativeness signal this track can compute — and it belongs here because it needs the M-A3 mask.

**It is confounded and must be reported as such:** Khumaltar swings **294 mm (2023) to 1,504 mm (2024)**,
a 5× inter-annual change of undocumented cause. Compute on hours retained for **both** stations
simultaneously; state the implication only conditionally — *if both gauges are unbiased over the
retained sample, half the observed pair discrepancy is a lower bound on the within-cell contribution at
~4.3 km*. **n = 1 pair, one valley, one separation. Never a network-wide estimate.**

## D9 — TWO THINGS THE `/plan` ROUND SURFACED THAT BELONG IN THE PLAN

*(A `/plan` sweep on 2026-08-18 stalled after expanding this doc from 90 to 1,235 lines and finding
blockers mostly in apparatus it had itself invented. The expansion is reverted. **Two of its findings
are real and are kept here**; both concern facts about the world, not about the document.)*

- **D9a — M-D3's "sum vs mean" is STILL UNANSWERED, and it rescales every magnitude statistic in this
  milestone.** DHM confirmed timestamps are period-ending and that off-grid rows are processing errors,
  but **has not said whether the hourly value is a SUM over the hour or a MEAN of sub-hourly samples**.
  A mean-of-samples reported as an hourly depth is wrong by a constant factor.
  **Scope of the damage, precisely:** it moves **MAGNITUDES** — the matched-hour mean difference, the
  conditional accumulated difference, the wet-hour intensity bias — but **not normalised SHAPE**
  (diurnal profile, wet/dry timing, between-station profile correlation).
  ⇒ **Every magnitude estimand in D1 is conditional on this answer.** Either report them with the
  dependency stated on the figure, or hold them until M-D3 resolves. **Do not silently assume SUM.**
  *(The plan's first draft omitted this dependency entirely — a real gap, not an invented one.)*

- **D9b — Read the operator-sensitivity schema from the artefact, not from memory.** Verified against
  the real published bundle (2026-08-18):
  `scope, station, season, statistic, quantile, nearest_value, bilinear_value, delta_absolute,
  delta_unit, ratio, n_hours_common_finite, n_hours_excluded, n_wet_nearest, n_wet_bilinear,
  sign_agreement_fraction`
  with **`statistic ∈ {QUANTILE, WET_MEAN_INTENSITY, WET_FREQUENCY}`** — *absolute / ratio /
  sign-agreement are COLUMNS, not statistic values.* **`sign_agreement_fraction` is legitimately NULL**
  on `STATION`-scope rows (it is an across-station quantity). M-A6 consumes this file for D5's
  representativeness characterisation; a validator that requires it non-null would reject every valid
  station row.

## Exit

Error characterisation across the 26 stations: named estimands (D1) at stated scales (D3), season- and
elevation-stratified (D4, D7), each signed per D6 and carrying its common-retained-hour count (D2); the
representativeness characterisation (D5); and the within-cell figure with its limits attached (D8).
**No correction is designed or recommended** — that is M-A9's decision node, not this milestone's.

## Non-goals

IMERG (M-A5b) · the diurnal profile (M-A7) · the co-located Pyramid adjudication (M-A10) · elevation and
regime structure (M-A8) · any bias-correction or downscaling design · annualised totals · any use of
ERA5-Land as a QC input (vision D4 — circularity).
