---
status: DRAFT
created: 2026-08-15
plan: 173
title: M-A3 — the fit-for-purpose QC mask
scope: A timestamp mask over the normalised hourly frame, produced by running the PRODUCTION QC checker with an in-code precipitation rule set, plus per-station/per-season removal accounting and the M-A6 exclusion list. Explicitly NOT a cleaned dataset (consumers apply the mask), NOT adjudication of which zero runs are real, NOT any ERA5 work, NOT operational config binding (M-I4, gated).
depends_on: [170, 172]
blocks: [M-A6, M-A7, M-I2]
source: docs/design/dhm-precipitation-milestones.md
---

# Plan 173 — M-A3 fit-for-purpose QC mask

## Status
**DRAFT.** Implements **M-A3**, unblocked by Plan 172 (the canonical axis and the `frozen_sensor`
value exclusion). This is the milestone that makes magnitude statistics usable.

## Problem

14 of M-A1's 45 expectations are `withdrawn_unreproducible`, mostly because unmasked computation still
contains the defects: Group B's q99.9 reads 33–77.6 mm/h because Sindhuli Madhi's 120-hour block
pinned at ~72 mm is still in the sample, exactly as Plan 170's D6 predicted. M-A3 removes it.

## Owner decisions this plan implements

| Decision | Consequence here |
|---|---|
| **QC depth: fit-for-purpose** | Drop candidate zero-run periods **wholesale, without adjudication**. We are not deciding which are real; we are declining to use any of them |
| **OD-3** | Run detection goes through the **production** `Stage1QualityChecker`, with an **in-code** rule set. No config rows (that is M-I4, gated) |
| **Rule 1 (MNAR)** | The mask makes the retained sample missing-not-at-random. That obligation binds the mask's **consumers**, not this plan — see D8 |

## Design decisions

- **D1 — The deliverable is a timestamp mask, not a cleaned dataset.** A set of
  `(station, timestamp)` pairs to drop, plus the accounting. Consumers apply it (and must apply it
  identically to any compared dataset — vision Rule 1). M-A3 mutates no values.

- **D2 — Constructing `Observation`s: the gap rows force a status.**
  `Observation.__post_init__` (`types/observation.py:46-52`) requires `value is None` **if and only
  if** `qc_status == MISSING`. M-A2's normalised frame is null at every gap, so **gap rows must be
  constructed with `QcStatus.MISSING`** and delivered rows with `QcStatus.RAW`. Get this wrong and
  construction raises on the first gap — of which there are 568 × 26.
  This is also semantically right: a gap *is* a missing observation, and `_apply_frozen_sensor`
  breaks a run on a `None` value, which is precisely the bridging M-A2 exists to prevent.

- **D3 — TWO `frozen_sensor` instances, because one cannot do both jobs.**
  `exclude_at_or_below` makes the rule **ignore** zeros (Plan 172 D8) — so the very threshold that
  lets dry spells pass also makes that instance blind to zero runs. `QcRuleSet.rules_for`
  (`types/domain.py:163`) filters by parameter and time step and returns **all** matches without
  deduplicating by `rule_id`, and the checker appends each rule's flags
  (`services/qc.py:255-263`), so one rule set can legitimately hold both:

  | Instance | `exclude_at_or_below` | `min_consecutive` | Catches |
  |---|---|---|---|
  | **stuck-value** | `0.0` (ignore zeros) | short | Sindhuli Madhi's ~72 mm pinned block |
  | **long-constant-run** | *absent* | long (the zero-run threshold) | Aiselukhark's 52-day zero run — and any other long constant run |

  The second instance flags **any** sufficiently long constant run, including a genuine 52-day dry
  spell. That is the wholesale decision working as intended, not a defect.
  **Give the two instances distinct `rule_version` strings** so an emitted flag says which fired —
  Plan 172's D8b made `rule_version` flow through, and this is the first thing that needs it.

- **D4 — Sentinels need no new rule.** A `range_check` with `value_min = 0.0` rejects `-9999999`
  already. Include it in the rule set with an upper bound sourced from regional extreme-value
  literature, **not** from this sample's maxima (Plan 170's standing caution).

- **D5 — `time_step` must be 3600 s.** The checker infers the step from the observations
  (`services/qc.py:252`) and `rules_for` matches on it, so a rule whose `time_step` differs is
  **silently skipped** — no error, no flags, an empty mask that looks like clean data. Assert the
  inferred step matches the rule set's before trusting any result.

- **D6 — The run-detection contract is parameterised, not implicit.** Minimum run duration, seasonal
  scope, treatment of isolated missing values inside a run, boundary handling, and whether nearby runs
  merge. Without these, "wholesale" is reproducible only after subjective identification. All live on
  the existing frozen parameter object.

- **D7 — Chunk per station.** ~1.37 M observations (26 × 52,597) as 12-field frozen dataclasses is the
  one real performance risk. The checker already groups by `(station_id, parameter)`, so per-station
  batches change no result and bound peak memory.

- **D8 — Rule 1's MNAR obligations attach to CONSUMERS, and the accounting is what makes them
  honourable.** M-A3 does not compute any masked statistic, so it cannot violate Rule 1 — but it must
  emit what consumers need to comply: retained/removed counts **per station and per season**, so
  every downstream figure can state the exposure it rests on. A station losing most of its monsoon is
  **excluded** from M-A6 via an explicit exclusion list, never silently thinned.

- **D9 — Task exit gate** (`docs/workflow.md:376`), referenced by every code task: the task's own
  test, `uv run ruff check src/ scripts/ tests/`, `uv run ruff format --check` (same paths),
  `uv run pyright src/`, `uv run pyright scripts/dhm_precip/`, `uv run pytest`, and affected docs
  updated in the same change.

## Out of scope

Applying the mask to emit a cleaned dataset (consumers do that) · deciding which zero runs are real
(explicitly declined) · IMERG adjudication (left our track with the wholesale decision) · any
ERA5-Land work · `config/qc_rules.py` binding (M-I4, gated on G1+G2) · recomputing M-A1's withdrawn
expectations (M-A7 and M-A6 own those).

## Phases and tasks

Every code task carries the D9 gate in addition to the test named below.

### Phase 1 — the rule set and the bridge

**1a — in-code precipitation `QcRuleSet`.** The two `frozen_sensor` instances (D3) with distinct
`rule_version`s, plus `range_check` (D4), all at `time_step = 3600 s` (D5). Detection parameters on
the parameter object (D6).
*Verification:* `uv run pytest tests/unit/scripts/test_dhm_precip_ruleset.py` — `rules_for` returns
**both** frozen_sensor instances for precipitation at 3600 s; a rule set whose `time_step` disagrees
with the data yields **no** flags (the silent-skip trap of D5, asserted so it can never surprise us).

**1b — normalised frame → `Observation`s.** Per-station chunking (D7); gap rows `MISSING`, delivered
rows `RAW` (D2).
*Verification:* `uv run pytest tests/unit/scripts/test_dhm_precip_observations.py` — a null row
becomes `MISSING` with `value=None` and a delivered row `RAW` with its value; **round-trip count and
values are preserved**; constructing a null row as `RAW` raises (locking the invariant).

### Phase 2 — the mask

**2a — run the checker and build the mask.** *(depends on 1a, 1b)* Call
`Stage1QualityChecker.check()`, collect flags, and reduce them to the `(station, timestamp)` drop set.
*Verification (red-first):* `uv run pytest tests/unit/scripts/test_dhm_precip_mask.py` — synthetic
frames reproducing each real defect signature: a **52-day zero run**, a **120-hour pinned non-zero
block**, and a **sentinel**, each caught; a legitimate short dry spell **not** caught; a zero run
**interrupted by a gap** yields two shorter runs, neither reaching the threshold (proving M-A2's
null-fill severs runs as intended).

**2b — removal accounting and the exclusion list.** *(depends on 2a)* Retained/removed per station and
per season; the M-A6 exclusion list from the monsoon-retention rule (D8).
*Verification:* `uv run pytest tests/unit/scripts/test_dhm_precip_mask.py` — a station losing most of
its monsoon appears in the exclusion list; one losing little does not; the accounting sums back to the
input population.

### Phase 3 — wire and record

**3a — emit the mask and accounting.** Extend the runner to write both alongside the existing
artefacts, declared in the manifest (the `NORMALIZED` axis status pattern Plan 172 established).
*Verification:* `DHM_PRECIP_XLSX=… uv run python scripts/dhm_precip/run.py --out <tmp>` exits 0 and
writes mask + accounting with round-tripping manifest declarations; the M-A1 reproduction gate still
passes **with the workbook** (a bare full-suite run skips it).

**3b — documentation.** Record M-A3 complete in the milestone doc with the real removal figures.
**Do not** update the vision's withdrawn expectations here — recomputing them is M-A6/M-A7's, and the
per-family citation ban stands until then.
*Verification:* affected docs updated in the same change (D9).

## Exit gate

A timestamp mask, per-station/per-season removal accounting, and the M-A6 exclusion list — with
red-first acceptance cases for all three real defect signatures (Aiselukhark's 52-day run, Sindhuli
Madhi's stuck-high block, Lukla's sentinels). **M-A6 and M-A7 are then unblocked.**

## Risks

- **The silent-skip trap (D5)** is the one that produces a confident wrong answer: a `time_step`
  mismatch yields an empty mask that looks exactly like clean data. Asserted, not assumed.
- **Wholesale removal discards real dry spells**, by decision. Sound only under identical masking
  (Rule 1), and every masked result must state its retained fraction — which is why D8's accounting
  is part of the exit, not a nicety.
- **This plan does not make the withdrawn expectations reproduce.** It supplies the mask; M-A6/M-A7
  do the recomputation. Nothing here lifts the citation ban.

```json
{
  "phases": [
    {"id": "phase-1", "tasks": ["1a", "1b"], "parallel": true},
    {"id": "phase-2a", "tasks": ["2a"], "parallel": false, "depends_on": ["phase-1"]},
    {"id": "phase-2b", "tasks": ["2b"], "parallel": false, "depends_on": ["phase-2a"]},
    {"id": "phase-3", "tasks": ["3a", "3b"], "parallel": false, "depends_on": ["phase-2b"]}
  ]
}
```
