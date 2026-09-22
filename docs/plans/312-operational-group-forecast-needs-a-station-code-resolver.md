---
status: DRAFT
created: 2026-09-22
plan: 312
title: A GROUP-scoped FI model can be onboarded but not served — the forecast cycle never supplies a station_code_resolver
scope: Give the operational forecast cycle the `station_code_resolver` that the FI adapter requires for GROUP conversion, so a GROUP-scoped ForecastInterface model can actually produce forecasts. Explicitly NOT changing the FI contract, NOT changing the STATION forecast path, NOT the onboarding flow's behaviour (it already works), NOT training or hindcast, NOT the group-assignment or artifact-import machinery, NOT Plan 311's seam question.
depends_on: []
blocks: [262]
open_decisions: [D1, D2]
source: 2026-09-22 — the Plan 262 T5 first-forecast attempt on the mac-mini at v0.1.949. Every line anchor below was read from `main` at `40bdc3d3`; the log lines are from that run.
---

# Plan 312 — the resolver the forecast cycle never supplies

## Status

**DRAFT. Not reviewed.** Written from a measured failure, not from a reading of the code.

⚖️ **Plan number 312 is claimed, not granted** — unused in `docs/plans/` and `docs/plans/archive/`,
and nothing in `docs/` refers to a "Plan 312".

## What happened

Plan 262 T5 ran the first real forecast for the Swiss `cmal_small` pilot on the mac-mini. The cycle
reached the model and stopped:

```
run_group_forecast.predict_batch_failed
  error='station_code_resolver required for GROUP input conversion / train / predict'
  group_id=13f2f0b0-…  model_id=cmal_small
forecast_cycle.group_completed  stations_forecast=0
```

🔑 **Everything upstream worked.** The group was discovered, the assignment resolved, inputs were
assembled — including Plan 261's in-memory tail fill
(`filled=['precipitation@2026-09-21', 'temperature@2026-09-21']`) — and forcing resolved to 58 rows
across `meteoswiss_tabsd`/`rprelimd`. The cycle then completed normally for every other model.
**Zero `cmal_small` forecast rows exist.**

## Why

| | |
|---|---|
| the FI adapter **requires** a resolver for GROUP conversion | `adapters/forecast_interface.py:1595-1600` — `_require_resolver` raises `ConfigurationError` when `self._station_code_resolver is None` |
| the injection point exists and is well-behaved | `adapt_if_fi(obj, station_code_resolver=…)` (`:192-210`) — its own comment says `discover_models()` wraps FI models **with no resolver**, so a later call must **attach** to the already-wrapped adapter rather than drop it |
| a builder already exists | `flows/onboard_model.py:87-104` `_build_station_code_resolver(station_store)` — fetches the station, rejects a missing station and an empty code with explicit errors |
| the **only** production caller that supplies one | `flows/onboard_model.py:854-857` — the **onboarding** flow |
| the forecast cycle | `flows/run_forecast_cycle.py:2357` calls `discover_models()` and **never adapts** |

⟹ **a GROUP-scoped FI model can be onboarded but not served.** That is the
**operational `GroupForecastModel` support** `CLAUDE.md` already lists as an outstanding v0b/v0c
follow-on; this plan is that item, now located.

⚠️ **Not a regression.** Nothing broke — the path was never wired. It could not have been noticed
before, because until Plan 262 T3b (2026-09-22) there was **no group model assignment in the
database at all** (`SELECT * FROM group_model_assignments` returned 0 rows).

## Tasks

### T1 — a red test that fails for the right reason

**Outcome.** A test that drives the **operational group path** with a GROUP-scoped FI model and
fails with the resolver `ConfigurationError` — before any fix.

**Out.** ⛔ Not a test that calls `_require_resolver` directly: that proves the raise, not the
wiring. The fault is that the **cycle** does not supply the resolver, so the test must enter
through the cycle's own model-preparation path.
⛔ Not gated on the `aquacast` extra — CI installs it only conditionally, and a test that silently
skips there is how this class of gap survives. Use a synthetic FI model declaring GROUP scope, as
`tests/unit/models/test_aquacast_shim_translation.py` does for the shim.

**Verification.** ⚠️ The red must be the resolver error specifically. A `TypeError`, an import
error, or "no group assignment found" all mean the test never reached the fault
(`feedback_red_first_must_prove_the_fault`).

### T2 — supply the resolver in the forecast cycle

**Outcome.** The cycle adapts discovered FI models with a resolver built from its own
`station_store`, and the group path produces forecasts.

**In.** `flows/run_forecast_cycle.py` around the `discover_models()` call (`:2355-2357`), plus
wherever `_build_station_code_resolver` ends up (D1).

**Out.** ⛔ **The STATION path must not change behaviour.** `adapt_if_fi` returns non-FI objects
untouched and attaches to an already-wrapped adapter, so this should be inert for every existing
model — but "should be" is not evidence, and T2's verification has to demonstrate it.
⛔ No change to the FI contract, the adapter's requirement, or the onboarding flow.

**Verification.**
- the T1 red turns green;
- 🔴 **a station-path regression check**: the existing 148-station models produce the same forecasts
  as before the change, on identical inputs. The cheapest honest form is a cycle run on staging
  compared against the previous cycle's rows — not a unit assertion that `adapt_if_fi` is a no-op,
  which merely restates the code;
- the resolver's own failure modes still raise clearly: unknown station id, and a station whose
  `code` is empty (`onboard_model.py:93-102` already distinguishes these).

### T3 — re-run Plan 262 T5

**Outcome.** `cmal_small` produces forecast rows for both pilot stations on the staging host.

**Depends on T2 being deployed.** ⛔ Not part of this plan's merge gate — it is the live
verification that 262 has been waiting for, and it belongs to 262's record.

## Owner decisions

### D1 — where should `_build_station_code_resolver` live?

It is currently private to `flows/onboard_model.py`.

| option | cost |
|---|---|
| **(a)** move it to a shared home (e.g. `services/model_registry.py`) and import from both | touches the onboarding flow, which currently works — a change to a working path for a caller's convenience |
| **(b)** leave it and have the cycle build its own two-line closure | duplication; two places to fix if the "empty code" rule changes |
| **(c)** move it and re-export from `onboard_model` for compatibility | the worst of both — indirection without removing the duplicate |

Recommendation: **(a)**, because the rejection rules (missing station, empty code) are a *contract*
about what a resolver may return, and duplicating a contract is how the two copies drift. ⚠️ The
onboarding flow must be covered by its existing tests after the move, and the move must be a pure
relocation in its own commit so a reviewer can see it changes nothing.

### D2 — adapt every discovered model, or only when a GROUP assignment exists?

Adapting everything is what onboarding does and is one line. Adapting conditionally means the cycle
must know which models are group-assigned before it prepares them — information it has, but later.

Recommendation: **adapt everything**. `adapt_if_fi` is explicitly a no-op for non-FI objects and an
attach for already-wrapped ones, so the blast radius is a resolver reference on adapters that do not
use it. ⚠️ That claim is exactly what T2's station-path regression check has to demonstrate rather
than assume.

## Watch items

- 🪤 **The FI adapter is shared with training and hindcast.** This plan changes only the operational
  cycle; do not let the fix migrate into `services/training_data.py` or `hindcast.py` without their
  own reasoning — they have their own resolver story, and Plan 262's onboarding already worked.
- 🪤 **`discover_models()` wraps FI models with NO resolver** (`adapt_if_fi`'s own comment). A fix
  that constructs a *new* adapter instead of attaching to the existing one would silently drop
  whatever else discovery configured.
- **This unblocks 262 T5 but not Plan 311.** The seam question is independent and still open; 311's
  D1 option (a) (the 00:00Z cycle) remains the recommended first observation.

## Exit gates

- T1's red is the **resolver error**, evidenced, not a signature or lookup failure.
- D1 and D2 are closed, or explicitly carried with the carrier named.
- The station path is shown unchanged by **comparison of real forecast rows**, not by an assertion
  that restates the code.
- The full unit suite passes, and CI is green on the merge commit.
- ⛔ **Independent review before READY**, and again on the fold — every fold on Plans 309 and 262
  this week introduced or left something.

```json
{
  "phases": [
    { "id": "P1", "tasks": ["T1"],
      "note": "red first, through the CYCLE's path — not by calling _require_resolver directly" },
    { "id": "P2", "tasks": ["T2"], "depends_on": ["P1"], "decision": "D1 and D2",
      "note": "supply the resolver; the station path must be shown unchanged" },
    { "id": "P3", "tasks": ["T3"], "depends_on": ["P2"],
      "note": "live re-run of 262 T5 after deploy; belongs to 262's record, not this merge gate" }
  ]
}
```

## Changelog

**2026-09-22 — created** from the Plan 262 T5 failure, the same morning it was observed. Not
reviewed.
