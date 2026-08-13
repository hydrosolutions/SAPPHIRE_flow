---
status: DRAFT
created: 2026-08-12
revised: 2026-08-12
plan: 158
title: M-A1 — reproducible DHM precipitation ingest and baseline statistics
scope: A committed, parameterised Polars pipeline that reads the DHM precipitation sample, emits every statistic the vision's Findings quote, and a gate that asserts the runner's own artefacts against an a-priori expectation manifest. Explicitly NOT QC masking or any exclusion mask (M-A3), time-axis normalisation (M-A2), or anything ERA5-Land (M-A4/A5).
depends_on: []
blocks: [M-A2, M-A3]
source: docs/design/dhm-precipitation-milestones.md
---

# Plan 158 — M-A1 reproducible ingest and baseline

## Status

**DRAFT — reconstructed 2026-08-12** after a `/plan` run escalated (stalled at 3 rounds, 2 blockers +
9 majors, doc grown 190 → 428 lines). Both blockers traced to the original draft, not to the
expansion, and are fixed here (D6, D8c). The loop's genuinely good structure — named views, explicit
grains, `AxisStatus`, an a-priori expectation manifest — is kept; its bulk is not.

Implements milestone **M-A1** (`dhm-precipitation-milestones.md:111-119`). First milestone of the
Track A spine.

## Problem

Every number in `dhm-precipitation-vision.md` § Findings came from throwaway exploratory analysis.
The vision says so and bars external citation until M-A1 lands (`:25-28`). Two of those numbers have
already been withdrawn or corrected under review (`:96-98`, `:112-114`) — the failure mode an
uncommitted analysis invites. M-A1 replaces it with a committed pipeline gated against the documented
values.

## What changed since the DRAFT

**M-D2 landed** — station coordinates and elevations for all 26 live stations. Verified: exact match
to the live station set, all inside Nepal, no duplicate locations. Two consequences for this plan:

1. **The vision's spatial-coherence Finding is now known to be misleading**, not merely uncertain. Its
   `r = 0.05` hourly / `0.28` daily were computed across all pairs out to 497 km. Distance-stratified:
   hourly `0.243` and daily `0.463` within 25 km, decaying to `0.032` / `0.165` beyond 200 km. The
   binding constraint is sparsity — **median nearest-neighbour distance 27 km, only 7 of 323 pairs
   inside 25 km**. Reproducing the undistanced number and immediately retracting it would be waste, so
   **Task 2f computes both** and Phase 4 corrects the vision.
2. **The reporting-precision / altitude confound is total.** Group A (0.01 mm) spans 2,490–3,700 m,
   Group B (0.2 mm) spans 67–2,147 m — **zero overlap, 343 m gap** (Task 2a). It converts a caveat
   into a measured fact, and tells M-A8 in advance that its exit is "the sample cannot separate them".

**Deliberately NOT added here:** ERA5-Land grid-cell assignment and same-cell pair analysis. Two
stations (Kirtipur/Khumaltar, 4.3 km apart, 30 m elevation difference) share a 0.1° cell and their
~37 % seasonal-total disagreement is an empirical floor on representativeness error — but that is
ERA5-Land work, explicitly out of scope here, and belongs to **M-A5/M-A6**. Recorded in the milestone
doc rather than smuggled into this plan.

**D12 — coordinate input contract.** **Task 1a loads and validates** a committed
`scripts/dhm_precip/station_coordinates.csv` (26 rows: station, excel_col, lat, lon, elev), provenance
recorded as delivered 2026-08-12, schema-validated and asserted to match the live station set exactly.
*Contingency:* if M-D1 forbids committing it, it moves to the `DHM_PRECIP_XLSX` pattern — an env-var
path, sha256-pinned. **Geometry results are a distinct expectation class** (`source = plan-158`, not
`vision-findings`) because the vision does not yet quote them; D8's vision-only rule is unchanged for
Findings expectations.

## Constraints

1. **Source lives outside the repo** (~2 MB, OneDrive), and M-D1 (authorisation) is open. Path via
   `DHM_PRECIP_XLSX`, sha256-verified. **The gate must not fail open**: the *only* skip condition is
   `DHM_PRECIP_XLSX` unset, applied by the integration test alone. Library and runner contain no
   pytest semantics — a missing path, digest mismatch, schema mismatch or parse failure is an error,
   never a skip. CI stays green because all statistic logic is unit-tested on synthetic frames.
2. **Polars, not pandas** — 33 source modules import polars against 3 for pandas.
3. **No Excel reader *or* writer is a dependency** (`pyproject.toml`). Task 1a adds both: a read
   engine, and a dev-only writer for synthetic workbook fixtures.
4. **The time axis is unresolved** (M-D3/M-A2, `dhm-precipitation-vision.md:56-57`). Every result row
   carries an `AxisStatus` (D7).
5. **`scripts/` idiom** — `scripts/audit_distribution_shift.py`: shebang, `# ruff: noqa: T201`, a
   docstring stating *why*, `Usage:`/`Environment:` sections, explicit exit codes. That docstring is
   the single source of truth for the invocation contract.
6. **`scripts/` is outside the default type gate** — `pyrightconfig.json` includes only `src`, and the
   pre-push ratchet runs `pyright src/`. Strict mode still applies to explicitly-named files, so D10
   names the package in every task's gate.

## Design decisions

- **D1 — `scripts/dhm_precip/`**, a small package. `scripts/` already hosts analysis and probe code.
  Not `src/sapphire_flow/tools/` (operational utilities), not `notebooks/` (ruff-excluded).
- **D2 — One frozen parameter object.** Every threshold, quantile grid, season, sentinel value and
  detection setting, defaults matching the vision's analysis. No magic numbers in statistic functions.
- **D3 — Canonical long frame at the boundary.** 38 columns (`Time (UTC)` + 37 stations) parsed once
  into `(source_row_index, station_name, timestamp, value_mm)`, station identity a `NewType`.
  `source_row_index` survives the melt so row-grain statistics need no re-read. The loader asserts the
  exact 37-name inventory against a committed expected list **shipped by 1a itself** (not 1c — the
  loader cannot depend on a later task for its own exit gate); the 11 all-empty columns are reported
  present-but-empty, never dropped.
- **D4 — Statistics are pure functions of `(view, params, stations)`.** No I/O, no globals — that is
  what makes them testable without the source file. `view` is always a named view, never "the frame";
  `stations` is the validated coordinate table (D12), loaded once by 1a and passed in. **No statistic
  task reads a file.**
- **D5 — One a-priori tolerance rule: quoted-precision rounding.** The manifest records the precision
  at which the vision states each number; the computed value rounds to that precision and must match
  exactly. Counts are exact. Quoted ranges assert min/max over the named population. **No per-statistic
  bands, and an implementer may not widen a tolerance to make a test pass.**
- **D6 — Two named views; every expectation-bearing statistic is computed on `ON_GRID`.**
  | View | Definition | Use |
  |---|---|---|
  | `RAW` | every delivered cell, unfiltered | **diagnostics only** — time-axis row counts, off-grid minute distribution |
  | `ON_GRID` | `RAW` restricted to minute == 0 — the "on-the-hour subset" the vision states its statistics use (`:28`) | **every statistic the vision quotes**, including defects, runs and extremes |

  *(Fixes review blocker 1: the escalated draft computed defects and extremes on `RAW` while claiming
  the vision's basis.)* **`RAW` may satisfy only expectations the manifest explicitly enumerates as
  `RAW_AXIS_DIAGNOSTIC`** — the vision does quote raw-axis Findings (55,379 rows, 3,350 off-grid,
  `dhm-precipitation-vision.md:51-54`), so a blanket ban would be unsatisfiable. Every other
  expectation is `ON_GRID`, and the manifest test rejects a non-diagnostic expectation declaring `RAW`.

  **M-A1 builds no exclusion mask.** A defect-filtered third view is a defect-derived timestamp mask
  however it is labelled; it belongs to M-A3, which sits behind M-A2 because run detection is
  undefined on an unnormalised axis. Task 2d *inventories* candidate defects; nothing consumes that
  inventory as a filter. **Accepted consequence:** some quoted intensity numbers will not reproduce
  unmasked — Sindhuli Madhi is pinned at ~72 mm/h for 120 hours, so its unmasked q99.9 plausibly lands
  outside the quoted 13–33 mm/h band. That is a Phase-4 outcome (iv), evidenced by the 2d inventory:
  **M-A3 supplies the QC mask, but the reproducible successor is M-A7** (consistent with D11).
- **D6b — Three grains, named everywhere.** `source_timestamp_rows` (55,379) · `station_timestamp_cells`
  (~2.05 M) · `non_null_observations` (~1.04 M). Every expectation declares its grain.
  *Settled during reconstruction:* off-grid rows are **6.0 % at row grain but 0.64 % at observation
  grain** (3,350/55,379 vs 6,633/1,039,330) — off-grid rows are ~10× sparser. Both the vision's
  numbers are correct; the manifest names the denominator. No Phase-4 item.
- **D7 — `AxisStatus` enum on every result row** (a column, not a table attribute):
  `AXIS_INDEPENDENT` — genuinely invariant outputs only: the workbook column inventory, the
  empty-column list, and coordinate-only geometry (pairwise distances, nearest neighbours,
  elevation-overlap). **Sentinel counts and reporting resolution are `RAW_PROVISIONAL`, not
  `AXIS_INDEPENDENT`**: they are computed after the `ON_GRID` timestamp selection, and M-A2 may move
  formerly off-grid observations into that population. `RAW_AXIS_DIAGNOSTIC` (the raw axis is the
  subject), `RAW_PROVISIONAL` (those two, plus everything time-bucketed — wet-hour fractions, intensity quantiles, run durations, daily/monthly aggregates,
  coherence, diurnal). Candidate-run durations additionally carry `pre_normalisation_candidate`.
- **D8 — The expectation manifest is authored before the code it gates.** Committed data, not code
  (`scripts/dhm_precip/expectations.toml`), enumerating every statistic with `id`, a **source-specific
  provenance field** — `vision_ref` (`file:line`) when `source = vision-findings`, `plan_ref` when
  `source = plan-158` (the D12 geometry class) — `statistic`, `value`/`range`, `unit`, `view`, `grain`, `axis_status`,
  `population`, `quoted_precision`, and a `method` table (D8b). **`vision-findings` entries derive
  solely from the vision's Findings** — no statistic is invented to be reproduced; the `plan-158`
  geometry class (D12) is the sole exception and is enumerated there. Lands in Phase 1, before any
  statistic is implemented.
- **D8b — The `method` table pins choices that silently move a quoted digit.** Mandatory where
  applicable, rejected by the manifest test if omitted: `quantile_definition`, `zero_policy`,
  `wet_threshold_side` (`>` vs `>=` at 0.2 mm/h), `modal_binning`, `pairwise_missing_policy`,
  `min_paired_samples`, `bucket_alignment`, `daily_completeness`, `period_completeness` (no rescaling
  of incomplete totals), `year_attribution` (which year a DJF December belongs to), `denominator`.
  **For candidate runs additionally:** `minimum_run_duration`, `run_predicate` (the value test that
  opens a run), `stuck_value_tolerance`, `ordering_basis`, `adjacency_rule`, `gap_treatment`,
  `missing_value_bridging`, `season_boundary`, `merge_distance` — runs are undefined on an
  unnormalised axis, so every rule must be declared rather than assumed
  (`dhm-precipitation-milestones.md:147-150`). These are *our declared
  choices*; the original exploratory ones are unrecoverable, so a mismatch means either our method
  differs or a number is wrong, and Phase 4 must say which.
- **D8c — Expectations carry a terminal disposition, so the gate can go green.**
  `active` (must match) · `corrected` (vision edited; the **new value is asserted numerically too**,
  with the original retained plus correction provenance) · `withdrawn_unreproducible` (**the only
  disposition exempt from numeric matching**; requires a complete Phase-4 record: original value,
  method comparison, evidence, successor milestone). The gate asserts `active` **and** `corrected`
  values under D5, and requires a complete record for every `withdrawn_unreproducible` one. *(Fixes review blocker 2: the escalated draft asserted all expectations
  unconditionally while the exit permitted classified discrepancies to survive — leaving the gate
  permanently red.)*
- **D9 — One runner; the gate reads back what the runner wrote.** The runner loads, builds views,
  computes every family, writes parquet + a Markdown summary, and serialises a `RunManifest`
  (`results.json`) with run id, source path, sha256, parameter snapshot, the three D6b counts per
  view, the table inventory, and every value keyed by expectation `id`. Each table declares the set of
  `(view, axis_status)` pairs it contains; the manifest test asserts declared set == observed set.
  **The runner exits 0 whenever ingestion, computation and serialisation succeed — expectation
  evaluation is the gate's job, not the CLI's.** Exit codes: `0` success · `2` path unset/unreadable ·
  `3` sha256 mismatch · `4` schema mismatch · `5` parse failure.
  **The gate reopens every declared parquet file** and validates schema, `(view, axis_status)` rows,
  counts and expectation-bearing values against `results.json` — so it cannot pass while the artefacts
  are wrong.
- **D9b — A typed digest-injection seam for tests.** The loader accepts an internal
  `expected_sha256` argument; the CLI always supplies the pinned production digest. Tests compute and
  inject their fixture's digest to reach the schema and parse paths, which digest pinning would
  otherwise make unreachable. **Not exposed via CLI or environment.**
- **D10 — One shared task exit gate**, referenced by every code task (`docs/workflow.md:376`): the
  task's own test, plus `uv run ruff check src/ scripts/ tests/`, `uv run ruff format --check`
  (same paths), `uv run pyright src/`, `uv run pyright scripts/dhm_precip/`, `uv run pytest`, and
  affected documentation updated in the same change.
- **D11 — The citation ban lifts per family, against that family's own successor.** M-A1 lifts it for
  `AXIS_INDEPENDENT` and `RAW_AXIS_DIAGNOSTIC` results only. **`successor` is a mandatory,
  schema-tested manifest field on every `RAW_PROVISIONAL` entry** (1c), with no family left unmapped:

  | Provisional family | Successor |
  |---|---|
  | Candidate runs (zero, stuck-high) | **M-A3** — rebuilt on the normalised axis |
  | Intensity distributions, diurnal profiles, coherence | **M-A7** (M-A2 is its prerequisite) |
  | Coverage, simultaneity, missingness, climatology, aggregates | **M-A2** — axis normalisation |
  | Reporting resolution, non-run defect counts | **M-A2** — the `ON_GRID` population itself moves |

  A provisional label is not a licence to cite. **Task 4b must also correct
  `dhm-precipitation-milestones.md:118-119`, which still says the whole ban ends with M-A1.**

## Out of scope

QC masking, flagging or any exclusion mask (M-A3) · time-axis normalisation (M-A2) · calling
`Stage1QualityChecker` (M-A3, per OD-3) · ERA5-Land in any form (M-A4/A5) · any correction to the
data · packaging or publishing a dataset (M-I2).

## Phases and tasks

Every code task carries the D10 gate in addition to the test named below.

### Phase 1 — foundation

**1a — dependencies and loaders.** Add a read engine and a dev-only Excel writer; sha256-verified
loading from `DHM_PRECIP_XLSX` with the D9b seam; parse to the canonical long frame (D3); expose the
station inventory including empty columns; ship the expected 37-name list. **Also owns the coordinate
table (D12): loads and schema-validates it, asserts it matches the live station set, and hands a
validated `stations` structure to Phase 2 — no statistic task performs I/O (D4).**
*Verification:* `uv run pytest tests/unit/scripts/test_dhm_precip_loader.py`

**1b — types and parameters.** The frozen parameter object (D2), `AxisStatus` / view / grain enums
(D6, D6b, D7), and the `RunManifest` type (D9).
*Verification:* `uv run pytest tests/unit/scripts/test_dhm_precip_types.py`

**1c — expectation manifest.** *(depends on 1b — it validates against 1b's enums.)* Author
`expectations.toml` (D8) with method tables (D8b), dispositions (D8c) and per-family successors (D11).
A schema test rejects any entry missing a required `method` key, any non-diagnostic entry declaring
`RAW` (D6), and **any `RAW_PROVISIONAL` entry missing a `successor` milestone (D11)**. *(The expected 37-station list ships with 1a, not here.)*
*Verification:* `uv run pytest tests/unit/scripts/test_dhm_precip_expectations.py`

### Phase 2 — statistic families (parallel)

Pure functions per D4, unit-tested on synthetic frames, each emitting a typed result table with `view`
and `axis_status` columns.

- **2a — inventory and coverage.** Usable vs empty stations, per-station span and coverage, hourly
  reporting simultaneity, and the **Group A/B elevation-overlap statistic** (D12).
- **2b — time-axis diagnostics.** Row counts vs expected slots, off-grid rows and minute distribution,
  duplicates, monotonicity, per-station off-grid attribution. `RAW`/`RAW_AXIS_DIAGNOSTIC` only.
- **2c — reporting precision and intensity.** Inferred resolution per station (`RAW_PROVISIONAL`, D7),
  wet-hour fraction, intensity quantiles, sub-threshold mass fraction, scale-normalised shape ratios,
  leave-one-out tail-prediction error. **Must not use Pearson correlation between quantile vectors**
  (vision rule 2) — a regression test asserts the transferability statistic separates an exponential
  from a Pareto sample.
- **2d — candidate defect inventory.** Sentinels, stuck-high runs, zero runs under the D8b run
  contract, extremes. Inventory only; nothing consumes it as a filter.
- **2e — climatology and missingness.** Monthly climatology normalised for coverage, DJF share,
  per-year totals under `period_completeness`, and the wet-biased-missingness ratio **with the tested
  station excluded** from the regional wet indicator.
- **2f — coherence, diurnal and geometry.** *(Single owner of all coherence computation — see D12.)*
  Consumes 1a's validated `stations` (no I/O, D4); pairwise great-circle distances and
  nearest-neighbour distribution (**`AXIS_INDEPENDENT`** — coordinate-only, unaffected by M-A2);
  inter-station correlation at hourly/3-hourly/daily **both undistanced (reproducing the vision) and
  distance-stratified (superseding it)**; diurnal profiles and interannual stability — these
  **`RAW_PROVISIONAL`**. No ERA5-Land grid assignment (out of scope, D12).
*Verification:* one test module per task, e.g.
`uv run pytest tests/unit/scripts/test_dhm_precip_coherence.py`

### Phase 3 — runner and evaluator (sequential)

**3a — runner and artefacts.** The `scripts/dhm_precip/run.py` entry point per constraint 5 and D9:
parquet tables, Markdown summary, `results.json`, explicit exit codes, exit 0 on successful
computation.
*Verification:* `DHM_PRECIP_XLSX=… uv run python scripts/dhm_precip/run.py --out <tmp>` exits 0 and
writes the declared artefacts.

**3b — expectation evaluator, report mode.** *(Fixes review blocker 2: the escalated draft made 3b an
asserting gate that had to pass before Phase 4 — but Phase 4 is what resolves the discrepancies 3b
would fail on. Circular, and it violated the per-task passing gate at `docs/workflow.md:376-384`.)*
Build the evaluator: invoke the runner into a temp directory, reopen every declared parquet (D9),
compare each expectation under D5, and **emit a discrepancy report**. In report mode it exits 0
whenever the machinery works — a mismatch is data, not a failure. Its own tests run against
**synthetic** expectations and artefacts, so they are green independently of the real file.
*Verification:* `uv run pytest tests/unit/scripts/test_dhm_precip_evaluator.py`

### Phase 4 — reconcile, then gate

**4a — resolve discrepancies.** Run 3b's evaluator against the real file to produce the report, then
classify each mismatch: (i) pipeline wrong → fix; (ii) vision wrong → correct it, disposition
`corrected`; (iii) methods differ → reconcile via the D8b table; (iv) original methodology
unrecoverable → `withdrawn_unreproducible` with a complete record. **A mismatch may not be classified
(iii) or (iv) until method definitions have been compared.** Known incoming: the spatial-coherence
restatement and the confound result from "What changed".
*Verification:* every entry in the report is classified and dispositioned.

**4b — documentation.** Update the vision's Findings for every `corrected`/`withdrawn` expectation;
correct `dhm-precipitation-milestones.md:118-119`, which still says the whole citation ban ends with
M-A1 (D11); record M-A1 complete. Do not restate the runner's invocation contract (constraint 5).
*Verification:* affected docs updated in the same change (D10).

**4c — final asserting gate.** *(This is the M-A1 exit, and the only asserting test.)* The integration
test asserts `active` and `corrected` expectations under D5 and requires a complete record for every
`withdrawn_unreproducible` one (D8c). Skips only when `DHM_PRECIP_XLSX` is unset.
*Verification:* `DHM_PRECIP_XLSX=… uv run pytest tests/integration/test_dhm_precip_reproduction.py`

## Exit gate for M-A1

4c green: every `active` and `corrected` expectation matches under D5, and every
`withdrawn_unreproducible` one carries a complete Phase-4 record. The vision's external-citation ban then lifts for `AXIS_INDEPENDENT` and
`RAW_AXIS_DIAGNOSTIC` results only — each `RAW_PROVISIONAL` family stays barred until its own D11
successor lands.

## Risks

- **The reproduction gate is a local gate.** CI cannot run it without the source file. Stated plainly
  rather than papered over; unit coverage of the logic is CI's share.
- **Several vision numbers are expected not to reproduce.** Two have already been corrected, and D6
  predicts more under unmasked computation. Phase 4 exists because this is the normal case.
- **`RAW_PROVISIONAL` results must not leak into any conclusion** before their D11 successor (M-A2,
  M-A3 or M-A7 depending on family).
- **A `/plan` run already escalated on this document.** If a review round again fails to converge,
  reconstruct rather than grind — this revision is itself that remedy.

```json
{
  "phases": [
    {"id": "phase-1a", "tasks": ["1a", "1b"], "parallel": true},
    {"id": "phase-1b", "tasks": ["1c"], "parallel": false, "depends_on": ["phase-1a"]},
    {"id": "phase-2", "tasks": ["2a","2b","2c","2d","2e","2f"], "parallel": true,
     "depends_on": ["phase-1b"]},
    {"id": "phase-3a", "tasks": ["3a"], "parallel": false, "depends_on": ["phase-2"]},
    {"id": "phase-3b", "tasks": ["3b"], "parallel": false, "depends_on": ["phase-3a"]},
    {"id": "phase-4a", "tasks": ["4a"], "parallel": false, "depends_on": ["phase-3b"]},
    {"id": "phase-4b", "tasks": ["4b"], "parallel": false, "depends_on": ["phase-4a"]},
    {"id": "phase-4c", "tasks": ["4c"], "parallel": false, "depends_on": ["phase-4b"]}
  ]
}
```
