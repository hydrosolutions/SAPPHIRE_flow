---
status: DRAFT
created: 2026-09-26
plan: 405
title: Four warm-start requirements that never shipped, and two boundaries nothing tests
scope: Close the gaps a post-merge review found in Plan 399's shipped code — the missing changed-template refusal, the never-recorded donor config path, the un-inspected donor params, and the retrain-of-a-retrain crash — plus the two verification bullets 399 wrote and never implemented. NOT Plan 399's staging run (it stays there, orchestrator-gated). NOT new capability of any kind. NOT the FI contract changes in fi-issue 004. NOT the fine-tuning strategy surface (owner: opaque config for v1).
depends_on: [399]
blocks: []
related: [262, 399]
open_decisions: [D1]
source: 2026-09-26 — two independent reviews of Plan 399's MERGED code (PR #314) against the plan. Every item below is a thing 399 asserts and the code does not do, measured at `7aec753b`.
---

# Plan 405 — the warm-start gaps 399 asserted and did not ship

⚠️ **Plan number 405 PROVISIONAL until the owner grants it.**
⛔ *Drafted as 400. **400 was NOT free** — it belongs to `400-infer-cadence-from-recent-readings.md`,
granted by the owner 2026-09-25. My check was invalid: `ls docs/plans/400* docs/plans/archive/400*`
aborts in zsh when EITHER glob has no match, so the `|| echo "FREE"` fallback fired while the first
glob was matching. 405 was confirmed free three independent ways.*

## Status

**DRAFT.** ⛔ No implementation until an independent review and a READY flip.
**D1 must be answered first** — it changes what A4 means.

## Why this plan exists

Plan 399 merged as PR #314 and works: a fine-tune runs, is stored without promoting, and records its
donor. ⭐ *That part is verified, including against a real database.*

🔴 **But a post-merge review — the first able to check 399 against the code rather than against
itself — found four requirements 399 ASSERTS that the code does not do, and two verification bullets
399 wrote and never implemented.** One of the four is a crash. 399's status block has been corrected
to say so; this plan closes them.

⛔ **Nothing here is new capability.** Every item is 399 finishing what it claimed.

## What is measured

`origin/main` at `7aec753b` (PR #314 merged as `dd75a963`), 2026-09-26.

1. 🔴 **The changed-template refusal does not exist.** 399 requires: *"the vendored config differs
   from the donor's recorded hash ⇒ REFUSED, naming both hashes."*
   `store/model_artifact_warm_start.py:123` accepts `installed_config_sha256`, threads it at `:205`
   and `:211`, and **never reads it** — the body references only `installed_config_path` and
   `provenance.config_hash`. Its own docstring defers: *"a mismatch is a refusal the caller makes"*
   — and `flows/train_models.py:188-209` makes none.
2. 🔴 **`resolve_donor_config` has NO test.** `grep -rn resolve_donor_config tests/` finds only two
   hand-written fakes (`tests/unit/flows/test_train_models.py:1482,1528`) that return a fixed tuple.
3. 🔴 **The donor config path is never recorded, for any donor, ever.**
   `flows/train_models.py:191` hardcodes `installed_config_path=None`, so the resolver always takes
   its no-path branch and `base_config_path` is **always NULL**. ⚠️ *The path is trivially available —
   `_shim.py:598-600` computes the donor hash FROM it, and the flow already reads `model.config_hash`
   on the line above.*
4. 🔴 **Donor params are not inspected.** 399's T4 says *"INSPECT this donor's provenance before
   concluding NULL."* `flows/train_models.py:202-208` writes a **constant** reason for every donor,
   inspecting nothing. ⛔ *And that constant — "SAP3 has never recorded training params for any
   artifact" — became FALSE when #314 merged: a retrained donor's `run_config` IS recorded in its own
   warm-start row.*
5. 🔴 **A retrain-of-a-retrain CRASHES — demonstrated, not argued.** Resolving a retrained donor
   returns `(path=None, sha=<hash>, reason=None)`: `:167-172` carries the inherited path and hash
   forward and **drops `base_config_unknown_reason`**. `WarmStartRecord.__post_init__` then raises
   *"base_config_path is NULL without a reason"*.
   ⛔ **It raises AFTER the new artifact is stored** (`flows/train_models.py` records provenance
   after `_store_artifact_task`), leaving a saved model with no provenance and a failed run.
   ⚠️ *D1 of 399 explicitly permits a retrain of a retrain, so this is a supported path.*
6. 🔴 **Neither retrain boundary is tested.** No test touches
   `adapters/forecast_interface.py::ForecastInterfaceAdapter.retrain` or
   `models/aquacast/_shim.py::AquacastShim.retrain`. **Measured: replacing the adapter's `retrain`
   body with `raise AssertionError` leaves 723 tests PASSING.** ⚠️ *The shim is doubly unexercised —
   the local venv has no `aquacast` extra (see the merge commit).*
7. 🔴 **399's wrapped-adapter test uses a stand-in, not the adapter.**
   `tests/unit/services/test_training.py:492-522` defines
   `_WrapperDefiningRetrainUnconditionally` which **re-implements `supports_warm_start` in its own
   body**. ⛔ *399's changelog claims all three stand-in tests were "deleted and replaced". Two were.*
8. 🔴 **399's two-surface no-config assertion was never written.** T2 required asserting the
   unchanged case on the training flow **AND** the four onboarding sites. Only the training-flow half
   exists (`tests/unit/flows/test_train_models.py`); nothing in
   `tests/unit/flows/test_onboard_model_flow.py` or `tests/unit/services/test_model_onboarding.py`
   asserts they still pass `{}`. ⚠️ *That bullet existed to prevent exactly this divergence.*
9. ⚠️ **The § 11 resolver wiring — 399's headline blocker — is untested.** The wiring is real
   (`flows/train_models.py:598-605`) but every retrain test injects a station-scoped fake straight
   into the flow; none goes through `discover_models()` / `adapt_if_fi`. ⛔ *Deleting the block fails
   no test, and the thing tests could most usefully have de-risked before the staging run is the one
   thing they do not cover.*

## Owner decisions

### D1 — what does "inspect the donor's params" MEAN now? **OPEN.**

§ 4 measured that 399's constant reason is already false. Two donor classes now exist:

| | option | cost |
|---|---|---|
| **(a)** ⭐ | **Resolve params the same way config is resolved**: a retrained donor's own warm-start row carries its `run_config`, so record THAT as the donor's params. An imported donor stays UNKNOWN with an accurate reason. | Symmetric with config, and makes the second generation genuinely traceable. ⚠️ A `run_config` is not a params FILE, so either the column's meaning widens or it gains a sibling. |
| (b) | Keep NULL for every donor, but fix the reason to be true per class. | Smallest. ⛔ *Throws away provenance we now demonstrably hold, for a model lineage the owner has said they want traceable.* |

**Recommendation: (a).** ⚠️ *It is the difference between "we recorded that we do not know" and "we
know and did not write it down" — and § 4's constant already claims the former while the latter is
true.*

## Tasks

### T1 — Stop the crash, and carry the reason forward (§ 5)

**Outcome.** A retrain of a retrain completes and records accurate provenance.

**In.** `resolve_donor_config`'s inherited branch returns the inherited
`base_config_unknown_reason` rather than `None`. ⚠️ **And provenance is written BEFORE or ATOMICALLY
WITH the artifact store**, or the failure mode stays "artifact saved, provenance lost" — ⛔ *stating
which, because § 5's damage came from the ordering, not only the dropped field.*

**Out.** ⛔ Changing what a FIRST-generation retrain records. ⛔ Relaxing `WarmStartRecord`'s
invariant — the unexplained NULL it rejects is a real defect, and rejecting it is correct.

**Pre-change.** 🔴 **A RED test of the PRODUCTION-SHAPED chain**: generation 0 imported → 1 retrain →
2 retrain, through the flow, not by calling the helper. ⚠️ *It must fail with
`"base_config_path is NULL without a reason"` — that exact raise is the defect.*

**Verification.**
- Generation 2 completes and its record resolves to generation 1.
- 🔴 **Generation 3 also completes** — ⛔ *a fix that only survives one more generation is a
  postponement; the reason must propagate indefinitely.*
- 🔴 **If provenance fails, no orphaned artifact remains** — asserted, since § 5's real damage was
  the ordering.

### T2 — Record the donor's config path, and refuse a changed template (§§ 1, 3)

**Outcome.** The donor's config identity is recorded, and a retrain from a template that no longer
matches the donor is refused.

**In.**
- The flow passes the **real** installed config path (it already reads `model.config_hash` beside it).
- `resolve_donor_config` **compares** `installed_config_sha256` with the donor's recorded hash, and
  the refusal happens **BEFORE retraining**, naming both hashes.
- ⛔ *`installed_config_sha256` stops being a dead parameter, or it is removed — not left accepted
  and ignored.*

**Out.** ⛔ Hashing today's template as the donor's identity (399 § 13's trap). ⛔ Refusing when the
donor's hash is genuinely unknown — that is a NULL-with-reason, not a mismatch.

**Pre-change.** A RED test: **a donor whose recorded hash differs from the installed template's is
refused, before the model is called.** ⚠️ *Assert the model was NOT invoked — "an error was raised"
would also pass on a refusal that happens too late.*

**Verification.**
- Matching hashes ⇒ the path and hash are both recorded (no longer NULL).
- Mismatched ⇒ refused, both hashes named, **model not called**.
- Unknown donor hash ⇒ NULL with a reason, **not** a refusal.
- 🔴 **`resolve_donor_config` gains direct tests** (§ 2) — ⛔ *it currently has none at all.*

### T3 — Test the two retrain boundaries (§§ 6, 7)

**Outcome.** The adapter's and shim's `retrain` are exercised, and the capability check is tested
against the REAL adapter.

**In.**
- A test that a non-default config reaches the inner model through
  `ForecastInterfaceAdapter.retrain`, and through the shim's.
- 🔴 **The wrapped-adapter refusal test rewritten against the real `ForecastInterfaceAdapter`**,
  replacing the stand-in that re-implements the check (§ 7).
- ⚠️ **State how the shim is exercised** given the local venv lacks `aquacast` — either install the
  extra or mark it CI-only, but ⛔ *not silently unexercised, which is how § 6 happened.*

**Out.** ⛔ Changing the adapter's or shim's behaviour. ⛔ Weakening the inner-model check.

**Pre-change.** 🔴 **Gut each `retrain` body with `raise AssertionError` and confirm a named test
fails.** ⚠️ *Today 723 tests pass with the adapter's gutted — that measurement IS the red.*

**Verification.**
- Each boundary's `retrain` has a test that fails when its body is gutted.
- An FI model without `retrain`, wrapped in the **real** adapter, is refused.

### T4 — Assert onboarding's unchanged config (§ 8)

**Outcome.** The four onboarding sites are proven to still pass an empty config.

**In.** Assertions on `flows/onboard_model.py:368,375` and
`services/model_onboarding.py:1453,1457`. ⛔ *The two synthetic smoke-test sites are out of scope by
399 § 15 — they have no caller.*

**Out.** ⛔ Giving onboarding a config channel. ⛔ Changing onboarding behaviour at all.

**Pre-change.** N/A — this asserts existing behaviour. ⚠️ *So it cannot be red; state that rather
than inventing a red.*

**Verification.** Each of the four sites asserted, individually. ⛔ *One test covering "onboarding"
would pass on a single site and prove nothing about the rest.*

### T5 — Test the resolver wiring through discovery (§ 9)

**Outcome.** 399's headline blocker is covered before the staging run depends on it.

**In.** A flow-level test through `discover_models()` / `adapt_if_fi`, as 399's T3 required.

**Out.** ⛔ Changing the wiring.

**Pre-change.** 🔴 **Delete the wiring block and confirm the new test fails.** ⚠️ *Today nothing
does.*

**Verification.** The test fails with the resolver error when the wiring is removed.

### T6 — Donor params, per D1

**Outcome.** What a donor was trained with is recorded accurately, or its absence is explained truly.

**In.** D1's answer, replacing the constant reason at `flows/train_models.py:202-208`. ⛔ *Whatever
D1 decides, the recorded reason must be TRUE of the donor in hand — § 4's is already false.*

**Out.** ⛔ Inventing a params file where none exists.

**Pre-change.** A RED test: **a retrained donor's recorded params are not the constant string.**

**Verification.** An imported donor and a retrained donor produce DIFFERENT, accurate records —
asserted both ways.

## Explicitly out of scope

- **Plan 399's staging run.** ⛔ Stays with 399, orchestrator-gated. ⚠️ *T5 is what makes that run
  less of a leap, but it does not replace it.*
- **fi-issue 004** — a typed training failure and FI-side parent identity. Still drafted, not filed.
- **The fine-tuning strategy surface** — ⚖️ owner: opaque config for v1.
- **Whether a fine-tuned model is BETTER** — skill comparison, and still nobody's plan.

```json
{
  "phases": [
    {"phase": 1, "tasks": ["T1"], "parallel": false,
     "note": "the crash first — it is the only item that loses data, and it makes a supported path unusable"},
    {"phase": 2, "tasks": ["T2", "T3"], "parallel": true,
     "note": "independent: T2 is the resolver/flow, T3 is the adapter and shim"},
    {"phase": 3, "tasks": ["T4", "T5"], "parallel": true, "note": "pure test additions, no production change"},
    {"phase": 4, "tasks": ["T6"], "parallel": false, "decision": "D1 CLOSED"}
  ]
}
```

## Changelog

- **2026-09-26** — drafted from two independent post-merge reviews of Plan 399's shipped code. ⭐ *Every
  item is a thing 399 asserts that the code does not do, measured at `7aec753b` — not new scope.*
  ⚠️ **§ 5's crash and § 6's 723-passing mutation were both measured by execution, not inferred.**
