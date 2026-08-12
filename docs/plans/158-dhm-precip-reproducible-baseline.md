---
status: DRAFT
created: 2026-08-12
plan: 158
title: M-A1 — reproducible DHM precipitation ingest and baseline statistics
scope: A committed, parameterised pipeline that reads the DHM precipitation sample and emits every statistic quoted in the vision's Findings, plus a gate asserting the emitted values against those documented numbers. Foundation for the whole Track A spine. Explicitly NOT QC masking (M-A3), time-axis normalisation (M-A2), or anything ERA5-Land (M-A4/A5).
depends_on: []
blocks: [M-A2, M-A3]
source: docs/design/dhm-precipitation-milestones.md
---

# Plan 158 — M-A1 reproducible ingest and baseline

## Status
**DRAFT.** Implements milestone **M-A1** of `docs/design/dhm-precipitation-milestones.md`.
First milestone of the Track A spine; unblocked.

## Problem

Every number in `docs/design/dhm-precipitation-vision.md` § Findings was produced by throwaway
exploratory analysis. The vision says so explicitly, and states that **until M-A1 lands, no number in
it may be cited externally**. Two of those numbers have already been withdrawn or corrected after
review, which is exactly the failure mode an uncommitted analysis invites.

M-A1 replaces the exploration with a committed, parameterised pipeline, and gates it by asserting the
emitted values against the documented ones. Where they disagree, either the pipeline is wrong or the
vision is — and both outcomes are acceptable exits, provided the discrepancy is resolved explicitly.

## Constraints that shape the design

1. **The source file lives outside the repo** (OneDrive), is ~2 MB, and may not be committable —
   M-D1 (authorisation) is still open. So: source path via environment variable, sha256 verified on
   read, real-data tests **skipped when absent**, and all logic unit-tested against synthetic frames.
   CI must stay green without the file.
2. **Polars, not pandas** — 33 modules import polars against 3 for pandas.
3. **No Excel reader is currently a dependency.** `polars.read_excel` needs an engine; add one.
4. **The time axis is unresolved** (M-D3/M-A2). Any sub-daily statistic computed here is on the *raw*
   axis and must be **labelled provisional**; M-A7 recomputes it after normalisation. M-A1 reproduces
   the vision's diurnal numbers to verify the pipeline, not to settle the science.
5. **`scripts/` idiom** — see `scripts/audit_distribution_shift.py`: `#!/usr/bin/env python3`,
   `# ruff: noqa: T201`, a docstring stating *why* the script exists, `Usage:` and `Environment:`
   sections, explicit exit codes.

## Design decisions

- **D1 — Location: `scripts/dhm_precip/`.** A small package, not one file: six statistic families plus
  a loader is too much for a single module, and `scripts/` already hosts analysis and probe code
  (`audit_distribution_shift.py`, `recap_probe_loop.py`, `check_readiness.py`). **Not**
  `src/sapphire_flow/tools/` — that is operational utility code — and **not** `notebooks/`.
- **D2 — One frozen parameter object.** Every threshold, quantile grid, season definition, sentinel
  value and run-detection setting lives in a single frozen dataclass with defaults matching the values
  the vision used. No magic numbers inside statistic functions. Per CLAUDE.md type-driven design.
- **D3 — Canonical long frame at the boundary.** The wide 37-column sheet is parsed once into a long
  frame `(station_name, timestamp, value_mm)` with station identity as a `NewType`. Statistic
  functions never touch the workbook.
- **D4 — Statistics are pure functions of (frame, params).** No I/O, no globals. That is what makes
  them unit-testable on synthetic data without the source file.
- **D5 — The reproduction gate asserts against the vision, with tolerances stated per statistic.**
  Integer counts exact; percentages and correlations banded. A failure is a finding, not a bug —
  the exit allows correcting the vision instead of the pipeline.
- **D6 — Emit tables as artefacts, not prints.** Parquet plus a rendered Markdown summary, written to
  a local output directory. Nothing is written into a synced folder (milestone M-I2 storage rule).

## Out of scope

QC masking or flagging (M-A3) · time-axis normalisation (M-A2) · calling `Stage1QualityChecker`
(M-A3, per OD-3) · ERA5-Land in any form (M-A4/A5) · any correction to the data · packaging or
publishing a dataset (M-I2).

## Phases and tasks

### Phase 1 — foundation

**Task 1a — dependency and source loader**
*Scope:* add an Excel engine dependency (`uv add fastexcel` for the calamine engine, or justify
another); implement sha256-verified loading of the workbook from `DHM_PRECIP_XLSX`; parse the wide
sheet into the canonical long frame (D3); expose the station inventory including empty columns.
*Out:* any statistic.
*Verification:* `uv run pytest tests/unit/scripts/test_dhm_precip_loader.py`

**Task 1b — parameter object**
*Scope:* the frozen parameter dataclass (D2) with defaults matching the vision's analysis: wet
threshold 0.2 mm/h, harmonised-floor flag, quantile grid, JJAS season months, sentinel value
`-9999999.0`, zero-run and stuck-run detection parameters, minimum-coverage rules.
*Out:* using it.
*Verification:* `uv run pytest tests/unit/scripts/test_dhm_precip_params.py`

### Phase 2 — statistic families (parallel)

Each task: pure functions per D4, unit-tested on synthetic frames, emitting a typed result table.

**Task 2a — inventory and coverage.** Usable vs empty stations, per-station first/last/coverage over
its own span, reporting simultaneity per hour.
*Verification:* `uv run pytest tests/unit/scripts/test_dhm_precip_inventory.py`

**Task 2b — time-axis diagnostics.** Row count vs expected hourly slots, off-grid rows and their
minute distribution, duplicates, monotonicity, per-station off-grid attribution.
*Verification:* `uv run pytest tests/unit/scripts/test_dhm_precip_timeaxis.py`

**Task 2c — reporting precision and intensity.** Inferred reporting resolution per station, wet-hour
fraction, intensity quantiles, sub-threshold mass fraction, scale-normalised shape ratios and the
leave-one-out tail-prediction error. **Must not use Pearson correlation between quantile vectors**
(vision rule 2) — a regression test should assert that the transferability statistic distinguishes an
exponential from a Pareto sample.
*Verification:* `uv run pytest tests/unit/scripts/test_dhm_precip_intensity.py`

**Task 2d — defect inventory.** Sentinels, stuck-high runs, zero runs under the parameterised
detection contract, and the extreme-value table.
*Verification:* `uv run pytest tests/unit/scripts/test_dhm_precip_defects.py`

**Task 2e — climatology and missingness.** Monthly climatology normalised for coverage, DJF share,
per-year totals with a coverage rule, and the wet-biased-missingness ratio **with the tested station
excluded from the regional wet indicator** (the endogeneity fix already applied in the vision).
*Verification:* `uv run pytest tests/unit/scripts/test_dhm_precip_climatology.py`

**Task 2f — coherence and diurnal (provisional).** Inter-station correlation at hourly/3-hourly/daily,
diurnal profiles and interannual stability. Every output from this task carries a **provisional** flag
per constraint 4.
*Verification:* `uv run pytest tests/unit/scripts/test_dhm_precip_coherence.py`

### Phase 3 — gate and artefacts

**Task 3a — reproduction gate.** A test module asserting each emitted statistic against the value
documented in the vision, with per-statistic tolerances (D5). Skipped without the source file;
emits a discrepancy report naming every mismatch.
*Verification:* `DHM_PRECIP_XLSX=… uv run pytest tests/integration/test_dhm_precip_reproduction.py`

**Task 3b — runner and artefacts.** The `scripts/dhm_precip/` entry point following the `scripts/`
idiom (constraint 5): writes parquet tables plus a Markdown summary to a local output directory,
with explicit exit codes.
*Verification:* `DHM_PRECIP_XLSX=… uv run python scripts/dhm_precip/run.py --out <tmp>` exits 0 and
writes the expected artefacts.

### Phase 4 — reconcile

**Task 4a — resolve discrepancies.** For each mismatch from 3a: determine whether the pipeline or the
vision is wrong, fix the responsible one, and record the correction in the vision's Findings.
*Verification:* 3a passes with zero unexplained discrepancies; any vision edit is committed alongside.

## Exit gate for M-A1

The reproduction gate passes against the real file, **or** every discrepancy is explained and the
vision corrected. On completion, the vision's "no number may be cited externally" restriction lifts.

## Risks

- **Source unavailable to CI** — mitigated by env var + skip + synthetic unit tests (constraint 1),
  but it means the reproduction gate is a *local* gate. State that plainly rather than pretending CI
  covers it.
- **The vision's numbers may be wrong.** Two have already been corrected. Phase 4 exists because this
  is expected, not exceptional.
- **Sub-daily statistics are provisional** until M-A2. Task 2f's outputs must not leak into any
  conclusion before then.

```json
{
  "phases": [
    {"id": "phase-1", "tasks": ["1a", "1b"], "parallel": true},
    {"id": "phase-2", "tasks": ["2a", "2b", "2c", "2d", "2e", "2f"], "parallel": true,
     "depends_on": ["phase-1"]},
    {"id": "phase-3", "tasks": ["3a", "3b"], "parallel": true, "depends_on": ["phase-2"]},
    {"id": "phase-4", "tasks": ["4a"], "parallel": false, "depends_on": ["phase-3"]}
  ]
}
```
