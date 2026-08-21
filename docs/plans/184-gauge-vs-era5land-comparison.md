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

  **AMENDED 2026-08-20 — the mass fraction carries a LAPSE sensitivity as well as a threshold
  sensitivity.** D4's argument that "the choice of 1.5 °C must not be invisible" applies equally to
  D14's lapse correction, which is large and measured: at Humde the model orography is 1,299 m above
  the station, so the correction is **+8.44 °C at 6.5 °C/km** and spans **6.24 °C** across plausible
  rates (5.0–9.8 °C/km); Olangchunggola +6.73, Lukla +6.52, Syangboche +4.86. Both choices move the
  effective rain/snow cutoff, so **both are reported** and the binding Exit rule covers both.

  **⛔ Do NOT rank the two.** A 6.24 °C band is not "three times" a 2.00 °C band in effect: how much
  either moves the mass fraction depends on where precipitation mass actually sits in the temperature
  distribution, which is an output of this analysis, not an input to it. Report both; let the numbers
  say which mattered. *(Codex review 2026-08-20 — an earlier draft asserted the lapse term dominated;
  that ordering was unestablished.)*

  This is **not** licence to refit: D14's "use 6.5, never fit to Pyramid" stands, and a reported
  sensitivity is neither a refit nor a decision threshold (vision D8 is unaffected).

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

  **AMENDED 2026-08-20 — the caveat extends to TEMPERATURE, which D7 was written without.** D14's
  phase flag pairs **gauge** precipitation mass with an **ERA5-cell** temperature adjusted to station
  elevation: cross-source and cross-scale, a ~110 km² cell value standing in for point air
  temperature in terrain where D3 already refuses to headline the precipitation equivalent. The lapse
  step **applies an assumed elevation adjustment** — it does not establish that the observed offset
  was caused by elevation, and it does nothing about the boundary-layer structure of a cell whose
  surface sits 750–1,300 m above the gauge. Every row of M-A5's elevation table is
  `datum_reconciled = UNRECONCILED` with station datum `UNKNOWN`, so **the elevation difference
  driving the adjustment carries an unquantified datum error — label it, and put no number on it**
  (Plan 174 permits the label only). *(Codex review 2026-08-20 — an earlier draft said the lapse step
  "fixes the mean offset" and invented a ±30–50 m datum figure; both are withdrawn.)*

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

  **✅ THE PREREQUISITE IS DELIVERED (Plan 191, 2026-08-20).** The cell-level series exists under
  `data/dhm_precip/era5_land_t2m/era5_land/points/<NNNN>-<identity>/series_t2m_degc.nc` —
  `temperature_degc(station=26, valid_time=52608)`, °C, UTC, zero non-finite. **Corrected 2026-08-20
  (review round 2):** t2m's own bundle publishes the same way as the precipitation bundle now — a
  fresh, per-run-unique `<NNNN>-<identity>` directory, never the fixed path an earlier revision used
  (that fixed-path scheme swapped in place via a `.points.prev` backup, which could leave the
  canonical path briefly absent on a crash, or deadlock two concurrent publishers). It is
  **uncorrected**: applying the lapse rate and running the Pyramid check are THIS plan's work. Resolve
  BOTH the referenced precipitation bundle and t2m's own bundle by P2/P6's convention — the highest
  `NNNN` whose manifest validates (`discover_t2m_bundle` for t2m) — never by a run-numbered path and
  never by globbing `*-<identity>` (an identity is a label, not a lookup key, and may cover different
  payloads). **Note `pyramid_loader.py` parses `RR` only — the `AT` loader D14's check needs does not
  exist yet.**

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

  **AMENDED 2026-08-20 — the check is NARROWER than this decision assumed, its loader does not exist,
  and aggregation does NOT dispose of the timing problem.** Measured against the real files:

  - **Five** Pyramid stations carry `AT` overlapping our window, at **2,660 / 3,570 / 4,260 / 5,035 /
    5,600 m** — a 2,940 m transect, a genuinely strong test of a lapse rate.
  - **The overlap is 2020–2023 only**; the Pyramid record ends in 2023 (AWS0 to 2005, South Col
    2008–11, Changri Nup 2010–15 do not overlap at all). The rate is therefore validated on **four of
    the six comparison years** — say so beside any 2024–2025 figure.
  - **⛔ Aggregating to monthly means does NOT remove the ±1.75 h alignment uncertainty, and a naive
    monthly mean introduces a SEPARATE bias.** Pyramid's record is **irregularly sampled**: AWS3
    August 2023 holds **215 `AT` observations, 3–19 per hour**, so its ordinary monthly mean is
    **17.37 °C against 16.37 °C hour-equalised — a 1.00 °C shift**, on a check trying to validate a
    ~6.5 °C correction. **Equalise hour-of-day exposure in any aggregate**, and **state the ±1.75 h
    rather than resolve it** (Plan 182 D2 requires exactly that). Hourly pairing is not forbidden —
    it is one legitimate view, reported with its alignment uncertainty attached.
    *(Codex review 2026-08-20 — an earlier draft claimed aggregation removed the uncertainty and
    banned hourly pairs; the first was false, the second unjustified.)*
  - **⚠️ `pyramid_loader.py` parses `RR` ONLY.** `AT` appears in that module solely inside an error
    message. The loader is unwritten work **inside this plan's scope** — Plan 191 deliberately stopped
    at the cell-level series (its D7).

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

9. **Regenerable, and every consumed identity is recorded.** Every number in the report comes out of
   the committed pipeline from the on-disk inputs, in one command. The report records the ERA5-Land
   product identity and **every** extraction identity it consumed — the precipitation bundle's and
   t2m's separate one — and **never a run-numbered path**, since `<NNNN>-` is allocated per publish
   and is a function of how many times the gated suite has run.

   **⛔ But an identity is a LABEL, not a lookup key** (`era5_extract_manifest.py` P3), and the same
   identity may legitimately cover **different** payloads (`test_era5_extract_manifest.py:445`
   publishes two). So **do not resolve a bundle by globbing `*-<identity>`** — that can match many.
   Use the existing documented convention: **the highest `NNNN` whose manifest validates** (P2/P6).
   M-A6 CONSUMES the published bundle and does not re-run extraction.
   *(Codex review 2026-08-20 — an earlier draft prescribed the glob and assumed re-runs produce
   identical copies; both contradict the publication contract.)*

**Explicitly NOT an exit condition:** any statement about whether ERA5-Land is fit to force a
hydrological model (that is M-A8/M-DEC), and any correction, adjustment or downscaling design
(D7, vision).

## Tasks

Six tasks, four phases. **The shape follows M-A10's precedent** (`coloc_*.py` + `stats_coloc.py` + one
runner writing a markdown report to `--out`) — that convention exists and is not re-invented. No new
framework, no config surface, no new file format.

Throughout, `$ENV` abbreviates
`DHM_PRECIP_ERA5_ROOT=data/dhm_precip DHM_PRECIP_XLSX=data/dhm_precip/combined_precipitation_37_stations.xlsx`.

### T1 — the paired, masked substrate
**In:** a public, season-agnostic accessor returning each station's M-A3-masked gauge series paired
hour-by-hour with the ERA5-Land nearest series on **commonly-retained timestamps only** (D2), and a
**per-subset** common-retained-hour count — computed for whatever season/scale/period a caller asks
for, **never a single series-level `n`**, since a JJAS-monthly statistic does not rest on the whole
series' retention (Exit 1/4). Consume D11's list via `qc_mask.build_exclusion_list` and report what it
removed (empty on this delivery — a measured result, not a skipped step). **Out:** any statistic; any
re-derivation of the mask or the exclusion predicate.
*The only masked-series code today is `coloc_run.py:422` — private, JJAS-only, bound to M-A10's
two-station registry. This generalises it; it does not fork it.*

**⛔ T1 exposes the GAUGE-ONLY masked population as a named output, before ERA5 pairing.** M-A7
(Plan 193) is gauge-only and consumes exactly that; if the only public output were the paired frame,
a gauge-only milestone would become conditional on ERA5 availability, and the alternative — a second
implementation of "the masked series" — would drift invisibly, both versions looking right alone.
Two named outputs, one definition. *(Codex review 2026-08-20.)*
**Verify:** `uv run pytest tests/unit/scripts/test_ma6_pairs.py -q` and
`$ENV uv run pytest tests/integration/test_dhm_precip_reproduction.py -q`

### T2 — Pyramid `AT` loader and the D14 lapse correction
**In:** `AT` support in `pyramid_loader.py` sharing **only its file/timestamp parsing**; the standard
6.5 °C/km correction from model orography to station elevation using M-A5's published
`station_grid_elevation.csv` (D6 — reuse, never re-derive); and the check on the transect
**AWS3 (2,660 m), AWS5 (3,570 m), AWS2 (4,260 m), AWS1 (5,035 m), AWS4 (5,600 m)** over **2020–2023**,
with **hour-of-day exposure equalised**. **Out:** fitting or tuning the rate (D14), and **routing `AT`
through the precipitation retention path**.

**Timestamp convention: assume PERIOD-ENDING, state the assumption, and record that this check is
INVARIANT to it.** *(Amended 2026-08-21 — an earlier version imported M-A10's "±1.75 h stated, not
resolved" into this task, which overstates the uncertainty here.)* Pyramid's README does not declare
whether an hourly value is period-beginning or period-ending; period-ending is the dominant convention
(WMO reports precipitation as accumulation over the preceding period, and AWS loggers write a record
when the interval closes). Two reasons it does not bind this task:

  1. **Measured: a whole-hour relabelling moves an hour-equalised mean by exactly 0.000 °C** — the same
     24 hour-means are averaged, only their labels change. Verified on AWS4 and AWS5 for 2022-07 and
     2022-01.
  2. The convention is **sharp for accumulations and soft for state variables**. Precipitation is an
     integral, so a one-hour ambiguity is a full hour of rainfall; temperature varies smoothly, so the
     difference between a spot reading and a preceding-interval mean is a fraction of the hourly rate
     of change.

  **The ±1.75 h belongs to M-A10 and M-A7's diurnal-PHASE work, not here.** Do not carry it into the
  lapse report. The open question to the Pyramid authors stays open for those milestones; T2 does not
  wait on it.

**⛔ `AT` MUST NOT pass through the `RR` range check.** `pyramid_loader.py:198` rejects
`value_mm < qc_mask_range_check_value_min_mm` = **0.0** (`params.py:142`). `AT` is °C and largely
sub-zero at altitude — measured on the real files: **AWS4 5,600 m 82.0 % sub-zero (min −24.79 °C)**,
AWS1 5,035 m 60.1 %, AWS2 4,260 m 42.3 %, AWS5 3,570 m 20.6 %, AWS3 2,660 m 3.5 %. The deletion rate
rises **monotonically with elevation — the very axis this check measures** — so reusing that path
would flatten the measured lapse rate by deleting the cold high end. *(Codex review 2026-08-20.)*
**Verify:** `uv run pytest tests/unit/scripts/test_pyramid_at.py -q`, including a test that a −24 °C
reading is RETAINED; and `$ENV uv run python scripts/dhm_precip/ma6_lapse_check.py --out <dir>`

### T3 — the estimands (depends: T1)
**In:** D1's three — matched-hour mean difference, conditional accumulated difference, wet-hour
conditional intensity bias — at the D3 scales with the scale named beside every number; plus D12's
categorical scores at **daily and monthly grain only**, the 0.2 mm floor applied **after** valid
aggregation (Rule 3), on jointly-valid periods, labelled conditional-on-retention (Rule 1). **Every
statistic carries its OWN subset `n` from T1**, is emitted **per station AND per elevation stratum**
(Exit 1), and cross-station summaries are **stratified BY retention, never filtered ON it** (D13,
Exit 4). **Out:** annualisation, unconditional totals, any headline hourly matched pair (D3).
**Verify:** `uv run pytest tests/unit/scripts/test_ma6_estimands.py -q`, including a test that a
JJAS/DJF-grain categorical score is refused rather than computed (D12's vacuity trap), and one that a
statistic emitted without its own subset `n` fails.

### T4 — representativeness, characterised (depends: T1)
**In:** the operator-sensitivity envelope read from the published bundle's `operator_sensitivity.csv`
(D9's real schema — `sign_agreement_fraction` is legitimately null on `STATION` rows), the elevation
mismatch as a covariate labelled **datum-unreconciled** (D7), neighbouring-cell variability, and the
Kirtipur/Khumaltar within-cell pair on hours retained for **both** — reported descriptively with its
`n` and each station's exposure. **Out:** any decomposition of representativeness from model error,
and any lower-bound claim from the pair (D5, D8).
**Verify:** `uv run pytest tests/unit/scripts/test_ma6_representativeness.py -q`

### T5 — the sub-freezing mass fraction (depends: T2, T3)
**In:** the mass-weighted fraction of each period's precipitation falling below 1.5 °C, computed
alongside each magnitude and carrying that magnitude's subset `n`, with the **0 °C / 2 °C threshold
sensitivity AND the 5.0–9.8 °C/km lapse sensitivity**. **Out:** gating, adjusting or correcting
anything by it; ranking the two sensitivities against each other (D4); and any assertion about how it
is *rendered* — that is T6's, which owns the renderer.
**Verify:** `uv run pytest tests/unit/scripts/test_ma6_mass_fraction.py -q`

### T6 — the report and Exit compliance (depends: T1–T5)
**In:** one runner writing the markdown report plus its tables to `--out` (M-A10 shape), enforcing the
binding placement rule — **no magnitude rendered without BOTH its subset `n` and its mass fraction in
the same cell** — and signing every result per D6 with its estimand, retained fraction and the
catch-efficiency caveat (Exit 3). Record **every** consumed identity: the **precipitation** bundle's
and **t2m's**, BOTH discovered by the highest valid `NNNN` whose manifest validates (P2/P6 —
`_discover_precip_bundle`/`discover_t2m_bundle`). *(Corrected 2026-08-20, review round 2: an earlier
revision of this note said t2m's manifest was read directly from its own fixed
`era5_land/points/extraction_manifest.json`, because Plan 191 D5 originally gave t2m no `NNNN` bundle
shape. That fixed-path scheme had two crash/concurrency defects and was replaced — t2m now publishes
numbered bundles exactly like precipitation, so one discovery rule covers both.)* Headline numbers
locked in
`tests/integration/test_dhm_precip_reproduction.py`; results folded into
`docs/design/dhm-precipitation-milestones.md`, this track's only durable record.
**Out:** any fitness-to-force statement (M-A8/M-DEC) and any correction design.
**Verify:** `uv run pytest tests/unit/scripts/test_ma6_report.py -q` (including a rendering test that a
magnitude missing either companion is a hard failure), then end-to-end:
`$ENV uv run python scripts/dhm_precip/ma6_run.py --out <dir>` followed by
`$ENV uv run pytest tests/integration/test_dhm_precip_era5_extraction.py tests/integration/test_dhm_precip_reproduction.py -q`

```json
{
  "phases": [
    {"id": "phase-1", "tasks": ["T1", "T2"], "parallel": true},
    {"id": "phase-2", "tasks": ["T3", "T4"], "parallel": true, "depends_on": ["phase-1"]},
    {"id": "phase-3", "tasks": ["T5"], "parallel": false, "depends_on": ["phase-2"]},
    {"id": "phase-4", "tasks": ["T6"], "parallel": false, "depends_on": ["phase-3"]}
  ]
}
```

*T2 is independent of the gauge side; T5 is the first required join. The graph conservatively lets
T3/T4 wait on phase-1 as a whole — harmless, and not worth a finer graph.*

## Non-goals

IMERG (M-A5b) · diurnal profile (M-A7) · Pyramid adjudication (M-A10) · elevation/regime structure
(M-A8) · any correction or downscaling design · annualised totals · ERA5-Land as a QC input (circular).
