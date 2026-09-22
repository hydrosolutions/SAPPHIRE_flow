---
status: DRAFT
created: 2026-09-22
plan: 312
title: A GROUP-scoped FI model can be onboarded but not served — the forecast cycle never supplies a station_code_resolver
scope: Give the operational forecast cycle the `station_code_resolver` that the FI adapter requires for GROUP conversion, so a GROUP-scoped ForecastInterface model can actually produce forecasts. Also in scope, narrowly: a **read-only accessor** on SAP3's `ForecastInterfaceAdapter` so the cycle can see whether a resolver is already attached. Explicitly NOT changing the FI contract, NOT any other part of that adapter (the setter and `adapt_if_fi` semantics are untouched), NOT changing the STATION forecast path, NOT the onboarding flow's behaviour (it already works), NOT training or hindcast, NOT the group-assignment or artifact-import machinery, NOT Plan 311's seam question.
depends_on: []
blocks: [262]
open_decisions: []
source: 2026-09-22 — the Plan 262 T5 first-forecast attempt on the mac-mini at v0.1.949. Every line anchor below was read from `main` at `40bdc3d3`; the log lines are from that run.
---

# Plan 312 — the resolver the forecast cycle never supplies

## Status

**DRAFT — all three decisions CLOSED by the owner 2026-09-22; FIVE review passes folded.**
⛔ **Not READY**: this closed state has not itself been reviewed, and on this week's evidence every
fold has introduced or left something.

| decision | closed as |
|---|---|
| **D1** where the resolver builder lives | **move to a shared home**, as its own pure-relocation step |
| **D2** which models to adapt | **every** discovered model — *and every caller-supplied one* |
| **D3** whose resolver wins on conflict | 🔑 **the EXISTING one.** The cycle fills a gap and never overrides; the guard goes at the cycle's call site, NOT in `adapt_if_fi`, which onboarding depends on |

Written from a measured failure, not from a reading of the code.

⚖️ **Plan number 312 is claimed, not granted.** *(Checked at creation, 2026-09-22, before this file
existed: 312 was unused across `docs/plans/` and `docs/plans/archive/`, and nothing in `docs/`
referred to a "Plan 312". Stated as the pre-creation check rather than a present-tense claim, which
this file itself now falsifies — fifth review pass, minor.)*

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
| the only production caller that supplies one, **on the paths reviewed** | `flows/onboard_model.py:854-857` — the **onboarding** flow. ⚠️ No pass performed a repository-wide caller audit, so this is not a global claim |
| the forecast cycle | `flows/run_forecast_cycle.py:2357` calls `discover_models()` and **never adapts** |

⟹ **a GROUP-scoped FI model can be onboarded but not served.**

⚠️ **This is ONE blocking dependency, not the whole of "operational `GroupForecastModel` support".**
*(Independent review 2026-09-22, minor: an earlier wording equated the two.)* GROUP discovery,
assembly, prediction and persistence all **exist**; in the T5 attempt discovery and assembly
**succeeded** and prediction was **attempted**. ⛔ Nothing was persisted — `run_group_forecast`
returned `{}`, so there were no station results to write. *(Second review pass, minor: an earlier
wording said all four "ran".)* This repairs the single missing wire; closing it does not discharge
every GROUP follow-on `CLAUDE.md` lists.

⚠️ **Not a regression**: the path was never wired. But ⛔ **"it could not have been noticed" is too
strong** — a synthetic test could have caught it at any time. What is true is narrower: until Plan
262 T3b (2026-09-22) there was **no group model assignment in the database at all**
(`SELECT * FROM group_model_assignments` → 0 rows), so no *production* run ever entered this path.

## Tasks

### T0 — relocate the resolver builder, changing nothing

**Outcome.** `_build_station_code_resolver` lives where both the onboarding flow and the forecast
cycle can import it, with **no behaviour change anywhere**.

🔴 **Its own task and its own commit** — D1 closed as "move it, as a pure relocation", and
*(third review pass, moderate)* the earlier task list bundled that into T2, where a reviewer could
not see that it changes nothing.

**In.** `flows/onboard_model.py` (remove the private definition, import instead) and the shared home.

**Out.** ⛔ **No edit to the function body.** Not one rule about missing stations or empty codes may
change while it moves. ⛔ No call-site behaviour change in onboarding.

**Verification.** The relocation commit's diff shows the function's body **unchanged** (a pure move),
and onboarding's existing tests pass untouched.

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

**In.** `flows/run_forecast_cycle.py`; an **import** of the relocated builder from its shared home
(T0 moved it — this task does not move anything); and `adapters/forecast_interface.py` for a
**read-only accessor only**, so the guard can observe resolver presence without touching a private
field (D3). ⛔ The setter and `adapt_if_fi`'s semantics stay exactly as they are.

🔴 **Attach AFTER the `if models is None:` branch, not inside it.** `run_forecast_cycle_flow`
accepts a caller-supplied `models` parameter (`:2138`), and discovery runs **only when it is
absent** (`:2354-2357`). Injected models reach the same GROUP dispatch, so attaching at the
discovery site alone leaves a supported entry point broken. *(Independent review 2026-09-22,
moderate — I had placed it inside the branch.)* **Both routes must be exercised**, including an
**already-wrapped GROUP adapter**, since `adapt_if_fi` attaches to those in place rather than
re-wrapping.

**Out.** ⛔ **The STATION path must not change behaviour.**

⛔ **Do NOT claim this is inert for every existing model — it is not.** *(Second review pass,
moderate; an earlier wording did.)* `adapt_if_fi` calls an **unconditional** setter
(`forecast_interface.py:476`), so attaching **replaces any resolver the object already carries** and
the replacement **persists on the caller's object after the cycle returns**. A caller-supplied GROUP
adapter configured with its own resolver works **today**; overwriting its station-code mapping could
break artifact/code compatibility and every later use of that object. **D3 settles it: the existing resolver WINS** — attach only
when absent, leaving a configured adapter's mapping and identity untouched after the call.
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
🔴 **Three acceptance cases, named separately.** *(Fourth review pass, moderate: "including an
already-wrapped GROUP adapter" was satisfiable by the configured case below, so a wrong
skip-existing-adapters guard could still have passed.)* Note discovery **already wraps** before
returning (`services/model_registry.py:108`), so a fixture handing back a raw FI model does not
represent the real path:

  1. **discovered**, already-wrapped GROUP adapter with **no** resolver → two stations' expected rows
     persist;
  2. **caller-supplied**, already-wrapped GROUP adapter with **no** resolver → the same;
  3. **caller-supplied** adapter with a **conflicting** resolver → its mapping, its resolver identity
     and the adapter identity all survive the call. An already-wrapped adapter with *no* resolver does not exercise this — it is the case
  the first draft would have tested and learned nothing from;
- the resolver's own failure modes still raise clearly: unknown station id, and a station whose
  `code` is empty (`onboard_model.py:93-102` already distinguishes these).

### T3 — re-run Plan 262 T5

**Outcome.** `cmal_small` produces forecast rows for both pilot stations on the staging host.

**Depends on T2 being deployed.** ⛔ Not part of this plan's merge gate — it is the live
verification that 262 has been waiting for, and it belongs to 262's record.

## Owner decisions

### D1 — ✅ CLOSED, owner 2026-09-22: move it to a shared home (option a)

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

### D2 — ✅ CLOSED, owner 2026-09-22: adapt every discovered model (and every supplied one)

Adapting everything is what onboarding does. Adapting conditionally means the cycle
must know which models are group-assigned before it prepares them — information it has, but later.

⚖️ **CLOSED as adapt-everything, owner 2026-09-22** — every discovered model *and* every
caller-supplied one, combined with D3's attach-only-when-absent. Review confirmed the STATION claim
from the code: attachment
only assigns the adapter's resolver field (`forecast_interface.py:476-481`), STATION input
conversion keeps the literal `"station"` key (`:1254-1293`), STATION prediction never consults the
resolver, and non-FI objects pass through untouched.

⚠️ **But it is not universally inert**, and the plan should not say so: attachment **replaces any
existing resolver and mutates the adapter in place**. Preserving that same instance is also what
keeps discovery's copied classification attributes — which is an argument for attaching rather than
re-wrapping, and a reason a fix that constructs a fresh adapter would be wrong.

### D3 — ✅ CLOSED, owner 2026-09-22: the EXISTING resolver wins (option b)

*(Raised by the second review pass, moderate. Not a detail: `adapt_if_fi`'s setter is unconditional,
so "just call it" silently answers this question with "the cycle's".)*

| option | behaviour | cost |
|---|---|---|
| **(a)** cycle's resolver always wins | today's `adapt_if_fi` semantics, one line | silently overwrites a deliberate caller configuration, and the change **outlives the call** on the caller's object |
| **(b)** attach ONLY when absent | the cycle fills a gap and never overrides | one conditional at the cycle's call site; a caller wanting the cycle's resolver supplies a freshly constructed adapter without one — ⛔ NOT by "clearing theirs", an operation that does not exist (the setter takes a callable, not `None`, and a read-only getter adds no clearing API) |
| **(c)** raise on conflict | no silent anything | turns a working injected-model call into a failure |

⚖️ **CLOSED as (b), owner 2026-09-22.** A supplied adapter carrying its own resolver was configured
deliberately, and the operational cycle has no standing to overrule it — especially when the
overwrite persists after the call. ⛔ **The guard goes at the CYCLE's call site, not in
`adapt_if_fi`**: onboarding depends on attach-always (`onboard_model.py:854-857`).

🔴 **But the guard has nothing to read.** *(Third review pass, moderate.)* The resolver is stored
privately (`forecast_interface.py:472`) and the only public accessor is the **unconditional**
setter (`:476-481`). So "attach only when absent" is **not expressible through today's public
interface**, and the plan must say which way out it takes:

| | |
|---|---|
| ✅ **add a read-only accessor** to `ForecastInterfaceAdapter` and widen scope to that file | a getter is not an FI-contract change — the contract is the FI protocol, not this adapter's surface — and it makes the precedence rule expressible without reaching into a private field |
| reach into `_station_code_resolver` from the cycle | works (Python permits it), but couples the flow to the adapter's internals for want of one line |

**Take the accessor.** The scope line's "NOT changing the FI contract" stands; adding a getter to a
SAP3-side adapter is not that, and this plan now names `adapters/forecast_interface.py` as in scope
**for a read-only accessor only**.

🔴 **The guard must test RESOLVER ABSENCE, not adapter existence.** Testing "is it already wrapped"
would **skip the discovered GROUP adapter that is the entire reason for this plan** — discovery
wraps FI models *with no resolver* (`adapt_if_fi`'s own comment).

## Watch items

- 🪤 **The FI adapter is shared with training and hindcast.** This plan changes the operational
  cycle **and adds one read-only accessor to that shared adapter** — a getter, which no existing
  caller can be affected by, but it is a change to shared code and should be reviewed as one; do not let the fix migrate into `services/training_data.py` or `hindcast.py` without their
  own reasoning — they have their own resolver story, and Plan 262's onboarding already worked.
- 🪤 **`discover_models()` wraps FI models with NO resolver** (`adapt_if_fi`'s own comment). A fix
  that constructs a *new* adapter instead of attaching to the existing one would silently drop
  whatever else discovery configured.
- **This unblocks 262 T5 but not Plan 311.** The seam question is independent and still open; 311's
  D1 option (a) (the 00:00Z cycle) remains the recommended first observation.

## Exit gates

- T1's red is the **resolver error**, evidenced, not a signature or lookup failure.
- ✅ D1, D2 and D3 all closed (owner, 2026-09-22) — D3 as **existing-wins**.
- 🔴 **All THREE acceptance cases in T2 are exercised**, named separately: discovered-wrapped-without-
  resolver, supplied-wrapped-without-resolver, and supplied-with-a-conflicting-resolver. ⛔ The first
  two are not interchangeable with the third — a guard that wrongly skips *any* already-wrapped
  adapter passes the third alone.
- The read-only accessor is the **only** change to `adapters/forecast_interface.py`; the setter and
  `adapt_if_fi` are untouched.
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
    { "id": "P0", "tasks": ["T0"],
      "note": "pure relocation of the resolver builder, its own commit, body unchanged (D1)" },
    { "id": "P1", "tasks": ["T1"], "depends_on": ["P0"],
      "note": "red first, through the CYCLE's path — not by calling _require_resolver directly" },
    { "id": "P2", "tasks": ["T2"], "depends_on": ["P1"], "decision": "D1, D2, D3 all CLOSED",
      "note": "attach AFTER the `if models is None` branch, ONLY when the resolver is absent (read via a new read-only accessor); station path shown unchanged by REPLAY under frozen inputs" },
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


**2026-09-22 — second review pass: NEEDS CHANGES (1 moderate, 1 minor). Folded.**

| finding | what it was |
|---|---|
| **moderate** | the fold correctly said attachment is not inert, then **left the blanket "should be inert for every existing model" standing in T2's Out** — the same fix-one-site-leave-its-twin failure the corpus records. Worse, the prescribed already-wrapped test case need not carry an **existing resolver**, so it would have exercised nothing. A supplied GROUP adapter with its own resolver **works today**; overwriting its mapping could break artifact/code compatibility, and the overwrite **outlives the call**. **D3 added**, and it now gates T2 |
| minor | "discovery, assembly, prediction and persistence all **ran**" is false — prediction was *attempted* and nothing was persisted, because `run_group_forecast` returned `{}` |

**Cleared by this pass:** the replay prescription **is achievable** — the cycle takes `clock` and
`rng` explicitly (`:2124`) and stores, models, config, artifacts and model state are all injectable,
with live defaults only when omitted (⚠️ reusing mutated stores or an advanced RNG would *not*
satisfy "same initial state"); attachment after the `models is None` branch **is** the correct common
point, with no third route found in the inspected cycle; and the swallowed-error corrections are
operative in T1, T2, the exit gates and the phase graph.

⚠️ Still carried: neither pass performed a repository-wide caller audit, so *"the only production
caller"* holds for the inspected paths only.


**2026-09-22 — third review pass (the closed state): NEEDS CHANGES (2 moderate, 1 minor). Folded.**

| finding | what it was |
|---|---|
| **moderate** | 🔴 **D3 as closed was not implementable.** "Attach only when absent" needs to *observe* absence, but the resolver is private and the only public accessor is the unconditional setter. The plan now takes a **read-only accessor** (and says why that is not an FI-contract change) rather than reaching into a private field. It also makes explicit that the guard tests **resolver absence, not adapter existence** — the latter would skip the discovered GROUP adapter this plan exists for |
| **moderate** | D1 closed as "a pure relocation, its own commit", but **no such task existed** — it was bundled into T2 where a reviewer could not see it changes nothing. **T0 added**, with the phase graph and scope line updated |
| minor | the closure was incompletely swept: T2 still read "cannot be written until D3 decides", the verification still said "assert which mapping", D2 still called it "one line", and the graph still said "D3 GATES this task" |

⭐ **All three are the same failure: closing a decision is not the same as folding it.** The status
table said existing-wins while the operative surfaces still described an open choice — and the one
that mattered, the accessor, only surfaced because someone asked whether the closure could actually
be implemented.


**2026-09-22 — fourth review pass: NEEDS CHANGES (2 moderate, 2 minor). Folded.**

| finding | what it was |
|---|---|
| **moderate** | 🔴 **the accessor never reached the implementation scope.** D3 authorised the getter, but the scope line and T2's In still named only the cycle and the builder's home — and **the previous changelog claimed the scope line had been updated when it had not.** *(Cause: that one edit was the single string replacement written without an assert, so it silently matched nothing. Fourth silent no-op this week; every replacement in this fold asserts, and the fold re-reads the file to confirm each landed.)* |
| **moderate** | "including an already-wrapped GROUP adapter" was satisfiable by the **configured** adapter case, so a wrong skip-existing-adapters guard could still pass. Three cases are now named separately — and discovery **already wraps** before returning (`model_registry.py:108`), so a fixture returning a raw FI model does not represent the real path |
| minor | D3's cost column said a caller "must clear theirs" — an operation that **does not exist**: the setter takes a callable, not `None`, and a getter adds no clearing API |
| minor | the status line said "two review passes" with three recorded, and the Why table stated "the only production caller" unqualified while the changelog limited it to inspected paths |

**Confirmed by this pass:** T0 is correct and `P0 → P1 → P2 → P3` matches D1's closure; no operative
task still assigns relocation to T2; and adding a getter genuinely does **not** change the FI
contract — it exposes SAP3 adapter configuration without touching FI model methods, input/output
semantics or artifacts — though it *does* narrowly expand that adapter's public surface, which the
scope line and watch item now say.
