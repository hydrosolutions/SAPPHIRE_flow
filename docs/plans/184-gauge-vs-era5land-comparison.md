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

**A SECOND `/plan` sweep the same day expanded it to 420 lines and ESCALATED** (stalled after 3 rounds,
2 blockers + 5 majors), its blockers again sitting inside apparatus it had itself invented — task
scaffolding, bundle-identity verification, manifest cross-checks. Reverted again. **Four findings were
genuinely analytical and are folded in below by hand** (D4's shoulder-season gap, D11, D12, and the
withdrawn "50–100 %" citation); the rest concerned the sweep's own additions. Two headline findings did
NOT survive checking: the "primary extraction operator is unspecified" blocker (`era5_extract.py:258`
already declares NEAREST as *THE* operator, and the `series_*.nc` files it cited do not exist).

**⚠️ THE IN-DOCUMENT PROPORTIONALITY BLOCK BELOW DID NOT RESTRAIN THE WORKFLOW.** It was added
specifically to prevent the second expansion and failed. **Do not re-run `/plan` on this plan expecting
a different result — review it by hand.**

## ⛔ PROPORTIONALITY IS A BINDING CONSTRAINT ON THIS PLAN AND ON ITS REVIEW

**Owner instruction, 2026-08-18: "we must not over-engineer this plan."** This is a standing constraint
on every reviewer and every revision, not a preference. It is repeated here because it is the single
thing this plan has already failed at once: a `/plan` sweep on 2026-08-18 expanded it to **1,235 lines**
and then stalled, with its own blockers landing inside apparatus the sweep had invented. It was reverted
to this form.

**M-A6 is a COMPARISON, not a system.** All inputs already exist on disk. The work is: extract, pair,
stratify, report. It needs no new framework, no new abstraction layer, no configuration surface, no
plugin seam, and no validation apparatus beyond what the existing `scripts/dhm_precip/` modules already
provide.

**Rules for reviewers of this plan:**

1. **"No findings" is a complete and welcome review.** Do not manufacture findings to justify the pass.
2. **A finding must name a CONCRETE FAILURE of the ANALYSIS** — a number that would come out wrong, a
   comparison that would be invalid, an estimand that would mislead. Cite the decision it breaks.
3. **A missing specification is NOT a finding.** "The plan does not say how X is configured" is out of
   scope unless leaving it unsaid produces a wrong number.
4. **Do not propose new apparatus.** No registries, no schemas, no abstraction layers, no frameworks,
   no orchestration, no new file formats. If a decision can be expressed as a sentence, it must not
   become a module.
5. **Adding length is a cost, not a contribution.** A revision that grows this document without
   removing a concrete analytical error has made it worse. Prefer deleting to adding.
6. **The existing decisions D1–D10 are load-bearing and already reviewed.** Challenge them only on
   correctness, never on completeness of specification.

**If the review's conclusion is "this plan is adequately scoped and ready", say exactly that and stop.**

## Decisions

- **D1 — Name the estimands.** Under an MNAR mask "bias" is undefined. Report exactly: **matched-hour
  mean difference**, **conditional accumulated difference**, **wet-hour conditional intensity bias**.
  Frequency and categorical scores only as conditional-on-retention estimands. **No annualisation.**

- **D2 — Apply the M-A3 mask IDENTICALLY to both sides at pairing time.** Masking one side measures
  selection, not weather. Carry the **common-retained-hour count** on every statistic.

- **D3 — Never headline an hourly matched pair.** One gauge vs one 0.1° cell (~110 km²) is dominated by
  representativeness error at that scale. Aggregate, and state the scale beside every number.

- **D4 — Warm season quantitative; cold-season high-altitude qualitative only.** A winter "bias" at
  3,700 m measures the gauge, not the model (vision D6). **⛔ The "50–100 %" snow-undercatch figure an
  earlier draft attributed to vision D6 is WITHDRAWN — D6 constrains the SIGN only and states we know
  neither gauge type nor wind speed. Cite primary literature or give no number.**

  **⚠️ OPEN — owner's call, do not leave implicit.** The carve-out is scoped to calendar DJF but the
  mechanism is not. At the six stations ≥2,400 m (Syangboche 3,700, Humde 3,401, Olangchunggola 3,119,
  Lukla 2,860, Ghorepani 2,742, Lete 2,490) freezing levels in **MAM and ON** are frequently at or below
  station height, so shoulder-month snow undercatch is physically plausible — and would enter the
  MONTHLY magnitude estimands with no flag, since D6's caveat governs sign rather than usability.
  Either extend the qualitative-only treatment to MAM/ON at those six, or say in the Exit that those
  months are **not screened** for undercatch, so the "quantitative" label there is asserted rather than
  verified.

- **D5 — Representativeness is characterised, not decomposed.** One point vs one cell cannot separate
  grid error from model error. Use M-A5's operator-sensitivity envelope, the elevation mismatch, and
  neighbouring-cell variability — and label it a characterisation.

- **D6 — Sign every result.** Each figure carries its estimand, the retained fraction it rests on, and
  the undercatch caveat — stated as a property of **catch efficiency**, not of the observed total:
  *for a correctly-functioning gauge, catch ≤ true precipitation.*

  **⛔ CORRECTED 2026-08-19 (Codex review): do NOT assert that the observed post-QC total is a lower
  bound.** Our QC is a **physical-impossibility gate, not an outlier filter** — Plan 173 D4 sets
  `value_max = 200.0` mm/h deliberately "comfortably unreachable rather than discriminating"
  (`173:108`), and no isolated-outlier check exists. A single spurious 150 mm/h reading therefore
  passes QC and can push a station total **above** true precipitation, reversing the very sign this
  decision exists to guarantee. The stuck-high check catches only *sustained* blocks (it caught this
  sample's 72 mm/h × 4 days); an isolated spike is invisible to it. **The caveat is directional about
  catch, never a bound on a reported number.**

- **D7 — Elevation mismatch is a DESCRIPTIVE COVARIATE, not a causal explanation.** ERA5-Land's
  orography sits far above our high stations — Syangboche **3,700 m vs 4,447 m**, Humde **3,401 m vs
  4,700 m**. Report it as a covariate against which discrepancies are stratified.
  **⛔ It does NOT explain precipitation phase, and an earlier draft of this plan wrongly said it did.**
  ERA5-Land's `total_precipitation` is **interpolated from ERA5**, not regenerated by ERA5-Land's own
  land-surface scheme on its 0.1° grid — ERA5-Land applies elevation corrections to temperature,
  humidity and pressure, **not to precipitation**. So its precipitation never "sees" the 0.1° orography
  and cannot be re-phased rain→snow by it. Any such attribution would be **wrong about how the dataset
  is built**. Station-side datum is `UNKNOWN` ⇒ the mismatch is **datum-unreconciled**; label it.
  *(D4 is unaffected — it rests on GAUGE undercatch, not on model phasing.)*

- **D8 — The within-cell pair is DESCRIPTIVE. Do not convert it into a bound.** Kirtipur and Khumaltar
  share one cell, 4.33 km apart; ERA5 returns one identical series for both. Compute the discrepancy on
  hours retained for **both** and **report it as an observed quantity, full stop.**
  **⛔ The "half the discrepancy is a lower bound on within-cell variability" claim is WITHDRAWN** — it
  was inherited from Plan 174's D6 handoff and does not follow. Two gauges with **unbiased** errors will
  still differ over a finite sample by noise alone, so the plan would report a positive "lower bound"
  where the true spatial contribution is **zero**. The triangle-inequality argument needs effectively
  **error-free aggregates**, not merely unbiased ones — which we do not have, least of all here:
  Khumaltar swings **294 → 1,504 mm** (2023→2024), cause undocumented. **n = 1 pair.**

- **D9 — Read the sensitivity schema from the artefact.** Verified on the real bundle:
  `statistic ∈ {QUANTILE, WET_MEAN_INTENSITY, WET_FREQUENCY}` — absolute/ratio/sign-agreement are
  **columns**, not statistic values — and `sign_agreement_fraction` is legitimately **null** on
  `STATION` rows. A validator demanding it non-null rejects every valid station row.

- **D10 — M-D3 RESOLVED 2026-08-18: DHM hourly values are SUMS.** Reported by the students who supplied
  the data (**provenance is second-hand, not a DHM statement — record it as such**). Magnitude
  estimands are therefore unblocked and need no rescaling factor. *Had it been a mean of sub-hourly
  samples, every magnitude in D1 would have been wrong by a constant.*

- **D11 — Consume Plan 173's M-A6 exclusion list; do not re-derive it.** 173 produces that list from the
  exact monsoon-retention predicate *precisely so M-A6 does not invent its own rule* (173 `scope:`,
  and its §:164/:232). **Apply it before every station, elevation and cross-station summary, and report
  which stations it removed and why.** Omitting it silently pools stations 173 already judged unusable,
  which changes every cross-station number in the Exit.

- **D12 — Categorical scores at DAILY and MONTHLY grain only.** At a JJAS/DJF grain "wet" means ≥0.2 mm
  accumulated over whole months, so both sides are wet in essentially every station-year: POD→1.0,
  CSI→1.0, FAR→0 *by construction*, while the totals may still differ by hundreds of mm. That is D3's
  scale-validity objection one tier up — a well-defined number that reads as perfect agreement and is
  analytically vacuous. Report categorical skill where wet/dry is informative, and nowhere else.

## Exit

Error characterisation across the 26 stations: the D1 estimands at stated scales, season- and
elevation-stratified, each signed per D6 with its common-retained-hour count; the D5 representativeness
characterisation; the D8 within-cell figure with limits attached. **No correction is designed or
recommended** — that is M-A9's decision node.

## Non-goals

IMERG (M-A5b) · diurnal profile (M-A7) · Pyramid adjudication (M-A10) · elevation/regime structure
(M-A8) · any correction or downscaling design · annualised totals · ERA5-Land as a QC input (circular).
