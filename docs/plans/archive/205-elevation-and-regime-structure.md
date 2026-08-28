---
status: COMPLETE
created: 2026-08-27
plan: 205
title: M-A8 — elevation and regime structure, and the rain-phase gradient
scope: Characterise the elevation dependence of the M-A6 bias estimands and the M-A7 intensity/diurnal statistics, bound the reporting-precision/altitude confound (or state that the sample cannot separate it), and fit a JJAS rain-phase precipitation gradient on the Pyramid transect bounded at the rain line. NOT a correction design, NOT a disaggregator, NOT a Phase-2 GO/NO-GO, NOT an extrapolation above the rain line.
depends_on: [184, 193]
blocks: [M-A9]
source: docs/design/dhm-precipitation-milestones.md § M-A8
---

# Plan 205 — M-A8 elevation and regime structure

## Status

**READY** (owner, 2026-08-27) — **and COMPLETE**: T1–T3 shipped in PR #218.

## ⛔ PROPORTIONALITY IS BINDING, AS IT WAS ON 184 AND 193

**This is a CHARACTERISATION, not a system.** No new framework, abstraction layer, config surface,
plugin seam or file format. If a decision fits in a sentence, it must not become a module. **Adding
length is a cost.** Reviewers: "no findings" is a complete review; a finding must name a concrete
defect, not a missing feature.

**Most of this milestone's DHM half is already computed.** M-A6 produced per-band bias estimands and
M-A7 produced per-band intensity and diurnal statistics. M-A8 assembles what exists and answers one
question about it. It does not recompute them.

## ⭐ WHAT IS IDENTIFIED, AND WHAT IS NOT

M-A8's stated exit permits either "elevation relationships with the confound bounded" **or a statement
that the sample cannot separate them**. Measured 2026-08-27 on the 26-station masked population:

| band | Group A (0.01 mm) | Group B (0.2 mm) |
|---|---|---|
| `< 700 m` | 0 | 9 |
| `700–2,000 m` | 0 | 9 |
| **`2,000–3,000 m`** | **3** | **2** |
| `≥ 3,000 m` | 3 | 0 |

**Group A spans 2,490–3,700 m. Group B spans 67–2,147 m. The ranges do not overlap** — there is no
high-altitude Group B station and no low-altitude Group A station anywhere in the sample. So the
**BETWEEN-group contrast is unidentified**: any difference between Group A and Group B is equally a
resolution effect and an elevation effect, and no covariate adjustment over these 26 stations can
separate them.

**⛔ That does NOT make elevation unanalysable, and an earlier revision of this plan wrongly said it
did.** Elevation varies widely *inside* each group at fixed reporting resolution — Group B across
**2,080 m** (67 → 2,147 m, 20 stations) and Group A across **1,210 m** (2,490 → 3,700 m, 6 stations).
A **within-group** elevation analysis therefore holds resolution constant by construction and is
identified. It is also the method this track has already used: the exploratory result that motivated
M-A7 — profile similarity against elevation difference at `r = −0.486` — was computed
**"within Group B alone (one reporting population, 67–2,147 m, 190 pairs)"**
(`docs/design/dhm-precipitation-milestones.md:573`) for exactly this reason.

**One band contains both groups, and it is the band M-A7's headline failed on.** In `2,000–3,000 m`,
M-A7 found zero of five stations predicting within 25 % — the only such band. The q99/q50 ratio that
the prediction method targets splits **exactly** along group lines:

| station | elevation | group | q99/q50 |
|---|---|---|---|
| Aiselukhark | 2,064 m | **B** | 32.00 |
| Nagarkot | 2,147 m | **B** | 31.35 |
| Lete (FNEP) | 2,490 m | **A** | 9.56 |
| Lukla Airport | 2,860 m | **A** | 12.02 |
| Ghorepani | 2,742 m | **A** | 16.21 |

Group B ≈ 31–32, Group A ≈ 9.5–16.2, no overlap. This is the sample's **only** observation bearing on
the confound, and it is consistent with a reporting-resolution effect — but elevation still varies
across it (2,064 → 2,860 m, with a 343 m gap between the groups), so it does not settle the question
either. n = 5.

## Decisions

- **D1 — Elevation is analysed WITHIN each reporting-resolution group; only the BETWEEN-group
  contrast is reported as unidentified.** Group B (20 stations, 67–2,147 m) is the primary elevation
  analysis and Group A (6 stations, 2,490–3,700 m) is reported beside it, each at fixed resolution, each
  carrying its own `n`. **⛔ Do not fit a model with both elevation and group as terms across the whole
  sample** — the groups' elevation ranges do not overlap, so no data constrains the group term against
  an elevation jump at the gap, and the fit reports an assumption rather than a measurement. **⛔ Do not compare a Group A number to a
  Group B number and attribute the difference to either factor.** The between-group statement is a
  result in its own right, not a failure: M-A8's exit permits exactly it.

- **D2 — Elevation relationships are reported DESCRIPTIVELY, carrying M-A6's and M-A7's existing
  per-band numbers.** M-A8 assembles; it does not recompute. Every assembled number keeps the
  conditions it was published with — its `n`, its retained exposure, its sub-freezing mass fraction
  where M-A6 attached one (Plan 184 Exit 2 binds here too), and its adequacy designation where M-A7
  attached one. **⛔ A number may not be re-quoted stripped of its companions.**

- **D3 — The `2,000–3,000 m` band is reported as the confound's only evidence, with its own limits
  attached.** State the group split of the q99/q50 ratios, state that it is consistent with a
  resolution effect, and state that elevation varies across it so it does not isolate one. n = 5, one
  band, one season.

- **D4 — The Pyramid rain-phase gradient is IN scope, and is bounded at the rain line.** Six RR
  stations span 2,660–5,600 m. Pyramid's gauges are **unheated**, so undercatch grows with elevation and
  a gradient fitted to raw totals measures the undercatch profile rather than precipitation. The
  **rain-only screening is what makes the claim legitimate**, because it is the condition under which
  M-A10's "undercatch largely cancels" is actually true. Screen on **Pyramid's OWN `AT`** — every RR station
  carries it, so this needs no cross-source pairing and inherits none of Plan 184 D7's cell-vs-point
  caveat — at D4/D14's 1.5 °C with the 0 °C and 2 °C sensitivity M-A6 already uses; restrict to JJAS;
  and **⛔ never extrapolate above the rain line.**

  *This screen DEFINES the estimand (a rain-phase gradient); it does not gate a reported magnitude.*
  Plan 184 D4's "the fraction ANNOTATES, never gates" binds the sub-freezing mass fraction attached to a
  magnitude — it does not forbid conditioning an estimand on phase and saying so in its name.

  **⛔ BUT the screening removes the SNOW problem, NOT the WIND-CATCH problem, and an earlier revision
  of this plan wrongly implied it removed both.** M-A10 (Plan 182) explicitly retains
  elevation- and exposure-dependent **wind catch** as an unresolved alternative and binds Pyramid to
  shape and timing only. Rain catch efficiency also degrades with wind speed, and wind exposure
  generally increases up a valley transect, so a rain-phase gradient fitted up this transect is
  **confounded with the wind-catch profile**. Consequences, all binding:
  **(i)** the quantity is named an **apparent rain-phase gradient, uncorrected for wind catch**, never
  "the precipitation gradient"; **(ii)** its sign is stated, and it runs the way that
  makes the result WEAKER, not stronger: catch efficiency falls with wind and wind exposure rises up a
  transect, so observed precipitation declines FASTER than true precipitation
  (`d log(obs)/dz = d log(true)/dz + d log(CE)/dz`, second term negative). The apparent gradient is
  therefore an **UPPER bound in magnitude on any true decline** — the true decline is no steeper than
  the apparent one, and could in principle be nil, with the whole apparent decline being catch. The snow-dominated high basins —
  where the flood interest sits — remain out of reach by this route, and the report says so.

- **D5 — The gradient is signed as a RAIN-PHASE gradient, never as "the precipitation lapse rate".**
  It describes rain-phase hours on one transect in one valley. ERA5-Land structurally cannot supply a
  competing estimate: per Plan 184 D7 its `total_precipitation` is interpolated from ERA5 and never sees
  the 0.1° orography, so any gradient fitted to it is the parent field on a finer grid.

- **D6 — The AWS0/AWS1 contrast is an OBSERVED SAME-ELEVATION DISCREPANCY, not a resolvability
  floor.** AWS0 and AWS1 sit at the same 5,035 m: over 12,819 common hours their wet-hour count ratio is
  1.01 (they agree on *when*) while the rain-only amount ratio is 1.18. Report that as what it is — one
  realized pair, one window (2000–2004, older than the rest of the record) — beside the gradient, so a
  reader can weigh a fitted decline against a same-elevation disagreement that exists in the data.

  **⛔ Do NOT convert it into a threshold.** "A gradient must exceed ~18 % to be distinguishable" does
  not follow, and neither does its converse: two gauges with unbiased errors still differ over a finite
  sample by noise alone, so a single realized ratio bounds nothing. **This track has already withdrawn
  the identical inference** — Plan 184 D8 withdrew "half the discrepancy is a lower bound on within-cell
  variability" from the Kirtipur/Khumaltar pair for exactly this reason, noting the argument needs
  effectively error-free aggregates rather than merely unbiased ones.

- **D7 — No pre-registered gradient value** (vision D8). M-A8 computes it; that becomes the first
  reproducible number. **⛔ Do not carry forward any gradient quoted in an earlier draft or milestone
  note as an expectation to be matched.**

- **D8 — Report determinism is an exit condition, and this track has failed it twice.** M-A6's report
  produced three different values across four runs; M-A7's produced two. Both were order-dependent float
  aggregation. Any aggregation M-A8 adds rounds to a fixed precision before comparison or rendering
  (`ma6_estimands._BUCKET_TOTAL_ROUNDING_DECIMALS`, `ma7_profiles._HOURLY_MEAN_ROUNDING_DECIMALS` are
  the precedents), any bootstrap takes an injected seeded RNG, and **a two-run diff is not sufficient
  evidence** — the mechanism must be argued, because an intermittent nondeterminism passes sampling.

- **D9 — Consume the existing substrates; build no third one.** DHM side: Plan 184 T1's masked
  population, via M-A6/M-A7's own published results. Pyramid side: `pyramid_loader.py`'s `RR` and `AT`
  parsing, and `ma6_lapse_check.py`'s existing hour-equalisation and NPT/UTC reconciliation — Pyramid is
  irregularly sampled, and M-A6 measured a **1.00 °C** artefact from a naive monthly mean, so
  hour-of-day exposure must be equalised in any aggregate here too.

## Tasks

Three tasks, three phases. `$ENV` abbreviates
`DHM_PRECIP_XLSX=data/dhm_precip/combined_precipitation_37_stations.xlsx`.

### T1 — the confound bound (depends: nothing new)
**In:** the **within-group** elevation analysis of M-A6's bias estimands and M-A7's intensity and
diurnal statistics — Group B primary (20 stations, 67–2,147 m), Group A beside it (6 stations,
2,490–3,700 m), each at fixed reporting resolution and each carrying its own `n`; plus the group × band
cross-tabulation, the non-overlapping ranges, and the `2,000–3,000 m` band's group-split q99/q50 ratios,
assembled into the statement that the BETWEEN-group contrast is unidentified, with D3's limits attached.
**Out:** any whole-sample fit carrying both elevation and group as terms (D1); any comparison of a
Group A number to a Group B number with the difference attributed to either factor; any attribution of
M-A7's band-transferability failure to either factor.
**Verify:** `uv run pytest tests/unit/scripts/test_ma8_confound.py -q`, including a test that a
station's group and band are read from the existing helpers (`resolution.infer_reporting_resolution`,
`ma6_estimands.assign_elevation_band`) rather than re-derived, and a test asserting the two groups'
elevation ranges do not overlap on the current population.

### T2 — the Pyramid rain-phase gradient (depends: nothing new)
**In:** the **apparent rain-phase gradient, uncorrected for wind catch** (D4's mandatory name) over
the Pyramid RR stations sharing P1's common window — **five stations, AWS0 excluded** (its record ends
2005) — rain-screened per D4 with the 0 °C / 2 °C sensitivity, hour-of-day exposure equalised (D9),
carrying its own `n` per station, and reported beside D6's **observed** AWS0/AWS1 same-elevation
discrepancy with that pair's own caveats.
**The rain line is MEASURED, not assumed** — report the
per-station rain-phase hour counts up the transect and let them show where the estimate stops being
supportable; pre-registering an elevation would be exactly the pre-registered threshold vision D8
forbids.

**PINNED — three mechanics T2's text would otherwise leave to the implementer:**
- **P1 — the time population is ONE COMMON WINDOW across the stations in the fit.** Pyramid records do
  not coincide, and the window is **computed from the archive, never assumed**. **⛔ CORRECTED
  2026-08-27 (measured during T1/T2):** an earlier revision of this pin said AWS0 ends in 2005 "while
  the other five overlap 2020–2023". AWS0's end is right; the rest is not. Measured `RR` spans:
  AWS1 2000-10→2023-12, AWS2 2001-10→2023-12, AWS3 2002-11→2023-12, AWS5 2001-10→2023-12, but
  **AWS4 — the TOP of the transect at 5,600 m — stops reporting `RR` on 2018-05-09** (its `AT` does
  continue to 2023, which is why M-A6's D14 note about a 2020–2023 overlap held for temperature and
  does not transfer to precipitation). The real five-station common window is therefore
  **2009-01-01 → 2018-05-09**.

  That forces a trade-off the plan did not anticipate, and the report must state which side it took:
  keeping AWS4 preserves the full **2,940 m** transect but costs 2018–2023 for everyone else; dropping
  it buys the recent period but lowers the transect's top to AWS2 at 4,260 m, losing 1,340 m of the
  relief the gradient exists to measure. **Keep AWS4 and the full transect**, and say plainly that the
  gradient is fitted on 2009–2018 rather than on a recent period. Fitting across stations
  with disjoint eras would read an era difference as an elevation gradient. Fit on the common window,
  **name it and name every station excluded for not covering it**. AWS0 is therefore NOT in the
  cross-station fit; its only role is D6's same-elevation contrast with AWS1.
- **P2 — hour-of-day equalisation does NOT equalise date or season exposure** (`ma6_lapse_check`'s
  helper equalises hour only, and Pyramid is irregularly sampled). State what is equalised and what is
  not; do not describe the result as "exposure-equalised" unqualified.
- **P3 — the fit is an ordinary least-squares line of `log(rain-phase mean hourly intensity)` on station
  elevation, reported as **% per km** with its interval, alongside the raw per-station values and their
  `n`. One form, named, so two implementations are comparable.
**Out:** any extrapolation above the rain line; any snow-phase or all-phase gradient; any claim framed
as "the precipitation lapse rate" (D5); any comparison to an ERA5-Land-derived gradient (D5).
**Verify:** `uv run pytest tests/unit/scripts/test_ma8_gradient.py -q`, including a test that an
unscreened (all-phase) gradient is refused rather than computed, and a test that the reported gradient
carries D4's mandatory name rather than a bare "precipitation gradient".

### T3 — report and Exit (depends: T1, T2)
**In:** one runner writing the markdown report to `--out` (M-A10 shape, as `ma6_run.py` and
`ma7_run.py` already follow), assembling T1 and T2 with every M-A6/M-A7 number carrying the companions
it was published with (D2).
⛔ **The runner writes ONLY under `--out`** — folding results into
`docs/design/dhm-precipitation-milestones.md` is a HUMAN step (Plan 184 T6 P5, Plan 193 T4 P7).
**Out:** any correction design, disaggregator, or Phase-2 recommendation.
**Verify:** `$ENV uv run python scripts/dhm_precip/ma8_run.py --out <dir>` run **twice** with both
outputs proven non-empty before comparison, byte-identical apart from the generated-at line (D8); then
`$ENV uv run pytest tests/integration/test_dhm_precip_reproduction.py -q`.

**PINNED 2026-08-27 — four points T3's text leaves open.** Its two predecessors needed the same block;
this one is shorter because the plan already pins the science.
- **Q1 — fixed table inventory**, one per Exit clause: (a) the within-Group-B elevation relationship;
  (b) Group A per station; (c) the group × band cross-tabulation with the non-overlapping ranges and
  the between-group unidentified statement; (d) the `2,000–3,000 m` band's group-split q99/q50 ratios
  as D3's only evidence; (e) the apparent rain-phase gradient with all four sensitivity legs and
  per-station rain-hour counts; (f) the observed AWS0/AWS1 same-elevation discrepancy; (g) consumed
  inputs, the fit window, and the seed if one is used.
- **Q2 — the locked headline set is SMALL and NAMED**: the within-Group-B correlation for each of the
  two assembled quantities; the apparent gradient at all four screens with its intervals; the fit
  window endpoints; and the AWS0/AWS1 wet-hour-count and rain-amount ratios. Nothing else. A snapshot
  of every station × quantity would break on any legitimate change and read as a regression.
- **Q3 — if the report renders ANY bootstrap-derived quantity** (M-A7 adequacy designations and
  intervals are bootstrap-derived, and D2 requires assembled numbers to keep their companions),
  **it seeds from one named constant and prints it**, as `ma7_run.REPORT_SEED` does. Phase 1 uses no
  RNG; if T3 introduces none either, say so in the report rather than leaving it ambiguous.
- **Q4 — the gradient is rendered with D4's mandatory name and P1's window.** "Apparent rain-phase
  gradient, uncorrected for wind catch", fitted **2009-01-01 → 2018-05-09**, with AWS0 named as
  excluded and AWS4 named as the station that sets the window's end. ⛔ Never "the precipitation
  gradient", and never without the period.

Refusals and inadequate values render as explicit absences with their reasons, never as blanks, zeros
or dropped rows — the convention Plan 184 T6 P3 and Plan 193 T4 P5 already establish; it is not
restated here.

```json
{
  "phases": [
    {"id": "phase-1", "tasks": ["T1", "T2"], "parallel": true},
    {"id": "phase-2", "tasks": ["T3"], "parallel": false, "depends_on": ["phase-1"]}
  ]
}
```

## Exit

The elevation relationships of M-A6's bias estimands and M-A7's intensity and diurnal statistics,
reported descriptively with every number carrying the conditions it was published with; **a within-group elevation
analysis at fixed reporting resolution** — Group B (20 stations, 2,080 m of relief) primary, Group A
(6 stations, 1,210 m) beside it — together with **an explicit statement that the BETWEEN-group
contrast is unidentified in this sample**, supported by the non-overlapping group ranges and by the
one band containing both; and the **apparent rain-phase gradient,
uncorrected for wind catch**, fitted on P1's common window over the five qualifying Pyramid stations,
signed as an **upper bound in magnitude** on any true decline, and reported beside the **observed**
AWS0/AWS1 same-elevation discrepancy with that pair's n = 1 and older-window caveats attached.

## Non-goals

Any correction, adjustment, disaggregation or downscaling design · any Phase-2 GO/NO-GO (M-A9/M-DEC) ·
any gradient above the rain line · any attribution of an effect to elevation rather than reporting
resolution, or the reverse · recomputing M-A6's or M-A7's numbers · IMERG (M-A5b) · a second masked
series (D9).
