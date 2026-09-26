---
status: IMPLEMENTED_EXCEPT_STAGING_RUN
created: 2026-09-25
plan: 399
title: SAP3 never calls the warm-start retrain both sides already implement
scope: Make SAP3 able to fine-tune an existing model artifact — the FI `RetrainableModel.retrain()` capability check, the passthrough down to the model, a channel for the fine-tuning config (there is none today), base-artifact selection, and recording which artifact a retrained one came from. NOT the FI contract changes in fi-issue 004 (a typed training failure, FI-side parent identity), NOT the fine-tuning STRATEGY surface (owner: accepted as opaque config for v1), NOT judging whether a retrained model is good (that is skill comparison, ours and outside this plan), NOT ERA5-Land onboarding (deliberately superseded by this approach).
depends_on: []
blocks: []
related: [262, 307, 329]
open_decisions: []   # D1, D2 closed 2026-09-25; D3 closed 2026-09-26 — all by the owner
source: 2026-09-25 — the owner asked whether we could fine-tune `cmal_small` on Swiss forcing rather than onboard ERA5-Land. Measured the same day: the capability exists on BOTH sides and SAP3 never calls it.
---

# Plan 399 — the warm-start retrain nobody calls

⚖️ **Plan number 399 GRANTED by the owner, 2026-09-25** (drafted as 330; renumbered on the grant).

## Status

⚖️ **MERGED 2026-09-26 as PR #314** (`dd75a963` on `main`, version 0.1.994, migration **0060**).
**T1, T2 and T4 are complete. T3 is complete IN CODE but its one required run has NOT happened.**

🔴 **WHAT REMAINS — exactly one thing:**
> T3: *"One real run on staging"* — ⛔ **not done.** The host has been unreachable all session
> (`ssh sapphire@192.168.1.136` times out), and the run is **orchestrator-gated** in any case.
> ⚠️ **This is the only part of the plan nothing has exercised**: § 10/§ 11 measured that group
> training has never produced an artifact on any deployment — because it *could not* until this
> merge — so the retrain path has never met a real model. Unit and integration tests prove the
> mechanism; only this proves the thing works.

⚠️ **Also pending: the mini runs 0.1.986 and `main` is at 0.1.994** — this merge is NOT deployed.
⛔ *Merging is never deploying ([[project_qc_cadence_is_inferred_not_declared]]'s lesson).*

⭐ **Verified before merge:** the full unit suite; the CI shard that had failed, reproduced locally
(1661 passed); the four warm-start **integration** tests against a real PostGIS container; and
mutation checks on each changed line — including flipping the donor FK to `CASCADE`, which fails the
delete-refusal test, so `RESTRICT` is proven rather than merely written.

⚠️ **Three CI failures preceded the merge, all the same shape** — a FAKE more permissive than the
real thing: the fake store's own SHA-256 check masked the flow's new guard; the `aquacast` extra is
absent locally but installed in CI, so the shim changes were never exercised here; and
`ck_model_artifacts_scope_xor` (exactly one of station/group) is unenforced by the fake store, so
test artifacts seeded with neither passed every unit test. *Recorded so the next reader does not
trust a green unit run for a schema or shim change.*

---

**Previously READY** — flipped by the orchestrator 2026-09-26 on the owner's explicit instruction
(*"ok, flip ready and implement"*). ⚖️ **All three decisions closed by the owner** — D1 and D2 on
2026-09-25, D3 on 2026-09-26. **Five review rounds.** ⚠️ *An earlier version claimed "nine independent reviews"; the changelog
records **six** passes, and per this project's own rule the RECORD is what stands.*

⚠️ **Stated plainly: the round-5 fold itself is UNREVIEWED.** *The two round-5 reviewers read
`b8465e6c`; this state adds that fold — including four newly-MADE decisions (the donor-config
resolution table, the changed-template refusal, `RESTRICT` parent retention, and the four-row abort
rule).* ⇒ 🔑 **The owner directed that the plan AND the diff be independently checked AFTER
implementation instead of before.** *That is the mitigation, and it is deliberate — recorded here so
the sequencing is not mistaken for a skipped gate.*

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
   | the FI adapter | `adapters/forecast_interface.py:1062` | ❌ `train` only |
   | the aquacast shim | `models/aquacast/_shim.py:606` | ❌ `train` / `predict` only |
   | the training service | `services/training.py:37,47` | ❌ both call `model.train(...)` |
5. 🔴 **THERE IS NO CALLER-SUPPLIED CONFIG CHANNEL, AT SEVEN SITES.**
   `flows/train_models.py:170` hardcodes **`params: dict = {}`**, which flows unchanged to
   `services/training.py:43,53` → `adapters/forecast_interface.py:1070` (`config=params`) →
   `_shim.py:609` (`config=config`). ⛔ *An earlier version named only that one site.* Measured, the
   empty mapping is hardcoded at **seven**:
   | file | lines |
   |---|---|
   | `flows/train_models.py` | `:170` |
   | `flows/onboard_model.py` | `:368`, `:375` |
   | `services/model_onboarding.py` | `:552`, `:585`, `:1453`, `:1457` |
   ⇒ **No caller can supply training configuration, on any path** — ⚠️ *including onboarding, so
   "every current model trains through THIS path" was also wrong: a newly onboarded model trains
   through the onboarding path.* Fine-tuning cannot work without one: the
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
   `services/training_data.py:620` `assemble_group_training_data` delegates per station
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
   ⚠️ *"No parent column" describes the GAP, not the fix — § 14 decides the fix is a side table, and
   `model_artifacts` gains no column.*
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

12. 🔴 **A STRUCTURAL `isinstance` ON THE ADAPTER WOULD SILENTLY DEFEAT D2.**
    `ForecastInterfaceAdapter` (`adapters/forecast_interface.py:463`) wraps `self._model` and has
    **no `__getattr__` passthrough** — the repo already learned this and warns in-code at
    `:492-502`, where `config_hash` needs an explicit proxy because otherwise *"every real FI model
    reaches `import_external_artifact` wrapped, and `getattr(model, 'config_hash', None)` silently
    returns `None` regardless of what the wrapped model declares, disabling the drift check
    entirely."*
    ⇒ **The same trap, with teeth:** if T1 gives the adapter an unconditional `retrain` method, a
    `runtime_checkable` structural `isinstance` passes for **every** FI model — *including ones with
    no `retrain`* — so **D2's refusal never fires for the exact case it exists for**, and the failure
    surfaces as an `AttributeError` deep inside the adapter instead of a typed error naming the
    model. ⛔ *And if T1 does NOT give the adapter a `retrain`, no FI model is ever retrainable.*
    ⇒ **Support must be interrogated on the INNER model and surfaced through an explicit proxy**,
    following the `config_hash:492` precedent.
    ⚠️ **Which protocol matters, and the draft was imprecise.** *Against **FI's** `RetrainableModel`
    the adapter fails the structural check anyway — FI's protocol extends `ForecastModel` and so
    requires `input_requirement`, which the adapter does not expose (it exposes `data_requirements`,
    `adapters/forecast_interface.py:473`). The false-positive danger is real for a **SAP3-side**
    retrain protocol declaring only `retrain`, which is what T1 would naturally add.* ⛔ *Either way
    the conclusion stands — read support off the inner model — but T1 must say which protocol it is
    checking against, because the two behave differently.*

13. 🔑 **THE DONOR'S OWN CONFIG IDENTITY IS ALREADY RECORDED — and today's installed file is the
    WRONG place to read it.** `_shim.py:598-600` computes `config_hash` from
    `_config_path(type(self).CONFIG_FILENAME).read_bytes()` — **the currently installed template**.
    The DONOR's hash was captured separately at import
    (`services/model_import.py:460`, `config_hash=declared_config_hash`) and is readable back through
    `store/model_artifact_provenance.py`.
    ⇒ 🔴 **If the vendored template changes after import, recording its new path and hash would
    satisfy a naive "the hash matches the file it names" check while MISIDENTIFYING the donor's
    configuration.** ⛔ *T4 must resolve the donor's recorded provenance and compare, not hash
    whatever is on disk now.*
    🔴 **BUT provenance exists ONLY for an IMPORTED donor**, and `fetch_artifact_provenance` returns
    `| None`. `store/model_artifact_provenance.py:1-8` says so outright: it *"records that a
    `model_artifacts` row was **EXTERNALLY IMPORTED**, not trained by SAP3 … only ever called from the
    import path"*, and it has exactly one caller. ⇒ **A donor SAP3 trained itself has NO recorded
    config hash** — and D1 permits such a donor, because the caller names any base it likes (a retrain
    of a retrain).
    ⚖️ **DECIDED, so an implementer does not guess:**
    | donor | where its config identity comes from |
    |---|---|
    | imported | its provenance row's `config_hash` |
    | produced by SAP3 **after T4** | the record T4 wrote when SAP3 produced it |
    | produced by SAP3 **before T4** | **NULL, with the reason recorded** — the same discipline § 5a applies to the params path |
    ⛔ **Never fall back to hashing today's installed template.** *That is this section's own trap, and
    it would pass a naive check while naming the wrong configuration.*

14. ⚖️ **STORAGE SHAPE — DECIDED HERE, once: a SIDE TABLE, and `model_artifacts` gains no column.**
    The repo states the precedent explicitly at `db/metadata.py:177-182`:
    *"A join table (not a singular FK on `model_artifacts`) because a GROUP-scoped artifact spans many
    stations → many basins → many basin_versions. `model_artifacts` itself gains no new column."*
    ⇒ **ALL of this plan's provenance — parent artifact id, base config path, base params path, and
    the config used for the run — lives in ONE side table, shipped by ONE migration, owned by T4.**
    ⛔ *An earlier version left this as an instruction to decide ("Decide column-vs-side-table
    explicitly"), which is not a decision — and § 9's "no parent column" phrasing implied the
    opposite of T4's cited precedent.*
    ⚠️ **This refines which TASK records the config; it does not change D3.** *The owner's decision
    stands in full: supplied per run AND recorded. T2 ships the channel, T4 ships the record.*
15. ⚖️ **THE SEVEN `{}` SITES — DECIDED: T2 covers ONE, and the reason the others are excluded.**
    | site | disposition |
    |---|---|
    | `flows/train_models.py:170` | ✅ **IN** — the operational training path, and the one retrain uses |
    | `services/model_onboarding.py:552`, `:585` | ⛔ **OUT, by nature** — these are SMOKE TESTS on synthetic data (`_make_synthetic_group_training_data`, `ModelSmokeTestError`) proving a model can train at all. There is no caller and no config to supply |
    | `flows/onboard_model.py:368`, `:375`; `services/model_onboarding.py:1453`, `:1457` | ⛔ **OUT, deliberately** — real training, on the **ONBOARDING** path (Flow 12, `model_onboarding.py:1448` `# Step 3: Train`). ⚠️ *An earlier version called this "IMPORT time" — wrong, and it works against § 5a's whole argument: the IMPORT path (`services/model_import.py`) never trains at all.* Fine-tuning is a later act with an explicitly named base (D1); no closed decision needs onboarding to accept config. ⚠️ **T2 must ASSERT these are unchanged** |
    ⛔ *An earlier version said "either bring them into this task or state explicitly … that
    onboarding keeps `{}`" — an instruction to choose, not a choice.*

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
| the base **config** — its HASH always, its PATH only conditionally | the model template the donor was built from | 🔴 **Provenance stores a HASH and NO PATH** (`model_artifact_provenance` carries `source_repository`, `source_commit`, `config_hash`, `imported_at`, `imported_by`, `notes`). ⛔ *An earlier version of this cell asked for "the path … resolved from the DONOR's own provenance" — **not satisfiable**.* ⇒ The donor's **hash** is authoritative; the **path** can only be the INSTALLED one and is meaningful ONLY while the two agree (a mismatch is refused). A path that cannot be pinned records its reason. Resolved from the donor's provenance, not from today's installed file — `model_artifact_provenance` records the `config_hash` captured at import (`services/model_import.py:460`) and `store/model_artifact_provenance.py` reads it back. ⛔ *An earlier version said "derivable from the model", which would record the CURRENTLY installed template — see § 13* |
| the path to the base **params** | the configuration the donor was trained with | D3's channel, once it exists. ⚠️ **For `cmal_small` this is UNKNOWN, not known-absent** (§ 5a) — the field is nullable, and T4 must INSPECT the donor's provenance rather than assume. ⛔ *An earlier version of this cell asserted it "is genuinely absent … the first retrain records none". That is the invalid inference § 5a corrects, left standing here.* |

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

### D3 — where does the fine-tuning config come from? **⚖️ CLOSED — owner, 2026-09-26: (a), a run parameter.**

§ 5: `params` is hardcoded `{}`, so this is not "extend the channel", it is "there is no channel".

| | option | cost |
|---|---|---|
| **(a)** ⭐ | A **run parameter** on the training flow, passed through to `params`. | Smallest, and matches a fine-tune being a deliberate one-off. ⚠️ Nothing records what was used unless the artifact row keeps it. |
| (b) | Per-model config **in the database**, alongside the assignment. | Durable and auditable. ⛔ *A new config surface, for a v1 the owner has already scoped as opaque.* |
| (c) | A vendored YAML beside the model config. | Consistent with how the model's own config ships. ⛔ *Changing a strategy would need an image rebuild.* |

**⚖️ CLOSED on (a).** ⇒ **The configuration is supplied when the run is triggered.** ⛔ *No new
database surface (b), no vendored file (c).*

🔑 **The condition travels with it: the exact config used is RECORDED against the artifact it
produced.** ⚠️ *Without that, § 7's "opaque for v1" becomes "unknowable forever" — which is the
failure already on record for this very model, whose training revision is
"genuinely unrecoverable".* ⇒ **T2 ships the CHANNEL; T4 ships the RECORD** (§ 14). ⛔ *An earlier
version said "T2 owns both halves" — the recording moved to T4 so one task owns the whole record.*

⚠️ **This widens slightly beyond fine-tuning, deliberately.** *It is the same path all training uses
(§ 5's seven sites), so opening it touches ordinary training too. Nothing changes behaviour — no
caller sends configuration today — but T2 must PROVE that, which is why its verification asserts the
no-config case is unchanged.*

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
- 🔴 **The capability check, interrogated on the INNER FI model and surfaced through an EXPLICIT
  PROXY** (§ 12) — following the `config_hash` precedent at `adapters/forecast_interface.py:492-502`.
  ⛔ *NOT a bare `isinstance(adapter, RetrainableModel)`: the adapter has no `__getattr__`, so an
  unconditional `retrain` on it makes the structural check pass for EVERY FI model and D2's refusal
  never fires for the one case it exists for.* Per **D2 (closed: REFUSE)**, a typed error naming the
  model when support is absent. ⛔ *No fall-back to `train` at any layer.*
- ✅ **The `docs/fi-issues/004` divergence note is ALREADY WRITTEN** — § 3 of that draft, carrying the
  owner's decision and the ask that the contract COMMENT be amended. ⛔ *An earlier version of this
  bullet asked for it as if outstanding.* ⇒ **Nothing to do here; T1 only re-checks it still matches
  the implemented refusal.*
- 🔴 **DOCUMENTATION, which the plan had NONE of** (⛔ *`CLAUDE.md`: "every code change updates
  affected docs — no exceptions"; the fi-issue paragraph was the plan's only doc deliverable*):
  | file | why |
  |---|---|
  | `docs/spec/types-and-protocols.md:2096-2137` | **the authoritative Protocol spec** — `StationForecastModel` (`:2104`) and `GroupForecastModel` (`:2121`); T1 adds a capability to both |
  | `docs/touchpoint-maps.md:60` | "ForecastInterface / model execution" — the map for this subsystem |
  | `docs/touchpoint-maps.md:883` | "Training / hindcast / skill" — the map for this flow |
  | `docs/design/v0-flow678-training-pipeline.md` | describes this pipeline and already mentions retrain |
  ⛔ **Consult the two touchpoint maps BEFORE implementing** — `CLAUDE.md` requires it for any task
  touching those subsystems, and this plan cited no map at all.

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
- 🔴 **A NON-DEFAULT config supplied by the caller arrives at the INNER model through `retrain`** —
  asserted at BOTH boundaries it crosses (`adapters/forecast_interface.py:1070`'s
  `config=params` and `_shim.py:609`'s `config=config` have retrain equivalents).
  ⛔ *T2 delegated this to T1 ("T1 covers retrain's own config arrival") and an earlier version of T1
  did NOT cover it — so nothing verified it. Without this, an implementation can pass T2's
  ordinary-training config tests, hardcode a fine-tuning strategy, and still look correct.*
- 🔴 **An FI model WITHOUT `retrain`, WRAPPED IN THE ADAPTER, is refused with the typed error**
  (§ 12) — ⛔ *this is the case a bare structural `isinstance` would silently pass, so testing the
  inner model alone proves nothing.*
- 🔴 **Every existing model still trains unchanged** — the Swiss statistical models do not implement
  retrain and must be untouched.

### T2 — Give model config a route (D3)

**Outcome.** A caller can supply model configuration. ⛔ *Recoverability is **T4's** outcome, not this
one (§ 14) — an earlier version claimed both here.*

**In.** D3's channel, replacing `flows/train_models.py:170`'s hardcoded `{}` (§ 5). ⛔ *An earlier
version also required "the config recorded against the produced artifact" — which contradicted the
very next bullet ("NO storage and NO migration here") and is T4's (§ 14).*

- 🔑 **The CHANNEL only — `flows/train_models.py:170` (§ 15). NO storage and NO migration here**
  (§ 14: one side table, one migration, owned by T4). ⛔ *An earlier version of this task also
  required "the config recorded against the produced artifact" while declaring no schema change,
  which left two migrations in the same area with no owner.*
- ⚖️ **The entry point, DECIDED: a parameter on the EXISTING `train_models_flow`**
  (`flows/train_models.py:267`), threaded to `_train_model_task` at `:559` — which is called directly,
  not through `.map`, so no fan-out serialisation is involved. ⛔ *NOT a separate flow.* ⚠️ *An earlier
  version said "name the flow entry point", leaving it to the implementer — but the answer constrains
  this task's parameter shape three phases before T3 needs it.*
  ⛔ **Read `docs/standards/orchestration.md` first** — mandatory for flow work, and this plan cited no
  standards document until now.
- 🔴 **Documentation** (⛔ *`CLAUDE.md`: "every code change updates affected docs — no exceptions"*):
  `docs/standards/orchestration.md`'s flow-parameter conventions if this adds one.

**Out.** ⛔ Validating or typing the fine-tuning strategy — owner: opaque for v1 (§ 7). ⛔ Changing
what any model does with config it already ignores.

**Pre-change.** A RED test: **a config supplied by the caller arrives at `train`**. ⚠️ *It must fail
because the config did not arrive — not because a parameter is missing from a signature.*
⛔ *An earlier version said "`train`/`retrain`" — but `retrain` does not exist until T1, a LATER
phase, so that half was unsatisfiable here. T1 covers retrain's own config arrival.*

**Verification.**
- Config supplied → received by the model, **byte-identical**.
- 🔴 **No config supplied → the existing behaviour is unchanged** (an empty mapping), asserted,
  ⛔ *An earlier version justified this as "every current model trains through this path today" —
  **retracted by § 5**: a newly onboarded model trains through the ONBOARDING path.* ⇒ **Assert the
  no-config case on BOTH**: `flows/train_models.py` AND the four real onboarding sites (§ 15), which
  stay at `{}` by decision. ⚠️ *Asserting only the training flow is how onboarding silently diverges
  while the suite stays green.*
⛔ *An earlier version also asserted "the config used is readable back from the artifact record".
**Removed — it was UNSATISFIABLE at this phase**: T4's side table does not exist until phase 3. That
is the same defect class round 1 caught in this task's RED test, reintroduced by the § 14 fold.
T4 verifies readback.*

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
- ⚠️ **A path that does NOT auto-promote.** `flows/train_models.py:207` calls
  `store_and_promote_artifact()`; the retrain path must store WITHOUT promoting.
- 🔴 **One real run on staging**, since § 10/§ 11 show this path has never worked here.
- 🔴 **The config SUPPLIED, the config the model RECEIVED, and the config RECORDED against the
  artifact are all the same** — asserted at the flow level on the real run. ⛔ *Three separate values
  today; checking only that "a config was recorded" would pass while recording something else.*
- 🔴 **T3 must actually CALL T4's recorder.** ⛔ *T4 ships a helper and a migration; nothing else in
  the plan forces the retrain path to pass the parent through — so the plan could end with a lineage
  recorder nobody calls, which is § 3's defect all over again.*
- ⚠️ **Run preconditions, RECORDED IN THIS TASK BEFORE THE RUN** (⛔ *an earlier version demanded
  them without supplying any, which is a TODO not a deliverable*):
  - **the base artifact id** — the single ACTIVE `cmal_small` artifact; ⚠️ *a measurement, so T3
    records the id it used rather than the plan naming it now*;
  - **expected runtime and hardware** — the aquacast worker image on the staging host, the only
    place this model runs;
  - **the abort criterion** — ⛔ *an earlier version said "anything raised INSIDE the model after
    inputs are accepted is environmental and is retried once". **That is wrong**: a model raises
    DETERMINISTICALLY for a missing fine-tuning config and for a feature-manifest mismatch (the very
    refusal fi-issue 004 § 1 exists about). Retrying those wastes a run and, worse, labels OUR
    configuration bug as flaky infrastructure.*
    | failure | action |
    |---|---|
    | the § 11 resolver error, or a missing donor (§ 8) | **STOP** — T1/T3 are wrong |
    | a deterministic model refusal (no fine-tuning config, feature-manifest mismatch) | **STOP and report** — ours to fix |
    | an identified transient failure | retry **once**, then escalate |
    | anything else | **STOP and preserve it for diagnosis** — ⛔ *do not retry an exception you cannot classify* |
- ⛔ **The staging run is ORCHESTRATOR-GATED.** *`CLAUDE.md`: staging deploys are the orchestrator's;
  the owner keeps production. This task does not self-authorise the run.*

**Out.** ⛔ Promoting the result, assigning it, or letting it serve a forecast — that is a separate,
owner-gated act. ⛔ Judging whether it is any good (skill comparison, outside this plan).

**Pre-change.** 🔴 **A RED test at the FLOW level, through `discover_models()` / `adapt_if_fi`** —
⛔ *an earlier version said "T1/T2's tests cover the mechanism". They do not: they exercise the
adapter directly and so never hit § 11's missing resolver, which is exactly the failure that has kept
group training from ever running.* The test must fail with the resolver error today.

**Verification.**
- An artifact is produced, stored, and deserializes back to a working model.
- 🔴 **A run that ASKS FOR A RETRAIN but names no base artifact is refused, and does NOT fall
  through to `train`** — asserted (D1).
  ⛔ *An earlier version of this bullet said "a run that names no base artifact does NOT silently
  train from scratch". **That was wrong and dangerous**: an ORDINARY training run names no base
  artifact and MUST train from scratch — that is the existing flow every Swiss statistical model uses
  (§ 5). As worded it could be satisfied by breaking ordinary training, and it contradicted T1's
  "every existing model still trains unchanged" and T2's "no config supplied → behaviour unchanged".*
- 🔴 **It is NOT promoted and NOT assigned** — asserted, not assumed, and note that the existing
  store step promotes by default (`flows/train_models.py:207`). ⭐ *`cmal_small`'s current artifact
  keeps serving until a human decides otherwise.*
- ⚠️ **The PAST-leg forcing it trained on is the same reanalysis binding the operational path reads**
  (§ 6) — recorded from the run, because a plausible failure is quietly assembling something else.
  ⛔ *Do NOT claim full train/serve forcing parity: § 6 measured that the future leg differs
  (reanalysis in training, NWP forecast in serving) and this plan does not change that.*

### T4 — Record which artifact a retrained one came from (§ 9)

**Outcome.** "What was this fine-tuned from?" is answerable from our own records.

**In.** 🔑 **ONE side table, ONE migration — ALL of this plan's provenance (§ 14).**
`model_artifacts` gains no column. Per **D1 (closed)**, the record carries the parent artifact id,
the path to the base **config**, and the path to the base **params** — ⭐ **plus the config used for
THIS run**, which moved here from T2 so one task owns the whole record (§ 14; D3 unchanged) — each stored **with the hash
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
- **Parent id recorded on EVERY retrain; base config path recorded; base params path recorded ONLY
  when the base has one** — and **all three absent on a fresh train**, asserted both ways.
  ⛔ *An earlier version said "all three recorded on retrain", which an implementer would write as
  "all three non-null". ⚠️ Whether that holds for `cmal_small` is a T4 MEASUREMENT — its donor params
  are UNKNOWN, not known-absent (§ 5a). Do not encode either answer as an assumption.*
- The parent is resolvable to a real artifact row.
- 🔴 **The config used for the run is recorded and readable back** — the half of D3's condition that
  moved here from T2 (§ 14). ⚠️ *T3 then asserts supplied = received = recorded.*
- 🔴 **A base whose params are UNKNOWN records a NULL params path WITH a reason** — asserted at the
  RECORD level here. ⛔ *An earlier version said "still **retrains**", which is unsatisfiable in this
  phase: there is no retrain caller until T3. The end-to-end half belongs to T3.* ⚠️ *Whether
  `cmal_small` is that case is a measurement, not an assumption.*
- 🔴 **The donor's config is identified from ITS OWN recorded provenance and compared with the
  donor's import-time hash** (§ 13) — ⛔ *"the hash matches the file it names" is NOT sufficient: it
  passes while recording today's template.*
- 🔴 **A CHANGED-TEMPLATE case, with ONE required outcome**: the vendored config differs from the
  donor's recorded hash ⇒ **REFUSED**, naming both hashes. ⚠️ **T4 owns the comparison helper and
  tests it directly; T3 owns the end-to-end refusal**, since the retrain caller does not exist until
  then — ⛔ *an earlier version put the end-to-end assertion here, unsatisfiable in this phase and the
  third instance of that defect class on this plan.* ⛔ *An earlier version also
  offered "rejected, OR the donor's configuration preserved" — an either/or that two different
  implementations both satisfy.* ⭐ *Refusing is consistent with D2: we would rather stop than
  fine-tune from something we cannot identify.* ⛔ *Matching today's file alone must not pass.*
- ⚖️ **Parent retention, DECIDED — and it must be written into T4's migration as FK semantics.**
  ⛔ *An earlier version said "state the intended behaviour rather than discovering it", which is an
  instruction to decide, not a decision.*
  | event | behaviour |
  |---|---|
  | **deleting** a referenced parent | **REFUSED** — `RESTRICT`, not `CASCADE` and not `SET NULL`. ⛔ *`CASCADE` would delete the child's provenance row, which passes a naive "no orphan remains" test while destroying the answer to "what was this fine-tuned from?"* |
  | **superseding** a parent | **the lineage survives untouched** — supersession marks (`services/training.py:127`), it does not delete (Plan 328) |
  ⇒ **Assert the child's record still exists AND still resolves after the parent is superseded.**

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
     "note": "the config channel first — D3 (closed: a run parameter) gates everything downstream; nothing can select a strategy without the channel. RECORDING belongs to T4 (see 14), not here"},
    {"phase": 2, "tasks": ["T1"], "parallel": false, "decision": "D2 CLOSED",
     "note": "T1 is NOT technically blocked by T2 — the services and the FI boundary already accept a config argument, so the passthrough is independently testable. T2 first is a sequencing PREFERENCE (D3 gates the real run), not a dependency"},
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
    must be NULLABLE**. ⚖️ *SUPERSEDED in part — the REASON given here ("no model has ever received
    params, so `cmal_small` has none to point at") is the invalid inference § 5a retracts: it was
    IMPORTED, not trained here, so our empty run params say nothing about its external training.
    Nullable stands; "known to have none" does not.*
  - ⛔ **D3 remained open at that point and still gated everything**, declared as a machine-readable
    gate on phase 1 rather than only noted in prose. ⚖️ *SUPERSEDED 2026-09-26 — D3 is now closed.*
- **2026-09-26** — **independent review: NEEDS CHANGES. Every finding verified against the code
  before folding.** ⭐ *One is a genuine blocker the draft had no idea about.*
  - 🔴 **GROUP TRAINING IS BROKEN TODAY, and that explains § 10.** The training flow wraps discovered
    models with `adapt_if_fi` and attaches **no station-code resolver**; the adapter raises for every
    GROUP train/predict without one. ⇒ Group training has never produced an artifact here because it
    **cannot**, not merely because nobody ran it. New § 11; T3 now owns the wiring, and its RED test
    moved to the FLOW level — ⛔ *the draft's "T1/T2's tests cover the mechanism" was false: those
    exercise the adapter directly and never reach the missing resolver.*
  - 🔴 **I inferred a fact I had not measured.** ⚖️ *(Round 3 found this correction had NOT reached
    D1's table or T4's verification — see the 2026-09-26 round-3 entry.)* From "run params are an
    empty dict" I concluded "`cmal_small` has no training params, so the first retrain records none".
    **Invalid** — it was
    IMPORTED, not trained here, so our empty params say nothing about its external training. New
    § 5a separates three kinds of config (vendored template / run params / the donor's own), and T4
    must now INSPECT the donor rather than assume NULL.
  - 🔴 **The forcing-parity justification was overstated.** Measured: training's future leg is
    reanalysis, serving's is NWP forecast records. ⇒ Fine-tuning fixes the **climatology** mismatch
    (ERA5-Land vs RhiresD/TabsD); it does **not** teach the model ICON's forecast-error behaviour.
    ⚠️ *Equally true of the original Caravan training, so nothing is made worse — but the plan must
    not be read as closing a gap it does not close.*
  - **§ 8 stated the wrong store contract** — `fetch_artifact` returns `(ArtifactId, bytes) | None`,
    not `(sha256, bytes)`; and the existing call site is not a model to copy (it discards the id and
    deserializes the in-memory bytes). T3 now deserializes the FETCHED bytes and rejects a missing
    donor.
  - **The existing store step auto-promotes** (`store_and_promote_artifact`), so T3's
    already-stated "do not promote" needs a path that does not, and says so.
  - **T2's RED test named `train`/`retrain`** — unsatisfiable, since `retrain` does not exist until
    the next phase. Split.
  - **fi-issue 004 had two claims this plan contradicts**, both corrected there: that SAP3 cannot
    *record* which strategy produced an artifact (it can — T2 does), and an unscoped parent-identity
    ask (scoped now to donors SAP3 did not select). A third section added: the D2 refusal diverges
    from FI's own fall-back comment, so the issue asks upstream to confirm the choice is the
    caller's — ⚠️ *documenting it locally does not reconcile it.*
  - **Phase note added:** T1 is not technically blocked by T2; T2-first is a preference, not a
    dependency.
- **2026-09-26 — SECOND independent review: NEEDS CHANGES, four majors, all different from the
  first.** Every finding verified before folding.
  - 🔴 **A bare structural `isinstance` would have silently defeated D2** — the owner's refusal
    decision. The adapter has no `__getattr__`, so an unconditional `retrain` on it makes the check
    pass for **every** FI model including non-retrainable ones, and the refusal never fires for the
    one case it exists for. ⭐ *The repo already warns about exactly this trap in-code, where
    `config_hash` needs an explicit proxy.* New § 12; T1 now interrogates the INNER model through a
    proxy, and a new verification bullet covers the WRAPPED case — the one a naive test would pass.
  - 🔴 **I introduced a wrong, dangerous bullet in the PREVIOUS fold.** I wrote *"a run that names no
    base artifact does NOT silently train from scratch"*. An ordinary training run names no base
    artifact and **must** train from scratch — so as worded it could be satisfied by breaking every
    Swiss statistical model, and it contradicted T1's and T2's own "unchanged" bullets. Rewritten to
    scope it to a run that ASKS for a retrain. ⛔ *A fold that adds a defect is worse than the gap it
    closed.*
  - 🔴 **The hardcoded empty-config mapping is at SEVEN sites, not one** — `onboard_model.py` ×2 and
    `model_onboarding.py` ×4 as well. So "every current model trains through THIS path" was also
    wrong: a newly onboarded model trains through the onboarding path. T2 must either cover them or
    say why not.
  - 🔴 **T2 required provenance storage it never declared**, leaving two migrations in the same area
    with no owner, and the plan was internally split on column-vs-side-table (§ 9 implies a column;
    T4 cites a side-table precedent, and the repo's own metadata comment argues for that). Now one
    decision, one owner per migration.
  - **T4's first verification bullet contradicted its third** for the only base that exists: "all
    three recorded" would be written as "all three non-null" and fail on `cmal_small`.
  - **T4 would have shipped a recorder nobody calls** — T3 now must demonstrably pass the parent
    through, else the plan reproduces § 3's defect. Run preconditions added too.
  - Citation drifts fixed (adapter `train` at `:1062`, `assemble_group_training_data` at `:620`), and
    fi-issue 004 gained the D2-divergence section, asking for the contract COMMENT to be amended and
    noting the divergence is documented only inside SAP3 until filed.
- **2026-09-26** — ⚖️ **D3 CLOSED on (a): the config is supplied when the run is triggered**, with
  the condition that **the exact config used is recorded against the artifact** — otherwise § 7's
  "opaque for v1" becomes "unknowable forever", the failure already on record for this model. T2 owns
  both halves. ⚠️ *This widens slightly beyond fine-tuning by design: it is the same path all
  training uses, so T2 must PROVE the no-config case is unchanged.*
  🔴 **`open_decisions` is now empty — and the INDEX had never been updated when D1/D2 closed either.**
  ⛔ *Four stale sites found by sweeping for the value rather than re-reading where I had just edited:
  the phase-1 note, a changelog line asserting D3 open in the present tense, and the index entry's
  machine field plus its prose. The index is the corpus entry point and `open_decisions` reads as a
  gate — exactly the failure recorded against plan 329 two days ago, repeated.*
- **2026-09-26 — THIRD review round: NEEDS CHANGES, three majors. Every finding verified.**
  ⛔ *Two of the three are faults in the PREVIOUS fold, not gaps in the original draft.*
  - 🔴 **The donor-params correction reached § 5a and nowhere else.** D1's table still asserted the
    params were *"genuinely absent … the first retrain records none"*, and T4's verification still
    said requiring non-null *"FAILS on `cmal_small`"*. ⛔ **Third round running, third instance of
    the same failure mode: correct one site, leave the others.** Both fixed, and the earlier
    changelog entry now cross-references this one so it cannot be read as still current.
  - 🔑 **A genuine improvement I had missed: the donor's config identity is ALREADY RECORDED.** The
    import path captures the donor's `config_hash` and provenance reads it back — so we can identify
    the donor's actual configuration. ⛔ *My "derivable from the model" would instead have hashed
    TODAY's installed template, which satisfies a naive "hash matches the file" check while
    misidentifying the donor if the template has changed since import.* New § 13; T4 now resolves the
    donor's recorded provenance and must handle a changed-template case.
  - 🔴 **The previous fold removed a check and promised a replacement that did not exist.** T2's RED
    test was narrowed to `train` with the note *"T1 covers retrain's own config arrival"* — and T1
    did not. ⇒ Nothing verified that a caller's config reaches the model on the retrain path. T1 now
    asserts it at BOTH boundaries, and T3 asserts that the config SUPPLIED, RECEIVED and RECORDED are
    the same value — ⚠️ *three distinct things today; "a config was recorded" would pass while
    recording something else.*
  - **§ 12's explanation was imprecise about WHICH protocol.** Against FI's own `RetrainableModel` the
    adapter fails the structural check regardless, because that protocol also requires
    `input_requirement`, which the adapter does not expose. The false-positive danger is real for a
    SAP3-side protocol declaring only `retrain` — which is what T1 would naturally add. The
    conclusion is unchanged; T1 must now name the protocol it checks against.
  - ⭐ **Confirmed unchanged by this round:** the seven config sites, the missing resolver, the
    adapter's absent `__getattr__` and its quoted warning, the narrowed forcing claim, both corrected
    citations, the index entry, and the T2→T1→T4→T3 ordering.
- **2026-09-26 — FOURTH review: NEEDS CHANGES, three majors + a process finding. All verified.**
  ⛔ **Process finding first, because it is mine:** *I folded the other reviewer's findings into this
  file WHILE this reviewer was reading it — 501 lines at its start, 534 eight minutes later, and
  uncommitted. Two findings it had drafted were fixed under it mid-review.* ⇒ 🔴 **No review of a
  moving file certifies anything.** *One committed state, then one review of THAT state. Reviews are
  not launched while an edit is in flight.*
  - 🔴 **T2 parked two decisions as instructions to itself** — *"Decide column-vs-side-table
    explicitly"* and *"either bring them in or state explicitly"*. ⛔ **Those are round-3 findings
    pasted in as imperatives; a plan that tells itself to decide has not decided.** Both are now
    DECIDED, in the measured section, once:
    § 14 — **a side table, one migration, owned by T4**, on the repo's own explicit precedent
    (*"A join table (not a singular FK on `model_artifacts`) … `model_artifacts` itself gains no new
    column"*). ⚠️ *This moves which TASK records the config; D3 itself is unchanged.*
    § 15 — **T2 covers ONE of the seven `{}` sites**, with the other six ruled out by reason: two are
    smoke tests on synthetic data (no caller exists), four are import-time onboarding, which no closed
    decision requires to accept config — **and T2 must assert those four are unchanged.**
  - 🔴 **T2's verification still carried the rationale § 5 retracts** — *"every current model trains
    through this path today"*. **Third stale site for that one claim**, and it has teeth: an
    implementer reading it asserts on the training flow only, leaving the onboarding path — the path a
    newly onboarded model actually trains through — unasserted while the suite stays green.
  - 🔴 **The plan had NO documentation deliverable**, in a repo whose rules forbid that. The
    authoritative Protocol spec (`docs/spec/types-and-protocols.md:2096-2137`) defines both model
    Protocols and T1 adds a capability to them; two touchpoint maps covering exactly this subsystem
    were never consulted. All four now named in T1. ⭐ *Same omission as plan 329's second round —
    and this time I did not catch it either.*
  - **T3's run preconditions were demanded, not supplied.** Now recorded: the base artifact id is a
    measurement T3 reports, the abort criterion distinguishes a resolver/missing-donor failure (our
    bug) from one raised inside the model (environmental, retried once). ⛔ **And the staging run is
    orchestrator-gated** — the plan does not self-authorise it.
  - ⭐ **Confirmed correct by this round:** § 12's adapter trap including the new protocol-precision
    paragraph, all seven config sites, § 11's resolver gap, § 5a/§ 6's consistency, both citations,
    the index entry, fi-issue 004, and that **this round's predecessor introduced no new defect** —
    the round-1 bullet that could have been satisfied by breaking ordinary training is properly scoped.
- **2026-09-26 — FIFTH review round, TWO reviewers on ONE clean committed state (`b8465e6c`).**
  ⭐ **Both independently confirmed the file did not move under them** (one checked its md5 at start and
  end) — so unlike round 4, this round can gate. **Verdict: NEEDS CHANGES.** Both found the same major.
  - 🔴 **§ 14's own refinement reached two sections and left SIX others stale.** *"T2 ships the channel,
    T4 ships the record"* landed in § 14 and T4, while T2's Outcome, T2's In, T2's verification, D3's
    text, the **JSON phase graph** and the **index entry** all still said T2 records it. ⛔ **Fifth
    round, fifth instance of the same failure mode** — and the two surfaces that went stale are the two
    the round-3 entry already named as repeat offenders. Fixed by sweeping for the VALUE across the
    plan *and* the index, not by re-reading what I had just edited.
  - 🔴 **One of those stale sites was UNSATISFIABLE, not merely wrong.** T2's verification asked that
    the config be "readable back from the artifact record" — in phase 1, against a side table that does
    not exist until phase 3. ⛔ *That is exactly the defect class round 1 caught in this same task's RED
    test, reintroduced by my own § 14 fold.*
  - 🔴 **NEW major: the donor's config can only be resolved for an IMPORTED donor.** Provenance is
    written on the import path ONLY — the module says so outright — and the fetch returns `None`
    otherwise. ⇒ A donor SAP3 trained itself (a retrain of a retrain, which D1 permits) has no recorded
    config hash, and my rule assumed one always exists. ⛔ *The natural fallback — hash today's
    installed template — is § 13's own trap.* **Now decided in a table**: imported ⇒ its provenance
    row; SAP3-produced after T4 ⇒ T4's own record; SAP3-produced before T4 ⇒ **NULL with the reason**,
    the same discipline § 5a applies to the params path. Never today's file.
  - 🔴 **My staging abort rule misclassified deterministic refusals as environmental.** It said anything
    raised inside the model is retried once — but a missing fine-tuning config and a feature-manifest
    mismatch are deterministic, and retrying them labels OUR bug as flaky infrastructure. Replaced with
    a four-row table, including *"anything you cannot classify: STOP and preserve it"*.
  - **Two more parked decisions, now made**: the changed-template case had an either/or (two
    implementations could both pass) ⇒ **REFUSE, naming both hashes**; and parent retention ⇒
    **`RESTRICT`, not `CASCADE`** — ⛔ *cascade would delete the child's provenance row, passing a naive
    "no orphan remains" test while destroying the very answer T4 exists to give.* Supersession
    preserves the lineage, since it marks rather than deletes.
  - **Smaller:** T1 asked for a fi-issue paragraph that was already written (now marked done); the
    retrain entry point is now NAMED (a parameter on the existing training flow, threaded to a task
    that is called directly, not mapped) rather than left to the implementer; the 2026-09-25 changelog
    entry's retracted params reasoning is cross-referenced; "IMPORT time" corrected to the ONBOARDING
    path (the import path never trains); two off-by-one citations fixed.
  - ⭐ **Confirmed correct and not to be re-litigated:** § 14's precedent quoted verbatim, all seven
    `{}` sites exact, § 15's dispositions true to the code, T1's four documentation targets exact,
    T4's internal consistency, T2's two-surface no-config assertion, and that **round 4's fold
    introduced no new defect** — the bullet that could once have been satisfied by breaking ordinary
    training is properly scoped.
- **2026-09-26 — POST-IMPLEMENTATION check, two reviewers, both verdicts negative:
  DIFF INCOMPLETE and PLAN NEEDS CHANGES.** ⛔ *The most serious finding is about my own
  verification, not the code.*
  - 🔴 **I reported mutation testing as assurance, having mutated the WRONG LAYER.** I told the owner
    "each rule's test fails when that rule is broken". The mutations ran against
    `services/training.py` — which my tests DO call and which was already correct on `main`. A
    reviewer measured what I had not: the T2 tests pass verbatim against unmodified `main`, and
    `grep training_params tests/` returned **zero hits**. Mutating the real change (making the flow
    ignore the caller's config) left **all six green**. ⇒ [[feedback_mutate_the_line_you_changed]].
  - 🔴 **Three tests verified their own fakes.** A "flow-level" test that never called the flow; a
    wrapped-adapter test that re-implemented the capability check in its own body. All three deleted
    and replaced with tests that drive the real flow.
  - 🔴 **T3 was one bullet of seven.** No donor input, no fetch, no non-promoting store path, and
    **nothing called T4's recorder** — the precise defect this plan named against itself. Now
    implemented, with the red measured for the RIGHT reason (the first red was "missing parameter",
    which the plan explicitly rules out, so the parameter was added alone and red re-measured).
  - 🔴 **A doc I wrote reintroduced an error this plan retracts** — "onboarding trains at import
    time". The import path never trains. Corrected, and the distinction spelled out because § 5a's
    argument depends on it.
  - **Two of four documentation targets were untouched**; both now written, plus the orchestration
    standard's flow-parameter conventions.
  - 🔴 **A plan requirement was NOT SATISFIABLE**: D1 asked for the donor's config PATH resolved from
    its provenance, and provenance stores a **hash and no path**. I had silently resolved that in code
    by pairing today's installed path with the donor's hash — the mixed-provenance record § 13 warns
    against — and the default argument made that branch produce a record the type rejects. The plan
    now says hash-authoritative, path-conditional; the code records a reason when it cannot pin one.
  - **T4 carried two more unsatisfiable-in-phase bullets** (they need T3's retrain caller). Split:
    T4 tests the helper directly, T3 owns the end-to-end. ⚠️ *Third instance of that defect class here.*
  - **"Nine independent reviews" was unsupported** — the changelog records six passes. Corrected.
  - ⭐ **Confirmed sound:** the D2 capability check really does read the inner model; §§ 13/14/15 are
    internally consistent and true of the code; no parked "decide X" instructions remain; the index
    entry did not regress; and scope is clean — no `train` signature change, no mandatory retrain, no
    FI package edit.
