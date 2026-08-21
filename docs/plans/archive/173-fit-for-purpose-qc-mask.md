---
status: COMPLETE
created: 2026-08-15
revised: 2026-08-15
plan: 173
title: M-A3 — the fit-for-purpose QC mask
scope: A timestamp mask over the normalised hourly frame, produced by running the PRODUCTION QC checker with an in-code precipitation rule set, plus per-station/per-season removal accounting and the M-A6 exclusion list. Explicitly NOT a cleaned dataset (consumers apply the mask), NOT adjudication of which zero runs are real, NOT any ERA5 work, NOT operational config binding (M-I4, gated).
depends_on: [170, 172]
blocks: [M-A6, M-A7, M-I2]
source: docs/design/dhm-precipitation-milestones.md
---

# Plan 173 — M-A3 fit-for-purpose QC mask

## Status
**COMPLETE** — shipped on `main` as `c2d59d0` (feat(dhm-precip): Plan 173 — M-A3 fit-for-purpose QC mask (#158)).
*(Status reconciled 2026-08-21 in a housekeeping pass: the plan had shipped but was still marked READY, so it read as outstanding work.)*

**READY — owner-confirmed 2026-08-15.** Gated with **three manual Codex rounds** (2B+3M, then a
third round asked what the first two had MISSED, which found 2B+4M more). Two of the four blockers
were the same class of defect — an unpinned, mask-changing parameter — caught in different places.

Implements **M-A3**, unblocked by Plan 172 (the canonical axis and the `frozen_sensor`
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
  `exclude_at_or_below` makes the rule **ignore** values at or below it (Plan 172 D8) — so the very
  threshold that lets dry spells pass also makes that instance blind to zero runs.
  `QcRuleSet.rules_for` (`types/domain.py:163`) filters by parameter and time step and returns **all**
  matches without deduplicating by `rule_id`, and the checker appends each rule's flags
  (`services/qc.py:255-263`), so one rule set can legitimately hold both:

  | Instance | `exclude_at_or_below` | `min_consecutive` | Scope | Catches |
  |---|---|---|---|---|
  | **stuck-value** | **5.0** (`stuck_high_min_value_mm`) | 12 h | whole series | Sindhuli Madhi's ~72 mm pinned block |
  | **long-zero-run** | *absent* | **168 h (7 days)** | **JJAS only** (D3b) | Aiselukhark's 52-day run and its six siblings |

  **The exclusion floor is 5.0 mm, not 0.0.** `stuck_high_min_value_mm = 5.0`
  (`scripts/dhm_precip/params.py:118`) exists precisely because the 0.5 mm adjacency tolerance would
  otherwise chain ordinary tipping-bucket noise (`0.2 / 0.4 / 0.6 …`) into a false "stuck" run. At
  `0.0` every positive value stays eligible and that noise is masked as a defect.

  **The 7-day threshold is measured, not asserted.** `minimum_run_duration_hours = 12`
  (`params.py:110`) is M-A1's **inventory** threshold — right for *reporting* candidates, catastrophic
  for *removal*. Measured against the pinned workbook, JJAS hours removed:

  | threshold | median station | worst station |
  |---|---|---|
  | 12 h | **61.1 %** | 75.2 % |
  | 24 h | 41.1 % | 58.6 % |
  | 3 d | 12.4 % | 24.6 % |
  | **7 d** | **1.6 %** | 17.0 % |
  | 14 d | 0.0 % | 17.0 % |

  7 days catches **every** documented defect — the shortest is Biratnagar at 12.2 days — while costing
  the median station 1.6 %. 14 days would miss three of the seven; 12 hours would discard most of the
  monsoon as if it were broken.

  **Give the two instances distinct `rule_version` strings** so an emitted flag says which fired —
  Plan 172's D8b made `rule_version` flow through, and this is its first real use.

- **D3c — Two PASSES with pass-specific rule sets, not one call with `skipped_rule_ids`.** Both
  instances share `rule_id = "frozen_sensor"`, and the checker selects rules only by `rule_id`
  (`services/qc.py:256`) — `skipped_rule_ids` cannot distinguish them, so a single full-rule-set call
  would apply the long-zero-run rule year-round and defeat D3b. Instead:
  | Pass | Rule set | Observations |
  |---|---|---|
  | **A** | `range_check` + stuck-value `frozen_sensor` | the whole station series |
  | **B** | long-zero-run `frozen_sensor` only | one JJAS season at a time |

  The mask is the **union** of both passes' flagged keys.

- **D3b — Seasonal scope is applied OUTSIDE the checker, because the rule cannot express it.**
  `_apply_frozen_sensor` takes only tolerance, minimum length and the exclusion floor
  (`services/qc.py:92-98`) — no season parameter — and the checker applies every matching rule to the
  **whole** station series (`services/qc.py:250`). A year-round long-zero-run rule would therefore
  mask the entire dry season: Terai stations run DJF shares of 1–2 %, so their winters are *legitimately*
  weeks of zero. The long-zero-run pass is run **per JJAS season**, on JJAS observations only, and the
  stuck-value pass runs on the whole series (a pinned sensor is a defect in any month). Tested at the
  cutoff (threshold − 1 h does not flag) and at a season boundary (a run spanning 30 Sep → 1 Oct does
  not silently merge across seasons).

- **D4 — `range_check` is a physical-impossibility gate, NOT an outlier filter.** `value_min = 0.0`
  rejects the `-9999999` sentinels. The upper bound is **`value_max = 200.0` mm/h**, chosen to be
  comfortably unreachable rather than discriminating: every value above it becomes `QC_FAILED`
  (`services/qc.py:57`), so a tight bound would silently mask real extremes.
  Calibration for why 200 is safe: Adhikari et al. (2025) define extreme hourly precipitation in
  Nepal as **> 40 mm/h** and find such events *widespread*, with one Koshi station recording eight in
  a single season — so 40 is common, not a ceiling. Our own largest defensible daily total is ~438 mm
  (Tarahara, 2021-10-19, corroborated by Kanyam the same day).
  **Outlier detection is explicitly not attempted here** — `gross_outlier` is unsuitable for
  zero-inflated precipitation (Plan 170), and nothing replaces it. Boundary-tested both ways: a
  legitimate 100 mm/h extreme passes; 200.1 is masked.

- **D5 — The mask builder RAISES on a `time_step` mismatch.** The checker infers the step from the
  observations (`services/qc.py:252`) and `rules_for` matches on it, so a rule whose `time_step`
  differs is **silently skipped** — no error, no flags, an empty mask indistinguishable from clean
  data. A characterisation test of that silence is not enough: the **mask builder itself** compares
  the inferred step against the rule set's and raises a typed error before returning, and **task 2a
  tests that exception**. This is the plan's most dangerous failure mode because it fails toward
  "everything is fine".

- **D6 — The run-detection contract is parameterised, not implicit.** Minimum run duration, seasonal
  scope, treatment of isolated missing values inside a run, boundary handling, and whether nearby runs
  merge. Without these, "wholesale" is reproducible only after subjective identification. All live on
  the existing frozen parameter object.

- **D7 — Chunk per station.** ~1.37 M observations (26 × 52,597) as 12-field frozen dataclasses is the
  one real performance risk. The checker already groups by `(station_id, parameter)`, so per-station
  batches change no result and bound peak memory.

- **D8 — Accounting is THREE-WAY, because "not removed" is not "retained".** A `MISSING` row
  legitimately draws no flag (`services/qc.py:55`, `:106`), so counting it as retained would inflate
  every retention figure with hours that were never observed. Three mutually exclusive categories,
  reconciling exactly to the axis row count:
  | Category | Meaning |
  |---|---|
  | `source_missing` | a gap row from M-A2 — never observed |
  | `qc_removed` | observed, and masked by a rule |
  | `retained_nonmissing` | observed and kept — the retention **numerator** |

  **Retention is computed over OBSERVED rows only:**
  `retained_nonmissing / (retained_nonmissing + qc_removed)`. `source_missing` is excluded from the
  denominator — including it would blend "the sensor never reported" with "we masked it", which are
  different facts and are exactly what this three-way split exists to keep apart.

  **Exposure is CROSS-CLASSIFIED by `(station, season, hour_of_day, category)`**, not two separate
  marginals. Rule 1 requires hour-of-day exposure (`dhm-precipitation-milestones.md:51`) because a
  mask that removes hours unevenly across the day biases diurnal means — and M-A7's whole subject is
  diurnal structure. Season and hour reported separately cannot answer "what was the JJAS diurnal
  exposure?", which is the question that actually matters.

  **Seasons must be exhaustive.** The parameter object names only JJAS and DJF, leaving five months
  unassigned — every axis row must land in exactly one bin, so the mapping is the conventional Nepali
  four: **MAM** (pre-monsoon), **JJAS** (monsoon), **ON** (post-monsoon), **DJF** (winter). Asserted
  by reconciling the cross-classified table back to the axis row count exactly once per row.

  **"Losing most of its monsoon" is an exact, pinned predicate**, not a judgement. A station is
  excluded from M-A6 when its JJAS
  `retained_nonmissing / (retained_nonmissing + qc_removed)` is **strictly below
  `minimum_jjas_retained_fraction = 0.50`**. Equality passes (0.50 is retained). A **zero denominator**
  — a station with no observed JJAS hours at all — is excluded, and recorded with that reason rather
  than dividing by zero.

  **Expect the list to be EMPTY, and do not treat that as a bug.** Measured at the 7-day threshold,
  the worst-affected station retains **0.830** (Lete; then Aiselukhark 0.831, Nagarkot 0.913) against
  a median of **0.984**, and **no station falls below 0.75**. The predicate is a safeguard against a
  pathological case, not a filter expected to fire on this delivery. An implementer who finds an empty
  list has not broken anything — the test asserts the boundary with synthetic stations, not that any
  real station is excluded.

  Rule 1's obligations otherwise bind the mask's **consumers**: M-A3 computes no masked statistic, so
  it cannot violate them; it supplies what compliance requires.

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

**1a — in-code precipitation `QcRuleSet`.** The two `frozen_sensor` instances (D3) — stuck-value at
`exclude_at_or_below = 5.0` / 12 h, long-zero-run with no exclusion at 168 h — with distinct
`rule_version`s, plus `range_check` at `0.0 / 200.0` (D4), all at `time_step = 3600 s`. Detection
parameters on the frozen parameter object (D6).
*Verification:* `uv run pytest tests/unit/scripts/test_dhm_precip_ruleset.py` — `rules_for` returns
**both** frozen_sensor instances for precipitation at 3600 s, with distinct `rule_version`s; a
`0.2/0.4/0.6` tipping-bucket noise sequence **at least `min_consecutive` long** is **not** flagged as
stuck — the length matters, since a shorter sequence would pass for the wrong reason (the 5.0 floor
must do the work, not the run being too short to qualify);
a mismatched `time_step` yields no flags at the checker level (characterisation only — the enforcing
raise is task 2a's).

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
block**, and a **sentinel** — each masked **in full**, asserted as an exact expected timestamp set
rather than "at least one flag", since wholesale removal is the decision and a partial mask would
satisfy a weaker assertion; a **167-hour** zero run (threshold − 1 h) **not** caught; a
zero run **interrupted by a gap** yields two shorter runs, neither reaching the threshold (proving
M-A2's null-fill severs runs as intended); a run spanning **30 Sep → 1 Oct** does not merge across
seasons (D3b); a **winter** month of zeros is untouched (D3b's whole point); and a **`time_step`
mismatch raises** the typed error rather than returning an empty mask (D5).

**2b — removal accounting, overlap handling, and the exclusion list.** *(depends on 2a)* The three-way accounting of
D8 — `source_missing` / `qc_removed` / `retained_nonmissing` — per station, per season **and per hour
of day**; the M-A6 exclusion list from the exact monsoon-retention predicate.
*Verification:* `uv run pytest tests/unit/scripts/test_dhm_precip_mask.py` —
**overlap**: a ≥168 h JJAS constant run *above* the 5.0 floor trips **both** instances, so two flags
with distinct `rule_version`s are emitted, yet the mask holds **one** key per timestamp and
`qc_removed` counts each observation **exactly once** (the double-counting this design invites);
the cross-classified `(station, season, hour_of_day, category)` table reconciles **exactly** to the
axis row count, every row in exactly one of the four seasons, with **exact expected hour counts** for
a synthetic station — not merely "exposure is emitted";
a gap row counts as `source_missing` and **never** as retained; retention divides by
`retained_nonmissing + qc_removed` only; a synthetic station at retention 0.49 is excluded, one at
**exactly 0.50** is not, and one with zero observed JJAS hours is excluded with that reason.

### Phase 3 — wire and record

**3a — emit the mask, accounting, exclusion list and rule provenance.** Extend the runner to write
**all four** alongside the existing artefacts, declared in the manifest (the `NORMALIZED` axis-status
pattern Plan 172 established). The exclusion list is an artefact (D8), not a log line. **Rule
provenance is part of it**: the mask reduction collapses flags to timestamps and discards which rule
fired, so the manifest records the **exact executed rule definitions — id, `rule_version`, thresholds
and scope, per pass (D3c)**. M-I2 needs the mask definition and version to package the dataset
(`dhm-precipitation-milestones.md:447-452`); without it the mask is unreproducible from the artefact
alone.
*Verification:* `DHM_PRECIP_XLSX=… uv run python scripts/dhm_precip/run.py --out <tmp>` exits 0 and
writes **mask, accounting, exclusion list and rule provenance**, all with round-tripping manifest
declarations. **Workbook-gated assertions on the real outputs**, because the M-A1 reproduction gate
evaluates *unmasked* statistics by design and would therefore pass against an **empty** mask: assert
the real mask is non-empty, that it contains Sindhuli Madhi's stuck-high block and Aiselukhark's
52-day run, and that the cross-classified accounting reconciles to the axis row count.

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
