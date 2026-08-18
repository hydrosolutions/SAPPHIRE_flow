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

## ⛔ CORRECTED after slim review 2026-08-18 — the test as first written could NOT identify H1

**BLOCKER, accepted.** H1 blames **sub-0.1 mm resolution-level counts**. But the plan's wet-hour
statistic used **≥0.2 mm**, which *excludes those values entirely*, and the unthresholded profile
*mixes them with genuine rain* (they are 22–34 % of Group A wet hours but only 0.8–2.1 % of mass).
**Nothing in the plan isolated the suspected contaminant**, so both H1 and H0 could produce the observed
anti-phase and the analysis would have declared H1 anyway — then modified M-A7 on that basis.

- **D7 — THE ABLATION IS THE IDENTIFYING TEST, and it is the primary result. REVISED after round 2,
  which showed the first version was NOT specific to sensor noise.**
  Recompute the normalised diurnal profile at a ladder of thresholds — **all values**, **≥0.1 mm**,
  **≥0.2 mm** — and report the phase at each. **Three constraints make it interpretable; without them
  it is not:**
  1. **Zero the sub-threshold values; NEVER drop the row.** The timestamp population must be
     **identical at every rung**. Deleting rows changes which hours are non-empty and reshapes the
     normalisation denominator **mechanically**, moving the peak for reasons having nothing to do with
     H1.
  2. **Run the SAME ladder on PYRAMID as a negative control.** Pyramid's resolution differs, so if its
     peak also migrates under thresholding, **the ablation moves peaks by itself** and any DHM movement
     is uninterpretable. The control is what licenses the inference.
  3. **State the residual confound rather than hiding it.** Thresholding removes **genuine light
     precipitation** as well as resolution-level counts. If physical morning drizzle is systematically
     lighter than nocturnal storms, the DHM peak shifts toward Pyramid's **even when every count is
     real** — falsely supporting H1. ⇒ **A DHM-only shift is SUGGESTIVE, never conclusive**, unless it
     can be distinguished from intensity-dependent physical rainfall. **The plan does not currently
     have a way to make that distinction, and must say so in its verdict.**

- **D8 (NEW) — Co-location is NOT identical exposure, and normalisation does NOT cancel a
  diurnally-varying catch bias.** 1.4–1.9 km and 130–200 m in steep terrain can carry genuine
  microclimatic difference, and neither network's instrument type, orifice height or wind exposure is
  documented. Critically: **normalisation cancels only *hour-independent* multiplicative undercatch —
  and mountain wind is strongly diurnal** (anabatic/katabatic), so catch efficiency is plausibly
  *hour-dependent*, which normalisation does **not** remove.
  ⇒ **A real micro-climatic or wind-driven difference is a live alternative this test cannot exclude.**
  The verdict must be phrased against H1 *and* this alternative, never as "co-located therefore
  comparable". If D7's ablation refutes H1, this becomes the leading explanation, not a footnote.

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

- **D3 — CORRECTED (major, slim review): asymmetric masking MANUFACTURES the very gap we would read
  as evidence.** The first version applied the M-A3 mask to DHM and only a range check to Pyramid.
  **M-A3 preferentially removes DRY hours** — its dominant population is long zero runs (Aiselukhark
  alone contributes 2,852 of 11,381 masked rows) — so masking one side **inflates DHM's wet-hour
  fraction relative to Pyramid's**, producing a positive frequency gap that looks exactly like noise
  contamination but is an artefact of unequal selection. **Carrying `n` does not cure this**; the
  populations differ.
  ⇒ **Compare on COMMON RETAINED TIMESTAMPS.** Pair the two series hour-by-hour and keep only hours
  retained on **both** sides (M-A3 for DHM; the physical range check for Pyramid, since applying our
  defect-specific rules to another network's instrument remains unjustified). Report the common-retained
  count, and report each side's own retention separately so the pairing loss is visible.
  **The wet-hour fraction is reported ONLY on the paired population** — a wet-hour fraction over
  differently-selected populations is not a comparison.

- **D4 — Report the retained-hour count beside every statistic (vision Rule 1).** Both series are
  MNAR after masking. Every number carries its `n`, and no annualisation is performed.

- **D5 — Two windows, reported separately.** **(a) Overlap:** JJAS **2020–2023** (Lukla's overlap is
  only **2021–2023**), the contemporaneous comparison. **(b) Full record:** each station's whole JJAS
  history, the climatological comparison. **The climatological comparison is the primary one** — a
  diurnal cycle is a stable climatological feature, and the overlap is thin (Lukla: 3 monsoons,
  ~4,000 h). **⛔ The r = 0.82–0.98 figure I quoted earlier does NOT establish this and is withdrawn as
  evidence** (major, slim review): the 2020+ profile is a **subset of** the full profile, so correlating
  them is **in-sample**, and correlating 24 smooth, autocorrelated bins inflates `r` regardless — the
  same artefact class as this track's withdrawn q-vector `r = 0.998`. A handful of monsoon events could
  set the decisive peak while `r` stayed high.
  ⇒ **Establish adequacy properly or not at all:** compare **disjoint** periods (pre-2020 vs 2020+),
  and attach **sampling uncertainty** by bootstrapping whole monsoon seasons (resample season-years,
  recompute the profile, report the spread of the peak hour). **⚠️ Round 2: the bootstrap as first written could FALSELY CERTIFY adequacy.** Lukla's overlap is
  only **three season-years**, and three seasons all peaking at 23:00 yield **zero spread** — which
  would authorise a verdict while leaving interannual uncertainty entirely unmeasured. And the spread
  must be **circular** (D9): peaks split 23:00/00:00 are 1 h apart, not 23.
  ⇒ **Circular intervals throughout, and a small-sample rule: fewer than 5 season-years cannot on its
  own establish adequacy**, regardless of how narrow the spread looks. With 3 units, report the spread
  as indicative and rest the verdict on the full record.
  **If the circular bootstrap spread on the peak hour exceeds ±2 h, the overlap window cannot support a
  phase verdict** and only the full record may be
  used — with D8's caveat that a non-contemporaneous comparison assumes the diurnal cycle is stationary
  across the record.

- **D6 — A null result is a real result.** If the profiles broadly agree, H1 is not supported and the
  Group A signal survives — which strengthens M-A7 rather than weakening it. **The plan must not be
  written so that only "artefact" counts as success.**

- **D9 (NEW, round 2) — THE VERDICT RULE MUST BE QUANTITATIVE, CIRCULAR, AND ABLE TO SAY
  "INDETERMINATE".** "Moves toward" and "survives" had no boundary, no uncertainty test, and no rule
  for the two pairs disagreeing — a rule that cannot return *inconclusive* will always return whatever
  the analyst expected.
  - **Phase distance is CIRCULAR.** Hour-of-day wraps: 23 and 00 are **1 h apart, not 23**. Every
    peak-hour comparison, spread and interval uses circular statistics. Pyramid's peak sits at 22–00,
    so this bites immediately and ordinary arithmetic would produce nonsense.
  - **Pre-declared thresholds** (fixed before the numbers are seen): H1 **supported** if the DHM peak
    moves ≥4 h circularly toward Pyramid's across the ladder *and* the Pyramid control moves <2 h;
    H1 **refuted** if the DHM peak moves <2 h; **INDETERMINATE otherwise** — including whenever the
    control moves ≥2 h.
  - **Per-station verdicts first, then synthesis.** Lukla and Syangboche are reported **separately**.
    If they disagree, the outcome is **INDETERMINATE for Group A as a whole**, and the disagreement is
    itself the finding — it is not averaged away.
  - **INDETERMINATE is a permitted, publishable outcome** and blocks the M-A7 correction rather than
    licensing it.

## Exit

- **The D7 threshold ladder — the primary result**: the DHM normalised profile at *all values*,
  *≥0.1 mm* and *≥0.2 mm*, with the peak hour at each, showing whether the phase survives removal of
  resolution-level counts
- Normalised JJAS diurnal profiles for both co-located pairs, in NPT, both windows, `n` beside each
- Wet-hour fractions **on the paired common-retained population only** (D3), with each side's own
  retention reported separately
- A **bootstrap spread on the peak hour** by monsoon season (D5); if it exceeds ±2 h on the overlap
  window, the verdict rests on the full record only, stated as such
- **The Pyramid negative control** (D7.2): the same threshold ladder applied to Pyramid, showing
  whether the ablation moves peaks on its own — without this the DHM result is uninterpretable
- **Per-station verdicts** (Lukla, Syangboche) under D9's pre-declared circular thresholds, then a
  synthesis — with **INDETERMINATE** a permitted outcome, including when the two disagree
- A stated **verdict on H1** with its uncertainty, phrased no finer than ±2 h, explicitly adjudicated
  **against D8's micro-climate/wind alternative** and **against D7.3's intensity-dependent-drizzle
  confound**, not only against H0
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
