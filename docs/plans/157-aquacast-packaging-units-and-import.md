---
status: READY
created: 2026-08-12
plan: 157
title: External-artifact import path + provenance (the in-repo half of aquacast packaging)
scope: Make an externally-trained model artifact importable into SAPPHIRE Flow with representable provenance, without entering the training path. RESCOPED 2026-08-13 after implementation: the shim distribution (G3/G9/G15) and the worker image (G10) are SPLIT OUT — they are an external-repo deliverable that this repo cannot build or test, and putting them in an /implement-driven plan produced two green-but-hollow tests. What remains is T3: the import service and flow, the model_artifact_provenance table and store, AuditedWriter atomicity, the scoped-role write GRANT, and the tenant/trained_at invariants.
depends_on: []
blocks: [152]
supersedes: []
---

# Plan 157 — external-artifact import path + provenance

## Status
**DRAFT.** Split out of **Plan 152** (tasks T2 and T3) on 2026-08-12.

**Read Plan 152 first** for shared context: the selected artifact (`cmal_pool_PT`), its verified
contract, and the owner decisions — especially **D1** (config ships as shim package data) and **D10**
(a dedicated aquacast worker image). This plan does not duplicate that material.

**Why it is separate:** every task here is **testable against synthetic single-resolution FI models**,
so it blocks on nothing — not the Swiss data (Plan 155), not the modeller's 5-day variant, not the
artifacts. It is the work that can start today.

**Note to reviewers — scope is bounded.** State INVARIANTS, not mechanisms: transaction protocols,
schema column lists, unique-index definitions, CLI flag design and wire contracts belong to
implementation and its review, and putting them in a plan doc regenerates findings every round (it
did — a review round expanded this doc 4.8x and then faulted its own additions). **Do not add tasks
or phases.** In scope: contradictions, unowned work, inaccurate citations, and red-first tests that
cannot fail for the stated reason.

**Note to reviewers — this plan is one of a FAMILY (152 / 155 / 156 / 157).** Shared context (the
selected artifact and its verified contract, owner decisions D1–D13, and what the Swiss run can and
cannot prove) lives in **Plan 152 only, by design** — the siblings reference it rather than
duplicating it, because duplication is what produced the drift Plan 152 spent three review rounds
correcting. **"This plan does not explain X" is NOT a finding if X is in 152.** Do flag it if a
statement here CONTRADICTS 152, or if this plan depends on something no plan in the family owns.

## ⚠️ RESCOPED after implementation (2026-08-13) — T1/T2 split out

`/implement` escalated with `redFirstMissed: true`, and its own report named the cause: **T1 and T2
were "NOT locked / not testable in this repo".**

**That was a PLAN defect, not an implementation one.** T1 specified an external distribution
(`sapphire-aquacast`, with torch and the trained weights) that by design lives *outside* this repo —
and then gave it acceptance criteria requiring it to exist and be installed. `/implement` runs
against **this** repo, so it could not satisfy them. What it produced instead:

- a shim test that **monkeypatched `importlib.metadata.entry_points`** with a fabricated class, green
  whether or not the real package exists or works;
- a "cold-start `discover_models()` in the deployed worker" test that ran on the **host interpreter**
  and asserted only native models — unable to detect the single failure G10 exists to catch, an entry
  point that resolves in a full dev environment but is invisible in the minimal production image.

**Both tests are deleted.** A test that cannot fail is worse than no test: it makes a green suite
look like coverage.

**The T2 deployment work is also reverted** — compose services, pool routing, `cicd.md`, and the
compose tests. It created `prefect-worker-forecast-cycle` using **`build: *app-build` and the same
`sapphire-flow:${VERSION}` image**, so a pool split with an identical image on both sides bought
nothing until the shim exists, while adding a third pool, a mixed-version upgrade window and
contradictions with `orchestration.md`. Both deployments are back on `default`. *(The reason is
recorded at the `import-model-artifact` spec and in its test, so nobody rebuilds it and rediscovers
the dead end.)*

**What survives is T3, and it is complete:** the `0048` migration, `model_artifact_provenance` table
+ store, the import service and flow, `AuditedWriter` atomicity proven against **real Postgres**
(with an AUTOCOMMIT characterization showing the same failure without the writer leaves an orphan),
`train()` provably never called on either path, the tenant derived from the target rather than a
caller argument, `trained_at` kept distinct from `imported_at`, and the **INSERT-only write GRANT
tested under the real scoped worker role** — the check that catches "passes as superuser, fails in
production".

**Successor plan:** the shim + worker image. It is an **external-repo deliverable that `/implement`
cannot drive from here**, and it is blocked on the real `sapphire-aquacast` package existing — itself
gated on Plan 152/155 and the modeller's 5-day-horizon variant.

## Re-grounded against post-Plan-156 `main` (2026-08-13)

Plan 156 merged (#145, `aaafa12`) and **substantially rewrote `adapters/forecast_interface.py`** —
the file this plan's G9 work centres on. Re-verified against current `main`:

- **All 9 drifted citations corrected** (the `_FI_UNIT_TO_CANONICAL` dict, `fi_unit_to_canonical`,
  the `_ensemble_from_variable_output` call site, adapter construction, `_static_inputs`,
  `discover_models`' `cls()`, the classification assert, the FI compatibility path, and the
  non-nullable training columns).
- **G9 HOLDS, verified by execution not citation:** `fi_unit_to_canonical(Unit.MM_PER_DAY)` still
  raises `ConfigurationError`; both locked tests survive. And `M3_PER_S` → `m³/s`, `MM` → `mm` both
  map — which is exactly why T1's red assertion must be **numeric**, not mappability.

**NEW interaction introduced by Plan 156 — an unsupported shim model is now SILENTLY SKIPPED.**
`discover_models` gained an `except UnsupportedModelRequirementError` clause
(`services/model_registry.py:96`), so a model whose `InputRequirement` SAP3 cannot represent is
dropped from the registry **per entry point** instead of blacking out discovery. That is the
behaviour Plan 156 wanted, but it changes the failure mode for **this** plan:

- **`cmal_pool_PT` is safe** — verified to declare exactly one `time_step` branch.
- **A future multi-resolution aquacast artifact shipped through the shim would not fail loudly at
  onboarding — it would simply be ABSENT from `discover_models()`**, surfacing downstream as
  `MODEL_NOT_FOUND` rather than "this requirement is unsupported". T1 must therefore assert that the
  shim's model **is discoverable** (a positive assertion), not merely that it constructs; and Plan
  152's deferred multi-resolution artifact inherits this when Plan 153 is picked up.

## The four problems

### G3 — the entry-point registry cannot construct an aquacast model
`discover_models` constructs each entry point **with no arguments** — `raw_instance = cls()`
(`services/model_registry.py:87`; group `"sapphire_flow.models"` at `:23`). aquacast needs
`AquacastModel(ModelTemplate.from_yaml(...), device=...)`. And the adapter computes
`data_requirements` from `input_requirement` **at construction**
(`adapters/forecast_interface.py:453`), which derives from that config — so **the config must bind at
import time**, i.e. **one entry point per trained config** (D1).

### G9 — **`mm/day` has no SAP3 canonical unit** (the blocker)
PT emits `discharge` in **`mm/day`** and consumes discharge/precipitation in `mm/day`. But
`_FI_UNIT_TO_CANONICAL` (`adapters/forecast_interface.py:123-134`) **deliberately omits
`MM_PER_DAY`** — its own comment says so — and `fi_unit_to_canonical` (`:157-164`) raises
`ConfigurationError` for it. Two locked tests enforce the omission
(`tests/unit/types/test_forcing_schema.py:103`, `tests/unit/adapters/test_fi_unit_mapping.py:31`).

`_ensemble_from_variable_output` calls it at `:199` **before** building the ensemble, so **every
predict would raise**; compatibility validation rejects the unit too
(`services/model_onboarding.py:211-320`, the `_fi_compatibility_checks` path), so the model would not even onboard.

**Resolution belongs HERE, in the shim — not in SAP3's unit map.** Our canonical discharge is `m³/s`
and `mm/day ↔ m³/s` is **area-dependent**, so a bare map entry would be numerically wrong. The FI
contract explicitly allows declaring the unit your pipeline actually has.

### G10 — no operational deployment path for an external shim
`discover_models()` sees only **installed entry points**. The runtime image runs
`uv sync --frozen --no-dev` against **this repo's** lockfile (`Dockerfile:32`) and copies only that
virtualenv (`:82`). An external distribution is therefore invisible in production.
**D10 resolved this: a dedicated aquacast worker image.** But see § Open items — the decomposition is
not as clean as D10 assumed.

### G4/G7 — no external-artifact import path, and provenance is not representable
Flow 13 runs scope → register → validate → smoke → assemble → **train** → store → promote → assign
(`flows/onboard_model.py`). No path registers an **externally-trained** artifact, and we will not
retrain a pooled model on the mac-mini.

Worse, there is nowhere to record what such an artifact *is*: `model_artifacts` has no provenance
column (`db/metadata.py:907-951`), `ModelArtifactRecord` has no provenance field
(`types/model.py:292-307`), and `ModelArtifactStore.store_artifact` **requires**
`training_period_start`/`end`/`trained_at` (`protocols/stores.py:416-427`), all `nullable=False`
(`db/metadata.py:935-937`), with no meaning for an artifact we did not train. And the
obvious-looking module means something **else**:
`store/model_artifact_lineage.py::record_artifact_basin_lineage` (`:33-103`) writes one row per
**basin the artifact trained on** (Plan 120 retrain SLA, docstring `:40-58`) — calling it with *our
target* stations would durably assert a falsehood.

## Tasks

### T1 — `sapphire-aquacast` shim distribution (closes G3, G9)
A thin package **outside this repo** (Plan 135 decision 3 — no torch in our `pyproject.toml`)
exposing **one zero-argument entry-point class per trained config**:

```toml
[project.entry-points."sapphire_flow.models"]
aquacast_cmal_pool_pt = "sapphire_aquacast.models:AquacastCmalPoolPT"
```

Each class binds its `ModelTemplate.from_yaml(...)` + device at construction and declares the
`model_tier` / `alert_eligibility` attributes `_assert_model_classification_declared` requires
(`services/model_registry.py:61-76`). `adapt_if_fi` wraps it at discovery (`:76-84`). The config
ships as **package data** (D1).

**The shim owns the NAME boundary too (G15 — review finding, 2026-08-12).** aquacast declares
**`mean_temperature`**; SAP3's canonical forcing names are `{"precipitation", "temperature"}`
(`config/deployment.py:132`) and the MeteoSwiss reanalysis adapter emits `temperature`. Under the
aquacast name, compatibility reports **both past and future forcing missing** and the operational
read looks for a series that does not exist. This fell between the sibling plans — it is the same
class as G9 but for names, and it belongs in the same place: **expose canonical `temperature`
outward, translate internally to `mean_temperature`.** Red-first: compatibility reports zero missing
forcing for a Swiss station (fails today), and an FI input built from a `temperature` series reaches
the model as `mean_temperature`.

**The shim owns the unit boundary (G9).** It must present SAP3-compatible units outward and convert
numerically in both directions:
- **discharge** — expose `M3_PER_S`, doing the **area-aware** `mm/day ↔ m³/s` conversion internally
  (`area` is already a required static). **Split the area contract by failure mode:** a *missing*
  declared static is rejected by SAP3 during compatibility/input assembly — `_static_inputs`
  (`adapters/forecast_interface.py:1139-1151`) raises `ConfigurationError` **before** the shim's
  `predict()` runs, so the shim cannot promise a `ModelFailure` there. What the shim *can* own is an
  **invalid supplied** area (zero, negative, non-finite), which must return `ModelFailure` per the FI
  contract rather than raising.
- **precipitation** — audit what PT declares; if `MM_PER_DAY`, expose canonical `MM` (daily
  accumulation) and translate consistently.

**Red-first — assert NUMBERS, not mappability.** `M3_PER_S` and `MM` are **already** in
`_FI_UNIT_TO_CANONICAL` (`adapters/forecast_interface.py:123-134`), so a test that merely proves
`fi_unit_to_canonical` succeeds on the shim's declared units **passes today and proves nothing**. The
genuinely red assertion is a **numeric** area-aware round trip: a known discharge in `mm/day` at a
known `area` arrives as the correct `m³/s` value — which is what catches
relabelling-without-conversion.

### T2 — the aquacast worker image (closes G10)
Build the dedicated image/environment carrying the shim and its torch runtime (D10), keeping ~2 GB of
PyTorch out of every existing worker. Update `docs/standards/cicd.md` with the topology.

**Acceptance: a cold-start `discover_models()` test in that deployed environment.** Without it,
neither this plan nor Plan 152 may claim "operational".

**Settle the decomposition first — see § Open items (D13).**

### T3 — External-artifact import path + provenance (closes G4, G7)
**(a) Make provenance representable.** Decide and implement how an artifact we did not train records
what it is — source repo, commit, config hash — given `store_artifact` demands
`training_period_start`/`end`/`trained_at` as non-nullable. Do **not** reuse
`record_artifact_basin_lineage`: it asserts *training* basins and would record a falsehood.

**(b) An executable import entry point**, not just a service function: validate the bytes deserialize
via the model's own `deserialize_artifact`, store, record provenance, and promote — **without
entering the training path**. Flow 13 has no artifact/provenance/import inputs today, so this needs a
real flow or CLI boundary plus deployment registration.

**Invariants (implementation owns the mechanisms) — all verified against `main`:**
- **All-or-nothing.** A failure leaves no artifact row of any status, no changed prior ACTIVE row, no
  provenance row, and no orphaned file. *(Note the current store generates the id and writes the file
  before inserting the row (`store/model_artifact_store.py:44-80`), so a naive caller cannot know
  which file to clean up — a recoverable write protocol must be chosen at implementation time.)*
- **A new table needs an explicit write GRANT, or the import fails only in production.**
  `docker/bootstrap-roles.sql:114-120` grants broad SELECT but says in its own comment that "a NEW
  table's write grants still need an explicit line below". Any provenance table therefore needs
  `GRANT INSERT … TO sapphire_worker` plus the service-user matrix in `docs/conventions.md`, and the
  import must be **tested under the real scoped worker role** — a superuser test would pass while
  production fails.
- **`trained_at` ≠ import time.** `store_and_promote_artifact` takes a single `clock()` value
  (`services/training.py:185`) and uses it both as `trained_at` and as the audit/authorization `now`
  (`:213`). For an externally-trained artifact these are **different instants**: passing the real
  training-completion time would backdate the promotion audit event to the model's training date.
  Separate them.
- **Authorize against the tenant DERIVED from the target, never one supplied by the caller.**
  Resolve the station/group first, take its `tenant_id`, and require any operator-supplied value to
  match. Existing code already derives it this way (`services/run_group_forecast.py:408-412`);
  trusting an argument would let a caller authorize against a tenant it does not own.
- **A config/artifact mismatch must fail loudly, never silently predict.** D1 ships the config as
  shim package data while the artifact is the native `best.pt`, so the two can drift across a shim
  release. The stored-artifact format must make that detectable; the format itself is an
  implementation choice.

**Red-first:** an undeserializable blob fails loudly; a valid import yields a promotable artifact with
external provenance; **an acceptance test from the public boundary proving `train()` is never
called**.

## Phase dependency graph

```json
{
  "phases": [
    { "id": "T1", "tasks": ["T1"], "parallel": false },
    { "id": "T2", "tasks": ["T2"], "parallel": false, "depends_on": ["T1"] },
    { "id": "T3", "tasks": ["T3"], "parallel": false }
  ]
}
```

T3 is independent of the shim and can run in parallel. T2 needs something to install.

## Open items

- **D13 — the worker image does not decompose as cleanly as D10 assumed (owner decision needed).**
  Group forecasting is **not a separable service**: it is Phase B2 **inside** `run_forecast_cycle`
  (`flows/run_forecast_cycle.py:2316-2503`), in the same process as station forecasting, and its
  results are accumulated into structures Phase C alerting consumes **in-process** (`:2497-2542`).
  Prefect workers are `--type process` on a single image (`docker-compose.yml:80-83`, `:149-151`).
  So "a dedicated aquacast worker" means one of:
  **(A)** route the **whole forecast cycle** to the aquacast pool — cheap, but every operational
  forecast then runs on the torch-carrying image, which **voids D10's stated rationale**;
  **(B)** extract Phase B2 into its own deployment — which requires re-joining group and station
  ensembles across processes for combination and alerting: real, unscoped, expensive.
  **RESOLVED (owner, 2026-08-13): option (A).** The aquacast image **IS the forecast-cycle worker
  image** — a superset of the standard one, served by its own pool. `default`/`ingest` workers stay
  torch-free because they **no longer run the cycle**.

  **This voids D10's stated rationale, and that is accepted: the rationale was wrong, not the
  decision.** D10 justified a dedicated image as "keeping ~2 GB of PyTorch out of every existing
  worker". Since group forecasting is Phase B2 *inside* `run_forecast_cycle` and Phase C alerting
  consumes its results in-process, the cycle cannot be split without re-joining group and station
  ensembles across a process boundary. So torch lands on whichever worker runs the cycle, and the
  honest framing is *one heavier cycle worker*, not *an isolated aquacast worker*.

  **What T2 must therefore deliver:** the forecast-cycle worker image carrying the shim + torch, its
  own Prefect pool, the cycle deployment routed to that pool, `default`/`ingest` left unchanged and
  torch-free, and `docs/standards/cicd.md` updated with the topology. **Acceptance is unchanged in
  form but now unambiguous in meaning: a cold-start `discover_models()` test in the deployed
  forecast-cycle worker** — that is the environment the cycle actually runs in.

  *(Rejected: (B) extract Phase B2 into its own deployment — real, unscoped, and it buys isolation we
  do not need while the cycle is a single process.)*

## Non-goals
- Retraining or fine-tuning in SAP3 (Plan 152 D3 — import-only for v1, flagged for revisit if ICON
  re-training is taken up).
- The Swiss data work (Plan 155) and the integration itself (Plan 152).

## Coordination
**`adapters/forecast_interface.py`** is edited by Plan 151 (T2 accessors) and Plan 156 (the
multi-resolution guard). This plan should need **no** change to it — the unit work lives in the shim
by design. If a task proposes editing it, stop and coordinate.

**Actual outcome (T3 fixer round, 2026-08-13):** one small, additive change landed anyway — a
`config_hash` property on `ForecastInterfaceAdapter` (`adapters/forecast_interface.py:462-473`)
forwarding the wrapped FI model's own `config_hash` attribute, which the adapter otherwise silently
dropped (no `__getattr__` passthrough), disabling T3's config/artifact drift check for every real FI
model. This is outside the unit-conversion logic the coordination note above was worried about, adds
no new surface to the multi-resolution guard or T2's accessors, and is proven necessary by
`tests/unit/adapters/test_forecast_interface_adapter.py::test_config_hash_is_forwarded_from_the_wrapped_fi_model`
and its sibling (both fail without it). Checked against `main` at fixer time — no active conflict with
Plans 151/156's own edits to this file. Noted here per the coordination rule, after the fact rather
than before, since the need surfaced mid-fix rather than at planning time.

## References
- `docs/plans/152-aquacast-pooled-model-integration.md` — parent; artifact contract and decisions
  D1/D3/D10.
- `docs/plans/135-eqrn-offline-model-onboarding-benchmark.md` — decision 3 (separate package).
