---
status: DRAFT
revised: 2026-09-16
created: 2026-09-11
plan: 271
reviews:
  - "claude 2026-09-11 r1 design/proportionality — NOT READY, 2 blockers + 4 majors + 3 minors; all verified, all folded"
  - "codex 2026-09-11 r1 citation verification — NOT READY, 2 blockers + 2 majors + 2 minors; all verified, all folded"
  - "claude 2026-09-11 r2 task-implementability — NOT READY, 3 blockers + 6 majors + 5 minors; all verified, all folded"
  - "codex 2026-09-11 r2 citation verification — NOT READY, 0 blockers + 3 majors + 3 minors; all verified, all folded"
  - "claude 2026-09-11 r3 coherence/implementability — NOT READY, 3 blockers + 7 majors + 4 minors; all verified, all folded"
  - "codex 2026-09-11 r3 citation verification — NOT READY, 0 blockers + 1 major + 3 minors; all verified, all folded"
  - "claude 2026-09-11 r4 implementability — NOT READY, 1 blocker + 2 majors + 3 minors; all verified, all folded"
  - "codex 2026-09-11 r4 citation verification — NOT READY, 0 blockers + 1 major + 3 minors; all verified, all folded"
  - "gpt-6-astra 2026-09-11 expert consultation on D1 — recommends complying with the FI mapping; folded as the recommendation, not as the decision"
title: Classify state-free models as FRESH without hiding genuine missing warm-up state
scope: Resolve the four-way conflict over what `warm_up_source` means for a model that holds no state, make the classifier honour the resolution, and correct whichever documents lose. Covers the `warm_up` category ONLY. NOT implementing warm-up state persistence (already built, see §What already exists), NOT a change to the ForecastInterface signature, NOT the `observation`/`NWP`/`forcing` categories, NOT Plan 270's forcing-gap detection.
depends_on: []
blocks: []
related: [270, 023, 262, 253]
source: 2026-09-11 — measured on the mac mini in Plan 261 T1's first post-deploy cycle (12:00Z, 514 forecasts). Plan 270 measured the same saturation independently the same day and explicitly scoped it out as unowned (`270:139-141`). Host numbers are from that host on that day and are NOT verifiable from the repository; re-measure before quoting.
---

# Plan 271 — state-free forecasts use FRESH; genuine missing state stays COLD_START

> ⚠️ **Plan numbers 270 and 271 are not PR numbers.** PR #270 is Plan 261's merge (`75cf80cf`).

## Status

DRAFT. D1–D6 are closed, including the owner's clarification in this pass that D6
applies only to undeclared models and the stateful golden is preserved. No owner
design decision remains open; implementation is still prohibited while DRAFT.
The dedicated `docs/plan-271-review` worktree was refreshed to fetched `origin/main`
`925ec5999862190f8c16c60c8664b9932699f5b3` on 2026-09-16, preserving the prior
uncommitted refinement from `3889c816` → `f2dc569c`. The warm-up implementation and
FI pin are unchanged; Plan 262 has added an eighth model entry point and a separate
forecast-worker image. The inventory, test scope, spec citations and deployment
evidence below account for those changes. This material revision is **not
independently reviewed**. Earlier reviews do not approve it. Only the owner can
set READY.

**Contract baseline.** SAPPHIRE Flow has no `docs/model_interface.md`. That requested
reference is in the sibling ForecastInterface repository. Contract evidence here
uses the revision pinned by `uv.lock:1443`: FI `v0.1.20`,
`ad19597e02f7bbd77b69f6339db3351847fe9791`, not uncommitted files in the sibling
checkout. Read its `docs/model_interface.md`, `docs/input_requirement.md`,
`forecast_interface/interface/protocol.py`, `interface/result.py`, and
`input/requirement.py`. FI citations below are relative to that repository/revision;
SAP3 source paths abbreviated as `services/`, `types/`, etc. are under
`src/sapphire_flow/`.

## Fresh review findings (2026-09-11)

These findings refine the proposal; they do not close an owner decision.

1. **Major — the mandatory golden regeneration was wrong.** The routing golden uses
   `_SmallFakeModel` (`tests/unit/flows/test_run_forecast_cycle.py:8966`), which inherits
   `FakeStationForecastModel` (`:549`). That fake returns `b"fake_state"`
   (`tests/fakes/fake_models.py:85`). It is a state-producing fixture, not evidence of
   a stateless model. The owner accepted the correction: keep its missing-state `cold_start` golden
   and declare the fake state-keeping, with separate state-free RED fixtures. D6
   now applies only to undeclared models; explicit declarations apply on direct
   calls too. This supersedes upstream D6's golden-regeneration instruction.
   The comment calling the same fake STATELESS in
   `tests/unit/services/test_run_station_forecast.py:2077` is also misleading.
2. **Major — T2 did not require discriminating tests for all paths and the combined
   case.** Pure classifier tests already accept FRESH and None
   (`tests/unit/services/test_input_quality.py:204-218`); they cannot catch incorrect
   model-to-source mapping. T2 now separates stateless RED tests through actual
   services from preservation tests for stateful missing state and combined NULL.
3. **Major — copying an arbitrary policy through FI is not a state implementation.**
   FI `interface/protocol.py:27-34` and `interface/result.py:10-18` have no state
   channel; SAP3's adapter returns no state (`adapters/forecast_interface.py:1109`).
   T1 must reject a conflicting FI state-keeping declaration, classify directly
   adapted instances too, and leave FI's reserved extension unimplemented.
4. **Major — the documentation task omitted live semantics.** The helper signature
   also appears at `docs/spec/types-and-protocols.md:3643`; mandatory caller text is
   at `docs/touchpoint-maps.md:385-395`. Skipping the store for state-free models
   changes when `warm_up_load_failed` is possible (`docs/standards/logging.md:271`).
   WMO has a separate unverified forecaster-notification claim at
   `docs/standards/wmo.md:196`, not only the persistence/API row at `:188`.
5. **Major — the staging subset gate could reject a correct fix.** All forecasts may
   still have an observation, NWP, or forcing deficit; retained pre-cutover rows also
   remain degraded under D3. T5 must compare exact IDs in a bounded post-cutover
   window, not demand a non-total fleet-wide result. The filter itself already
   selects the stored stamp (`store/forecast_store.py:243`).
6. **Major — T4 promised discovery FROM THE DATA without a joinable cutover record.**
   `forecasts.version` is optimistic locking (`docs/architecture-context.md:1844`),
   not the software version. T4 now names the deployment record, actual runtime
   revision, timestamp/cycle boundary and affected model IDs needed to interpret
   retained rows, without adding a schema or rewriting history.
7. **Scope correction — an assembler API/flow rewrite is unnecessary.** The loader
   receives only an ID, but `assemble_station_operational_inputs` already receives
   the model (`services/operational_inputs.py:838-855`), including from the group
   caller (`services/run_group_forecast.py:136-139`). Classify it at that existing
   call site. Keep station provenance per assignment (`run_station_forecast.py:378`),
   never copied from the legacy representative model's metadata.

**Pre-change evidence:** a read-only isolated execution of the unchanged
`WarmUpState`/`load_warm_up_state` definitions on 2026-09-11 produced
`(cold_start, age=None, prior_state=None)` for either stateless route with an empty
store, and also for a stateful model with missing state. A two-hour snapshot returned
`(fresh, 2.0, bytes)`; a thirty-hour snapshot returned `(snapshot, 30.0, bytes)`.
The loader has no policy input. This is a bounded loader probe, **not execution of
the proposed service tests**. Those definitions remain unchanged at the refreshed
2026-09-16 baseline. The stateless assertions must fail before the future
implementation; existing stateful/combined behavior should already pass.

## What was measured

Mac mini, 2026-09-11, the first cycle after Plan 261 T1 deployed (`75cf80cf` / `0.1.901`,
confirmed present in the *running* worker, not merely in the checkout).

| fact | value |
|---|---|
| Forecasts in the 12:00:01Z cycle | **514** |
| `input_quality = degraded` | **514 (100%)** |
| Carrying a `warm_up` cold-start flag | **514** — re-measured; an earlier revision said 512 |
| Also carrying an `observation` staleness flag | 2 |
| `warm_up_source = cold_start` | **514** |
| Rows in `model_states` | **0** |
| Distinct `warm_up_source` in the retained window (2026-09-04 →) | **`cold_start`, and nothing else** |

Not a Plan 261 regression, and not new: every forecast in the retained window, ~1,335/day.

## 🔴 The conflict — FOUR sources, three answers

What should `warm_up_source` be for a model that holds no state? ⚠️ **Round 2 found a FOURTH
source** — the type/Protocol spec, which `CLAUDE.md` calls *authoritative for implementation*. TWO documents say `NULL`, and T3 must correct both or documentation exit gate 3 fails.

| source | answer | where |
|---|---|---|
| **Our type/Protocol spec** (`CLAUDE.md` calls it *authoritative for implementation*) | **`NULL`** — `# NULL for ML models` | `docs/spec/types-and-protocols.md:1791` |
| **Our architecture document** | **`NULL`** — "NULL for ML models" | `docs/architecture-context.md:1845` |
| **The ForecastInterface contract** (co-designed with hydrosolutions) | **`FRESH`** — "a state-free FI model … always runs `WarmUpSource.FRESH` — already legal SAP3 behaviour for stateless models" | `ForecastInterface/docs/model_interface.md:82` |
| **The running code** | **`COLD_START`** | `services/operational_inputs.py:96-113` → `services/input_quality.py:120-127` |

The code's answer degrades every forecast. The other three do not.

### Why they disagree — they are answering different questions

This is not four parties contradicting each other on one question. It is three answers to
three questions — two documents give the same answer — and only one was ever asked
deliberately:

- **`NULL`** was written when warm-up was conceived as a *conceptual-model* concept.
  `architecture-context.md:108-112` describes warm-up entirely in terms of conceptual models
  deriving soil moisture, snow and groundwater, with ML models explicitly excluded ("ML models
  do not produce state; this step is a no-op for them"). `NULL` there means **the axis does
  not apply to this model**.
- **`FRESH`** answers a different question: given SAP3's *existing* three-member vocabulary,
  which member does a state-free model map onto? FI's answer is the member meaning **no
  deficit** — and it explicitly calls that "already legal SAP3 behaviour".
- **`COLD_START`** answers no question at all. It is the unconsidered default: `load_warm_up_state`
  returns `COLD_START` whenever the store is empty, and the store is empty for every model that
  does not produce state. The rule dates to commit `77937c1b` (2026-04-13), implementing
  **Plan 023 Step 2** — the WMO-compliance plan. ⚠️ **Corrected round 2:** an earlier revision
  said this predates the other statements. It does not — `NULL for ML models` dates to `f87d6de1`
  (2026-03-11) and came FIRST; the code's answer came LAST, which if anything strengthens the
  case that it was never a considered choice. Plan 023 also explicitly excluded the pipeline
  wiring (`archive/023-degraded-forecast-input-quality.md:738-740`); the empty-store ⇒
  `COLD_START` path arrived separately in `7c642642`.

### 🔑 The resolution — TAKEN by the owner as D1

`NULL` and `FRESH` make the **same operational claim**: this forecast carries no warm-up
deficit. They differ only on whether the axis is "not applicable" or "applicable and nominal".
So the conflict is resolvable without declaring anyone wrong. The proposal:

> **`FRESH` = the axis applies to this model and there is no deficit** — including every
> state-free model, FI-routed or native.
> **`NULL` = no individual model warm-up state is represented** — for newly
> produced forecasts this means combined products (`services/forecast_combination.py:535` already does exactly this).
> **`COLD_START` = a model that CAN hold state was expected to have one and does not.**

Adopt FI's `FRESH`, and **amend BOTH documents that say `NULL` in the same change** —
`docs/spec/types-and-protocols.md:1791` and `docs/architecture-context.md:1845` — so the
repository carries one answer instead of three. Three reasons:

1. FI is the cross-organisation contract and `CLAUDE.md:54-69` makes compliance mandatory.
   This is the "our side violates the FI → fix our side" path, not the "file an FI issue" path.
2. Keep field semantics distinct: `input_quality = None` means *unknown/legacy row*
   (`architecture-context.md:110`), whereas combined `warm_up_source = None` means no
   individual model state is represented. NULL is not globally “taken” by either field.
   FRESH for state-free individuals follows FI, not a technical prohibition on NULL.
3. Project type rules prefer an explicit enum member over an overloaded `None`.

⚠️ **The amendment is the deliberate part.** Correcting a trusted internal document to match an
external contract must be a recorded decision, not a quiet edit. If the owner prefers `NULL`,
that is a divergence from FI and must go upstream as an FI issue per `CLAUDE.md`, not be taken
silently.

⛔ **This is NOT an FI gap.** FI's state-free design is deliberate and documented, and FI has
already specified the SAP3 mapping. Filing an FI issue here would be the wrong one of
`CLAUDE.md`'s two paths.

## What already exists, so nobody re-scopes it

- **Warm-up state persistence is fully built and wired.** `protocols/stores.py:563`,
  `services/operational_inputs.py:96-113`, and five `store_state` call sites in
  `flows/run_forecast_cycle.py` (`:2906`, `:2939`, `:3194`, `:3273`, `:3561`). The only missing
  piece is a model that returns non-`None` state. Do not plan a build here.
- **The "not applicable" representation exists**: `WarmUpSource | None`, with
  `input_quality.py:120` suppressing the whole block on `None`.
- **`WarmUpSource` has exactly three members** today — `FRESH`, `SNAPSHOT`, `COLD_START`
  (`types/enums.py:23-26`).

## Why this is worth fixing

**A flag that is always on is not a degraded signal, it is no signal.** The two forecasts in
that cycle with a REAL problem — 85.4 h stale observations — are indistinguishable from the
512 carrying ONLY a structural artefact (all 514 carry the cold-start flag; 512 carry nothing
else).

**The API consumer loses discrimination in the measured window.** Its
`degraded_only=true` filter (`api/routes/api_stations.py:274`,
`store/forecast_store.py:243`) correctly selects the stored DEGRADED stamp; the
producer has supplied a spurious stamp for state-free models.
⚠️ **No dashboard indicator exists** (`architecture-context.md:110`) — an earlier revision of
this plan claimed dashboard harm that does not exist.

**It does not invalidate the existing WMO persistence/API evidence.** `docs/standards/wmo.md:188` scopes its evidence to
"persistence and authenticated API serialisation ONLY" — it never rested on the cold-start
rule. Plan 023 owns that rule and should be cited when it changes.

## Consumers — who reads this field

| consumer | reads | effect of the change |
|---|---|---|
| `api/routes/api_stations.py:274` + `store/forecast_store.py:243` | `degraded_only` filter | Can discriminate again once state-free producers stop emitting spurious degradation; other deficits and historical rows may still make a bounded result total. |
| `services/forecast_combination.py:348-375` | contributor `input_quality` flags, inherited onto combined products | Combined products stop inheriting a spurious `warm_up` flag; their own `warm_up_source` stays `None` (`:535`) |
| `docs/standards/wmo.md:188` | compliance evidence row | Unaffected — scope is persistence + serialisation |
| Dashboard | — | **No indicator exists.** No effect. |
| Tests | Loader, station/group services, adapter/registry, combination, API/store, and routing golden | Audit the model behavior behind each assertion. Literal occurrence counts are not an edit inventory; some cold-start expectations must remain. |

## The decisions — D1–D6 CLOSED, including this pass's D6 clarification

**D1 — ✅ CLOSED: a state-free model records `FRESH`.** The owner adopted the FI mapping. The
resolution proposed above is therefore the resolution: `FRESH` = the axis applies and there is
no deficit; `NULL` = no individual model warm-up state represented (combined products for new output, already the case at
`services/forecast_combination.py:535`); `COLD_START` = a model that CAN hold state was expected
to have one and does not. **BOTH documents saying `NULL` lose and must be corrected in the same
change** — `docs/spec/types-and-protocols.md:1791` and `docs/architecture-context.md:1845`
(⚠️ round 3: an earlier revision named only the architecture document here, while the conflict
table, T3 and gate 4 all named two — the fold had been applied everywhere except the decision
itself) — that amendment is T3, and it is the deliberate part of this decision.
⚖️ Not an FI divergence, so nothing goes upstream.

**D2 — ✅ CLOSED: each model declares whether it keeps state.** Not adapter identity (three
NATIVE models — `linear_regression_daily.py:52`, `climatology_fallback.py:38`,
`persistence_fallback.py:36` — never touch the FI adapter and are equally state-free, so route
derivation would leave the saturation in place). Not an empty state table or a single `None`
return (a stateful model that LOST its state looks identical on its first cycle). An explicit
capability, following the optional-capability pattern FI documents
(`forecast_interface/interface/protocol.py:55-58`).
⚠️ **Corrected round 2:** an earlier revision said "exactly as SAP3 already detects
`RetrainableModel`". SAP3 does NOT — `RetrainableModel` appears nowhere in `src/`, and
`docs/requirements/03-forecast-interface-adherence.md:51` records that `isinstance` routing as
**designed, unbuilt**. The decision stands; the precedent it cited did not exist.

**D3 — ✅ CLOSED, AGAINST the recommendation: leave the history alone and record the period.**
All three advisors recommended selective reclassification; the owner chose the conservative
option. No historical row is re-stamped. T4 records which interval carried the old meaning and
from which version the new one applies.
⚠️ **Accepted consequence, stated plainly:** any metric spanning the cutover compares two
different meanings of the same stamp. Anyone computing a degraded-rate trend across it will get
a discontinuity that is an artefact, not a change in forecast quality. T4 must make that
discoverable through a deployment note linked from the data specification, not only
from this plan. It must not imply a per-row software version that does not exist.
📎 The outstanding "do those 514 include combined products?" measurement no longer blocks a
decision; it is now only needed to state the period's extent accurately in T4.

**D5 — ✅ CLOSED 2026-09-11: silence is legal, an UNINTERPRETABLE declaration is fatal.**
A model that declares nothing is treated as **state-free**. A model that declares something the
system cannot interpret **raises** and stops the load. This follows the working precedent in the
same trio T1 piggybacks on — `declared_static_naming` (`services/caravan_statics.py:161-186`)
defaults on absence and raises only on a malformed declaration.
⚖️ The owner rejected BOTH extremes, and the reasons are worth keeping: a mandatory declaration
follows the `model_tier` path where `discover_models` **re-raises** `ConfigurationError`
(`services/model_registry.py:122-124`), so one forgotten model would darken the ENTIRE registry
and stop forecasting outright; pure defaulting would silently misclassify a genuinely stateful
model and lose the alarm this plan exists to preserve. Silence-legal/gibberish-fatal keeps the
alarm for malformed declarations without the catastrophic failure mode.

**D6 — CLOSED, clarified by the owner in this pass (2026-09-11): the state-free
default applies only to UNDECLARED models, including direct calls.** An explicit
KEEPS_STATE declaration is honored even when the model never passes through
`discover_models`. Use the same resolver for discovered and directly injected
models; registration history must not determine state semantics.

**The stateful golden is preserved.** `FakeStationForecastModel.predict` returns
`b"fake_state"` (`tests/fakes/fake_models.py:85`), and the routing golden's
`_SmallFakeModel` inherits it (`tests/unit/flows/test_run_forecast_cycle.py:549`).
Declare this fake KEEPS_STATE and retain its COLD_START/DEGRADED golden on the empty
store. Use genuinely state-free variants returning None for the new RED tests.
This owner clarification supersedes upstream D6's instruction to regenerate the
golden solely because its model bypasses discovery.

**D5 limitation, stated plainly:** defaulting an absent declaration to STATE_FREE
cannot detect a genuinely stateful model that omits its declaration. Rejecting
malformed values does not solve that absence case. The owner-selected default
stands; models that keep state must declare it to retain the missing-state alarm.
No inference from returned bytes or a new enforcement subsystem is added here.

**D4 — ✅ CLOSED: this ships BEFORE the deep-learning pilot.** The pilot does not depend on it,
but whoever watches that pilot would otherwise be reading a health signal stuck on for every
forecast and could not see the pilot misbehaving. ⚠️ Sequencing only — this plan does not block
the pilot's own preparatory work, which is owned by another session and must not be disturbed.
⚠️ **This sequencing is OWNER-HELD and deliberately NOT encoded in the graph.** `blocks:` stays
empty and Plan 262 stays in `related:`, because 271 does not block the pilot technically — it
is a judgement about what should land first. Recorded here so it is not mistaken for an
unenforced dependency (round 2 finding). Plan 262's preparatory T1/T2/T3a work has
now landed in `925ec599` (#277); that does not change this owner-held sequencing.
This repository refresh does not establish whether the live pilot has been activated.

## Tasks

These are proposed implementation tasks, blocked by DRAFT status. One agent
owns the pass; the graph records ordering, not permission to delegate.

### T1 — declare and resolve each model's state policy

**Outcome.** One typed state policy reaches operational callers for native and
FI-adapted models, including instances passed directly without `discover_models`.

**In.** A two-member enum (e.g. `WarmUpStatePolicy.STATE_FREE` / `KEEPS_STATE`) and
one resolver using D5: an absent attribute defaults to STATE_FREE; an explicitly
present uninterpretable value (including None, bool or an unparsed string) raises
ConfigurationError. Use an absence sentinel and validate enum values. No boolean and no protocol named
`StatefulModel`. Never infer statefulness from `prior_state` in the signature,
artifact scope, an empty store, or one prediction returning None. Native base
`predict` already accepts prior state (`protocols/forecast_model.py:36`).

For native models, resolve the SAP3 registration mapping first, then the optional
class declaration, then D5's absence default. Validate supplied values; do not turn
malformed input into absence. Use the same resolver for directly injected models
(D6), rather than a discovery-history flag.
For FI models, expose STATE_FREE at `ForecastInterfaceAdapter` construction, so
`adapt_if_fi` and direct construction work without discovery. Registry propagation
(`services/model_registry.py:61-88`) must preserve the resolved policy and reject a
contradictory KEEPS_STATE declaration/configuration on an FI model. Do not copy an
arbitrary raw attribute over the adapter's FI contract fact. Test both construction
and discovery. This does not derive ALL models from adapter identity: native models
still declare their capability (D2).

Files: `types/enums.py`, `types/ids.py` (small registration mapping),
`services/model_registry.py`, `adapters/forecast_interface.py`, native
model declarations, affected fakes/tests, and the enum/declaration sections of
`docs/spec/types-and-protocols.md`. A small resolver may live in an existing suitable
module; no registry framework, persisted policy column, or new configuration
subsystem. **Do not add a required member to `StationForecastModel` or
`GroupForecastModel`.** D5 makes absence legal, while the runtime protocol checks in
`services/model_onboarding.py:547,577,690,697` would reject an absent data member.
Keep `protocols/forecast_model.py` read-only and document the optional declaration
without adding it to the required protocol examples (preserves the upstream r4 fix).
All eight entry points in `pyproject.toml:174-185` must resolve correctly:
three native classes, three FI regressions in `models/nwp_regression.py`, and
`CmalPoolPT` / `CmalSmall` in `models/aquacast/_shim.py:577,589` (both SAP3-owned).
Both shims use the same FI state-free boundary; the new `cmal_small` entry point
must be covered too. Preserve its separate tier/alert-eligibility declarations
(`:602-603`). No external-package edits are necessary. FI-derived declarations
need not be repeated on every class.

**Out.** FI package/signature changes; implementation or speculative precedence for
FI's reserved `StatefulModel`. If an actual FI state channel is needed later,
co-design it upstream before use. Do not pass bytes through an adapter that cannot
forward them. Do not alter forecast failure/result handling.

**Pre-change.** No policy declaration/resolver exists. The behavioral RED evidence
is T2; a missing-member/import failure alone is not proof of the forecast bug.

**Verification.** Extend `tests/unit/services/test_model_registry.py` and
`tests/unit/adapters/test_forecast_interface_adapter.py` for native state-free/state-keeping,
D5 absence/malformed behavior, FI construction/discovery, and conflicting FI policy
rejection with meaningful exception type/message. Check all installed entry points,
including both real Aquacast shims in `tests/unit/models/test_aquacast_shim.py`.
That module skips when the aquacast extra is absent (`:31`); record that optional
coverage gap explicitly and use an FI group fake for mandatory route coverage.
Run the registry and adapter modules, plus the shim module in an environment with
the extra when available. Policy/discovery checks need no model weights or pilot
deployment.

### T2 — map policy to provenance and protect all three cases

**Outcome.** State-free individual forecasts use FRESH; genuine missing state stays
COLD_START; combined products retain NULL without losing contributor deficits.

**In.** Add the typed policy to `load_warm_up_state` and pass the resolved policy at
both existing call sites: `_run_single_model` (`run_station_forecast.py:378`) and
`assemble_station_operational_inputs` (`operational_inputs.py:1132`). The latter
already receives the correct group/representative model; no new flow-layer argument
is needed. Station output must keep its own assignment's policy, source, and age.
Group output must carry the real assembler's metadata through
`run_group_forecast.py:315,359`; those consumers need no edit if already correct.

| Case | Returned provenance | Required behavior |
|---|---|---|
| STATE_FREE, native or FI | `prior_state=None`, `FRESH`, age `None` | Bypass the state store and state-age clock, even if obsolete rows exist. Never pass obsolete bytes to the model; no WARM_UP flag. |
| KEEPS_STATE, store has no row | `prior_state=None`, `COLD_START`, age `None` | Retain the WARM_UP/DEGRADED flag. |
| KEEPS_STATE, store has row | Existing bytes and actual age; FRESH below 24 h, SNAPSHOT at/above 24 h | Preserve the existing threshold, clock behavior, snapshot flags, and store-error containment. |
| Combined product | `warm_up_source=None`, age `None` | Preserve contributor input-quality flags, including real WARM_UP deficits. Never replace inherited quality with FULL just because its own source is NULL. |

The existing `services/input_quality.py:120-127` already implements the correct
FRESH/NULL/COLD_START distinction: **read-only**. `forecast_combination.py:348-375,535`
already implements contributor inheritance and combined NULL: **read-only** unless
a new discriminating test proves a defect within this plan. Keep nullable storage
and API round-tripping; do not rewrite legacy NULL rows or make them FRESH on read.
FRESH describes only the warm-up axis, not whether the whole input bundle is FULL.

**Pre-change / verification matrix.** Use controlled clocks/RNGs and valid forecast
inputs. Assert actual output provenance and category/level, never just the overall
count of flags or a spy asserting that a helper was called.

| Test | Expected before the fix | Protection |
|---|---|---|
| Native STATE_FREE through station service with empty store | RED: current source is COLD_START, with a WARM_UP flag | New source is FRESH, age None, no WARM_UP flag; complete otherwise-clean inputs are FULL. |
| FI STATE_FREE through real adaptation and station service | RED for the same reason | Same semantics as native, including direct construction and discovery coverage from T1. |
| STATE_FREE group through real assembly and group forecast service | RED: current metadata loads/classifies nonexistent or obsolete state | Every emitted individual group forecast is FRESH, age None (not NULL). |
| STATE_FREE with a store that would return obsolete bytes or raise if read | RED: current loader reads it | State store is irrelevant to a state-free run; no false snapshot/failure. |
| Native KEEPS_STATE with missing state, deterministic dispatch | Already GREEN; preservation test | Forecast remains COLD_START + WARM_UP/DEGRADED. Use a state-producing fake and successful output; ensemble rejection is not evidence of this case. |
| KEEPS_STATE recent/old snapshot and read failure | Already GREEN; preserve existing assertions | Bytes, source/age, 24 h boundary, and assignment-local error behavior remain intact. |
| Mixed native assignments: STATE_FREE plus KEEPS_STATE/missing | RED on the stateless member | Per-assignment policy cannot leak from the representative/primary model to another model. |
| Combined product built from actual service results | RED for stateless-only contributors' inherited spurious flag; own NULL already GREEN | Own source/age stay NULL; stateless-only clean contributors yield no WARM_UP flag, a real stateful cold-start contributor remains degraded. |
| Stateless forecast with stale observations (fresh NWP, complete forcing) | WARM_UP assertion RED; observation preservation already GREEN | Still carries OBSERVATION/DEGRADED after its WARM_UP artefact disappears. |

These tests protect the three outcomes **without falsely requiring preservation
tests to fail today**. Pure `assess_input_quality(FRESH/None)` tests alone are already
GREEN and insufficient. No tests written or application code modified during this
planning pass; after T1 supplies the declaration/test scaffolding, record the service tests' actual
assertion failures against the unchanged loader before T2 changes its behavior,
then run them after the fix. Do not count import/setup failures as RED.

**Existing-test disposition (owner-confirmed D6 clarification).** `FakeStationForecastModel` returns state bytes
(`tests/fakes/fake_models.py:85`), as does the multi-target station fake (`:154`).
Declare those KEEPS_STATE and keep their missing/snapshot/store-failure expectations
under the owner's D6 clarification.
Add small state-free variants returning None. `FakeGroupForecastModel` returns None
(`:222`), so stateful provenance fixtures in the group tests need separate explicit
fixtures or replacement stateless assertions; do not bulk-replace COLD_START tokens.
The Plan 151 routing golden uses the state-producing `_SmallFakeModel`
(`tests/unit/flows/test_run_forecast_cycle.py:549,8966`): retain the canonical JSON
and cold-start flag, as the owner confirmed. Retain the existing forcing flags,
state-write expectations, and numerical output. Add a separate genuinely stateless
case where needed; do not regenerate the stateful golden.

Run the focused modules: `tests/unit/services/test_operational_inputs.py`,
`test_run_station_forecast.py`, `test_run_group_forecast.py`, `test_input_quality.py`,
`test_forecast_combination.py`, plus
`tests/unit/flows/test_run_forecast_cycle.py::TestT8bPerTrackRouting` and the affected
warm-up scenarios in `tests/integration/test_e2e_pipeline.py`. Retain API/store checks
in `tests/unit/api/test_api_stations.py::TestListForecastsInputQuality`,
`tests/unit/api/test_api_forecasts.py`, and `tests/integration/store/test_forecast_store.py`.
Run via `uv run pytest <paths/nodes>`; DB-backed checks need the repository test DB.

### T3 — align architecture, contract, logging, and touchpoint documentation

**Outcome.** Current semantic documentation agrees with D1 and the implemented D5
rule. Historical plans remain historical evidence, not current specification.

**In.** Update the following in the same implementation patch:

- `docs/spec/types-and-protocols.md:1791-1792`: FRESH for state-free individuals,
  NULL when no individual model warm-up state is represented (combined products);
  age NULL when no snapshot was supplied. Update the helper signature and ownership
  note at `:3643-3673`, and T1's declaration contract.
- `docs/architecture-context.md:108-109,112`: describe state capability rather than
  equating all stateful models with conceptual models; explicitly distinguish FI
  lookback spin-up from persisted snapshots. Keep `:110`'s input-quality
  `None = unknown/legacy` meaning and its dashboard limitation. Replace `:1845` with
  the source semantics above and `:1846` with “age of the supplied snapshot; NULL
  when no snapshot is supplied (state-free, cold-start, combined).” Audit the
  adjacent model/state prose at `:1542,1681`; do not build its deferred warm-up
  strategy, group state-input channel, or alter age thresholds in this plan.
- `docs/touchpoint-maps.md:385-395`: actual helper signature, policy resolution,
  state-free read bypass, and the per-assignment versus GROUP metadata paths.
- `docs/standards/logging.md:271`: `warm_up_load_failed` only applies when a
  state-keeping assignment attempts a read. Keep event name, fields, severity and
  containment; add no event. The `forecast.input_quality_assessed` event at `:270`
  remains specified but unimplemented.
- `docs/requirements/03-forecast-interface-adherence.md`: a dated, narrow amendment
  recording the pinned FI FRESH mapping and why no FI change is needed. Its older
  “adapter unbuilt” analysis is historical; do not rewrite the entire gap analysis.

**Bounded dispositions (reviewed, no change expected):**
`types/forecast.py:59`, `store/forecast_store.py:77,432,448`,
`api/routes/api_forecasts.py:83`, `api/schemas.py:123`, `db/metadata.py:1134`,
`alembic/versions/0001_v0_schema.py:477`, `docs/spec/database-schema.md:284,856`,
and `docs/conventions.md:415` already support the three values plus NULL; no
migration, serializer normalization, enum-value change or historical migration edit.
`docs/v0-scope.md` needs no phase/scope change. Read
`docs/standards/orchestration.md`; scheduling/fan-out are unchanged.
`docs/standards/wmo.md:188` remains persistence/API evidence only, and `:196`'s
forecaster-notification claim stays unverified. No WMO compliance promotion and no
dashboard/log-event implementation in Plan 271. Link the cutover note from the
current metadata documentation under T4.

**Pre-change.** N/A for documentation behavior. Concrete stale text is cited above.
**Verification.** Search current architecture/spec for `NULL for ML`, the old helper
signature and `NULL when fresh or ML`; none may remain as current conventions.
Confirm NULL for combined products, nullable legacy readback, and input-quality
unknown/legacy semantics are retained. Check all named dispositions against the
final patch; avoid a repository-wide historical-plan rewrite.

### T4 — record the historical meaning and actual deployment cutover (D3)

**Outcome.** A data consumer can interpret retained forecast rows using a named,
joinable deployment note without any row being restamped.

**In.** Add a “Plan 271 input-quality cutover” note to
`docs/deployment/mac-mini-staging.md`, linked from the current warm-up metadata
specification. Prepare the note after T2/T3 with the code version; complete it during
T5 using the running worker revision/image, actual deployment UTC time, last old
cycle and first verified new `issued_at`, affected model IDs, and retained interval
observed. Report individual versus combined counts separately; do not infer that
all 514 measured rows are individual forecasts. Mark unknown earlier boundaries
unknown rather than inventing the start of the defect.

Verify the image/revision of the worker that actually executes forecasts. The
current Compose topology uses `sapphire-flow-aquacast:${VERSION}` for
`prefect-worker` (`docker-compose.yml:84-99`), distinct from the base image used
by init/API. An init/API revision alone is not worker-cutover evidence. Follow
`docs/standards/cicd.md` for that topology; no image/configuration change is added
to this plan.

`forecasts.version` is an optimistic-lock counter, not the software version. Relate
rows using `issued_at`, `created_at` and the measured model/cycle boundaries in the
note. If workers overlap, a rollback occurs, or replay/backfill makes timestamps
ambiguous, record that ambiguity and the observed affected IDs/ranges rather than
claiming an exact universal cutoff. This is discoverable **by joining data to the
linked deployment record**, not from a new per-row software-version field.

**Out.** Database schema, data backfill, historical stamp edits, new audit subsystem.
**Pre-change.** N/A: documentation-only; no deployment cutover exists yet.
**Verification.** Inspect the completed note for actual runtime evidence and links;
a reader must be able to bound the affected rows, or see which boundary remains
unknown. Read `docs/standards/cicd.md` before later deployment work. No deployment
or host access is authorized by this planning pass.

### T5 — verify the deployed signal in a bounded cycle

**Outcome.** One post-cutover cycle shows the correct category semantics; the API
returns exactly the stored degraded set for the same bounded query.

**In.** After an owner-authorized deployment, inspect source, age, individual versus
combined identity, and flags. STATE_FREE individuals must have FRESH/NULL age and
no WARM_UP flag. Combined source/age must remain NULL while contributor deficits
remain inherited. Preserve any genuine observation/NWP/forcing deficit.

Compare the complete paginated `degraded_only=true` response against IDs with
`input_quality=degraded` in the same station/time window and visibility scope.
Empty or total results can both be correct; a strict subset is expected only when
the window demonstrably includes both degraded and non-degraded forecasts. Exclude
retained pre-cutover rows from this check. Never change Plan 270 to make this gate pass.
If staging has no stateful model or no stale-observation example, use T2's
corresponding deterministic tests as evidence and report that limit explicitly.
Complete T4's actual cutover note here.

**Pre-change.** The historical measured window reports saturated cold-start flags;
re-measure deployment facts rather than treating that report as fresh evidence.
**Verification.** Record cycle/time bounds, model and product kinds, category
counts and exact filtered-ID equality. No unconditional fleet percentage gate.

## Dependency graph

```json
{
  "plan": 271,
  "nodes": [
    {"id": "T1", "phase": 1, "depends_on": [], "note": "blocked on READY"},
    {"id": "T2", "phase": 2, "depends_on": ["T1"]},
    {"id": "T3", "phase": 3, "depends_on": ["T1", "T2"]},
    {"id": "T4", "phase": 4, "depends_on": ["T2", "T3"], "note": "prepare cutover note; actual runtime evidence completed with T5"},
    {"id": "T5", "phase": 5, "depends_on": ["T2", "T3", "T4"], "note": "owner-authorized deployment required"}
  ]
}
```

## Exit gates

1. T1 declaration/error behavior matches D5 and the owner-clarified D6; native and FI/direct
   adapter construction/discovery agree with the pinned contract. No contradictory
   FI state policy is accepted and no future FI state channel is invented.
2. T2 records genuine stateless RED assertions before its behavioral edits and GREEN
   afterward; stateful COLD_START, snapshot behavior and combined NULL/inherited
   deficits remain protected. State-producing fakes declare KEEPS_STATE and the routing golden remains intact.
3. T3 updates all affected documentation and records the bounded no-change
   dispositions, including logging, WMO and touchpoints. No new forecast logger,
   dashboard indicator, compliance claim, schema or model-state persistence system.
4. T4/T5 contain actual deployed-cutover evidence; no historical row is restamped.
   T5 uses category assertions and exact bounded filter equality, with missing
   staging scenarios explicitly covered by deterministic tests.
5. Observation, NWP and forcing rules, model numerics, FI signatures/results and
   Plan 270 are unchanged. Review the diff for accidental fixture/numeric changes.
6. Focused tests and task checks pass. Before merge, after the final code change,
   the full `uv run pytest` suite must pass locally or in CI, with repository lint,
   formatting and type gates satisfied (`docs/workflow.md` § Task Exit Gate).

**Review handoff.** This DRAFT is ready to be submitted for independent Claude and
Codex review, not ready for implementation. Neither reviewer approves this author's
output or changes status. Under `docs/workflow.md` § High-risk work, the FI boundary
and user-visible metadata change also require an additional owner-commissioned
relevant independent review before READY and again before the implementation PR;
the earlier expert consultation does not review this material revision. D5 and D6
are closed; no owner design decision remains unresolved. Do not launch reviewers from this pass.

## Related, explicitly NOT in scope

- **Plan 270** — forcing-gap detection. Measured the same saturation and scoped it out as
  unowned (`270:139-141`). 271 is the owner it was waiting for.
- **Plan 023** — introduced the rule (commit `77937c1b`). Read its rationale before overriding
  it. ⚠️ An earlier revision misattributed the rule to Plan 239, which added the *forcing* flags
  to an already-existing gate.
- **Plan 262** — the deep-learning pilot. Its preparatory T1/T2/T3a work landed in
  `925ec599` (#277). `cmal_small` is now registered (`pyproject.toml:185`) and its
  30-day-lookback GROUP shim uses the FI state-free boundary
  (`models/aquacast/_shim.py:523-540,589-603`). T1 includes it in the existing model
  inventory. Pilot activation, artifacts, forcing data and deployment remain
  Plan 262's work; no live-pilot status is inferred here.
- **Building warm-up state persistence.** Already built and wired (`protocols/stores.py:563`,
  `services/operational_inputs.py:96-113`, five `store_state` sites in
  `flows/run_forecast_cycle.py`). What is genuinely open — and unowned — is whether any model
  will ever produce state. Not started here.

## Changelog

- **2026-09-11 (initial)** — drafted from the first post-deploy cycle of Plan 261 T1.
- **2026-09-11 (r1 folds)** — two independent reviews, both NOT READY, converging on the same
  blocker: FI already specifies the mapping. An expert consultation surfaced a third conflicting
  source. Subject changed from "an open design space" to "a conflict and its resolution".
- **2026-09-11 (owner decisions)** — D1–D4 closed. D3 decided AGAINST the unanimous advice of
  three advisors; recorded as the owner's call with its consequence stated. Tasks written.
- **2026-09-11 (r2 folds)** — two further independent reviews: 3 blockers, 9 majors, 8 minors
  between them, all verified before folding. ⭐ **The mechanism was wrong twice over**: an
  `isinstance` capability would have inspected the FI ADAPTER rather than the model (the adapter
  forwards nothing, which `model_registry.py` already exists to work around), and keying it on
  the state signature would have matched EVERY model because `prior_state` is already on the base
  protocol. A **fourth** conflicting source was found, ranked above the one already named. T2's
  edit surface gained a signature change, two call sites and the entire GROUP route; its test
  surface gained 6 references and a golden fixture. T4 was unbuildable in phase 1. Three
  citations were wrong, including one that would have produced a locking test passing for the
  wrong reason. The count of models was "five" (observed) where it should be seven (registered).
  ⚖️ The FI-adherence judgement in T1 was independently assessed as SOUND.
- **2026-09-11 (r3 folds)** — two further reviews: 3 blockers, 8 majors, 7 minors between them.
  ⭐ The round-2 fold had been applied at the conflict table, T3 and gate 4 but **NOT at D1
  itself**, which still named one document — the exact fix-the-primary-site failure this project
  has on record. ⭐ **`warm_up_state_age_hours` was named for the first time in any round**: after
  T2 the database carries two meanings of `FRESH`, separable only by that field. ⭐ T1 had copied
  only HALF the classification pattern — `model_tier`/`alert_eligibility` are config-FIRST, and
  that route is how a model whose class we do not own gets classified, so attribute-only
  propagation would leave partner models unclassifiable. T1 also listed no documentation despite
  editing a spec file T3 edits, while the graph ran them concurrently; T3 now depends on T1.
  D5 was found to gate T2 as well as T1. The ensemble-guard citation was wrong for the THIRD
  round running (`:434` → `:536` → actually `:425`/`:527`, and both are inert for the branch under
  test). Gate 3 could legitimately block on an empty set. The "ranks ABOVE" claim was false.
  ⚖️ **The count dispute settled by direct measurement:** the reviewers disagreed 17 vs 23; both
  were right — 17 uppercase `COLD_START` + 6 lowercase `cold_start`, and the lowercase form is the
  persisted DB value, so 23 is the impact inventory.
- **2026-09-11 (r4 folds)** — two reviews: 1 blocker, 3 majors, 6 minors. ⭐ **A verified design
  trap**: T1 had said to add the policy to `protocols/forecast_model.py`. Both forecast Protocols
  are `@runtime_checkable` with data members and are isinstance-gated at four production sites in
  `services/model_onboarding.py`; proven by execution on 3.12.10, `isinstance` returns False when a
  data member is absent — so the member would have made ONBOARDING REJECT exactly the model D5
  declares legal. Now read by `getattr` with a sentinel, and no Protocol is touched. ⚠️ The
  ensemble-guard citation was wrong for the FOURTH round running, and for the second time because a
  correction landed at the exit gate and not at the task it gates; T2's verification is now gate 2's
  text verbatim. Two live copies of the superseded D5-open framing were deleted — a parenthetical
  "superseded, kept for one revision" fence did NOT neutralise a bold imperative four bullets later,
  exactly as this repo's own record on correction notes says. `types/ids.py` added to T1. The
  circular "see T3"/"see T2" replacement text for `architecture-context.md:1846` is now written out.
  Four line references re-measured.
- **2026-09-11 (owner closes D5 + D6)** — silence is legal and an uninterpretable declaration is
  fatal, following `declared_static_naming` rather than `_declared_model_tier`; and a model that
  never passes through `discover_models` is state-free, which regenerates the golden fixture
  rather than erroring on it. Both extremes were rejected deliberately. No decision is now open.
- **2026-09-11 (fresh Codex planning/refinement pass, `3889c816` → `f2dc569c`)** — verified
  the pinned FI contract; corrected the state-producing golden/fake assumption;
  replaced literal counts with behavioral test dispositions; bounded policy
  propagation and FI conflict handling; specified store bypass, all three output
  cases and per-assignment isolation; added logging/touchpoint/spec documentation
  and WMO dispositions; replaced the staging percentage gate with bounded ID
  equality; made the historical cutover record concrete. Reconciled concurrent r4
  and D5/D6 owner decisions from origin/main; retained the no-required-Protocol-member
  correction. The owner then clarified D6: only undeclared models default to
  STATE_FREE; explicit KEEPS_STATE applies on direct calls; preserve the stateful
  golden. No owner design decision remains open. This material revision awaits independent review; no implementation, READY,
  stage, commit, push or PR in this pass. Earlier changelog counts/findings describe
  those historical revisions and are not the current task specification.
- **2026-09-16 (refresh against `origin/main` at `925ec599`)** — fast-forwarded the
  dedicated branch while preserving the uncommitted DRAFT. Warm-up paths, the FI
  pin, state-producing fake and canonical golden are unchanged upstream. Added
  the newly registered `CmalSmall` to T1's inventory and optional real-shim checks;
  refreshed shifted spec references and required evidence from the actual
  Aquacast forecast-worker image for T4. D6 still applies only to undeclared
  models; explicit KEEPS_STATE and the stateful golden remain protected. No new
  owner decision, implementation, deployment, or independent review occurred.
