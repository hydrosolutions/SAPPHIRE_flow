---
status: DRAFT
created: 2026-09-28
plan: 500
title: Plan 399's record still asserts seven defects the code no longer has
scope: Reconcile Plan 399's OWN document with the code merged by Plan 405 (#323) — its Status tables, its `status:` frontmatter, and the in-body claims the merge falsified — leaving the staging run as its one genuine remainder. NOT re-verifying 405's work (it was independently reviewed after every fold), NOT the staging run itself (399 owns it), NOT any code change. Also corrects 399's non-canonical `status:` value and the live stale claims in its INDEX entry.
depends_on: []
blocks: []
related: [399, 405]
open_decisions: [D1]
source: 2026-09-28 — found while auditing a stale local checkout; the same audit found and fixed Plan 319's stale status. Plan 405 merged 2026-09-27 (#323) and closed seven of 399's eight remaining items; 399's document still describes all seven in the PRESENT TENSE. Every claim below was measured against `origin/main` today.
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

🔬 **Direct evidence of how the staleness arose, stronger than inference.** Commit `18109182`
(2026-09-27) is titled *"docs(plans,405): COMPLETE and archived — **and 399 no longer claims eight open
items**"*. ⛔ **Its diff touches only `docs/plans/README.md` and the 405 file: 399 was never edited**
(measured). ⇒ The index was corrected, the plan was not, and the commit message asserted otherwise.
*That false claim is mine, in a commit already on `main`; it cannot be amended, so it is recorded
here.*

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
| B2 | "Only the training-flow half shipped" | ✅ **TWO classes in TWO files, two sites each**: `tests/unit/services/test_onboarding_passes_no_config.py::TestOnboardingServiceSites` (2 tests, the positional sites) and `tests/unit/flows/test_onboarding_passes_no_config.py::TestOnboardingFlowSites` (2 tests, the keyword sites). ⛔ *First cited as "`TestOnboardingServiceSites` … all four sites" — FALSE, it holds two. Measured: 2 and 2. A count again, and T1 requires naming the closing artifact, so the false attribution would have been copied into 399.* |
| B3 | the flow-level resolver red "Never written" | ✅ `tests/unit/flows/test_resolver_wiring_through_discovery.py` exists |

🔑 **399 has ONE open OUTCOME — the staging run — with deployment as its PRECONDITION, not a second
outcome.** ⛔ *First written as "the ONE item that is still genuinely open", which ignores `399:60`:
"⚠️ **Also pending: the mini runs 0.1.986 and `main` is at 0.1.994** — this merge is NOT deployed."
Both reviewers caught it. The run cannot happen on a host running a build without the code, so
deployment is a precondition — but the plan must SAY that rather than silently absorb it.*
⚠️ **And `399:60`'s figures are themselves stale**: `main` is at **0.1.1016**, not 0.1.994 (measured).
⇒ T1 must fix that line too; ⛔ *no grep term in the original Pre-change list would have found it.*

⚠️ **Two kinds of stale text, and they must be treated differently:**

1. **LIVE claims.** ⛔ *First listed as just "the Status tables (`:28-50`) and the § 13 cell at
   `:247`" — INCOMPLETE. A reviewer enumerated the rest, and two of them sit outside `:28-50` where a
   Status-only edit would never reach:*
   | line | live claim | status |
   |---|---|---|
   | `:34, :36, :48-50` | the A1/A3/B1/B2/B3 table cells | now false |
   | `:39-43` | "A2's consequence is a **CRASH, demonstrated**" | now false |
   | `:60` | "Also pending … NOT deployed", with stale version figures | pending, but the numbers are wrong |
   | `:67-70` | "Neither the adapter's nor the shim's `retrain` **is touched by any test** … leaves 723 tests PASSING" | now false, and OUTSIDE `:28-50` |
   | `:194` | "**group training is BROKEN TODAY**" | now false — the resolver is wired (`flows/train_models.py:661`). ⚠️ *Not one of "the seven", and the INDEX has already historicised it while 399's body has not. The most consequential falsehood in the file: it asserts a standing production blocker.* |
   | `:6` (frontmatter `scope:`) | "a channel for the fine-tuning config (**there is none today**)" | now false |
   | `:86` | "the round-5 fold itself is UNREVIEWED" | the reviews it names have since happened |
2. **CHANGELOG entries** (`:842`, `:853`) recording what a review found *at the time*. ⛔ *These are
   legitimate history and must NOT be rewritten* — a dated "here is what we found" is true as a record
   even when the defect is gone. ⭐ *Distinguishing these two is the whole care in this task:
   over-correcting destroys the audit trail, under-correcting leaves a live falsehood.*

## Owner decisions

### D1 — after reconciliation 399 has exactly ONE item left, and it is an OPERATIONAL run. Where does it live? **OPEN.**

🔴 **`PARTIALLY_IMPLEMENTED` IS NOT A CANONICAL STATUS.** The seven canonical values are `DRAFT`,
`READY`, `BLOCKED`, `DEFERRED`, `PARTIAL`, `SUPERSEDED`, `COMPLETE` (`docs/plans/README.md:19-20`,
`docs/workflow.md:80-81`, codified in `tests/unit/tooling/test_workflow_policy_coherence.py:15-23`), and
**399 is the only plan in the corpus using the invented one** (measured: 1 file). ⛔ *An earlier version
of this task said "the status VALUE stays `PARTIALLY_IMPLEMENTED`" — a plan premised on
[[feedback_plan_status_is_a_machine_field]] instructing the preservation of a non-conforming machine
value. Both reviewers caught it; I had read that convention list the same day, fixing Plan 319.*

| | option | cost |
|---|---|---|
| **(c)** ⭐ | **`status: BLOCKED`** — "cannot proceed until a named blocker changes" (`docs/workflow.md:85`), the blocker being the staging host. | ⭐ *(a)'s minimal churn, and it makes the blocked-ness MACHINE-READABLE instead of prose in a comment. Fixes the non-canonical value at the same time.* |
| (a) | `status: PARTIAL`, owning the staging run, with the blocker in a comment. | Canonical and honest. ⚠️ The blocker stays prose a tool cannot read. |
| (b) | Move the staging run to its own plan; 399 becomes `COMPLETE` and archives. | A clean close. ⛔ *Churn: a new plan whose content is "run this once", plus index edits, to retire a label.* |

**Recommendation: (c) `BLOCKED`.** ⭐ *Says the true thing in the field built for it.* ⚠️ **But the
blocker must be re-measured, not inherited**: "the mini is unreachable" rests on a dated report
(`399:52`) and on the owner's 2026-09-27 remark, not on a probe at implementation time.

## Tasks

### T1 — Reconcile the Status section against the measured table (D1)

**Outcome.** 399's Status section describes what is true on `main`: seven items closed with their
evidence, one open.

**In.**
- Each of A1-A4 and B1-B3 marked closed, **naming the closing artifact and #323** — ⛔ *not a blanket
  "405 fixed these": the point of the tables was per-item evidence, and the replacement owes the same.*
- The "EIGHT items" header corrected to the one remaining, with D1's answer on where it lives.
- 🔴 **Every live site in the table above, not only the Status tables** — `:39-43`, `:60` (including
  its stale version figures), `:67-70`, `:194`, `:6`, `:86`, and the § 13 cell at `:247`. ⛔ *Four of
  those sit OUTSIDE `:28-50` and would survive a Status-only edit — the one-site sweep this plan's own
  source found four times in 405, and which the first draft of this task then committed.*
- ⚖️ **`:194` ("group training is BROKEN TODAY") is IN SCOPE, decided.** It is not one of "the seven",
  but it asserts a standing production blocker that #314 removed, and the INDEX has already
  historicised it while 399's body has not. ⛔ *Declining it would leave the file's most consequential
  falsehood in place while the task claims to remove exactly that hazard.*

**Out.** ⛔ Rewriting the CHANGELOG entries at `:842`/`:853` — dated findings are history.
⛔ Re-verifying 405's work. ⛔ Any code change. ⛔ Marking 399 `COMPLETE` (the staging run is open).

**Pre-change.** 🔴 **A WHOLE-FILE READ of 399, not a term list.** ⛔ *The first version specified a
grep for four spellings ("dead parameter", "is NEVER recorded", "No test touches", "CRASHES today") and
promised Verification that "no live claim survives anywhere in the file". A reviewer measured that the
list MISSES `:39` ("is a CRASH, demonstrated" — not "CRASHES today") and `:68` ("is touched by any
test" — not "No test touches"), so the specified sweep could not discharge its own promise.* ⇒ 399 is
868 lines; read all of it, classify every 🔴/⚠️ claim as live-and-true, live-and-false, or dated
history, and record the classification. ⚠️ *A term list finds the spellings you already thought of;
that is the failure this plan was written about* ([[feedback_sweep_by_value_not_by_site]]).

**Verification.**
- Every row of § What is measured has a corresponding closed entry in 399 naming its evidence.
- 🔴 **The grep from Pre-change returns only changelog lines and explicit "was" retractions** — no live
  present-tense claim about the seven survives, anywhere in the file.
- ⛔ **A reader can answer "what is left in 399?" correctly from the Status section alone**, and the
  answer is the staging run.

### T2 — Make the `status:` line say what is actually left

**Outcome.** The frontmatter stops advertising code gaps that do not exist.

**In.** `status: PARTIALLY_IMPLEMENTED   # four code gaps + two missing test sets` → **D1's canonical
value** (`BLOCKED` recommended), with a comment naming the staging run and its precondition
(deployment). ⛔ *The value MUST become canonical: `PARTIALLY_IMPLEMENTED` is in no allowed list and
399 is the only plan using it.*

**Out.** ⛔ `COMPLETE`, and ⛔ archiving: both would be the mirror hazard, a plan claiming done work
that has not run (⚖️ the same error the index records against Plan 273 in the other direction).

**Pre-change.** N/A — a two-line frontmatter edit whose correctness T1's verification already covers.

**Verification.** 🔴 **399's status value is one of the seven canonical strings** — asserted against
the list in `docs/plans/README.md`, not merely "not READY". ⛔ *`scripts/check_readiness.py` refuses
any string that is not exactly `READY`, so it passes for a typo and CANNOT detect non-canonicality —
an earlier version of this bullet cited it and was therefore vacuous.* And the status comment names
the staging run and its deployment precondition.

### T3 — Correct the index entry to match

**Outcome.** `docs/plans/README.md` and 399 agree.

**In.** 399's index entry says seven are closed — ⛔ **and then contradicts itself**: `README:694`
still reads *"🔴 A retrain-of-a-retrain **currently** CRASHES after storing the artifact."* ⚠️ *Named
concretely because "verify rather than assume" is an instruction to go looking; this is the divergence
that demonstrably exists.* Also fix the stale deployment figure in the same entry (`README:695`,
"the mini runs 0.1.986" — true of the mini, but paired with a `main` version that has moved).

**Out.** ⛔ Touching other plans' entries.

**Pre-change.** A grep of `README.md` (900+ lines) for live claims about 399's seven — ⛔ *the first
version specified none, making T3 exploratory rather than executable.*

**Verification.** The index and 399's Status agree on what remains, and `README:694`'s "currently
CRASHES" is gone. ⛔ *NOT "the three tests that read the plan index pass" — **ONE** test reads it
(`tests/unit/tooling/test_workflow_policy_coherence.py`), it was miscounted as three, and it reads
only the slice between `## Status convention` and `## Archived by` — i.e. NOT the region T3 edits. It
is therefore no evidence for this task at all.*

## Explicitly out of scope

- **The staging run itself** — 399's, and blocked on the mini being reachable.
- **Re-verifying Plan 405** — it was independently reviewed after every fold; ⛔ *an earlier version said "twelve independent review passes", which is the figure 405's changelog records for ONE task's loop ("six rounds, twelve reviewer passes"), not a plan-wide total. This plan trusts `main` and cites the measurements in § What is measured instead.*
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
