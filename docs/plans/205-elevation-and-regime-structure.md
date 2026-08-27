---
status: DRAFT
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

**DRAFT.** Not for implementation until the owner confirms.

## ⛔ PROPORTIONALITY IS BINDING, AS IT WAS ON 184 AND 193

**This is a CHARACTERISATION, not a system.** No new framework, abstraction layer, config surface,
plugin seam or file format. If a decision fits in a sentence, it must not become a module. **Adding
length is a cost.** Reviewers: "no findings" is a complete review; a finding must name a concrete
defect, not a missing feature.

**Most of this milestone's DHM half is already computed.** M-A6 produced per-band bias estimands and
M-A7 produced per-band intensity and diurnal statistics. M-A8 assembles what exists and answers one
question about it. It does not recompute them.

## ⭐ THE CENTRAL QUESTION IS ALREADY ANSWERED, AND THE ANSWER IS "NO"

M-A8's stated exit permits either "elevation relationships with the confound bounded" **or a statement
that the sample cannot separate them**. Measured 2026-08-27 on the 26-station masked population:

| band | Group A (0.01 mm) | Group B (0.2 mm) |
|---|---|---|
| `< 700 m` | 0 | 9 |
| `700–2,000 m` | 0 | 9 |
| **`2,000–3,000 m`** | **3** | **2** |
| `≥ 3,000 m` | 3 | 0 |

**Group A spans 2,490–3,700 m. Group B spans 67–2,147 m. The ranges do not overlap** — there is no
high-altitude Group B station and no low-altitude Group A station anywhere in the sample. Reporting
resolution and elevation are therefore **perfectly confounded at station level**, and no stratification,
matching or covariate adjustment over these 26 stations can separate them. That is not a modelling
difficulty to be worked around; it is a property of the sample.

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

- **D1 — The DHM half's answer is the "cannot separate" statement, and M-A8 delivers it as a RESULT.**
  Not as a failure, not as a caveat on some other number. The cross-tabulation above, the
  non-overlapping ranges, and the one mixed band are the deliverable. **⛔ Do not attempt a separation
  the sample cannot support** — no covariate adjustment, no matching, no regression with both terms.
  Any such fit would be reporting the arbitrary tie-break an algorithm makes between two perfectly
  collinear predictors.

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
  magnitude — it does not forbid conditioning an estimand on phase and saying so in its name. The snow-dominated high basins —
  where the flood interest sits — remain out of reach by this route, and the report says so.

- **D5 — The gradient is signed as a RAIN-PHASE gradient, never as "the precipitation lapse rate".**
  It describes rain-phase hours on one transect in one valley. ERA5-Land structurally cannot supply a
  competing estimate: per Plan 184 D7 its `total_precipitation` is interpolated from ERA5 and never sees
  the 0.1° orography, so any gradient fitted to it is the parent field on a finer grid.

- **D6 — The ~18 % noise floor is a FLOOR ON RESOLVABILITY, not an uncertainty estimate.** AWS0 and
  AWS1 sit at the same 5,035 m: over 12,819 common hours their wet-hour count ratio is 1.01 (they agree
  on *when*) while the rain-only amount ratio is 1.18. So a vertical gradient must exceed ~18 % to be
  distinguishable from siting and exposure alone. **n = 1 pair, and its common window (2000–2004) is
  older than the rest of the record** — report both caveats beside the floor every time it is used.

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
**In:** the group × band cross-tabulation, the non-overlapping elevation ranges, and the
`2,000–3,000 m` band's group-split q99/q50 ratios — assembled into the D1 statement that the sample
cannot separate reporting resolution from elevation, with D3's limits attached.
**Out:** any covariate adjustment, matching, or two-term regression (D1); any attribution of M-A7's
band-transferability failure to either factor.
**Verify:** `uv run pytest tests/unit/scripts/test_ma8_confound.py -q`, including a test that a
station's group and band are read from the existing helpers (`resolution.infer_reporting_resolution`,
`ma6_estimands.assign_elevation_band`) rather than re-derived, and a test asserting the two groups'
elevation ranges do not overlap on the current population.

### T2 — the Pyramid rain-phase gradient (depends: nothing new)
**In:** a JJAS rain-phase precipitation gradient over the six Pyramid RR stations (2,660–5,600 m),
rain-screened per D4 with the 0 °C / 2 °C sensitivity, hour-of-day exposure equalised (D9), reported
against D6's ~18 % resolvability floor and carrying its own `n` per station.
**The rain line is MEASURED, not assumed** — report the
per-station rain-phase hour counts up the transect and let them show where the estimate stops being
supportable; pre-registering an elevation would be exactly the pre-registered threshold vision D8
forbids.
**Out:** any extrapolation above the rain line; any snow-phase or all-phase gradient; any claim framed
as "the precipitation lapse rate" (D5); any comparison to an ERA5-Land-derived gradient (D5).
**Verify:** `uv run pytest tests/unit/scripts/test_ma8_gradient.py -q`, including a test that an
unscreened (all-phase) gradient is refused rather than computed, and a test that the reported gradient
carries its comparison to the noise floor.

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
reported descriptively with every number carrying the conditions it was published with; **an explicit
statement that this sample cannot separate reporting resolution from elevation**, supported by the
non-overlapping group ranges and by the one band containing both; and a JJAS rain-phase precipitation
gradient on the Pyramid transect, bounded at the rain line, signed as rain-phase only, and reported
against the measured resolvability floor with its n = 1-pair and older-window caveats attached.

## Non-goals

Any correction, adjustment, disaggregation or downscaling design · any Phase-2 GO/NO-GO (M-A9/M-DEC) ·
any gradient above the rain line · any attribution of an effect to elevation rather than reporting
resolution, or the reverse · recomputing M-A6's or M-A7's numbers · IMERG (M-A5b) · a second masked
series (D9).
