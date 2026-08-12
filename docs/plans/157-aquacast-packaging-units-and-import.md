---
status: DRAFT
created: 2026-08-12
plan: 157
title: aquacast packaging — shim distribution, the mm/day unit boundary, worker image, and external-artifact import
scope: Make an externally-trained aquacast artifact installable, discoverable, unit-correct and importable. Four coupled problems that all live at the packaging seam — the entry-point registry cannot construct an aquacast model (it takes no arguments); `mm/day` has NO SAP3 canonical unit so every predict would raise at our output boundary; an external distribution is invisible to `discover_models()` in the production image; and there is no path to register an externally-trained artifact or represent its provenance. Split out of Plan 152 (its T2 and T3) because all of it is exercised against SYNTHETIC FI models and therefore blocks on nothing.
depends_on: []
blocks: [152]
supersedes: []
---

# Plan 157 — aquacast packaging, units and import

## Status
**DRAFT.** Split out of **Plan 152** (tasks T2 and T3) on 2026-08-12.

**Read Plan 152 first** for shared context: the selected artifact (`cmal_pool_PT`), its verified
contract, and the owner decisions — especially **D1** (config ships as shim package data) and **D10**
(a dedicated aquacast worker image). This plan does not duplicate that material.

**Why it is separate:** every task here is **testable against synthetic single-resolution FI models**,
so it blocks on nothing — not the Swiss data (Plan 155), not the modeller's 5-day variant, not the
artifacts. It is the work that can start today.

## The four problems

### G3 — the entry-point registry cannot construct an aquacast model
`discover_models` constructs each entry point **with no arguments** — `raw_instance = cls()`
(`services/model_registry.py:78-82`; group `"sapphire_flow.models"` at `:23`). aquacast needs
`AquacastModel(ModelTemplate.from_yaml(...), device=...)`. And the adapter computes
`data_requirements` from `input_requirement` **at construction**
(`adapters/forecast_interface.py:449`), which derives from that config — so **the config must bind at
import time**, i.e. **one entry point per trained config** (D1).

### G9 — **`mm/day` has no SAP3 canonical unit** (the blocker)
PT emits `discharge` in **`mm/day`** and consumes discharge/precipitation in `mm/day`. But
`_FI_UNIT_TO_CANONICAL` (`adapters/forecast_interface.py:119-130`) **deliberately omits
`MM_PER_DAY`** — its own comment says so — and `fi_unit_to_canonical` (`:153-160`) raises
`ConfigurationError` for it. Two locked tests enforce the omission
(`tests/unit/types/test_forcing_schema.py:103`, `tests/unit/adapters/test_fi_unit_mapping.py:31`).

`_ensemble_from_variable_output` calls it at `:194` **before** building the ensemble, so **every
predict would raise**; compatibility validation rejects the unit too
(`services/model_onboarding.py:173`), so the model would not even onboard.

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
(`db/metadata.py:934-936`), with no meaning for an artifact we did not train. And the
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
(`services/model_registry.py:60-67`). `adapt_if_fi` wraps it at discovery (`:76-84`). The config
ships as **package data** (D1).

**The shim owns the unit boundary (G9).** It must present SAP3-compatible units outward and convert
numerically in both directions:
- **discharge** — expose `M3_PER_S`, doing the **area-aware** `mm/day ↔ m³/s` conversion internally
  (`area` is already a required static).
- **precipitation** — audit what PT declares; if `MM_PER_DAY`, expose canonical `MM` (daily
  accumulation) and translate consistently.

**Red-first — assert NUMBERS, not mappability.** `M3_PER_S` and `MM` are **already** in
`_FI_UNIT_TO_CANONICAL` (`adapters/forecast_interface.py:119-130`), so a test that merely proves
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

**Invariant (implementation owns the mechanism):** an import is **all-or-nothing** — a failure leaves
no artifact row of any status, no changed prior ACTIVE row, no provenance row, and no orphaned file.

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
  *Recommendation: (A), reframing the aquacast image as **the forecast-cycle worker image** (a
  superset of the standard one) served by its own pool, with `default`/`ingest` workers staying
  torch-free because they no longer run the cycle. State whichever is chosen — the acceptance test's
  meaning depends on it.*

## Non-goals
- Retraining or fine-tuning in SAP3 (Plan 152 D3 — import-only for v1, flagged for revisit if ICON
  re-training is taken up).
- The Swiss data work (Plan 155) and the integration itself (Plan 152).

## Coordination
**`adapters/forecast_interface.py`** is edited by Plan 151 (T2 accessors) and Plan 156 (the
multi-resolution guard). This plan should need **no** change to it — the unit work lives in the shim
by design. If a task proposes editing it, stop and coordinate.

## References
- `docs/plans/152-aquacast-pooled-model-integration.md` — parent; artifact contract and decisions
  D1/D3/D10.
- `docs/plans/135-eqrn-offline-model-onboarding-benchmark.md` — decision 3 (separate package).
