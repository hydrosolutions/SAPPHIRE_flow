---
status: DRAFT
created: 2026-09-28
plan: 500
title: Plan 399's record still asserts seven defects the code no longer has
scope: Reconcile Plan 399's OWN document with the code merged by Plan 405 (#323) — its Status tables, its `status:` frontmatter, and the in-body claims the merge falsified — leaving the staging run as its one genuine remainder. NOT re-verifying 405's work (twelve independent review passes did that), NOT the staging run itself (399 owns it), NOT any code change.
depends_on: []
blocks: []
related: [399, 405]
open_decisions: [D1]
source: 2026-09-28 — found while auditing a stale local checkout, not by a status sweep. Plan 405 merged 2026-09-27 (#323) and closed seven of 399's eight remaining items; 399's document still describes all seven in the PRESENT TENSE. Every claim below was measured against `origin/main` today.
---

# Plan 500 — 399's record against the merged code

## Status

**DRAFT.** ⛔ No implementation until an independent review and a READY flip.

## Why this plan exists

Plan 399 reads, on `main`, today:

- `status: PARTIALLY_IMPLEMENTED   # four code gaps + two missing test sets`
- *"🔴 **WHAT REMAINS — EIGHT items, not one.**"*
- *"🔴 **Nothing compares them.** … a dead parameter."* (A1)
- *"🔴 **The path is NEVER recorded, for any donor, ever.**"* (A3)
- *"🔴 **No test touches `ForecastInterfaceAdapter.retrain` or the shim's `retrain`.**"* (B1)

⛔ **All seven are fixed and on `main`.** An agent opening 399 reads a list of live defects, and the
cheapest correct response to that document is to go and re-implement finished work.

⚖️ **This is the same hazard class this repo has already booked twice** — a plan whose `status:` or body
outlives its work ([[feedback_plan_status_is_a_machine_field]]; the 2026-09-18 audit; Plan 319, found
and fixed 2026-09-28 in the same sweep that found this). ⭐ *What is new here is that the stale text is
not the status field but the EVIDENCE TABLES, which read as measurements and are therefore more
convincing than a status label.*

## What is measured

`origin/main` at `81bc9861`, 2026-09-28. Each item probed for its closing artifact:

| item | 399's live claim | measured on `main` |
|---|---|---|
| A1 | "Nothing compares them … a dead parameter … no test of `resolve_donor_config` at all" | ✅ `_refuse_changed_template` exists (×3 refs); `TestComparingTheInstalledTemplate` exists |
| A2 | the retrain-of-a-retrain case "CRASHES today" | ✅ the inherited branch derives a per-donor reason ("produced by SAP3 (a retrain)") |
| A3 | "The path is NEVER recorded, for any donor, ever" | ✅ the flow passes `getattr(model, "config_path", None)` |
| A4 | a constant reason for every donor, "no inspection" | ✅ `resolve_donor_params` classifies per donor state |
| B1 | "No test touches" either `retrain`; the stand-in "re-implements the capability check" | ✅ adapter and shim retrain tests exist; ⛔ **`class _WrapperDefiningRetrainUnconditionally` occurs ZERO times** — the one surviving mention is a comment recording its removal |
| B2 | "Only the training-flow half shipped" | ✅ `TestOnboardingServiceSites` exists (all four sites) |
| B3 | the flow-level resolver red "Never written" | ✅ `tests/unit/flows/test_resolver_wiring_through_discovery.py` exists |

🔑 **The staging run is the ONE item that is still genuinely open**, and 399 keeps it.

⚠️ **Two kinds of stale text, and they must be treated differently:**

1. **LIVE claims** — the Status tables (`:28-50`) and the § 13 decision-table cell at `:247`
   ("⚠️ **And the reachable case CRASHES today — Status A2.**"). These assert the present and are now
   false.
2. **CHANGELOG entries** (`:842`, `:853`) recording what a review found *at the time*. ⛔ *These are
   legitimate history and must NOT be rewritten* — a dated "here is what we found" is true as a record
   even when the defect is gone. ⭐ *Distinguishing these two is the whole care in this task:
   over-correcting destroys the audit trail, under-correcting leaves a live falsehood.*

## Owner decisions

### D1 — after reconciliation 399 has exactly ONE item left, and it is an OPERATIONAL run. Where does it live? **OPEN.**

| | option | cost |
|---|---|---|
| **(a)** ⭐ | **399 stays `PARTIALLY_IMPLEMENTED`, owning the staging run.** | Smallest change, and the run's context (why fine-tune, which forcing, what `cmal_small` was trained on) already lives there. ⚠️ A plan stays open indefinitely on an item that cannot be done while the mini is unreachable. |
| (b) | Move the staging run to its own short plan; 399 becomes `COMPLETE` and archives. | A clean close for 399. ⛔ *Churn: a new plan whose entire content is "run this once", plus two index edits, to retire a label.* |

**Recommendation: (a).** ⭐ *The item is real and 399 is where its reasoning lives. ⚠️ But state in the
status line WHY it is still open — "blocked on the staging host, not on code" — so the next reader does
not go looking for missing implementation, which is exactly the confusion this plan exists to remove.*

## Tasks

### T1 — Reconcile the Status section against the measured table (D1)

**Outcome.** 399's Status section describes what is true on `main`: seven items closed with their
evidence, one open.

**In.**
- Each of A1-A4 and B1-B3 marked closed, **naming the closing artifact and #323** — ⛔ *not a blanket
  "405 fixed these": the point of the tables was per-item evidence, and the replacement owes the same.*
- The "EIGHT items" header corrected to the one remaining, with D1's answer on where it lives.
- 🔴 **The § 13 cell at `:247` ("the reachable case CRASHES today")** — it is outside the Status
  section and would survive a Status-only edit. ⛔ *Fixing the tables and leaving this is the one-site
  sweep this plan's own source found in 405 four times.*

**Out.** ⛔ Rewriting the CHANGELOG entries at `:842`/`:853` — dated findings are history.
⛔ Re-verifying 405's work. ⛔ Any code change. ⛔ Marking 399 `COMPLETE` (the staging run is open).

**Pre-change.** 🔴 **A grep, recorded in the task, for every PRESENT-TENSE defect claim about the
seven** — the claim texts from § What is measured, plus the concept's other spellings
("dead parameter", "is NEVER recorded", "No test touches", "CRASHES today"). ⚠️ *A count without the
enumeration beside it is a claim, not a measurement*
([[feedback_measure_dont_reason_about_operational_numbers]]).

**Verification.**
- Every row of § What is measured has a corresponding closed entry in 399 naming its evidence.
- 🔴 **The grep from Pre-change returns only changelog lines and explicit "was" retractions** — no live
  present-tense claim about the seven survives, anywhere in the file.
- ⛔ **A reader can answer "what is left in 399?" correctly from the Status section alone**, and the
  answer is the staging run.

### T2 — Make the `status:` line say what is actually left

**Outcome.** The frontmatter stops advertising code gaps that do not exist.

**In.** `status: PARTIALLY_IMPLEMENTED   # four code gaps + two missing test sets` → a comment naming
the ONE remaining item and that it is blocked on the host, not on code (D1a). ⚠️ *The status VALUE
stays `PARTIALLY_IMPLEMENTED` — the plan genuinely is not complete.*

**Out.** ⛔ `COMPLETE`, and ⛔ archiving: both would be the mirror hazard, a plan claiming done work
that has not run (⚖️ the same error the index records against Plan 273 in the other direction).

**Pre-change.** N/A — a two-line frontmatter edit whose correctness T1's verification already covers.

**Verification.** `scripts/check_readiness.py` still refuses 399 as not implementable, and the status
comment names the staging run.

### T3 — Correct the index entry to match

**Outcome.** `docs/plans/README.md` and 399 agree.

**In.** 399's index entry already says seven are closed (edited 2026-09-27 when 405 archived). ⚠️
**Verify rather than assume** — re-read it against T1's result and fix any divergence.

**Out.** ⛔ Touching other plans' entries.

**Pre-change.** N/A.

**Verification.** The index and 399's Status agree on the count and on which item remains; the three
tests that read the plan index pass.

## Explicitly out of scope

- **The staging run itself** — 399's, and blocked on the mini being reachable.
- **Re-verifying Plan 405** — twelve independent review passes did that; this plan trusts `main`.
- **Whether a fine-tuned `cmal_small` is BETTER** — no plan owns the skill comparison. ⚠️ *Named because
  it is the real open question behind the whole retrain effort, and reconciling a document does not
  advance it.*
- **The deeper limitation**: fine-tuning on Swiss forcing addresses the CLIMATOLOGY mismatch, not the
  model's behaviour on ICON's forecast errors — training's future leg is reanalysis either way.

```json
{
  "phases": [
    {"phase": 1, "tasks": ["T1", "T2"], "parallel": false, "note": "same file; T2's comment depends on T1's reconciled count", "decision": "D1 CLOSED"},
    {"phase": 2, "tasks": ["T3"], "parallel": false, "note": "index last — it must agree with the reconciled plan, not lead it"}
  ]
}
```
