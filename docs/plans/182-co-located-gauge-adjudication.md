---
status: READY
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
**READY — owner-confirmed 2026-08-18.** Gated by **three slim Codex rounds**: round 1 found the test
could not identify its own hypothesis (blocker); round 2, asked *what round 1 missed*, found the
ablation was not specific to sensor noise (blocker) and the verdict rule could not resolve partial or
discordant outcomes; round 3, a **verification pass** asking whether the fixes were correct, found the
negative control was **vacuous** — confirmed empirically: 0.00 % of Pyramid values fall below 0.2 mm.
Each round's findings are folded in place above.

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

- **D7 — REDESIGNED after the round-3 verification pass, which showed the negative control was
  VACUOUS. Verified empirically 2026-08-18 against the files on disk:**

  | | Pyramid Lukla | Pyramid Namche |
  |---|---|---|
  | positive JJAS values | 7,133 | 9,280 |
  | **below 0.2 mm** | **0 (0.00 %)** | **0 (0.00 %)** |
  | smallest value | **exactly 0.2000** | **exactly 0.2000** |
  | commonest values | 0.2, 0.4, 0.6, 0.8, 1.0 | 0.2, 0.4, 0.6, 0.8, 1.0 |

  **Every Pyramid value is a multiple of 0.2 mm** (LSI-Lastem tipping buckets, 0.2 mm resolution). So a
  threshold ladder is a **no-op on Pyramid by construction**: all rungs identical, control movement
  necessarily 0 h, and D9's "control moved < 2 h" clause **satisfied automatically**. The control
  licensed everything and could never have detected anything. **Removed.**

  **⇒ The resolution gap IS the design, not an obstacle.** DHM Group A reports **0.01 mm** increments;
  Pyramid **cannot represent anything below 0.2 mm at all**. That asymmetry gives a cleaner test than
  the ablation control ever did:

  1. **PRIMARY — matched-resolution comparison.** Compare **DHM thresholded at ≥0.2 mm** against
     **Pyramid at its native resolution** (which is already ≥0.2 mm). Both instruments can now
     represent the same events, so this is the only apples-to-apples phase comparison available.
  2. **The ablation, on DHM only** — profile at *all values* vs at *≥0.2 mm*, zeroing not dropping
     (round 2; verified sound: the daily mean changes but as a common scalar cannot move the peak).
  3. **Read the two together:**
     - DHM's peak moves under ablation **and** DHM@≥0.2 then agrees with Pyramid ⇒ **H1 supported** —
       the sub-0.2 mm counts carried the signal.
     - DHM's peak moves **but** DHM@≥0.2 still disagrees ⇒ sub-threshold values matter but do **not**
       explain the discrepancy ⇒ **INDETERMINATE**, and D8's alternatives lead.
     - DHM's peak does **not** move ⇒ **H1 refuted**; the signal is in rain both instruments can see.

  **⚠️ This also invalidates a comparison made earlier in this track.** The wet-hour fractions I
  contrasted (DHM 54–55 % vs Pyramid 33 %) are **not comparable**: Pyramid's is structurally lower
  because it *cannot record* anything below 0.2 mm. **Wet-hour fraction must be computed at a matched
  ≥0.2 mm threshold on both sides**, or it measures instrument resolution rather than weather.

  **Residual confound, unchanged (round 2):** thresholding also removes genuine light rain, so if
  physical morning drizzle is systematically lighter than nocturnal storms the DHM peak shifts even
  when every count is real. **A DHM-only shift remains suggestive, not conclusive**, and step 3's
  agreement test is what raises it above suggestion.

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

- **D9 — THE VERDICT RULE. Rewritten in round 3: the round-2 version was CONTRADICTORY and left
  "toward" undefined.**

  **The contradiction:** DHM moves 1 h while the control moves 2.5 h satisfied *both* "refuted (< 2 h)"
  and "INDETERMINATE (control ≥ 2 h)". **The undefined term:** with DHM at 08:00 and Pyramid at 20:00 —
  antipodal — "moves 4 h *toward*" has no unique direction on a circle.

  ⇒ **Evaluated as an ORDERED sequence of gates. The first gate that fires decides; no later gate can
  overturn it.** This ordering is what removes the overlap.

  0. **Adequacy gate (D5).** If the window fails the small-sample rule (< 5 season-years) **or**
     stationarity fails (disjoint-period peaks differ by > 4 h circularly) ⇒ **INDETERMINATE**, stop.
     *Round-3 finding: this gate did not previously exist, so a failed-adequacy Lukla could still reach
     a "supported" verdict on a pooled ladder.*
  1. **Matched-resolution gate (D7.1).** If DHM@≥0.2 and Pyramid disagree by > 4 h circularly *after*
     ablation ⇒ **INDETERMINATE** — sub-threshold counts are not the explanation.
  2. **Ablation gate (D7.2).** Circular movement of the DHM peak between *all values* and *≥0.2 mm*:
     **≥ 4 h ⇒ H1 supported** (given gates 0–1 passed); **< 2 h ⇒ H1 refuted**; **2–4 h ⇒
     INDETERMINATE**.

  **"Toward" is defined as a REDUCTION IN CIRCULAR DISTANCE** to the Pyramid peak — never as a signed
  direction, which is undefined for antipodal peaks. Movement is `|circ_dist(all, ≥0.2)|`; the
  *toward* test is `circ_dist(DHM@≥0.2, Pyr) < circ_dist(DHM@all, Pyr)`.

  **Per-station first, then synthesis.** Lukla and Syangboche get independent verdicts. **Disagreement
  ⇒ INDETERMINATE for Group A**, and the disagreement is itself reported as the finding, never averaged.

  **Threshold coherence** (checked, round 3): D2's ±1.75 h alignment uncertainty and D5's ±2 h bootstrap
  rule are both **strictly below** the 4 h decision boundary, and the 2 h refute-boundary sits at or
  above both. No decision turns on a difference smaller than the measurement uncertainty.

  **INDETERMINATE is a permitted, publishable outcome. It BLOCKS the M-A7 correction rather than
  licensing it** — the asymmetry is deliberate: an unproven artefact must not silently reshape
  operational timing.

- **D11 — ⛔ CORRECTED 2026-08-18: THE FULL RECORD IS THE ADJUDICATED COMPARISON. The overlap is
  corroboration.** *(Implementation review found the plan structurally unable to produce a verdict.)*

  **The contradiction I introduced:** D9's ordered gates say *"the first gate that fires decides; no
  later gate can overturn it"*, and gate 0 returns INDETERMINATE on <5 season-years. But the real
  overlap windows are **Lukla 2021–2023 (3 seasons)** and **Syangboche 2020–2023 (4)**, so gate 0 fires
  **unconditionally for both stations** and the runner can **never** return anything but INDETERMINATE.
  D5's own fallback — *"rest the verdict on the full record"* — was made unreachable by D9's
  no-overturn rule. **My round-3 fix broke my round-2 fix.**

  **Resolution, and it is what D5 already said:** D5 states the **climatological comparison is the
  PRIMARY one**. So:
  - **The verdict is adjudicated on the FULL RECORD** — DHM's full JJAS (2020–2025, **6 season-years**)
    against Pyramid's full JJAS (Lukla 2005–2023, Namche 2002–2023). **Both sides clear the 5-season
    threshold comfortably**, so a decisive verdict is reachable in production.
  - **The overlap window is computed and reported as CORROBORATION**, carrying its own small-sample
    caveat. It does **not** gate the verdict; a disagreement between overlap and full record is itself
    reportable.
  - **Gate 0 now applies to the FULL-RECORD comparison.** Only if *that* fails adequacy or stationarity
    is the outcome INDETERMINATE.

  **⚠️ The test that would have caught this:** every decisive-verdict test fed a synthetic 5-year
  window, bypassing the real `COLOCATED_PAIRS` bounds. **A test must drive the pipeline through the REAL
  registry bounds and prove a decisive verdict is reachable.** A suite that cannot fail on "the
  deliverable is unreachable" is not testing the deliverable.

- **D12 — Stationarity is checked on PYRAMID, not DHM.** D5 said "compare disjoint periods (pre-2020 vs
  2020+)" without naming the network, and the implementation applied it to DHM — which **has no pre-2020
  data at all** (it starts 2020), so the check was vacuous and a genuine Pyramid phase shift would pass
  unnoticed, licensing an invalid non-contemporaneous comparison.
  ⇒ **The pre-2020 vs 2020+ split is PYRAMID's** (1994/2002/2005–2023). A DHM 2020–2022 vs 2023–2025
  split may be reported as *additional* evidence but **never as a substitute**.

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

## Fixer round 2026-08-18 — post-commit diff review (4 blockers + 5 majors resolved)

An independent Codex diff-review pass over the committed implementation (`4d71186`) found the library
did not actually implement several of the design decisions above, despite being marked CODE COMPLETE.
All resolved in place; **test-soundness proved for every correctness fix** (each locking test confirmed
RED against the pre-fix commit via `git stash`, then restored):

- **D2 UTC->NPT reconciliation was dead configuration (blocker).** `coloc_dhm_utc_to_npt_hour_offset`
  was declared in `params.py` but never applied — DHM's UTC hour-of-day and Pyramid's NPT hour-of-day
  were compared as if on the same clock, in both the D3 pairing join and every D9 gate. Fixed:
  `stats_coloc.dhm_utc_to_npt` shifts every DHM timestamp by the declared offset AND re-applies the
  JJAS filter on the shifted timestamp (a UTC-JJAS hour can cross a month boundary once shifted into
  NPT), applied once in `coloc_adjudication.adjudicate_station` before anything else touches DHM data.
- **D5's disjoint-period stationarity split was structurally broken (blocker).** The real DHM source
  record spans 2020-01-01 -> 2025-12-31 in its entirety (`docs/design/dhm-precipitation-vision.md:20`)
  — a `pre-2020` partition against a `2020` split year is ALWAYS empty, so `peak_hour` raised
  `NoProfileRowsError` before any real adjudication completed; the integration test hid this by
  fabricating a 2019 DHM year that cannot exist in the real record. Fixed: the default split moved to
  2023 (splitting the real 2020-2025 span into two non-empty halves); `StationVerdictInputs` gained
  `disjoint_period_data_sufficient`, and gate 0 maps insufficient data to `INDETERMINATE`
  (`ADEQUACY_INSUFFICIENT_DISJOINT_DATA`) rather than crashing, for any station whose own record still
  doesn't straddle the split.
- **The D5 bootstrap peak-hour spread was computed but never gated on (blocker).** A five-season sample
  with a wide (e.g. 12h) circular peak-hour spread could still receive `H1_SUPPORTED`/`H1_REFUTED`.
  Fixed: `StationVerdictInputs` gained `bootstrap_spread_hours`; gate 0 now rejects a spread above
  `coloc_bootstrap_adequate_max_spread_hours` (`ADEQUACY_BOOTSTRAP_SPREAD_TOO_WIDE`) before the
  matched-resolution gate ever runs.
- **D9's phase peaks were computed independently per side, before D3 pairing (major).** The threshold
  ladder and the Pyramid peak were each computed from that side's OWN retained population, then paired
  ONLY for the wet-hour fraction — contrary to D3's own fix, letting hour-dependent missingness
  manufacture a phase difference the gates would then "see". Fixed: `adjudicate_station` pairs FIRST
  (`common_retained_frame`), then derives the ladder and the Pyramid peak from the SAME paired
  population; the standalone full-record profile remains separate (D5b's climatological check).
- **`synthesize_verdict` accepted a duplicated or unregistered station (major).** One station's verdict
  supplied twice (or a single station, or a station outside `coloc_pairs.COLOCATED_PAIRS`) could
  synthesize a decisive Group A verdict without the second required station's evidence. Fixed:
  `synthesize_verdict` now requires EXACTLY the two registered stations, each exactly once
  (`DuplicateStationVerdictError`, `UnregisteredStationVerdictError`).
- **The Pyramid loader had no D4 physical-range boundary (major).** Timestamps were not type-validated,
  NaN/infinite/out-of-range precipitation survived into `retained`, and duplicate timestamps were
  unchecked. Fixed: `load_pyramid_lvl1_csv` now returns a `PyramidLoadResult` (retained frame + `n_raw`/
  `n_nonfinite`/`n_out_of_range`/`n_retained`), validates the timestamp dtype and uniqueness, and
  excludes non-finite/out-of-range `value_mm` using the SAME D4 bounds DHM's own mask uses.
- **The library could not produce the plan's Exit deliverables (blocker) — no runner existed.** Fixed:
  `scripts/dhm_precip/coloc_run.py` composes both pairs, both windows, full profile tables (not just
  peak-hour scalars), the exact two-station synthesis, and a Markdown report writer. Its
  loader-agnostic core (`run_coloc_adjudication`) is exercised end-to-end against synthetic fixtures;
  `main()` wires the real production DHM ingest+mask pipeline and the real Pyramid loader, but — honest
  residual risk — has not been run against real data, since neither the pinned DHM workbook nor the
  real Pyramid Lvl1 CSVs have been present in any workspace to date. See
  `docs/design/dhm-precipitation-milestones.md`'s M-A10 entry for the up-to-date status.
- **Minors also resolved:** an all-zero/non-finite grand mean now raises `NonPositiveGrandMeanError`
  instead of silently dividing by zero (`stats_coloc.normalised_diurnal_profile`); the threshold ladder
  validation now rejects duplicate rungs and requires the first rung to be exactly `0.0`
  (`params.py`).
