---
status: DRAFT
created: 2026-09-24
plan: 324
title: Plan 272's last four items — a compliance row that points at nothing, and three records that describe a system we no longer run
scope: Close the four items Plan 272's own triage marked "DO NOW" or "UNCONDITIONALLY" and which no follow-on plan owns — the stale WMO evidence row, the un-bumped observation QC rule version, Plan 264's unrecorded cross-plan debt, and Plan 272's own text describing the pre-316/317/318 world. NOT the parked items in 272's triage section C, NOT the DHM mask byte-identical test (item 5, deliberately deferred), NOT Plan 272's own `status:` field (the owner closes that after this merges), NOT the `status: READY` fields on merged plans 316/317/318, NOT Plan 314's open decisions, NOT any change to QC behaviour.
depends_on: []
blocks: []
related: [101, 264, 272, 314, 315, 316, 317, 318, 323]
open_decisions: [D1]
source: 2026-09-24 — a full item-by-item audit of Plan 272 against `origin/main` at `8b68f6f8`, run because PR #297 had previously been merged believing it implemented 272 when it shipped 12 of 54 items. Every claim below was re-verified directly before drafting; each says how.
---

# Plan 324 — Plan 272's last four items

⚠️ **Plan number PROVISIONAL until the owner grants it.** 320-322 are held by a concurrent session.

## Status

**DRAFT.** ⛔ No implementation until an independent review is complete and the orchestrator sets
READY.

## Why this plan exists

Plan 272's **behaviour** is complete and deployed: the selection fix, `QC_UNCHECKED`, the consumer
split, the `DEGRADED` provenance, the published-series exclusion, re-examination and the telemetry
all shipped through Plans 316/317/318. The audit confirmed its triage sections A and B are closed.

What remains is four items that the plan itself marked **"DO NOW, trivial, no decision needed"** or
**"UNCONDITIONALLY, not 'if'"**, and which **no follow-on plan owns**. They are all records rather
than behaviour — which is precisely why they have survived four merges without anyone tripping over
them.

⭐ **One of them is a compliance claim.** That is what makes this worth a plan rather than a drive-by
commit.

## What is measured

Verified against `origin/main` on 2026-09-24. Commands are given so a reviewer can re-run them.

1. 🔴 **The WMO evidence row points at nothing.** `docs/standards/wmo.md:187` — the row
   *"Automated range + temporal-consistency checks | WMO-168 Vol I"* — cites `services/qc.py:50`,
   `:71` and `:225`. Those lines now hold, respectively, **a bare `"""` docstring terminator, the
   parameter `observations: list[Observation],` and the keyword `rule_version=_RULE_VERSION,`.**
   The real symbols are `_apply_range_check` **`:103`**, `_apply_rate_of_change` **`:124`**,
   `Stage1QualityChecker` **`:281`**. The row is still stamped *Verified 2026-09-04*.
   ⛔ *Plan 272's T4 demanded this row be updated **unconditionally**, citing that this repo has
   already paid five months of false WMO compliance for exactly this class of stale-but-plausible
   evidence (Plan 023).*
2. **The observation QC rule version was never bumped.** `services/qc.py:22` is
   `_RULE_VERSION = "1.0"`; `services/qc_datum.py:18-19` are `"1.1-datum"` / `"1.1-datum-skip"`.
   `qc_datum.py` is byte-identical to its pre-#297 state. ⇒ **a verdict produced by the new
   selection logic is indistinguishable from one produced by the old**, which is the property
   272 T2b's verification clause asked for.
3. ✅ **Bumping is safe — nothing compares the value.** `qc_rule_version` is written and stored and
   never read back for a decision: `grep -rn qc_rule_version src/` returns only the dataclass field
   (`types/observation.py:43`), the column (`db/metadata.py:536`), the two write sites
   (`flows/ingest_observations.py:487`, `services/onboarding.py:810`), the producer
   (`services/qc_datum.py:23`) and store plumbing. **No equality test, no filter, no branch.**
4. ⛔ **THE TRAP: there are TWO `_RULE_VERSION = "1.0"` constants.** `services/qc.py:22` is
   observation QC and in scope; **`services/forecast_qc.py:22` is FORECAST QC and is NOT**. A
   blanket edit or a careless `sed` hits both and silently re-versions every forecast QC flag.
5. **Two tests assert the datum versions by exact value** — `tests/unit/flows/test_ingest_observations.py:344`
   (`== "1.1-datum"`) and `:370` (`== "1.1-datum-skip"`). They move with the bump.
   ⚠️ **This list came from one grep pattern and is therefore a starting point, not an inventory** —
   see D1 and T2's In.
6. **Plan 264 carries a false premise about live code.** `docs/plans/264-...md:252-262` states that
   *"`_infer_time_step` returns one hour for fewer than two rows (`services/qc.py:40-47`)"*. That
   branch was **deleted** by 272: the function is now `infer_time_step` at `:40-61` and returns
   `None`. The same passage specifies a **raise** for the zero-rule case, which 272 D2 replaced with
   the `QC_UNCHECKED` status. 264 is `DRAFT` and touches live Swiss QC, so a reader acting on it
   would implement a superseded design against a deleted branch.
7. **Plan 272's own text describes a system that no longer exists.** Its Status section still
   asserts as measured fact that `QC_UNCHECKED` *"occurs in exactly three files … **No consumer
   accepts it**"* — four consumers now accept it (`services/input_quality.py:37`) — and carries a
   red headline that a zero-rule group *"disappears from every consumer"*, which Plan 316 closed.
   It also promises a `DeploymentConfig` flag that item 12 deliberately did not build
   (`272:1471`, `:1479`, and further sites — see D1).
8. ⚠️ **The bump interacts with an OPEN, UNOWNED question in Plan 314.** `314:98` asks whether
   T0's never-implemented `-norules` sentinel *"is superseded by the status, or still owed"*, and
   records that after 314's rollback statement the rewritten rows become indistinguishable again.
   A version bump changes what such a revert could identify afterwards. ⛔ **This plan does not
   answer 314's question** — it must not silently foreclose it either, so T2 records the
   interaction where 314's reader will meet it.

## Owner decisions

### D1 — how the stale-text sweep is bounded. **OPEN.**

§ (7) and § (5) are both enumerations, and **both of my counts came from a single grep pattern.**
The audit's count of the flag-promise sites (five) and mine (two) **disagree**, because we matched
different phrasings — which is itself the evidence for the hazard.

⛔ *This repo has already booked the lesson: "an enumeration by ONE grep pattern is not an
inventory", after a sweep missed three consumers a pattern could not match.*

| | option | cost |
|---|---|---|
| **(a)** ⭐ | **Sweep by VALUE and by CONCEPT**: for each claim, grep case-insensitively for the *thing* (the flag, the three-files claim, the consumer claim) in several spellings, and fix every hit found. The task reports the final count as a measurement, not as a target. | Slower; the number is unknown until done. Correct. |
| (b) | Fix the sites this plan lists and stop. | ⛔ Rejected in drafting: two independent counts already disagree, so a fixed list is known-incomplete before work starts. |

**Recommendation: (a).** The owner's confirmation matters because (a) means T4 has no pre-agreed
size, and a reviewer must accept a measured count rather than a checklist.

## Tasks

### T1 — Correct the WMO compliance row (audit item 1)

**Outcome.** The WMO-168 row cites the code it claims to evidence.

**In.**
- `docs/standards/wmo.md:187`: `:50` → **`:103`**, `:71` → **`:124`**, `:225` → **`:281`**, and the
  *Verified* date restamped to the date the check is actually re-run.
- 🔴 **Re-verify every OTHER `services/qc.py` line citation in that file at the same time.** § (1)
  is one row; 272's whole point was that this class hides. ⛔ *Do not fix only the row this plan
  names.*

**Out.** ⛔ Any change to what the row CLAIMS about WMO compliance — the claim is unchanged, only
its citations were wrong. ⛔ Other standards documents.

**Pre-change.** N/A — documentation. ⚠️ *But the line numbers must be read from the tree at
implementation time, not copied from § (1): this plan's own numbers go stale the moment `qc.py`
changes.*

**Verification.** Each cited line, opened in the tree, contains the symbol the row names. ⭐ Stated
as an operation a reviewer performs, not as a claim the implementer makes.

### T2 — Bump the observation QC rule version (audit item 2)

**Outcome.** A verdict produced by the post-272 selection logic is distinguishable, by stored value,
from one produced before it.

**In.**
- `services/qc.py:22` and `services/qc_datum.py:18-19` bumped to the next version, the three moving
  together so a row's version identifies one logic generation.
- Every site carrying an old value by literal, found by a **value sweep** (D1a) rather than from
  § (5)'s list — at least `tests/unit/flows/test_ingest_observations.py:344,370`.
- A line beside the constants saying what the bump marks, so the next reader knows what changed.
- ⚠️ **A note in Plan 314 beside `:98`** recording that the bump gives its open question a new fact:
  post-bump rows are identifiable by version as well as by status (§ 8). ⛔ *Recording only — this
  plan does not answer 314's question.*

**Out.** ⛔ **`services/forecast_qc.py` — the other `_RULE_VERSION`** (§ 4). ⛔ Rewriting any stored
row. ⛔ Any change to QC behaviour: this changes a label, nothing else. ⛔ Answering 314's sentinel
question.

**Pre-change.** A test asserting the DESIRED behaviour: **an observation checked by the current code
carries the new version**, which fails today because the constant still reads the old one.
⚠️ It must fail on the version value, not on a missing symbol.

**Verification.**
- The new value is what the ingest path stores, asserted through the flow rather than by reading the
  constant.
- 🔴 **`services/forecast_qc.py:22` is UNCHANGED** — asserted, because § (4) is the one way this task
  can do damage and nothing else would catch it.
- No test asserts an old value anywhere (the value sweep's result, reported as a count).

### T3 — Record Plan 264's cross-plan debt (audit item 3)

**Outcome.** A reader of Plan 264 is not sent to implement a superseded design against a deleted
branch.

**In.** At `264:252-262`: that `_infer_time_step`'s under-two-rows fallback **no longer exists**
(now `infer_time_step` → `timedelta | None`), and that T3's **raise** was replaced by 272 D2's
`QC_UNCHECKED` status. ⛔ *Correct the text in place — a note appended beneath wrong text leaves the
contradiction standing, which this repo has already paid three review rounds for.*

**Out.** ⛔ Re-scoping Plan 264, changing its status, or closing its decisions. This records a debt;
264 remains its owner's.

**Pre-change.** N/A — documentation.

**Verification.** 264 contains no citation of a branch that is absent from `services/qc.py`, and no
unqualified instruction to raise on zero rules.

### T4 — Make Plan 272 describe the system we actually run (audit item 4, D1)

**Outcome.** Plan 272 read cold does not raise a false alarm or promise unbuilt machinery.

**In.**
- The Status section's two false statements (§ 7) corrected **in place**, each saying what closed it
  (316 for the consumer claim; the `MODEL_INPUT_QC_STATUSES` set for the three-files claim).
- Every flag promise found by D1a's sweep, marked as **deliberately not built** per the owner's
  2026-09-23 decision — ⛔ *not deleted: the decision not to build it is part of the record.*
- A short line recording that triage sections A and B are **closed by 316/317/318**, section C's
  parks stand, and what this plan closes.

**Out.** ⛔ **Plan 272's `status:` field** — the owner sets it after this merges. ⛔ The `status:
READY` fields on merged plans 316/317/318 and the plan index's "pending implementation" line: same
class, different plans, and correcting them here would hide them in an unrelated diff.
⛔ Rewriting 272's history sections.

**Pre-change.** N/A — documentation.

**Verification.** ⭐ **A reader who knows nothing of this session can state, from 272 alone, which of
its items shipped and which did not.** The count of corrected flag sites is reported as measured.

## Explicitly out of scope

- **The DHM mask byte-identical test** (audit item 5) — a real test debt, deferred deliberately;
  it is a test to write, not a record to correct, and belongs with whoever next touches that script.
- **272's triage section C parks** — bounded inference lookback, the severity ranking and public
  filter (item 11), the compatibility release. Each needs a decision or a re-justification.
- **Plan 314's open decisions**, including the sentinel question T2 only records.
- **Plan 323's hourly rules** — the same symptom from the other side, already owned.

## A note on how this lands

The code change (T2) and the record corrections (T1/T3/T4) go in **one PR**, against the usual
"plans direct to `main`, PRs for code" convention. ⚠️ Stated rather than done quietly: the records
are the *evidence* for the version bump, and splitting them would put the reviewer's two halves in
different places. The owner merges, as always.

```json
{
  "phases": [
    {"phase": 1, "tasks": ["T1", "T3"], "parallel": true},
    {"phase": 2, "tasks": ["T2"], "parallel": false},
    {"phase": 3, "tasks": ["T4"], "parallel": false,
     "note": "last, because T4 must report what the earlier tasks actually closed"}
  ]
}
```
