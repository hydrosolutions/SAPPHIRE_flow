# FI issue draft — `retrain` can neither REFUSE a base artifact nor record that it used one

**Status:** DRAFT, ready to file at `hydrosolutions/ForecastInterface`.
⛔ *Drafted by SAP3; filing is the owner's.*
**Raised by:** SAPPHIRE Flow (SAP3), 2026-09-25.
**Measured against:** ForecastInterface **v0.1.20**, aquacast **0.1.356**, both as installed in the
SAP3 forecast worker image `sapphire-flow-aquacast:0.1.986`.
**Related:** `002` (declaration the contract cannot express), `003` (declaration never validated) —
same shape as both: the contract is silent, and the silence is currently absorbed in prose.

---

## Summary

`RetrainableModel.retrain()` exists, aquacast implements it, and its signature matches the contract
exactly. ⭐ **Nothing below asks for a new capability** — warm-start retrain is already agreed and
already built on the model side.

Two things the contract cannot express, both of which become load-bearing the moment `retrain` is
actually called:

1. **`retrain` has no failure return.** `predict` may return a typed `ModelFailure`; `train` and
   `retrain` return `TrainedArtifact` or nothing. An **anticipated** refusal — "this fine-tuning
   data is not compatible with this base artifact" — therefore has nowhere to go but an exception.
2. **A retrained artifact does not record its parent.** `TrainedArtifact` is a marker Protocol with
   no members, and its docstring defers provenance to "a LATER phase (Phase 4)". For `train` that
   deferral is comfortable. For `retrain` it is not: the artifact is *derived*, and the identity of
   what it was derived from exists only at the moment of the call.

## What was measured

ForecastInterface v0.1.20, aquacast 0.1.356, 2026-09-25.

1. **The contract exists and is honoured.** `interface/protocol.py` defines
   `RetrainableModel.retrain(base_artifact, inputs, *, config, rng) -> TrainedArtifact`, commented
   *"Warm-start retrain — OPTIONAL. SAP3 checks `isinstance(model, RetrainableModel)` to know whether
   warm-start is supported; otherwise it falls back to `train`."*
   `aquacast.operational.model.AquacastModel.retrain` has the **identical** signature.
   ⚠️ *SAP3 does not yet call it — that is our gap, not FI's, and it is being closed separately.*
2. **The refusal is real, and it raises.** `aquacast/finetuning/compat.py::check_feature_compatibility`
   raises `SchemaContractError` when the donor checkpoint's feature manifest does not match the
   fine-tune config — per resolution, per dynamic feature, per static feature and per target, naming
   the first differing index. `FineTuner.run()` calls it immediately after loading the donor bundle,
   **before** any training work.
3. **`FailureCause` already has the right vocabulary** — `INPUT_DATA`, `RESOURCE`, `MODEL_ERROR`,
   `CONFIGURATION`, `DEPENDENCY` (`interface/result.py`). A feature-manifest mismatch is squarely
   `INPUT_DATA` or `CONFIGURATION`.
4. 🔴 **But `ModelFailure` cannot be reused as-is:** it carries a required `issue_datetime`, which is
   a *prediction* concept. A training call has no issue time. ⇒ Reusing it would force a meaningless
   value into a required field.
5. **`TrainedArtifact` is a marker Protocol with no members** (`interface/artifact.py`), explicitly:
   *"Rich provenance metadata (scope, region, training period, hashes, seed, product versions) …
   deferred to a LATER phase (Phase 4)."*

## Why this is worth raising NOW rather than at Phase 4

⭐ **`retrain` is what converts the deferral from comfortable to lossy.**

A `train` artifact has no parent, so provenance can be reconstructed later from the training inputs.
A `retrain` artifact's parent is knowable only at the call. If it is not captured there, it is not
recoverable afterwards.

⚠️ **Where the caller picks the donor, the caller can capture it** — SAP3's Plan 399 does, so that
case needs nothing from FI. The gap is the artifact whose donor the consumer did **not** choose:
produced elsewhere, or handed over already fine-tuned. ⛔ *We are asking for one edge on THOSE, not
for FI to record what we already know.*

🔴 **This has already cost us once, on the artifact we would be fine-tuning.** SAP3's provenance row
for `cmal_small` records, in the operator's own words, that the training revision is
*"genuinely unrecoverable — the bundle records aquacast 0.1.346 while the runtime pins 0.1.356, so
the training revision is not determinable from either."* That artifact has no parent. Its successor
would, and we would like to be able to say so.

## What we are asking for

### 1. A way for `train` / `retrain` to REFUSE without raising

⚖️ **SAP3 owner, 2026-09-25:** *"we can only check the data format and then compare the model results
and accept a trained model or not. but right, we might need a failure code we can propagate."*

⇒ The acceptance decision is a two-stage thing, and only the **first** stage belongs in the
contract:

| stage | who decides | what the contract needs |
|---|---|---|
| **data format compatible?** | the model — it alone knows its manifest | a typed refusal it can RETURN |
| **is the retrained model any good?** | SAP3 / the operator, by comparing results | nothing — this is skill evaluation, outside FI |

⛔ *We are explicitly NOT asking FI to express model quality, acceptance thresholds, or a
promote/reject decision.* Only: **"I cannot train on this input, and here is why, as data."**

**Shape we would find natural** (the modeller's call, not ours):
- a `TrainingResult = TrainedArtifact | TrainingFailure` union, mirroring
  `ModelResult = ModelSuccess | ModelFailure`; **or**
- a `TrainingFailure` reusing `FailureCause` but **without** `issue_datetime` (§ 4).

⚠️ *We are not asking for `train`'s existing signature to break.* If a backwards-compatible route
matters, an optional return variant on `retrain` alone would already cover the case that motivated
this.

### 2. Parent identity on a retrained artifact

Enough to answer **"which artifact was this fine-tuned from?"** — a stable identifier of the base
artifact, carried on or alongside the result.

⛔ *Not the full Phase 4 provenance set.* We are asking for **one edge**, not the graph. Scope,
region, training period, seed and product versions can stay deferred; SAP3 already records those
itself. What SAP3 **cannot** record is a parent it was never told about.

⚠️ *If FI would rather keep `TrainedArtifact` a pure marker, an equally good answer is a documented
guarantee that the model embeds the parent id in its own serialized bytes and exposes it on
deserialize — we can consume either. The requirement is that the answer exists, not that it lives in
a particular place.*

### 3. Is a caller allowed to REFUSE rather than fall back?

`RetrainableModel`'s comment says SAP3 *"falls back to `train`"* when warm-start is unsupported.

⚖️ **SAP3 owner, 2026-09-25: we will REFUSE instead.** *"agreed to refuse to fall back to training
from start."* ⇒ Asking to fine-tune and silently getting a from-scratch retrain would discard the
global pre-training that motivates the whole exercise, and the resulting artifact would be
indistinguishable from a fine-tuned one in our records.

⇒ **We are not asking FI to change the behaviour — we are asking the CONTRACT COMMENT ITSELF to be
amended**, so it says the fall-back is one permitted choice and refusing is another, rather than
stating what SAP3 does. ⛔ *No signature change.*

🔴 **Until this is filed, the divergence is documented ONLY inside SAP3** — sufficient as a record,
not yet as communication.

## Explicitly NOT part of this issue

- **The fine-tuning strategy surface.** aquacast's `FineTuningConfig` carries `strategy` (seven
  values: `full_model`, `last_layer`, `static_only`, `lora`, `lora_ensemble`, `svd`, `svd_ensemble`),
  `lr`, `rank`, `alpha`, `lora_target`, `n_members`, `normalization` and `per_branch`. All of it
  reaches the model through FI's opaque `config: Mapping[str, Any]`, so SAP3 cannot **validate** it
  or reason about it. ⛔ *An earlier version of this line also said SAP3 cannot **record** which
  strategy produced an artifact. **That is wrong** — opacity does not prevent us storing the mapping
  we supplied, and SAP3's Plan 399 T2 does exactly that.* ⇒ The remaining limitation is validation,
  not provenance.
  ⚖️ **SAP3 owner, 2026-09-25: "agreed strategy surface is currently invisible. it's config.
  ACCEPTED for v1."** ⛔ *Recorded here so it is a known, accepted limitation rather than an
  oversight — not a request.*
- **Whether fine-tuning is the right thing to do** for ~148 Swiss basins off a globally-trained
  model. That is a modelling question we are raising with the modeller directly, not a contract gap.
- **SAP3 calling `retrain` at all.** Ours to build; tracked on our side.
