---
status: READY
created: 2026-08-10
plan: 151
title: Forecast-cycle redesign Phase 3 — ForcingTrackKey projection + per-track resolution + per-assignment assembly (one atomic phase)
scope: Build-sequence item 3 of the forecast-cycle redesign. REALIZE the locked contracts in docs/spec/types-and-protocols.md — do not redesign them. Adds per-requirement, per-time-step ForcingTrackKey projection with per-feature horizons derived at the FI boundary BEFORE the max-collapse; the pure deterministic resolve_tracks() reducer; a CandidateAwareForecastSource Protocol whose fetch_requirement fetches exactly the nominal cycle (walk-back is a services/ policy, never adapter-side) and whose expected_member_ids makes the completeness member set a per-source derived property (never a literal 51); candidate-local CandidateFetchResult accumulation; per-track resolution to ONE cycle with per-station StationTrackOutcome; per-assignment assembly from the track's stored rows; the runner ModelRunContext-consumption seam with two new additive AssignmentFailureCause members (MISSING_CONTEXT, TRACK_UNAVAILABLE); and fail-loud cross-cycle combination. EXACTLY ONE forcing track per assignment (multi-track is a follow-on). CONTROL-only forecasting stays green via isinstance dispatch keeping legacy adapters and the GROUP path on the existing superset path; legacy superset REMOVAL is Phase 4.
depends_on: [148, 150]
blocks: []
supersedes: []
---

# Plan 151 — Forecast-cycle redesign Phase 3: track projection + per-track resolution + per-assignment assembly

## Status
**READY — Phase 3 of the forecast-cycle redesign** (owner-ratified 2026-08-11: D26 freshness cost accepted, D31 spec
edit approved, D21 global sweep confirmed — **no open forks remained**).
**Staleness-corrected 2026-08-18** against `main` `c81041e` after 142 commits of drift: every `file:line` re-anchored
and every claim re-verified. **Three** corrections change a ratified decision's observable consequence and are filed
under *Open items → Requires owner re-ratification* (D21, D32, and — added by the 2026-08-18 independent sweep —
`D34-atmost-guard-unreachable`). Everything else was pure correction — no decision was reopened.

**OWNER RE-RATIFIED 2026-08-18 — all three settled; status returns to `READY`:**
- **D21** — the corrected failure mode is **ACCEPTED**: a non-conforming FI model is SKIPPED at discovery with
  `log.exception("model_discovery_unsupported_requirement")` while every other model loads. A hard abort would need a
  separate onboarding-time check outside `discover_models()`; that is **not funded** here.
- **D32 — option (b) CHOSEN**: accept **construct-only**. T2's red-first narrows to construction-time assertions; the
  delivery-level assert (`adapters/forecast_interface.py:997-1021`) is **NOT** relaxed. Consequence: D2's
  selected-branch rule stays unexercised end-to-end until Plan 153, and **T6's branch-specific past-variable /
  `max_nan` criterion is restated against a SINGLE-branch fixture** (this unblocks the criterion marked BLOCKED at T6).
  Option (a) was rejected: relaxing the assert imports Plan 153 multi-resolution scope into a phase whose non-goals
  exist to exclude it.
- **D34 — option (a) CHOSEN**: **DROP the T7 route-time `AT_MOST` guard.** The axis is unreachable twice over (FI
  pinned at v0.1.19 has no `horizon_semantics`; and `discover_models()` hands the runner a `ForecastInterfaceAdapter`
  that does not expose `input_requirement` at all). **Revisit trigger:** an FI bump to >= v0.1.20 **AND** a
  per-track-eligible model declaring divergent per-variable `min_future_steps`. The per-feature `AT_MOST` limitation
  stays RECORDED in D10a as an accepted cost. Option (b) was rejected as gold-plating a doubly-unreachable path.

**Second independent Codex sweep, 2026-08-18 (after the rulings above).** A correctness/buildability pass found four
further defects, all folded in and none reopening a decision: T1's spec-edit scope omitted two corrections that left
the AUTHORITATIVE spec contradicting D4/D22 (mixed-mode "SPLITS" vs rejected) and D1/D12 (`nwp_source: NwpSource` vs
`str`); T4 was assigned cadence threading it cannot perform (the resolver does not exist until T5, flow wiring is
T8's) — **T4 adds the config field, T5 defines the parameter, T8a threads it** (as `ForcingResolutionPolicy`); four criteria labelled red-first are
already GREEN and are now marked **regression gates**, with the preamble requiring at least one genuinely failing
test per task; and T1's key-equality criterion was unbuildable (no horizon field exists to vary) and is now
structural, with the behavioural half carried by T3. The same sweep independently re-confirmed D10a, the registry
behaviour, D34, the pyright/ruff baselines, and that **no ForecastInterface anticipated-failure violation exists**.

**FIRST SLICE LANDED 2026-08-18 — T1–T4 merged via PR #182 (squash `707237e` on `main`).** Types, FI accessors +
conformance sweep, the projector/`resolve_tracks()` reducer, and the `CandidateAwareForecastSource` contract with the
recap adapter's `fetch_requirement` / `expected_member_ids` and `RecapGatewayConfig.cycle_cadence_hours` are now on
`main`. That slice — T5–T7, with T8 split out — is now **also merged**; see the paragraphs immediately below.

**SECOND SLICE MERGED 2026-08-19 — T5–T7 via PR #192 (squash `351bac3` on `main`).** Track resolution,
per-assignment assembly, and the runner seam are implemented and locked but **dormant**: `flows/run_forecast_cycle.py`
calls **none** of the new T5–T7 entry points. **T1–T7 are therefore all merged** (#182 `707237e`, #192 `351bac3`) and
**nothing merged so far changes production behaviour.**

**THIS DOCUMENT NOW DRIVES THE T8 RUN — the LAST task, and the only one that flips the switch.** T8 is split into
**T8a (dormant)** and **T8b (activation)** per **D36**, after an independent Codex review of the T8 task spec returned
**NO — not ready to build** (one blocker, four majors, two minors, all folded into the task text below). **D35** (the
earlier T5–T7 / T8 split) sits under *Open items* and is the owner's to confirm. **D36** (the T8a/T8b split) is
**RATIFIED** (owner, 2026-08-19, re-confirmed 2026-08-26) and is NOT among them.

Phase 3 of the forecast-cycle redesign (`docs/design/forecast-cycle-redesign.md`, build-sequence item 3).
This is the **one atomic phase** the redesign refuses to split: a per-`(track,station)` cycle has no coherent consumer
while assembly is a single shared frame, so track resolution and per-assignment assembly land together. It drops the
station superset **for the per-track path**, and enforces exact-**member-set** input completeness with
all-members-or-fail survival. Cross-cycle model combination is **fail-loud** here.

**This plan REALIZES locked contracts; it does not redesign them.** The concrete types and the
`CandidateAwareForecastSource` Protocol are authoritative in `docs/spec/types-and-protocols.md`. Where the spec leaves
a field "typed in the build plan", this plan pins it (T1). Where the spec and a genuine need conflict, it is surfaced
in **Open items** — never silently diverged (`CLAUDE.md` § ForecastInterface Adherence).

**Predecessors on `main`:** Plan 148 (Phase 1 — `ModelRunContext` + per-assignment warm-up) and Plan 150 (Phase 2 —
the assignment-level `AssignmentOutcome`/`AssignmentFailureCause` shape + loop-level backstop). Plan 150 landed the
enum explicitly extensible by Phase 3 with no runner/type rework: Phase 3 adds two members and new production sites
only.

**Provenance of this document.** Two `/plan` runs (2026-08-11) escalated by over-expansion (999 → 1316 → 1940 lines)
rather than converging; the second round's residual "blockers" were largely forks the loop had left conditional. This
is a **deliberate reconstruction** to the pre-expansion shape with only independently re-verified findings folded in;
the expanded version is preserved outside the repo for audit. Per `docs/workflow.md`, implementation mechanism belongs
in code and docstrings — this document states **decisions and invariants**, with `file:line` grounding, and lets
implementation own the mechanism.

**Citations** were carried from the reviewed drafts and spot-verified against the worktree; the confirming review pass
re-verifies them before READY.

## Build scope for THIS run — T8a ONLY (2026-08-19)

**T1–T7 ARE MERGED.** T1–T4 landed as **PR #182** (squash `707237e` on `main`); T5–T7 landed as **PR #192** (squash
`351bac3` on `main`). Everything merged so far is **dormant**: `flows/run_forecast_cycle.py` contains **zero**
references to `resolve_candidate`, `commit_track`, `assemble_assignment_inputs` or
`run_all_station_forecasts_per_track` — only tests call them. The `isinstance` dispatch that would route a station to
the per-track path **does not exist yet**.

**This run builds T8a ONLY. It does NOT build T8b.** The T8a/T8b split was **OWNER-RATIFIED 2026-08-19**
(**D36-t8ab-split**); it re-scopes only what T8 already owned — **no new scope is added**. T8b follows in a separate
PR off the merged T8a base.

**Nothing outside T8a may be built, wired, or "prepared" in this run.** Specifically **DO NOT**: add the `isinstance`
dispatch; call any T5–T7 entry point (`resolve_candidate`, `commit_track`, `assemble_assignment_inputs`,
`run_all_station_forecasts_per_track`) from `flows/run_forecast_cycle.py`; re-scope Phase A; or author any flow-level
golden. Those are T8b.

**T8a's defining property is DORMANCY, and it is a gate, not an aspiration.** Every helper T8a adds must have **zero
production call sites** from the flow at the end of this run. The exit gate asserts it: `grep -rnE
"resolve_candidate|commit_track|assemble_assignment_inputs|run_all_station_forecasts_per_track" src/sapphire_flow/flows/`
must return **zero hits**, exactly as it does today. A helper that is wired in "just to prove it works" has failed
this task. Dead-code lints must **not** be silenced to accommodate the new helpers.

**Why dormancy is the whole point here:** T8b is the first task in this plan that changes production behaviour, and
its flow-level goldens are the only protection for the live control-only route. Writing those goldens in the same
diff that activates the dispatch would let an accidental re-baseline pass unnoticed. T8a therefore lands the
machinery, reviewable in isolation; T8b lands the switch.

**The safety property for THIS run is unlike every previous run's, and must be stated plainly.** T1–T7 were safe
because they were unreachable — no production call site existed. **T8b is the first task in this entire plan that
changes production behaviour.** The moment the `isinstance` dispatch lands, a candidate-aware, non-group station stops
taking the legacy superset path and takes the per-track path instead. There is no dormancy left to lean on:

- **The flow-level golden tests are the ONLY protection for the live control-only route.** Not the unit tests for
  T5–T7 (they exercise the services in isolation and cannot see a routing mistake), and not the `-m slow` end-to-end
  test (which does not reach the flow at all — see the correction below).
- **Weakening, skipping, re-baselining or "updating" a golden to make a build pass is a BUILD FAILURE, not a fix.**
  If a golden goes red, the dispatch is wrong until proven otherwise. The baseline for the homogeneous golden must be
  **frozen from the pre-T8 tree** (`main` `351bac3`) and committed as data in T8b's first commit, before any dispatch
  code exists, so a later re-baseline is visible in the diff rather than invisible in a regenerated fixture.

**CORRECTION 2026-08-19 — the `-m slow` end-to-end run is NOT a gate on the dispatch.** Earlier revisions of this
section and of T8 required `uv run pytest tests/integration/test_e2e_pipeline.py -m slow -q` "so the end-to-end
control-only path actually runs", and treated that as protection for the route T8b activates. **That claim is FALSE
and is withdrawn.** `tests/integration/test_e2e_pipeline.py` calls `run_station_forecast` **directly**
(`tests/integration/test_e2e_pipeline.py:653`, imported at `:563`) and contains **no reference to
`run_forecast_cycle_flow` anywhere in the file** — verified 2026-08-19 by grep over the whole `tests/integration/`
tree, which has zero hits for `run_forecast_cycle_flow`. The `-m slow` run therefore exercises the **runner**, not the
**flow**, and cannot detect a mis-routed station, a dropped assignment, or a missing dispatch guard. Measured on
`351bac3` (2026-08-19): it is **one test, 520s (8m40s), passing** — an expensive gate that does not protect what T8
activates, which is precisely why the flow-level goldens below are mandatory rather than optional. It is retained in
T8b's gate as a **runner-level regression gate only** (it proves T7's seam did not break the direct-call entry point),
and it is **explicitly not** the dispatch protection. The dispatch protection is the flow-level goldens in
`tests/unit/flows/test_run_forecast_cycle.py`, which is where `run_forecast_cycle_flow` is actually driven.

**Exit gate for this run:** the per-task gates for **T8a** and **T8b** below, plus the whole-repo gate —
`uv run pytest -q` at **`passed >= 4550`** with **no baseline test regressing**; `pyright` **≤ 432** over
`src/` via `tools/pyright_ratchet.py`; and `uv run ruff check` with **no new findings** beyond the **12 pre-existing
`E501`s** in `alembic/versions/0037_calculated_station_formulas.py` (4) and
`alembic/versions/0038_calculated_station_formula_trigger.py` (8) — note `ruff check src/ tests/`, the scope this
gate actually uses, is fully clean; those 12 only appear under a bare repo-wide `ruff check`.

**Measured baseline for the T8 run (2026-08-19, against merged `main` `351bac3`, before any T8 work):**
**4550 passed / 20 skipped / 15 deselected** (697s); `pyright` over `src/` = **404**, i.e. *below* the ratcheted
baseline of 432 (T5–T7 improved it — the gate is ≤ 432, so 404 passes and must not regress above it);
`ruff check src/ tests/` clean. Do **not** reuse 4284 (measured against `707237e`, before T5–T7's tests landed)
or 4181 (pre-Phase-3).

**Governing rulings for this run** (settled — build to these, do not reopen): **D11** (cross-cycle preflight = fail-loud
before any per-station write), **D12/D30** (per-track routing requires a candidate-aware adapter **and** no group
membership), **D26** (one `resolved_cycle` per track, with the operator-facing freshness note), **D28** (`retries=`
from `RecapGatewayConfig.max_retries`), and Plan 116's freshness contract. **D32 = option (b)** and **D34 = option (a)**
governed T6/T7 and are already built — nothing in T8 revisits them.

**Hold at PR — do not merge.**

## Review scope — staleness confirmation only (2026-08-18)
**This round is a staleness confirmation, not a design review.** The only question a reviewer answers is: *what in this
plan is now wrong because `main` moved 142 commits?* Not whether the design is the right one.

- The design was **owner-ratified 2026-08-11 and has no open forks**, except the three filed under *Open items →
  Requires owner re-ratification* (D21's corrected sweep failure mode, `D32-multibranch-delivery`, and
  `D34-atmost-guard-unreachable`). Those three are **the owner's to settle, not a reviewer's**. Do not propose answers
  to them as findings.
- **D1–D31 are settled, as are D33 and the D10a coverage rule. Do not reopen, re-argue, or "strengthen" them.**
  ⚠️ **STALE PROSE, corrected 2026-08-26: D21, D32 and D34 are NOT open — the owner RATIFIED all three in commit
  `1a02e24` ("Plan 151 READY — owner ratifies D21/D32/D34", 2026-08-18), consistent with lines 22-36 and 162.** This
  paragraph pre-dates that commit. A grounding pass on 2026-08-26 read the stale wording and flagged D34 as
  "contradicting itself and the owner's to settle" — that flag was WRONG and reopened a settled decision; it has been
  removed. The only decision genuinely awaiting confirmation is **D35**. A finding that reduces to preferring a
  different design is out of scope by construction.
- The **atomic 8-task phase shape**, the **phase dependency graph**, and every **Non-goal** are **fixed for this round**.
  *(That round is CLOSED. Since 2026-08-19 the graph carries NINE nodes — T8 split into T8a → T8b under D36 — and the
  scope rules in this section describe the 2026-08-18 staleness round only, not the T8 build run.)*
- **In-scope findings are exactly these five:** (1) a `file:line` citation that is still wrong; (2) a factual claim
  about current `main` that is false; (3) a red-first criterion that would not actually be red against current `main`
  (a false red-first); (4) an exit gate that cannot pass; (5) a collision with work that landed since 2026-08-11.
  Anything else is out of scope.
- **Proportionality rule — growth is a defect signal.** This document is a deliberate reconstruction after two `/plan`
  runs over-expanded it (999 → 1316 → 1940 lines) by re-litigating settled design. It is now ~1035 lines, having
  absorbed four verified correction rounds (staleness re-anchoring, the `plan` loop, and two independent Codex
  sweeps); growth beyond that is a defect signal. A reviewer
  proposing net-new scope, new tasks, or new decisions has mis-scoped the round; so has a round that ends longer than
  it began by more than the corrections require.

## Owner rulings folded in (2026-08-11)
1. **Exact-51 is a source-derived property, never a literal.** 51 is ECMWF-IFS-only (fc member 0 + 50 pf,
   `recap_gateway.py:328-330`). The live Swiss deployment runs **ICON-CH2-EPS = 21 members**
   (`docs/architecture-context.md:1471`; `adapters/meteoswiss_nwp.py:968-972`, `_MIN_ENSEMBLE_MEMBERS` at `:38`).
   `min_operational_ensemble_size` (`config/deployment.py:123`) is an alert/onboarding **output** floor
   (`services/alert_checker.py:184`, `services/model_onboarding.py:492-508`) and is **never** a statement about a
   source's raw member identity, so it cannot serve as the input-completeness rule.
2. **Exactly ONE forcing track per assignment.** Multi-track assignments (multi-product / mixed-mode requirements) are
   a named follow-on, rejected at registration by T2's conformance sweep. Grounding (re-verified 2026-08-18): the only
   `snow` hits under `src/sapphire_flow/models/` are **static** features in aquacast's config
   (`models/aquacast/configs/cmal_pool_pt.yaml:33,34,46`), whose `future_dynamic:` block is `precipitation` +
   `mean_temperature` only (`:19-21`) — so **no model consumes snow as a `future_known` forcing feature**; snow is
   fetched only by the legacy flow-level `_fetch_nwp_task` path. See D22.
3. **Per-feature horizons are RETAINED** (`FeatureFetchHorizons`), but **acceptance is evaluated at the deduped
   track's per-feature `fetch_horizons` MAX**, not per consuming assignment. Collapsing the horizon axis to a
   *scalar* was considered and rejected: per-variable horizons are named inside build-sequence item 3 of the ratified
   redesign, and the FI `max` collapse (`adapters/forecast_interface.py:514-521`) flattens per-variable `future_steps`
   **within one branch, across every variable and product** — "precip 2 steps + temp 10 steps" in a single branch still
   collapses to a scalar 10. *(Grounding corrected 2026-08-18: the ruling had cited the **cross-branch** form, which
   Plan 156 removed (`:475-485`, guard `:489-497`); the within-branch collapse alone carries it — **decision
   unchanged, not reopened**.)* The earlier
   per-consumer-acceptance variant of this ruling was **withdrawn on 2026-08-11** because it is unrepresentable under
   the locked one-cycle-per-track contract (`docs/spec/types-and-protocols.md:3289-3294,3313-3316`); see D26 for the
   reversal and its cost.
4. **Walk-back cadence comes from configuration, not a third Protocol member** — the more proportional choice given
   the plan's own precedent for config-threaded policy inputs. **No such field exists today**, so **T4 adds and
   validates** `RecapGatewayConfig.cycle_cadence_hours` (`config/recap_gateway.py`); **T5** defines the `services/`
   resolver parameter that consumes it, and **T8a** threads it on **all three** construction paths as one
   `ForcingResolutionPolicy` (production, injected client, injected adapter — see T8a; the earlier "both construction
   branches, exactly as `max_cycle_age_hours` is threaded" wording was wrong twice over: there are three paths, and
   `max_cycle_age_hours` is threaded on only ONE of them today, privately onto the adapter). *(Task split corrected 2026-08-18 — the ruling itself, cadence-from-configuration
   rather than a third Protocol member, is unchanged and NOT reopened.)* The earlier
   "the adapter already carries the constant it needs" phrasing was **withdrawn** — the adapter's private constant is
   not a contract, and "configuration" and "adapter constant" cannot both be the source. See D24.

## Problem
The forecast cycle collapses per-feature horizons in **three** independent places, assembles **one** shared frame per
station, and enforces **no** exact ensemble membership.

1. **Per-feature horizon (and mode) is lost — three collapses.**
   - *Cross-assignment superset collapse.* `build_superset_requirements` (`services/operational_inputs.py:325`) unions
     every assigned model's features and takes the **max** step count (`:378-379`), and rejects a mixed
     SINGLE/ENSEMBLE station outright (`:352-359`). The flow builds this superset once per station
     (`flows/run_forecast_cycle.py:2171`). "precip 2 steps + temp 10 steps" becomes a rectangular 10-step demand.
   - *Within-FI-requirement collapse.* The FI adapter irreversibly `max`-collapses per-variable `future_steps` into one
     scalar `forecast_horizon_steps`, **across every variable and product of the selected branch**
     (`adapters/forecast_interface.py:514-521`; the *cross-branch* form of this collapse was removed by Plan 156).
     Fixing only the superset does not fix this — the FI boundary must slice each variable to its own `future_steps`
     for the **selected** branch, in both the NaN-tolerance gate and `InputSeries` construction.
   - *Consumption-side (runner) collapse.* Even with per-feature fetch and per-assignment assembly, `_run_single_model`
     re-derives its forcing decisions from the globally collapsed `ModelDataRequirements`: coverage from the single
     `forecast_horizon_steps` (`services/run_station_forecast.py:173-188`), fan-out mode (`:250-252`), and the whole
     `future_dynamic_features` set to `fan_out_ensemble` (`:295`). The **horizon** axis is the one that actually bites:
     `assess_future_coverage` takes ONE `required_steps: int` and demands EVERY required feature reach it
     (`services/nwp_coverage.py:75-81,128-150`), so "precip 2 steps + temp 10 steps" is rejected
     `INSUFFICIENT_COVERAGE` (`run_station_forecast.py:199`) even after T3/T6 fix the first two collapses. Phase 3
     therefore carries the per-feature contract into the runner — stated as a rule in **D10a** and built in **T7**.

2. **One assembly / one shared cycle per station.** `assemble_station_operational_inputs`
   (`services/operational_inputs.py:385`) reads NWP for one `cycle_time` (`:494`) and produces one frame for every
   assignment. The flow threads one `nwp_cycle_reference_time` (`flows/run_forecast_cycle.py:2070-2092`) into every
   store/provenance call. A heterogeneous station cannot be represented — and this is **real today**, since a control
   station is many assignments run as a fallback chain.

3. **No exact-member-set enforcement.** `assess_future_coverage` (`services/nwp_coverage.py:116-117`) checks only that
   every required feature carries an **identical** member set — a uniform 30-member set passes. Recap makes this
   concrete: `fetch_forecasts` deliberately breaks out of the pf loop at the first unavailable member and returns the
   partial ensemble (`recap_gateway.py:886-906`).

The consequence: a heterogeneous station is today an **unsupported configuration** (hard-fail or silent-collapse).
Phase 3 makes it supported by fetching per deduplicated forcing track, resolving each track to one cycle with
per-station availability, and assembling **per assignment**.

**Scope honesty — which deployment this actually fixes.** Phase 3 migrates **`recap_gateway` only** (D6), so
heterogeneous-station support, exact-member-set completeness, and per-assignment cycles land **only for
recap-backed tracks** — i.e. the future Nepal / ECMWF-IFS deployment. The **currently live Swiss deployment is
MeteoSwiss-fed** (`flows/run_forecast_cycle.py:1699`) and stays on the legacy best-effort superset path, so for it
the three problems above are **unchanged by this phase**. That is a deliberate scope choice (smallest atomic surface
that exercises walk-back and exact membership end-to-end), not an oversight. **Trigger for the MeteoSwiss follow-on**
(mirroring D21's pattern): the first of — Swiss stations needing heterogeneous per-assignment cycles, an ICON
completeness requirement, or Phase 4's removal of the legacy path, whichever comes first. Phase 4 forces it
regardless, since removing the superset path leaves MeteoSwiss with no assembly route.

## What Phase 3 delivers (and deliberately does not)

**Delivers:**
- The locked domain types (T1): `FeatureName`, `FutureSteps` (validated wrapper, not a `NewType` — a `NewType` cannot
  enforce `> 0`), `FeatureFetchHorizons`, `InputFrameHorizon`, `ForcingTrackKey`, `ForcingRequired`,
  `ResolvedTrackRequest`, the `TrackProjection` and `StationTrackOutcome` discriminated unions, `TrackFetchResult`,
  `CandidateFetchResult` + `CandidateFetchStatus` + `StationUnavailableReason`, and the two additive
  `AssignmentFailureCause` members.
- Pure `services/` **track projection** over the explicit join `(assignment, model, station_weather_source)`, deriving
  per-feature horizons via new public FI accessors computed **before** the max-collapse, plus the pure deterministic
  **`resolve_tracks()`** reducer producing one `ResolvedTrackRequest` per distinct key.
- **`CandidateAwareForecastSource`** Protocol (`@runtime_checkable`) with `fetch_requirement(...)` and
  `expected_member_ids(track) -> frozenset[int]`.
- **Per-track resolution in `services/`** to **exactly one cycle per track**: a walk-back policy bounded by
  `max_cycle_age_hours`, fetching each candidate into a fresh `CandidateFetchResult`, validating raw completeness
  **per station** (against the track's `fetch_horizons` max) **before** any persist, committing only the complete
  stations, then persisting, reading back, and mapping per-station availability.
- **Per-assignment assembly** from the track's available records, with the FI per-variable, per-time-step slice.
- The **runner consumption seam** via a discriminated input, plus a per-feature forcing contract on `ReadyContext`
  that the runner actually consumes for coverage, ensemble dispatch, and fan-out (D10a).
- **Fail-loud cross-cycle combination** as a preflight before any per-station write.
- **CONTROL-only stays green** via `isinstance` dispatch; homogeneous control output is pinned by golden tests.

**Does not:**
- **Remove the legacy station-superset path** — Phase 4.
- **Multi-track assignments** (multi-product / mixed-mode requirements) — follow-on; rejected at registration (D22).
- **Group operational ENSEMBLE fan-out**; group **CONTROL** stays on the legacy superset path fed by the Phase A
  `_fetch_nwp_task` (`flows/run_forecast_cycle.py:1970-1982`), read back at `nwp_readback_cycle_time` (`:2559`).
- **Member survival** — v1 is all-members-or-fail.
- **Hindcast/operational parity** — `services/hindcast.py` uses a separate assembler (`:143`, called `:362` and `:576`) with
  `prior_state=None` (`:389`).
- **Richer combined-forecast provenance** for differing cycles, **write-side per-assignment state**, and **concurrent
  track fetch**.

## Design

Each decision realizes a locked contract. **The CONTROL-only invariant holds at every step:** a control station is
many assignments run as a priority fallback chain, so an assignment whose track or cycle is unavailable **advances the
chain** — it never darkens the station.

- **D1 — Domain types in a new `types/forcing_track.py`.** All `@dataclass(frozen=True, kw_only=True, slots=True)`;
  every timestamp is `UtcDatetime`. `ForcingTrackKey` is exactly the locked spec shape — `(nwp_source: str,
  ensemble_mode, time_step, spatial_representation, features: frozenset[FeatureName])` — carrying **no horizon
  values**, so two assignments differing only in horizon length dedup onto one track. `nwp_source` stays the repo's
  existing `str` (`types/station.py:106`); no `NwpSource` type exists (D12). `FeatureName` is created here as a `str`
  `NewType` wrapped at the projection boundary. `OutputHorizon` is **not** created in Phase 3 (D23).
- **D2 — Pure track projection returning a discriminated result.** A `ModelAssignment` (`types/station.py:66-72`)
  carries no NWP source and no spatial binding; those live on `StationWeatherSource` (`types/station.py:103-109`). The
  projector's input is therefore the explicit join `(assignment, model, station_weather_source)`, returning
  `TrackProjection = ForcingRequired(track, assignment_horizons) | NoForcingRequired`.
  - **The SELECTED time-step branch is authoritative.** FI permits a `DynamicInputSpec` containing only `past_known`,
    so a model may have one future-forced branch and one past-only branch, and the existing adapter unions future
    features across all branches (`forecast_interface.py:505-513`). `NoForcingRequired` is therefore decided from **the
    selected branch's own** `future_known` — not from the globally collapsed `future_dynamic_features` — and no scalar
    mode accessor is called for a branch whose `future_known` is empty.
  - **`NoForcingRequired`** covers fallback and skill models with no future forcing (`models/persistence_fallback.py:48`,
    `models/linear_regression_daily.py:57`). These have no track; their context is assembled independently of track
    resolution, so a fallback still runs when forcing tracks fail.
  - **Non-FI models** broadcast their single scalar `forecast_horizon_steps` (`types/model.py:275`) across every
    declared future feature (`:271`).
- **D3 — Public FI pre-collapse accessors (in-scope change to `adapters/forecast_interface.py`).** The pre-collapse
  per-variable `future_steps` and `ensemble_mode` are hidden behind the SAP3↔FI boundary. T2 adds **per-time-step**
  public accessors returning the selected branch's per-feature horizons and modes **without** the cross-branch max/OR
  collapse. The single sanctioned SAP3↔FI boundary stays the only reader of `InputRequirement`.
- **D4 — Enforced FI subset, rejected at registration.** Phase 3 supports and **enforces at the FI boundary**: single
  spatial representation (already enforced, `forecast_interface.py:537-541`); **exactly one non-empty `future_known`
  product per selected time-step/spatial branch**; and **one `ensemble_mode` per branch**. A violating requirement
  raises **`UnsupportedModelRequirementError`** (`exceptions.py:111`) at construction, so Flow 12 onboarding surfaces it
  before a cycle runs — a documented, enforced narrowing, never a silent one (`CLAUDE.md` § FI Adherence).
  - **Exception type corrected 2026-08-18 (was `ConfigurationError`).** `discover_models()` **skips** an entry point
    raising `UnsupportedModelRequirementError` but **re-raises** `ConfigurationError`, which would darken discovery for
    **every** model (`services/model_registry.py:116-121`). Plan 156 chose this deliberately (reasoning at
    `adapters/forecast_interface.py:523-531`) and retro-fitted the type onto the three pre-existing shape guards
    (`:533`, `:539`, `:543`) — so the multi-spatial guard above **also** raises it now. D21's ratified consequence
    changes with it; see *Open items → Requires owner re-ratification*.
  Scope cost is recorded in D21.
  - **`past_known` products are NOT counted** (reviewer blocker-fix). `SeasonalPrecipRunoffRegression` declares a
    **second** `past_known` product alongside the single `future_known` one (`models/nwp_regression.py:765`), while
    `NwpRegression`/`NwpRainfallRunoff` inherit the base's empty extra-product hook (`:155,:570`). A bare "single
    product per branch" rule would therefore reject a shipped model that keeps control forecasting green today. The
    restriction is on the **future-forcing** axis only — the axis a forcing track actually fetches. All three shipped
    FI models declare exactly one `future_known` product (`:211,:223`), so none is rejected by the rule as now written.
- **D5 — Track deduplication → exactly ONE resolved cycle per track.** `resolve_tracks()` groups `ForcingRequired` by
  key, emits one `ResolvedTrackRequest` per key whose `fetch_horizons` is the per-feature **max** across the group,
  retains each assignment's own horizons, and is deterministic (sorted output). Fetching once per track — not per
  assignment (repeated 51-member downloads) nor per station (too coarse) — is the point. **The dedup identity is also
  the resolution identity:** every assignment sharing a key settles on the *same* `TrackFetchResult.resolved_cycle`
  (`docs/spec/types-and-protocols.md:3313-3316` — one `resolved_cycle: UtcDatetime` field, no per-assignment cycle).
  Consequently the completeness threshold is `fetch_horizons` (the max) and the retained per-assignment horizons are
  used **only** for post-fetch slicing (≤ max) at assembly (D9), never as an acceptance threshold (D8, D26).
- **D6 — `CandidateAwareForecastSource` + `isinstance` dispatch.** `@runtime_checkable` is mandatory: the dispatch is
  `isinstance(source, CandidateAwareForecastSource)`, which raises `TypeError` against a non-runtime-checkable
  Protocol. Do **not** add the method to `WeatherForecastSource`, which would make every legacy adapter structurally
  non-conforming — the pattern Plan 175 followed again with `BatchStationDataSource` (`protocols/adapters.py:75-93`), a
  fresh in-repo precedent for a narrow capability Protocol over widening the base one. `expected_member_ids(track)` is the **single derivation point** for the source's raw member identity.
  **Migrate exactly one adapter: `recap_gateway`**; MeteoSwiss and replay stay legacy, and MeteoSwiss migration
  (moving its internal `resolve_cycle` walk-back, `meteoswiss_nwp.py:601`, out to `services/`) is a follow-on.
- **D7 — Per-track resolution = walk-back policy in `services/`, driven at flow level.** A pure service walks back from
  the nominal cycle at the track's configured cadence (D24), calling `fetch_requirement` per candidate into a fresh
  `CandidateFetchResult`, validating completeness before anything is persisted, and committing only on full pass.
  Bounded by `max_cycle_age_hours`, threaded **explicitly from parsed configuration on both construction branches** —
  today it reaches only the production factory branch (`flows/run_forecast_cycle.py:465-474`, threaded at `:473`) while
  the injected-client branch (`:475-478`) silently uses the constructor default.
  - **Fetch/persist split.** `PgWeatherForecastStore` holds one `sa.Connection` (`store/weather_forecast_store.py:24-25`)
    that is not safe for concurrent use. External fetch and validation perform **no store I/O**; persist, readback, and
    per-station availability mapping run in a **serial** convergence step. Phase 3 resolves tracks sequentially;
    concurrent fetch is a follow-on.
  - **Per-HRU containment is required of the migrated adapter — and the SHAPE already exists on `main`** (corrected
    2026-08-18; Plan 154, `0de0b0f`, archived COMPLETE). The earlier claim that the control (`fc`) fetch "sits outside
    any containment block" is **false today**: `fetch_forecasts` stages each HRU inside a per-HRU `try:`
    (`recap_gateway.py:855`) over the whole per-variable fc+pf block, catches `RecapDataUnavailableError` at `:936` →
    `hru_discarded` + `recap.hru_unavailable_contained` + `continue`, and commits per-HRU all-or-nothing at
    `:955-957`. T4's work is to **mirror** that shape into `fetch_requirement`, not invent it. The **requirement** is
    unchanged: contain temporal unavailability per HRU, retain successful siblings, and reserve a candidate-wide
    `ABSENT_INCOMPLETE` for a payload in which **no** in-scope station is complete (D8) — `fetch_requirement` returns
    whatever each HRU yielded, per station, without judging it; the completeness verdict is the services-side gate's
    job (D8), never the adapter's.
    - **Three `fetch_forecasts` behaviours mapped onto the candidate taxonomy — LOCKED HERE, not left for T4 to decide**
      (2026-08-18; leaving them open risked softening Plan 154's malformed-payload guard mid-build). Current behaviour is
      precise and the mapping preserves it; `file:line` re-anchored at the same time:
      - **Malformed partial-control payload** — the **uncontained** `AdapterError` (`recap_gateway.py:927-935`: some
        variables returned control rows while others returned a well-formed EMPTY response in the *same* cycle, Plan
        154's control-coverage gate) → **candidate-FATAL**. This is a data-**integrity** fault, not a gap, so it is
        **not** walk-back eligible: walking back would silently paper over a malformed source response and erode the
        very guard Plan 154 added. Fail loud, exactly as today.
      - **Total loss after contained per-HRU failures** — the re-raised `RecapDataUnavailableError` (`:974-1000`,
        `code="source_data_missing"`, chaining `__cause__`) → **walk-back-eligible absence** (`ABSENT_INCOMPLETE`).
      - **All responses well-formed but empty** — the plain `{}` (`:1002`) → **walk-back-eligible absence**
        (`ABSENT_INCOMPLETE`) as well.
      The last two share a candidate status but must stay **tellable apart**: `nwp.candidate_rejected` carries distinct
      diagnostics for "every in-scope HRU failed" (including the chained Gateway text) versus "every response was
      well-formed empty", so an operator can still separate a source outage from a genuinely empty cycle.
  - **Missing polygon column = per-station unavailability on the per-track path; the legacy path is unchanged**
    (reviewer blocker-fix, decided here rather than deferred). Today `_iter_long_rows` raises one **batch-wide**
    `AdapterError` when any resolved polygon lacks a response column (`recap_gateway.py:511-526`). That fail-loud
    exists for a specific reason its own comment states: a missing column would otherwise **silently drop** that
    station, and the legacy return shape has no way to say "this station is absent". **Phase 3 has that
    representation** — `StationTrackUnavailable` — so on the per-track path a missing polygon column becomes an
    **explicit** per-station unavailability with reason `MISSING_POLYGON_COLUMN` (T1), while complete siblings survive.
    The diagnostic the batch-wide raise carried (station ids + polygon names) is preserved in the **structured WARNING
    log event only** — `StationTrackUnavailable.reason` is enum-valued with no free-text payload
    (`docs/spec/types-and-protocols.md:3310`), and Phase 3 does not widen it. The original concern is answered, not
    discarded: the station is *recorded* unavailable, never silently dropped. `fetch_forecasts` (legacy) keeps the
    batch-wide raise verbatim.
  - **Configuration errors stay candidate-wide, never per-station** (D27). Duplicate-polygon resolution raises
    `RecapConfigurationError` with **zero** Gateway calls (`tests/unit/adapters/test_recap_gateway.py:644`,
    `TestDuplicatePolygonResolution`). That is a misconfiguration, not a data gap: it must **not** be routed through
    the new per-HRU containment path and silently downgraded to a soft per-station outcome. T4 locks this
    distinction with a red-first test.
  - **Phase A must not persist partial candidates for migrated stations.** The legacy `_fetch_nwp_task` still runs for
    the group path, and the legacy `fetch_forecasts` still returns partial ensembles by design — its inner `pf`
    containment survives Plan 154 intact (`recap_gateway.py:886-906`).
    Phase A is therefore re-scoped to exclude stations served by the per-track path, while still covering group-member
    stations, so an unvalidated partial candidate can never land in Postgres for a migrated station.
- **D8 (completeness) — Completeness is a PER-STATION predicate; the candidate verdict is derived from it** (distinct
  from the ledger's `D8-group`, which is a routing decision). Two gates that must not
  be conflated, plus the rule that reconciles them.
  1. **Raw completeness — evaluated per `(station, feature, member, horizon)`.** For the pre-extracted recap path the
     candidate is already a `dict[StationId, WeatherForecastResult]` accumulated per HRU
     (`recap_gateway.py:821-973` — `acc` is station-keyed at `:822` and the `pf` loop `break` at `:899-906`
     deliberately keeps a *partial* member set), so "the candidate" has no single global member axis: a candidate-wide union of member ids
     can read complete while one station holds `fc` + 10 members and its sibling holds all 51. The predicate is
     therefore evaluated **per station**: for every `f in key.features`, that station's series must reach
     `fetch_horizons[f]` steps, and (ENSEMBLE) must carry **exactly** `expected_member_ids(track)` at that horizon;
     a no-member-axis track requires the single run. **Threshold = `ResolvedTrackRequest.fetch_horizons`, the
     per-feature group MAX** (`docs/spec/types-and-protocols.md:3289-3294`) — so "5-day + 10-day dedup onto one track"
     is checked at 10-day and both assignments settle on the same cycle (D5, D26).
     - **"Steps" are counted in the TRACK's `time_step` units, after the same reduction assembly uses** (reviewer
       blocker-fix). Recap returns sub-daily rows (`recap_gateway.py:527`) while a daily track's `FutureSteps` counts
       **daily** buckets, and assembly applies issue-time filtering plus per-feature aggregation and bucketing before
       counting (`services/operational_inputs.py:131,212,523,537`). Counting raw rows would accept a candidate with
       many sub-daily rows but too few daily steps, and would disagree with assembly around a non-midnight issue
       cycle. The validator therefore applies that same reduction **before** counting — and still **before** persist,
       so commit-only-on-full-pass holds. Note `_filter_and_cap_daily_records` keeps `valid_time >= issue_time`
       (`operational_inputs.py:212-235`, the rule itself at `:233`), not `>`.
  2. **Candidate verdict (drives walk-back).** `COMPLETE` iff **at least one** in-scope station is complete under (1);
     `ABSENT_INCOMPLETE` (walk-back eligible) iff **none** is. One station's shortfall must never reject the candidate
     for its complete siblings — that is the same containment rule D7 imposes on the adapter.
  3. **Incomplete stations are dropped BEFORE persist.** On acceptance, only the complete stations' rows are persisted;
     an incomplete station's rows are discarded with the candidate scratch and recorded as
     `StationTrackUnavailable(reason=INCOMPLETE_AT_CYCLE)` in the `TrackFetchResult`. So Postgres never receives a
     partial ensemble on the per-track path (the legacy Phase A path is separately re-scoped away from these stations,
     D7), and the readback gate below can keep treating "no rows" as a plain absence.
  4. **Per-station availability (assignment-local).** Determined after readback, within the already-accepted cycle: a
     station with no readback data yields `StationTrackUnavailable(NO_DATA_AT_CYCLE)` (or `EXTRACTION_EMPTY` /
     `NOT_SUBSCRIBED`). Every `StationTrackUnavailable` — from (3) or from here — fails that assignment locally with
     `TRACK_UNAVAILABLE` and advances the fallback chain (D10). It does **not** reject the cycle.
  - **Accepted cost (recorded, not hidden):** an incomplete station does **not** get its own walk-back — the track's
    cycle is established by its complete siblings, so that station fails this cycle and its chain advances. Per-station
    cycle resolution is explicitly a follow-on in the ratified design (`docs/design/forecast-cycle-redesign.md`,
    group-CONTROL round-3 note: "Per-station group cycles are a follow-on"), and is unrepresentable under the locked
    single `TrackFetchResult.resolved_cycle` (`docs/spec/types-and-protocols.md:3313-3316`). See D29.
  - **`StationUnavailableReason` gains `INCOMPLETE_AT_CYCLE`** (and `MISSING_POLYGON_COLUMN`, D27) — the spec's enum
    (`docs/spec/types-and-protocols.md:3308-3311`) carries only `NO_DATA_AT_CYCLE | EXTRACTION_EMPTY | NOT_SUBSCRIBED`
    and cannot express "present but short of the member/horizon contract". Additive; carried by T1's spec edit and
    ratified alongside the other T1 spec edits (D31, owner-ratified 2026-08-11).
- **D9 — Per-assignment assembly.** Each assignment assembles its own frame from its track's available records at the
  track's single `resolved_cycle` (its features, its `time_step`, its `InputFrameHorizon`), **slicing its own
  `assignment_horizons` (≤ `fetch_horizons`) out of the fetched-at-max rows** — this slicing is the only consumer of
  the retained per-assignment horizons (D5). The FI per-variable slice is applied at **both** FI sites —
  the future-known NaN-tolerance gate and `InputSeries` construction — so a short-horizon feature is not counted NaN
  for lacking long-horizon steps. **The selected-branch rule applies to `past_known` too**:
  `_variables_over_nan_tolerance` evaluates both past and future inputs (`forecast_interface.py:676`) and
  `_past_known_nan_tolerances` still iterates every branch (`:646`), so both tolerance maps must derive from the one
  selected `DynamicInputSpec`. The station NaN gate is at `:768`; it now has a **second** call site in `predict_batch`
  (`:820`) — the GROUP path, **out of Phase 3's scope** (D8-group), which T6 must not silently migrate. Per-assignment `nwp_age_hours` is recomputed from **that assignment's** resolved cycle
  (`None` for `NoForcingRequired`); otherwise a stale fallback reports fresh input quality.
- **D10 — Runner consumption seam via a discriminated input.** "Receive a context, but also detect an absent one" is
  type-incoherent, and `ModelRunContext | None` makes invalid states representable. The two pre-run failure arms are
  resolved in the runner **before** `_run_single_model` is invoked, via
  `AssignmentRunInput = ReadyContext | MissingTrackContext | UnavailableTrackContext`. *(Naming corrected 2026-08-19:
T7 did NOT migrate `run_all_station_forecasts`; it added a SEPARATE entry point, `run_all_station_forecasts_per_track`
(`services/run_station_forecast.py:657`). `run_all_station_forecasts` and `run_station_forecast` remain the LEGACY
calls and are not modified by Phase 3 — see T8b.)* The caller pattern-matches and
  short-circuits both failure arms to an `AssignmentFailure` **without touching the model registry, artifact store,
  model-state store, or QC**.
  - **`MISSING_CONTEXT`** — the track never resolved (no candidate passed completeness within the walk-back bound).
  - **`TRACK_UNAVAILABLE`** — the track resolved, but this station is unavailable at the accepted cycle.
  Both are assignment-local and advance the fallback chain. `_run_single_model` receives a non-optional
  `ModelRunContext` plus the assignment's own `ForecastProvenance`, and writes each `OperationalForecast`'s cycle
  fields from **that** provenance rather than the shared runner arguments (`run_station_forecast.py:145-146,:400-401`).
  A pre-run defensive assert re-checks the expected member set over the assembled frame.
  - **D10a — on the `ReadyContext` route the runner's forcing reads come from the CONTRACT, not
    `ModelDataRequirements`** (reviewer major-fix, 2026-08-18; the Problem section promised this and neither D10 nor T7
    previously said it, so T8b's own heterogeneous golden would have failed with no task assigned to fix it).
    - **Horizon — the axis that actually diverges.** `assess_future_coverage` takes ONE `required_steps: int` and
      requires **every** required feature to reach it (`services/nwp_coverage.py:75-81,128-150`), and `RequiredSteps`
      carries a single `steps: int` with no per-feature dimension (`services/horizon_semantics.py:51`). A heterogeneous
      assignment — T6's own "precip 2 steps + temp 10 steps" red-first — would assemble correctly and then be rejected
      `INSUFFICIENT_COVERAGE` (`run_station_forecast.py:183-188,:199`) on the short feature, silently defeating the two
      collapses T3/T6 just fixed.
    - **Mechanism — the Plan 159 seam is called ONCE PER ASSIGNMENT, unchanged; the per-feature split happens AFTER it.**
      `resolve_required_steps` is **not** per-feature: it takes `(model, model_id, declared_steps, *, opt_in)`
      (`horizon_semantics.py:119-125`) and delegates to `_model_declared_floor(model)` (`:74-117`), whose signature takes
      **only the model** — it walks the model's entire `input_requirement.dynamic` tree (spatial → spec → `future_known`
      source → variable) and returns ONE scalar, "The binding floor across variables is the LARGEST" (`:114-115`). Calling
      it per feature would reuse that same model-wide floor every time, varying only `declared_steps`. **So:** call it
      **once per assignment**, exactly as today (`run_station_forecast.py:177-182`), for the resolved ceiling; then call
      `assess_future_coverage` **per feature** with `required_features=frozenset({f})` and `required_steps=min(ceiling,
      the contract's horizon for f)`. That fixes the "precip 2 / temp 10" collapse with **no signature change** to
      `horizon_semantics.py` or `nwp_coverage.py` — neither joins the touched set — and without claiming per-feature
      precision the seam cannot provide.
    - **Accepted cost (parallel to the member-set cost below): per-feature `AT_MOST` floors are NOT expressible through
      this seam.** Every feature shares the model-wide **maximum** floor, so `min(max floor, f's horizon)` ≠ `min(f's own
      floor, f's horizon)` whenever another variable's floor dominates. **The `AT_MOST` axis is dead TWICE OVER — two
      INDEPENDENT structural reasons, both verified 2026-08-18 — and the second of them is what makes T7's guard
      unimplementable as it was first written:**
      - **(a) The pinned FI has no such field at all.** `pyproject.toml:104` pins ForecastInterface **v0.1.19**, and the
        installed package contains neither `horizon_semantics` nor `min_future_steps` (grepped: zero hits). Every
        variable the walk reaches therefore hits the "not declared" branch and `_model_declared_floor` returns `None`
        for every model (`services/horizon_semantics.py:103-106`).
      - **(b) The model the runner holds is the ADAPTER, which does not expose `input_requirement`.**
        `discover_models()` **always** wraps an FI model in `ForecastInterfaceAdapter`
        (`services/model_registry.py:92-114`); the adapter keeps the raw model private as `self._model`
        (`adapters/forecast_interface.py:450`) and has **no `__getattr__` passthrough** — its own `config_hash`
        docstring records that as a deliberate choice (`:462-473`). `_model_declared_floor` reads
        `getattr(model, "input_requirement", None)` (`horizon_semantics.py:85`), so it returns `None` on its very first
        line for every discovered FI model. **Confirmed at runtime:** `discover_models()` yields
        `ForecastInterfaceAdapter` for `nwp_regression`, `nwp_rainfall_runoff`, `seasonal_precip_runoff_regression` and
        `cmal_pool_pt`, and `hasattr(m, "input_requirement")` is **False** for every one.
        **Consequence for any guard:** a route-time check that reads divergent per-variable floors off the model can
        never observe them in production. A guard tested against a **raw** FI fake would pass while the
        production-shaped adapter route never sees a divergent floor — a test that proves nothing. Reaching into
        `self._model` from the runner is not an escape: it violates the single-FI-boundary rule
        (`CLAUDE.md` § ForecastInterface Adherence).
      A third, weaker observation still holds but is *not* a separate deadness: the only in-repo ceiling declaration is
      the **model-keyed** opt-in `HORIZON_CEILING_FLOORS` (`types/ids.py:69-71`), inherently model-wide, whose one entry
      `cmal_pool_pt` is `ArtifactScope.GROUP` (`models/aquacast/_shim.py:566`) and is excluded from this route by
      D8-group / D30. **The trap is real but doubly unreachable:** an FI bump to ≥ v0.1.20 with divergent per-variable
      `min_future_steps` would silently give a short feature a floor it never declared, and T2's D4 sweep does **not**
      reject divergent floors — but such a model would ALSO have to defeat (b) before any guard could see it. Whether
      Phase 3 funds a guard at all is **`D34-atmost-guard-unreachable`** (*Open items → Requires owner
      re-ratification*) and is **not decided here**.
    - **Mode and feature set do NOT diverge today — recorded, not assumed.** Post-Plan-156 at most one time-step branch
      may carry `future_known` (`adapters/forecast_interface.py:487-497`), and both `future_dynamic_features` and the
      OR-ed `ensemble_mode` accumulate **only** from `spec.future_known` (`:512-515`, mode built `:579-581`) — so both
      already equal the SELECTED branch's values, and neither is a live collapse (a non-FI model declares them directly,
      `types/model.py:271,275,277`, and D2 broadcasts its scalar). They are nevertheless read from the contract on this
      route: it is one substitution at each of two call sites (`:250-252`, `:291-295`) and it removes the coupling that
      would silently reintroduce the divergence the moment **D32-multibranch-delivery** relaxes the
      single-deliverable-branch assert. T7 pins this as a **no-behaviour-delta equality**, not a behaviour change.
    - **Cost (recorded, not hidden):** called per feature, `assess_future_coverage`'s cross-feature "identical member
      set across features" check (`nwp_coverage.py:116-126`) no longer fires on this route. Not a loss of strength:
      T5's per-station exact-`expected_member_ids` gate (D8) is strictly stronger and runs **before** persist, and the
      pre-run defensive member-set assert above re-checks the assembled frame. The **legacy route keeps the single
      scalar call verbatim** — that is what the T7 legacy red-first pins.
  - **Legacy vs per-track dispatch needs an explicit discriminant.** A legacy NWP context must not be forced to choose
    between carrying a per-feature contract it never built and skipping coverage entirely. The runner-boundary type
    discriminates the two routes explicitly rather than inferring the route from an empty contract.
- **D11 — Cross-cycle combination = fail-loud preflight; same-cycle stays green.** The check runs at the **top of the
  per-station persist block**, before the individual-forecast store (`flows/run_forecast_cycle.py:2354-2367`) and the
  state store (`:2368-2383`) — not at the combination point (`:2386`), where a mismatch would leave partial writes
  committed. On mismatch the station fails loud with **zero** forecast/state writes. The resolved cycle is read from
  each combinable result's own forecasts' provenance (`types/forecast.py:78-83`).
  - **Trackless combinable models are neutral.** `LinearRegressionDaily` is a non-fallback skill model with no future
    forcing and is included in `combinable_results`, which excludes only explicit fallback IDs
    (`run_station_forecast.py:111-115`). Equality is compared only across **non-null** forcing cycles; the preflight fails
    only when two distinct non-null cycles exist, and a sole non-null cycle supplies combined provenance.
- **D12 — CONTROL-only green via `isinstance` dispatch + golden tests.** Only migrated-adapter station assignments take
  the per-track path, **minus the group-overlap exclusion**: a station that is also a group member stays legacy in
  Phase 3 (D30), so adapter capability is necessary but not sufficient for per-track routing. A **homogeneous** control station must produce today's behaviour, pinned by golden tests. A
  **heterogeneous** control station is an intentional behaviour change, pinned by new golden tests. "Control-only stays
  green" means the homogeneous case is byte-identical — not that every control config is unchanged.
- **D13 — Observation fetch stays parallel; logging.** Observation fetch runs concurrently with track projection and
  candidate fetch, with an explicit gather barrier before the assemble/run phase (today `_fetch_obs_timestamps_task.submit`
  at `flows/run_forecast_cycle.py:1985` runs parallel to Phase A at `:1966`). New canonical `{entity}.{action}` events:
  `nwp.candidate_rejected`, `nwp.track_resolved`, `forecast.assignment_failed`, `forecast.fallback_advanced` — WARNING
  for fallback, member-level at DEBUG (`docs/standards/logging.md`).

## Phases
The phase lands **atomically**, and the tree stays green at every task gate — but **not** because the whole phase is
dormant behind the `isinstance` dispatch until the final wiring task. That blanket claim was too broad; corrected
2026-08-18, per task: **T1 / T3 / T4 / T5 are genuinely unused** until T8b wires them (T8a is dormant too). **T2 is globally LIVE the moment
it lands** — its conformance sweep runs inside *every* `ForecastInterfaceAdapter` construction
(`adapters/forecast_interface.py:443-453`), which `discover_models()` reaches repo-wide, including for legacy-path and
GROUP-only FI models, before any dispatch exists. **T6 is globally LIVE too** — its FI per-variable slicing changes the
shared `predict` / `predict_batch` paths (`:646-718`, `:750-824`), which every route uses. **T7** preserves the legacy
route explicitly (the scalar `assess_future_coverage` call, byte-for-byte). **T8a** is dormant; **T8b** activates
per-track routing and is the FIRST task in this plan that changes production behaviour.
**The safety CONCLUSION is unchanged — control-only stays green at every task gate — but it rests on two facts, not on
unreachability, and this plan says so explicitly:** (i) every current FI entry point *conforms* to T2's sweep (the four
of `pyproject.toml:165-175`, per D21's standing check), and (ii) every current model's per-variable horizons are
*uniform*, so T6's slice is a no-behaviour-delta on them. Every task must carry **at least one separately named
acceptance test that genuinely FAILS against pre-task code**, which the implementation then turns green, with test
soundness proven. **A preservation / regression gate does NOT satisfy that requirement** (corrected 2026-08-18 — the
blanket "every criterion is red-first" claim was false): several criteria below are explicitly labelled *REGRESSION
gate, EXPECTED to stay GREEN* — T2's multi-spatial guard, T4's legacy `fetch_forecasts` behaviour, T7's legacy ENSEMBLE
caller, and T8b's same-cycle / trackless / group-only / D30-overlap / legacy-adapter cases. They prove nothing was **broken**, not that
anything new was **built**, and are named as such rather than counted toward the red-first requirement. T3, T5 and T6
carry no already-green criteria. Each task
lands its production change **and its test consumers together** — no task leaves a known failure. **Per-task exit gate
(corrected 2026-08-18 — it is "no NEW findings versus a pinned baseline", never "clean"):** the focused modules, plus
full `uv run pytest -q` green at **`passed >= 4550` with NO baseline test regressing** — every task is
red-first and adds tests, so the total is **expected to rise**; the baseline is a **floor, not an equality**, and a
gate demanding equality would fail on the first new passing test; `pyright` gated by `tools/pyright_ratchet.py` against
`tools/pyright_baseline.json` **scoped to `src/`** (pre-push hook `pyright-ratchet`, `.pre-commit-config.yaml:61-69`),
which must not exceed the pinned **432** — a task that legitimately reduces it regenerates the baseline via
`tools/pyright_baseline.py` in that task's own commit; and `uv run ruff check` introducing **no new findings** beyond
the 12 pre-existing ones already on `main`.

**Measured baseline (2026-08-18, `main` `c81041e`, before any Phase 3 work):** `uv run pytest -q` = **4181 passed,
15 skipped, 11 deselected** — the RECORD of what was measured **before T1–T4**. It is **NOT this run's floor**: PR #182
(`707237e`) added T1–T4's own tests, so the floor for the T5–T7 run (T8 split out, see D35) is the re-measured post-merge count: **4284 passed
/ 15 skipped / 15 deselected**, measured 2026-08-18 against `707237e`. **Neither number is the floor for the T8 run:** PR #192
(`351bac3`) added T5–T7's own tests, so the T8 floor is the post-#192 re-measured count: **4550 passed / 20 skipped /
15 deselected**, measured 2026-08-19 against `351bac3`. `uv run pyright` over `src/` was **exactly 432** at the
c81041e baseline — precisely AT the ratchet; it is now **404**, i.e. below it, because T5–T7 removed errors. The gate
stays **≤ 432** and 404 must not regress above it (zero is *not* the gate); `uv run ruff check` **exits 1** with 12 pre-existing
`E501 Line too long` findings, all in `alembic/versions/0037_calculated_station_formulas.py` (4) and
`alembic/versions/0038_calculated_station_formula_trigger.py` (8) — outside `src/`, unrelated to Phase 3. Stated so a
reader (or the `implement` loop) can tell drift from a regression it introduced, without re-measuring and without
burning fixer rounds reformatting alembic migrations.

- **T1 — Domain types + additive failure causes (D1).**
  - **In scope:** create `types/forcing_track.py` with the D1 types (no `OutputHorizon`); add `MISSING_CONTEXT` and
    `TRACK_UNAVAILABLE` to `AssignmentFailureCause`; add `INCOMPLETE_AT_CYCLE` **and `MISSING_POLYGON_COLUMN`** to
    `StationUnavailableReason` (D8, D27); and add the **raw fetch-outcome type** the migrated adapter returns (D31).
    **Also in scope (docs-with-code):** the corresponding corrections to `docs/spec/types-and-protocols.md` —
    `expected_member_ids` on the Protocol, the source-derived member set, the two new `StationUnavailableReason`
    members, the per-`(station, feature, member, horizon)` wording of the completeness gate (`:3289-3294`,
    `:3308-3311`), the `OutputHorizon` deferral, **and the `fetch_requirement` return-type change from
    `CandidateFetchResult` to the raw fetch outcome (`:3210-3213`, `:3318-3324`), with `CandidateFetchResult`
    constructed services-side (D31)**; **the mixed-mode granularity note (`:3245`), rewritten from "a mixed-mode
    requirement SPLITS into per-mode `ForcingRequired`" to: a mixed-mode requirement is REJECTED at construction,
    raising `UnsupportedModelRequirementError` (D4 as corrected; and D22 — exactly ONE forcing track per assignment, so
    splitting is a follow-on, not this contract)**; **and `ForcingTrackKey.nwp_source` (`:3256`), retyped from
    `NwpSource  # existing NewType/enum` to the repo's existing `str` — no `NwpSource` type exists
    (`types/station.py:106`; D1, D12)** — so READY ratifies these authoritative contract changes rather than deferring
    them. Those last two correct an AUTHORITATIVE spec that today contradicts a settled decision; neither reopens the
    decision (added 2026-08-18 — they were previously owned by no task). **`TrackFetchResult` keeps its single `resolved_cycle` field unchanged** (`:3313-3316`): Phase 3 does **not**
    introduce a per-assignment or per-horizon-class cycle (D5, D26).
  - **Red-first:** `FutureSteps(value=0)` and `value=-1` raise `ValueError`; `ForcingTrackKey` is hashable and usable
    as a dict key, with keys built from differently-ordered inputs comparing and hashing equal; the key carries **no
    horizon field at all** — asserted **structurally** (its fields are exactly the five ratified ones: `nwp_source`,
    `ensemble_mode`, `time_step`, `spatial_representation`, `features`, and no horizon-valued attribute exists), because
    D1 excludes horizons from `ForcingTrackKey` entirely, so "two keys differing only in horizon" is not constructible
    at T1's own abstraction boundary (corrected 2026-08-18; the **behavioural** half — that two requirements differing
    only in per-feature horizons project to the SAME key and dedup onto one `ResolvedTrackRequest` — belongs to **T3**,
    which owns the projector and reducer, and is already covered by T3's dedup criterion: cross-referenced here, not
    duplicated); keys differing in one feature
    name, mode, `time_step`, or spatial representation are not equal; the outcome and projection unions narrow via
    `isinstance`/`match`; the two new enum members exist and are distinct.

- **T2 — FI accessors + conformance sweep (D3, D4).**
  - **In scope:** per-time-step public accessors on `ForecastInterfaceAdapter` returning the selected branch's
    per-feature horizons and modes without the cross-branch collapse; and the construction-time conformance sweep
    raising **`UnsupportedModelRequirementError`** (`exceptions.py:111`, D4 — **not** `ConfigurationError`, which would
    darken discovery for every model) for multi-product or mixed-mode branches. Note there are **four** FI entry points
    (`pyproject.toml:165-175`), not one — the sweep is repo-wide (D21). The fourth, `cmal_pool_pt` (`:175`), resolves
    **only** with the `aquacast` extra (`:163`; CI installs it in the unit job, `5201f60`): without it the sweep has
    three targets, not four.
  - **Red-first (accessor tests belong in the existing `tests/unit/adapters/test_forecast_interface_adapter.py`):** a
    model declaring precip 2 steps and temp 10 steps exposes both, not the collapsed scalar (**fails today**); a model
    with two supported time steps carrying different features exposes each branch separately (no cross-step
    inheritance) — but see *Open items → D32-multibranch-delivery*: post-156 the only constructible two-branch shape
    is one future-forced + one past-only, which still cannot **deliver**, so this criterion's wording depends on the
    owner's answer there; a **past-only branch** exposes empty future horizons and does
    **not** raise from a mode accessor; multi-product and mixed-mode branches raise
    `UnsupportedModelRequirementError`; the existing multi-spatial guard is unchanged (it now raises that same type
    since Plan 156, `forecast_interface.py:539`) — that last one is a **REGRESSION gate, EXPECTED to stay GREEN**: it
    already raises `UnsupportedModelRequirementError` (`adapters/forecast_interface.py:537`) with a passing test at
    `tests/unit/adapters/test_forecast_interface_adapter.py:235`, so it proves T2 broke nothing and is **not** one of
    T2's genuinely failing tests.

- **T3 — Track projection + `resolve_tracks()` (D2, D5).**
  - **In scope:** the pure `services/` projector over the explicit join, and the deterministic reducer.
  - **Red-first:** the projector maps a join to `ForcingRequired` with per-feature horizons and the locked key shape; a
    model whose **selected branch** has no `future_known` projects to `NoForcingRequired` **even when another branch
    has future forcing**; a fallback model projects to `NoForcingRequired`; a non-FI multi-feature model broadcasts its
    scalar horizon to every feature; two assignments with identical track fields **but DIFFERENT per-feature horizons**
    project to the same `ForcingTrackKey` and dedup to one `ResolvedTrackRequest`
    whose `fetch_horizons` is the per-feature max, with each assignment's own horizons retained — this criterion
    **already covered the dedup case**; the horizon divergence is made explicit here so that it carries the behavioural
    half of T1's key-equality assertion (A4), with no new criterion added; reducer output order
    is deterministic.

- **T4 — Source contract + adapter migration (D6, D7 containment).**
  - **In scope:** `CandidateAwareForecastSource` (`@runtime_checkable`) in `protocols/adapters.py`;
    `fetch_requirement` + `expected_member_ids` on `recap_gateway` as a **pure single-cycle** fetch with **no** internal
    walk-back and **per-HRU containment**; wire the adapter factory's return type for dispatch. **Add and validate
    `RecapGatewayConfig.cycle_cadence_hours`** (the walk-back cadence source, ruling 4 / D24) — **the configuration
    field ONLY**. **Threading it is NOT T4's** (corrected 2026-08-18): D7 puts the walk-back resolver in `services/`,
    **T5** creates that resolver and defines the cadence parameter that consumes the value, and **T8a** owns the
    flow-level policy carrier and therefore threads the configured cadence through all three construction paths. Today those two
    branches only construct and return the adapter (`flows/run_forecast_cycle.py:465-478`), so T4 cannot thread a bound
    into a resolver that does not exist yet — and parking the policy on the adapter instead would violate D7.
    Legacy adapters and fakes untouched.
  - **Ownership: the adapter classifies TRANSPORT, the service classifies COMPLETENESS** (reviewer major-fix). The
    spec sketches `fetch_requirement -> CandidateFetchResult`, but the adapter must not judge completeness (D7/D8) —
    it cannot, since the threshold is a `services/`-side `ResolvedTrackRequest.fetch_horizons`. `fetch_requirement`
    therefore returns a **raw typed fetch outcome** (per-station payload + transport classification: fetched /
    absent-at-cycle / auth-config), **raises** typed transient transport errors, and the `services/` resolver
    constructs the immutable `CandidateFetchResult` — assigning `COMPLETE`/`ABSENT_INCOMPLETE` after the per-station
    predicate, and `TRANSIENT` only after the retry budget is exhausted. Surfaced as **D31-candidate-ownership**
    (an explicit SPEC EDIT, scoped into T1 — not a silent divergence).
  - **Red-first:** a conformance test proves `fetch_requirement` requests **exactly** the nominal cycle and performs no
    walk-back (fails if the implementation reuses `resolve_latest_cycle`); `isinstance` is `True` for the migrated
    adapter and `False` for a legacy-only fake (the negative case would raise `TypeError` without the decorator);
    **a two-HRU test where HRU A's control fetch is unavailable and HRU B still resolves — asserted against the NEW
    `fetch_requirement`, not `fetch_forecasts`** (corrected 2026-08-18: as originally written it already PASSES on
    `main` since Plan 154 — a false red-first); **a missing polygon column
    for one station yields that station's explicit unavailability while its sibling's rows survive** (today this raises
    batch-wide, `recap_gateway.py:511-526`); **a duplicate-polygon misconfiguration still raises
    `RecapConfigurationError` naming both stations with ZERO Gateway calls** — the D27 regression proving a config
    error was not silently downgraded into per-station containment (mirrors
    `tests/unit/adapters/test_recap_gateway.py:644`); **a MALFORMED partial-control payload
    (Plan 154's control-coverage gate, `recap_gateway.py:927-935`) is candidate-FATAL and does NOT become
    walk-back-eligible** — the resolver must not try an older cycle after it (D7's locked mapping); a transport failure
    is **raised** rather than returned as a status; an auth/config error is fatal, never walked back;
    **`cycle_cadence_hours` parses and validates** — a valid value round-trips through the parsed `RecapGatewayConfig`
    and a non-positive / malformed value is rejected with the typed configuration error; this fails today because the
    field does not exist (`config/recap_gateway.py:29`). **T4 asserts CONFIG PARSING AND VALIDATION ONLY — never
    threading**, since the consumer (T5's resolver) and the wiring (T8a) are both outside this task. Legacy
    `fetch_forecasts` behaviour is asserted **unchanged** throughout — that one is a **REGRESSION gate, EXPECTED to
    stay GREEN**, already covered by the legacy regression tests from `tests/unit/adapters/test_recap_gateway.py:2318`;
    it proves T4 broke nothing and is **not** one of T4's genuinely failing tests.

- **T5 — Per-track resolution: walk-back policy + completeness + `TrackFetchResult` (D7, D8).**
  - **In scope:** the pure fetch/validate half (walk-back bounded by `max_cycle_age_hours` threaded on **both**
    construction branches, plus the **cadence parameter that consumes T4's `RecapGatewayConfig.cycle_cadence_hours`** —
    T5 defines the parameter, T8a supplies the value; the **per-station** completeness validator keyed on `expected_member_ids` and on the track's
    `fetch_horizons` max, plus the candidate verdict derived from the per-station verdicts) and the serial convergence
    half (persist **only complete stations**, readback, per-station availability, complete `TrackFetchResult`). Name the
    conversion boundary between the candidate payload and the records the validator reads — there are now **two**
    conversion sites, gridded/extracted (`flows/run_forecast_cycle.py:1275`) and pre-extracted dict (`:1356`); T5 must
    name which it mirrors.
  - **Red-first:** an incomplete ensemble at the freshest cycle with a complete set one cycle back walks back and
    **never persists** the incomplete candidate; a uniform-but-partial member set is rejected (the new gate is
    exact-set, unlike `nwp_coverage.py:116-117`); the expectation comes from `expected_member_ids`, so a **21-member
    source** validates against 21 and a 51-member source against 51 **with no test or validator change** (the Swiss
    regression); **one cycle per track** — a candidate that covers a 5-day assignment but falls short of the 10-day
    sibling's `fetch_horizons` is **rejected**, walk-back continues, and both assignments end up on the *same*
    `resolved_cycle` (D5/D26); **a partial station beside a complete sibling** — station A returns control + 10 of the
    expected members while station B returns the full set: the track resolves at that cycle, B is
    `StationTrackAvailable`, A is `StationTrackUnavailable(INCOMPLETE_AT_CYCLE)`, and **no A rows reach the store**
    (assert the store, not just the outcome map — an empty-sibling test would pass without the drop-before-persist
    rule); a candidate where **no** station is complete is `ABSENT_INCOMPLETE` and walks back; exceeding
    `max_cycle_age_hours` yields no resolved cycle; a non-default bound is honoured on **both** construction branches;
    post-readback, one station yields available and a sibling unavailable **within the same accepted cycle**.

- **T6 — Per-assignment assembly (D9).**
  - **In scope:** the new per-assignment assembler building each assignment's frame and its `ReadyContext` (including
    per-assignment provenance and `nwp_age_hours`); the FI per-variable slice at both sites, with **both** past- and
    future-known tolerance maps derived from the selected branch. The legacy superset assembler is untouched.
  - **Red-first:** an assignment needing precip 2 steps and temp 10 assembles a frame where precip's short horizon is
    accepted and temp gets 10 — **fails** under the single-scalar path. Because the NaN gate and `InputSeries`
    construction are separate paths, assert **each site independently**. A branch-specific past variable and a
    branch-specific `max_nan` are honoured from the selected branch only — **restated under D32 option (b)
    (construct-only, ratified 2026-08-18): assert it against a SINGLE-branch fixture.** The delivery-level assert
    `_assert_single_deliverable_dynamic_branch` (`adapters/forecast_interface.py:1078-1102`, called at `:827`, `:847`,
    `:892`, `:1113`) is **NOT** relaxed, so a multi-branch model cannot reach delivery at all; the criterion is that the
    past- and future-known tolerance maps (and `max_nan`) are derived from **that one selected `DynamicInputSpec`**
    rather than flattened across the requirement (`forecast_interface.py:727`, `:757`). End-to-end multi-branch
    delivery is Plan 153 and is **out of scope here**.
    `ReadyContext.nwp_age_hours` differs between a fresh and an older-resolved assignment. **T6 asserts `ReadyContext` only** — `ModelRunContext` is not constructed
    until `_run_single_model` (`run_station_forecast.py:243`), which T7 owns.

- **T7 — Runner consumption seam (D10).**
  - **In scope:** resolve the two pre-run arms before `_run_single_model` — **as built, in the SEPARATE entry point
    `run_all_station_forecasts_per_track` (`services/run_station_forecast.py:657`), not in the legacy
    `run_all_station_forecasts`, which is unmodified**; the
    discriminated `AssignmentRunInput` with an explicit legacy-vs-per-track discriminant; migrate `_run_single_model`
    to receive its context and write provenance from it; the pre-run defensive member-set assert; keep the fallback
    chain and the Plan 150 backstop intact. **Plus D10a — the runner's three forcing reads.** On the `ReadyContext`
    route, derive them from the context's per-feature contract instead of `model.data_requirements`: coverage
    evaluated **per feature** at `assess_future_coverage` with `required_steps=min(ceiling, contract horizon for f)`,
    the ceiling from the single per-assignment `resolve_required_steps` call (replacing the scalar coverage call at
    `run_station_forecast.py:177-192`), ENSEMBLE dispatch (`:254-256`), and `fan_out_ensemble(..., future_features=…)`
    (`:295-299`). Without this the flagship heterogeneous station returns `AssignmentFailure(INSUFFICIENT_COVERAGE)`
    and T8b's heterogeneous golden fails with no task owning the fix. The legacy route keeps the scalar call unchanged;
    `nwp_coverage.py` is **called differently, not modified**, and `horizon_semantics.py` exactly as today.
  - **No `AT_MOST` guard — deliberately NOT built (D34 option (a), ratified 2026-08-18).** The per-feature `AT_MOST`
    limitation stays recorded as an **accepted cost in D10a**; **revisit trigger:** an FI bump to **>= v0.1.20** **AND**
    a per-track-eligible model declaring divergent per-variable `min_future_steps`. Nothing is to be built, stubbed or
    left `TODO` for it in this run.
  - **Transitional API:** the seam changes the input shape of `_run_single_model` **and** `run_all_station_forecasts`,
    so T7 must keep every current caller green — including the integration caller
    (`tests/integration/test_e2e_pipeline.py:653`) — either by retaining a behaviour-preserving entry point or by
    migrating all callers in this task. T7's gate **includes** that integration caller.
  - **Red-first:** a station whose higher-priority assignment is ready and whose lower-priority assignment has a
    missing track returns the higher-priority success and records `MISSING_CONTEXT`, **asserting the model registry,
    artifact store, model-state store, and QC were not accessed** for the failing arm; an unavailable-track assignment
    records `TRACK_UNAVAILABLE` and advances the chain; a resolved-but-partial context trips the pre-run assert as an
    assignment-local failure; a **legacy ENSEMBLE caller** still performs coverage and fan-out unchanged (the scalar
    `assess_future_coverage` call, byte-for-byte) — a **REGRESSION gate, EXPECTED to stay GREEN**, since that path
    already exists (`services/run_station_forecast.py:187`) and is already tested
    (`tests/unit/services/test_run_station_forecast_fanout.py:312`); it proves T7 broke nothing and is **not** one of
    T7's genuinely failing tests. **D10a:** a heterogeneous `ReadyContext` — precip contracted at 2
    steps, temp at 10, frame assembled to match — reaches `predict` instead of returning
    `AssignmentFailure(INSUFFICIENT_COVERAGE)`; this **fails today**, because the scalar `required_steps=10` makes
    `min(counts)=2` inadequate (`nwp_coverage.py:144-150`). Paired with the opposite case so the gate is proven
    *tightened, not deleted*: a `ReadyContext` whose frame is genuinely short on **one** feature relative to **that
    feature's** contracted horizon still fails `INSUFFICIENT_COVERAGE`. **There is NO `AT_MOST` guard
    criterion — D34 option (a) dropped it outright (see the in-scope note above); do not write one, and do not treat
    its absence as a gap to fill.** And an
    equality assertion that the contract's mode and feature set match `model.data_requirements`' for a single-branch
    model, pinning D10a's mode/feature-set substitution as a no-behaviour-delta.

- **T8a — Flow-level policy carrier, retrying candidate task, freshness-on-fatal helper, cross-cycle preflight, D30
  discovery helper — ALL DORMANT (D11, D28, D30; split proposed under D36).**
  **T8a adds ZERO production call sites in `flows/run_forecast_cycle.py`'s cycle body.** Nothing it builds is reached
  by a running cycle: every item is a constructor, a pure helper, or a task definition that only T8b calls. Verify
  dormancy the same way the T5–T7 slice was verified — grep the flow for each new symbol and expect the only hits to
  be its definition.
  - **In scope (1) — `ForcingResolutionPolicy`, the flow-level resolution-policy carrier (BLOCKER fix, 2026-08-19).**
    `resolve_candidate` requires **`cycle_cadence_hours` AND `max_cycle_age_hours` explicitly**
    (`src/sapphire_flow/services/track_resolution.py:168`, both keyword-only, both `ValueError` if `<= 0` at `:198`),
    and the retrying candidate task requires **`RecapGatewayConfig.max_retries`**
    (`src/sapphire_flow/config/recap_gateway.py:60`). Today **none of the three reaches the services layer on any
    path**: `_build_recap_forecast_adapter` returns **only an adapter**
    (`src/sapphire_flow/flows/run_forecast_cycle.py:425-478`); only its real-client branch loads
    `RecapGatewayConfig` at all, and it stores just `max_cycle_age_hours` **privately on the adapter** (`:473`); the
    injected-client branch (`:475-478`) receives none of the three; and a **directly injected adapter** — the flow's
    own `adapter: object = None` parameter (`:1564`), short-circuited by `if adapter is None:` (`:1670`) — bypasses
    adapter construction entirely. **This is specified here, not left to the implementer**, because the plausible
    guesses (break injection, silent per-field defaults, read the adapter's private `max_cycle_age_hours`, change the
    helper's return type ad hoc, add an untyped flow parameter) are materially different runtime contracts.
    - **The type.** `ForcingResolutionPolicy` — `@dataclass(frozen=True, kw_only=True, slots=True)` per `CLAUDE.md`
      § Type Driven Development — with exactly three fields: `cycle_cadence_hours: float`,
      `max_cycle_age_hours: float`, `max_retries: int`. `__post_init__` rejects non-positive cadence/age and negative
      retries. **One object supplies all three values to the services layer.** Location: alongside the other
      services-layer forcing types (`types/forcing_track.py`), NOT in `adapters/` — **D7: the adapter must never be
      the carrier of services-layer policy**, so the resolver must not read `max_cycle_age_hours` (or anything else)
      off the adapter, private or public. The adapter's existing private `max_cycle_age_hours` (used by its own legacy
      `resolve_latest_cycle`) is untouched and is **not** a source for this policy.
    - **Construction path 1 — production (real client).** `_build_recap_forecast_adapter`'s real-client branch already
      parses `RecapGatewayConfig` (`:465`); it builds the policy from that parsed config —
      `cycle_cadence_hours` / `max_cycle_age_hours` / `max_retries` **all read from the same parsed object**. The
      helper's **return type changes** from a bare adapter to a frozen pair (adapter + policy); its single caller
      (`:1691`) unpacks both. This is the sanctioned resolution of the return-type question — do not instead widen the
      adapter or add a second config load.
    - **Construction path 2 — injected Recap client.** The injected-client branch must receive **the same policy
      object shape**, built the same way: **when `config_path` is not `None`, load `RecapGatewayConfig` and build the
      policy from it, exactly as path 1 does** (this branch loads no config today — that changes). **When
      `config_path` is `None`** — which is how the existing unit test drives it
      (`tests/unit/flows/test_run_forecast_cycle.py:1037`, `config_path=None` with a fake client) — the helper uses
      **ONE named, module-level, explicitly-documented default policy constant** (built from the same
      `DEFAULT_CYCLE_CADENCE_HOURS` / `DEFAULT_MAX_CYCLE_AGE_HOURS` the config defaults use, plus a **named** default
      retries constant, since `max_retries` has no config default) and **logs its use at construction**. It must
      **NOT** be a set of silent per-field fallbacks scattered at use sites, and it must **NOT** be a hard failure —
      failing here would break the injected-client path that existing tests and fakes depend on, which is not a change
      this task is funded to make.
    - **Construction path 3 — directly injected candidate-aware adapter.** A caller that injects an adapter never
      reaches `_build_recap_forecast_adapter`. The policy reaches the flow through **one new optional flow parameter**,
      `forcing_resolution_policy: ForcingResolutionPolicy | None = None`, on `run_forecast_cycle_flow` (`:1551`).
      When an adapter is injected and the parameter is `None`, the **same named default constant** as path 2 applies,
      logged identically. When the parameter is supplied it wins on every path, including paths 1 and 2 — so a test
      can pin an exact policy without touching config.
    - **Red-first (one per path, all three genuinely failing today — no policy type exists):** (a) production —
      a parsed `RecapGatewayConfig` carrying non-default cadence / age / retries yields a policy whose three fields
      equal those parsed values; (b) injected client with `config_path` set yields the **config-derived** policy, and
      with `config_path=None` yields the **named default constant** (asserted field-by-field against that constant,
      plus the construction log event) — the same test proves the injected-client path still constructs at all;
      (c) an injected adapter with `forcing_resolution_policy=None` gets the named default constant, and with an
      explicit policy gets exactly that object. Plus a **negative** criterion: assert the resolver call path reads
      **no** attribute off the adapter for any of the three values (D7) — e.g. an adapter fake that raises on any
      private-attribute access still resolves.
  - **In scope (2) — the retrying candidate-fetch task (D28).** Define the Prefect task that wraps
    `fetch_requirement` for one candidate cycle, with **`retries=` taken from `ForcingResolutionPolicy.max_retries`**.
    `max_retries` is parsed today but consumed nowhere in `src/`, and `_fetch_nwp_task`
    (`flows/run_forecast_cycle.py:1009-1015`) sets no `retries=` — so T4's "raise typed transients so a retry can
    fire" design has **no** consumer and a transient would terminate the track on first failure. **Defined only —
    T8b is what calls it.** *Red-first:* a fake whose fetch raises a typed transient N times then succeeds resolves
    successfully at `max_retries=N` and fails at `max_retries=N-1`, proving the configured count is the one in force.
  - **In scope (3) — the freshness-on-fatal-exit helper (Plan 116 contract).** `_emit_forecast_freshness_record` has
    exactly **four** call sites (`flows/run_forecast_cycle.py:1805`, `:2016`, `:2644` — the fatal GROUP-store path —
    and `:2744`, normal completion), and `:2744` sits **inside** the outer `try`; the `finally` (`:2791-2793`) only
    closes the HTTP client. A fatal auth/config, payload-integrity or store failure raised during per-track resolution
    would therefore escape past all four sites and emit **no** `FORECAST_FRESHNESS` record at all — the exact inverse
    of Plan 116's contract (a dark cycle that also silences its own heartbeat). T8a builds the helper that emits
    **exactly one forced-CRITICAL** record carrying the correct `forecasts_stored` count before a fatal resolution
    exit returns or re-raises. **Helper only — T8b installs it.** *Red-first:* direct tests that the helper emits one
    record, forced CRITICAL, with the count it was given, and re-raises the original exception unchanged
    (type, message and `__cause__` preserved).
  - **In scope (4) — the cross-cycle combination preflight as a PURE helper (D11).** Given a station's combinable
    results, return the single non-null forcing cycle or a mismatch. Equality is compared only across **non-null**
    forcing cycles read from each result's own forecasts' provenance (`types/forecast.py:78-83`); a **trackless**
    combinable model (`LinearRegressionDaily`, included in `combinable_results` — only explicit fallback IDs are
    excluded, `run_station_forecast.py:111-115`) is neutral, and a sole non-null cycle supplies combined provenance.
    **Pure function with direct tests only — T8b installs it at the top of the per-station persist block.**
    *Red-first:* two distinct non-null cycles report a mismatch; one non-null plus one null does not; all-null does not.
  - **In scope (5) — the D30 group-overlap discovery helper.** A pure function computing the per-track-eligible
    station set: candidate-aware adapter **AND NOT** a member of any station group. Membership is available before
    Phase A via `StationGroupStore.fetch_groups_for_station` (`src/sapphire_flow/protocols/stores.py:604`), and the
    flow holds `group_store` well before Phase A submission — it is a flow parameter
    (`flows/run_forecast_cycle.py:1562`), bound from `stores` at `:1636` and already consumed at `:1875`, while Phase A
    is submitted only afterwards (`:1943`) — so the exclusion is computable at the right moment without reordering the
    flow. **Helper only — T8b applies it.** *Red-first:* a
    station in a group is excluded from the eligible set while its non-group sibling is included, against the same
    candidate-aware adapter.
  - **Exit gate:** the focused modules, plus the whole-repo gate (`uv run pytest -q` at
    **`passed >= 4550`** with no baseline test regressing; `pyright` ≤ **432** over `src/`; `ruff check`
    with no new findings beyond the 12 pre-existing alembic `E501`s). **Plus a dormancy assertion:** grep proves the
    flow's cycle body still calls **none** of `resolve_candidate` / `commit_track` / `assemble_assignment_inputs` /
    `run_all_station_forecasts_per_track`, and every flow-level golden T8b will add is **not yet written** — T8a must
    not pre-baseline them.

- **T8b — Dispatch activation, Phase-A / D30 re-scoping, flow-level goldens, operator docs (D11, D12, D13, D26, D30).**
  **This is the FIRST task in Plan 151 that changes production behaviour.** Everything before it was dormant. The
  goldens below are the only protection for the live control-only route; **a golden that is weakened, skipped or
  re-baselined to make this build pass is a build failure, not a fix** (see *Build scope for THIS run*).
  - **In scope:** wire `flows/run_forecast_cycle.py` to project and dedup tracks, resolve each track sequentially via
    T8a's retrying candidate task + `resolve_candidate` / `commit_track`, assemble per assignment via
    `assemble_assignment_inputs`, and run via **`run_all_station_forecasts_per_track`**
    (`src/sapphire_flow/services/run_station_forecast.py:657`) — **behind the `isinstance` dispatch**. Install T8a's
    cross-cycle preflight **before EVERY in-scope persist call — there are THREE arms today, not one.**
    ⚠️ **SPEC GAP found by independent review 2026-08-26, not a renumber.** The original instruction ("before the
    individual-forecast store and the state store, not at the combination point") described a simpler structure and
    names only the **single-model** arm. `flows/run_forecast_cycle.py` now has **seven** store call sites across
    three arms:
    - **single-model arm (IN SCOPE):** forecast store `:2562`, state store `:2572`
    - **combination arm (IN SCOPE):** individual forecasts `:2636`, primary state `:2651`, combined forecast `:2676`
    - **group arm (OUT OF SCOPE):** `:2891`, `:2934` — a group-member station stays on the LEGACY path per D30's
      overlap rule, so T8b must not install the preflight there
    Installing it before `:2562`/`:2572` alone leaves the combination arm unguarded — **precisely the "partial writes
    committed" failure the instruction exists to prevent**, because a per-track-served station in combination mode
    would persist individual forecasts at `:2636` before any check ran. The preflight must gate the single-model and
    combination arms both, and must NOT gate the group arm.
    *(The durable statement of intent is T8a's own docstring at `:1718-1733`; anchor to that prose, not to the digits,
    if they drift again — this file grew 2793 → 3070 lines between 2026-08-19 and 2026-08-26.)*
    *(Re-anchored 2026-08-26: the file grew 2793 → 3070 lines after T8a/T5–T7 merged, so the 2026-08-19 numbers
    2354/2368/2386 now land on `NwpCycleSource.FALLBACK` and unrelated code. The CONTRACT is unchanged and is also
    stated in prose in T8a's own docstring at `:1718-1733` — anchor to that text, not to the digits, if they drift
    again.)* Install T8a's freshness-on-fatal helper at every
    new fatal resolution exit. Apply T8a's D30 helper to gate the dispatch **and** to re-scope Phase A so it no longer
    fetches/persists for per-track-served stations while still covering group-member stations (D7). Thread the
    `ForcingResolutionPolicy` from T8a into the resolver. The four canonical log events
    (`nwp.candidate_rejected`, `nwp.track_resolved`, `forecast.assignment_failed`, `forecast.fallback_advanced`,
    D13) are already emitted by the T5/T7 services; T8b adds only what the flow layer itself emits.
  - **NAME THE RUNNER PRECISELY (2026-08-19 correction).** T7 did **not** migrate the existing runner — it added a
    **separate** entry point, `run_all_station_forecasts_per_track`
    (`src/sapphire_flow/services/run_station_forecast.py:657`). The Design text at D10 says the two pre-run arms are
    resolved "in `run_all_station_forecasts`", which names the **legacy** function and is imprecise as built.
    **T8b calls `run_all_station_forecasts_per_track` and ONLY that.** `run_all_station_forecasts` and
    `run_station_forecast` are the **legacy** entry points, remain the legacy route's calls, and **must not be
    modified by T8b** — they are exactly the route the goldens exist to preserve. A diff touching either is a
    finding, not an implementation detail.
  - **Thread the source-derived member set into BOTH consumers (2026-08-19 correction).**
    `assemble_assignment_inputs` takes `expected_member_ids: frozenset[int] | None = None`
    (`src/sapphire_flow/services/track_assembly.py:152`), and the runner's `_assert_consistent_member_set` performs the
    exact source-set check **only when it is non-`None`** — otherwise it merely compares features against each other
    (`src/sapphire_flow/services/run_station_forecast.py:147`). **Omitting the argument compiles, passes every
    SINGLE/CONTROL golden, and silently weakens T7's defensive invariant to a no-op for a single-feature model or a
    uniformly-short ensemble.** **Rule:** the member set is obtained **ONCE per track** from
    `CandidateAwareForecastSource.expected_member_ids(track)`
    (`src/sapphire_flow/protocols/adapters.py:71`) and passed to **BOTH** `resolve_candidate(expected_member_ids=…)`
    **and every corresponding `assemble_assignment_inputs(expected_member_ids=…)` call** for that track — the same
    frozenset object, never re-derived per assignment.
  - **Overlap rule (D30) — a station that is BOTH group-member and per-track-eligible stays on the LEGACY path in
    Phase 3.** Groups contain ordinary operational station ids (`flows/run_forecast_cycle.py:2768,2774` — re-anchored
    2026-08-26 from the stale `:2457,2489`), so the two
    re-scoping rules can collide on one station. Group assembly holds no in-memory Phase A payload — it reads each
    member **from the store** at the single legacy `nwp_readback_cycle_time` (`:2553`,
    `services/run_group_forecast.py:129`) — so suppressing Phase A's write would drop that member from the group
    unless per-track resolution happened to land on the same cycle, which nothing guarantees. **Rule:** an overlapping
    station is **excluded from the per-track path** and served entirely by legacy Phase A. This follows directly from
    the ratified D8-group. **Cost:** overlapping stations get no Phase-3 benefit until Phase 4. Recorded as
    **D30-overlap-deferral**.
  - **Docs (same task):** `docs/design/forecast-cycle-redesign.md` (mark item 3 landed, item 4 pending),
    `docs/architecture-context.md` (Flow 1 per-track phase note), `docs/standards/logging.md` (the four events),
    `docs/plans/README.md`, and `docs/touchpoint-maps.md` if an existing bullet names the old superset shape. **Plus
    the operator-facing shared-track freshness note in `docs/operations/recap-gateway-runbook.md`** (D26): models
    sharing a forcing track resolve to one common cycle, so a shorter-horizon model may run on an older cycle than it
    alone required — with the reason (one `resolved_cycle` per track) and the symptom an operator would notice.
  - **The flow-level goldens — the canonical snapshot, defined (2026-08-19).** All goldens drive
    **`run_forecast_cycle_flow`** and live in `tests/unit/flows/test_run_forecast_cycle.py`, which is where the flow is
    actually exercised. The **canonical persisted-output snapshot** compared by any "byte-identical" / "unchanged"
    criterion is, for every persisted `OperationalForecast`, sorted by `(station_id, model_id)`, **exactly these
    fields**: `station_id`, `model_id`, `nwp_cycle_reference_time`, `nwp_cycle_source`, `representation`, `status`,
    `warm_up_source`, `observation_staleness_hours`, `input_quality`, `input_quality_flags`, `qc_status`, `qc_flags`,
    `combination_strategy`, `source_model_ids`, and the `ensemble`'s member ids, valid times and values —
    **plus** `forecasts_stored` and the emitted `FORECAST_FRESHNESS` records (count, level, `forecasts_stored`).
    **Excluded** as identity/clock noise: `id`, `created_at`, `updated_at`. **The baseline is FROZEN from the pre-T8
    tree** (`main` `351bac3`) and committed as data in T8b's first commit, before any dispatch code exists.
  - **Red-first / golden — ROUTING (both directions named explicitly, since "mis-routed" has no direction without
    naming the adapter).** D12/D30 route a **candidate-aware, non-group** station to per-track while **legacy**
    adapters stay legacy, so two goldens are required and each must **assert on the CALL, not only on the output**:
    - **(a) per-track route taken.** A station served by a **candidate-aware** adapter and belonging to **no** group
      MUST reach `run_all_station_forecasts_per_track` (assert the call), and its homogeneous single-track control
      output MUST equal the frozen pre-T8 canonical snapshot exactly.
    - **(b) legacy route preserved.** A station served by a **legacy** (non-candidate-aware) adapter MUST NOT reach
      **any** per-track entry point — assert that `resolve_candidate`, `commit_track`, `assemble_assignment_inputs`
      and `run_all_station_forecasts_per_track` were **all** un-called — and its output MUST equal the frozen
      pre-T8 canonical snapshot exactly.
    Output equality alone cannot distinguish these: a homogeneous station produces the same numbers on either route,
    which is precisely why a call assertion is required in both directions.
  - **Red-first / golden — MEMBER SET (R3).** An **ENSEMBLE** per-track station whose assembled frame is uniformly
    short of the source's `expected_member_ids` (every feature carrying the same wrong subset) MUST fail
    assignment-locally on the pre-run defensive assert. **This test fails if T8b omits the
    `expected_member_ids=` argument to `assemble_assignment_inputs`** — that is the specific regression it exists to
    catch, and it must be proven RED by deleting that one argument.
  - **Red-first / golden — THE TWO NEWLY-ACTIVATED FAILURE ROUTES (R4).** T8b owns the mapping from track results
    into run inputs; T7's unit tests cannot detect a flow that drops the assignment, picks the wrong reason, or
    starves the fallback chain. Both cases assert the cause **and** that the chain still produced a station forecast:
    - **Walk-back exhaustion.** `resolve_candidate` returns `None` when the bound is exhausted
      (`services/track_resolution.py:292`); the caller must construct `MissingTrackContext` (`track_assembly.py:121`, `class MissingTrackContext`).
      Golden: the affected assignment records **`MISSING_CONTEXT`** and the station's lower-priority fallback still
      **succeeds** (the station is not darkened).
    - **Accepted track, station unavailable.** A committed track can still yield a per-station
      `StationTrackUnavailable` (`services/track_resolution.py:336`), which T7 handles on a **different** arm
      (`services/run_station_forecast.py:708`). Golden: the affected assignment records **`TRACK_UNAVAILABLE`** and
      the fallback still **succeeds**.
  - **Red-first / golden — FRESHNESS ON EVERY FATAL RESOLUTION EXIT, PARAMETERISED (R5).** The criterion is
    **parameterised across four fatal classes**, each asserting **exactly ONE** `FORECAST_FRESHNESS` record, forced
    **CRITICAL**, with the correct `forecasts_stored` count: **(i) auth** (`RecapAuthError`,
    `adapters/recap_gateway.py:302`), **(ii) configuration/resolution** (`RecapConfigurationError`, `:259`),
    **(iii) payload integrity** (`RecapPayloadIntegrityError`, `:288`) — explicitly candidate-**fatal** by D7's locked
    mapping, and `resolve_candidate` catches **only** `RecapTransientError` (`services/track_resolution.py:223`), so
    every one of these propagates — and **(iv) store failure** during commit/readback. Earlier revisions tested only
    (i) and (iv); (ii) and (iii) were missing and are the ones D7 spent the most review effort pinning as fatal.
  - **Red-first / golden — CROSS-CYCLE PREFLIGHT (D11 + Plan 116).** Two combinable assignments on **different**
    cycles fail loud with **zero** forecast/state writes — and, per Plan 116's contract, that station yields
    `forecasts_stored == 0` **and** a CRITICAL `FORECAST_FRESHNESS` record **by design**: the golden must **assert**
    that record, not collide with it.
  - **Red-first / golden — HETEROGENEOUS STATION.** A heterogeneous control station produces per-assignment outputs
    (reachable only because of **D10a** — with the runner still reading the scalar `forecast_horizon_steps` it would
    fail `INSUFFICIENT_COVERAGE` inside `_run_single_model`).
  - **REGRESSION gates, EXPECTED to stay GREEN** (they preserve behaviour that already works and do **not** satisfy
    T8b's red-first requirement): same-cycle combination unchanged (tested at
    `tests/unit/flows/test_run_forecast_cycle.py:5613`,
    `TestForecastCycle::test_pooled_combination_stores_individual_and_combined` — **corrected 2026-08-26: the old
    `:5368` now falls inside `test_group_path_skips_overlapping_same_model_members`, a different concern entirely, so
    this gate was pointing at the wrong test, not merely a stale line**); the **trackless** and all-trackless combination cases; the
    **group-only** Phase A case (a group-only feature is still fetched via the re-scoped Phase A and the group
    forecast succeeds at its shared readback cycle); the **D30 OVERLAP** case (a station that is both group-member and
    per-track-eligible is routed LEGACY — absent from the resolved track set, Phase A still writes its rows, the group
    forecast succeeds at the shared readback cycle); and golden **(b)** above, the legacy-adapter deployment.
    **T8b's genuinely failing tests are at least:** golden (a) routing, the member-set golden, the two failure-route
    goldens, the four-way parameterised fatal-freshness golden, the cross-cycle preflight, and the heterogeneous
    station.
  - **Exit gate (whole repo):** the same baseline-relative gate as every task — `uv run pytest -q` at
    **`passed >= 4550`** with no baseline test regressing; `pyright` at or below the ratcheted **432**
    over `src/`; `ruff check` with no new findings beyond the 12 pre-existing alembic `E501`s. **Plus**
    `uv run pytest tests/integration/test_e2e_pipeline.py -m slow -q`, overriding the default `not slow` exclusion
    (`pyproject.toml:134` — the default `not slow` exclusion; the `slow` marker itself is declared at `:132`; `:131` is `live_recap`) — **as a RUNNER-level regression gate only.** That
    file calls `run_station_forecast` directly (`tests/integration/test_e2e_pipeline.py:653`) and never references
    `run_forecast_cycle_flow`, so it **cannot** exercise the dispatch T8b activates; it proves the legacy direct-call
    entry point still works. **The dispatch protection is the flow-level goldens above — nothing else.**

## Phase dependency graph
```json
{
  "phases": [
    { "id": "T1", "tasks": ["T1"], "parallel": false },
    { "id": "T2", "tasks": ["T2"], "parallel": false, "depends_on": ["T1"] },
    { "id": "T3", "tasks": ["T3"], "parallel": false, "depends_on": ["T1", "T2"] },
    { "id": "T4", "tasks": ["T4"], "parallel": false, "depends_on": ["T1"] },
    { "id": "T5", "tasks": ["T5"], "parallel": false, "depends_on": ["T3", "T4"] },
    { "id": "T6", "tasks": ["T6"], "parallel": false, "depends_on": ["T2", "T5"] },
    { "id": "T7", "tasks": ["T7"], "parallel": false, "depends_on": ["T6"] },
    { "id": "T8a", "tasks": ["T8a"], "parallel": false, "depends_on": ["T7"] },
    { "id": "T8b", "tasks": ["T8b"], "parallel": false, "depends_on": ["T8a"] }
  ]
}
```
**T8 is split into T8a → T8b (2026-08-19, D36 — RATIFIED by the owner; re-confirmed 2026-08-26).** The seam is
dormant-helper / activation: T8a builds the policy carrier, the retrying task, the freshness-on-fatal helper, the
cross-cycle preflight and the D30 discovery helper with **no** production call site; T8b lands the `isinstance`
dispatch, the Phase-A re-scoping, the flow-level goldens and the docs. Authoring the safety goldens in the same patch
that activates the dispatch would make an accidental re-baseline hard to detect — the same reasoning that made T1–T4
and T5–T7 independently verifiable.

**T2 and T6 both edit `adapters/forecast_interface.py`.** T6 depends on T2 transitively (T6 → T5 → T3 → T2), so the
two edits to that file are genuinely serialized — T3's explicit dependency on T2 is what guarantees it. T4 is the only
task that may run alongside T2/T3: it touches `protocols/adapters.py` and `recap_gateway.py`, disjoint from the FI
adapter.

**Atomicity re-examined (2026-08-18).** An independent sweep tested the redesign's "one atomic phase" claim and found it
holds only for **T5–T8**: a resolved cycle has no consumer until assignment-local assembly, and T8 cannot activate any
proper subset. **T1–T4 is a separable capability/preparation slice** — types, FI accessors, the pure projector/reducer,
and the source contract — that ships no behaviour change on its own. Estimated diff is dominated by **T4 (~700–1,100
lines)** and **T8 (~1,000–1,600)**, with T5–T7 the next cluster. **Consequence for PR strategy only:** the **T1–T4 / T5–T8
boundary is the single cleanest cut** — and the build **WAS** split there: T1–T4 merged as PR #182 (`707237e`);
T5–T7 merged as PR #192 (`351bac3`), with T8 split out post-hoc (D35) rather than landing with T5–T7 as this section
originally assumed. T8 is itself now split at the dormant/activation seam into T8a → T8b (D36), which is the only
change to the phase graph; the task content is unchanged by either split.

## Non-goals
- Removing the legacy station-superset path (Phase 4).
- Multi-track assignments / multi-product / mixed-mode requirements (D22).
- Group operational ENSEMBLE fan-out; group CONTROL stays on the legacy path in Phase 3.
- Member survival — v1 is all-members-or-fail.
- Hindcast/operational parity.
- Richer combined-forecast provenance for differing cycles; write-side per-assignment state; concurrent track fetch.
- MeteoSwiss migration to `fetch_requirement`.

## Open items

**Requires owner re-ratification (staleness, 2026-08-18):** `main` moved under three ratified decisions. Neither reopens
a design fork; each changes a ratified decision's **observable consequence**, so it is surfaced, not absorbed.
- **D21-sweep-failure-mode.** *Old:* a multi-product / mixed-mode requirement **hard-aborts** at registration.
  *New fact:* the sweep must raise `UnsupportedModelRequirementError`, not `ConfigurationError` (D4) —
  `discover_models()` re-raises the latter for **every** entry point (`services/model_registry.py:116-121`; Plan 156's
  reasoning at `adapters/forecast_interface.py:523-531`).
  *Consequence to re-ratify:* the offending entry point is **SKIPPED** with
  `log.exception("model_discovery_unsupported_requirement")` while every other model loads — a **log line plus a
  missing model**, not a hard abort. D21's *preference* (global sweep over option B) is unaffected. A true hard abort
  would need a separate onboarding-time check outside `discover_models()`.
- **D32-multibranch-delivery** (new constraint, collides with D2 / T2 / T6). *Old:* D2 rests on a one-future-forced +
  one-past-only model with the selected branch authoritative; T2's red-first asserts two branches expose separately.
  *New fact:* `_assert_single_deliverable_dynamic_branch` (`adapters/forecast_interface.py:997-1021`, called from
  `_station_inputs_from_frames` `:1032` and `predict`/`predict_batch` at `:766`/`:811`) raises
  `UnsupportedModelRequirementError` at predict/train time for **any** >1-dynamic-branch requirement; its comment
  (`:998-1010`) records this shape as "ACCEPTED at construction (Plan 151 T2 needs that shape constructible)".
  *Consequence:* D2's shape is **constructible but cannot DELIVER inputs today**. Two
  options — **this plan does not decide**: **(a)** relax the assert for that pair (what 156 anticipated); *cost:* T6
  must then really deliver the past-only branch — Plan 153's multi-resolution work, real scope on an atomic phase.
  **(b)** accept **construct-only** and narrow T2's red-first to construction-time assertions; *cost:* D2's
  selected-branch rule stays unexercised end-to-end until Plan 153, and T6's branch-specific past-variable / `max_nan`
  criteria must be restated against a single-branch model.
- **D34-atmost-guard-unreachable** (new, 2026-08-18; collides with D10a / T7). *Old:* D10a records per-feature
  `AT_MOST` floors as an accepted cost and funds a T7 **route-time guard** that fails loud if a per-track-eligible model
  declares divergent per-variable floors. *New fact:* that guard is **unimplementable as specified**, for **two
  independent structural reasons** (both verified 2026-08-18, detail in D10a): **(i)** the repo pins ForecastInterface
  **v0.1.19** (`pyproject.toml:104`), whose installed package has neither `horizon_semantics` nor `min_future_steps`, so
  `_model_declared_floor` returns `None` at its "not declared" branch for every model
  (`services/horizon_semantics.py:103-106`); and **(ii)** even after an FI bump, the discovered model is the
  **`ForecastInterfaceAdapter`**, which stores the raw model privately (`adapters/forecast_interface.py:450`) and has no
  `__getattr__` passthrough (`:462-473`), so `getattr(model, "input_requirement", None)` is `None` — confirmed at
  runtime for all four FI entry points. A guard proved green against a **raw** FI fake would therefore prove nothing
  about the production route, and reading `self._model` from the runner would violate the single-FI-boundary rule
  (`CLAUDE.md`). *Consequence — two options, and **this plan does not decide**:*
  **(a)** **Drop the T7 guard**, record the double-deadness in D10a, and attach a revisit **trigger**: *revisit when FI
  >= v0.1.20 lands AND a per-track-eligible model declares divergent `min_future_steps`.* *Cost:* no fail-loud on the
  day that trigger fires; the wrong-floor trap stays documented but unguarded.
  **(b)** **Add a public per-variable horizon-semantics accessor at the FI boundary** — T2 scope, since T2 already owns
  the pre-collapse accessors — and have the guard consume that instead of `getattr` on the model. *Cost:* real net-new
  scope on T2 and a **widened** FI boundary surface, funded for a code path that is currently **doubly** unreachable.
  *Plan author's recommendation: **(a)**.* Option (b) funds a boundary accessor for a path no model can reach today —
  precisely the gold-plating this document's reconstruction exists to avoid.
- **D35-t8-scope-split** (new, 2026-08-19; raised by an independent post-implementation review pass, collides with the
  *Build scope for THIS run* header's own "T5–T8 is genuinely inseparable" claim). *Old:* this document's own
  "Build scope for THIS run" section stated unconditionally that the run implements T5, T6, T7 **and** T8 together,
  and that T8 cannot activate any proper subset of T5–T7. *New fact:* the commit that actually landed
  (`3481179` + fixer round `52a7c73`) implements **only** T5–T7; `flows/run_forecast_cycle.py` has zero references to
  any new T5–T7 entry point, so the committed code is dormant behind the existing `isinstance` dispatch exactly as the
  inseparability argument predicted it would need to be for a T1–T4-style dormant cut — the difference is this cut
  was decided **inside a fixer round**, after the fact, rather than through a re-reviewed scope change to the READY
  plan before implementation started. *Consequence — two options, and **this plan does not decide**:*
  **(a)** **Ratify the split.** Accept T5–T7 as this run's actual, final scope; keep T8 in this plan document as a
  fully-specified follow-on task, to be built in its own `implement` pass (the Fixer round's recommendation, given
  T8's own risk profile — D11 preflight semantics, D26 freshness heartbeat, D30 group/per-track overlap, D28 retries,
  and five documentation files — is materially different from a fixer patch). *Cost:* Plan 151 as a whole spans two
  separate implementation passes instead of one, and the original "atomic, T8-cannot-activate-a-subset" framing this
  document argued for T5–T8 no longer describes what was actually built.
  **(b)** **Reject the split.** Hold this commit un-merged until a fresh, combined T5–T8 pass lands together, as the
  original *Build scope for THIS run* section intended. *Cost:* T8's ~1,000–1,600 lines (preflight, freshness,
  overlap, retries, docs) get built and reviewed under the same time pressure that produced the two blockers and six
  majors the fixer round already had to resolve in T5–T7 alone, with no intervening independent review checkpoint.
  *Plan author's recommendation: **(a)**.* The T5–T7 code is dormant and the golden tests keep control-only forecasting
  green regardless of which option is chosen (see *Build scope for THIS run*); splitting lets T8 get its own
  red-first/independent-Codex-review loop instead of inheriting a fixer round's time pressure, which is the workflow
  this repo's own `docs/workflow.md` § Multi-Model Review mandates for non-trivial changes.
- **D36-t8ab-split** (new, 2026-08-19; raised by an independent Codex review of the T8 task specification, which
  returned **NO — not ready to build** with one blocker, four majors and two minors, all now folded into T8a/T8b).
  **OWNER-RATIFIED 2026-08-19 — SPLIT ACCEPTED.** T8a and T8b build as two separate tasks, two `implement` passes,
  two PRs. *Ratified proposal:* split T8 at the **dormant-helper / activation seam** —
  **T8a** builds the `ForcingResolutionPolicy` carrier and its three construction paths, the `retries=` wiring, the
  freshness-on-fatal-exit helper, the cross-cycle preflight and the D30 discovery helper, each with direct tests and
  **zero production call sites** from the flow; **T8b** lands the `isinstance` dispatch, the Phase-A / D30 re-scoping,
  every flow-level golden and the operator docs. **This splits only EXISTING T8 scope — nothing is added.**
  *Rationale:* authoring the safety goldens inside the same large patch that activates the dispatch makes an
  accidental re-baseline hard to detect — a golden written and "confirmed" in the same diff that changes the
  behaviour it pins proves very little. The seam is the one that made T5–T7 (and T1–T4 before them) independently
  verifiable: dormant first, activation second. *Cost:* two `implement` passes instead of one for T8. *Plan author's
  recommendation: **split**.* — accepted. **THIS RUN BUILDS T8a ONLY**; T8b follows in a separate PR off the merged
  T8a base.

**Resolved (owner-ratified 2026-08-10 unless noted):**
- **D1-types-location** — new `types/forcing_track.py`; `ModelRunContext` stays service-local (Plan 148).
- **D2-survival** — all-members-or-fail; member survival is a follow-on.
- **D3-mapping** — `TRACK_UNAVAILABLE` = track resolved but this station unavailable; `MISSING_CONTEXT` = the track
  never resolved.
- **D5-combine** — fail-loud, as a preflight before any per-station write.
- **D6-adapter-scope** — migrate `recap_gateway` only.
- **D7-walkback** — bounded solely by the existing `max_cycle_age_hours`.
- **D8-group** (routing; unrelated to Design D8, which is the completeness predicate) — group stays on the legacy
  superset path in Phase 3.
- **D9-records-type** — `StationTrackAvailable.records` is the station-keyed `WeatherForecastRecord` list from readback
  at the track's single `resolved_cycle` (singular by D5/D26 — no per-assignment readback source).
- **D10-completeness-pre-extract** — for the pre-extracted recap path the gate boundary is **before persist**; the
  spec's "before extraction" wording assumes a gridded candidate. The same wording assumes a single global member axis,
  which a station-keyed pre-extracted candidate does not have (`recap_gateway.py:821-973`), so the gate is stated
  per `(station, feature, member, horizon)` (D8). A spec-precision clarification, not a silent divergence; the
  spec-wording fix rides along with T1's spec edit.
- **D28-max-retries-wiring** (new, 2026-08-11 review; **superseded by the Codex gate — now IN SCOPE at T8a**)
  — `RecapGatewayConfig.max_retries` (`config/recap_gateway.py:51`) is parsed and stored but wired to nothing: no
  `@task(retries=...)` reads it anywhere in `src/`, and `_fetch_nwp_task` (`flows/run_forecast_cycle.py:1009-1015`)
  sets no `retries=` (re-verified 2026-08-18 — unchanged). It was
  briefly cut from T4 as flow-layer work — correct about the *layer*, wrong to defer it: T4's design **raises** typed
  transients so a retry can fire, which without a consumer means a single transient terminates the track. **T8a sets
  `retries=` on the candidate-fetch task from this field, via `ForcingResolutionPolicy.max_retries`.** It stays out of
  T4 (not a `CandidateAwareForecastSource` conformance concern), but it is a Phase 3 acceptance gate at T8a.
- **D29-per-station-cycle-deferred** (new, 2026-08-11 review) — a station that is incomplete at the track's accepted
  cycle gets **no walk-back of its own** (D8): its complete siblings establish the cycle, it is reported
  `StationTrackUnavailable(INCOMPLETE_AT_CYCLE)`, and its fallback chain advances. Per-station cycle resolution is a
  named follow-on in the ratified design (group-CONTROL round-3 note, `docs/design/forecast-cycle-redesign.md`) and is
  unrepresentable under the locked single `TrackFetchResult.resolved_cycle` (`docs/spec/types-and-protocols.md:3313-3316`).
  The alternative — rejecting the whole candidate when *any* in-scope station is incomplete — was rejected because it
  lets one station darken every sibling, contradicting D7's containment rule.
- **D12-nwp-source-str** — `ForcingTrackKey.nwp_source` is the existing `str`; no `NwpSource` type exists in the repo.
- **D13-runtime-checkable** — the Protocol is realized `@runtime_checkable`, matching every other capability Protocol
  in `protocols/adapters.py`.
- **D22-single-track** (owner, 2026-08-11) — exactly one forcing track per assignment. Multi-product / multi-channel /
  mixed-mode requirements are deferred to a follow-on triggered by the first FI model that declares one; Phase 3
  rejects them at registration (D4). The question "must a deterministic snow track resolve to the same cycle as its
  paired ensemble track?" is **moot for Phase 3** and inherited by that follow-on. Two limitations recorded so the
  follow-on inherits them: (a) `ForcingTrackKey` carries **no product identity**, so a migrated source serves its own
  NWP channel for every track — a model declaring a single **non-NWP** product would pass the guard and be served the
  wrong rows (unreachable today: zero snow-consuming models); (b) `ForecastProvenance` carries exactly **one**
  `nwp_cycle_reference_time` (`types/forecast.py:34-45`), so a multi-track assignment either resolves its tracks in
  lockstep or the follow-on must first fund a multi-reference provenance type.
- **D24-cadence-source** (owner, 2026-08-11; **corrected after review**) — the walk-back candidate cadence comes from
  **configuration**, not a third Protocol member. The original wording added "recap already carries the constant it
  needs"; that is **withdrawn** as self-contradictory — an adapter-private constant is not a configuration contract,
  and `config/recap_gateway.py:29` has **no** cadence field today. **T4 adds and validates**
  `RecapGatewayConfig.cycle_cadence_hours`; **T5** defines the resolver parameter that consumes it and **T8a** threads
  it on all three construction paths inside one `ForcingResolutionPolicy`. *(Task assignment corrected 2026-08-18 —
  T4 cannot thread a bound into a `services/` resolver that T5 has not created yet, and D7 forbids parking the policy on
  the adapter. The DECISION — cadence from configuration, not a third Protocol member — is unchanged and NOT reopened.)*
  (Re-verified 2026-08-18: still no cadence field.)
- **D26-horizon-axis** (owner, 2026-08-11; **revised 2026-08-11 after review**) — per-feature horizons are
  **retained**. Collapsing to a scalar was considered and rejected: per-variable horizons are named inside
  build-sequence item 3 of the ratified redesign, and the FI `max` collapse flattens per-variable `future_steps`
  **within one branch, across every variable and product** (`adapters/forecast_interface.py:514-521`) — precip 2 steps
  and temp 10 steps in one branch still become a scalar 10. *(Grounding corrected 2026-08-18: the original wording
  leaned on the **cross-branch** form, which Plan 156 eliminated; the within-branch collapse alone carries it — **the
  ratified decision is UNCHANGED and not reopened**.)*
  **Reversal (the review was right):** the earlier form of this ruling also rejected "accept at the group max" and
  required acceptance per consuming assignment. That is **not realizable** under the locked contracts this plan commits
  to realizing (line "This plan REALIZES locked contracts"): the spec pins the completeness gate to
  `ResolvedTrackRequest.fetch_horizons`, the MAX (`docs/spec/types-and-protocols.md:3289-3294`), and gives
  `TrackFetchResult` a **single** `resolved_cycle` (`:3313-3316`) — two assignments sharing a track cannot settle on two
  cycles. Per-consumer acceptance would have been a genuine **spec change** (a per-assignment or per-horizon-class
  cycle in `TrackFetchResult`, a per-assignment readback/provenance source in D9, and a matching walk-back loop),
  which Phase 3 explicitly does not fund. **Ratified position:** fetch at the group max, accept at the group max, slice
  per assignment at assembly (D5, D8, D9). **Cost:** a 5-day assignment sharing a track with a 10-day assignment
  inherits the older cycle the 10-day one needed — a freshness cost, not a correctness one, and moot in practice under
  the v0/v1 `{tp, t_2m}` allowlist (D20) where real assignments share horizons. Per-assignment cycles remain available
  as a future spec change if a deployment ever shows the freshness loss to matter.
  **OWNER-RATIFIED 2026-08-11: the freshness cost is ACCEPTED.** The reversal of the per-consumer-acceptance clause
  stands; the per-feature-horizon retention clause of the original ruling is untouched. **Because this is
  operator-visible behaviour** — two models sharing a forcing track always resolve to the *same* cycle, so a
  shorter-horizon model can consume an older cycle than it strictly required — **T8b documents it in the
  operator-facing `docs/operations/recap-gateway-runbook.md`**, so the behaviour is discoverable when someone asks why
  a 5-day model ran on yesterday's cycle. Per-assignment cycles remain available as a future spec change if a
  deployment ever shows the freshness loss to matter.

**Deferred / carried:**
- **D20-union-dedup-residual** — feature-set **union** dedup (fetching a superset once to serve a narrower assignment)
  is deliberately out of scope; requirements dedup only on **equal** feature sets. Moot under the v0/v1 `{tp, t_2m}`
  allowlist. Carries the spec's own `RESIDUAL (human confirm)` note.
- **D21-conformance-sweep-scope — RESOLVED (owner-ratified 2026-08-11): option (A), keep the GLOBAL sweep.** T2's
  sweep runs for every FI model at discovery time, so the supported-subset narrowing applies **repo-wide**, including
  to models assigned only to legacy-adapter or group stations. Deliberate fail-fast: an unsupported requirement fails
  at onboarding rather than mid-cycle. **Trigger:** a real deployed model declaring a multi-product or mixed-mode
  requirement. **Ratified PREFERENCE (unaffected by the 2026-08-18 correction):** the global sweep over option (B)
  (gating the sweep behind the migrated-adapter dispatch), which would defer the identical failure to mid-cycle on a
  live station. **Ratified CONSEQUENCE — CORRECTED 2026-08-18:** not a hard abort but a
  per-entry-point SKIP at discovery (D4's exception type); stated in full under *Requires owner re-ratification*.
  Nothing in-repo trips it today: the three `nwp_regression`-family FI models each declare exactly one `future_known`
  product (`models/nwp_regression.py:211,:223`).
  - **aquacast (Plan 152) — CHECKED 2026-08-18; the check becomes STANDING.** Live introspection of
    `CmalPoolPT().input_requirement` (extra installed): ONE `time_step` branch (daily), ONE spatial rep
    (`BASIN_AVERAGE`), ONE `future_known` product (`'aquacast'`), uniform `EnsembleMode.SINGLE`, uniform
    `future_steps=15` — **the D4 sweep ACCEPTS it**. Caveats: (a) its `future_known` **passes through** the external
    `aquacast` package's `InputRequirement` (`models/aquacast/_shim.py:156-171,194-196` preserve upstream product
    keys), so conformance is **not statically verifiable from this repo** and can change on an upstream bump with no
    repo change — a standing/CI-visible check, not a one-time sign-off; (b) it is `ArtifactScope.GROUP`, landing on the
    legacy GROUP path Phase 3 excludes while the **global** sweep still runs over it — D21 as ratified, not a defect.
    Its horizons are uniform, so it is **not** a useful T2 per-feature-horizon fixture.
- **D23-output-horizon-deferred** — `OutputHorizon` is not created in Phase 3: it has no consumer here, and a
  zero-caller dataclass is dead code. Its consumer is the phase that emits per-assignment output horizons (Plan 148's
  deferred write-side follow-on). T1's spec edit records the deferral.
- **D25-group-snow-scoping** — `_compute_required_snow` (`flows/run_forecast_cycle.py:852-881`) excludes group
  requirements, as its own docstring records (`:866-868`). Phase 3 **does not fix this**: it is pre-existing on `main`,
  unconnected to per-track migration, and has zero observable effect today (no model consumes snow). Phase 3 takes only
  the group **membership** pre-discovery its Phase A re-scoping needs (D7). The requirement merge folds into the
  group-ensemble follow-on.

**Resolved by the Codex gate (2026-08-11) — decisions, not deferrals:**
- **D27-polygon-parser-fail-loud — RESOLVED here** (superseding the earlier "resolve during
  implementation" note, which contradicted D7's claim that this is decided). Two distinct cases, split by cause:
  - **Missing polygon column** (a data gap) → **per-station** `StationTrackUnavailable`, new reason
    `MISSING_POLYGON_COLUMN` (T1). The diagnostic that the existing batch-wide raise carried — affected station ids and
    polygon names — is preserved in the **structured WARNING log event**, not in the type: `StationTrackUnavailable`
    carries an enum-valued `reason` with no free-text payload (`docs/spec/types-and-protocols.md:3310`), and Phase 3
    does not widen it. Nothing is silently dropped: the station is explicitly recorded unavailable.
  - **Duplicate-polygon resolution** (a misconfiguration) → **unchanged** candidate-wide `RecapConfigurationError` with
    zero Gateway calls, locked by a T4 red-first test.
- **D28-fetch-retries — IN SCOPE at T8a** (superseding the earlier "cut/deferred" note). T4's design raises typed
  transient transport errors so a retry can fire; that requires a real consumer, so T8a sets `retries=` on the
  candidate-fetch task from `RecapGatewayConfig.max_retries` (`config/recap_gateway.py:51`, parsed but consumed nowhere
  today; `flows/run_forecast_cycle.py:1009-1015` sets no `retries=`). Without it a single transient terminates the track.
- **D33-runner-forcing-contract** — the runner's three forcing reads come from the context contract on the per-track
  route; see Design D10a, in scope at T7.
- **D31-candidate-ownership — SPEC EDIT, folded into T1** (reviewer blocker-fix). The spec pins
  `fetch_requirement(...) -> CandidateFetchResult` (`docs/spec/types-and-protocols.md:3210-3213`) whose `status` is
  the **completeness** taxonomy (`:3318-3324`), but the adapter cannot judge completeness — the threshold is a `services/`-side
  `ResolvedTrackRequest.fetch_horizons` (D8). Declaring both would leave two incompatible return contracts. Phase 3
  therefore **edits the spec** (T1 already owns the spec edits, per the docs-with-code rule): `fetch_requirement`
  returns a **raw typed fetch outcome** (per-station payload + transport classification), and `services/` constructs
  the immutable `CandidateFetchResult` after the per-station predicate. **OWNER-RATIFIED 2026-08-11** — the locked
  Protocol is unimplementable as written (the adapter cannot see the services-side threshold), so this spec edit is
  approved and lands in T1. Surfaced and ratified, never silently diverged.

**Verified (2026-08-18, no longer an assumption):** `AssignmentFailureCause` (`services/run_station_forecast.py:72`,
8 members) is consumed by **no** exhaustive `match` in the repo — the only nearby `match` is on the `AssignmentOutcome`
union (`:497-502`), and every `src/` use is a construction site. The two additive members need **no** new match arms.

## Fixer round (post-implementation review fold-in, 2026-08-18)
An independent Codex pass over the committed T5–T7 diff (`3481179`) plus a Claude design pass raised two blockers,
six majors, and two minors. All findings against T5–T7 (the committed slice) are resolved below; the one finding
about T8 is recorded as **explicitly deferred**, not resolved, for the reason stated.

- **Blocker — T8 (flow wiring, golden tests, docs) is absent.** Correct as reported: `flows/run_forecast_cycle.py`
  has zero references to `resolve_candidate`/`commit_track`/`assemble_assignment_inputs`/
  `run_all_station_forecasts_per_track` — only tests call T5–T7. **NOT resolved in this fixer round, by design.**
  T8 is its own phase in the plan's dependency graph (`depends_on: ["T7"]`), separately estimated at ~1,000–1,600
  diff lines (dominant among all eight tasks), and its own task text specifies a materially different risk profile
  than a fixer patch is suited for: precise cross-cycle preflight semantics (D11), a fail-loud freshness heartbeat
  that must honour Plan 116's existing contract (D26) at every new fatal exit, a group/per-track overlap rule
  (D30-overlap-deferral) with a demonstrated history of getting the direction wrong during design review, configured
  retries threaded from `RecapGatewayConfig.max_retries` (D28), and five documentation files. Building this from a
  review-findings list — without the red-first/independent-Codex-review loop this repo's own workflow mandates for
  non-trivial changes (`docs/workflow.md` § Multi-Model Review; `CLAUDE.md` "Multi-model review is mandatory for all
  non-trivial plans and patches") — risks exactly the kind of silent contract violation (D26/D30) the plan spent
  substantial review effort pinning down precisely. **Recommendation:** run T8 as its own `implement` pass against
  this plan's existing T8 task text (already fully specified, phase-gated, with its own red-first criteria and exit
  gate) rather than folding it into a fixer round on top of T5–T7.
- **Blocker — all-members-at-horizon not enforced (staggered valid_times).** `_station_complete` compared per-member
  step *counts*, so member 0 covering days 1–2 and member 1 covering days 3–4 each individually satisfied a two-step
  requirement while sharing no common valid_time — `_filter_and_cap_daily_records`'s downstream earliest-N-times cap
  would then silently retain one member's days and drop the other member entirely. **Fix:** `reduced_daily_step_times`
  (renamed from `reduced_daily_step_counts`, which is now a thin wrapper) returns the actual valid_time *set* per
  `(parameter, member_id)`; `_station_complete`'s ENSEMBLE branch now requires every expected member's earliest
  `horizon.value` valid_times to be the *identical* set, not just of adequate count. The runner's defensive
  `_assert_consistent_member_set` also gained an `expected_member_ids` parameter (threaded through
  `ForcingContract.expected_member_ids` ← `assemble_assignment_inputs(expected_member_ids=...)`) so a single-feature
  or uniformly-partial-across-features model is checked against the source's ground-truth member set, not merely
  against itself. Locked by `test_staggered_member_valid_times_rejects_candidate_and_walks_back`
  (`tests/unit/services/test_track_resolution.py`) and
  `test_single_feature_partial_ensemble_checked_against_expected_member_ids`
  (`tests/unit/services/test_run_station_forecast_per_track.py`); both proven RED against the pre-fix code.
- **Major — typed `RecapTransientError` was not classified TRANSIENT.** `resolve_candidate` let a
  `RecapTransientError` (raised only once the source's own retry budget is exhausted) propagate uncaught instead of
  walking back like an `ABSENT_AT_CYCLE` candidate. **Fix:** `resolve_candidate` now catches `RecapTransientError`
  specifically, logs `nwp.candidate_rejected` with `reason="transient_error"`, and continues to the next older
  candidate; every other exception still propagates uncaught (D31). Locked by
  `test_transient_error_walks_back_to_older_complete_candidate` (RED against pre-fix), paired with
  `test_fatal_auth_error_still_propagates_uncaught` (a regression guard — passes before and after; proves the
  carve-out did not widen to swallow fatal errors).
- **Major — `RawFetchOutcome.missing_polygon_column` was discarded (D27).** A station the adapter explicitly excluded
  for a missing HRU polygon column fell through to the generic `NO_DATA_AT_CYCLE`, losing the diagnostic D27 was
  written to preserve. **Fix:** `AcceptedCandidate` gained a `missing_polygon_column: frozenset[StationId]` field,
  populated in `resolve_candidate` from the accepted outcome; `commit_track`'s per-station fallback checks it before
  defaulting to `NO_DATA_AT_CYCLE`. Locked by `test_missing_polygon_column_station_gets_specific_reason`; proven RED
  against the pre-fix code.
- **Major — `commit_track` could "resurrect" unrelated pre-existing rows.** Readback was keyed only on
  `(station_id, nwp_source, cycle_time)`, so a station absent from *this* candidate's results but with old rows
  already in the store for the same key (a different track, or a legacy write) would be misclassified
  `StationTrackAvailable`. **Fix:** readback is now attempted only for stations in
  `accepted.complete_station_records`; every other in-scope station is classified from `incomplete_at_cycle` /
  `missing_polygon_column` / the generic fallback without touching the store. `commit_track` also gained a
  `track_features` parameter so readback is filtered to this track's own features. Locked by
  `test_commit_track_does_not_resurrect_unrelated_preexisting_rows`; proven RED against the pre-fix code.
- **Major — one scalar horizon capped every feature.** `build_future_dynamic_frame` /
  `_filter_and_cap_daily_records` applied one `forecast_horizon_steps` to every parameter, so a precip=2/temp=10
  assignment retained 10 values for both. **Fix:** both functions accept an optional `feature_horizons: dict[str,
  int]` that caps each parameter to its own horizon independently (`None`, the default and every legacy caller,
  preserves the old scalar behaviour byte-for-byte); `assemble_assignment_inputs` now derives and passes it. Locked
  by `test_per_feature_horizon_caps_each_column_independently` (precip retains 2 non-null values, temp retains 10 on
  the same 10-row frame); proven RED against the pre-fix code.
- **Major — warm-up state was loaded twice.** `assemble_assignment_inputs` (T6) built a full `ModelRunContext`
  including a `load_warm_up_state` call, which `_run_single_model` (T7) then discarded and reloaded from scratch —
  doubling the store read and moving the first read's failure mode outside the assignment-local
  `WARM_UP_LOAD_FAILED` path. **Fix:** `ReadyContext` now carries loose `inputs` / `observation_staleness_hours` /
  `nwp_age_hours` fields instead of a prebuilt `ModelRunContext`; `assemble_assignment_inputs` no longer loads
  warm-up state at all — `_run_single_model`'s existing assignment-local load is the sole owner. Locked by
  `test_assembly_and_runner_share_exactly_one_warm_up_load` (asserts `state_store.accessed_model_ids == [_MODEL_HIGH]`
  over the full T6→T7 pipeline); proven RED against the pre-fix code (the pre-fix pipeline reads state twice).
- **Minor — the NaN-gate slice lacked its own lock.** T6's red-first criterion asserts the past-/future-known
  tolerance-map slice at *both* the `InputSeries`-construction site and the NaN-gate site independently, but only
  the construction-site test existed; the gate-side slice (already correct, landed under T2) had no test that would
  fail if it regressed. **Fix (test-only — no code defect):** two new tests in
  `tests/unit/adapters/test_forecast_interface_adapter_nan_gate.py` — trailing NaNs outside a two-step feature's
  declared horizon must not trip `max_nan=0`, paired with a NaN inside the declared horizon that still must.
- **Minor — `_forecast_result_to_records` didn't document which legacy conversion site it mirrors.** **Fix:** a
  docstring on the function now names the pre-extracted-dict site (`flows/run_forecast_cycle.py:1356`) it matches,
  not the gridded/extracted site (`:1275`).

**Test soundness:** every correctness-fix locking test above was confirmed to fail against the pre-fix code by
reverting the `src/` half of the fix (`git apply -R` on the isolated production-code patch, keeping the new tests),
running the affected tests (8 of 9 new/changed locking tests failed as expected; the ninth,
`test_fatal_auth_error_still_propagates_uncaught`, is a regression-guard pair that is correctly green both before and
after), then restoring the fix and re-confirming full green.

**Exit gate:** `uv run pytest -q` full suite green, `uv run pyright src/` at 404 (ratchet ceiling 432, no new
findings), `uv run ruff check .` clean beyond the 12 pre-existing alembic `E501`s.

## Second fixer round (doc-only, 2026-08-19)
An independent Codex pass over the committed diff (including the first fixer round's own addendum, `52a7c73`) raised
one major: the *Build scope for THIS run* header (originally lines 79–93) stated unconditionally that "This run
implements T5, T6, T7 and T8" and that T8 "cannot activate any proper subset" of T5–T7, while the Fixer round section
appended 60+ lines later self-disclosed that T8 was "NOT resolved in this fixer round, by design." A reader who stops
at the header is told all four tasks land together; only the addendum reveals the actual T5–T7-only scope. The split
itself was correct and well-reasoned (T5–T7 is dormant behind the existing `isinstance` dispatch, so control-only
forecasting stays green regardless), but it was decided unilaterally inside a post-hoc fixer-round note rather than
through a re-confirmed scope change to the READY plan.

- **Major — scope-statement / actual-diff mismatch.** **Fix (doc-only — no code defect):** the *Build scope for THIS
  run* header now states the actual committed scope (T5–T7; T8 split out) instead of the original T5–T8 claim; the
  original inseparability argument is retained for provenance but marked superseded in observable scope; the *Status*
  section's "SECOND SLICE LANDED" line and the *Exit gate for this run* line were corrected to match; and a new
  **D35-t8-scope-split** entry was added under *Open items → Requires owner re-ratification* so the human owner has an
  explicit point to confirm the split before a fresh `implement` pass targets T8, per this repo's own multi-model
  review convention (`docs/workflow.md` § Multi-Model Review). No `src/` or `tests/` files changed — this finding was
  entirely a plan-document consistency defect, not a code defect, so no locking test applies.

**Exit gate:** unchanged from the first fixer round (no code touched); re-confirmed `uv run pytest -q` full suite
green, `uv run pyright src/` at ratchet ceiling, `uv run ruff check .` clean beyond the 12 pre-existing alembic
`E501`s.

## Third fixer round (T8a review fold-in, 2026-08-19)
An independent Codex pass over the committed T8a diff (`5d921ee`) plus a Claude design pass raised one blocker, two
majors, and one minor — all against T8a's own committed code, not a T5–T7 regression. All four are resolved below.

- **Blocker — `_slice_to_future_steps` sliced the UNION frame's positions, not the variable's own timestamps.**
  `future_dynamic` is a per-assignment frame that may carry several future_known variables pivoted onto one shared
  `timestamp` axis; when two variables have DIFFERENT own timestamp sets (D9 permits this — e.g. a 2-day precip
  variable sharing a track with a 10-day temp variable, at genuinely staggered valid_times, not just a shorter
  prefix), the short variable's cells at every union timestamp it does not itself declare are structural nulls the
  pivot introduced. `head(future_steps)` on the union's timestamp-sorted rows could therefore select one of those
  structural nulls ahead of the variable's own later-but-real value, either falsely tripping `max_nan` or silently
  delivering an incomplete series with the wrong values. **Fix:** `_slice_to_future_steps` takes a `name` parameter
  and filters to that column's non-null rows *before* sorting/capping, applied identically at both call sites (the
  `InputSeries`-construction site in `_future_known_inputs` and the NaN-gate site in
  `_variables_over_nan_tolerance`) so the two stay consistent. A genuine in-window NaN (an IEEE float NaN, distinct
  from a Polars null) still survives the filter and is still counted by the NaN gate — only structural absence is
  removed. Locked by
  `test_predict_slices_a_variable_to_its_own_timestamps_not_the_union_frame`
  (`tests/unit/adapters/test_forecast_interface_adapter_nan_gate.py`), the first fixture in this test module to give
  two future_known variables genuinely staggered (not just differently-truncated) timestamp sets; proven RED against
  the pre-fix code (it raised `ModelOutputError` before reaching its own assertions).
- **Major — `fetch_forcing_candidate_task` had no `retry_condition_fn`, so Prefect retried every exception.** Once
  T8b sets `retries=` from `ForcingResolutionPolicy.max_retries`, Prefect's default behaviour retries on ANY raised
  exception — including the fatal typed taxonomy D31 declares non-retryable (`RecapAuthError`,
  `RecapConfigurationError`, `RecapPayloadIntegrityError`) and any unanticipated bug, burning the whole retry budget
  on an error that will never succeed and delaying the walk-back that should have handled it instead. **Fix:** a new
  `_retry_only_recap_transient_error` reads the failed state's carried exception (`state.data`) and returns
  `isinstance(state.data, RecapTransientError)`, wired as the task's `retry_condition_fn`. Locked by
  `TestFetchForcingCandidateTaskRetries.test_fatal_and_unexpected_exceptions_are_never_retried` (parametrized over
  `RecapAuthError`/`RecapConfigurationError`/`RecapPayloadIntegrityError`/a plain `RuntimeError`, asserting exactly
  one call each even with a generous `retries=3` budget); proven RED against the pre-fix code (all four raised the
  wrong exception type after a spurious retry instead of failing on the first call).
- **Major — `_station_complete`'s SINGLE branch did not enforce the EXACT source-derived member set.** It originally
  took the max row-count over EVERY `member_id` present, so a stray/foreign member (e.g. a mis-tagged member 7, or a
  second run mixed into the same candidate) could satisfy completeness even though the source's OWN declared SINGLE
  identity (`expected_member_ids` — `{0}` for Recap) never appeared, or appeared short. A first fix FILTERED to
  `member_id in expected_member_ids` before taking the max; a follow-up review showed filtering is not the contract —
  the spec (`docs/spec/types-and-protocols.md`, "carries member_ids == source.expected_member_ids") requires
  EQUALITY. Under filtering a candidate carrying a COMPLETE member 0 **beside** a COMPLETE foreign member 9 was
  ACCEPTED, both runs were persisted, SINGLE assembly silently dropped the foreign run
  (`operational_inputs._pivot_nwp_records`), and — the candidate having been accepted — walk-back to a clean older
  candidate never happened. **Fix:** the mode branch is gone; ONE gate now serves both modes — the present (non-null)
  member set must EQUAL `expected_member_ids`, then each expected member's series must reach the horizon at the same
  retained valid_times (degenerate for a one-element SINGLE identity). Rejection is walk-back-eligible, exactly like
  a short series. `resolve_candidate` additionally rejects an EMPTY `expected_member_ids` with a `ValueError` (a
  source declaring no member identity is fatal config, not a candidate outcome). The test fixtures' `_forecast`
  helper default changed from `member_ids=[None]` to `member_ids=[0]` (Recap's source-derived SINGLE identity) so
  every existing SINGLE-mode test — which already passed `expected_member_ids=frozenset({0})` — continues to mean
  what it always claimed to mean. Locked by `test_single_mode_wrong_member_rejects_candidate_and_walks_back`,
  `test_single_mode_rejects_a_candidate_carrying_a_complete_foreign_member`,
  `test_single_mode_short_member_with_a_longer_foreign_member_rejected` and
  `test_foreign_member_candidate_walks_back_and_never_persists` (the last asserting against the STORE that no row of
  the rejected candidate was persisted) — `tests/unit/services/test_track_resolution.py`; the two both-complete cases
  proven RED against the filtering implementation (the fresh foreign-member candidate was accepted at
  `2026-01-10T06:00` instead of walking back to `2026-01-10T00:00`).
- **Minor — the public flow parameter was typed `object | None` instead of `ForcingResolutionPolicy | None`.**
  Every other `run_forecast_cycle_flow` parameter is deliberately `object`-typed because it is a Protocol-typed
  injection point Prefect's pydantic-backed parameter validation cannot usefully check; `ForcingResolutionPolicy` is
  a plain frozen dataclass with no such constraint, so the loose annotation only meant a malformed value crossed the
  flow boundary unchecked and failed later, inside T8b, when a policy attribute was first accessed. **Fix:** the
  parameter is now typed `ForcingResolutionPolicy | None`, and the now-redundant internal `cast(...)` was removed.
  Locked by
  `TestForcingResolutionPolicyConstruction.test_flow_boundary_rejects_a_value_that_is_not_a_forcing_resolution_policy`,
  which passes `object()` and asserts `prefect.exceptions.ParameterTypeError`; proven RED against the pre-fix code
  (the flow accepted the malformed value and only failed downstream).

**Exit gate:** `uv run pytest tests/unit -q` full suite green; `uv run ruff format`/`uv run ruff check .` clean
beyond the same 12 pre-existing alembic `E501`s; `uv run pyright src/sapphire_flow/adapters/forecast_interface.py
src/sapphire_flow/flows/run_forecast_cycle.py src/sapphire_flow/services/track_resolution.py` at 0 errors.
