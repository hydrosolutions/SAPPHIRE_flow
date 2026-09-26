---
status: DRAFT
created: 2026-09-25
plan: 399
title: SAP3 never calls the warm-start retrain both sides already implement
scope: Make SAP3 able to fine-tune an existing model artifact — the FI `RetrainableModel.retrain()` capability check, the passthrough down to the model, a channel for the fine-tuning config (there is none today), base-artifact selection, and recording which artifact a retrained one came from. NOT the FI contract changes in fi-issue 004 (a typed training failure, FI-side parent identity), NOT the fine-tuning STRATEGY surface (owner: accepted as opaque config for v1), NOT judging whether a retrained model is good (that is skill comparison, ours and outside this plan), NOT ERA5-Land onboarding (deliberately superseded by this approach).
depends_on: []
blocks: []
related: [262, 307, 329]
open_decisions: [D3]   # D1 and D2 closed by the owner 2026-09-25
source: 2026-09-25 — the owner asked whether we could fine-tune `cmal_small` on Swiss forcing rather than onboard ERA5-Land. Measured the same day: the capability exists on BOTH sides and SAP3 never calls it.
---

# Plan 399 — the warm-start retrain nobody calls

⚖️ **Plan number 399 GRANTED by the owner, 2026-09-25** (drafted as 330; renumbered on the grant).

## Status

**DRAFT.** ⛔ No implementation until an independent review and a READY flip.
⚖️ **D1 and D2 closed by the owner 2026-09-25.** **D3 remains open and is load-bearing** — there is
no channel for model config today at all, so without it nothing can select a fine-tuning strategy
and the rest of the plan cannot run.

## Why this plan exists

`cmal_small` was trained on ERA5-Land (Caravan) and is served MeteoSwiss RhiresD/TabsD plus
ICON-CH2-EPS. ⭐ **The owner's chosen fix is to fine-tune on Swiss forcing rather than onboard
ERA5-Land** — which is cheaper *and* keeps the global pre-training that is likely carrying most of
the model's skill.

🔑 **Nothing new has to be invented.** The contract exists, the model implements it, and the training
data assembler already reads Swiss forcing. **SAP3 is the only side that does not participate.**

## What is measured

FI **v0.1.20**, aquacast **0.1.356**, `origin/main`, and the live staging DB — 2026-09-25.

1. ✅ **The contract exists.** `interface/protocol.py::RetrainableModel.retrain(base_artifact,
   inputs, *, config, rng) -> TrainedArtifact`, commented *"SAP3 checks
   `isinstance(model, RetrainableModel)` … otherwise it falls back to `train`."*
2. ✅ **aquacast implements it**, with an **identical** signature
   (`aquacast.operational.model.AquacastModel.retrain`), backed by a real fine-tuning subsystem:
   `aquacast/finetuning/` — LoRA, SVD, freeze strategies, and `check_feature_compatibility`. Its
   `FineTuner` already accepts an **in-memory data source**, commented *"operational retrain"*.
3. 🔴 **SAP3 never calls it.** `grep -rn "RetrainableModel\|retrain" src/` returns **no call site** —
   only unrelated prose in four comments.
4. 🔴 **Four layers lack the passthrough**, and all four are ours:
   | layer | file | has `retrain`? |
   |---|---|---|
   | our model Protocols | `protocols/forecast_model.py` | ❌ `train` / `predict` only |
   | the FI adapter | `adapters/forecast_interface.py:1058` | ❌ `train` only |
   | the aquacast shim | `models/aquacast/_shim.py:606` | ❌ `train` / `predict` only |
   | the training service | `services/training.py:37,47` | ❌ both call `model.train(...)` |
5. 🔴 **THERE IS NO CALLER-SUPPLIED CONFIG CHANNEL.** `flows/train_models.py:170` hardcodes
   **`params: dict = {}`**, which flows unchanged to `services/training.py:43,53` →
   `adapters/forecast_interface.py:1070` (`config=params`) → `_shim.py:609` (`config=config`).
   ⇒ **No caller can supply training configuration.** Fine-tuning cannot work without one: the
   strategy alone is a required choice (§ 7). ⇒ **D3.**
   ⛔ *An earlier version of this item said "no model receives ANY configuration". **That
   overstates it**, and the overstatement propagated into a false conclusion (§ 5a). Three distinct
   things must be kept apart:*
   | | what | where it comes from today |
   |---|---|---|
   | the **vendored template** | the model's own architecture/feature config | `_shim.py:581-582` — `ModelTemplate.from_yaml(...)` at CONSTRUCTION. ⭐ *Models DO receive this.* |
   | **run params** | per-run training overrides | 🔴 nothing — the hardcoded `{}`. This is D3's gap. |
   | the **donor's training params** | what the base artifact was trained with, externally | ⚠️ **UNKNOWN** — see § 5a |
5a. ⚠️ **What the donor was trained with is UNKNOWN, not absent.**
   ⛔ *An earlier version inferred "`cmal_small` has no params, so the first retrain records none"
   from § 5's empty dict. **That inference is invalid.*** `cmal_small` was **imported**, not trained
   here (`services/model_import.py:401-404` — *"This never calls model.train"*), so SAP3's empty run
   params say nothing about whether its external training used a params file, whether that file
   survives, or whether the bundle carries equivalent configuration.
   ⇒ **The stored params path is nullable because it may be UNKNOWN or UNAVAILABLE — not because it
   is known to be absent.** T4 must not mandate NULL for this donor without inspecting its
   provenance first.
6. ✅ **The group assembler reads the operational REANALYSIS binding** —
   `services/training_data.py:630` `assemble_group_training_data` delegates per station
   (`:639-650`) and station assembly reads `station_store.fetch_reanalysis_bindings(...)` (`:460-469`),
   the same binding `operational_inputs.py:1004-1006` uses.
   🔴 **But forcing parity is PARTIAL, and an earlier version of this item overstated it.**
   ⛔ *It claimed "the train/serve forcing match this plan exists to achieve is already true of the
   assembler". It is true of the PAST leg only:*
   | leg | training | serving |
   |---|---|---|
   | past | reanalysis binding | reanalysis binding ✅ **same** |
   | future | **reanalysis** (`_future_dynamic_from_forcing`, `training_data.py:571`) | **NWP forecast records** (`build_future_dynamic_frame`, `operational_inputs.py:1090`) ⛔ **different** |
   ⇒ **Fine-tuning on Swiss reanalysis fixes the CLIMATOLOGY mismatch (ERA5-Land vs RhiresD/TabsD).
   It does NOT teach the model ICON's forecast-error characteristics**, because the future leg it
   trains on is reanalysis either way. ⚠️ *That limitation is equally true of the ORIGINAL Caravan
   training, so this plan does not make it worse — but the plan must not be read as closing a gap it
   does not close.*
7. **The strategy surface is real and rich.** aquacast's `FineTuningConfig`: `strategy` ∈
   {`full_model`, `last_layer`, `static_only`, `lora`, `lora_ensemble`, `svd`, `svd_ensemble`},
   plus `lr`, `rank`, `alpha`, `lora_target`, `lora_layers`, `n_members`, `svd_init_std`,
   `svd_train_head`, `normalization` ∈ {`pretrained`, `refit`}, `per_branch`.
   ⚖️ *Owner 2026-09-25: the surface stays opaque config for v1 — but something must still SELECT it.*
8. **Loading a base artifact is already easy** — ⛔ *but an earlier version stated the contract
   wrongly.* `ModelArtifactStore.fetch_artifact(artifact_id)` returns
   **`tuple[ArtifactId, bytes] | None`** (`protocols/stores.py:531-533`), **not** `(sha256, bytes)`:
   `store/model_artifact_store.py:109-112` verifies the hash internally and returns
   `(artifact_id, stored)`. ⚠️ **And the existing call site is not a model to copy** —
   `flows/train_models.py:613` discards the first element and `:623` deserializes the ORIGINAL
   in-memory bytes, not the fetched ones. ⇒ **T3 must deserialize the FETCHED bytes and reject a
   missing donor explicitly** (`None` is a real return value).
9. 🔴 **Nothing records that one artifact came from another.** `model_artifacts` columns are
   `id, model_id, station_id, group_id, status, artifact_path, training_period_start/end,
   trained_at, promoted_at, promoted_by, superseded_at, created_at, sha256_hash` — **no parent.**
   The existing `store/model_artifact_lineage.py` records artifact→**basin** lineage only.
   ⭐ **We do not need FI to tell us the parent: WE choose the base artifact, so we know its id.**
   *(fi-issue 004 § 2 matters for artifacts trained elsewhere; it does not block this plan.)*
10. **Group training has never actually run here.** Of **903** artifacts on staging, exactly **one**
    is group-scoped — `cmal_small`, and it was **imported**, not trained. ⚠️ *So T3's path is
    unexercised in production, and the plan must not assume it works because tests pass.*
11. 🔴 **AND THERE IS A REASON IT NEVER RAN — group training is BROKEN TODAY.**
    `flows/train_models.py:435` calls `discover_models()`; `services/model_registry.py:158` wraps
    each with **`adapt_if_fi(raw_instance)`** and attaches **no station-code resolver**; the adapter
    needs one for every GROUP path and raises `ConfigurationError`
    *"station_code_resolver required for GROUP input conversion / train / predict"*
    (`adapters/forecast_interface.py:1626-1630`).
    ⇒ **A group train — and therefore a group RETRAIN — fails before the model is ever reached.**
    ⭐ *`services/model_registry.py:30` `build_station_code_resolver()` already exists; it is simply
    not wired into the training flow.* ⛔ *Mirroring `train` for `retrain` would faithfully
    reproduce this failure, which is why T3 owns it.*

## Owner decisions

### D1 — which artifact is the base? **⚖️ CLOSED — owner, 2026-09-25: an explicit INPUT.**

| | option | cost |
|---|---|---|
| **(a)** ⭐ | **The caller names it explicitly** (an artifact id parameter on the run). | Unambiguous, and a fine-tune is a deliberate act, not a schedule. ⚠️ One more thing to get right when triggering. |
| (b) | Implicit: the model's current ACTIVE artifact. | Convenient. ⛔ *A scheduled retrain would then silently fine-tune a fine-tune, drifting generation by generation with nothing naming the original.* |

**⚖️ CLOSED on (a).** Owner: *"the parent model is an input so it can be clearly identified and
stored."*

⇒ **The base artifact is named by the caller**, never inferred. ⛔ *No implicit "current ACTIVE
artifact" resolution — (b) stays rejected.*

🔑 **AND the base model's own configuration is stored with it.** Owner: *"that model should also
have its base config so that can also be stored (I mean the paths to params and config here)."*

⇒ A retrained artifact records **three** things about what it came from:
| | what | why it is recoverable |
|---|---|---|
| the base **artifact id** | which weights were fine-tuned | chosen by the caller (D1) — known at the call |
| the path to the base **config** | the model template the donor was built from | `model_artifacts.artifact_path` already exists; the vendored config path is derivable from the model |
| the path to the base **params** | the configuration the donor was trained with | D3's channel, once it exists — ⚠️ *for the CURRENT `cmal_small` this is genuinely absent (§ 5: nothing has ever received params), so the field is nullable and the first retrain records none* |

⚠️ **A path alone is weak provenance — pin it with the hash that already exists.** *`_shim.py`'s
`config_hash` (SHA-256 of the vendored config's bytes) is already computed and already used by
`services/model_import.py` to refuse an artifact/config mismatch. Storing the path WITHOUT it would
record a pointer that silently changes meaning when the file does.* ⇒ **T4 stores path + hash**,
which is what makes the owner's requirement actually answerable later.

### D2 — what if the model does NOT support retrain? **⚖️ CLOSED — owner, 2026-09-25: REFUSE.**

FI's comment says SAP3 *"falls back to `train`"*.

| | option | cost |
|---|---|---|
| **(a)** ⭐ | **REFUSE**, with a typed error naming the model. | ⛔ *A silent fall-back means asking to fine-tune and getting a FROM-SCRATCH retrain — which discards exactly the global pre-training that motivated this plan, and the resulting artifact is indistinguishable in the table.* |
| (b) | Fall back to `train`, as FI suggests. | Matches the contract's stated expectation. 🔴 *Dangerous here for the reason above.* |

**⚖️ CLOSED on (a) — REFUSE.** Owner: *"agreed to refuse to fall back to training from start."*

⇒ **A model that does not implement `retrain` gets a typed error naming it.** ⛔ *There is no
fall-back path to `train`, at any layer.*

🔴 **This is a DELIBERATE divergence from the FI contract's own comment**, which says SAP3
*"falls back to `train`"*. ⇒ **fi-issue 004 must record it** — ⛔ *a provider that refuses where the
contract says it falls back is a real interoperability difference, and leaving it undocumented is
how the next reader finds it the hard way.*

### D3 — where does the fine-tuning config come from? **OPEN — nothing can run without this.**

§ 5: `params` is hardcoded `{}`, so this is not "extend the channel", it is "there is no channel".

| | option | cost |
|---|---|---|
| **(a)** ⭐ | A **run parameter** on the training flow, passed through to `params`. | Smallest, and matches a fine-tune being a deliberate one-off. ⚠️ Nothing records what was used unless the artifact row keeps it. |
| (b) | Per-model config **in the database**, alongside the assignment. | Durable and auditable. ⛔ *A new config surface, for a v1 the owner has already scoped as opaque.* |
| (c) | A vendored YAML beside the model config. | Consistent with how the model's own config ships. ⛔ *Changing a strategy would need an image rebuild.* |

**Recommendation: (a) now**, ⚠️ **and record the exact config used on the artifact**, or § 7's
"opaque for v1" becomes "unknowable forever" — the failure already on record for this very model,
whose training revision is *"genuinely unrecoverable"*.

## Tasks

### T1 — Carry `retrain` through the four layers (D2)

**Outcome.** SAP3 can call `retrain` on a model that supports it, and refuses cleanly on one that
does not.

**In.**
- An optional retrain capability on our model Protocols, mirroring FI's `RetrainableModel` —
  ⛔ *optional, not required: every Swiss statistical model must keep satisfying the protocol
  untouched.*
- The passthrough in `adapters/forecast_interface.py` and `models/aquacast/_shim.py` (§ 4).
- `retrain_station_model` / `retrain_group_model` in `services/training.py`, mirroring the existing
  pair.
- **The capability check** — `isinstance(model, RetrainableModel)` — and, per **D2 (closed:
  REFUSE)**, a typed error naming the model when it is false. ⛔ *No fall-back to `train` at any
  layer.*
- ⚠️ **A note in `docs/fi-issues/004` recording the divergence** — FI's own comment says SAP3 falls
  back to `train`; we refuse. ⭐ *One paragraph, not a new issue.*

**Out.** ⛔ Changing `train`'s signature or behaviour. ⛔ The FI package (fi-issue 004). ⛔ Making
retrain mandatory on any protocol.

**Pre-change.** A RED test: **a model implementing retrain is asked to retrain and the base artifact
reaches it**, asserted on the artifact the model actually received — ⛔ *not "the method exists",
which a stub satisfies.*

**Verification.**
- The base artifact reaches the model **unchanged** — round-tripped through serialize/deserialize
  and compared, because that is the path a stored artifact takes (§ 8).
- 🔴 **A model WITHOUT retrain is REFUSED with a typed error naming it** (D2), asserted.
  ⛔ *Explicitly assert that `train` is NOT called — "an error was raised" would also pass on an
  implementation that trained from scratch and then failed for some other reason.*
- 🔴 **Every existing model still trains unchanged** — the Swiss statistical models do not implement
  retrain and must be untouched.

### T2 — Give model config a route (D3)

**Outcome.** A caller can supply model configuration, and what was used is recoverable afterwards.

**In.** D3's channel, replacing `flows/train_models.py:170`'s hardcoded `{}` (§ 5), and **the config
recorded against the produced artifact**.

**Out.** ⛔ Validating or typing the fine-tuning strategy — owner: opaque for v1 (§ 7). ⛔ Changing
what any model does with config it already ignores.

**Pre-change.** A RED test: **a config supplied by the caller arrives at `train`**. ⚠️ *It must fail
because the config did not arrive — not because a parameter is missing from a signature.*
⛔ *An earlier version said "`train`/`retrain`" — but `retrain` does not exist until T1, a LATER
phase, so that half was unsatisfiable here. T1 covers retrain's own config arrival.*

**Verification.**
- Config supplied → received by the model, **byte-identical**.
- 🔴 **No config supplied → the existing behaviour is unchanged** (an empty mapping), asserted,
  because every current model trains through this path today.
- The config used is readable back from the artifact record afterwards.

### T3 — Select a base artifact and run a retrain end to end (D1)

**Outcome.** A fine-tune of `cmal_small` on Swiss forcing produces a stored artifact.

**In.**
- The base artifact as an **explicit caller-supplied input** (D1, closed) — ⛔ *never resolved
  implicitly from the model's ACTIVE artifact*.
- 🔴 **The station-code resolver wired into the training flow (§ 11)** — `build_station_code_resolver()`
  exists and is not attached, so EVERY group path raises before the model is reached. ⛔ *This is not
  optional polish: without it T3 cannot run at all, and mirroring `train` would reproduce the same
  failure.* ⭐ *It also explains § 10 — group training never ran because it cannot.*
- Loading the donor via `fetch_artifact`, **deserializing the FETCHED bytes**, and **rejecting a
  missing donor explicitly** — `None` is a real return value (§ 8).
- ⚠️ **A path that does NOT auto-promote.** `flows/train_models.py:208` calls
  `store_and_promote_artifact()`; the retrain path must store WITHOUT promoting.
- 🔴 **One real run on staging**, since § 10/§ 11 show this path has never worked here.

**Out.** ⛔ Promoting the result, assigning it, or letting it serve a forecast — that is a separate,
owner-gated act. ⛔ Judging whether it is any good (skill comparison, outside this plan).

**Pre-change.** 🔴 **A RED test at the FLOW level, through `discover_models()` / `adapt_if_fi`** —
⛔ *an earlier version said "T1/T2's tests cover the mechanism". They do not: they exercise the
adapter directly and so never hit § 11's missing resolver, which is exactly the failure that has kept
group training from ever running.* The test must fail with the resolver error today.

**Verification.**
- An artifact is produced, stored, and deserializes back to a working model.
- 🔴 **A run that names no base artifact does NOT silently train from scratch** — asserted (D1).
- 🔴 **It is NOT promoted and NOT assigned** — asserted, not assumed, and note that the existing
  store step promotes by default (`flows/train_models.py:208`). ⭐ *`cmal_small`'s current artifact
  keeps serving until a human decides otherwise.*
- ⚠️ **The PAST-leg forcing it trained on is the same reanalysis binding the operational path reads**
  (§ 6) — recorded from the run, because a plausible failure is quietly assembling something else.
  ⛔ *Do NOT claim full train/serve forcing parity: § 6 measured that the future leg differs
  (reanalysis in training, NWP forecast in serving) and this plan does not change that.*

### T4 — Record which artifact a retrained one came from (§ 9)

**Outcome.** "What was this fine-tuned from?" is answerable from our own records.

**In.** Per **D1 (closed)**, the produced artifact records **all three**: the parent artifact id,
the path to the base **config**, and the path to the base **params** — each stored **with the hash
that pins it** where one exists (D1's note: a bare path silently changes meaning when the file
does). ⭐ *We know all of it because the caller named the base (D1) — this does not wait on
fi-issue 004.* With a migration. Follow `store/model_artifact_lineage.py`'s precedent: a standalone
helper, **not** a widening of the cross-cutting `ModelArtifactStore` Protocol.
⚠️ **The params path is NULLABLE because it may be UNKNOWN or UNAVAILABLE** (§ 5a) — ⛔ *an earlier
version said "the first retrain will record none, because no model has ever received params". **That
inference was invalid***: `cmal_small` was imported, not trained here, so our empty run params say
nothing about its external training. ⇒ **Inspect this donor's provenance before concluding NULL**;
model the column as nullable either way, and record WHY it is null (unknown vs genuinely none) rather
than just that it is.

**Out.** ⛔ Backfilling a parent for existing artifacts — there is exactly one candidate and it has
none. ⛔ The rest of FI's deferred provenance set (scope, region, seed, product versions); SAP3
already records those.

**Pre-change.** A RED test: **a retrained artifact records its base; a freshly trained one records
none.**

**Verification.**
- Parent id, base config path and base params path recorded on retrain; **all three absent on a
  fresh train**, asserted both ways.
- The parent is resolvable to a real artifact row.
- 🔴 **A base whose params are UNKNOWN still retrains, and stores a NULL params path with a reason**
  — asserted (§ 5a). ⚠️ *Whether `cmal_small` is that case is a T4 measurement, not an assumption.*
- Each stored path carries its hash where one exists, and the hash matches the file it names.
- 🔴 **Deleting or superseding a parent does not orphan the child's record** — ⚠️ *state the intended
  behaviour rather than discovering it; supersession already exists (Plan 328).*

## Explicitly out of scope

- **fi-issue 004** — a typed training failure, and FI-side parent identity. ⭐ *Neither blocks this
  plan:* the compatibility refusal surfaces as an exception we can catch, and we know the parent
  ourselves.
- **The fine-tuning strategy surface** — ⚖️ owner, 2026-09-25: *"agreed strategy surface is currently
  invisible. it's config. accepted for v1."*
- **Whether a fine-tuned model is BETTER** — skill comparison against the current artifact. ⚠️ *The
  reason to fine-tune is a measured forcing mismatch, but this plan does not prove the cure works;
  it makes the cure runnable.*
- **ERA5-Land onboarding** — the alternative the owner deliberately set aside. ⛔ *The backfill
  service exists and has never run; zero ERA5-Land rows exist. Recorded so the road not taken is not
  later mistaken for a gap.*

```json
{
  "phases": [
    {"phase": 1, "tasks": ["T2"], "parallel": false, "decision": "D3 CLOSED",
     "note": "the config channel first — D3 is the ONE decision still open and it blocks everything; nothing can select a strategy without it"},
    {"phase": 2, "tasks": ["T1"], "parallel": false, "decision": "D2 CLOSED"},
    {"phase": 3, "tasks": ["T4"], "parallel": false,
     "note": "lineage BEFORE the first real run, so the first retrained artifact is not the one with no parent recorded"},
    {"phase": 4, "tasks": ["T3"], "parallel": false, "decision": "D1 CLOSED"}
  ]
}
```

## Changelog

- **2026-09-25** — drafted. Every claim measured against FI v0.1.20, aquacast 0.1.356, `origin/main`
  and the live staging DB the same day. ⭐ *The headline finding is that this plan asks for no new
  capability: the contract, the model implementation and the Swiss-forcing training assembler all
  already exist. SAP3 is the only side that does not participate.*
- **2026-09-25** — ⚖️ **Plan number 399 granted; D1 and D2 CLOSED by the owner.**
  - **D2 = REFUSE.** *"agreed to refuse to fall back to training from start."* ⇒ a model without
    `retrain` gets a typed error; there is no fall-back to `train` at any layer. 🔴 **This is a
    deliberate divergence from the FI contract's own comment**, so T1 now also records it in
    `docs/fi-issues/004` — a provider that refuses where the contract says it falls back is a real
    interoperability difference.
  - **D1 = an explicit INPUT**, plus a requirement the draft did not have: *"that model should also
    have its base config so that can also be stored (I mean the paths to params and config here)."*
    ⇒ a retrained artifact now records **three** things — the parent artifact id, the base config
    path, and the base params path — not just parentage. T4 grew accordingly.
  - ⚠️ **Two consequences the owner's requirement surfaced, folded rather than discovered later:**
    a bare path is weak provenance, so each is stored with the hash that pins it (`config_hash`
    already exists and is already used to refuse an artifact/config mismatch); and the **params path
    must be NULLABLE**, because § 5 measured that no model has ever received params — so
    `cmal_small`, the very first base, has none to point at.
  - ⛔ **D3 remains open and still gates everything**, now declared as a machine-readable gate on
    phase 1 rather than only noted in prose.
