---
status: DRAFT
created: 2026-07-23
plan: 146
title: Antecedent (past) snow reanalysis channel — provenance + owning ingest flow + read-side routing
scope: Verify the read-path PLUMBING that carries historical/antecedent JSNOW (swe/snow_depth/snowmelt) into a model's `past_dynamic` frame end to end (proven with a test model). Add a supported `ForcingSource` provenance for the recap snow-reanalysis literal, a DEDICATED recap-reanalysis ingest flow/schedule that fetches + persists snow reanalysis to `HistoricalForcingStore` (the blocker: today no production caller runs it), and read-side hybrid routing so a stored snow series is selectable by the training / hindcast / live-inference read path. Does NOT onboard a real snow-fed FI model — the canonical schema / default-availability / unit follow-on is an explicit Plan 139 dependency (see D6). Split from Plan 145 (which owns the future channel). Forcing ingest.
depends_on: [082, 145]
blocks: [139, 144]
supersedes: []
---

# Plan 146 — Antecedent (past) snow reanalysis channel

## Status
**DRAFT — split from Plan 145 (owner 2026-07-23).** This is the load-bearing half of the original snow-forcing
plan: the antecedent channel needs a new provenance source, a read-side snow tier, and — the blocker — an
**owning ingest flow/schedule** (today the snow-reanalysis adapter has zero production callers). **D2 DECIDED
(owner 2026-07-24): a SCHEDULED daily ingest deployment bounded by a FIXED ROLLING WINDOW** — no persisted
watermark; see D2 for the full rationale — mirroring the sibling `ingest_weather_history` flow
(`ingest_weather_history.py:372-425`), whose idempotency comes for free from the store's
`on_conflict_do_nothing()` upsert (`historical_forcing_store.py:34-55`). It ALSO mirrors that flow's
**health-by-EFFECT** classification — a before/after `MAX(valid_time)` DB readback
(`_snapshot_latest_valid_times`/`_horizon_advanced`, `ingest_weather_history.py:331-369`), never a fetch-success
counter — so a silently-stalled JSNOW feed is not reported healthy (D5). **D5 LOCKED (owner 2026-07-24): the
ingest flow is MODEL-AGNOSTIC, not model-aware.** The recap adapter already discriminates the only condition
the earlier model-scoped design existed to suppress — `RecapSnowUnavailableError.code` returns
`subscription_not_found` (permanent/structural, e.g. basin 12300 has `swe` but not `snow_depth`) vs
`source_data_missing` (transient reanalysis lag) via `_SNOW_UNAVAILABLE_CODES` (`recap_gateway.py:300-318`). So
the flow fetches the full snow ceiling for every in-scope recap-reanalysis HRU (over-fetch is free — idempotent
`on_conflict_do_nothing` upsert) and classifies health from the code, with no need to know about
models/assignments/groups. This matches the repo's own precedent (`_compute_required_snow`,
`run_forecast_cycle.py:781-810`, explicitly defers group scoping even for the live FUTURE channel). This
removes the `ModelStore`/`StationGroupStore` injection, `required_snow` requirement-resolution, and the
`no_snow_requirement` WARNING case entirely — see D5/D5a. Depends on Plan 145 for the canonical
snow aggregation fix and its per-`(hru, variable)` snow error boundary (the training/read path uses both).
Needs a confirming `/plan` before READY. Grounded in [[reference_recap_gateway_12300_products]].

## Problem — antecedent snow is not fetched, not provenance-supported, and not read-routed
A model needing snow **lookback** (antecedent SWE/depth/melt in its `past_dynamic_features`,
`operational_inputs.py:410-431`) gets nothing today. Three coupled gaps:
1. **No production caller fetches snow reanalysis.** `RecapGatewayReanalysisAdapter.fetch_reanalysis`
   (`recap_gateway.py:1039-1068`, `_rows_for_variable` at `:1070-1132`) *can* fetch `snow.reanalysis`, but the
   production weather-history ingest (`ingest_weather_history.py:402-417`) builds **only** the MeteoSwiss adapter
   (`build_production_reanalysis_adapter`) — nothing runs the recap reanalysis adapter. **This is the
   blocker:** a standalone task with no owning flow/schedule leaves the gap intact.
2. **No supported provenance.** The persisted literal `recap_snow_reanalysis` (`recap_gateway.py:323`,
   `_SNOW_SOURCE`) is **not a `ForcingSource` member** and has **no `SOURCE_ATTRIBUTIONS` entry**
   (`forcing_sources.py:18-47` — only MeteoSwiss/CAMELS/NWP_ARCHIVE members), so a persisted snow row has no
   supported provenance/attribution.
3. **No read-side routing.** The hybrid read chain wires **MeteoSwiss-only** per-parameter priority chains
   (`hybrid_reanalysis_factories.py:37-57`, Plan 115b4 §5B) — no snow tier — so even a stored snow series is
   never selected and never reaches `past_dynamic` for training, hindcast, or live inference.

## What already exists — 146 fills the wiring, doesn't rebuild
- **The reanalysis adapter** — `RecapGatewayReanalysisAdapter.fetch_reanalysis` already routes `snow.reanalysis`
  → `RawHistoricalForcing` (`recap_gateway.py:1039-1068`); it lacks a production caller + provenance + read-routing.
- **The snow error boundary + the typed-result pattern** — Plan 145 already added `_guarded_snow_fetch` +
  `RecapSnowUnavailableError` (`recap_gateway.py:284-318`), which contain
  `source_data_missing`/`subscription_not_found` per `(hru, variable)`, AND the `fetch_snow_forecast` /
  `SnowForecastFetchResult` pattern (`recap_gateway.py:898-982`, `types/weather.py:80-95`) — a NON-Protocol
  method returning a typed partial-result. D5 mirrors that on the reanalysis side with a **new** non-Protocol
  `fetch_snow_reanalysis` + `SnowReanalysisFetchResult`; it does **not** touch `fetch_reanalysis`/the
  `WeatherReanalysisSource` Protocol (which is return-locked to `list[RawHistoricalForcing]`,
  `protocols/adapters.py:75-84`, and asserted by `test_recap_gateway.py:405`).
- **The stores + readers** — `HistoricalForcingStore` (idempotent `store_forcing`, `:34-55`), `PerSourceStoreReader`,
  the hybrid factory (`hybrid_reanalysis_factories.py`). 146 adds a snow tier, does not rebuild the read stack.
- **The rolling-ingest shell** — `ingest_weather_history_flow` (`ingest_weather_history.py:372-425`): a `@flow`
  with injected stores/adapter/clock, a `window_days` override (`:382-387`), and `start = now - window_days`
  (`:424-425`). 146's ingest flow copies this shape against the recap adapter.
- **The client** — `recap_client.snow.reanalysis` (pin ≥ 9340e40, #127).
- **Plan 145** — the canonical snow aggregation fix (`swe`/`snow_depth` MEAN, `snowmelt` SUM) used by the
  training/read resample.

## Design decisions
- **D1 — a DEDICATED recap-reanalysis ingest path, NOT the MeteoSwiss flow.** The `ingest_weather_history` flow
  types its adapter as `_ReanalysisAdapter` requiring `fetch_products(...)` + `discover_rhiresd_boundary()` and
  unconditionally does MeteoSwiss RhiresD boundary discovery (`:451-521`). `RecapGatewayReanalysisAdapter`
  exposes only `fetch_reanalysis(station_configs, start, end, parameters)` — it satisfies neither method — so it
  cannot drop into that flow. 146 adds a **standalone recap-reanalysis ingest flow**. The flow is **parametrized
  by a variable *ceiling* list** (defaulting to `swe`/`snow_depth`/`snowmelt`) — the flow FETCHES this full
  ceiling for every in-scope HRU each run (MODEL-AGNOSTIC — LOCKED, owner 2026-07-24; see D5). `variables` bounds
  the flow's product scope and lets a future ERA5-Land ingest plan reuse the shell with a different ceiling (not
  a design driver here, no Phase-2 task). Per-HRU **subscription** — which of the ceiling's variables a basin
  actually has access to — is discovered at RUNTIME via the adapter's `subscription_not_found` code, never
  pre-computed from model or group requirements (D5). **The default ceiling is `swe`/`snow_depth`/`snowmelt`
  because those are the three snow params 146 wires; it is NOT a claim that all three are always available for
  every HRU** — an unsubscribed variable is still fetched, classified `subscription_not_found`, and excluded
  from WARNING (D5), never silently skipped from the fetch attempt.
- **D2 — a SCHEDULED recap-reanalysis ingest deployment, FIXED ROLLING WINDOW (DECIDED, owner 2026-07-24).**
  The ingest is a real Prefect **deployment on a daily cron** (`SCHEDULE_INGEST_SNOW_REANALYSIS`, mirroring
  `SCHEDULE_INGEST_WEATHER_HISTORY = "0 6 * * *"`, `register_deployments.py:40-41`). Each run fetches a **fixed
  rolling window** `start = clock() - window_days` through `clock()`, exactly as `ingest_weather_history`
  (`:424-425`) — **no persisted watermark, no read-then-advance step**. `window_days` defaults to a value that
  safely exceeds the ~7-day JSNOW reanalysis lag (e.g. **21 days**) and is overridable; the **initial history
  backfill is the *same flow* run with a wide `window_days`** (e.g. 730), mirroring `ingest_weather_history`'s
  parametric backfill (`:379-387`). **Idempotency is free** — `HistoricalForcingStore.store_forcing` upserts with
  `on_conflict_do_nothing()` (`:34-55`), so a re-run over an overlapping window stores zero duplicate rows.
  This eliminates the earlier watermark subsystem entirely and, with it, the read-then-advance concurrency race
  (further guarded by `concurrency_limit=1` on the deployment, matching `ingest-weather-history`,
  `register_deployments.py:107-113`). **NOT a one-shot backfill** — the operational antecedent is **read from the
  persisted store** (`operational_inputs.py:410-421`), so a scheduled rolling run keeps the lookback current.
  Acceptance test invokes the **actual entry point** (the deployed flow), proves persistence, and asserts a second
  run over an overlapping window stores **no duplicate rows** (idempotency by upsert, not by watermark).
- **D2a — newly-bound stations get an explicit wide-window backfill (NOT the rolling window); backfill SIZING and
  BEFORE-OPERATIONAL guarantees are ONBOARDING (Plan 139) concerns, not this flow's (re-framed, folds findings
  #3/#6).** The 21-day rolling window keeps *existing* stations current but never acquires history older than
  `window_days` for a station bound AFTER scheduled ingest is already running — it would start with only a
  21-day lookback and never fill the gap. This is a REAL rule 146 must specify, not an operator-remembers-to-run-it
  hope. The ingest flow already resolves reanalysis-role stations each run via the adapter's `NWP_SOURCE`
  (`ingest_weather_history.py:280-289` is the sibling `_reanalysis_sources` pattern), so the SAME model-agnostic
  flow entry point (D5) is the backfill primitive — a wide `window_days` (e.g. 730) over an explicit `station_ids`
  subset. Two mechanisms, ranked; 146 ships (1) and documents (2):
  1. **Operational backfill runbook + deployment parameter (SHIPPED by 146):** `docs/standards/orchestration.md`
     documents running the ingest deployment once with a wide `window_days` (and a station subset) when a
     recap-reanalysis station is onboarded. A Phase-2b acceptance test covers "a station introduced after
     scheduled ingestion is running is backfilled by a wide-window flow run over its `station_ids` subset" — the
     MECHANISM is tested. **Because the flow is model-agnostic (D5, LOCKED 2026-07-24), it has no way to know how
     DEEP a given model's lookback requires the backfill window to be, nor can it certify that backfill completed
     BEFORE that model becomes operationally active — 146 does NOT claim either guarantee (narrowing the earlier
     draft's claim, findings #3/#6):**
     - **Sizing derivation (onboarding-owned).** The operator/onboarding flow sizes `window_days` from the
       model's `data_requirements.lookback_steps × time_step` (`types/model.py:264-275`), mirroring the existing
       `lookback_start = issue_time - reqs.lookback_steps * time_step` pattern already used at
       `operational_inputs.py:354`. This is documented (Phase 4) as the derivation onboarding uses; 146's flow
       does not compute it.
     - **Before-operational / depth-sufficiency enforcement (deferred to Plan 139 / onboarding-flow).** 146's
       health-by-effect classification (D5) detects NEW rows landing, not historical DEPTH already present in the
       store — it cannot certify "the store holds N days of antecedent history for this model." An enforced
       onboarding prerequisite / depth-sufficiency check before a model is marked active is explicitly OUT of
       146's scope and is a Plan-139/onboarding-flow responsibility — flagged here as deferred, not silently
       omitted.
  2. **Onboarding-triggered auto-backfill (DEFERRED, noted):** wiring station-onboarding (Flow 5/Flow 0) to fire
     this flow with an onboarding-derived window for the new station is the eventual clean answer, but it couples
     onboarding to a new deployment trigger and is out of 146's plumbing scope — flagged as a follow-on, not
     silently omitted.
  *(Rationale for dropping the watermark: reviewers correctly noted the sibling flow this plan claims to mirror
  uses NO watermark — it re-fetches a rolling window and leans on the store's content-hash/PK supersession. A
  per-`(station, variable)` watermark would have needed new schema, a natural key, boundary/overlap/newly-bound
  rules, and a concurrency guard for a problem the rolling window already solves.)*
- **D3 — supported provenance for `recap_snow_reanalysis`.** Add one `ForcingSource` member
  `RECAP_SNOW_REANALYSIS = "recap_snow_reanalysis"` for the persisted literal (`recap_gateway.py:323`) + its
  `SOURCE_ATTRIBUTIONS` entry (`forcing_sources.py:38-47`); round-trips through the provenance layer. **This does
  NOT touch `CANONICAL_FORCING_SCHEMA`** (`forcing_schema.py:37-53` keeps exactly its five MeteoSwiss params —
  expanding it is gated on unit resolution, D6). **Attribution string — authoritative text is a HARD READY/merge
  gate; NO provisional string ships.** The product is the recap-gateway JSNOW reanalysis (client per-row source
  literal `jsnow_reanalysis`, `recap_gateway.py:332`; live-probed in [[reference_recap_gateway_12300_products]]).
  The exact acknowledgement/licence text is not repo-grounded, so it MUST be obtained from the owner and recorded
  in this plan BEFORE the plan becomes READY — `SOURCE_ATTRIBUTIONS[RECAP_SNOW_REANALYSIS]` is populated with that
  confirmed text at implementation time and never with an invented placeholder. (Earlier draft allowed shipping a
  "pending owner confirmation" string; that contradicted the merge gate and is REMOVED — the two are mutually
  exclusive and the gate wins.) **Test-scope correction (review):** `SOURCE_ATTRIBUTIONS` values are unrestricted
  strings (`forcing_sources.py:38-47`), so a dictionary-membership assertion can only prove an entry is *present*,
  NOT that it is non-placeholder — a completeness test alone cannot detect a placeholder. The Phase-1 test
  therefore does exactly two things and claims exactly two things: (a) it blocks a member with a **missing**
  attribution from ever merging (completeness — not covered today), and (b) it asserts
  `SOURCE_ATTRIBUTIONS[RECAP_SNOW_REANALYSIS]` **equals the exact owner-confirmed licence string recorded in this
  plan** (an equality assertion, the only mechanism that actually rejects a placeholder). Because the confirmed
  string is the READY gate (Open items), the equality assertion is written against that recorded value at
  implementation time. (The sibling `recap_era5_land_reanalysis` literal at `:322` is out of scope — no live
  consumer; belongs to whichever plan wires ERA5-land read-routing.)
- **D4 — read-side snow tier reaches the training/hindcast/live read path (HYBRID mode; single mode out of
  scope).** Add per-parameter read routing for `swe`/`snow_depth`/`snowmelt`:
  1. add each as a single-source chain `(ForcingSource.RECAP_SNOW_REANALYSIS,)` to `_PRIORITY_CHAINS`
     (`hybrid_reanalysis_factories.py:37-46`), and
  2. **add the three snow params to `DEFAULT_PARAMETERS`** (`hybrid_reanalysis_factories.py:51-57`).
  Step 2 is REQUIRED, not optional: `HybridForcingSource._sources` (the set of child readers actually fanned out
  to) is derived ONCE at construction from `parameters_in_scope` (`hybrid_reanalysis.py:52-59`,
  `hybrid_reanalysis_factories.py:63-75`), and every read-side caller builds it via
  `select_reanalysis_source(...) → default_hybrid_forcing_source(forcing_store=...)` with **no
  `parameters_in_scope` override** (training `training_data.py:194`, hindcast `hindcast.py:305`, live
  `operational_inputs.py:414`) — so it always falls back to `DEFAULT_PARAMETERS`. Adding snow to `_PRIORITY_CHAINS`
  alone wires NO snow reader; the snow `PerSourceStoreReader` only gets constructed when the snow params are in
  the construction-time scope, i.e. in `DEFAULT_PARAMETERS`. **Accepted side effect, tested:** `DEFAULT_PARAMETERS`
  is also consumed verbatim by the dashboard forcing endpoint (`api/routes/stations.py:498-505`,
  `mode="hybrid"`), so it will now also request + surface stored snow series — this is *desirable* (the forcing
  inspection endpoint should show snow when present) and is covered by a new endpoint test (Phase 3). This also
  resolves the "operator endpoint omits snow" minor. **Prove the stored series reaches `past_dynamic` in each
  consumer separately:** training (`training_data.py:185,194`), hindcast (`hindcast.py:287,305`), live input
  assembly (`operational_inputs.py:410-421`) — adapter/factory tests alone are insufficient.
  **Single mode is explicitly out of scope:** `select_reanalysis_source(mode="single")` returns
  `StoreBackedReanalysisSource`, which reads `source=cfg.nwp_source` (`store_backed_reanalysis.py:35-41`) — recap
  stations bind as `nwp_source="era5_land"` while snow rows are tagged `recap_snow_reanalysis`, so single mode
  cannot serve recap snow. The default/supported deployment (mini + the dashboard endpoint, `stations.py:498`)
  runs hybrid mode, so this is a documented limitation, not a gap 146 must close; a follow-on owns single-mode
  endpoint-provenance selection if ever needed.
- **D5 — snow-reanalysis degradation via a NEW typed adapter method + typed result (NOT a one-line swap on
  `fetch_reanalysis`).** Reviewers correctly caught that `fetch_reanalysis` is Protocol-locked to
  `list[RawHistoricalForcing]` (`protocols/adapters.py:75-84`; the conformance test
  `tests/unit/adapters/test_recap_gateway.py:405` asserts `RecapGatewayReanalysisAdapter` satisfies
  `WeatherReanalysisSource`), and its inner loop has **zero** exception handling today
  (`recap_gateway.py:1061-1068`). So merely switching the snow branch's `_guarded_fetch` → `_guarded_snow_fetch`
  (`recap_gateway.py:1095`) would only change *which* exception aborts the whole call — it gives the flow **no
  channel** to learn which `(hru, variable)` failed while keeping the rows that succeeded. The forecast side
  already solved this identical problem the right way: `fetch_snow_forecast` is **deliberately NOT part of the
  `WeatherForecastSource` Protocol** (`recap_gateway.py:898-982`, docstring `:906-907`) precisely so it can
  return the richer `SnowForecastFetchResult(forecasts, unavailable)` (`types/weather.py:80-95`). 146 mirrors
  that exactly:
  1. **Leave `fetch_reanalysis` and the `WeatherReanalysisSource` Protocol UNTOUCHED** (read-side contract +
     the ERA5-land branch keep their `list[RawHistoricalForcing]` return; the conformance test at
     `test_recap_gateway.py:405` stays green).
  2. **Add a NEW non-Protocol method** `RecapGatewayReanalysisAdapter.fetch_snow_reanalysis(station_configs,
     start, end, variables=None)`, structurally the reanalysis twin of `fetch_snow_forecast` — it loops per
     `(hru, variable)` calling `_guarded_snow_fetch` inside a `try/except RecapSnowUnavailableError`, preserving
     already-accumulated rows for other keys, and returns a **new typed result**
     `SnowReanalysisFetchResult(rows: list[RawHistoricalForcing], unavailable: Mapping[GatewayHruName,
     Mapping[str, str]], attempted: Mapping[GatewayHruName, frozenset[str]])` (add to `types/weather.py` next to
     `SnowForecastFetchResult`; imported only by the new ingest flow, never by the shared Protocol). `variables`
     is an **optional allowlist override** defaulting to the full D1 ceiling (`swe`/`snow_depth`/`snowmelt`) — it
     is NOT a model-derived requirement map (see the model-agnostic decision below). `attempted` records the
     `(hru, variable)` keys that were actually requested after resolver skips — the denominator the flow needs to
     distinguish partial vs total loss. `unavailable`'s value type carries the **failure CODE**, not just the
     variable name — `{variable: RecapSnowUnavailableError.code}` — so the flow can tell `subscription_not_found`
     apart from `source_data_missing` without re-deriving it. This is the one shape change the model-agnostic
     decision requires of the typed result; everything else about it (a new non-Protocol method returning a typed
     partial result) is unchanged from earlier review rounds.
  - **MODEL-AGNOSTIC ingest, full-ceiling fetch, per-HRU subscription discovered at RUNTIME (LOCKED, owner
    2026-07-24 — supersedes the earlier model-scoped design).** The recap adapter already discriminates the ONLY
    condition the earlier model-scoping existed to suppress: `RecapSnowUnavailableError.code` returns
    `subscription_not_found` (permanent/structural — e.g. basin 12300 is subscribed to `swe` but not
    `snow_depth`, per the live probe at `docs/plans/139-nepal-12300-swe-regression-enablement.md:54,202`) vs
    `source_data_missing` (transient reanalysis lag), via `_SNOW_UNAVAILABLE_CODES` (`recap_gateway.py:300-318`).
    Because the adapter already tells the ingest flow WHY a variable is missing, the flow no longer needs to know
    WHICH models need WHICH variables to avoid alarm fatigue — it can just always fetch the full ceiling and let
    the code decide the response. So: **the ingest flow fetches the full D1 ceiling
    (`swe`/`snow_depth`/`snowmelt`) for every in-scope recap-reanalysis HRU, unconditionally** — no
    `ModelStore`/`StationGroupStore` injection, no `discover_models()`/`ForecastModel.data_requirements`
    resolution, no active-assignment union, no group-to-member-station mapping. Over-fetching an unsubscribed
    variable is free: the store upsert is idempotent (`on_conflict_do_nothing`, `historical_forcing_store.py:34-55`,
    D2) and a `subscription_not_found` key never stores a row, so there is no wasted write — only a classified,
    expected, at-most-once-logged non-event (health classification below). This matches the repo's own
    precedent: `_compute_required_snow` (`run_forecast_cycle.py:781-810`) explicitly defers group scoping even
    for the live FUTURE channel; 146's antecedent ingest goes one step further and drops model-awareness
    entirely, because the code-level discrimination makes the model-requirement-resolution machinery unnecessary
    for this flow's job (fetch + persist + report health — never decide what a model needs). **What
    model-requirement scoping is still for:** a model's `past_dynamic_features` ∩ snow params still determines
    what the model ACTUALLY reads at inference/training time (`operational_inputs.py:410-421`, D4/D6) — that is a
    read-side, per-model concern, unaffected by this decision. 146's ingest flow simply keeps the store as
    current as possible for every in-scope HRU; which snow params any given model consumes is decided
    downstream, per model, by the existing read path.
  **Health classification (health-by-EFFECT, WARNING-only vocabulary, re-keyed by CODE; folds two review
  findings):**
  - **`subscription_not_found` keys → EXPECTED-PERMANENT, excluded from WARNING.** Logged **at most once** per
    `(hru, variable)` at `INFO` (not re-logged every run) and never contributes to a WARNING or counts as a gap
    for health purposes — this is the structural, permanent condition `RecapSnowUnavailableError.code` already
    discriminates (`recap_gateway.py:300-318`); alarming on it would be alarm-fatigue-by-construction (the
    basin-12300 `snow_depth` case would otherwise fire forever).
  - **Config / auth / unanticipated errors** (`RecapAuthError`, `ConfigurationError`, or anything
    `_map_recap_error` returns — `recap_gateway.py:318`) → **raised**, the flow fails. These are not containable.
  - **In-scope station resolution reconciliation (finding #2 — false-OK guard).** The flow's PRE-resolution
    in-scope station set (resolved via the reanalysis-role pattern, `ingest_weather_history.py:280-289`) is
    reconciled against the stations that actually survive `_prefilter` + polygon resolution
    (`_prefilter` silently drops inactive/non-basin-average bindings, `recap_gateway.py:543`; the individual HRU
    resolver skips+logs unmappable stations one at a time and only raises when ALL are unmappable). A station
    that was in-scope going in but is dropped by `_prefilter` or resolution must surface as **at least
    `WARNING`**, naming the station and the drop reason — it must never simply vanish into a smaller `attempted`
    set that then reports `OK`. A **mixed-resolution acceptance test** (one resolvable + one unresolvable
    in-scope station in the same run) locks this (Phase 2b).
  - **`source_data_missing` (transient) keys, and no-horizon-advance among the surviving keys** →
    **`WARNING`**. Transient unavailability or a stalled feed inside JSNOW's ~7-day lag window is expected, not
    alarming; keeps the deliberate WARNING-not-CRITICAL divergence established below.
  - **Per-`(station_id, parameter)` health granularity, aggregated to `(HRU, variable)` (finding #1 — no single
    global `MAX(valid_time)`).** The before/after `MAX(valid_time)` DB readback
    (`HistoricalForcingStore.fetch_latest_valid_time`, `store/historical_forcing_store.py:188`,
    `protocols/stores.py:901`; `_snapshot_latest_valid_times`/`_horizon_advanced` pattern,
    `ingest_weather_history.py:331-369`) is taken **per `(station_id, parameter)`**, then aggregated per `(HRU,
    variable)` — **never** collapsed into one run-wide `MAX(valid_time)`, which would let a single advancing key
    mask N stalled ones. The run classifies **`OK`** only when **every** attempted key whose code is not
    `subscription_not_found` advanced; if even one such key stalled (no new rows, or an empty/duplicate
    re-fetch), the run is **`WARNING`** `reason="no_horizon_advance"` and the WARNING record **names the stalled
    `(station_id, parameter)`/`(HRU, variable)` subset explicitly** — not a bare "some keys stalled" message. A
    **multi-HRU/multi-variable acceptance test** where one key advances and one key stalls (empty or duplicate)
    asserts `WARNING` naming the stalled key, never `OK` (Phase 2b).
  - `rows_stored`/`len(records)` is still NOT used for health — it reports rows even on a pure-duplicate
    re-fetch, exactly the failure the sibling flow's module docstring calls out, `ingest_weather_history.py:15-19`.
  **Status vocabulary note:** `PipelineHealthStatus` has only `OK`/`WARNING`/`CRITICAL`
  (`types/enums.py:145-148`) and the DB `CHECK` allows only `ok`/`warning`/`critical`
  (`db/metadata.py:1566-1570`) — there is **no `DEGRADED` member**. The earlier draft's "DEGRADED" status was
  unimplementable without an Alembic migration + serialization/API tests. 146 reuses the existing **`WARNING`**
  for transient/stalled unavailability, per-key no-horizon-advance, and the resolution-reconciliation guard;
  `subscription_not_found` is **excluded from WARNING entirely** (not merely downgraded), and there is **no
  `no_snow_requirement` case any more** — the model-agnostic flow always has a non-empty ceiling to attempt.
  **Deliberate divergence from the sibling (noted, not a regression):** `ingest_weather_history` classifies
  `no_horizon_advance` as `CRITICAL` (`ingest_weather_history.py:545-548`); 146 uses `WARNING` because JSNOW's
  ~7-day reanalysis lag is longer than MeteoSwiss's, so a window that shows no advance is far more often normal
  lag than a true outage — a `CRITICAL` here would be chronic alarm-fatigue. The health-by-EFFECT *mechanism* is
  identical; only the severity mapping is tuned to the feed's lag. If production evidence later shows `WARNING`
  is too coarse (e.g. a persistent multi-day stall needs `CRITICAL`), a follow-on adds a lag-aware escalation;
  146 does not add it speculatively. Because the window is a fixed rolling range with idempotent upsert (D2),
  there is **no watermark to advance** — a WARNING run simply re-attempts the same window next cron tick and any
  keys that have since landed are stored then. This is strictly simpler than a "advance only proven-complete
  keys" rule.
- **D5a — the production construction path is fully specified.** The `@flow` accepts injected stores/adapter/clock
  for tests, but its **production entry** (the `None`-defaulted branch, mirroring `run_forecast_cycle`'s lazy
  construction) MUST build every dependency explicitly — Phase 2b names them so the acceptance tests have a
  concrete contract:
  1. **Stores** — `station_store`, `forcing_store` (`HistoricalForcingStore`), and the Plan 082
     `gateway_polygon_store` (`GatewayPolygonBindingStoreLike`) — same production store set `run_forecast_cycle`
     assembles for the reanalysis-role resolution path. **Model-agnostic ingest (LOCKED, owner 2026-07-24, D5)
     needs no `ModelStore`/`StationGroupStore`** — the earlier draft's requirement-resolution dependencies are
     removed entirely, not merely deferred.
  2. **Recap adapter construction** mirrors `_build_recap_forecast_adapter` (`run_forecast_cycle.py:413-466`):
     requires `SAPPHIRE_CONFIG` → `load_recap_gateway_config(Path(config_path))`, `load_recap_api_key()`,
     `build_recap_client_config(...)`, `RecapClient(...)`, and a `StoreBackedGatewayPolygonResolver(
     gateway_polygon_store)`. **Failure behavior (raised, not swallowed):** missing `SAPPHIRE_CONFIG` /
     missing `[adapters.recap_gateway]` section → `ConfigurationError`; missing `gateway_polygon_store` →
     `ConfigurationError` (same guard as `recap_gateway.py:437-441`).
  **Production-construction acceptance tests (Phase 2b):** (a) missing `SAPPHIRE_CONFIG` raises
  `ConfigurationError`; (b) missing `gateway_polygon_store` raises `ConfigurationError`.
- **D6 — snow units stay unresolved; end-to-end reachability is PLUMBING-only.** `convert=None` retained
  (`recap_gateway.py:1111` passes `variable.convert`); the antecedent series flows through with correct
  shape/provenance, not canonical magnitudes. `CANONICAL_FORCING_SCHEMA` is NOT expanded and default model-onboarding
  feature-availability is NOT changed, so 146 **cannot by itself onboard a real snow-fed FI model** — it verifies
  the read-path plumbing with an **injected test model** whose `past_dynamic_features` include the snow params.
  The unit-resolution + canonical-schema + onboarding-availability follow-on is an **explicit dependency of Plan
  139** (the 12300 SWE model), not a claim 146 makes. This keeps 146 honest about what "reaches a model" means.
- **D7 — a DEDICATED `PipelineCheckType`, not reuse of `WEATHER_HISTORY_INGEST`.** The sibling flow's health
  record is keyed `PipelineCheckType.WEATHER_HISTORY_INGEST` (`ingest_weather_history.py:305`,
  `_append_weather_history_health_record`). Reusing it for recap-snow ingest would **conflate** MeteoSwiss and
  Recap ingest health under one queryable key — an operator filtering `WEATHER_HISTORY_INGEST` could not tell
  which feed is degraded. 146 adds `PipelineCheckType.RECAP_SNOW_REANALYSIS_INGEST =
  "recap_snow_reanalysis_ingest"` (`types/enums.py:151-165`) with `subject="recap_snow_reanalysis_ingest"`.
  **No migration needed** — `pipeline_health.check_type` is a bare `sa.Text` column with no `CHECK` constraint
  (`db/metadata.py:1564`), unlike `status` (`:1566-1570`). Phase-2 tests cover: (a) the new enum member exists
  and round-trips its `.value`, and (b) the health-history API filters by the new check type without conflating
  it with `WEATHER_HISTORY_INGEST`.

## Non-goals (owned elsewhere)
- The FUTURE snow-forecast channel + the aggregation fix (**Plan 145**). Snow unit resolution + canonical-schema
  expansion + onboarding feature-availability (Plan 139 follow-on; gated on units).
- ERA5-land recap read-routing (a parallel gap; the shared adapter can fetch it, but 146 neither ingests nor
  routes it — though D1's parametrized flow is reusable for it). The MeteoSwiss weather-history flow /
  `_ReanalysisAdapter` protocol (unchanged). The snow model itself. Single-mode recap-snow read selection (D4).
- Resolving which models need which snow variables — a per-model READ-side concern (D4/D6). The ingest flow
  fetches the full ceiling for every in-scope HRU regardless of model requirements (D5, LOCKED 2026-07-24); no
  `ModelStore`/`StationGroupStore`/requirement-resolution lives in the ingest flow.
- Backfill-depth sizing (from a model's `lookback_steps`) and any enforced "sufficient antecedent depth before a
  model goes active" onboarding prerequisite (D2a; Plan 139/onboarding-flow follow-on). 146's ingest flow is
  model-agnostic and reports health-by-effect (new rows landing), not historical depth — it cannot itself
  guarantee backfill-before-operational.

## Phases (red-first; each task lists In/Out + Verify)
### Phase 1 — Provenance + attribution (D3)
- **1a — provenance member + attribution.**
  **In:** add `ForcingSource.RECAP_SNOW_REANALYSIS = "recap_snow_reanalysis"` + its `SOURCE_ATTRIBUTIONS` entry
  populated with the owner-confirmed licence text (D3 READY gate) (`forcing_sources.py:18-47`); a focused
  `tests/unit/types/test_forcing_sources.py` asserting (a) every `ForcingSource` member has a
  `SOURCE_ATTRIBUTIONS` entry (completeness — not covered today; blocks a **missing** entry, which a membership
  assertion CAN prove), (b) `SOURCE_ATTRIBUTIONS[RECAP_SNOW_REANALYSIS]` **equals the exact owner-confirmed string
  recorded in D3** (an equality assertion — the only mechanism that rejects a placeholder; membership alone cannot,
  since values are unrestricted strings, `forcing_sources.py:38-47`), (c) `RECAP_SNOW_REANALYSIS.value` round-trips
  to the persisted `_SNOW_SOURCE` literal (`recap_gateway.py:323`).
  **Out:** `CANONICAL_FORCING_SCHEMA` (NOT expanded — units unresolved, D6); read routing (Phase 3); ingest
  (Phase 2); the `recap_era5_land_reanalysis` literal.
  **Verify:** `uv run pytest tests/unit/types/test_forcing_sources.py tests/unit/types/test_forcing_provenance.py`.
  *(Note: `test_forcing_schema.py` asserts exactly the current five params and is intentionally left untouched —
  it guards that D6 is honoured, not that snow was added.)*

### Phase 2 — Owning ingest flow + persistence (D1/D2/D2a/D5/D7) — the blocker
- **2a — typed snow-reanalysis adapter method (D5).**
  **In:** add `RecapGatewayReanalysisAdapter.fetch_snow_reanalysis(station_configs, start, end,
  variables=None)` (NON-Protocol, mirrors `fetch_snow_forecast` `recap_gateway.py:898-982`) looping per
  `(hru, variable)` with `_guarded_snow_fetch` inside `try/except RecapSnowUnavailableError`; `variables` is an
  optional allowlist override defaulting to the full D1 ceiling — never a model-derived requirement map (D5,
  LOCKED 2026-07-24) — returning the new `SnowReanalysisFetchResult(rows, unavailable, attempted)`, where
  `unavailable`'s value type is `Mapping[str, str]` (variable → `RecapSnowUnavailableError.code`), not a bare
  `frozenset[str]`, added to `types/weather.py` (next to `SnowForecastFetchResult:80-95`). An adapter test
  proving: partial (one var `source_data_missing`, others stored + named with their code in `unavailable`),
  total (all requested missing → `unavailable` keys == `attempted` keys), `subscription_not_found` vs
  `source_data_missing` recorded with distinct codes, and fatal (`RecapAuthError` propagates).
  **Out:** `fetch_reanalysis` and the `WeatherReanalysisSource` Protocol (UNTOUCHED — the `test_recap_gateway.py:405`
  conformance test stays green); the ERA5-land branch of `_rows_for_variable`; any model-requirement resolution
  (D5 — the adapter method takes no model/assignment input).
  **Verify:** `uv run pytest tests/unit/adapters/test_recap_gateway.py tests/unit/types/test_weather.py`.
- **2b — dedicated recap-reanalysis ingest flow (D1/D2/D5/D5a/D7).**
  **In:** a standalone `@flow` with injected `station_store`, `forcing_store`, `gateway_polygon_store`, `adapter`,
  and `clock` (production `None`-branch builds them per D5a — recap adapter via `SAPPHIRE_CONFIG` +
  `load_recap_gateway_config`/`load_recap_api_key`/`RecapClient` + `StoreBackedGatewayPolygonResolver`; **NO
  `ModelStore`/`StationGroupStore`** — the flow is MODEL-AGNOSTIC, D5 LOCKED 2026-07-24). Takes a `variables`
  **ceiling** param (default `("swe","snow_depth","snowmelt")` — D1: an allowlist, fetched in full every run, not
  model-scoped), a `window_days` override (default ~21), and an optional `station_ids` subset (D2a backfill).
  Computes the rolling window `start = clock() - window_days` → `clock()` (mirror
  `ingest_weather_history.py:424-425`), resolves reanalysis-role stations (sibling `_reanalysis_sources` pattern
  `ingest_weather_history.py:280-289`), **reconciles that PRE-resolution in-scope set against the stations that
  survive `_prefilter` + polygon resolution** (finding #2 — a dropped in-scope station surfaces as `WARNING`,
  named, never a silent shrink), calls `fetch_snow_reanalysis` with the full `variables` ceiling for every
  surviving in-scope HRU, persists `result.rows` → `HistoricalForcingStore.store_forcing` under Phase-1
  provenance, and — **with the health-by-EFFECT before/after `fetch_latest_valid_time` readback taken per
  `(station_id, parameter)` and aggregated per `(HRU, variable)` (D5, finding #1)** — emits a
  `PipelineHealthRecord` keyed `RECAP_SNOW_REANALYSIS_INGEST` (D7). **No watermark.**
  **Acceptance tests:** (i) real flow entry point persists rows AND reports `OK` only when the per-`(station_id,
  parameter)` store horizon advanced for every attempted non-`subscription_not_found` key (health-by-EFFECT,
  finding #1); (ii) the D5 outcome classification — `subscription_not_found` excluded from WARNING (logged at
  most once at `INFO`); `source_data_missing` partial → `WARNING` + stored rows; total loss (all attempted
  non-`subscription_not_found` keys missing) → `WARNING`; all keys succeed but no horizon advance → `WARNING`
  `no_horizon_advance`, never `OK`; config/auth/unanticipated → raise; (iii) **multi-HRU/multi-variable
  partial-stall** (finding #1) — one key advances, one key stalls (empty/duplicate) in the same run → `WARNING`
  naming the stalled `(station_id, parameter)`/`(HRU, variable)` key, never `OK`; (iv) **mixed-resolution**
  (finding #2) — one resolvable + one unresolvable in-scope station in the same run → the unresolvable station
  surfaces as `WARNING` naming the station + drop reason, not a silently-shrunk `OK`; (v) **D2a backfill** — a
  station bound AFTER a normal scheduled run is backfilled by a wide-`window_days` run over an explicit
  `station_ids` subset (mechanism only — sizing derivation is onboarding-owned, D2a); (vi) **production
  construction (D5a)** — missing `SAPPHIRE_CONFIG` → `ConfigurationError`, missing `gateway_polygon_store` →
  `ConfigurationError`; (vii) **enum/API** — `RECAP_SNOW_REANALYSIS_INGEST` round-trips and the health API
  filters it distinctly from `WEATHER_HISTORY_INGEST` (D7). (Fake-store flow tests simulate dedup but CANNOT
  prove `on_conflict_do_nothing`; the physical idempotency proof is 2d.)
  **Out:** watermark storage; MeteoSwiss flow; read routing; model-requirement resolution (D5 —
  `ModelStore`/`StationGroupStore` injection removed).
  **Verify:** `uv run pytest tests/unit/flows/test_ingest_recap_reanalysis.py tests/unit/types/test_enums.py tests/unit/api/test_pipeline_health.py`.
- **2c — register the scheduled deployment (D2).**
  **In:** `SCHEDULE_INGEST_SNOW_REANALYSIS` (default a daily cron, e.g. `"0 5 * * *"`) as a `DeploymentSpec` with
  `concurrency_limit=1` in `register_deployments.py` (mirror `ingest-weather-history`, `:107-113`) +
  `docker-compose.yml`.
  **Out:** any separate backfill command — the initial/newly-bound backfill is the same flow run with a wide
  `window_days` (D2/D2a), documented in Phase 4, not a new entry point.
  **Note (finding #8):** because ingest is model-agnostic (D5), registering the deployment now — before Plan 139
  onboards any recap-reanalysis snow station (basin 12300) — is harmless: the flow simply resolves zero in-scope
  snow HRUs and is a benign no-op, never a manufactured WARNING. Early registration carries no false-alarm risk.
  **Verify:** `uv run pytest tests/unit/cli/test_register_deployments.py`.
- **2d — PostgreSQL physical-idempotency integration test (major review fix).** `store_forcing` returns
  `None` and the flow can only report `len(records)`, so a fake-store flow test cannot prove
  `on_conflict_do_nothing`. **In:** an integration test using `PgHistoricalForcingStore` that runs THIS flow twice
  over identical windows/versions and asserts the **physical row count is unchanged** — the one thing new to 146
  (that the flow's repeated writes over an overlapping window produce zero duplicate physical rows). "No
  duplicates" is defined against the table's existing natural-key constraint (see `db/metadata.py`); the test
  asserts the concrete column list in its own code, not this plan, so the plan does not carry a schema copy that
  can drift.
  **Out:** unit-level dedup (already in 2b); **version-supersession semantics — DELIBERATELY NOT re-proven here
  (minor review fix).** `store_forcing`'s changed-`version` → superseding-audit-row + latest-read behavior is a
  LOCKED, store-level acceptance test independent of any ingest flow
  (`tests/integration/store/test_historical_forcing_supersession.py`, "Milestone 071-reanalysis-core criterion 4,
  LOCKED"); nothing in D1–D7 touches that logic (146 only calls `store_forcing` with its existing semantics
  unchanged), so re-deriving it against this second code path is redundant. A one-line comment in the 2d test
  points at that locked test instead.
  **Verify:** `uv run pytest -m integration tests/integration/flows/test_ingest_recap_reanalysis_pg.py`.

### Phase 3 — Read-side routing to all consumers (D4) — depends Phases 1,2
- **3a — snow read tier + consumer proofs.**
  **In:** add the snow tier to `_PRIORITY_CHAINS` **and** `DEFAULT_PARAMETERS` (`hybrid_reanalysis_factories.py:37-57`),
  wiring the snow `PerSourceStoreReader`; prove the **same stored snow series** reaches `past_dynamic` via
  **training** (`training_data.py:185,194`), **hindcast** (`hindcast.py:287,305`), and **live**
  (`operational_inputs.py:410-421`) read paths (three separate consumer tests over a fake/test store, each using
  an injected model whose `past_dynamic_features` include the snow params — D6); a **dashboard forcing-endpoint
  test** (`api/routes/stations.py:498-505`) asserting the endpoint now surfaces stored snow series (the accepted
  `DEFAULT_PARAMETERS` side effect, D4).
  **Out:** single-mode routing (`store_backed_reanalysis.py` — UNTOUCHED per D4; per minor review, NO dedicated
  single-mode regression test is added — the scope boundary lives in D4 prose + a one-line comment at the
  `select_reanalysis_source(mode="single")` call site referencing D4, since 146 neither modifies nor risks that
  path).
  **Verify:** `uv run pytest tests/unit/adapters/test_hybrid_reanalysis_factories.py tests/unit/adapters/test_hybrid_reanalysis.py tests/unit/services/test_training_data.py tests/unit/services/test_hindcast.py tests/unit/services/test_operational_inputs.py tests/unit/api/test_stations_forcing.py`.

### Phase 4 — Docs
- **In:** `docs/standards/orchestration.md` (new ingest flow/schedule + rolling-window rationale + the **D2a
  newly-bound-station wide-`window_days` backfill runbook**, including the backfill-SIZING derivation an
  operator/onboarding flow uses — `window_days` from `data_requirements.lookback_steps × time_step`
  (`types/model.py:264-275`) — and an explicit flag that enforced depth-sufficiency / backfill-before-operational
  is a Plan-139/onboarding-flow responsibility, not this flow's, D2a findings #3/#6), `docs/v0-scope.md`,
  `docs/standards/logging.md` (ingest outcome/event names + the OK/WARNING classification, the
  `subscription_not_found` at-most-once-INFO exclusion, and `RECAP_SNOW_REANALYSIS_INGEST` check type), the
  relevant touchpoint map.
  **Out:** code (all in Phases 1-3).
  **Verify:** `uv run pytest tests/unit/docs 2>/dev/null || true` then `rg -n "recap_snow_reanalysis|RECAP_SNOW_REANALYSIS_INGEST" docs/` shows the new flow/check-type/runbook are documented (docs-only phase; no runtime gate).

## Phase dependency graph
```json
{
  "phases": [
    {"id": "1", "task": "provenance member + attribution (D3)", "depends_on": []},
    {"id": "2a", "task": "typed fetch_snow_reanalysis + SnowReanalysisFetchResult (D5)", "depends_on": ["1"]},
    {"id": "2b", "task": "owning ingest flow + health record (D1/D2/D5/D7)", "depends_on": ["2a"]},
    {"id": "2c", "task": "register scheduled deployment (D2)", "depends_on": ["2b"]},
    {"id": "2d", "task": "PostgreSQL idempotency + supersession integration test", "depends_on": ["2b"]},
    {"id": "3a", "task": "read-side snow tier + consumer proofs (D4)", "depends_on": ["1", "2b"]},
    {"id": "4", "task": "docs", "depends_on": ["2c", "2d", "3a"]}
  ]
}
```

## Dependencies
- **082** (gateway reanalysis adapter + polygon bindings) · **145** (canonical snow aggregation + the
  `_guarded_snow_fetch`/`RecapSnowUnavailableError` boundary D5 reuses). Client pin ≥ 9340e40 (#127). Blocks
  **139** (antecedent SWE for the 12300 model — 139 ALSO owns the unit/canonical-schema/onboarding follow-on, D6,
  AND the backfill-depth-sizing / before-operational depth-sufficiency enforcement, D2a) and **144** (any
  snow-lookback model).

## Open items / to confirm
- *(Resolved — owner 2026-07-24: **D2 = scheduled ingest deployment, FIXED ROLLING WINDOW** — NO persisted
  watermark. Idempotency from `on_conflict_do_nothing`; concurrency from `concurrency_limit=1`. The initial
  backfill is the same flow run with a wide `window_days`. See D2. This closes the former "watermark storage"
  and read-then-advance race open items entirely.)*
- *(Resolved — owner 2026-07-24: **ingest flow is MODEL-AGNOSTIC** — fetches the full snow ceiling for every
  in-scope HRU every run; per-HRU subscription is discovered at runtime via `RecapSnowUnavailableError.code`,
  never pre-computed from model/group requirements. See D5. This closes the former
  `required_snow`/`ModelStore`/`StationGroupStore`/group-scoping/`no_snow_requirement` design entirely.)*
- **Snow attribution string (HARD READY GATE — STILL OPEN)** — the exact acknowledgement/licence text is not
  repo-grounded, so it MUST be obtained from the owner and **recorded in D3 here** BEFORE this plan goes READY.
  No provisional/placeholder string ships. Two complementary guards (D3, Phase 1): the completeness test blocks a
  **missing** entry, and an **equality** test asserts the member's value equals the exact recorded string (the
  only thing that rejects a placeholder — a membership check cannot, since values are free strings,
  `forcing_sources.py:38-47`). This is the one item that must close before READY; the plan stays DRAFT until the
  owner text is folded into D3.
- **Snow unit magnitudes + canonical-schema/onboarding availability** — shared follow-on with 145; an explicit
  **Plan 139** dependency (D6). Gates onboarding a real snow-fed FI model; 146 proves plumbing with a test model.
- **Backfill-depth sizing + before-operational depth-sufficiency enforcement** — NOT this flow's responsibility
  (D2a, findings #3/#6); an explicit **Plan 139**/onboarding-flow follow-on. 146 ships only the tested
  wide-window backfill MECHANISM (operator runbook + flow param), not a guarantee that backfill completes to a
  model's required depth before that model goes active.
- **ERA5-land recap read-routing + single-mode recap-snow selection** — noted parallel gaps, out of scope here
  (D1's parametrized flow and a future endpoint-provenance selector would own them).
