---
status: READY
created: 2026-09-24
revised: 2026-09-24
plan: 324
title: Plan 272's last four items — a compliance row that points at nothing, and three records that describe a system we no longer run
scope: Close the four items Plan 272's own triage marked "DO NOW" or "UNCONDITIONALLY" and which no follow-on plan owns — the stale WMO evidence row, the un-bumped observation QC rule version, Plan 264's unrecorded cross-plan debt, and Plan 272's own text describing the pre-316/317/318 world. NOT the parked items in 272's triage section C, NOT the DHM mask byte-identical test (item 5, deliberately deferred), NOT Plan 272's own `status:` field (the owner closes that after this merges), NOT the `status: READY` fields on merged plans 316/317/318, NOT Plan 314's open decisions, NOT any change to QC behaviour.
depends_on: []
blocks: []
related: [101, 264, 272, 314, 315, 316, 317, 318, 323, 325]
open_decisions: []
source: 2026-09-24 — a full item-by-item audit of Plan 272 against `origin/main` at `8b68f6f8`, run because PR #297 had previously been merged believing it implemented 272 when it shipped 12 of 54 items. Every claim below was re-verified directly before drafting; each says how.
---

# Plan 324 — Plan 272's last four items

⚠️ **Plan number PROVISIONAL until the owner grants it.** 320-322 are held by a concurrent session.

## Status

**READY** — set by the orchestrator 2026-09-24, after **three** independent review rounds: 4 medium
+ 1 low, then 4 medium + 1 low again on the fold (every finding acknowledged in an appendix while
the task text still said the old thing), then **READY with no findings** on the corrected operative
text. D1 closed below. ⛔ *The first two rounds are why this is not "reviewed once"* and the orchestrator sets
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
3. ✅ **Bumping is safe — no PRODUCTION DECISION compares the value.** ⚠️ *The first draft said
   "nothing compares it". Tests do — store round-trip and preservation tests, and
   `test_ingest_observations_recheck.py:267`. The safety conclusion survives; the wording did not.*
   Searches across `src/`, `tests/`, `scripts/`, `alembic/` and SQL find **no branch, filter or
   equality test in production code**. The write sites are `flows/ingest_observations.py:533`,
   `services/onboarding.py:824`, and — ⛔ **omitted from the first draft** — the derived-observation
   writes at `flows/ingest_observations.py:687` and
   `services/calculated_station_onboarding.py:355`.
4. ⛔ **THE TRAP: there are TWO `_RULE_VERSION = "1.0"` constants.** `services/qc.py:22` is
   observation QC and in scope; **`services/forecast_qc.py:22` is FORECAST QC and is NOT**. A
   blanket edit or a careless `sed` hits both and silently re-versions every forecast QC flag.
5. **THREE tests assert versions by exact value** in `tests/unit/flows/test_ingest_observations.py`:
   `:352` (`"1.1-datum"`), `:385` (`"1.1-datum-skip"`) and `:456`. ⛔ *The first draft said two, at
   `:344`/`:370` — quoted from one grep without re-reading the file, which is the exact habit D1
   exists to stop. Corrected here rather than noted below.*
   🔴 **A FOURTH literal exists and is easy to miss: the stored version for non-water-level
   parameters comes from `services/qc_datum.py:25`, not from `qc.py`'s `_RULE_VERSION`.** Bumping
   three and not the fourth leaves discharge rows on the old value.
   ⚠️ **Not every old-value assertion moves.** `test_ingest_observations_recheck.py:250-268`
   deliberately seeds `"1.0"` to prove already-passed rows are left untouched — that is **evidence
   and must survive**. See T2's In.
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
   ⚠️ **But a bump labels a GENERATION GOING FORWARD and nothing more.** ⛔ It cannot retrospectively
   separate rows already stored under post-272 logic from pre-272 rows — they share a label — and
   after 314's status rewrite it cannot recover which rows were formerly unchecked either. ⇒ **This
   plan does not answer 314's question and does not weaken it**; T2 records both limits where 314's
   reader will meet them.

## Owner decisions

### D1 — how the stale-text sweep is bounded. **⚖️ CLOSED — orchestrator, 2026-09-24: (a).**

§ (7) and § (5) are both enumerations, and **both of my counts came from a single grep pattern.**
The audit's count of the flag-promise sites (five) and mine (two) **disagree**, because we matched
different phrasings — which is itself the evidence for the hazard.

⛔ *This repo has already booked the lesson: "an enumeration by ONE grep pattern is not an
inventory", after a sweep missed three consumers a pattern could not match.*

| | option | cost |
|---|---|---|
| **(a)** ⭐ | **Sweep by VALUE and by CONCEPT**: for each claim, grep case-insensitively for the *thing* (the flag, the three-files claim, the consumer claim) in several spellings, and fix every hit found. The task reports the final count as a measurement, not as a target. | Slower; the number is unknown until done. Correct. |
| (b) | Fix the sites this plan lists and stop. | ⛔ Rejected in drafting: two independent counts already disagree, so a fixed list is known-incomplete before work starts. |

**⚖️ Closed on (a) — sweep by value and by concept, report the count as a measurement.**

⚠️ *This was drafted as an owner decision and should not have been. The owner's response — "no need
to... put that into a plan" on the neighbouring question, and silence here — plus the fact that (b)
is known-incomplete before work starts, leaves exactly one defensible answer. Asking the owner to
choose between a right option and a self-evidently wrong one is offloading a judgement, not seeking
a decision.* ⇒ Closed by the orchestrator under its delegated sequencing authority; ⛔ the owner can
of course overturn it.

⇒ **T4 has no pre-agreed size**, and a reviewer must accept a measured count rather than tick a
checklist.

## Tasks

### T1 — Correct the WMO compliance row (audit item 1)

**Outcome.** The WMO-168 row cites the code it claims to evidence.

**In.**
- `docs/standards/wmo.md:187`: the three `services/qc.py` anchors repointed at
  `_apply_range_check`, `_apply_rate_of_change` and `Stage1QualityChecker`, **read from the tree
  after T2 has run**, and the *Verified* date restamped to the day the check is re-run.
  ⛔ *The numbers in § (1) (`:103`, `:124`, `:281`) were measured BEFORE T2 inserts its comment
  beside the constants, which shifts every line below it. Copying them is the defect this plan
  exists to fix, committed by the plan itself.*
- 🔴 **Re-verify every OTHER `services/qc.py` line citation in that file at the same time.** § (1)
  is one row; 272's whole point was that this class hides. ⛔ *Do not fix only the row this plan
  names.*

**Out.** ⛔ Any change to what the row CLAIMS about WMO compliance — the claim is unchanged, only
its citations into OUR CODE were wrong. ⛔ **Where the row points into the WMO publication** — that
is Plan 325, which found that none of the eleven compliance rows cites a chapter at all.
⛔ Other standards documents.

**Pre-change.** N/A — documentation. 🔴 **T1 RUNS AFTER T2** — see the phase graph. That is not a
preference: T2 edits `qc.py`, so anchors read before it are wrong by construction.

**Verification.** Each cited line, opened in the tree, contains the symbol the row names. ⭐ Stated
as an operation a reviewer performs, not as a claim the implementer makes.

### T2 — Bump the observation QC rule version (audit item 2)

**Outcome.** Observation QC verdicts written **from this bump onward** carry a version that
identifies the post-272 generation.

🔴 **What this does NOT do, stated in the Outcome because it was overclaimed once:** it does **not**
retrospectively separate rows already stored under post-272 logic from pre-272 rows — they keep the
same label — and after Plan 314's status rewrite it does **not** recover which rows were formerly
unchecked. ⛔ *The bump is a forward generation label, not a provenance repair.*

**In.**
- **All FOUR literals** moved together (§ 5): `services/qc.py:22`, `services/qc_datum.py:18`, `:19`
  and the non-water-level value at **`qc_datum.py:25`**.
- The **current-generation expectations** updated to match — the three assertions at
  `test_ingest_observations.py:352`, `:385`, `:456`.
  ⛔ **NOT "every site carrying an old value".** A test that deliberately seeds an old version to
  prove historical rows are untouched — `test_ingest_observations_recheck.py:250-268` — is
  **evidence and must survive**. ⇒ The implementer states, per site found, whether it is a
  current-generation expectation (update) or a historical fixture (keep).
- A line beside the constants saying what the bump marks.
- A note in Plan 314 beside `:98` recording that post-bump rows carry a generation label —
  **together with both limits from the Outcome**, so it cannot be read as answering 314's sentinel
  question.

**Out.** ⛔ **`services/forecast_qc.py`** (§ 4). ⛔ Rewriting any stored row. ⛔ Any change to QC
behaviour. ⛔ Answering 314's sentinel question. ⛔ Touching historical-version fixtures.
⛔ **Conflating the three different things called a version**: `QcFlag.rule_version`, the
rule-definition version, and the stored `Observation.qc_rule_version`. Frozen-sensor flags
deliberately carry the rule's own version (`qc.py:196`).

**Pre-change.** A test asserting the DESIRED behaviour: **an observation checked by the current code
carries the new version**. Written by changing an existing flow assertion to the new literal before
the production change, so it fails on the VALUE, not on a missing symbol. Cover the ordinary, datum
and datum-skip paths — they come from different literals.

**Verification.**
- The new value is what the ingest path stores on each of the three paths, asserted through the
  flow, not by reading the constant.
- 🔴 **Forecast QC is untouched**, asserted behaviourally: trigger one forecast QC failure, assert
  **exactly one** flag, and assert that flag's `rule_version` is still `"1.0"`. ⛔ *Not "the file is
  unchanged" — that is a diff check, not a test — and not an `all(...)` over possibly-empty flags,
  which passes vacuously on zero flags.*
- Every **current-generation** expectation carries the new value, and every historical fixture still
  carries its old one. Reported as two counts, not one.

### T3 — Record Plan 264's cross-plan debt (audit item 3)

**Outcome.** A reader of Plan 264 is not sent to implement a superseded design against a deleted
branch.

**In.** 🔴 **The WHOLE of Plan 264's T3 contract — `:252` through `:289`, its In and its
Verification included.** ⛔ *The first draft bounded this to `:252-262`, which would have left
executable instructions to raise standing at `:281-289` beneath a corrected introduction — the
"corrected note above wrong text" failure this repo has already paid three review rounds for, and
which this very plan then committed in its own first fold.*

Record: that `_infer_time_step`'s under-two-rows fallback **no longer exists** (now
`infer_time_step` → `timedelta | None`), and that T3's **raise** was replaced by 272 D2's
`QC_UNCHECKED` status. ⛔ **Correct the text in place**, including the verification clauses that
would otherwise still instruct someone to assert a raise.

**Out.** ⛔ Re-scoping Plan 264, changing its status, or closing its decisions. ⛔ **Its network-
dimension decisions**, which 272 did not touch. This records a debt; 264 remains its owner's.

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


---

## Changelog

**2026-09-24 — reviewed twice.** Round 1: NEEDS CHANGES, 4 medium + 1 low. Round 2, on the fold:
**NEEDS CHANGES again — every finding was acknowledged in an appended section while the operative
task text still said the old thing.** ⛔ *That is the "corrected note above wrong text" failure this
repo has already booked, committed here by the fold that was supposed to fix it. The appended
section has been deleted and the findings applied where the implementer actually reads:*

| finding | where it now lives |
|---|---|
| **M3** T2's comment shifts the lines T1 cites | the single phase graph (T2 first), and T1's Pre-change states it is not a preference. The superseded graph is **deleted**, not left alongside. |
| **M1** the sweep would destroy historical fixtures | T2's In distinguishes current-generation expectations from historical evidence, per site; § (5) names the surviving fixture |
| **M2** the bump cannot repair provenance | T2's **Outcome** states both limits; § (8) narrowed |
| **M4** 264's raise contract runs to `:289` | T3's In covers `:252-289` including its Verification |
| **L1** stale anchors, loose claim 3 | § (3) and § (5) corrected in place — three assertions at `:352`/`:385`/`:456`, a **fourth literal** at `qc_datum.py:25`, and "no production decision compares it" |

⚠️ **Two defects the second round found in the fold itself:** the forecast-QC guard as first written
required asserting `"1.0"`, contradicting the same task's sweep; and the plan carried **two phase
graphs** saying opposite things.

## ⚖️ Scope narrowed by the owner, 2026-09-24

Owner: *"data quality checks should point at the correct locations in the wmo documents. that may
require a separate plan."*

⇒ **T1 corrects only where the row points INTO OUR CODE.** Making the row cite the WMO clause it
claims conformance with is **[Plan 325](325-compliance-rows-must-cite-the-wmo-clause.md)**, which
also found that **none of the eleven compliance rows cites a clause, section or page** — the
standing evidence rule disciplines the code half of the claim and is silent on the WMO half.

⛔ *This plan must not be read as making that row compliant. It makes one half of it accurate.*

```json
{
  "phases": [
    {"phase": 1, "tasks": ["T2"], "parallel": false,
     "note": "FIRST — M3: T2's comment shifts the line numbers T1 cites"},
    {"phase": 2, "tasks": ["T1", "T3"], "parallel": true,
     "note": "T1 reads its anchors from the post-T2 tree"},
    {"phase": 3, "tasks": ["T4"], "parallel": false}
  ]
}
```
