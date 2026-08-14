---
status: DRAFT
created: 2026-08-14
plan: 172
title: M-A2 + M-I1 — canonical hourly axis, and the precipitation QC rule logic
scope: Two small milestones that jointly unblock M-A3. M-A2 materialises the 568 hours missing entirely from the workbook as explicit null rows, so run detection cannot bridge them, with per-row provenance and a row-identity conservation proof. M-I1 gives `frozen_sensor` the value exclusion that makes it usable for precipitation, plus the rule-version emission that lets a flag record which variant produced it. Explicitly NOT the QC mask itself (M-A3), NOT any ERA5 work (M-A5/A6), NOT operational config binding (M-I4, gated).
depends_on: [170]
blocks: [M-A3]
source: docs/design/dhm-precipitation-milestones.md
---

# Plan 172 — M-A2 canonical hourly axis + M-I1 precipitation QC rules

## Status
**DRAFT.** Implements **M-A2** and **M-I1**. Folded into one plan because **M-A3 depends on both**
and neither is large alone; they touch disjoint code (`scripts/dhm_precip/` and
`src/sapphire_flow/services/qc.py`) and share no state.

## Problem

M-A3 — the fit-for-purpose QC mask — cannot start. It needs two things that do not exist:

1. **A gap-explicit hourly axis.** *(Rationale corrected in review — my first version was wrong.)*
   Station non-reporting is **already** represented: the loader `unpivot`s every workbook row across
   every station **without dropping nulls** (`scripts/dhm_precip/loader.py:223`), so a silent station
   has rows with `value_mm = NULL`, and `stats_defects.py:54` already defines adjacency as exactly
   3,600 s. The frame is not sparse across stations.

   The actual gap is **hours missing from the workbook entirely**: 52,029 delivered on-grid rows
   against 52,597 expected slots — **568 hours where no station has a row at all**. And OD-3 routes
   M-A3 through the production checker, whose `_apply_frozen_sensor` (`services/qc.py:104-110`)
   iterates by **list index**, comparing `group[i]` to `group[i-1]` and never reading a timestamp. It
   would therefore **silently bridge all 568 gaps**, treating the rows either side as consecutive and
   fusing two separate runs into one.
2. **A `frozen_sensor` rule that can ignore zeros.** The same function flags any repeated value. For
   precipitation, long runs of exact zero are *normal dry weather* — as it stands the rule would flag
   every dry spell in the record.

## What M-D3's answers changed (2026-08-13)

**M-A2 got smaller.** The milestone was written to cope with two unknowns that are now answered:

| Was | Now |
|---|---|
| "If the convention is unresolved, emit under a stated assumption with a ±1 h phase-uncertainty flag carried downstream" | **Resolved: period-ending** (16:00 UTC = 15:00→16:00). No assumption, no uncertainty flag, no downstream carry |
| "NPT→UTC handling" for the 3,350 off-grid rows | **They are processing errors** (DHM), to be **excluded and counted** — not converted |

The remaining M-D3 unknown (**sum vs mean**) does not touch this plan: it rescales every value by a
constant, which changes magnitudes but not the axis, the provenance, or run detection.

## Constraints

1. **No DHM data enters the public repository.** Research data lives in `data/dhm_precip/`
   (gitignored). Fixtures are **synthetic**. A worktree starts with none of it — see the milestone
   doc's "Working on this track".
2. **Polars** for the research pipeline (`scripts/dhm_precip/`); the QC service is existing
   production code and keeps its own idiom.
3. **`QcRuleParams.thresholds` is `dict[str, float]`** (`types/domain.py:152`) — scalars only. Any
   new rule parameter must be expressible as a single float, or it does not fit the contract.
4. **`QcRuleId` is a closed `Literal`** (`types/domain.py:137`) **and** separately allowlisted in
   `config/qc_rules.py:14`. This plan adds **no new rule id**, so neither changes.

## Design decisions

### M-A2 — the axis

- **D1 — Materialise the 568 globally-missing hours as explicit rows.** Reindex each station onto
  every hour from the record's first to last canonical stamp, so a gap becomes a row rather than an
  absence. Station-level nulls already exist and are untouched; what is new is that an hour missing
  from the workbook *for every station* stops being invisible.
- **D2 — Reindexing inserts NULL, never `0.0`.** *The single most dangerous line in the plan.* A
  null means "no observation"; a zero means "observed, no rain". Filling gaps with zeros would
  **manufacture dry periods** — and M-A3's job is detecting implausible dry runs, so it would
  fabricate exactly the defect we are hunting.
  This is **mechanically** necessary, not merely conceptual: `_apply_frozen_sensor` breaks a run on a
  `None` value (`services/qc.py:107-110`), so a null fill correctly severs a run at a gap, while a
  zero fill would let the run continue straight through it. Asserted in code and locked by a test.
- **D2b — The null-vs-zero guarantee must survive aggregation.** Polars returns `0.0` for the sum of
  an all-null group, a trap already documented at `stats_coherence.py:107` but **not** handled in
  `stats_climatology.py:83` (`per_year_totals_with_completeness`) or `stats_defects.py:130`
  (`annual_totals`). Those would turn a wholly-unreported year into a fabricated 0 mm total — D2's
  defect re-entering by a different door. Both must emit null when `hours_present == 0`, with a
  regression case built from an all-null station-period.
- **D3 — Off-grid rows are excluded and counted, not converted** (M-D3), and the counts are recorded
  **at two named grains that must not be summed together**: `off_grid_source_timestamp_rows` (3,350 —
  workbook rows, the vision's figure) and `off_grid_non_null_observations` (6,633 — actual
  observations, total and per station). The existing code already treats these as distinct
  (`stats_axis.py:36`, `:69`, `:106`); conflating them produces a reconciliation that cannot balance.
  Each is tested against its own known total.
- **D4 — Per-row provenance.** Every emitted row records whether it came from a delivered
  observation, or was inserted as an explicit gap. The `source_row_index` that M-A1's frame already
  carries (D3 of Plan 170) is preserved for delivered rows and null for inserted ones.
- **D5 — Conservation is proved by ROW IDENTITY, not by summing.** An earlier version asserted the
  normalised sum equals the input sum *exactly*; review showed that fails — reindexing changes
  summation order, and float addition is not associative (measured on the pinned workbook:
  `-449781508.2` becomes `-449781508.1999999`). Summing also **is** arithmetic, contradicting the
  claim that none is performed.
  Instead: every delivered row, keyed by `(source_row_index, station, timestamp)`, must appear in the
  output with a **bit-identical** `value_mm`; every added row must have a null source index **and** a
  null value. That is exact, order-independent, and proves more — not just that mass balances but that
  no value moved. A summed total may be *reported* alongside, with a tolerance, as a readability aid.
- **D6 — Period-ending is recorded, not assumed.** The convention is now known, so it is written into
  the output's metadata and the provenance record as a stated fact with its source (M-D3). Recording
  it costs nothing now and prevents the next dataset silently inheriting a convention nobody checked.
- **D6b — The station population is supplied, not inferred.** The loader's long frame carries **all
  37 workbook columns**, empties included (`loader.py:213`), so a naive reindex would emit
  37 × 52,597 rows — 11 stations of pure fabricated gap. The runner's already-validated live-station
  set (26) is **passed in**, and the output's station set is asserted to equal it exactly. The
  normaliser does not decide usability; M-A1 already did.
- **D7 — M-A2 makes NO value judgements.** It does not drop sentinels, stuck-high blocks or zero
  runs. *Considered and rejected:* pulling sentinel exclusion forward here, since sentinels need no
  time axis. Rejected because it splits the mask across two milestones and leaves two owners for
  "which values are real". M-A2 owns the **axis**; M-A3 owns **values**. A −9999999 passes through
  M-A2 untouched and is M-A3's to remove.

### M-I1 — the rule

- **D8 — `frozen_sensor` gains a scalar value-exclusion threshold.** Per constraint 3 it must be one
  float. Add `exclude_at_or_below`: values `<= ` it never start or extend a frozen run. Setting it to
  `0.0` makes the rule ignore dry spells while still catching Sindhuli Madhi's 120-hour block pinned
  at ~72 mm. **Backwards-compatible**: absent from the thresholds dict, the rule behaves exactly as
  it does today, so no existing discharge or water-level rule changes behaviour.
- **D8b — Changed semantics need a changed rule version, and the flag must carry it.**
  `_apply_frozen_sensor` currently stamps a hard-coded module constant `_RULE_VERSION`
  (`services/qc.py:22`, written at `:124`) and **ignores** the `rule_version` on the
  `QcRuleParams` it was given — so a flag cannot record which variant produced it, which is the whole
  point of per-flag versioning (`architecture-context.md:283`). Fix: emit `rule.rule_version`, give
  the exclusion-enabled precipitation rule its own version, and **assert the emitted version in a
  test**. Existing configurations keep their current version string and thresholds, so no stored flag
  changes meaning.
- **D9 — No new rule id, and `gross_outlier` is not reused.** `_apply_gross_outlier`
  (`services/qc.py:197`) is a symmetric `|value − mean| > k·std` test against a climatological
  baseline. On a zero-inflated, right-skewed variable it flags real heavy rain, and it encodes no
  repeated-zero-run detection at all — though it *can* flag an individual zero when the baseline mean
  is positive and its standard deviation small, so "never flags zeros" (an earlier wording here) is
  false.
  Sentinel rejection needs no new rule either — a `range_check` with `value_min = 0.0` rejects
  `-9999999` already.
- **D10 — No operational config binding.** Per OD-3 the research pipeline (M-A3) is this rule's first
  consumer, via an **in-code** `QcRuleSet`. No precipitation rows are added to `config/qc_rules.py` —
  that is M-I4, gated behind weather-observation ingest.

### Both

- **D11 — Task exit gate** (`docs/workflow.md:376`), referenced by every code task: the task's own
  test, `uv run ruff check src/ scripts/ tests/`, `uv run ruff format --check` (same paths),
  `uv run pyright src/`, `uv run pyright scripts/dhm_precip/`, `uv run pytest`, and affected docs
  updated in the same change.

## Out of scope

The QC mask itself (M-A3) · any ERA5-Land work (M-A5/A6) · binding precipitation rules into
`config/qc_rules.py` (M-I4, gated on G1+G2) · changing `gross_outlier` · the sum-vs-mean question
(M-D3, and it does not affect this plan).

## Phases and tasks

Every code task carries the D11 gate in addition to the test named below.

### Phase 1 — parallel; the two milestones are independent

**1a — `frozen_sensor` value exclusion (M-I1).** Add the `exclude_at_or_below` threshold to
`_apply_frozen_sensor` (`services/qc.py:92`). Absent ⇒ today's behaviour exactly.
*Verification (red-first):* `uv run pytest tests/unit/services/test_qc.py` — new cases that **fail
against current `main`**: a 120-hour run of identical non-zero values is flagged; a 50-day run of
exact zeros with `exclude_at_or_below = 0.0` is **not**; and an existing discharge frozen-sensor case
is unchanged when the threshold is absent.

**1b — canonical hourly axis (M-A2).** A pure function from M-A1's `ON_GRID` frame **plus the
validated live-station set** (D6b) to the normalised frame: complete hourly reindex per station (D1),
NULL fill (D2), provenance column (D4).
*Verification:* `uv run pytest tests/unit/scripts/test_dhm_precip_normalise.py` — a gap materialises
as NULL and **never** as `0.0`; delivered rows keep their `source_row_index` and inserted rows have
none; the axis is complete, unique, strictly increasing and exactly hourly; the output station set is
exactly the 26 supplied, with the 11 empty columns absent.

### Phase 2 — assertions and provenance

**2a — conservation, the exclusion record, and the aggregation guard (M-A2).** *(depends on 1b)*
Assert D5's **row-identity** conservation in code; build the provenance record carrying both off-grid
count grains (D3) and the period-ending statement with its source (D6); and close D2b by making
`per_year_totals_with_completeness` (`stats_climatology.py:83`) and `annual_totals`
(`stats_defects.py:130`) emit null rather than `0.0` for an all-null group.
*Verification:* `uv run pytest tests/unit/scripts/test_dhm_precip_normalise.py` and
`tests/unit/scripts/test_dhm_precip_climatology.py` — a deliberately lossy reindex fails the
row-identity assertion; a mutated value fails it even when the total still balances; each off-grid
count matches its own grain's known total (3,350 rows / 6,633 observations); and an all-null
station-year yields null, not `0.0`.

### Phase 3 — wire and record

**3a — emit the normalised dataset (M-A2).** Extend the M-A1 runner to write the normalised frame and
its provenance alongside the existing tables. **The manifest cannot describe them as they stand**:
declarations are generated from `ComputedTables` frames carrying a `View`/`AxisStatus`
(`pipeline.py:424`) and neither enum can represent a normalised dataset (`domain_types.py:19`). Add a
`NORMALIZED` axis status and declare both artefacts.
*Verification:* `DHM_PRECIP_XLSX=… uv run python scripts/dhm_precip/run.py --out <tmp>` exits 0 and
writes the normalised dataset + provenance; **their manifest declarations round-trip** (declared set
== observed set, not merely "the files exist"); the existing M-A1 expectation gate still passes
(`tests/integration/test_dhm_precip_reproduction.py`, run **with** the workbook — a bare full-suite
run skips it).

**3b — documentation.** Record M-A2 and M-I1 complete in the milestone doc, including the off-grid
exclusion counts the real data yields.
*Verification:* affected docs updated in the same change (D11).

## Exit gate

A canonical, gap-explicit hourly dataset with per-row provenance and a passing mass-conservation
assertion, plus a `frozen_sensor` that ignores dry spells while still catching a stuck sensor.
**M-A3 is then unblocked** — it has both an axis to detect runs on and a rule to detect them with.

## Risks

- **The NULL-vs-zero trap (D2)** is the one that would quietly corrupt everything downstream: zeros
  in the gaps would fabricate the dry runs M-A3 exists to find. Hence an assertion, not just a test.
- **`frozen_sensor` is production code** used today by discharge and water-level QC. The
  backwards-compatibility case in 1a is not optional.
- **This plan does not make the data usable** — the mask is M-A3. Nothing here licenses citing any
  magnitude-bearing statistic; the vision's per-family citation ban (D11) is unchanged.

```json
{
  "phases": [
    {"id": "phase-1", "tasks": ["1a", "1b"], "parallel": true},
    {"id": "phase-2", "tasks": ["2a"], "parallel": false, "depends_on": ["phase-1"]},
    {"id": "phase-3", "tasks": ["3a", "3b"], "parallel": false, "depends_on": ["phase-2"]}
  ]
}
```
