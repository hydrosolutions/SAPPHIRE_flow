---
status: READY-PENDING-RESTALENESS
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
and every claim re-verified. Two corrections change a ratified decision's observable consequence and are filed under
*Open items → Requires owner re-ratification*; **status stays `READY-PENDING-RESTALENESS` until the owner re-ratifies
those two**. Everything else was pure correction — no decision was reopened.
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

## Review scope — staleness confirmation only (2026-08-18)
**This round is a staleness confirmation, not a design review.** The only question a reviewer answers is: *what in this
plan is now wrong because `main` moved 142 commits?* Not whether the design is the right one.

- The design was **owner-ratified 2026-08-11 and has no open forks**, except the two filed under *Open items →
  Requires owner re-ratification* (D21's corrected sweep failure mode, and `D32-multibranch-delivery`). Those two are
  **the owner's to settle, not a reviewer's**. Do not propose answers to them as findings.
- **D1–D31 are settled. Do not reopen, re-argue, or "strengthen" them.** A finding that reduces to preferring a
  different design is out of scope by construction.
- The **atomic 8-task phase shape**, the **phase dependency graph**, and every **Non-goal** are **fixed for this round**.
- **In-scope findings are exactly these five:** (1) a `file:line` citation that is still wrong; (2) a factual claim
  about current `main` that is false; (3) a red-first criterion that would not actually be red against current `main`
  (a false red-first); (4) an exit gate that cannot pass; (5) a collision with work that landed since 2026-08-11.
  Anything else is out of scope.
- **Proportionality rule — growth is a defect signal.** This document is a deliberate reconstruction after two `/plan`
  runs over-expanded it (999 → 1316 → 1940 lines) by re-litigating settled design. It is now ~757 lines. A reviewer
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
   the plan's own precedent for config-threaded policy inputs. **No such field exists today**, so T4 **adds and
   validates** `RecapGatewayConfig.cycle_cadence_hours` (`config/recap_gateway.py`) and threads it into the `services/`
   resolver on **both** construction branches, exactly as `max_cycle_age_hours` is threaded. The earlier
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
     `forecast_horizon_steps` (`services/run_station_forecast.py:173-188`), fan-out from the global mode (`:250-252`),
     and the whole `future_dynamic_features` set to `fan_out_ensemble` (`:295`). Phase 3 therefore carries the per-feature
     contract into the runner (D10).

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
- The **runner consumption seam** via a discriminated input, plus a per-feature forcing contract on `ReadyContext`.
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
    - **Two `fetch_forecasts` behaviours the plan did not model; T4 must decide each for `fetch_requirement`:** (a) the
      **uncontained** `AdapterError` at `:923-935` for a mixed/PF-only HRU (Plan 154's control-coverage gate) — keep the
      fail-loud or route it per-station? (b) the total-loss re-raise at `:986-1000` (`RecapDataUnavailableError`,
      `code="source_data_missing"`, chaining `__cause__`) versus the plain `{}` at `:1004` for the
      all-well-formed-empty case — both must map onto the candidate taxonomy.
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
- **D8 — Completeness is a PER-STATION predicate; the candidate verdict is derived from it.** Two gates that must not
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
  resolved in `run_all_station_forecasts` **before** `_run_single_model` is invoked, via
  `AssignmentRunInput = ReadyContext | MissingTrackContext | UnavailableTrackContext`. The caller pattern-matches and
  short-circuits both failure arms to an `AssignmentFailure` **without touching the model registry, artifact store,
  model-state store, or QC**.
  - **`MISSING_CONTEXT`** — the track never resolved (no candidate passed completeness within the walk-back bound).
  - **`TRACK_UNAVAILABLE`** — the track resolved, but this station is unavailable at the accepted cycle.
  Both are assignment-local and advance the fallback chain. `_run_single_model` receives a non-optional
  `ModelRunContext` plus the assignment's own `ForecastProvenance`, and writes each `OperationalForecast`'s cycle
  fields from **that** provenance rather than the shared runner arguments (`run_station_forecast.py:145-146,:400-401`).
  A pre-run defensive assert re-checks the expected member set over the assembled frame.
  - **The runner's horizon read now goes through a seam.** Plan 159 T0d interposed `resolve_required_steps(...)`
    (`services/horizon_semantics.py`) at `run_station_forecast.py:177-182`, between the requirement declaration and
    `assess_future_coverage` (`:183-188`). The runner still re-derives from the collapsed `ModelDataRequirements`, but
    the per-feature contract must route **through** that seam, not around it by reading the requirement directly.
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
The phase lands **atomically**, but the tree stays green at every task gate because the per-track path is dormant
behind the `isinstance` dispatch until the final wiring task. Every task is **red-first**: the acceptance test is
written to fail against pre-task code, then the implementation turns it green, and test soundness is proven. Each task
lands its production change **and its test consumers together** — no task leaves a known failure. **Per-task exit gate
(corrected 2026-08-18 — it is "no NEW findings versus a pinned baseline", never "clean"):** the focused modules, plus
full `uv run pytest -q` still green at the pinned counts; `pyright` gated by `tools/pyright_ratchet.py` against
`tools/pyright_baseline.json` **scoped to `src/`** (pre-push hook `pyright-ratchet`, `.pre-commit-config.yaml:61-69`),
which must not exceed the pinned **432** — a task that legitimately reduces it regenerates the baseline via
`tools/pyright_baseline.py` in that task's own commit; and `uv run ruff check` introducing **no new findings** beyond
the 12 pre-existing ones already on `main`.

**Measured baseline (2026-08-18, `main` `c81041e`, before any Phase 3 work):** `uv run pytest -q` = **4181 passed,
15 skipped, 11 deselected**; `uv run pyright` over `src/` = **exactly 432**, i.e. precisely AT the ratchet, not above it
(bare `uv run pyright` also reports 432 — zero is *not* the gate); `uv run ruff check` **exits 1** with 12 pre-existing
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
    constructed services-side (D31)** — so READY ratifies these authoritative contract changes rather than deferring
    them. **`TrackFetchResult` keeps its single `resolved_cycle` field unchanged** (`:3313-3316`): Phase 3 does **not**
    introduce a per-assignment or per-horizon-class cycle (D5, D26).
  - **Red-first:** `FutureSteps(value=0)` and `value=-1` raise `ValueError`; `ForcingTrackKey` is hashable and usable
    as a dict key, with keys built from differently-ordered inputs comparing and hashing equal; two keys differing only
    in per-feature horizon values are equal (horizons are not in the dedup identity); keys differing in one feature
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
    since Plan 156, `forecast_interface.py:539`).

- **T3 — Track projection + `resolve_tracks()` (D2, D5).**
  - **In scope:** the pure `services/` projector over the explicit join, and the deterministic reducer.
  - **Red-first:** the projector maps a join to `ForcingRequired` with per-feature horizons and the locked key shape; a
    model whose **selected branch** has no `future_known` projects to `NoForcingRequired` **even when another branch
    has future forcing**; a fallback model projects to `NoForcingRequired`; a non-FI multi-feature model broadcasts its
    scalar horizon to every feature; two assignments with identical track fields dedup to one `ResolvedTrackRequest`
    whose `fetch_horizons` is the per-feature max, with each assignment's own horizons retained; reducer output order
    is deterministic.

- **T4 — Source contract + adapter migration (D6, D7 containment).**
  - **In scope:** `CandidateAwareForecastSource` (`@runtime_checkable`) in `protocols/adapters.py`;
    `fetch_requirement` + `expected_member_ids` on `recap_gateway` as a **pure single-cycle** fetch with **no** internal
    walk-back and **per-HRU containment**; wire the adapter factory's return type for dispatch. **Add and validate
    `RecapGatewayConfig.cycle_cadence_hours`** (the walk-back cadence source, ruling 4 / D24) and thread it alongside
    `max_cycle_age_hours` on both construction branches. Legacy adapters and fakes untouched.
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
    `tests/unit/adapters/test_recap_gateway.py:644`); a transport failure is **raised** rather than returned as a
    status; an auth/config error is fatal, never walked back. Legacy `fetch_forecasts` behaviour is asserted
    **unchanged** throughout.

- **T5 — Per-track resolution: walk-back policy + completeness + `TrackFetchResult` (D7, D8).**
  - **In scope:** the pure fetch/validate half (walk-back bounded by `max_cycle_age_hours` threaded on **both**
    construction branches; the **per-station** completeness validator keyed on `expected_member_ids` and on the track's
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
    branch-specific `max_nan` are honoured from the selected branch only. `ReadyContext.nwp_age_hours` differs between
    a fresh and an older-resolved assignment. **T6 asserts `ReadyContext` only** — `ModelRunContext` is not constructed
    until `_run_single_model` (`run_station_forecast.py:239`), which T7 owns.

- **T7 — Runner consumption seam (D10).**
  - **In scope:** resolve the two pre-run arms in `run_all_station_forecasts` before `_run_single_model`; the
    discriminated `AssignmentRunInput` with an explicit legacy-vs-per-track discriminant; migrate `_run_single_model`
    to receive its context and write provenance from it; the pre-run defensive member-set assert; keep the fallback
    chain and the Plan 150 backstop intact.
  - **Transitional API:** the seam changes the input shape of `_run_single_model` **and** `run_all_station_forecasts`,
    so T7 must keep every current caller green — including the integration caller
    (`tests/integration/test_e2e_pipeline.py:653`) — either by retaining a behaviour-preserving entry point or by
    migrating all callers in this task. T7's gate **includes** that integration caller.
  - **Red-first:** a station whose higher-priority assignment is ready and whose lower-priority assignment has a
    missing track returns the higher-priority success and records `MISSING_CONTEXT`, **asserting the model registry,
    artifact store, model-state store, and QC were not accessed** for the failing arm; an unavailable-track assignment
    records `TRACK_UNAVAILABLE` and advances the chain; a resolved-but-partial context trips the pre-run assert as an
    assignment-local failure; a **legacy ENSEMBLE caller** still performs coverage and fan-out unchanged.

- **T8 — Flow wiring + golden tests + fail-loud combination (D11, D12, D13).**
  - **In scope:** wire `flows/run_forecast_cycle.py` to project and dedup tracks, resolve each track sequentially,
    assemble per assignment, and run via the migrated runner — **behind the `isinstance` dispatch**. Legacy adapters
    keep the superset path; the GROUP path keeps Phase A, **re-scoped** so it no longer fetches/persists for stations
    served by the per-track path while still covering group-member stations (D7). Add the cross-cycle preflight at the
    top of the per-station persist block, and the new log events. **Set `retries=` on the candidate-fetch task** from
    `RecapGatewayConfig.max_retries` (`config/recap_gateway.py:51`) — today that field is parsed but consumed nowhere
    and `_fetch_nwp_task` (`flows/run_forecast_cycle.py:1009-1015`) sets no `retries=`, so T4's "raise transients so a
    retry can fire" design would otherwise have **no** consumer and a transient would terminate the track on first
    failure (reviewer major-fix).
  - **Overlap rule: a station that is BOTH group-member and per-track-eligible stays on the LEGACY path in Phase 3**
    (reviewer major-fix; corrected after the Codex gate). Groups contain ordinary operational station ids
    (`flows/run_forecast_cycle.py:2457,2489`), so the two re-scoping rules can collide on one station. The earlier
    "per-track owns the write, Phase A must not persist for it" form was **incoherent**: group assembly holds no
    in-memory Phase A payload — it reads each member **from the store** at the single legacy `nwp_readback_cycle_time`
    (`flows/run_forecast_cycle.py:2553`, `services/run_group_forecast.py:128`). Suppressing Phase A's write would drop
    that member from the group unless per-track resolution happened to land on the same cycle, which nothing
    guarantees.
    **Rule:** an overlapping station is **excluded from the per-track path** and served entirely by legacy Phase A in
    Phase 3. This follows directly from the already-ratified D8-group (group stays legacy in Phase 3): it keeps the
    group's single readback cycle coherent, avoids double-writes, and keeps unvalidated partial rows away from any
    station the per-track path claims — because it claims none of them. **Cost:** overlapping stations get no Phase-3
    benefit until Phase 4 folds group into track discovery. Recorded as **D30-overlap-deferral**.
  - **Docs (same task):** `docs/design/forecast-cycle-redesign.md` (mark item 3 landed, item 4 pending),
    `docs/architecture-context.md` (Flow 1 per-track phase note), `docs/standards/logging.md` (the four events),
    `docs/plans/README.md`, and `docs/touchpoint-maps.md` if an existing bullet names the old superset shape. **Plus
    the operator-facing shared-track freshness note in `docs/operations/recap-gateway-runbook.md`** (D26): models
    sharing a forcing track resolve to one common cycle, so a shorter-horizon model may run on an older cycle than it
    alone required — with the reason (one `resolved_cycle` per track) and the symptom an operator would notice.
  - **Plan 116's freshness contract must be honoured by the fail-loud preflight** (2026-08-18). Plan 116 (`bd7e041`)
    added a `FORECAST_FRESHNESS` heartbeat (`flows/run_forecast_cycle.py:607`, emitted `:648`) and rewrote both the
    station and group `store_forecast` loops — the group path now emits a **forced-CRITICAL** freshness record and
    re-raises on any exception. D11's preflight writes **zero** forecasts/state on a mismatch, so a single-station
    cross-cycle test yields `forecasts_stored == 0` and a CRITICAL `FORECAST_FRESHNESS` record **by design**: T8's
    golden must **assert** it, not collide with it. 116 is the only merged plan in this window that mutated a Phase-3
    blast-radius file.
  - **Red-first / golden:** a homogeneous single-track control station produces **byte-identical** output to
    pre-Phase-3 `main`, and this golden **fails** if the homogeneous case is mis-routed; a heterogeneous control
    station produces per-assignment outputs; two combinable assignments on different cycles fail loud with **zero**
    forecast/state writes while same-cycle combination is unchanged; a **trackless combinable model** (null cycle)
    combines with an NWP model without failing, and an all-trackless station combines normally; a **group-only feature**
    is still fetched via the re-scoped Phase A and the group forecast succeeds at its shared readback cycle regardless
    of how station tracks resolved; **an OVERLAP test — a station that is both group-member and
    per-track-eligible is routed to the LEGACY path (D30): it is absent from the resolved track set, Phase A still
    writes its rows, and the group forecast succeeds at the shared readback cycle**; a legacy-adapter
    deployment is unchanged.
  - **Exit gate (whole repo):** the same baseline-relative gate as every task (full `uv run pytest -q` green at the
    pinned counts; `pyright` at or below the ratcheted **432** over `src/`; `ruff check` with no new findings beyond the
    12 pre-existing alembic `E501`s) — **plus** `uv run pytest tests/integration/test_e2e_pipeline.py -m slow -q`,
    explicitly overriding the default `not slow` exclusion (`pyproject.toml:132`; the `slow` marker is declared at
    `:130`) so the end-to-end control-only path actually runs.

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
    { "id": "T8", "tasks": ["T8"], "parallel": false, "depends_on": ["T7"] }
  ]
}
```
**T2 and T6 both edit `adapters/forecast_interface.py`.** T6 depends on T2 transitively (T6 → T5 → T3 → T2), so the
two edits to that file are genuinely serialized — T3's explicit dependency on T2 is what guarantees it. T4 is the only
task that may run alongside T2/T3: it touches `protocols/adapters.py` and `recap_gateway.py`, disjoint from the FI
adapter.

## Non-goals
- Removing the legacy station-superset path (Phase 4).
- Multi-track assignments / multi-product / mixed-mode requirements (D22).
- Group operational ENSEMBLE fan-out; group CONTROL stays on the legacy path in Phase 3.
- Member survival — v1 is all-members-or-fail.
- Hindcast/operational parity.
- Richer combined-forecast provenance for differing cycles; write-side per-assignment state; concurrent track fetch.
- MeteoSwiss migration to `fetch_requirement`.

## Open items

**Requires owner re-ratification (staleness, 2026-08-18):** `main` moved under two ratified decisions. Neither reopens
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

**Resolved (owner-ratified 2026-08-10 unless noted):**
- **D1-types-location** — new `types/forcing_track.py`; `ModelRunContext` stays service-local (Plan 148).
- **D2-survival** — all-members-or-fail; member survival is a follow-on.
- **D3-mapping** — `TRACK_UNAVAILABLE` = track resolved but this station unavailable; `MISSING_CONTEXT` = the track
  never resolved.
- **D5-combine** — fail-loud, as a preflight before any per-station write.
- **D6-adapter-scope** — migrate `recap_gateway` only.
- **D7-walkback** — bounded solely by the existing `max_cycle_age_hours`.
- **D8-group** — group stays on the legacy superset path in Phase 3.
- **D9-records-type** — `StationTrackAvailable.records` is the station-keyed `WeatherForecastRecord` list from readback
  at the track's single `resolved_cycle` (singular by D5/D26 — no per-assignment readback source).
- **D10-completeness-pre-extract** — for the pre-extracted recap path the gate boundary is **before persist**; the
  spec's "before extraction" wording assumes a gridded candidate. The same wording assumes a single global member axis,
  which a station-keyed pre-extracted candidate does not have (`recap_gateway.py:821-973`), so the gate is stated
  per `(station, feature, member, horizon)` (D8). A spec-precision clarification, not a silent divergence; the
  spec-wording fix rides along with T1's spec edit.
- **D28-max-retries-wiring** (new, 2026-08-11 review; **superseded by the Codex gate — now IN SCOPE at T8**)
  — `RecapGatewayConfig.max_retries` (`config/recap_gateway.py:51`) is parsed and stored but wired to nothing: no
  `@task(retries=...)` reads it anywhere in `src/`, and `_fetch_nwp_task` (`flows/run_forecast_cycle.py:1009-1015`)
  sets no `retries=` (re-verified 2026-08-18 — unchanged). It was
  briefly cut from T4 as flow-layer work — correct about the *layer*, wrong to defer it: T4's design **raises** typed
  transients so a retry can fire, which without a consumer means a single transient terminates the track. **T8 sets
  `retries=` on the candidate-fetch task from this field.** It stays out of T4 (not a `CandidateAwareForecastSource`
  conformance concern), but it is a Phase 3 acceptance gate at T8.
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
  and `config/recap_gateway.py:29` has **no** cadence field today. T4 therefore **adds and validates**
  `RecapGatewayConfig.cycle_cadence_hours` and threads it on both construction branches, exactly as
  `max_cycle_age_hours` is threaded. (Re-verified 2026-08-18: still no cadence field.)
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
  shorter-horizon model can consume an older cycle than it strictly required — **T8 documents it in the
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
- **D28-fetch-retries — IN SCOPE at T8** (superseding the earlier "cut/deferred" note). T4's design raises typed
  transient transport errors so a retry can fire; that requires a real consumer, so T8 sets `retries=` on the
  candidate-fetch task from `RecapGatewayConfig.max_retries` (`config/recap_gateway.py:51`, parsed but consumed nowhere
  today; `flows/run_forecast_cycle.py:1009-1015` sets no `retries=`). Without it a single transient terminates the track.
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
