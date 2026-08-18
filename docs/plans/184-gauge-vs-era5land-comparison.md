---
status: DRAFT
created: 2026-08-18
revised: 2026-08-18
plan: 184
title: M-A6 — gauge vs ERA5-Land comparison
scope: Compare the QC-masked DHM gauge series against ERA5-Land extracted at the same 26 stations, scale- and season-stratified, reporting named estimands with their retained-hour counts. NOT IMERG (M-A5b), NOT the diurnal profile (M-A7), NOT the Pyramid adjudication (M-A10), NOT a correction design, NOT annualisation.
depends_on: [173, 174]
blocks: [M-A8, M-A9]
source: docs/design/dhm-precipitation-milestones.md
---

# Plan 184 — M-A6 gauge vs ERA5-Land

## Status
**DRAFT.** All inputs exist on disk: six annual `hourly_mm` products (365/366 accumulation days per
year, verified) and a published extraction bundle for 26 stations.

**A `/plan` sweep on 2026-08-18 expanded this to 1,235 lines and stalled, its blockers landing in
apparatus it had invented. Reverted. This plan is deliberately short — the analysis is a comparison,
not a system.**

## Decisions

- **D1 — Name the estimands.** Under an MNAR mask "bias" is undefined. Report exactly: **matched-hour
  mean difference**, **conditional accumulated difference**, **wet-hour conditional intensity bias**.
  Frequency and categorical scores only as conditional-on-retention estimands. **No annualisation.**

- **D2 — Apply the M-A3 mask IDENTICALLY to both sides at pairing time.** Masking one side measures
  selection, not weather. Carry the **common-retained-hour count** on every statistic.

- **D3 — Never headline an hourly matched pair.** One gauge vs one 0.1° cell (~110 km²) is dominated by
  representativeness error at that scale. Aggregate, and state the scale beside every number.

- **D4 — Warm season quantitative; cold-season high-altitude qualitative only.** Snow undercatch is
  50–100 %, so a winter "bias" at 3,700 m measures the gauge (vision D6).

- **D5 — Representativeness is characterised, not decomposed.** One point vs one cell cannot separate
  grid error from model error. Use M-A5's operator-sensitivity envelope, the elevation mismatch, and
  neighbouring-cell variability — and label it a characterisation.

- **D6 — Sign every result.** A post-QC gauge total is a **lower bound** on true precipitation. Each
  figure carries that caveat, its estimand, and the retained fraction it rests on.

- **D7 — Expect elevation-dependent structure; it is already measured.** ERA5-Land's model orography
  sits far above our high stations — Syangboche **3,700 m vs 4,447 m**, Humde **3,401 m vs 4,700 m**. A
  0.1° cell cannot resolve a valley. Report the discrepancy **as elevation structure**, not per-station
  curiosities. A model whose terrain is ~1.3 km too high will also phase rain as snow, which is D4's
  point. Station-side datum is `UNKNOWN`, so the mismatch is **datum-unreconciled** — label it.

- **D8 — The within-cell pair, with its confound stated.** Kirtipur and Khumaltar share one cell,
  4.33 km apart. Compute on hours retained for **both**, and state the implication only conditionally:
  *if both gauges are unbiased over the retained sample, half the pair discrepancy is a lower bound on
  the within-cell contribution at ~4.3 km.* Khumaltar swings **294 → 1,504 mm** (2023→2024), cause
  undocumented. **n = 1 pair.**

- **D9 — Read the sensitivity schema from the artefact.** Verified on the real bundle:
  `statistic ∈ {QUANTILE, WET_MEAN_INTENSITY, WET_FREQUENCY}` — absolute/ratio/sign-agreement are
  **columns**, not statistic values — and `sign_agreement_fraction` is legitimately **null** on
  `STATION` rows. A validator demanding it non-null rejects every valid station row.

- **D10 — M-D3 RESOLVED 2026-08-18: DHM hourly values are SUMS.** Reported by the students who supplied
  the data (**provenance is second-hand, not a DHM statement — record it as such**). Magnitude
  estimands are therefore unblocked and need no rescaling factor. *Had it been a mean of sub-hourly
  samples, every magnitude in D1 would have been wrong by a constant.*

## Exit

Error characterisation across the 26 stations: the D1 estimands at stated scales, season- and
elevation-stratified, each signed per D6 with its common-retained-hour count; the D5 representativeness
characterisation; the D8 within-cell figure with limits attached. **No correction is designed or
recommended** — that is M-A9's decision node.

## Non-goals

IMERG (M-A5b) · diurnal profile (M-A7) · Pyramid adjudication (M-A10) · elevation/regime structure
(M-A8) · any correction or downscaling design · annualised totals · ERA5-Land as a QC input (circular).
