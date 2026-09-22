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

⟹ **a GROUP-scoped FI model can be onboarded but not served.**

⚠️ **This is ONE blocking dependency, not the whole of "operational `GroupForecastModel` support".**
*(Independent review 2026-09-22, minor: an earlier wording equated the two.)* GROUP discovery,
assembly, prediction and persistence all already exist and all ran in the T5 attempt — this repairs
the single missing wire, and closing it does not discharge every GROUP follow-on `CLAUDE.md` lists.

⚠️ **Not a regression**: the path was never wired. But ⛔ **"it could not have been noticed" is too
strong** — a synthetic test could have caught it at any time. What is true is narrower: until Plan
262 T3b (2026-09-22) there was **no group model assignment in the database at all**
(`SELECT * FROM group_model_assignments` → 0 rows), so no *production* run ever entered this path.

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

🔴 **And the red cannot be "the cycle failed", because the cycle does NOT fail.**
`services/run_group_forecast.py:504-512` catches the exception and returns `{}`; the cycle then
completes normally. *(Independent review 2026-09-22, moderate.)* That is exactly why the T5 run
logged `forecast_cycle.complete` while producing nothing. So the red must assert **both**:

* the `run_group_forecast.predict_batch_failed` log carries the exact resolver error, **and**
* **no GROUP forecast rows were written.**

⚠️ Asserting only that the error disappeared would pass against a *different* failure that is
equally silent.

### T2 — supply the resolver in the forecast cycle

**Outcome.** The cycle adapts discovered FI models with a resolver built from its own
`station_store`, and the group path produces forecasts.

**In.** `flows/run_forecast_cycle.py`, plus wherever `_build_station_code_resolver` ends up (D1).

🔴 **Attach AFTER the `if models is None:` branch, not inside it.** `run_forecast_cycle_flow`
accepts a caller-supplied `models` parameter (`:2138`), and discovery runs **only when it is
absent** (`:2354-2357`). Injected models reach the same GROUP dispatch, so attaching at the
discovery site alone leaves a supported entry point broken. *(Independent review 2026-09-22,
moderate — I had placed it inside the branch.)* **Both routes must be exercised**, including an
**already-wrapped GROUP adapter**, since `adapt_if_fi` attaches to those in place rather than
re-wrapping.

**Out.** ⛔ **The STATION path must not change behaviour.** `adapt_if_fi` returns non-FI objects
untouched and attaches to an already-wrapped adapter, so this should be inert for every existing
model — but "should be" is not evidence, and T2's verification has to demonstrate it.
⛔ No change to the FI contract, the adapter's requirement, or the onboarding flow.

**Verification.**
- the T1 red turns green — and the **green** asserts persisted forecast rows for **two distinct
  station ids with distinguishable expected values**, not merely that the cycle completed. Two
  stations also exercise the code→id mapping across the FI boundary, which is the thing the
  resolver exists for;
- 🔴 **a station-path regression check, done as a REPLAY.** ⛔ *"Compare against the previous
  cycle's rows"* — which this task said first — **is not a controlled comparison**: the cycle uses a
  live clock and an unseeded RNG (`run_forecast_cycle.py:2200-2203`) and persists model state, so
  two successive cycles differ for reasons that have nothing to do with this change. *(Independent
  review 2026-09-22, major.)* The comparison must replay **the same cycle** against **frozen
  inputs** — same artifacts, assignments, configuration, initial model state, injected clock and
  seeded RNG — and compare forecast **values, valid times, representations and metadata**, excluding
  generated row ids. Comparing real rows remains the right bar; the method had to change, not the
  bar;
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

Recommendation: **adapt everything**. Review confirmed the STATION claim from the code: attachment
only assigns the adapter's resolver field (`forecast_interface.py:476-481`), STATION input
conversion keeps the literal `"station"` key (`:1254-1293`), STATION prediction never consults the
resolver, and non-FI objects pass through untouched.

⚠️ **But it is not universally inert**, and the plan should not say so: attachment **replaces any
existing resolver and mutates the adapter in place**. Preserving that same instance is also what
keeps discovery's copied classification attributes — which is an argument for attaching rather than
re-wrapping, and a reason a fix that constructs a fresh adapter would be wrong.

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
- The station path is shown unchanged by **comparison of real forecast rows from a REPLAY of the
  same cycle under frozen inputs** — not by successive live cycles (uncontrolled) and not by an
  assertion that restates the code.
- The resolver is attached on **both** model routes — discovered and caller-supplied — and both are
  exercised, including an already-wrapped GROUP adapter.
- The T1 red asserts the resolver error **and** the absence of GROUP rows, because
  `run_group_forecast` swallows the exception and the cycle completes regardless.
- The full unit suite passes, and CI is green on the merge commit.
- ⛔ **Independent review before READY**, and again on the fold — every fold on Plans 309 and 262
  this week introduced or left something.

```json
{
  "phases": [
    { "id": "P1", "tasks": ["T1"],
      "note": "red first, through the CYCLE's path — not by calling _require_resolver directly" },
    { "id": "P2", "tasks": ["T2"], "depends_on": ["P1"], "decision": "D1 and D2",
      "note": "attach AFTER the `if models is None` branch so caller-supplied models are covered too; station path shown unchanged by REPLAY under frozen inputs, not by successive live cycles" },
    { "id": "P3", "tasks": ["T3"], "depends_on": ["P2"],
      "note": "live re-run of 262 T5 after deploy; belongs to 262's record, not this merge gate" }
  ]
}
```

## Changelog

**2026-09-22 — created** from the Plan 262 T5 failure, the same morning it was observed. Not
reviewed.


**2026-09-22 — independent review: NEEDS CHANGES (1 major, 2 moderate, 1 minor). Folded.**

| finding | what it was |
|---|---|
| **major** | the station-path check compared **successive live cycles**, which cannot establish a regression: the cycle uses a live clock and unseeded RNG and persists model state. Replaced with a **replay of the same cycle under frozen inputs**. The bar (real forecast rows) was right; the method was not |
| moderate | the fix was placed **inside** the `if models is None:` branch, leaving the caller-supplied `models` entry point broken — it reaches the same GROUP dispatch |
| moderate | 🔴 `run_group_forecast.py:504-512` **catches** the resolver exception and returns `{}`, so the cycle completes normally — which is exactly what the T5 run did. Red and green evidence must therefore name the log AND the absence/presence of rows; "the cycle completed" proves nothing |
| minor | equating this one wire with the whole of operational `GroupForecastModel` support, and claiming the gap "could not have been noticed" when a synthetic test could have caught it at any time |

**Confirmed by the review, and worth keeping:** all five code anchors check out; the cycle-level
composition point is appropriate; **Plan 151 is NOT a second unresolved GROUP route** —
`per_track_eligible_stations` explicitly excludes grouped stations (`run_forecast_cycle.py:1814-1835`);
D2's STATION-path claim holds in the code; and no FI-contract deviation or training/hindcast
regression follows from the proposed wiring.

⚠️ **Carried, not resolved:** the review's budget did not permit a repository-wide caller audit, so
*"the only production caller"* is verified for the paths inspected, **not globally**.
