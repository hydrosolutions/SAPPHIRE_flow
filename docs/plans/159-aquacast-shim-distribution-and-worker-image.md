---
status: DRAFT
created: 2026-08-13
plan: 159
title: aquacast shim distribution + forecast-cycle worker image — the EXTERNAL half of packaging
scope: Build the `sapphire-aquacast` shim distribution (a zero-argument entry-point class per trained config, owning the mm/day↔m³/s unit boundary and the mean_temperature↔temperature name boundary) and the worker image that carries it, so an aquacast model is discoverable and unit-correct in production. Split out of Plan 157 on 2026-08-13 because it is an EXTERNAL-REPO deliverable — this repo cannot build, install or test it, and attempting to do so from an `/implement`-driven plan produced two tests that could not fail. Blocked on the real distribution existing, which is itself gated on Plan 152/155 and the modeller's 5-day-horizon variant.
depends_on: [152, 155, 157]
blocks: [152]
supersedes: []
---

# Plan 159 — aquacast shim distribution + worker image

## ⚠️ Read this first: `/implement` CANNOT drive this plan from this repo

This is the lesson Plan 157 paid for. Its T1/T2 asked this repo to build and test a distribution that
lives outside it; `/implement` could not comply and produced:

- a shim test that **monkeypatched `importlib.metadata.entry_points`** with a fabricated class — green
  whether or not the real package existed;
- a "cold-start `discover_models()` in the deployed worker" test that ran on the **host interpreter**,
  unable to detect the exact failure it existed to catch.

Both were deleted. **The acceptance criteria below are therefore explicitly two-sided**: what the
external repo must ship, and what *this* repo can genuinely verify. Do not run `/implement` against
this plan expecting it to produce the distribution.

## Objective

Make an aquacast model **discoverable and unit-correct in production**. Plan 157 delivered the import
path; without this plan an imported artifact still cannot be constructed, cannot convert its units,
and is invisible to `discover_models()` in the deployed image.

## The problems (carried from Plan 157)

### G3 — the entry-point registry cannot construct an aquacast model
`discover_models` constructs each entry point **with no arguments** — `raw_instance = cls()`
(`services/model_registry.py:87`). aquacast needs
`AquacastModel(ModelTemplate.from_yaml(...), device=...)`, and the adapter computes
`data_requirements` at construction (`adapters/forecast_interface.py:453`), so **the config must bind
at import time**: one entry point per trained config (Plan 152 D1 — config ships as package data).

### G9 — **`mm/day` has no SAP3 canonical unit** (the blocker)
`_FI_UNIT_TO_CANONICAL` (`adapters/forecast_interface.py:123-134`) deliberately omits `MM_PER_DAY`
and `fi_unit_to_canonical` (`:157-164`) raises for it; two locked tests enforce this. **Re-verified by
execution against post-156 `main` (2026-08-13): still raises.** Since `_ensemble_from_variable_output`
calls it at `:199` before building the ensemble, **every predict would raise**.

Resolution belongs in the **shim**, not SAP3's unit map: our canonical discharge is `m³/s` and
`mm/day ↔ m³/s` is **area-dependent**, so a bare map entry would be numerically wrong.

### G15 — `mean_temperature` vs `temperature` is a NAME boundary
aquacast declares `mean_temperature`; SAP3's canonical names are `{"precipitation", "temperature"}`
(`config/deployment.py:132`). Same class as G9, same owner: expose canonical `temperature` outward,
translate internally.

### G10 — an external distribution is invisible in the production image
`discover_models()` sees only **installed** entry points; the runtime image runs
`uv sync --frozen --no-dev` against **this repo's** lockfile (`Dockerfile:32`) and copies only that
virtualenv (`:82`).

**Post-156 this failure is now SILENT.** `discover_models` gained an
`except UnsupportedModelRequirementError` clause (`services/model_registry.py:96`), so an
unrepresentable model is skipped per entry point. A missing or unsupported shim therefore surfaces
downstream as `MODEL_NOT_FOUND`, not as a clear packaging error.

## T0 — PREREQUISITE: bump ForecastInterface to v0.1.19 (drift check, 2026-08-14)

**aquacast merged `feat/subdaily-operational` (PR #127) on 2026-08-14 and now pins FI `v0.1.19`;
this repo pins `v0.1.17`.** The shim imports both, so installing aquacast into a `v0.1.17`
environment is an **unsatisfiable resolve** — the bump is a prerequisite for T1, not a follow-on.

**Drift check performed against our merged code — nothing we shipped is affected.** The four changes
between v0.1.17 and v0.1.19:

| change | impact |
|---|---|
| `AggregationMethod.MAX` added | **None for PT.** Returned only for `*_peak` channels; `cmal_pool_PT`'s config has no peak channel (verified). |
| `RunConfig` base + opaque config mapping (new `interface/run_config.py`) | **None** — additive, unconsumed. |
| `train`/`retrain`: `config: Any` → `config: Mapping[str, Any]` | **None** — we pass `ModelParams`, which *is* `dict[str, Any]` (`types/model.py:30`), so the narrowed type is already satisfied. |
| `predict` | **Unchanged** — the forecast path and everything Plan 155 touched are untouched. |

So the bump is low-risk: three additive changes plus one narrowing we already satisfy.

### Two semantic findings the shim must own
- **`MM_PER_HOUR` is cadence-dependent in aquacast.** Its own comment: at a sub-daily cadence
  `MM_PER_HOUR` means mm per *fine step*, **not** mm per wall-clock hour — *except* for peak
  channels, where it genuinely is per wall-clock hour. T1 owns the unit boundary, so this ambiguity
  lands on the shim. Inert for `cmal_pool_PT` (DAILY), live the moment sub-daily is in scope.
- **Models now declare `AggregationMethod` per variable**, deliberately using "the same predicate the
  trained binning used ... so what we declare and what the weights were fit on cannot drift apart."
  **We currently ignore it** — acceptable for a daily model, and a real question for Plan 153.

## Tasks

### T1 — the `sapphire-aquacast` distribution (EXTERNAL REPO)
One zero-argument entry-point class per trained config, binding `ModelTemplate.from_yaml(...)` +
device at construction and declaring the `model_tier` / `alert_eligibility` attributes
`_assert_model_classification_declared` requires (`services/model_registry.py:61-76`). Config ships
as package data (Plan 152 D1).

**It owns both boundaries:**
- **units (G9)** — expose `M3_PER_S` for discharge, doing the **area-aware** conversion internally;
  audit precipitation and expose canonical `MM` if it declares `MM_PER_DAY`.
- **names (G15)** — expose canonical `temperature`, translate internally to `mean_temperature`.

**Acceptance — in the EXTERNAL repo:** a **numeric** area-aware round trip (a known `mm/day` at a
known `area` arrives as the correct `m³/s`). **Asserting that `fi_unit_to_canonical` merely succeeds
proves nothing** — `M3_PER_S` and `MM` already map; that test passes on day one. This is the exact
trap that produced two unsound tests in Plan 156.

**Acceptance — in THIS repo, once the package is installable:** with the real distribution installed,
`discover_models()` **returns** the aquacast model (a positive assertion — post-156 a broken shim is
silently skipped, so "it constructs" is not "it is registered"), and compatibility reports **zero**
missing forcing for a target station.

### T2 — the worker image (this repo, but only once T1 exists)
Per Plan 157 D13, the aquacast image **is the forecast-cycle worker image** — a superset of the
standard one on its own pool; `default`/`ingest` stay torch-free because they no longer run the cycle.

**Do not rebuild the pool before the image differs.** Plan 157 shipped exactly that and it was
reverted: `prefect-worker-forecast-cycle` used `build: *app-build` and the same
`sapphire-flow:${VERSION}` image, so the split bought nothing while adding a third pool, a
mixed-version upgrade window, and contradictions with `orchestration.md`. **The pool and the image
must land together**, with `import-model-artifact` routed alongside `forecast-cycle`.

**Acceptance:** a cold-start `discover_models()` **inside the built container**, asserting the
aquacast entry point resolves. A host-interpreter check does not count — that is precisely the gap
this task exists to close.

**Also required:** update `docs/standards/cicd.md` (topology + an upgrade procedure that stops the
cycle worker during migration) and `docs/standards/orchestration.md` (which still describes two
pools), and stop both workers during rerouting so no mixed-version window opens.

## Non-goals
- The import path and provenance — **delivered by Plan 157**.
- Retraining in SAP3 (Plan 152 D3), multi-resolution support (Plan 153).

## References
- `docs/plans/157-...md` — the in-repo half; its rescoping note explains why this plan exists.
- `docs/plans/152-...md` — artifact contract, decisions D1/D10/D13, and the four substitutions.
