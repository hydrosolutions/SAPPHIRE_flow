---
status: DRAFT
created: 2026-08-20
plan: 193
title: M-A7 — temporal characterisation (intensity distributions and diurnal structure)
scope: Per-station wet-hour intensity distributions and diurnal structure on the M-A3-masked gauge series, stratified by elevation band as well as per station, with bootstrap uncertainty and a Rule-2-compliant statement of what transfers between stations. NOT the ERA5 comparison (M-A6), NOT elevation/regime attribution (M-A8), NOT the Pyramid adjudication (M-A10), NOT a disaggregator design (Phase 2).
depends_on: [172, 173, 184]
blocks: [M-A8]
source: docs/design/dhm-precipitation-milestones.md
---

# Plan 193 — M-A7 temporal characterisation

## Status

**DRAFT** — awaiting owner confirmation before any subagent runs.

M-A7's dependencies (M-A2, M-A3) have been met since Plan 173 merged; it has simply never been
planned. It runs **parallel to M-A6** and **M-A8 needs both**, so it is the other half of the
near-term frontier. It is **gauge-only** — no ERA5, no IMERG, no Pyramid.

## ⛔ PROPORTIONALITY IS BINDING, AS IT IS ON PLAN 184

**M-A7 is a characterisation, not a system.** The inputs exist; the work is: mask, profile,
distribute, bootstrap, stratify, report. It needs no new framework, abstraction layer, config surface
or file format. **"No findings" is a complete review. Adding length is a cost.** Plan 184 was twice
wrecked by review sweeps that expanded it into apparatus they had invented — do not repeat that here,
and **do not run `/plan` on this document.**

## ⛔ THE TRAP THIS PLAN EXISTS TO AVOID

**`pipeline.py:283` feeds UNMASKED `on_grid` into `stats_coherence.diurnal_profiles`.** That is not a
detail — it is the exact code path that produced the diurnal anomaly M-A10 spent a milestone
retiring: *"an artefact of computing a normalised profile on unmasked data"*
(`dhm-precipitation-milestones.md:599`). Lukla appeared to peak at 02 UTC inside Pyramid's diurnal
minimum; masked, it peaks 21 NPT and agrees with Pyramid.

`frequency_correlations` (`pipeline.py:279`) and `interannual_diurnal_stability` (`:416`) take the
same unmasked input. **Every temporal statistic in this plan is computed on the M-A3-masked series.
Reusing those functions as currently wired would reproduce a retired artefact and publish it as a
finding.**

## Decisions

- **D1 — Masked input, always.** Every profile, distribution and derived statistic is computed on the
  M-A3-masked series (D10's substrate). The existing `stats_coherence` helpers may be reused only
  after their input is masked; their current wiring is not.

- **D2 — Hour-of-day exposure travels with every profile (Rule 1).** The mask is MNAR and its removals
  are hour-dependent, so a diurnal mean is a **conditional-on-retention** estimand. Every profile
  carries its **per-hour retained count**, and no profile is reported without it. This is the
  mechanism that would have caught the Lukla artefact at source.

- **D3 — ⛔ The "what transfers" headline is NOT a correlation (Rule 2).** Use scale-normalised
  comparison, a divergence measure, or held-out prediction error. *Nuance, so this is not
  over-applied:* `stats_coherence.diurnal_profile_correlations` documents itself as a profile-**shape**
  comparison rather than the quantile-vector trap, and that reasoning holds — a 24-hour profile is not
  a sorted vector. Shape similarity may still be computed. But the transferability **claim** must rest
  on a divergence or a prediction error, never on an r. *(This track withdrew an r = 0.998 result as a
  quantile-vector artefact; that error must not recur in a new costume.)*

- **D4 — Frequency statistics use the 0.2 mm/h harmonised floor; mass statistics use unthresholded
  values** (vision D5). Never mixed, and which is which is stated beside every number.

- **D5 — Body and tail reported separately, each with bootstrap uncertainty.** The tail is **already
  established as non-transferable** (−49 % to +241 %). M-A7 quantifies and reports that; it does not
  re-litigate it.

- **D6 — Stratify by elevation band AND by reporting-resolution group; attribute to NEITHER.** The
  motivating result is that elevation predicts diurnal regime (r = −0.486) while horizontal distance
  does not (r = −0.027). But **Group A is simultaneously the 0.01 mm subset and the high-altitude
  subset** — bounding that confound is **M-A8's exit, not this plan's**. M-A7 reports both cuts side by
  side and **explicitly declines the attribution**, handing M-A8 the material rather than pre-empting
  its conclusion.

- **D7 — Olangchunggola's 03 UTC peak is REPORTED, not adjudicated.** M-A10 retired Lukla's anomaly but
  left this one open on different grounds: zero sentinels, immovable across the ablation ladder, and
  **no co-located station exists**, so no gauge-vs-gauge adjudication is possible. M-A7 records its
  status and its exposure; resolving it is out of scope for this plan and for this data.

- **D8 — JJAS is primary; other seasons are reported where retention supports them, never forced.**
  The existing helpers are JJAS-only (`stats_coherence._jjas`, `stats_precision._jjas`), the mass and
  the literature are monsoon, and a DJF diurnal profile at a high station may rest on very few
  retained wet hours. Report the other seasons where the retained sample carries them, each labelled
  with its own exposure — and **set no completeness threshold** (Plan 184 D13, vision D8): stratify by
  retention rather than filter on it.

- **D9 — Bootstrap by resampling whole SEASON-YEARS, not hours.** No general bootstrap exists — only
  `coloc_bootstrap.bootstrap_peak_hour_spread` (circular, peak-hour). Precipitation is strongly
  serially correlated, so **hour-level resampling would break serial dependence and return falsely
  tight intervals**. Follow `coloc_bootstrap`'s existing season-year precedent. Build the smallest
  thing that yields intervals for the statistics actually reported — not a bootstrap framework.

- **D10 — Consume Plan 184 T1's gauge-masked accessor; do not build a second one.** *(This is the one
  cross-plan coupling, and it is deliberate.)* 184 T1 builds a public, season-agnostic masked-gauge
  accessor with per-subset retained counts; M-A7 needs exactly its gauge half. Two independent
  implementations of "the masked series" would drift, and the drift would be invisible — both would
  look right in isolation. The cost is that M-A7 starts after 184 T1 rather than beside it; the
  benefit is one definition of the masked population feeding both milestones that M-A8 joins.

## Tasks

Four tasks, three phases. `$ENV` abbreviates
`DHM_PRECIP_XLSX=data/dhm_precip/combined_precipitation_37_stations.xlsx`.

### T1 — masked diurnal profiles with exposure (depends: Plan 184 T1)
**In:** per-station hour-of-day profiles on the masked series, each carrying its **per-hour retained
count** (D2), plus the season-year bootstrap of D9 applied to the profile statistics; Olangchunggola's
status recorded per D7. **Out:** any between-station comparison; any use of unmasked `on_grid`.
**Verify:** `uv run pytest tests/unit/scripts/test_ma7_profiles.py -q`, including a test that a
profile computed from unmasked input is REFUSED, and `$ENV uv run pytest tests/integration/test_dhm_precip_reproduction.py -q`

### T2 — wet-hour intensity distributions (depends: Plan 184 T1)
**In:** per-station wet-hour intensity distributions with the 0.2 mm/h floor for frequency statistics
and unthresholded values for mass statistics (D4), body and tail reported separately with season-year
bootstrap intervals (D5, D9). **Out:** the transferability claim (T3); any threshold applied to mass
statistics.
**Verify:** `uv run pytest tests/unit/scripts/test_ma7_intensity.py -q`, including a test that a mass
statistic computed on thresholded values fails.

### T3 — transferability and elevation stratification (depends: T1, T2)
**In:** the quantified statement of what transfers between stations, expressed as a divergence or a
held-out prediction error (D3), reported **by elevation band and by reporting-resolution group side by
side** with the attribution explicitly declined (D6). **Out:** any transferability headline expressed
as a correlation; any attribution of an effect to elevation rather than resolution.
**Verify:** `uv run pytest tests/unit/scripts/test_ma7_transfer.py -q`, including a test that a
correlation offered as the transferability statistic is refused.

### T4 — report and Exit (depends: T1–T3)
**In:** one runner writing the markdown report and tables to `--out` (M-A10 shape), every profile and
distribution carrying its exposure, every result labelled conditional-on-retention; results folded into
`docs/design/dhm-precipitation-milestones.md`, this track's only durable record. **Out:** any
disaggregator design or Phase-2 recommendation.
**Verify:** `$ENV uv run python scripts/dhm_precip/ma7_run.py --out <dir>` then
`$ENV uv run pytest tests/integration/test_dhm_precip_reproduction.py -q`

```json
{
  "phases": [
    {"id": "phase-1", "tasks": ["T1", "T2"], "parallel": true},
    {"id": "phase-2", "tasks": ["T3"], "parallel": false, "depends_on": ["phase-1"]},
    {"id": "phase-3", "tasks": ["T4"], "parallel": false, "depends_on": ["phase-2"]}
  ]
}
```

## Exit

Per-station and per-elevation-band intensity distributions and diurnal profiles, each with bootstrap
uncertainty and its per-hour retained exposure attached; a quantified statement of what transfers
between stations expressed as a divergence or prediction error, never a correlation; both the
elevation and reporting-resolution cuts reported with the attribution between them explicitly declined
and handed to M-A8; and Olangchunggola's 03 UTC status recorded as open.

*Marginals plus mean profiles inform but do not suffice to design a disaggregator — that needs
temporal dependence structure, which is a Phase-2 question.*

## Non-goals

The ERA5-Land comparison (M-A6) · elevation/regime attribution and the Group A confound bound (M-A8) ·
the Pyramid adjudication (M-A10, complete) · IMERG (M-A5b) · any correction, disaggregation or
downscaling design · any Phase-2 GO/NO-GO recommendation (M-A9/M-DEC).
