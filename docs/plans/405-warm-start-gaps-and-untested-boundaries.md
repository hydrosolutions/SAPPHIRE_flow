---
status: DRAFT
created: 2026-09-26
plan: 405
title: Four warm-start requirements that never shipped, and two boundaries nothing tests
scope: Close the gaps a post-merge review found in Plan 399's shipped code — the missing changed-template refusal, the never-recorded donor config path, the un-inspected donor params, and the retrain-of-a-retrain crash — plus the THREE verification bullets 399 wrote and never implemented. NOT Plan 399's staging run (it stays there, orchestrator-gated). NOT new capability of any kind. NOT the FI contract changes in fi-issue 004. NOT the fine-tuning strategy surface (owner: opaque config for v1).
depends_on: [399]
blocks: []
related: [262, 399]
open_decisions: []   # D1 closed by the owner 2026-09-26 on (b)
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
⚖️ **All decisions closed** — D1 on (b), 2026-09-26. *It changed what A4 means; T6 now carries the settled shape.* ⛔ *An earlier version said "first", which reads as blocking the whole plan; T1-T5 are independent of it, and the phase graph gates only phase 4.*

## Why this plan exists

Plan 399 merged as PR #314 and works: a fine-tune runs, is stored without promoting, and records its
donor. ⭐ *That part is verified, including against a real database.*

🔴 **But a post-merge review — the first able to check 399 against the code rather than against
itself — found four requirements 399 ASSERTS that the code does not do, and **three** verification
bullets 399 wrote and never implemented. ⛔ *An earlier version of this sentence, 399's own status, and
the index all said "two" — § 9 (399's flow-level resolver red, through `discover_models()`) is a third,
and § 2 (the resolver has no test at all) is arguably a fourth.*** One of the four is a crash. 399's status block has been corrected
to say so; this plan closes them.

⛔ **Nothing here is new capability, with ONE declared exception.** Every item is 399 finishing what
it claimed — *except* T1's refusal-before-training ordering. ⚠️ *399 asserts no ordering, atomicity or
orphan requirement anywhere (grep `atomic|same transaction|rollback|orphan` hits only its FK-RESTRICT
discussion). That requirement arises from the post-merge measurement, not from 399's text. It is a
legitimate fix — declared here rather than hidden under a blanket claim.* ⭐ *D1, by contrast, IS inside
399's frame: its § 292 defines the base-params field as "the configuration the donor was trained with".*

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
   `models/aquacast/_shim.py::AquacastShim.retrain`. 🔬 **Measured 2026-09-26**: with the adapter's
   `retrain` body replaced by `raise AssertionError`,
   `uv run pytest tests/unit/services/test_training.py tests/unit/flows/test_train_models.py
   tests/unit/adapters/` → **723 passed, 0 failed**. ⛔ *That is the SELECTION, not the suite — the
   full unit suite is 6043 tests. An earlier version gave "723 tests PASSING" unqualified, which
   reads as the suite and is unreproducible without the command.* ⚠️ *The shim is doubly unexercised —
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

### D1 — what does "inspect the donor's params" MEAN now? **⚖️ CLOSED — owner, 2026-09-26: (b).**

§ 4 measured that 399's constant reason is already false. Two donor classes now exist:

| | option | cost |
|---|---|---|
| **(a)** | **Record the donor's `run_config` as its params.** A retrained donor's own row carries it (verified: `record_warm_start` writes `run_config`, JSONB at `0060:54-59`, read back by `fetch_warm_start`). | ⚠️ **Needs a MIGRATION this plan must then carry**: `base_params_path` is `sa.Text()` (`0060:50`) — a mapping cannot go in it. ⇒ a new column or a widened meaning, plus a `WarmStartRecord` field, an invariant update and the alembic-head test. ⛔ *An earlier version flagged this in a footnote and then T6's In-list carried none of it.* |
| **(b)** ⭐ | **Keep the path NULL, with a reason TRUE per donor class** — and record that the donor's configuration is retrievable via `base_artifact_id`. | ⭐ **Nothing is lost.** *A missing params-FILE path does not discard the donor's configuration: it stays addressable through its own warm-start row, and the donor FK is `RESTRICT` so it cannot vanish.* No migration, no schema scope. |

**⚖️ CLOSED on (b).** ⇒ **The params path stays NULL, carrying a reason true of the donor's class, and
records that the donor's own configuration is reachable through `base_artifact_id`.**
⛔ *No migration, no new column, no widened field — (a) is NOT taken.*

⭐ *The field asks WHERE the settings file is, and for these donors there is none. What we have is the
configuration itself, stored against the donor and safe from deletion (`RESTRICT`). (a) remains a
legitimate design if the inline value is ever wanted — it is schema work, not gap closure, and would
be its own plan.*

⛔ *An earlier version recommended (a), arguing that (b) "throws away provenance we now demonstrably
hold". **That reasoning was wrong** — 399 asks for the PATH to the base params, and D3 separately
records the run config; different facts, which I conflated to make (a) look necessary.*

🔴 **And D1 settles what happens when the donor's `run_config` is EMPTY** — T6 carries it. `flows/train_models.py:787` passes `run_config=training_params or {}`, so a retrained donor's row
can carry `{}`.
⚖️ **DECIDED — `{}` is a KNOWN-EMPTY run config, NOT unknown.** *The flow normalises "nothing supplied"
to it deliberately (`flows/train_models.py:269`), so it accurately records what the model was given.*
⛔ *An earlier version said "an empty mapping is UNKNOWN-with-a-reason, not a value". **That is wrong**
and conflates two things:*
| | what it means |
|---|---|
| `run_config == {}` | **KNOWN**: the caller supplied no overrides. Record it as such. |
| the model's EFFECTIVE settings after its own defaults | **UNKNOWN** — ⚠️ *and equally unknown for a NON-empty override, so this is not an empty-mapping problem at all.* |
⇒ Under **(b)**, the per-class reason must not claim the donor's configuration "is retrievable via
`base_artifact_id`" when that row holds `{}` — it is retrievable and it is empty, which is a different
sentence. ⭐ *Plan 257's hole was an omitted config read as a declared zero; the fix here is to keep
"empty" and "unknown" distinct rather than to relabel one as the other.*

⚠️ *Precision: (a) fires only when the DONOR is itself a retrain, so it is the **third** generation
that gains traceability, not the second.*

## Tasks

### T1 — Stop the crash, and carry the reason forward (§ 5)

**Outcome.** A retrain of a retrain completes and records accurate provenance.

**In.**
- 🔴 **`resolve_donor_config`'s inherited branch DERIVES a reason describing the IMMEDIATE donor** —
  regenerated, or naming which generation it describes — **only when the inherited path is NULL**
  (otherwise a record can carry BOTH a path and an "unknown" reason, which `__post_init__` does not
  catch).
  ⛔ *An earlier version of this bullet said "returns the inherited `base_config_unknown_reason`".
  **That is the naive fix this task's own Verification rejects** — an implementer building the In-list
  would have failed the checks below. And it writes something FALSE: generation 1 is SAP3-produced and
  has NO provenance row — which is precisely why the inherited branch is taken — yet the inherited
  sentence asserts "the donor's config hash is recorded in its provenance."*
- ⚖️ **DECIDED — separate RESOLVE/REFUSE from RECORD:**
  | step | when | why |
  |---|---|---|
  | resolve the donor's config, and refuse on a mismatch or an unconstructable record | **BEFORE training** | ⭐ *This is the cheap fix, and an earlier version of this task did not name it.* A `ValueError` then cannot happen after the artifact is stored, because the resolved triple is VALIDATED first. ⚠️ **Not by constructing the real record** — `WarmStartRecord.artifact_id` does not exist until the store runs. ⇒ **Validate the resolved `(path, sha, reason)` against the same invariant, and THREAD those values into the post-store record.** ⛔ *Re-resolving after the store would remove the raise by luck, not by construction; and a hand-rolled copy of the invariant can drift from `__post_init__` — share it.* |
  | write the row | after the store, as now | ⛔ **"BEFORE" is IMPOSSIBLE**: `model_artifact_id` is both PK and a FK to `model_artifacts.id` (`0060:30-37`), so no warm-start row can precede its artifact. *An earlier version offered "BEFORE or ATOMICALLY WITH … stating which" — a two-way choice that is a one-way street, and an instruction to decide rather than a decision.* |
  ⚠️ **Atomicity is NOT attempted.** *`PgWarmStartWriter` holds its own connection and the store is a
  separate Prefect task; joining them is a transaction restructuring this task does not scope. Moving
  the refusal earlier removes the failure mode without it.*

**Out.** ⛔ Changing what a FIRST-generation retrain records. ⛔ Relaxing `WarmStartRecord`'s
invariant — the unexplained NULL it rejects is a real defect, and rejecting it is correct.

**Pre-change.** 🔴 **A RED test of the PRODUCTION-SHAPED chain**: generation 0 imported → 1 retrain →
2 retrain, through the flow, not by calling the helper. ⚠️ *It must fail with
`"base_config_path is NULL without a reason"` — that exact raise is the defect.*
🔑 **This red is an INTEGRATION test.** *`resolve_donor_config` takes an `sa.Connection` and reads two
tables, so a production-shaped chain needs the real writer against PostGIS — as 399's four warm-start
tests did. ⛔ A unit fake writer here would recreate the "fake more permissive than production" failure
399 hit three times.*
🔴 **The donor must be pinned to a NULL config path deliberately.** ⛔ *Once T2 records a real path the
inherited branch stops returning NULL and this test silently stops exercising the branch it exists
for — vacuous by phase 2 unless the fixture forces the case.*

**Verification.**
- Generation 2 completes and its record resolves to generation 1.
- 🔴 **The recorded reason is TRUE OF THE IMMEDIATE DONOR, not inherited verbatim.** ⛔ *Propagating
  generation 0's sentence ("no installed config path was supplied to pair with it") to generation 2
  describes a DIFFERENT artifact. ⚠️ *T6 states this rule for the PARAMS reason; the principle applies
  here to the CONFIG reason — same rule, different column, so cite the principle rather than T6.* Either the reason names which generation it describes,
  or it is regenerated per donor.*
- 🔴 **Generation 3 completes AND its recorded reason is true of generation 2** — ⛔ *"completes"
  alone proves nothing: any non-empty placeholder satisfies `__post_init__`, so a wrong fix passes.
  Assert the CONTENT.*
- 🔴 **When resolution REFUSES, nothing is stored** — no artifact row and no warm-start row.
  ⛔ *An earlier version said "if provenance fails, no orphaned artifact remains". **Unsatisfiable in
  this task**: the row is still written after the store on its own connection, which is exactly the
  atomicity this task declines two bullets above. Only the PRE-TRAINING refusal leaves nothing
  persisted — the same scope T2 already words correctly.*

### T2 — Record the donor's config path, and refuse a changed template (§§ 1, 3)

**Outcome.** The donor's config identity is recorded, and a retrain from a template that no longer
matches the donor is refused.

**In.**
- The flow passes the **real** installed config path (it already reads `model.config_hash` beside it).
- `resolve_donor_config` **compares** `installed_config_sha256` with the donor's recorded hash, and
  the refusal happens **BEFORE retraining**, naming both hashes.
- ⛔ *`installed_config_sha256` stops being a dead parameter.* ⚠️ *An earlier version added "or it is
  removed" — foreclosed, since the bullet above REQUIRES the comparison. Same shape round 1 struck from
  T1: an either/or that is a one-way street.*

**Out.** ⛔ Hashing today's template as the donor's identity (399 § 13's trap). ⛔ Refusing when the
donor's hash is genuinely unknown — that is a NULL-with-reason, not a mismatch.

**Pre-change.** A RED test: **a donor whose recorded hash differs from the installed template's is
refused, before the model is called.** ⚠️ *Assert the model was NOT invoked — "an error was raised"
would also pass on a refusal that happens too late.*
🔑 **DEPENDS ON T1's ordering decision**: a pre-training refusal is only possible because T1 moves
*resolve/refuse* ahead of training. ⛔ *Declared because an earlier version left it implicit — if the
resolve call stays after the store, this task's central assertion is foreclosed.*

**Verification.**
- Matching hashes ⇒ the path and hash are both recorded (no longer NULL).
- Mismatched ⇒ refused, both hashes named, **model not called**, and 🔴 **neither an artifact row nor
  a warm-start row exists afterwards** — ⛔ *"not called" alone is the mechanism; "nothing persisted" is
  the property § 5 actually cares about.*
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
- ⚖️ **The shim is exercised WITHOUT the extra, via the established pattern.**
  `tests/unit/models/test_aquacast_shim_translation.py`'s `_shim_with_fake_inner` builds the REAL
  `AquacastShim` around a fake inner collaborator (bypassing `__init__`, which is the only part that
  imports `aquacast`), and `TestFISurfaceDelegation` already asserts real `train` delegation and its
  config that way. ⇒ **`retrain` gets the same treatment.**
  ⛔ *An earlier version said "install the extra or mark it CI-only" — a false choice that would have
  left the shim CI-only, which is how § 6 happened in the first place.*

**Out.** ⛔ Changing the adapter's or shim's behaviour. ⛔ Weakening the inner-model check.

**Pre-change.** 🔴 **Gut each `retrain` body with `raise AssertionError` and confirm a named test
fails.** ⚠️ *Today nothing fails — see § 6 for the exact command and selection. ⛔ "723 green" shows
the coverage is MISSING; it is not itself a failing test. The sequence is: confirm the mutation
survives → add the boundary test → confirm the test now kills the mutation.*

**Verification.**
- Each boundary's `retrain` has a test that fails when its body is gutted.
- An FI model without `retrain`, wrapped in the **real** adapter, is refused.

### T4 — Assert onboarding's unchanged config (§ 8)

**Outcome.** The four onboarding sites are proven to still pass an empty config.

**In.** Assertions on `flows/onboard_model.py:368,375` and
`services/model_onboarding.py:1453,1457`. ⛔ *The two synthetic smoke-test sites are out of scope by
399 § 15 — they have no caller.*

**Out.** ⛔ Giving onboarding a config channel. ⛔ Changing onboarding behaviour at all.

**Pre-change.** 🔴 **A PER-SITE SOURCE MUTATION**: flip the empty mapping to a non-empty one at each of
the four sites and confirm the matching assertion fails. ⚠️ **Two of the four pass it POSITIONALLY** —
`services/model_onboarding.py:1453,1457` are `train_*_model(model, training_data, {}, rng)`, not
`params={}`; only `flows/onboard_model.py:368,375` use the keyword. ⛔ *A mutation written for
`params={}` alone would silently skip half the sites.* ⛔ *An earlier version said "N/A — this asserts
existing behaviour, so it cannot be red". **That is the excuse, not an N/A**: T3 and T5 both derive
their red from a source mutation and the same tool applies here. Declining it leaves the textbook
vacuous pass open — an assertion shaped "every captured call passed `{}`" passes when NO call was
captured.* ⚠️ *The inconsistency within this plan was the tell.*

**Verification.**
- Each of the four sites asserted, individually. ⛔ *One test covering "onboarding" would pass on a
  single site and prove nothing about the rest.*
- 🔴 **Assert the call HAPPENED (a count), not only its value** — ⛔ *"every captured call passed `{}`"
  is vacuously true of zero captured calls.*
- Each site's mutation fails its own assertion (the Pre-change red).

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
- 🔴 **THREE donor classes, not two** — and the third is the one D1 flags: imported; a retrain whose
  `run_config` has content; **a retrain whose `run_config` is `{}`**. ⛔ *An earlier version of this task
  carried none of D1's empty-config clause — verbatim the criticism this plan levels at its own draft
  in the D1(a) cell ("flagged in a footnote and then T6's In-list carried none of it"), moved from a
  footnote to a table cell.*
- ⚖️ **Per D1(b): the path stays NULL with a per-class reason**, plus the note that the donor's own
  configuration is reachable via `base_artifact_id`. ⛔ *No migration and no schema change in this task —
  (a) was not taken.*
- 🔴 **The reason must NOT claim the configuration "is retrievable" when the donor's row holds `{}`.**
  *It IS retrievable, and it is empty — a different sentence. ⚠️ That is the known-empty vs
  unknown-effective distinction D1 settles; getting it wrong here relabels a known fact as unknown,
  which is the defect § 4 already has.*

**Out.** ⛔ Inventing a params file where none exists.

**Pre-change.** A RED test: **a retrained donor's recorded params are not the constant string.**

**Verification.**
- All **THREE** donor classes produce DIFFERENT, accurate records — imported, retrain-with-content,
  and **retrain-with-`{}`** — asserted individually. ⛔ *Two cases would leave the third, which is the
  one D1 exists to settle, unasserted.*
- 🔴 **The recorded reason is true of the donor in hand**, checked by CONTENT, not merely non-constant.
  ⛔ *`base_params_path=None` already differs from the old constant, so "not the constant string" passes
  trivially.*

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
  ⚖️ *AMENDED 2026-09-26: with ONE declared exception — T1's refusal-before-training ordering, which 399
  never required. See § Why this plan exists.*
  ⚠️ **§ 5's crash and § 6's 723-passing mutation were both measured by execution, not inferred.**
- **2026-09-26 — first review, two reviewers, both NEEDS CHANGES.** ⭐ *Both confirmed the measured
  section holds and the task decomposition is clean — all nine sections have exactly one owner, nothing
  dropped, nothing invented. **Every finding was in the layer I added on top**, which is where I asked
  them to aim.*
  - 🔴 **The plan number was already taken.** 400 belongs to the owner-granted cadence plan. ⛔ *My check
    was invalid: in zsh a multi-glob `ls` aborts when ANY glob misses, so `|| echo FREE` fired while the
    first glob was matching a real file.* Renumbered to 405, confirmed free three ways.
    ⚠️ **And I committed that rename WHILE a reviewer was reading the file** — the exact thing
    [[feedback_never_edit_a_file_under_review]] records, written this morning. It noticed.
  - 🔴 **T1 offered "BEFORE or ATOMICALLY WITH … stating which" — an instruction to decide, and BEFORE
    is IMPOSSIBLE** (the warm-start PK is a FK to the artifact, so no row can precede it). Now decided:
    **resolve/refuse before training, record after the store** — which removes the failure mode without
    the transaction restructuring atomicity would need, and is the cheap fix the draft never named.
  - 🔴 **T1's own fix would have violated T6.** Returning the inherited reason verbatim propagates
    generation 0's sentence to generation 2, describing a *different* donor — which T6 forbids. And
    "generation 3 also completes" proves nothing: any non-empty placeholder satisfies the invariant.
    Both now assert the reason's CONTENT.
  - 🔴 **T1's regression test would have gone vacuous once T2 lands** — a real recorded path makes the
    inherited-NULL branch unreachable. The fixture must pin a NULL-path donor deliberately.
  - 🔴 **"723 tests PASSING" was unusable** — the unit suite is **6043**; 723 is the three-selection
    subset I actually ran and never named. ⇒ The command is now written out. ⛔ *And "723 green" is not a
    failing test; it shows coverage is missing.*
  - 🔴 **D1's recommendation rested on a false premise.** I argued keeping the path NULL "throws away
    provenance we now hold" — it does not: the donor's config stays addressable through its own row.
    399 asks for the base-params PATH; D3 records the run config; **different facts, which I conflated
    to make my preferred option look necessary.** Recommendation flipped to (b), and (a)'s missing
    migration — a mapping cannot go in a `Text` column — is now named rather than footnoted.
  - 🔴 **D1 had Plan 257's hole**: a donor's `run_config` can be `{}`, and recording that as "the
    donor's params" is indistinguishable from unknown. Now explicitly UNKNOWN-with-a-reason.
  - 🔴 **T4 declined a red test as "N/A — this asserts existing behaviour". That was the excuse**: T3
    and T5 both derive a red from a source mutation and the same tool applies. Now a per-site mutation,
    plus an assertion that the call HAPPENED — ⛔ *"every captured call passed `{}`" is vacuously true
    of zero captured calls.*
  - **The shim can be tested WITHOUT the aquacast extra** — an established helper builds the real shim
    around a fake inner collaborator. ⛔ *My "install the extra or CI-only" was a false choice that would
    have left it CI-only, which is how the gap happened.*
  - **The "two verification bullets" count was wrong in three files** (this plan, 399, the index): it is
    three. **And T2's coupling to T1's ordering is now declared**, along with "nothing persisted" beside
    "model not called".
- **2026-09-26 — second review, two reviewers, both NEEDS CHANGES.** ⭐ *Both confirmed the file held
  still this time (one checked its md5 at start and end) — the discipline that failed in round 1 held.*
  ⭐ *Both also re-confirmed: the measured section holds, the decomposition is clean, the 723/6043 figures
  reproduce to the digit, and the shim route genuinely reaches the real shim (its helper subclasses
  `AquacastShim` with an empty body, so no method is stubbed).* ⛔ **Two of the four findings were
  defects my ROUND-1 FOLD introduced.**
  - 🔴 **T1's In-list prescribed exactly the fix T1's Verification calls a violation.** I added the
    content requirement to the Verification and left the In-list saying "return the inherited reason" —
    so an implementer building the In-list fails the checks below it. ⚠️ *And the inherited sentence is
    demonstrably FALSE of the immediate donor: generation 1 has no provenance row (that is why the
    inherited branch is taken) yet the sentence asserts its hash "is recorded in its provenance".*
  - 🔴 **T1's orphan bullet contradicted T1's own "atomicity is NOT attempted".** "If provenance fails,
    no orphaned artifact remains" is unsatisfiable while the row is still written after the store on its
    own connection. Narrowed to the refusal path — the scope T2 already worded correctly.
  - 🔴 **"An empty mapping is UNKNOWN-with-a-reason" was WRONG.** `{}` is a KNOWN-empty run config — the
    flow normalises "nothing supplied" to it deliberately. What is unknown is the model's EFFECTIVE
    settings after its own defaults, and that is equally unknown for a NON-empty override, so it was
    never an empty-mapping problem. Now a two-row table keeping "empty" and "unknown" distinct.
  - 🔴 **D1's empty-config clause reached no task.** T6 carried two donor classes; there are three, and
    the third is the one D1 exists to settle. ⛔ *Verbatim the criticism this plan levels at its own
    earlier draft — "flagged in a footnote and then T6's In-list carried none of it" — moved from a
    footnote to a table cell.*
  - **"Proven constructible" is not literally possible** (the artifact id does not exist pre-store) ⇒
    validate the resolved triple and **thread** it into the post-store record; re-resolving would remove
    the raise by luck. **T1's red is an INTEGRATION test**, not unit — a fake writer would recreate the
    "fake more permissive than production" failure 399 hit three times. **T2 kept a foreclosed
    either/or** ("or it is removed") — the shape round 1 struck from T1. **T4's mutation covered half
    the sites** — two pass the mapping positionally. **The T6 citation overreached** by one column.
  - 🔴 **The count fix reached the WORD "three" and not the ARITHMETIC.** 399's item table still headed
    "B. Two verification bullets" with only B1/B2 — so **B3, the bullet covering 399's own headline
    blocker, appeared in no table anywhere** — and both 399 and the index still said "SEVEN remain / six
    carried". Now EIGHT and SEVEN, B3 listed, and the index's unqualified "723 tests passing" and
    blanket "no new capability" both qualified. ⛔ *Fold reaches the sentence, misses the sum.*
- **2026-09-26** — ⚖️ **D1 CLOSED on (b) by the owner.** The donor's params PATH stays NULL, carrying a
  reason true of its class and noting that the donor's own configuration is reachable through
  `base_artifact_id`. ⛔ *(a) is NOT taken — no migration, no new column, no widened field.*
  ⭐ *The field asks WHERE the settings file is; for these donors there is none. What exists is the
  configuration itself, stored against the donor and safe from deletion (`RESTRICT`).*
  🔑 **`open_decisions` is now empty and every task is settled.**
  ⚠️ **T6 carries D1's harder half**: the reason must NOT claim the configuration "is retrievable" when
  the donor's row holds `{}` — it is retrievable AND empty, which is a different sentence. *That is the
  known-empty vs unknown-effective distinction; collapsing it relabels a known fact as unknown, which is
  the defect § 4 already has.*
  ⛔ **The index had gone stale again** — `open_decisions: [D1]` and "D1 asks what … means" both still
  present. *Third consecutive plan where a decision closure reached the plan and not the index. The
  value sweep caught it this time because I ran it before claiming the fold was done.*
