---
status: DRAFT
created: 2026-09-04
plan: 238
title: M-A11d — make every number in § M-A11c reproducible from tracked code
scope: Freeze one convention, report only null-relative increments, attach uncertainty, and assert the document's figures against the tracked module's output. NOT a new measurement, NOT a new statistic, NOT a re-litigation of the bimodality or bias-correction lines.
depends_on: [216, 220]
source: docs/design/dhm-precipitation-m-a11-tigge-ifs-screening.md § M-A11c; Codex method review 2026-09-03; PR #249
---

# Plan 238 — M-A11d reproducible event-timing numbers

## Status

**DRAFT.** Not for implementation until the owner confirms.

## ⛔ PROPORTIONALITY IS BINDING

**One module already exists and is tested** (`scripts/dhm_precip/ifs_event_timing.py`, PR #249,
10 tests, pyright-strict clean). This plan **removes** numbers and **binds** the rest to code.
⛔ No new framework, no new statistic, no new data. **Adding length is a cost.**

## ⛔ PER-RUN SCOPE (binding)

- ⛔ **No network, no retrieval.** Every input is on disk.
- ⛔ Never write under `data/dhm_precip/era5_land*`, `imerg_early/points/`, or any `points/` tree.
- ⛔ **Do not re-open** the diurnal-bimodality line (stopped by the owner 2026-09-03) or the
  bias-correction design (WRONG-SHAPE, not built).
- **Hold at PR.** Patch bump folded into the code commit; docs in a separate unbumped commit; stage
  by explicit path, never `git add -A`.
- ⚠️ **Verify every code reference against source.** This track has shipped wrong citations and a
  constant that real data rejected.

## Why this plan exists

§ M-A11c currently carries **two sets of numbers** — a reviewer's recomputation and the tracked
module's — and **they disagree on two of four rows**. Neither can be declared right: the reviewer's
recomputation is **not reproducible from the record**, and the module's absolute rates are
**convention-dependent**. Nothing asserts the document against any code, so the figures can drift
from the module the moment either changes.

The document also still shows an **absolute missed rate**, which measurement showed moves from 0.248
to 0.216 on a **0.05 change in an arbitrary threshold**.

## Decisions

- **D1 — Report ONLY null-relative increments. ⛔ No absolute rates.** Measured: the miss threshold
  moving 0.50 → 0.45 shifts the absolute rate 0.248 → 0.216, but the **observed−null increment barely
  moves** (−0.152 → −0.145). The same held for the skill increment across four search windows.
  ⇒ **An increment over the stated null is the reportable quantity; an absolute rate is not.**
  This is the plan's central rule and every other decision follows it.

- **D2 — The document's numbers are GENERATED, never transcribed.** The module emits the figures; the
  document carries the module's output. ⇒ A test asserts the numbers in § M-A11c **equal** the
  module's output for the frozen defaults, and **fails** if either drifts. ⚠️ This is the
  assert-before-render pattern that already works on this track (the IFS figure asserts 54/54 cells
  before drawing). ⛔ Do not invent a doc-generation framework — an assertion over a small parsed
  table is sufficient.

- **D3 — ONE frozen convention, stated in the module.** Search window, event quantile, decluster
  interval, miss fraction, lead set, init hour, null shifts and amplitude gate become **documented
  frozen defaults**. ⛔ Changing one is a reviewed commit, not a CLI habit. The module already exposes
  them as flags; this pins the published values.

- **D4 — Attach uncertainty or state that there is none.** 🔴 The independent review established the
  **±3 h figure is a sampling interval, NOT a statistical uncertainty bound**, and that no cell is
  "resolved from zero" without a bootstrap — which has never been run. ⇒ Add a **season-block
  bootstrap** (resample the 6 JJAS seasons with replacement) around each reported increment and
  publish an interval. ⛔ Six blocks is few; if the interval is uninformative, **say so** rather than
  substituting a per-window bootstrap that ignores autocorrelation.

- **D5 — Remove the non-reproducible figures from the document.** The reviewer's recomputation cannot
  be regenerated from anything in the repo. ⇒ Keep a short historical note that an independent review
  produced different absolute rates and that this is **why absolutes are no longer reported**;
  ⛔ do not keep the numbers themselves, which would only invite mis-citation.

- **D6 — Nothing here changes M-A11/M-A11b's published offsets.** Their two recorded limits (the
  `[−18,+6)` branch is not rotation-equivariant; the ±3 h figure is not an uncertainty bound) stay as
  written. ⛔ This plan does not re-estimate the climatological phase.

## Tasks

### T1 — freeze the convention and report increments only (depends: nothing)
**In:** D1, D3 — pin the published defaults in the module; change the report to emit
**observed, null, and increment** for each statistic, with the absolute rates marked non-reportable.
**Out:** any new statistic; any change to the estimator itself.
**Verify:** the increment for each statistic moves by less than its bootstrap interval when the miss
fraction is perturbed 0.50 → 0.45, while the absolute rate moves measurably. ⛔ Prove it by running
both.

### T2 — season-block bootstrap (depends: T1)
**In:** D4 — resample the six JJAS seasons with replacement around each reported increment.
**Out:** any other resampling scheme; any per-window bootstrap.
**Verify:** the interval is reported for every published increment, and the module states the block
unit and the number of blocks (6) beside it. ⛔ If an interval spans zero, that must be printed, not
smoothed.

### T3 — bind the document to the module (depends: T1, T2)
**In:** D2, D5 — regenerate § M-A11c from the module's output; delete the non-reproducible figures,
keeping the historical note; add the test that asserts the document's table equals the module's output.
**Out:** any change to M-A11/M-A11b's own sections beyond what D6 already records.
**Verify:** editing either the document's table or a module default **fails the test**. ⛔ Prove each
direction by reverting.

```json
{
  "phases": [
    {"id": "phase-1", "tasks": ["T1"], "parallel": false},
    {"id": "phase-2", "tasks": ["T2"], "parallel": false, "depends_on": ["phase-1"]},
    {"id": "phase-3", "tasks": ["T3"], "parallel": false, "depends_on": ["phase-2"]}
  ]
}
```

## Exit

Every number in § M-A11c is produced by `scripts/dhm_precip/ifs_event_timing.py` under a frozen,
documented convention, carries a season-block interval, and is **asserted by a test** that fails if
document or code drifts. ⛔ No absolute rate is published; only increments over a stated null.

## Non-goals

Re-estimating the climatological phase (D6) · the diurnal-bimodality line (owner-stopped) · the
IMERG bias-correction design (WRONG-SHAPE) · any new data or retrieval · a doc-generation framework ·
basin-average verification, which needs gateway data we cannot access at point level.
