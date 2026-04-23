# Plan 072 — v0b weather-history: hybrid forcing resolver

**Status**: DRAFT
**Date**: 2026-04-22 (revision 3 — post round-2 review; integrates B1
decision to drop the NWP-archive tier from v0b, simplifies the plan
substantially.)
**Depends on**: **Plan 071** (hard — `ForcingSource` enum from
T1; `SOURCE_ATTRIBUTIONS` dict; `MeteoSwissOpenDataReanalysisAdapter`
from T4/T5; latest-version supersession filter from T2). Plan 071
must be at `status: READY` with T1 + T2 complete before this plan
starts. Full DONE on Plan 071 is preferred for end-to-end integration.
**Scope**: v0b weather-history Phase B. Introduce three components
and one config switch:
(a) `PerSourceStoreReader` — thin wrapper over `HistoricalForcingStore`
that reads rows for a single `ForcingSource` tag and returns them as
`RawHistoricalForcing`;
(b) `HybridForcingSource` — chains per-source `WeatherReanalysisSource`
instances with per-parameter priority, returns a unified deduplicated
row stream;
(c) a single factory `default_hybrid_forcing_source` wiring the v0b
priority chain;
(d) `DeploymentConfig.reanalysis_source: Literal["single", "hybrid"]
= "single"` — opt-in flag at the `DeploymentConfig` boundary.
Wire the hybrid into the two read-side injection points — hindcast
(`flows/run_hindcast.py:194`) and forecast cycle
(`flows/run_forecast_cycle.py:462`). Onboarding
(`flows/onboard.py:133`) stays single-source because its role is
per-source ingest+readback, not multi-source blending.
**Note on what dropped from rev 2**: the `WeatherForecastBackedReanalysisSource`,
the NWP-archive store method, `min_lead_hours` leakage-floor design,
and the training-vs-operational factory split are all deferred per
user direction (B1). The MeteoSwiss RprelimD 2-day lag → operational
live-tail gap is documented as a known limitation; v0c may revisit
via a train/test-matched NWP-archive design.

---

## Context

### Why now

- Plan 071 closes the 2026-04-onwards forward accumulation gap with
  MeteoSwiss open-data, writing rows with source tags
  `meteoswiss_rprelimd`, `meteoswiss_tabsd`, `meteoswiss_tmind`,
  `meteoswiss_tmaxd`. v0b ML models also need `camels-ch` rows for
  2020-and-earlier training data.
- Today's read-side (`StoreBackedReanalysisSource`) picks its source
  tag from `station_config.nwp_source` — a per-station fixed value.
  That cannot serve multiple sources per parameter chained by
  priority. Without a resolver, each station is stuck on one source.
- A resolver that chains sources per-parameter with priority closes
  the "multiple sources per station" problem: post-2026-04 valid_times
  are served by MeteoSwiss; pre-2026-04 valid_times fall through to
  CAMELS-CH.

### Priority chains (v0b)

**Precipitation**: `METEOSWISS_RPRELIMD → CAMELS_CH`

**Temperature (mean)**: `METEOSWISS_TABSD → CAMELS_CH`

**Temperature (min)**: `METEOSWISS_TMIND → CAMELS_CH`

**Temperature (max)**: `METEOSWISS_TMAXD → CAMELS_CH`

CAMELS-CH covers the pre-2020 training window; MeteoSwiss covers
post-2026-04 forward-accumulation. Dates **between 2020 and 2026-04**
have no coverage from either source (a known gap that CAMELS-CH
extension or a MeteoSwiss commercial-archive ingestion would fill; out
of scope here).

### Principle

**Priority at read time, not at write time.** Each source writes with
its own immutable tag; the resolver picks per
`(station_id, valid_time, parameter)` at fetch time. Preserves
Plan 071's audit trail (no tag mutation), keeps sources mutually
independent, and makes the priority chain visible in one place.

**Opt-in first, default later.** `DeploymentConfig.reanalysis_source
= "single"` preserves v0a behaviour. Operators flip to `"hybrid"`
per-deployment. Promotion to default is a v0c decision once skill
comparison validates no regression.

**Known live-tail gap (B1 acknowledgement).** MeteoSwiss RprelimD
publishes with a 2-day lag. At forecast-cycle time, the last ~48h of
the lookback window may be empty in `historical_forcing`. The hybrid
returns no row for those valid_times. **Today's downstream behaviour**
(per `services/operational_inputs.py:167–186`): `_raw_forcing_to_dataframe`
returns `None` for an empty result, and the assembly step logs
`operational_inputs.no_past_dynamic` at `warning` and substitutes an
empty DataFrame. What the model does from there is **model-dependent** —
some models may raise a shape error, others may impute, others may
forecast with degraded features. This is the status quo, NOT a new
failure mode this plan introduces: the same behaviour exists today
when a station's CAMELS-CH coverage is incomplete. Rollout guidance
in T6 docs: deployments that flip `reanalysis_source` to `"hybrid"`
should coordinate with model-owners on truncation tolerance; models
sensitive to missing lookback should stay on `"single"` until they
are explicitly validated against the gap pattern. A v0c plan may
re-introduce an NWP-archive-backed source with train/test-matched
design; this plan explicitly does not.

### Non-goals

- **No NWP-archive-backed reanalysis source.** Dropped from v0b per B1.
  `ForcingSource.NWP_ARCHIVE` is reserved in the enum (Plan 071 T1)
  but has no implementation here.
- **No training/operational factory split.** With no NWP-archive
  tier, the distinction is irrelevant — one factory suffices.
- **No `min_lead_hours` floor.** Irrelevant without NWP-archive.
- **No store method additions.** Reuses
  `HistoricalForcingStore.fetch_forcing` (now latest-version-filtered
  per Plan 071 T2).
- **No change to `WeatherReanalysisSource` Protocol.**
- **No change to `historical_forcing` schema.**
- **No change to `StoreBackedReanalysisSource`.** The new
  `PerSourceStoreReader` lives alongside it.
- **No automatic model retrain trigger** when the flag is flipped.
  A deployment that flips `reanalysis_source` from `"single"` to
  `"hybrid"` mid-model-life may surface distribution-shift artifacts
  if the model was trained against a subset of the hybrid's chain.
  Plan 066 (retrain strategy) owns that policy; this plan
  cross-references it (D7).
- **No hybrid wiring into `onboard_stations_flow`.** Onboarding does
  source-specific ingest + readback verification, not blended read.
- **No async concurrency.** Protocol is synchronous. Hybrid uses a
  `ThreadPoolExecutor` as an opt-in kwarg; default is serial.
- **No promotion of hybrid to default in v0b.** Opt-in flag only.

### Inputs

- `src/sapphire_flow/protocols/adapters.py:47–55` —
  `WeatherReanalysisSource.fetch_reanalysis(station_configs, start,
  end, parameters) -> list[RawHistoricalForcing]`. Synchronous.
- `src/sapphire_flow/adapters/store_backed_reanalysis.py:13–53` —
  existing reader; untouched.
- `src/sapphire_flow/protocols/stores.py:651–682` —
  `HistoricalForcingStore.fetch_forcing(station_id, source, start,
  end, parameters=None, version=None, member_id=None)`. After Plan
  071 T2, returns latest `version` per logical key.
- `src/sapphire_flow/types/forcing_sources.py` — created by Plan 071
  T1. `ForcingSource` enum + `SOURCE_ATTRIBUTIONS`.
- `src/sapphire_flow/types/enums.py:55` — existing `ForcingType(Enum)`;
  independent concept from `ForcingSource` (docstring clarification
  in Plan 071 T1 already covers this; this plan does not touch).
- `src/sapphire_flow/config/deployment.py:62` — **`DeploymentConfig`
  (Pydantic `BaseModel`)**; `reanalysis_source` field added here.
- `src/sapphire_flow/services/operational_inputs.py:115` —
  `assemble_station_operational_inputs(forcing_source, ...)` — real
  injection function.
- `src/sapphire_flow/flows/onboard.py:133` —
  `StoreBackedReanalysisSource(forcing_store)`. **Untouched** (D8).
- `src/sapphire_flow/flows/run_hindcast.py:194` —
  `StoreBackedReanalysisSource(forcing_store)`. Wired by T4.
- `src/sapphire_flow/flows/run_forecast_cycle.py:462` —
  `StoreBackedReanalysisSource(forcing_store)`. Wired by T4.
- `docs/architecture-context.md:137–150`, `docs/v0-scope.md:199–209` —
  updated in T6.
- `docs/standards/logging.md` §Event naming — enforces
  `{entity}.{past_tense_action}`, single-word entity.

---

## Architecture decisions

| # | Decision | Rationale |
|---|---|---|
| D1 | **`PerSourceStoreReader`** at `src/sapphire_flow/adapters/per_source_store_reader.py`. Implements `WeatherReanalysisSource`. Ctor: `__init__(self, *, forcing_store: HistoricalForcingStore, source: ForcingSource)`. `fetch_reanalysis(...)` calls `forcing_store.fetch_forcing(station_id=cfg.station_id, source=self._source.value, start=start, end=end, parameters=parameters)` for each station in `station_configs`, transforms `HistoricalForcingRecord` → `RawHistoricalForcing`. Does NOT read `station_config.nwp_source` — uses only the ctor-fixed source tag. | Option 1c from the design discussion. Clean conceptual separation from `StoreBackedReanalysisSource` (which reads source from station config). No modification to the existing class, no behavioural change to existing callers. Hybrid wires one `PerSourceStoreReader` per active `ForcingSource` tag. |
| D2 | **`HybridForcingSource`** at `src/sapphire_flow/adapters/hybrid_reanalysis.py`. Implements `WeatherReanalysisSource`. Ctor: `__init__(self, *, sources: dict[ForcingSource, WeatherReanalysisSource], priority: Mapping[str, tuple[ForcingSource, ...]])`. `fetch_reanalysis(...)` fans out to each source **serially**, collects rows into a dict keyed on `(station_id, valid_time, parameter)`, walks each key's parameter-specific priority list and keeps the first row whose source tag matches. Missing-row-on-exhaustion → no row emitted (not an error). **No `ThreadPoolExecutor` opt-in kwarg.** The concrete `PgHistoricalForcingStore` wraps a single SQLAlchemy `sa.Connection`, which is documented-not-thread-safe — a thread-pool mode would race on cursor state and produce intermittent `InterfaceError` or silent row-interleaving. Serial execution is the only safe default against the current store; if profiling later shows latency pressure, a future plan can introduce per-thread connection pooling and re-add the opt-in. | `dict[ForcingSource, source]` gives O(1) source lookup. Tuple priority lists are hashable, immutable, and cheap. Serial is both safe and sufficient — source calls are DB-bound and indexed; at 1000 stations × 5 sources ≈ 5000 indexed queries on one connection ≈ order-of-single-digit seconds p95. Aligns with §Non-goals "No async concurrency." |
| D3 | **Single factory `default_hybrid_forcing_source`** at `src/sapphire_flow/adapters/hybrid_reanalysis_factories.py`. Signature: `default_hybrid_forcing_source(*, forcing_store: HistoricalForcingStore, parameters_in_scope: tuple[str, ...] = ("precipitation", "temperature", "temperature_min", "temperature_max")) -> HybridForcingSource`. Wires `PerSourceStoreReader` instances for `METEOSWISS_RPRELIMD`, `METEOSWISS_TABSD`, `METEOSWISS_TMIND`, `METEOSWISS_TMAXD`, `CAMELS_CH`. Priority per §Priority chains. No training-vs-operational split (not needed after B1). **Soft dependency note**: `"precipitation"` and `"temperature"` are canonical parameter strings today (`config/onboarding.py`, CAMELS-CH adapter). `"temperature_min"` and `"temperature_max"` are introduced by Plan 071 T4 (MeteoSwiss TminD/TmaxD). If Plan 072 T3 lands before Plan 071 T4, callers invoking the default parameter set against a store that has no TminD/TmaxD rows see empty results for those two parameters — non-breaking, but worth noting. | One factory is the right shape when the chain has no NWP-archive tier. The `parameters_in_scope` kwarg lets callers restrict the hybrid (e.g., a precipitation-only model); defaults cover all v0b-registered parameters. |
| D4 | **`DeploymentConfig.reanalysis_source: Literal["single", "hybrid"] = "single"`** added to `src/sapphire_flow/config/deployment.py:62` (Pydantic `BaseModel`). Opt-in for v0b. When `"single"`, all three flows retain current behaviour. When `"hybrid"`, hindcast + forecast-cycle use `default_hybrid_forcing_source(...)`; onboarding unaffected regardless. | Zero-regression rollout. Preserves locked §A12 decision until v0c promotes the hybrid. Per-deployment opt-in allows A/B skill comparison. |
| D5 | **Structured logging** (per `docs/standards/logging.md` §Event naming — single-word entity, past-tense action):<br>- `forcing.source_selected` — debug-level; fields `station_id`, `valid_time`, `parameter`, `winning_source` (string value), `available_sources` (list of strings).<br>- `forcing.resolution_completed` — info-level rollup emitted at end of each `fetch_reanalysis` call; fields `station_count`, `row_count`, `source_counts` (dict), `elapsed_ms`. <br>- No raw payloads logged at any level. | Event names align with the standard's `{entity}.{past_tense_action}` pattern (entity=`forcing`, single word). Info-level rollup is the audit-hook operators see; debug-level per-selection is the deep-trace for incident investigation. |
| D6 | **Clock injection** where time-based logic appears (e.g., logging elapsed_ms uses `monotonic()` rather than wall-clock; but any wall-clock reference takes `clock: Callable[[], UtcDatetime]`). | CLAUDE.md determinism. |
| D7 | **Documented retrain dependency on config-flag flip.** When a deployment flips `reanalysis_source` from `"single"` to `"hybrid"` (or vice versa), the set of data the model sees at inference changes. Plan 066 (retrain strategy) owns the retrain policy; this plan's docs (T6 docs update) link to Plan 066's retrain-trigger section. | Per round-2 review H4: a silent config flip with no retrain is a distribution-shift risk. Documenting the dependency keeps it visible; retrain enforcement is a Plan 066 problem. |
| D8 | **Onboarding stays single-source.** `flows/onboard.py:133` is NOT rewired. Its role is source-specific ingest + readback verification, not blended read. | Revealed in round-2 review as the correct semantic — onboarding ingests from ONE source (CAMELS-CH) and verifies the write; injecting the hybrid reader there would change what "readback" verifies, which is a semantic change this plan doesn't want. |
| D9 | **Test coverage for source distribution** — integration test (T5) asserts, for a 2026-02 → 2026-05 window spanning the pre-2026-04 (CAMELS-CH) and post-2026-04 (MeteoSwiss) regimes, the row distribution is {pre: all camels-ch, post: all meteoswiss_rprelimd/tabsd/etc}. Also asserts NO row has source `NWP_ARCHIVE` (reserved but unused — sanity check). A hash-of-sources assertion (per round-2 review H5) is included: the test computes a stable hash of `(valid_time, source)` pairs and pins it, detecting silent pivot-side issues in downstream code. | The v0b behaviour this plan delivers is entirely about the source distribution; testing it directly is the right integration check. The hash assertion catches pivot-collision bugs that D4/D5 logging cannot detect. |

---

## Task list

### Phase 1 — `PerSourceStoreReader`

#### T1 — New reader class for per-tag reads

**Scope (in)**: `src/sapphire_flow/adapters/per_source_store_reader.py` with the class per D1; unit tests covering (a) reads rows for the configured source tag only, (b) ignores `station_config.nwp_source` (passes only `self._source.value` to the store), (c) transforms records correctly, (d) returns empty list when the store has no rows for that source.
**Scope (out)**: modifications to `StoreBackedReanalysisSource`; flow wiring.
**Verification**:
- `uv run ruff check src/sapphire_flow/adapters/per_source_store_reader.py`
- `uv run pyright src/sapphire_flow/adapters/per_source_store_reader.py`
- `uv run pytest tests/unit/adapters/test_per_source_store_reader.py`

**Exit**: Class implements `WeatherReanalysisSource`; unit tests green; no other file modified.

### Phase 2 — Hybrid resolver

#### T2 — `HybridForcingSource`

**Scope (in)**: `src/sapphire_flow/adapters/hybrid_reanalysis.py` per D2; logging per D5; unit tests covering (a) 3 fake sources overlap → priority-1 wins; (b) gap-filling across sources; (c) exhaustion → no row; (d) parameter-specific priority (precipitation vs temperature chains differ); (e) logging events fire with documented schema and stable under `pytest-caplog` assertions.
**Scope (out)**: factory (T3); flow wiring (T4).
**Verification**:
- `uv run ruff check src/sapphire_flow/adapters/hybrid_reanalysis.py`
- `uv run pyright src/sapphire_flow/adapters/hybrid_reanalysis.py`
- `uv run pytest tests/unit/adapters/test_hybrid_reanalysis.py`

**Exit**: Hybrid passes all tests; logging schema stable.

### Phase 3 — Factory + config flag

#### T3 — `default_hybrid_forcing_source` + `DeploymentConfig.reanalysis_source`

**Scope (in)**: `src/sapphire_flow/adapters/hybrid_reanalysis_factories.py` implementing the single factory per D3; update `src/sapphire_flow/config/deployment.py:62` (Pydantic `BaseModel`) adding `reanalysis_source: Literal["single", "hybrid"] = "single"` field with appropriate Pydantic validation; unit tests for (a) factory produces the documented chain for every parameter, (b) config defaults to `"single"`, (c) config parses `"hybrid"` cleanly, (d) invalid value raises a clear Pydantic validation error.
**Scope (out)**: flow wiring (T4).
**Verification**:
- `uv run ruff check src/sapphire_flow/adapters/hybrid_reanalysis_factories.py src/sapphire_flow/config/deployment.py`
- `uv run pyright src/sapphire_flow/adapters/hybrid_reanalysis_factories.py src/sapphire_flow/config/deployment.py`
- `uv run pytest tests/unit/adapters/test_hybrid_reanalysis_factories.py`
- `uv run pytest tests/unit/config/test_deployment_config.py::TestReanalysisSourceFlag`

**Exit**: Factory instantiates with documented chain; config flag validates cleanly.

### Phase 4 — Flow wiring

#### T4 — Wire hindcast + forecast-cycle flows to the config flag

**Scope (in)**: `src/sapphire_flow/flows/run_hindcast.py:194` — when `cfg.reanalysis_source == "hybrid"`, instantiate `default_hybrid_forcing_source(forcing_store=...)`; else keep current `StoreBackedReanalysisSource(forcing_store)`. `src/sapphire_flow/flows/run_forecast_cycle.py:462` — analogous. Flow-level unit tests covering both config settings.
**Scope (out)**: `src/sapphire_flow/flows/onboard.py:133` — **untouched** (D8).
**Verification**:
- `uv run pytest tests/unit/flows/test_run_hindcast.py`
- `uv run pytest tests/unit/flows/test_run_forecast_cycle.py`

**Exit**: Both flows use the factory when flag is set; single-source regression tests still pass with flag unset.

### Phase 5 — Integration + docs

#### T5 — Integration test (source distribution + hash assertion)

**Scope (in)**: `tests/integration/test_hybrid_weather_history_flow.py` seeding a mix of CAMELS-CH rows (for pre-2020 valid_times) and MeteoSwiss rows in `historical_forcing`; running `default_hybrid_forcing_source(...).fetch_reanalysis(...)` across a 2020 → 2026-05 window; asserting (per D9) per-date source distribution; asserting no `NWP_ARCHIVE` rows in output (with a `# remove when NWP_ARCHIVE re-introduced in v0c` comment to flag the assertion as intentional-today, not permanent); asserting a stable hash of `(valid_time, source)` pairs; asserting **no duplicate `(station_id, valid_time, parameter)` rows** — verifies Plan 071 T2's supersession filter is active end-to-end; tests the hybrid at a 1000-station scale seeded from a synthetic fixture to pin memory and timing expectations (target: `fetch_reanalysis` p95 < 10 s, resident rowset < 150 MB). MeteoSwiss rows may come from Plan 071 T5 fixtures when available; otherwise synthetic CAMELS-CH-shape rows generated in the test body are acceptable for the chaining-logic verification.
**Scope (out)**: live service hits.
**Verification**:
- `uv run pytest tests/integration/test_hybrid_weather_history_flow.py`

**Exit**: Integration test green; D9's assertions explicit in the test body.

#### T6 — Docs + memory

**Scope (in)**: update `docs/architecture-context.md` §ML-lookback (describe hybrid + single-factory + parameter chains; note `ForcingSource` vs `ForcingType` distinction already live from Plan 071); `docs/v0-scope.md` §A12 (note opt-in flag; cross-reference Plan 066 retrain-trigger); `docs/standards/logging.md` (register `forcing.source_selected`, `forcing.resolution_completed`); `docs/standards/orchestration.md` (note hybrid is opt-in via `reanalysis_source` config — no new deployments); extend `project_weather_history_source.md` memory (the file Plan 071 introduces) with: hybrid chain, opt-in flag, live-tail gap known-limitation, D7 retrain dependency.
**Scope (out)**: dashboard UI (v2); changes to §A12 that promote hybrid to default (v0c).
**Verification**:
- `grep -c "HybridForcingSource\|default_hybrid_forcing_source\|reanalysis_source" docs/` ≥ 5
- `uv run ruff check` still clean

**Exit**: Docs updated; memory updated; commit per conventional-commit + version-bump.

---

## Priority order

| Rank | Task | Effect | Effort | Why |
|------|------|--------|--------|-----|
| 1 | **T1** (PerSourceStoreReader) | High (enabler) | Low (~half day) | ~40 LOC + tests. Zero-risk new class. |
| 2 | **T2** (HybridForcingSource) | High (core deliverable) | Medium (~1 day) | ~150 LOC + tests + logging. |
| 3 | **T3** (factory + config flag) | High | Low (~half day) | One factory, one config field. |
| 4 | **T4** (flow wiring) | High (operationalizes plan) | Low (~half day) | Two `if` branches + tests. |
| 5 | **T5 + T6** (integration + docs) | Medium | Low-medium | Closeout. Parallel. |

Total scope: ~2-3 days end-to-end. Smaller than rev 2 because B1 dropped the NWP-archive component.

---

## Dependency graph

```json
{
  "phases": [
    {
      "id": "phase-1-reader",
      "tasks": ["T1"],
      "parallel": false,
      "depends_on": []
    },
    {
      "id": "phase-2-hybrid",
      "tasks": ["T2"],
      "parallel": false,
      "depends_on": ["phase-1-reader"]
    },
    {
      "id": "phase-3-factory-config",
      "tasks": ["T3"],
      "parallel": false,
      "depends_on": ["phase-2-hybrid"]
    },
    {
      "id": "phase-4-wiring",
      "tasks": ["T4"],
      "parallel": false,
      "depends_on": ["phase-3-factory-config"]
    },
    {
      "id": "phase-5-closeout",
      "tasks": ["T5", "T6"],
      "parallel": true,
      "depends_on": ["phase-4-wiring"]
    }
  ]
}
```

External dependency: Plan 071 at least at T1 (enum) + T2 (supersession)
complete. Plan 071 full-DONE is preferred so T5 can run integration
against real Plan-071 data; otherwise T5 can stub Plan-071 rows with
the CAMELS-CH-style precedent.

---

## Open questions for user review

1. **Hybrid promotion to default in v0c**: what skill-delta threshold
   (hindcast skill vs single-source baseline) promotes the hybrid?
   Recommendation: defer to Plan 066 — retrain-strategy decision, not
   resolver design.
2. **Live-tail gap policy**: when MeteoSwiss is 2-day-lagged and
   downstream model-input prep truncates the lookback, should the
   operational forecast-cycle flow (a) proceed with truncated
   lookback, (b) skip the station's forecast for that cycle, or (c)
   emit a degraded-mode forecast marker? Recommendation: (a) today's
   behaviour for missing forcing is the right default; document the
   truncation behaviour in `run_forecast_cycle.py`.
3. **NWP-archive re-introduction plan**: if the v0b rollout reveals
   that the live-tail gap causes an unacceptable rate of forecast-
   cycle failures, a future plan (call it 075 or similar) can
   re-introduce `WeatherForecastBackedReanalysisSource` with a
   train/test-matched design (both training and operational factories
   include the source, so the model sees consistent feature
   distributions). Flag this as a watch-item for v0b observability.
4. **Logging volume (D5)**: at 1000 stations × 60 days × 4 parameters
   × 2-source chain, `forcing.source_selected` fires ~480 k times per
   full hindcast. Stays at debug. Recommendation: explicit test that
   at INFO log-level, no `forcing.source_selected` events appear.

---

## Changelog

- **2026-04-22 (rev 1)** — Initial DRAFT. Priority chain included a
  daily RhresD tier that turned out not to exist in open-data.
- **2026-04-22 (rev 2)** — Rewritten after round-1 critical review.
  Added `PerSourceStoreReader`; two factories (training vs
  operational) for leakage concern; `min_lead_hours=72` floor;
  three-site injection map; sync concurrency; `ForcingType` vs
  `ForcingSource` clarification.
- **2026-04-22 (rev 3, this document)** — Simplified per user B1
  decision: dropped `NWP_ARCHIVE` from v0b scope. Removed
  `WeatherForecastBackedReanalysisSource`, the new store method,
  `min_lead_hours` design, and the training-vs-operational factory
  split. Single factory `default_hybrid_forcing_source`. Priority
  chains reduced to MeteoSwiss → CAMELS-CH per parameter. Known
  limitation: MeteoSwiss 2-day lag → operational live-tail gap,
  deferred to v0c. Factual fixes: `DeploymentConfig` location pinned
  to `src/sapphire_flow/config/deployment.py:62`; logging event name
  fixed to `forcing.resolution_completed` (was
  `forcing.hybrid_run_completed`, which violated the
  `{entity}.{past_tense_action}` + single-word-entity convention).
  Cross-reference to Plan 066 for retrain-on-flag-flip policy. D9
  hash-of-sources assertion addresses round-2 review H5.
