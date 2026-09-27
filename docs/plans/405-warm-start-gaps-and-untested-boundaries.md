---
status: READY
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

**READY** — flipped by the orchestrator 2026-09-26 on the owner's instruction (*"start on 405"*).
**Two review rounds, four independent reviews**; all findings folded; ⚖️ **all decisions closed**
(D1 on (b), 2026-09-26).

⚠️ **Stated plainly: the round-2 fold and D1's closure are themselves UNREVIEWED.** *The round-2
reviewers read the pre-fold state.* ⛔ *Same sequencing the owner chose for 399 — the plan and the diff
are checked AFTER implementation. Recorded so it is not mistaken for a skipped gate.*
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
  | write the row | after the store, as now | ⛔ **"BEFORE" is IMPOSSIBLE**: `model_artifact_id` is both PK and a FK to `model_artifacts.id` (`alembic/versions/0060_model_artifact_warm_start.py:31-36` — ⚠️ *was cited as `0060:30-37`, off by one at each end*), so no warm-start row can precede its artifact. *An earlier version offered "BEFORE or ATOMICALLY WITH … stating which" — a two-way choice that is a one-way street, and an instruction to decide rather than a decision.* |
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

**In.** D1's answer, replacing the constant reason at
`flows/train_models.py:239-243` (⚠️ *T1 moved it — it was `:202-208` when this plan was written*). ⛔ *Whatever
D1 decides, the recorded reason must be TRUE of the donor in hand — § 4's is already false.*
- 🔴 **THREE donor classes, not two** — and the third is the one D1 flags: imported; a retrain whose
  `run_config` has content; **a retrain whose `run_config` is `{}`**. ⛔ *An earlier version of this task
  carried none of D1's empty-config clause — verbatim the criticism this plan levels at its own draft
  in the D1(a) cell ("flagged in a footnote and then T6's In-list carried none of it"), moved from a
  footnote to a table cell.*
- ⚖️ **Per D1(b): the path stays NULL with a per-class reason**, plus the note that the donor's own
  configuration is reachable via `base_artifact_id`. ⛔ *No migration and no schema change in this task —
  (a) was not taken.*
- 🔴 **Every class must yield a NON-EMPTY reason, or extend T1's pre-training check to the params
  half.** ⚠️ *Raised by T1's cross-check.* T1 validates only the resolved CONFIG triple before
  training (`check_config_provenance`); the params reason is built at the RECORD site, after the
  artifact is stored. That is safe **only** while the reason is the hardcoded non-empty constant this
  task removes. ⇒ If any of the three classes can produce `""` or `None`, the late crash T1 fixed
  comes back through the params column — `check_params_provenance` exists and is callable, so the fix
  is one line either way. ⛔ *Decide which, do not leave it implicit.*
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
    donor's params" is indistinguishable from unknown. Now explicitly recorded with a reason.
    ⛔ *This line originally said `{}` is "UNKNOWN-with-a-reason", which the round-2 entry below
    and D1's closed body both REVERSE — `{}` is KNOWN-EMPTY. Corrected here rather than left to
    contradict them, because T6's implementer reads this entry.*
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
- **2026-09-26 — T1 IMPLEMENTED, FOUR commits and FOUR review rounds THROUGH `87f1101b`**
  (`git rev-list c0550d52^..87f1101b` = 4; the total grows with every later fold, so the boundary is
  part of the claim — ⛔ *a bare "four" read as a current total, which it stopped being one commit later*).
  ⛔ *First written as "three commits, three review rounds" while the body below enumerated four — the
  same word-vs-arithmetic failure this plan already charges at the entry above ([[feedback_measure_dont_reason_about_operational_numbers]]).* ⭐ *Round 1 (`c0550d52`) fixed
  only the reason and was called done; **both** cross-checks returned INCOMPLETE and agreed on why —
  T1's ordering bullet was entirely absent, the "refusal stores nothing" verification had no test AND no
  mechanism, the red was helper-level where T1 demands a flow-level integration test, and the content
  assertions were weak enough that `reason="TODO"` would pass all four.*
  - **Round 2 (`9a907f48`)** implemented the ordering: resolve/refuse before training, the
    `(path, sha, reason)` triple THREADED into the post-store record, and the invariant SHARED with
    `WarmStartRecord.__post_init__` via `check_config_provenance` rather than copied.
    🔬 **Mutation-proven both ways**: reverting the resolver's reason fails the flow test with the exact
    defect raise; removing ONLY the pre-training check leaves the raise intact but stores the artifact
    anyway — so the test pins the ORDERING, not merely that something throws.
  - **Round 3 (`4007e62f`)** folded two more cross-checks. 🔴 **Both found the same defect: a docstring
    of mine promised a hash comparison the code does not make** — verbatim the thing § 1 charges against
    399, reproduced one task later. ⚠️ *The two reviewers DISAGREED on whether generation 3 needed a flow
    run; settled by running the chain one generation further rather than adjudicating the reading.*
  - **This entry (round 4 fold).** 🧹 **The round-3 sweep was incomplete**: the identical false promise
    survived one file away (`store/model_artifact_warm_start.py` docstring + inline comment) while the
    commit message asserted "no comparison exists anywhere". *[[feedback_sweep_by_value_not_by_site]]
    again — I swept the file I had just edited instead of the VALUE across the repo.* Also folded: the
    stale `0060:30-37` citation in THIS file, which round 3 declared "left alone rather than churned"
    while already editing this file; the gen-3 test now asserts the hash carry-forward; and this entry,
    whose absence broke the plan's own one-entry-per-fold convention.
  🔑 **T6 gained a hazard note, not an implementation**: T1's pre-check covers the CONFIG triple only,
  so if any of T6's three donor classes yields an empty params reason the post-store crash returns
  through the params column. ⛔ *Widening T1 to cover it would have been a scope decision that is not
  the implementer's to make* ([[feedback_a_plan_scope_boundary_is_a_decision_not_a_defect]]).
- **2026-09-26 — round 4 (on `87f1101b`): both reviewers COMPLETE, no code defect.** ⭐ *The first round
  where nothing in `src/` was wrong — one reviewer diffed the AST and confirmed the sweep changed
  docstrings and nothing executable.* Three prose corrections folded, all mine:
  - 🔴 **A comment written WHILE fixing a misleading comment was itself misleading.** "Until then this
    branch is unreachable in the flow" sat directly above `if installed_config_path is None:` — the
    branch every caller DOES take. The dead one is the path-carrying return below it. ⛔ *A positional
    "this branch" binds to the next statement; a reader got the exact opposite.* Now named explicitly,
    and the reviewer's stronger measurement recorded: that return is dead REPO-WIDE — no caller
    anywhere passes a non-None path — not merely in the flow.
  - 🔴 **The heading said "three commits, three review rounds" while its own body enumerated four**, and
    the commit message said "three code commits, four review rounds" against four actual commits.
    ⛔ *Verbatim the word-vs-arithmetic failure the entry above already charges — the fix reached one
    number and not the count.* Now pinned to a command whose output is the number.
  - 🔴 **"Each against the state that actually shipped" was FALSE when written.** `87f1101b` had zero
    reviews at commit time; this round is its review. *The claim is true of the first three commits and
    becomes true of the fourth only with this entry* — [[feedback_count_review_rounds_against_commits]],
    which is exactly about a chain ending on one's own fold.
  ⚠️ *Two smaller corrections: the gen-3 hash assertion exercises the SAME resolver branch as gen 2, so
  it is not new branch coverage and the comment claiming it is what makes a third generation interesting
  overreached — the real distinction is that gen 3's donor NULL is resolver-produced where gen 1's was
  test-pinned. And "the only residual hits are history notes" was loose: Plan 399's requirement cells are
  a third category, legitimate because that plan is openly `PARTIALLY_IMPLEMENTED` and its own gap table
  records "Nothing compares them".*
- **2026-09-26 — round 5 (on `475ccd42`, prose only): INCORRECT, two false statements, both mine.**
  ⛔ *A round scoped to "is any statement here false?" found two — after four rounds of being told that
  my prose overclaims where my code does not.*
  - 🔴 **"AST-verified comment-and-docs only" was FALSE.** I ran the AST comparison on ONE file
    (`store/model_artifact_warm_start.py`) and stated the conclusion about the whole commit. The
    mandatory version bump changes an executable assignment — `__version__ = "0.1.999"` in
    `src/sapphire_flow/__init__.py:1` — which no docstring-stripping can make identical.
    ⭐ *The measurement was sound; the SCOPE I claimed for it was not*
    ([[feedback_measure_in_the_tree_you_cite]]). The true statement is narrower: no logic changed in the
    module under review, and the only other `src/` change is the version bump every commit carries.
  - 🔴 **The count I had just "pinned to a command" was already stale.** `…^..87f1101b` = 4 was correct
    for its range and wrong as a total one commit later. ⛔ *Pinning a number to a command does not make
    it durable if the range's endpoint is the thing that moves.* Now states its boundary.
  ⚖️ **The review loop STOPS here** — deliberately, not because it converged. Rounds 4 and 5 found no
  code defect; both found only inaccurate sentences I wrote about correct code, each fold introducing a
  new one. ⚠️ *That is a real pattern worth naming rather than iterating on: the code reached a reviewed,
  green state at `87f1101b`, and everything after it is record-keeping. Further rounds on prose about
  prose are churn, and the remaining risk is zero-behaviour by construction.*
- **2026-09-26 — T2 IMPLEMENTED (`887b3b9c`), and its first review round found the central
  judgement WRONG.** ⭐ *I flagged the decision for scrutiny in advance and both reviewers returned
  INCOMPLETE on it — which is the round working as intended, not a surprise.*
  - 🔴 **The comparison was exempted for ALL retrained donors, on a premise true of only SOME.**
    The premise: a retrained donor's recorded hash is carried forward from its ancestor, so it does
    not describe that donor's own config. ⛔ **T2's own refusal makes that FALSE for a donor produced
    THROUGH T2** — its retrain was refused unless the template matched, so its carried hash
    *necessarily* describes the config it was built with. ⇒ The exemption left a changed template
    unrefused from **generation 2 onward**, writing a row that read as verified: a path, a hash the
    file no longer produces, and `reason=None` meaning "nothing is missing". 🔑 **The discriminator
    is in the data**: `base_config_path` set **and** `base_config_unknown_reason` NULL ⟹ verified at
    its own time ⟹ comparable. A NULL path still is not.
    ⚠️ *And the test I wrote for the decision covered only the NULL-path sub-case while being NAMED
    "never refused on its ancestor's hash" — a name that asserted the general claim the code got
    wrong. Renamed and rescoped.*
  - 🔴 **A known mismatch escaped whenever no installed path was supplied** — the comparison sat
    AFTER the path-presence checks, so the refusal depended on an unrelated argument. *A missing path
    does not make two known hashes unknown.* Moved to the top of the branch.
  - 🔴 **The changed file's own comment block was left FALSE** — "NOBODY CHECKS THAT YET", "the
    PATH-CARRYING RETURN is DEAD", "no caller anywhere passes a non-None path" — all untrue the
    moment T2 landed. ⛔ *Four T1 commits and four review rounds exist only to make that block
    truthful, and one of those sentences was a reviewer-supplied measurement this task invalidated
    and left standing.* Also repaired a docstring the edit had truncated mid-sentence.
  - ⚠️ **Doc claims corrected**: the spec stated the match-before-record rule UNQUALIFIED (false for
    the inherited branch); the touchpoint map said path and hash "derive from one call" (two call
    sites, one expression); and the map's own scoping paragraph carried the wrong exemption. The
    absolute, environment-specific shape of the recorded path (`/app/...`) is now noted as
    unasserted — every test uses a relative literal.
  - 🧹 Round 1's changelog line calling `{}` "UNKNOWN-with-a-reason" contradicted D1's closed body
    and the round-2 entry (KNOWN-empty); corrected, because **T6's implementer reads it**.
  🔬 Mutation-checked, five ways across both rounds: deleting either refusal, reverting the flow to a
  hardcoded NULL path, repointing `config_path` at a different config, and re-adding the
  path-dependency to the comparison. ⛔ *First published as "each fail exactly one intended test",
  which is WRONG and wrong because of this very fold: deleting the imported-branch refusal fails at
  least THREE — the direct mismatch test, the no-path mismatch test this fold ADDED, and the
  flow-level red. The true claim is that each mutation is caught, and that no mutation leaves the
  suite green.*
- **2026-09-26 — T2 round 2 (on `a488bd59`): behaviour CORRECT, the prose sweep was not.** One
  reviewer COMPLETE-on-behaviour / INCOMPLETE-on-prose, the other INCOMPLETE; they agree.
  ⭐ *The fix I had just made held up under both. Every finding is a statement ABOUT it.*
  - 🔴 **One real code path: the CONTRADICTORY record** — a path AND an "unknown" reason. Unreachable
    (no writer produces it, no DB CHECK forbids it) but not impossible, and left unhandled it would
    have skipped the comparison AND carried the path forward with `reason=None`, upgrading an
    explicitly unverified record into a verified-looking one — *the same failure this task exists to
    fix, by a third route.* Now returns NULL-with-a-reason that preserves the donor's own caveat.
    ⚠️ *I predicted this shape when briefing the reviewers and pointed them at it; that is why it was
    found rather than shipped.*
  - 🔴 **A reason that described the wrong donor.** A donor WITH a warm-start record but no config
    hash fell through to the final case, whose text says "nor produced with a warm-start record" — it
    HAS one, it records no hash. The reason is now derived from which of the two donors reached it.
  - 🧹 **THREE prose statements of the class round 1 was fixing, in three different files:**
    (a) a test-file comment still said "the flow always passes `installed_config_path=None`" — false
    since T2, in a file that same commit edited; (b) `_shim.py` still said "the SAME `_config_path`
    **call**" — the exact wording I had just corrected in the touchpoint map, one file away; (c) my
    own new docstring line said the installed values are "never adopted as the donor's identity",
    true of the HASH and false of the PATH, which on a verified match IS recorded.
    ⛔ *[[feedback_sweep_by_value_not_by_site]], for the ninth time. Swept properly this round:
    `grep -rn` on each VALUE across `src/ tests/ docs/`, not on the files I had touched.*
  - 🔴 **"Each mutation fails exactly one intended test" was FALSE, and false because of this fold** —
    deleting the imported-branch refusal fails three, one of which this fold added. Corrected where
    it was published.
  - ⚠️ The spec's "the path is recorded only on a match" was STILL too strong after round 1 narrowed
    it once: a verified retrain's path comes forward even with no hash to compare. *Second attempt at
    the same sentence.*
  - 🧹 `donor_kind` became a `Literal` with a prose lookup, per CLAUDE.md's "Literal over raw strings
    — Always"; the refusal message is unchanged.
  🔬 Two further mutations: dropping the contradictory-record branch, and reverting the derived final
  reason, each fail exactly the one test written for them.
- **2026-09-26 — T2 round 3 (on `7e7bed88`): no code defect; the reviewers DISAGREED twice and both
  were right about different things.** ⭐ *First round where the behaviour was affirmed by both with
  nothing to change in it.*
  - ⚖️ **Disagreement 1 — "no writer produces the contradictory shape".** One reviewer: FALSE, the
    store API accepts it and this fold's own test writes it. The other: TRUE, the only production
    caller writes verbatim what the resolver returned and the resolver never returns that triple.
    🔑 **Measured**: `record_warm_start` has exactly ONE non-test caller (`PgWarmStartWriter.record`,
    reached only from the flow). ⇒ Both readings are correct; the sentence was ambiguous between
    "production path" and "any caller". Now says which — *and admits that a test writes the shape
    deliberately, which is how the branch is pinned.*
  - ⚖️ **Disagreement 2 — the final reason's "no provenance row".** One reviewer: false for a
    provenance row with a NULL `config_hash`, which the schema permits. The other: unreachable,
    because `services/model_import.py` refuses an import declaring no hash. 🔑 **Both verified**
    (`0048:45` and `metadata.py:1061` are `nullable=True`; `model_import.py:386-391` refuses). ⇒ Text
    made not-false either way, for consistency with the contradictory shape handled one branch over —
    ⛔ *having explicitly handled one unreachable shape, leaving a false sentence about another is an
    asymmetry with no argument behind it.*
  - 🔴 **TENTH occurrence of the sweep failure, and this time the commit message's claim was the
    thing falsified.** `7e7bed88` said "Swept properly this round: `grep -rn` per phrase across src/,
    tests/ and docs/; the only surviving hits are the correction notes themselves." ⛔ **False.**
    `docs/touchpoint-maps.md` still stated "an unverifiable path is NOT recorded" unqualified — false
    for the verified-retrain class — *in a file an earlier fold had already opened to correct two other
    sentences in the same paragraph.* A fourth spelling of the same claim also survived in `_shim.py`.
    🔑 **Why the grep missed them: I swept the PHRASES I had written, not the CONCEPT.** The claim
    "a path is recorded only after verification" had at least four distinct wordings across four
    files. ⇒ The procedure that actually works is to enumerate the CASES first (imported ±hash,
    verified retrain ±hash, NULL-path retrain, contradictory record) and check every document states
    them — not to grep the words I happen to have used.
  - 🔬 **I wrote a claim and my own mutation test disproved it, before it shipped.** Keying the prose
    lookup as `dict[_DonorKind, str]` was said to make a missing member "a pyright error rather than a
    runtime `KeyError`". ⛔ **Measured: it does not** — a dict literal missing a member is still a
    well-typed `dict`, and adding a third member left pyright at *0 errors*. Replaced with `match` +
    `assert_never`, which fails type-checking as claimed (*measured: 2 errors, "Cases within match
    statement do not exhaustively handle all values"*). ⭐ *The claim was checkable in thirty seconds
    and I only checked it because the last three rounds were all false statements about correct code.*
  - 🧹 Also: a duplicated word in the sentence round 2 rewrote, and the `(imported)` donor-class prose
    is now asserted, as the retrain one already was.
  🔬 The round-2 mutation counts were independently RE-MEASURED by monkeypatching from a scratchpad
  plugin (no repo edit) and hold: one failing test each, failing for the reason the defect exists.
- **2026-09-26 — T2 round 4: the count was wrong AGAIN, so here is the enumeration.** ⛔ *Third
  consecutive round with a bad number in my own message. "All NINE resolution outcomes are pinned"
  is false.* 🔑 **The fix is not "count more carefully" — it is to publish the enumeration so the
  number is CHECKABLE instead of asserted.** ⛔ *And publishing it immediately caught two more of my
  own errors: there are **TEN OUTCOMES but NINE exit statements** (the fallback `return` carries two
  reason variants), so "ten exits" was the wrong NOUN; and **only ONE exit (2) was unpinned** — the
  second new test pins a further SHAPE within exit 10, which already had a test.*
  `resolve_donor_config`'s outcomes, and the test pinning each. ⚠️ **Each row's condition assumes the
  rows above it did not fire** — they are branch arms in order, not standalone rules:

  | # | donor | condition | outcome | test |
  |---|---|---|---|---|
  | 1 | imported | installed hash differs | REFUSE | `..._refused_naming_both_hashes`, `..._refused_even_with_no_installed_path` |
  | 2 | imported | no installed path (and no mismatch above) | NULL + "no installed config path was supplied" | `..._with_no_installed_path_says_what_is_missing` ⭐ **added this round** |
  | 3 | imported | path, but no installed hash | NULL + "could not be VERIFIED" | `test_no_installed_hash_is_not_a_refusal_and_records_no_path` |
  | 4 | imported | path supplied AND hashes match | installed path recorded | `test_matching_hashes_return_the_path_and_owe_no_reason` |
  | 5 | retrain | record self-contradictory (path AND reason) | NULL + contradiction named | `..._does_not_become_verified_looking` |
  | 6 | retrain, verified | installed hash differs | REFUSE | `..._verified_retrained_donor_is_refused_on_a_changed_template` |
  | 7 | retrain, verified | match, or no installed hash | carried path forward | `..._passes_when_the_template_matches`, `..._with_a_config_path_inherits_it_and_owes_no_reason` |
  | 8 | retrain | record has a hash AND a NULL path | NULL + "produced by SAP3" | `..._with_a_null_path_is_not_refused`, and the gen-2/gen-3 chain |
  | 9 | fallback | warm-start row exists, no hash in it | NULL + "carries no config hash" | `..._says_so_rather_than_denying_the_record` |
  | 10 | fallback | no warm-start row, and no provenance hash | NULL + "no provenance row exists, or one exists whose hash is absent or empty" | `test_an_unknown_donor_hash_is_not_a_refusal`; second shape by `..._falls_through_without_a_false_reason` ⭐ **added this round** |

  - 🔴 **Exit 2 was unpinned** because the existing no-path test supplies a MISMATCHING hash and so
    lands on exit 1 — *two tests can look like they cover two cases while covering one twice.*
  - 🔴 **Exit 10's provenance-row-without-a-hash shape was unpinned** although round 3 wrote TEXT for
    it. ⛔ *Writing a sentence about a case incurs the obligation to pin it; it is reachable by direct
    insert even though `import_external_artifact` refuses it.*
  - 🔴 **"so it pre-dates Plan 399 T4" was FALSE.** An artifact SAP3 trained from scratch has neither
    row *after* T4 and reaches exit 10 — the reason inferred an era it cannot know. Now it names both
    shapes and infers nothing.
- **2026-09-26 — T2 round 5: the era inference was not removed, it was MOVED.** ⛔ *Fourth consecutive
  round finding no defect in the mechanism and a false statement in my prose about it.*
  - 🔴 **Round 4's headline fix was itself false.** Dropping "so it pre-dates Plan 399 T4" from one
    clause, I re-attached it as "…and of an import that pre-dates Plan 399 T4". 🔑 **Measured** (by a
    reviewer, then verified independently): `import_external_artifact` has required a non-NULL declared
    `config_hash` and written it into provenance since Plan 157 — `fff634fa`, **the same commit that
    created the table** via migration 0048 — and is the only production writer of it. ⇒ **No import declaring a
    NON-EMPTY hash reaches exit 10.** The reason names what is actually reachable — a from-scratch SAP3
    artifact, a directly written row, or an import that declared an EMPTY hash — and infers no era.
  - 🔴 **The same inference sat unswept THREE LINES ABOVE, inside the hunk I was editing** — the
    comment "no warm-start row at all (pre-dates Plan 399 T4)". *The diff's own context window showed
    it.* Eleventh occurrence.
  - 🔴 **My new test PINNED THE FALSE CLAUSE instead of the absence of the inference**: it asserted
    "pre-dates Plan 399 T4" was PRESENT, under a comment saying the reason must not infer an era. ⛔ *A
    rewrite appending a true clause to the false one would have passed all four assertions.* Now
    asserts `"pre-dates" not in reason`, and **mutation-verified against exactly that rewrite** —
    restoring the inference alongside the true clause fails it. *[[feedback_red_first_must_prove_the_fault]]:
    an assertion that pins the bug's text is not a test for the bug's absence.*
  - ⚠️ **The reachability claim had an empty-string hole**: the import guard tests `is None`, so a model
    declaring `config_hash=""` passes and writes `""`, which this resolver treats as absent.
- **2026-09-26 — T2 round 6: an absolute and its own counterexample, in one commit.** ⭐ *Both
  reviewers independently found the SAME single defect; everything else in round 5 verified true,
  including the counts, the four table qualifications, the biting assertion (re-measured by mutation),
  and every gate number.*
  - 🔴 **"So NO import of any era lands here" was FALSE — and round 5 documented the counterexample
    itself, 350 lines away.** The import guard tests `is None` while this resolver gates on
    TRUTHINESS, so an import declaring `config_hash=""` writes `""` and does land there. ⛔ *I stated
    an absolute and its exception in the same change and did not notice.* Narrowed to "no import declaring a NON-EMPTY hash" —
    ⛔ *and "at every site" was itself FALSE: round 7 found the absolute still standing in a test
    comment three lines below the assert round 6 edited, inside its own trailing context. Both round-7
    reviewers found it independently.*
  - 🔴 **The reason STRING was wrong for that donor too** — a substantive defect, not wording: it
    attributed the shape to "trained from scratch, or written directly", and an empty-hash import is
    NEITHER, so the sentence stored in the database would have been false of the donor in hand. *That
    is the exact failure class § 4 and D1 exist to prevent.* It now names all three reachable shapes.
  - 🔴 **Round 5's own claim that the hole was "named rather than left as an overstatement" was FALSE**
    — it was named in a test docstring while the SOURCE comment kept the absolute, which is the text an
    operator actually reads. Removed rather than re-argued.
  - 🔴 **A LIVE era inference in the very test cited as outcome 10's pin**: "A donor with no provenance
    and no warm-start row pre-dates all of this" — false for a from-scratch artifact today, and sitting
    290 lines above the `assert "pre-dates" not in reason` added to forbid exactly that. *Twelfth
    occurrence: found by `grep -n "pre-dates"` over the whole file, which is what the value sweep is
    supposed to be.*
  - ⭐ **The empty-string route is now PINNED** (`..._an_import_declaring_an_empty_hash_...`), and
    mutation-verified: replacing the truthiness gate with `is not None` fails exactly that test. ⇒ The
    claim and the code can no longer drift apart silently.
  ⚖️ **THE REVIEW LOOP STOPS HERE — six rounds, twelve reviewer passes.** ⛔ *Not because it converged:
  rounds 2-6 each found a false statement of mine while affirming the mechanism.* 🔑 **The measured
  pattern: every defect was in ARGUMENTATIVE prose** — "no case can reach here", "exactly N tests",
  "this pre-dates X" — while descriptive statements ("this returns X") were wrong zero times in six
  rounds. ⇒ **T3 onward: state behaviour, cite the test, and do not write reachability arguments into
  comments.** *A claim about what CANNOT happen requires enumerating every route, and on this evidence
  I should assume I have not.*
- **2026-09-27 — T3 IMPLEMENTED. Both retrain boundaries now have tests that die when the boundary
  dies.** ⭐ *The full sequence the task demands was followed in order, and each step MEASURED:*
  | step | measured |
  |---|---|
  | gut BOTH `retrain` bodies | **883 passed, 1 skipped, 0 failed** — the mutation survived, so the coverage was genuinely absent |
  | add the boundary tests | — |
  | gut the ADAPTER's `retrain` | 2 named tests fail |
  | gut the SHIM's `retrain` | 1 named test fails |
  | break the adapter's OWN `supports_warm_start` | the rewritten refusal test fails |
  | restore, re-run | **886 passed, 1 skipped** — 883 + exactly the 3 tests added |

  🔑 **The selection for every number above is FOUR paths**, not § 6's three:
  `uv run pytest tests/unit/services/test_training.py tests/unit/flows/test_train_models.py
  tests/unit/adapters/ tests/unit/models/`. ⛔ *First labelled "§ 6's selection", which is FALSE:
  § 6 names three paths and collects **727** at this commit, against **886** for the four.
  Measured both. The widening is required — the shim tests live in `tests/unit/models/`, so § 6's
  selection could not have shown the shim mutation either way — but the label has to name the command
  that produced the number* ([[feedback_bind_published_numbers_on_values]]).
  - ⚖️ **§ 7 closed by the last row.** `_WrapperDefiningRetrainUnconditionally` RE-IMPLEMENTED
    `supports_warm_start` in its own body, so it asserted that the STAND-IN's copy of the rule worked
    and would have passed with the adapter's property deleted. Replaced with the REAL
    `ForecastInterfaceAdapter`; the mutation above proves the difference.
  - 🔴 **A NEAR-MISS I caught by running my own test**: the first refusal test passed **for the wrong
    reason.** FI's `RetrainableModel` protocol requires `artifact_scope`, `deserialize_artifact`,
    `input_requirement`, `predict`, `retrain`, `serialize_artifact`, `train` — and the recording fake
    has no `predict`, so the `isinstance` refusal fired on THAT, not on the missing `retrain`. ⛔ *A
    pass indistinguishable from a correct one.* Fixed by adding a fake that satisfies every member
    except the one under test, with the membership asserted in the test so the refusal can fire for
    one cause only. *[[feedback_red_first_must_prove_the_fault]] — the reason matters, not the colour.*
  - ⚖️ **The shim is exercised WITHOUT the `aquacast` extra**, per the task's decided option:
    `_shim_with_fake_inner` builds the REAL `AquacastShim` around a fake inner, bypassing `__init__`,
    the only code that imports `aquacast`. `_FakeInner` gained the `retrain` surface the shim binds to.
  - ⛔ **Delegation alone was not accepted as sufficient**: the shim test asserts the DELIVERED inputs
    are unit-translated (1.0 m³/s over 864 km² must arrive as 0.1 mm/day), so a pass-through
    delegation fails it — the same bar `train`'s existing test set.
- **2026-09-27 — T4 and T5 IMPLEMENTED** (the plan's phase 3, run together).
  **T4 — all four onboarding sites, each asserted individually, each with its own mutation red:**
  | site | form | mutation → |
  |---|---|---|
  | `flows/onboard_model.py` station | `params={}` **keyword** | fails only `TestOnboardingFlowSites::test_the_station_site_…` |
  | `flows/onboard_model.py` group | `params={}` **keyword** | fails only `…::test_the_group_site_…` |
  | `services/model_onboarding.py` station | **POSITIONAL** `{}` | fails only `TestOnboardingServiceSites::test_the_station_site_…` |
  | `services/model_onboarding.py` group | **POSITIONAL** `{}` | fails only `…::test_the_group_site_…` |
  - ⚖️ **The positional/keyword split was the trap the task named, and it is real**: a recorder reading
    `kwargs["params"]` alone would have covered two sites while appearing to cover four. Each recorder
    captures BOTH forms.
  - 🔴 **Every assertion pairs the value with a CALL COUNT.** *"Every captured call passed `{}`" is
    vacuously true of zero captured calls* — which is what T4's Verification forbids, and what would
    have happened silently had a unit been skipped before reaching the train step.
  - ⚠️ Both `train_*_model` and `assemble_*_training_data` are imported INSIDE `onboard_model`, so they
    are patched on their SOURCE modules. Assembly is stubbed: the site under test is the train call, and
    real observations would only add ways to be skipped before reaching it.

  **T5 — 399's headline blocker, covered through REAL discovery.**
  - ⭐ `importlib.metadata.entry_points` is patched so the actual `discover_models()` loads a raw FI
    model and actually calls `adapt_if_fi`. ⇒ The flow trains a REAL `ForecastInterfaceAdapter` with no
    resolver of its own — production's shape. ⛔ *Every other train/retrain test injects
    `models={...}`, so discovery never ran and the adapter was never the thing trained; that is exactly
    why deleting the wiring failed no test.*
  - 🔬 **The red is verbatim what the task demands**: with the wiring block deleted, the flow records
    `station_code_resolver required for GROUP input conversion / train / predict`.
  - ⚠️ *Two dead ends on the way, both in the requirement the fake FI model declares: discovery rejects
    an `InputRequirement` with no `future_known` ("cannot derive forecast horizon"), and the raw model
    must declare `model_tier`/`alert_eligibility` for the classification check. Neither is a defect —
    recorded because the next person writing a discovery-level test will hit both.*

  🔬 **Two near-misses in my own MEASUREMENT, not in the code** — both would have reported success:
  a shell helper that never ran, so a mutation sweep printed NOTHING and could have been read as "no
  failures"; and a filter that matched log lines containing `passed=True` instead of the pytest
  summary, which left two of the four sites unverified while looking verified. ⇒ *Caught by re-running
  with a tighter filter. [[feedback_well_formed_answers_to_the_wrong_question]] — an empty result is
  not a negative result.*
