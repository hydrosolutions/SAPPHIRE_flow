---
status: READY
created: 2026-08-18
revised: 2026-08-19
plan: 184
title: M-A6 — gauge vs ERA5-Land comparison
scope: Compare the QC-masked DHM gauge series against ERA5-Land extracted at the same 26 stations, scale- and season-stratified, reporting named estimands with their retained-hour counts. NOT IMERG (M-A5b), NOT the diurnal profile (M-A7), NOT the Pyramid adjudication (M-A10), NOT a correction design, NOT annualisation.
depends_on: [173, 174]
blocks: [M-A8, M-A9]
source: docs/design/dhm-precipitation-milestones.md
---

# Plan 184 — M-A6 gauge vs ERA5-Land

## Status
**READY** — owner flip 2026-08-19. All inputs exist on disk: six annual `hourly_mm` products (365/366 accumulation days per
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

## ✅ THE OPEN DECISION IS RESOLVED (grill-me, owner, 2026-08-19)

D4's shoulder-season fork is closed, and the resolution **removes a rule rather than adding one**: the
calendar carve-out is deleted outright and replaced by a measured diagnostic (D4 below, plus D13/D14).
All decisions are now made.

## Decisions

- **D1 — Name the estimands.** Under an MNAR mask "bias" is undefined. Report exactly: **matched-hour
  mean difference**, **conditional accumulated difference**, **wet-hour conditional intensity bias**.
  Frequency and categorical scores only as conditional-on-retention estimands. **No annualisation.**

- **D2 — Apply the M-A3 mask IDENTICALLY to both sides at pairing time.** Masking one side measures
  selection, not weather. Carry the **common-retained-hour count** on every statistic.

- **D3 — Never headline an hourly matched pair.** One gauge vs one 0.1° cell (~110 km²) is dominated by
  representativeness error at that scale. Aggregate, and state the scale beside every number.

- **D4 — ⛔ THE CALENDAR CARVE-OUT IS DELETED. Every magnitude estimand carries a measured
  sub-freezing mass fraction instead.** *(Owner, grill-me 2026-08-19 — this REPLACES "warm season
  quantitative; cold-season high-altitude qualitative only".)*

  The old rule withheld high-altitude magnitudes in calendar DJF. The mechanism it guarded against —
  snow undercatch — is **elevation- and temperature-driven, not calendar-driven**, so the rule was
  wrong in both directions: it withheld genuinely-quantitative DJF hours at 2,490 m and passed
  genuinely-snow MAM/ON hours at 3,700 m.

  **Replacement:** beside every magnitude estimand, in the SAME cell as its retained-hour `n`, report
  the **fraction of that period's precipitation MASS falling at air temperature < 1.5 °C**. Mass-
  weighted, not hour-weighted — the concern is undercaught amount, and a month with 5 % of wet hours
  but 40 % of its mass sub-freezing is badly contaminated in a way an hour-count hides. Report the
  fraction at **0 °C and 2 °C as a sensitivity**, since the rain/snow transition is genuinely fuzzy and
  the choice of 1.5 °C must not be invisible.

  **The fraction ANNOTATES; it never gates and never adjusts.** No threshold is set anywhere (vision D8
  — no pre-registered thresholds). A reader seeing "+38 mm, sub-freezing mass fraction 0.61" knows the
  figure is dominated by a phase the gauge cannot catch. **⚠️ Residual risk, named not solved:** this is
  the second decision in a row choosing annotation over withholding (see D13), and caveats get stripped
  when numbers are quoted downstream. Mitigation is placement — `n` and the mass fraction live in the
  same cell as the value, so stripping them takes deliberate effort — plus an Exit rule that no
  magnitude may be quoted without both.

  **⛔ The "50–100 %" snow-undercatch figure an earlier draft attributed to vision D6 is WITHDRAWN** —
  D6 constrains the SIGN only and states we know neither gauge type nor wind speed. Cite primary
  literature or give no number.

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

- **D13 — A partially-masked period is reported as what it literally is; no scaling, no completeness
  bar.** *(Owner, grill-me 2026-08-19.)* The M-A3 mask removes hours **non-randomly** (sentinels,
  stuck-high blocks, false-zero runs to 52 days), so aggregates rest on very different retention.
  **Never scale to a full period** — dividing by retained fraction assumes the removed hours resemble
  the kept ones, which is precisely the MNAR error Rule 1 exists to forbid. **Set no completeness
  threshold** either (vision D8), and note the gross cases are already handled upstream: D11 consumes
  Plan 173's M-A6 exclusion list. Below that line every aggregate is a **conditional sum over the hours
  retained in common**, self-describing at any retention, with its `n` attached. Where cross-station or
  elevation-stratified summaries are produced, **stratify BY retention rather than filter ON it**, so a
  retention-driven pattern is visible instead of hidden.

- **D14 — Snow phase is measured with ERA5-Land 2m temperature, lapse-corrected at the STANDARD rate,
  and checked against Pyramid.** *(Owner, grill-me 2026-08-19.)* Plan 171 fetched
  `total_precipitation` ONLY, which is why D4 originally reached for a calendar proxy. **This adds an
  ERA5-Land `2m_temperature` acquisition as a prerequisite** (same box, same window, 171's machinery
  and its monthly-window cost rule — see the milestones note).

  **✅ THE PREREQUISITE IS DELIVERED (Plan 191, 2026-08-20).** The cell-level series exists at
  `data/dhm_precip/era5_land_t2m/era5_land/points/series_t2m_degc.nc` —
  `temperature_degc(station=26, valid_time=52608)`, °C, UTC, zero non-finite. It is **uncorrected**:
  applying the lapse rate and running the Pyramid check are THIS plan's work. Resolve the referenced
  precipitation bundle by P2/P6's convention — the highest `NNNN` whose manifest validates — never by
  a run-numbered path and never by globbing `*-<identity>` (an identity is a label, not a lookup key,
  and may cover different payloads). **Note
  `pyramid_loader.py` parses `RR` only — the `AT` loader D14's check needs does not exist yet.**

  ERA5-Land is the right source for temperature specifically: per D7 it elevation-corrects temperature,
  humidity and pressure — but NOT precipitation. **The trap is that its orography sits 750–1,300 m
  ABOVE our high stations** (Syangboche 3,700 vs 4,447; Humde 3,401 vs 4,700), so at ~6.5 °C/km the raw
  cell runs 5–8 °C too cold and would systematically over-call snow. Correct from model orography down
  to station elevation.

  **Use the standard 6.5 °C/km — do NOT fit the rate to Pyramid.** Pyramid `AT` (six stations,
  2,660–5,600 m) is the *independent check* on the correction, and a rate derived from Lukla and Namche
  could not then be validated at Lukla and Namche. That is this track's own rule from M-A5: **a
  validator does not re-derive the computation.** Seasonal variation is expected — Himalayan valleys
  invert in winter, exactly when the flag matters most — but any seasonal rate must come from
  **literature, chosen a priori**, never fitted to the validation data. **If the check fails, widen the
  reported uncertainty on the mass-fraction column; do not refit.**

## Exit

A single comparison bundle plus the short report that reads it. Nothing here is a new framework —
the deliverable is numbers with their conditions attached.

1. **Error characterisation, not a correction.** The three D1 estimands — matched-hour mean
   difference, conditional accumulated difference, wet-hour conditional intensity bias — computed
   per station and per elevation stratum, at the D3 scales, with the scale stated beside every
   number. Categorical scores at DAILY and MONTHLY grain only (D12). No annualisation, no
   unconditional totals, no "mm/year".

2. **⛔ BINDING (D4 + D13): no magnitude may be quoted without BOTH its retained-hour `n` and its
   sub-freezing mass fraction in the SAME cell.** A table row, figure caption or sentence carrying
   a magnitude without both is a defect, not a formatting preference — the placement rule IS the
   mitigation for choosing annotation over withholding. The mass fraction is reported at 1.5 °C
   with 0 °C and 2 °C as a sensitivity.

3. **Every result signed per D6.** Each figure carries its named estimand, the retained fraction it
   rests on, and the undercatch caveat stated as a property of **catch efficiency** —
   *for a correctly-functioning gauge, catch ≤ true precipitation* — and **never** as a bound on
   the reported post-QC total (our QC is a physical-impossibility gate, not an outlier filter).

4. **Retention is a stratum, never a filter (D13).** No aggregate is scaled to a full period and no
   completeness threshold is set anywhere. Cross-station and elevation-stratified summaries are
   stratified BY retention so a retention-driven pattern is visible rather than hidden. Every
   aggregate is a conditional sum over commonly-retained hours, with its `n` attached.

5. **Plan 173's M-A6 exclusion list is consumed, not re-derived (D11)**, applied before every
   station, elevation and cross-station summary, and the report names which stations it removed and
   why. **On the current delivery this list is empty** — worst JJAS retention is Lete 0.8296 and the
   0.50 floor never binds — **which is a measured result, not a skipped step, and the report says so
   explicitly.**

6. **Representativeness is characterised, not decomposed (D5)** — operator-sensitivity envelope,
   station-to-grid elevation mismatch (labelled datum-unreconciled per D7), and neighbouring-cell
   variability, presented under that word.

7. **The within-cell Kirtipur/Khumaltar figure (D8)**, computed on hours retained for both stations
   simultaneously, reported with the common-retained-hour count, each station's own exposure, and
   its limits attached: **descriptive only, no lower-bound claim, n = 1 pair, one valley, one
   separation — never a network-wide estimate.**

8. **The D14 lapse-correction is reported with its independent check.** The standard 6.5 °C/km
   correction from model orography to station elevation, and the Pyramid `AT` check on it, are both
   reported. If the check fails, the reported uncertainty on the mass-fraction column widens; the
   rate is never refitted.

9. **Regenerable.** Every number in the report comes out of the committed pipeline from the on-disk
   inputs, in one command. The report states the ERA5-Land product identity and the
   extraction-bundle identity it was computed against.

**Explicitly NOT an exit condition:** any statement about whether ERA5-Land is fit to force a
hydrological model (that is M-A8/M-DEC), and any correction, adjustment or downscaling design
(D7, vision).

## Non-goals

IMERG (M-A5b) · diurnal profile (M-A7) · Pyramid adjudication (M-A10) · elevation/regime structure
(M-A8) · any correction or downscaling design · annualised totals · ERA5-Land as a QC input (circular).
