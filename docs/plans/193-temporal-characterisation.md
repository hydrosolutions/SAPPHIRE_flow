---
status: READY
created: 2026-08-20
plan: 193
title: M-A7 — temporal characterisation (intensity distributions and diurnal structure)
scope: Per-station wet-hour intensity distributions and diurnal structure on the M-A3-masked gauge series, stratified by elevation band as well as per station, with bootstrap uncertainty and a Rule-2-compliant statement of what transfers between stations. NOT the ERA5 comparison (M-A6), NOT elevation/regime attribution (M-A8), NOT the Pyramid adjudication (M-A10), NOT a disaggregator design (Phase 2).
depends_on: [172, 173]
blocks: [M-A8]
source: docs/design/dhm-precipitation-milestones.md
---

# Plan 193 — M-A7 temporal characterisation

## Status

**READY** — owner flip 2026-08-20, after three independent Codex rounds
(BLOCK → BLOCK → APPROVE WITH CHANGES, nine findings, all folded; the final round found no new
failures and judged the plan converging). **Do not run `/plan` on this document** — review it by hand,
as Plan 184 records for the same reason.

**Gated on `plan-184-T1`** (see the phase graph): T1 and T2 consume Plan 184 T1's gauge-only masked
population, so they start when that output exists, not before.

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

**`pipeline.py:283` feeds UNMASKED `on_grid` into `stats_coherence.diurnal_profiles`**, as do
`frequency_correlations` (`:279`) and `interannual_diurnal_stability` (`:416`). Those outputs are
therefore **contaminated by the very defects M-A3 exists to remove** — sentinels, stuck-high blocks
and false-zero runs all enter the mean. Every temporal statistic in this plan is computed on the
**masked** series; reusing those functions as currently wired would publish contaminated numbers.

**⛔ This is contamination, NOT the M-A10 artefact path — do not conflate them.** That artefact needed
division by a **negative grand mean** in `stats_coloc.normalised_diurnal_profile` (`stats_coloc.py:58`);
`diurnal_profiles` returns unnormalised means and cannot invert a profile, and M-A10's own path masks
first (`coloc_run.py:440`). The contamination claim above stands on its own.

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

  **PINNED 2026-08-27 (independent review) — the headline IS `leave_one_out_tail_prediction_error`,
  which already exists.** `scripts/dhm_precip/stats_precision.py:169` predicts each held-out station's
  **q99** as `median_i × pooled_ratio(excluding i)` and returns `median_abs_error`, `min_error`,
  `max_error`, `within_25pct_fraction`. Use it. "A divergence **or** a prediction error" left the
  support, the target and the pooling population open, so two compliant implementations would publish
  incomparable headlines — and reusing the repo's own formulation also keeps M-A7 comparable with the
  work that precedes it. Compute it on the MASKED series (D1); shape similarity stays a secondary
  statistic.

- **D4 — Frequency statistics use the 0.2 mm/h harmonised floor; mass statistics use unthresholded
  values** (vision D5). Never mixed, and which is which is stated beside every number.

  **PINNED 2026-08-27 — the mass estimand is the station's TOTAL RETAINED PRECIPITATION MASS in mm**
  over the reported period, unthresholded, carrying its retained-hour `n`. "A mass statistic" admitted
  a total, a mass-weighted distribution or a mass fraction, each satisfying the text and producing a
  different number.

- **D5 — Body and tail reported separately with bootstrap uncertainty. M-A7 MEASURES tail
  transferability; it inherits no number.** **⛔ Do NOT cite "−49 % to +241 %" as established** — it is
  `disposition = "withdrawn_unreproducible"` and `RAW_PROVISIONAL` in `expectations.toml`, computed on
  **unmasked** data, and assigned to *this* milestone (vision `:47` bars such figures until M-A7
  lands); a masked recomputation differs materially. **No figure is pre-registered** (vision D8): M-A7
  computes the range and that becomes the first reproducible value.

  **PINNED 2026-08-27 — the body/tail split is q50 / q99**, matching
  `stats_precision.py:169`'s own choice. D5 required the two to be reported separately without saying
  where one ends; q95, q99 and q99.9 are all defensible and would materially change the reported
  transferability. **Body = the distribution up to q50; tail = q99.** One definition, and it is the one
  the precursor already uses.

- **D5a — The elevation bands are NAMED HERE, once, chosen a priori from the cited literature.**
  `< 700 m` / `700–2,000 m` / `2,000–3,000 m` / `≥ 3,000 m`, giving **9 / 9 / 5 / 3** of the 26
  stations — no degenerate band. The edges follow the literature the milestone already cites (southern
  margin ~500–700 m; Lesser Himalaya ~2,000–2,200 m), are **declared, never fitted to our data**, and
  every band product reports its station count.

  **⛔ The band ESTIMAND is defined here too, because edges alone do not determine the answer**. A band profile is the **unweighted mean of its member stations' profiles**, not a
  pooled exposure-weighted mean of their observations — under an MNAR mask a station with more
  retained hours would otherwise dominate its band, making the band figure a function of retention
  (the pattern Plan 184 D13 exists to keep visible). The pooled form is reported **as a sensitivity**,
  because the two genuinely differ: measured, the `< 700 m` peak is **21 UTC pooled vs 23 UTC
  station-equal**. Band adequacy (D9) uses the season-years **common to the band's stations**, not
  their union — for `≥ 3,000 m` that is **n = 4 / inadequate**, where the union would have claimed
  **n = 5 / adequate**. Take the conservative one and label it.

  **The same station-equal rule governs band DISTRIBUTIONS, not just profiles.** Each station
  contributes **equal probability mass**, never equal observation count — station wet-hour counts span
  **732–5,232**, so pooling would let the wettest-sampled station set the band's tail. Measured, the
  `700–2,000 m` wet-hour q99.9 is **46.935 mm/h pooled vs 51.441 mm/h station-equal**. Pooled is
  reported as the sensitivity, as for profiles.

  **Recorded limit:** `≥ 3,000 m` holds only **three** stations (4/5/5 JJAS season-years) and one of
  them is **Olangchunggola**, whose 03 UTC peak is unexplained (D7). The band most relevant to the
  snow question is the thinnest and contains the track's one open anomaly — say so wherever a
  `≥ 3,000 m` band figure is reported. *(Note: both this plan's
  Exit and the milestone's require per-band products while no task produced them and no parameter
  defined the edges, so different implementers would have produced different profiles.)*

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

  **⛔ A DJF season-year is NOT a calendar year.** DJF uses `params.year_attribution`
  (`december_belongs_to_following_djf`), never `timestamp.dt.year()` —
  `coloc_bootstrap.per_season_hourly_means` is JJAS-only by its own docstring. Reusing it for DJF
  mislabels D9's adequacy flag: measured, **Lete reads 4/inadequate instead of 5/adequate, and
  Olangchunggola 5/adequate instead of 4/inadequate.**

  **⛔ CORRECTED 2026-08-27 (independent review) — "where retention supports them" IS a completeness
  threshold, and this decision forbids completeness thresholds in the same breath.** The two halves
  contradict each other, and an implementer could legitimately report a different set of seasons per
  station. **Resolved in favour of the no-threshold rule:** report **all four seasons — MAM, JJAS, ON,
  DJF — for every station**, each carrying its own exposure, its `n_season_years` and its adequacy
  designation. A season resting on few retained hours is **labelled**, never omitted, because omitting
  it silently tells a reader the season was not informative when in fact it was not reported. JJAS
  stays *primary* in the sense that it leads the report and carries the headline — not in the sense
  that other seasons are conditional on passing a bar.

- **D9 — Bootstrap by resampling whole SEASON-YEARS, and carry the precedent's ADEQUACY FLAG.**
  Precipitation is strongly serially correlated, so hour-level resampling would break serial
  dependence and return falsely tight intervals. Follow `coloc_bootstrap`'s season-year precedent —
  **including the part an earlier draft omitted**: `BootstrapPeakSpread` carries `n_season_years` and
  `adequate_sample`, and its docstring says a caller "must gate on THIS, never on `spread_hours`
  alone — a narrow spread from too few season-years is indicative, not adequate."

  **This bites hard here, measured on the real data.** JJAS season-years per station:
  **Udayapur Gadhi 2**; Lete, Num and Olangchunggola **4**; six stations 5; sixteen 6. Plan 182 D5
  sets adequacy at **≥ 5 seasons**, so **four of 26 stations are below the bar**, and at n = 2 the
  nonparametric season bootstrap has only **three distinct resample compositions** — an interval that
  would read as ordinary uncertainty while being nearly meaningless.

  So: every interval carries its `n_season_years` and its adequacy designation, and an inadequate
  interval is labelled, never suppressed. **Do NOT filter stations on it** — that is exactly the
  filter-on-retention error Plan 184 D13 forbids; stratify and label instead.

  **PINNED 2026-08-27 — the interval construction is named, because the precedent does not supply it.**
  `coloc_bootstrap` returns a circular range of peak hours and takes caller-supplied RNG and resample
  settings, so "follow the precedent" fixes the RESAMPLING UNIT (whole season-years) and the ADEQUACY
  FLAG (≥ 5 season-years, Plan 182 D5) but leaves the interval itself undefined. Fix all three:
  **percentile method, 2.5 / 97.5 bounds, 2,000 resamples, and an INJECTED seeded RNG** (CLAUDE.md
  forbids reaching for module-level randomness in logic that must be testable). A report that cannot be
  reproduced bit-for-bit fails the same Exit condition M-A6's report failed on 2026-08-27.

- **D10 — Consume Plan 184 T1's GAUGE-ONLY masked population; do not build a second one, and do not
  depend on the rest of Plan 184.** *(The one cross-plan coupling, deliberate and narrow.)* Two
  independent implementations of "the masked series" would drift invisibly — both looking right in
  isolation — so there is one definition feeding both milestones M-A8 joins.

  **Two constraints, both learned the hard way.** (a) Consume 184 T1's **gauge-only** output, never the
  paired frame's "gauge half" — the paired frame is restricted to timestamps retained on **both**
  sides, which would make this gauge-only milestone conditional on ERA5. (b) The gate belongs in the
  **JSON phase graph** (`"plan-184-T1"` on phase-1), the repo's convention for cross-plan gates
  (`152:1024`), **not** in frontmatter: `depends_on: [184]` would serialise M-A7 behind all of M-A6,
  contradicting the milestone doc's A6/A7 parallelism (`milestones:412`).

## Tasks

Four tasks, three phases. `$ENV` abbreviates
`DHM_PRECIP_XLSX=data/dhm_precip/combined_precipitation_37_stations.xlsx`.

### T1 — masked diurnal profiles with exposure (depends: Plan 184 T1)
**In:** hour-of-day profiles on the masked series **per station AND per D5a elevation band**, each
carrying its **per-hour retained count** (D2) and its band station count, plus the D9 season-year
bootstrap with `n_season_years` and its adequacy designation; Olangchunggola's status recorded per D7.
**Out:** any between-station transferability claim; any use of unmasked `on_grid`.
**Verify:** `uv run pytest tests/unit/scripts/test_ma7_profiles.py -q`, including a **regression that
rows removed by the M-A3 mask do not enter a computed profile** — assert on the output, not on input
provenance: structurally identical Polars frames carry no masked/unmasked marker, so a "refuse
unmasked input" test would need exactly the wrapper apparatus this plan forbids.
Then `$ENV uv run pytest tests/integration/test_dhm_precip_reproduction.py -q`

### T2 — wet-hour intensity distributions (depends: Plan 184 T1)
**In:** wet-hour intensity distributions **per station AND per D5a elevation band**, with the 0.2 mm/h
floor for frequency statistics and unthresholded values for mass statistics (D4), body and tail
reported separately with season-year bootstrap intervals carrying their adequacy designation
(D5, D9). **Out:** the transferability claim (T3); any threshold applied to mass statistics.
**Verify:** `uv run pytest tests/unit/scripts/test_ma7_intensity.py -q`, including a test that asserts
the **value** of a mass statistic equals the unthresholded computation and differs from the
thresholded one — assert on the number, not on input provenance, for the same reason as T1.
**Exposure (added 2026-08-27, independent review):** every distribution carries its **retained wet-hour
count and its total retained-hour count**, exactly as T1's profiles carry per-hour exposure. The Exit
requires exposure on distributions AND profiles; without this, the tasks did not entail the Exit and an
implementer could satisfy every task and still miss the milestone.

### T3 — transferability and elevation stratification (depends: T1, T2)
**In:** the quantified statement of what transfers between stations, expressed as a divergence or a
held-out prediction error (D3), reported **by elevation band and by reporting-resolution group side by
side** with the attribution explicitly declined (D6). **Out:** any transferability headline expressed
as a correlation; any attribution of an effect to elevation rather than resolution.
**Verify:** `uv run pytest tests/unit/scripts/test_ma7_transfer.py -q`, including a test asserting the
reported transferability field **is** a divergence or held-out prediction error and that its value
changes when the underlying distributions do. *(A "refuse a correlation" test is not executable and
would contradict D3, which permits profile-shape correlation as a secondary statistic.)*

### T4 — report and Exit (depends: T1–T3)
**In:** one runner writing the markdown report and tables to `--out` (M-A10 shape), every profile and
distribution carrying its exposure, every result labelled conditional-on-retention; results folded into
`docs/design/dhm-precipitation-milestones.md`, this track's only durable record.
⛔ **The runner writes ONLY under `--out`; folding results into the milestones document is a HUMAN
step** (Plan 184 T6 P5). A runner that rewrites this track's only durable record lets a rerun silently
restate history. **Out:** any
disaggregator design or Phase-2 recommendation.
**Verify:** `$ENV uv run python scripts/dhm_precip/ma7_run.py --out <dir>` then
`$ENV uv run pytest tests/integration/test_dhm_precip_reproduction.py -q`

```json
{
  "phases": [
    {"id": "phase-1", "tasks": ["T1", "T2"], "parallel": true, "depends_on": ["plan-184-T1"]},
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
