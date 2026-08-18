---
status: DRAFT
created: 2026-08-18
revised: 2026-08-18
plan: 182
title: M-A10 — co-located gauge-vs-gauge adjudication (Pyramid network)
scope: Compare the normalised diurnal profile and wet-hour fraction of two DHM stations against two effectively co-located independent AWS from the Pyramid network, on mask-retained hours, to test whether the Group A high-altitude diurnal signal is physical or noise-floor contamination. Explicitly NOT a magnitude comparison, NOT an undercatch correction, NOT an ERA5-Land comparison (M-A6), NOT the full elevation-banded profile (M-A7).
depends_on: [173]
blocks: [M-A7, M-A9]
source: docs/design/dhm-precipitation-milestones.md
---

# Plan 182 — M-A10 co-located gauge-vs-gauge adjudication

## Status
**DRAFT.** Not for implementation until the owner confirms.

## Why this is worth doing before M-A7

Nepal v1 delivers a **3-hourly** product (and a daily one). At 3-hourly resolution the sub-daily
distribution of precipitation reaches the model directly, so an error in observed diurnal timing
propagates into **operational warning times**. M-A7 will build an elevation-banded diurnal profile from
our DHM data and OD-12's correction operator will consume it. **If the Group A high-altitude signal is
an artefact, all of that inherits the artefact.** This plan tests that first, cheaply.

**We can test it because two independent instruments sit essentially on top of two of our stations:**

| DHM station | Pyramid station | Separation | Elevation Δ |
|---|---|---|---|
| Lukla Airport (27.69, 86.73, 2,860 m) | AWS3 Lukla (27.70, 86.72, 2,660 m) | ~1.4 km | 200 m |
| Syangboche Airport (27.817, 86.717, 3,700 m) | AWS5 Namche (27.80, 86.71, 3,570 m) | ~1.9 km | 130 m |

Pyramid is **independent of both ERA5-Land and DHM** (pure in-situ; "ERA5" appears nowhere in
Salerno et al. 2025), which is what makes it an adjudicator rather than another opinion.

## The hypothesis under test

**H1 (artefact):** our Group A diurnal signal is **noise-floor contamination**. Group A gauges report at
**0.01 mm** resolution, and sub-0.1 mm hours are 22–34 % of their wet hours but only 0.8–2.1 % of mass
(M-A1). A near-uniform drizzle of resolution-level counts would produce a diurnal shape driven by
*sensor behaviour*, not weather.

**H0 (physical):** the signal is real orographic behaviour that the Pyramid instrument should broadly
reproduce.

**Preliminary evidence for H1, already computed (orchestrator, 2026-08-18, from the Level-1 files):**
Pyramid Lukla's JJAS profile peaks at **22–00 NPT** with its minimum at **07–10** (normalised 0.29/0.28
at 07–08). Our DHM Lukla peaks at **02 UTC ≡ 07:45 NPT** — inside Pyramid's minimum, 1.4 km away.
**Near anti-phase.** This plan makes that comparison rigorous, mask-consistent, and reportable.

## Design decisions

- **D1 — Compare SHAPE, never MAGNITUDE.** Report the **normalised** diurnal profile (each hour ÷ the
  station's own daily mean) and the **wet-hour fraction**. Both gauge networks undercatch — Pyramid's
  are **explicitly unheated** — so any magnitude comparison would be comparing two biased instruments
  and inviting the correction that vision **D6/D9** forbid. Normalisation makes undercatch largely
  cancel. **No mm totals are compared or reported.**

- **D2 — The time bases do not align, and this must be stated, not silently resolved.**
  Pyramid is **NPT (UTC+5:45)** (README, verbatim); DHM is **UTC**, period-ending (M-D3). Converting
  either way leaves hourly bins offset by **45 minutes** — they cannot be made to coincide.
  **Additionally, the Pyramid README does NOT state period-beginning vs period-ending**, adding ±1 h.
  ⇒ Total alignment uncertainty ≈ **±1.75 h**.
  **This is acceptable ONLY because the hypothesis concerns a ~12 h discrepancy** — the test is robust
  to ±1.75 h by a factor of ~7. ⇒ Report both profiles **in NPT**, state the uncertainty on the figure,
  and **do not claim any phase result finer than ±2 h.** A future analysis needing finer timing must
  first resolve the Pyramid period convention with the authors.

- **D3 — Apply the M-A3 mask to the DHM side; do NOT invent a mask for Pyramid.** Our side must be
  mask-consistent or we compare a cleaned series against a raw one. Pyramid Level-1 is published raw and
  ungapfilled; we apply only the same **physical-impossibility range check** used in M-A3
  (`0 ≤ x ≤ 200 mm/h`), and **record how many hours that removes**. Applying our defect-specific rules
  to another network's instrument would be unjustified.

- **D4 — Report the retained-hour count beside every statistic (vision Rule 1).** Both series are
  MNAR after masking. Every number carries its `n`, and no annualisation is performed.

- **D5 — Two windows, reported separately.** **(a) Overlap:** JJAS **2020–2023** (Lukla's overlap is
  only **2021–2023**), the contemporaneous comparison. **(b) Full record:** each station's whole JJAS
  history, the climatological comparison. **The climatological comparison is the primary one** — a
  diurnal cycle is a stable climatological feature, and the overlap is thin (Lukla: 3 monsoons,
  ~4,000 h). Verified 2026-08-18: the 2020+ subset reproduces the full-record shape at **r = 0.82–0.98**,
  so the short overlap is adequate for shape but the full record is better constrained.

- **D6 — A null result is a real result.** If the profiles broadly agree, H1 is not supported and the
  Group A signal survives — which strengthens M-A7 rather than weakening it. **The plan must not be
  written so that only "artefact" counts as success.**

## Exit

- Normalised JJAS diurnal profiles for both co-located pairs, in NPT, both windows, `n` beside each
- Wet-hour fractions at the pinned 0.2 mm threshold, both sides, both windows
- A stated **verdict on H1** with its uncertainty, phrased no finer than ±2 h
- If H1 is supported: an explicit list of which vision/M-A1 claims are affected, **filed as a correction
  for M-A7 to apply** — this plan does not itself rewrite the vision
- **No magnitude comparison anywhere in the output**

## Non-goals

ERA5-Land comparison (M-A6) · the full elevation-banded profile (M-A7) · any undercatch or magnitude
correction (D6/D9 forbid it) · any use of Pyramid's Lvl2 monthly reconstruction (gap-filled; Lvl1 only)
· adjudicating Humde/Olangchunggola, which are in Manang and Kanchenjunga, **not** the Khumbu, and have
no co-located partner.

## Data

`data/dhm_precip/pyramid/` (gitignored, CC BY 4.0, attribution required on any published result).
**Lvl1 files only.** `AWS3_Z2660_Lvl1.csv` (Lukla), `AWS5_Z3570_Lvl1.csv` (Namche). Note `AWS0` and
`AWS1` are two instruments at the same 5,035 m site, and `AWS4`/`AWSSC`/`CNG_SNP` carry no usable `RR`
for this work.
