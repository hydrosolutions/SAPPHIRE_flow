# Plan 071 — v0b weather-history: MeteoSwiss open-data daily reanalysis adapter

**Status**: DRAFT
**Date**: 2026-04-22 (revision 3 — post round-2 critical review;
integrates A1 content-hash version + supersession, C2 converters
guard, D3 module-level attribution, E1 explicit DST handling, plus
round-2 factual fixes to run-name convention, CLI entry point, STAC
field path, DeploymentSpec cron type, and real `task.map` precedents.)
**Depends on**: none at the code level. Soft-consumed by Plan 066. Plan
072 depends on this plan's `ForcingSource` registry.
**Scope**: v0b weather-history Phase A. Implement a single adapter
(`MeteoSwissOpenDataReanalysisAdapter`) that ingests four daily gridded
products — **RprelimD** (preliminary daily precipitation), **TabsD**
(daily mean 2 m temperature), **TminD**, **TmaxD** — from the
`ch.meteoschweiz.ogd-surface-derived-grid` STAC collection (free,
CC-BY, anonymous). Reproject LV95 → WGS84 in the adapter, basin-
average via `ExactExtractGridExtractor`, persist to `historical_forcing`
with one source tag per product and a content-hash `version` that
makes upserts idempotent under MeteoSwiss republications. Wire a
decoupled post-onboarding rolling-ingest flow that catches up the last
60 days and a daily deployment that appends thereafter. Add a latest-
version supersession filter to `HistoricalForcingStore.fetch_forcing`
so downstream readers see deterministic values. Add a one-call guard
in `converters.basin_avg_to_records` to prevent future code from
accidentally routing reanalysis basin-averages into `weather_forecasts`.
Add a module-level `SOURCE_ATTRIBUTIONS` dict for CC-BY compliance
without dataclass/schema churn. **Training data source (CAMELS-CH) is
unchanged** — this plan adds forward-accumulating operational / recent-
past coverage. Plan 072 (Phase B) layers the hybrid resolver on top.

---

## Context

### Why now

- v0a ships only models that don't need past weather (lagged-discharge
  regression, climatology, persistence) or consume CAMELS-CH for
  training. v0b adds ML + conceptual rainfall-runoff models whose
  `past_dynamic_features` require a rolling recent-past weather
  stream extending to the last few days.
- `docs/architecture-context.md:137–150` anchors
  `WeatherReanalysisSource` as the Protocol;
  `StoreBackedReanalysisSource` reads from `historical_forcing` but
  today only CAMELS-CH ingestion writes to that table (CAMELS-CH
  coverage ends at 2020, leaving the 2021-onwards gap that this plan
  fills going forward).
- Plan 066 (retrain strategy) will consume this adapter's output for
  the "recent" leg of a retrain window. Plan 072 (hybrid resolver)
  depends on this plan's source-tag registry.
- v1 Nepal deployment will follow the same Protocol path with ECMWF
  IFS + ERA5-Land; the shape of this v0b adapter is a template.

### Data-source reality (investigated 2026-04-22)

- **STAC collection**: `ch.meteoschweiz.ogd-surface-derived-grid` at
  `https://data.geo.admin.ch/api/stac/v1/collections/ch.meteoschweiz.ogd-surface-derived-grid`.
- **License**: CC-BY.
- **Access**: anonymous HTTPS GET; no credentials.
- **Archive depth**: rolling 60 days for daily files (MeteoSwiss
  actively expires older items via per-item `expires` field); 14
  months for monthly aggregates. Pre-2026-03-31 daily history is not
  in open-data.
- **Daily products present**: RprelimD (precip), TabsD / TminD / TmaxD
  (temperature), SrelD (sunshine — not used).
- **Daily RhresD / RhiresD**: NOT in the open-data daily feed; only
  at monthly aggregate resolution. Daily RhiresD requires commercial
  delivery. Deferred until MeteoSwiss publishes daily RhiresD via
  open-data.
- **CRS**: Swiss LV95 (EPSG:2056). Adapter reprojects to WGS84.
- **File format**: NetCDF4, ~1.2 MB per daily file.
- **Item structure**: one STAC item per day (id like `20260311-ch`),
  multiple assets per item (one per product + CRS variant). **STAC
  metadata fields** (verified): item has `properties.updated`,
  `properties.datetime`, `properties.expires`. No `item.updated` at
  top level.
- **Asset URLs**: read hrefs from STAC items, never construct paths.
- **Attribution**: CC-BY requires source acknowledgement. Per-source
  attribution strings live in a module-level dict (D9); dashboard
  UI is v2 scope.

### Principle

**Start forward, accumulate forever.** MeteoSwiss's 60-day rolling
window cannot supply a multi-year training backfill, but we don't
need one — CAMELS-CH covers 1981–2020 training already. We need a
forward-accumulating source so v0b ML models have fresh lookback for
operational forecasting. Every day we ingest, we keep (MeteoSwiss
expires; we don't).

**Idempotent upserts under republication.** MeteoSwiss routinely
republishes a day's file as data QC completes. `version` is computed
from a content hash of the downloaded asset bytes so identical
republications are no-ops and content corrections land as a new
version alongside the old. `HistoricalForcingStore.fetch_forcing`
returns only the latest version per logical key so downstream
readers are deterministic.

**Read, don't construct, URLs.** STAC hrefs are authoritative;
constructing URLs from filename patterns is fragile.

**Preserve the forecast-path semantics.** The extractor's
`BasinAverageForecast.cycle_time` field is forecast-semantic. This
plan reuses the extractor for reanalysis where the value is a
valid-time anchor; adapter converts to `RawHistoricalForcing` (which
has no `cycle_time`) before the value escapes memory, so the on-disk
risk is zero today. A 5-line guard in
`converters.basin_avg_to_records` raises if a future caller ever
tries to route a reanalysis-sourced basin-average into
`weather_forecasts`.

### Non-goals

- **No multi-year backfill.** Open-data is rolling-60-day. §A12 /
  CAMELS-CH training unchanged.
- **No partner credentials, env var, Docker secret, or procurement.**
- **No daily RhresD / RhiresD ingestion.** Not available in open-data
  daily feed.
- **No monthly-aggregate ingestion.** v0b models use daily resolution.
- **No sub-daily / hourly grids.** MeteoSwiss hourly products are not
  in open-data.
- **No replacement of CAMELS-CH.** CAMELS-CH ingest + `"camels-ch"`
  source tag untouched.
- **No changes to `StoreBackedReanalysisSource`.** Plan 072's new
  `PerSourceStoreReader` lives alongside it.
- **No dashboard UI for attribution.** v2 scope.
- **No changes to `RawHistoricalForcing` or `HistoricalForcingRecord`
  dataclass shape.** Attribution is a source-level property, not a
  row-level one (D9).
- **No NWP-archive source.** Dropped from v0b scope per user
  direction; `ForcingSource.NWP_ARCHIVE` is reserved (value-only) in
  the enum for a potential v0c re-introduction after training-data
  maturity lets a train/test-matched design land safely. Plan 072
  now ships a MeteoSwiss-only hybrid chain.
- **No change to the `WeatherReanalysisSource` Protocol signature.**

### Inputs

- `src/sapphire_flow/protocols/adapters.py:47–55` —
  `WeatherReanalysisSource.fetch_reanalysis(station_configs, start,
  end, parameters) -> list[RawHistoricalForcing]`. Synchronous.
  `station_configs: list[StationWeatherSource]`.
- `src/sapphire_flow/adapters/store_backed_reanalysis.py:13–53` —
  existing reader; untouched.
- `src/sapphire_flow/adapters/meteoswiss_nwp.py` — Plan 067 STAC
  adapter precedent; also anonymous access.
- `src/sapphire_flow/preprocessing/exact_extract_grid_extractor.py:44–193` —
  signature `extract(grid, configs, basins, cycle_time, nwp_source)`
  (positional); reads `grid["valid_time"]` (line 103); handles
  missing `member` dim gracefully (lines 99–102). Returns
  `dict[StationId, BasinAverageForecast]`.
- `src/sapphire_flow/preprocessing/converters.py:46–72` —
  `basin_avg_to_records` writes `BasinAverageForecast.cycle_time`
  into `weather_forecasts.cycle_time` (NOT NULL). C2 guard is added
  here.
- `src/sapphire_flow/protocols/stores.py:651–682` —
  `HistoricalForcingStore.fetch_forcing`. **Supersession change** (A1,
  T2): return only latest `version` per
  `(station_id, source, valid_time, parameter, spatial_type, band_id,
  member_id)`.
- `src/sapphire_flow/store/historical_forcing_store.py:55–80` — same
  change at the concrete-backend level.
- `alembic/versions/0004_add_historical_forcing.py:22–70` —
  `historical_forcing` schema; natural key includes `version`. A1
  keeps the natural key unchanged; supersession is a read-time
  SELECT filter, not a schema change.
- `src/sapphire_flow/exceptions.py` — hierarchy: `SapphireError →
  AdapterError → {NoCycleAvailableError, BudgetExceededError}`;
  `SapphireError → ExtractionError`. Reuse.
- `src/sapphire_flow/types/enums.py:55` — existing `ForcingType(Enum)`
  has `NWP_ARCHIVE = "nwp_archive"` for WMO skill-interpretation.
  Independent concept from `ForcingSource` (D2).
- `src/sapphire_flow/cli/register_deployments.py:24–30` —
  `DeploymentSpec` dataclass with `cron: str | None` field (plain
  cron string, not a `CronSchedule` object). `_build_specs()` at
  line 33; module `main()` at line 151 (no subcommand, no
  `--dry-run`).
- `src/sapphire_flow/tools/record_fixtures.py` — Plan 021 recording
  tool precedent (lives inside the package).
- `src/sapphire_flow/adapters/camelsch_adapter.py:132` — existing
  CAMELS-CH ingest writes source tag `"camels-ch"`.
- `src/sapphire_flow/flows/train_models.py:435` +
  `src/sapphire_flow/flows/onboard_model.py:735` — **real `task.map`
  precedents** (not Plan 068, which is still DRAFT).
- `pyproject.toml:34` — `rioxarray >= 0.19.0` is **already a runtime
  dep** (no pyproject change needed).
- `docs/architecture-context.md:137–150`, `docs/v0-scope.md:199–209`,
  `docs/standards/orchestration.md`, `docs/standards/logging.md` —
  updated in T9.
- `https://opendatadocs.meteoswiss.ch/c-climate-data/c3-ground-based-climate-data`,
  `https://opendatadocs.meteoswiss.ch/general/terms-of-use`.

---

## Architecture decisions

| # | Decision | Rationale |
|---|---|---|
| D1 | **One adapter for all four products.** Class: `MeteoSwissOpenDataReanalysisAdapter` at `src/sapphire_flow/adapters/meteoswiss_open_data_reanalysis.py`. Implements `WeatherReanalysisSource`. Configurable per-instantiation variable allowlist; v0b default `{rprelimd, tabsd, tmind, tmaxd}`. `fetch_reanalysis` iterates STAC items within `[start, end]`, downloads + extracts each requested product, returns rows with the product's source tag. | One STAC endpoint, one CRS pipeline, one HTTP client. Allowlist kwarg keeps SrelD (and any future additions) forward-compat. |
| D2 | **`ForcingSource(Enum)` — plain `Enum` with string values.** At `src/sapphire_flow/types/forcing_sources.py`. Members: `METEOSWISS_RPRELIMD = "meteoswiss_rprelimd"`, `METEOSWISS_TABSD = "meteoswiss_tabsd"`, `METEOSWISS_TMIND = "meteoswiss_tmind"`, `METEOSWISS_TMAXD = "meteoswiss_tmaxd"`, `CAMELS_CH = "camels-ch"` (hyphen — existing on-disk literal), `NWP_ARCHIVE = "nwp_archive"` (reserved — no v0b consumer; kept for potential v0c re-introduction). **Distinct from `ForcingType`** (`types/enums.py:55`) which is the WMO-aligned skill-interpretation tag; docstring on `ForcingSource` spells out the distinction. | Plain `Enum` matches repo convention (~30 enums; only `FlowRunState` uses `StrEnum`). Hyphenated `camels-ch` preserves existing literal — zero migration risk; no grep-and-replace. Reserving `NWP_ARCHIVE` costs one line and avoids an enum edit if v0c wants it back. |
| D3 | **Enum-alongside, not enum-replaces.** The `ForcingSource` enum is introduced; existing string literals (`"camels-ch"` at ~40 sites) stay as-is. New code uses `ForcingSource.CAMELS_CH.value`. Future housekeeping plan may migrate legacy call sites. | Zero-risk rollout. The original rev-1 "grep and replace" was behaviour-changing. |
| D4 | **STAC asset iteration reads hrefs from the items endpoint.** For each day in `[start, end]`, adapter calls `GET /collections/ch.meteoschweiz.ogd-surface-derived-grid/items?datetime=<d>/<d>&limit=100`, filters assets by product token + `*.swiss.lv95_*` suffix, downloads each matching asset via its `href`. Days older than MeteoSwiss's rolling window return zero results; adapter treats that as a gap (not an error). | STAC items list is authoritative; URL construction broke Plan 067 twice. Gap handling is explicit. |
| D5 | **CRS reprojection in the adapter (not the extractor); streaming inner-loop, NOT full-window preload.** Adapter reads NetCDF via xarray, reprojects LV95 → WGS84 via the `rioxarray` `.rio.reproject` accessor (`ds.rio.write_crs("EPSG:2056").rio.reproject("EPSG:4326")` — `rioxarray` is already a runtime dep at `pyproject.toml:34` and imported by the existing extractor at `exact_extract_grid_extractor.py:11` to register the `.rio` accessor). **Memory lifecycle**: the catchup/ingest flow iterates days × products sequentially; inside each (day, product) iteration, the flow reprojects the grid **once** and then runs `task.map` station-fan-out for extraction against the single in-scope `xr.Dataset`. The Dataset is released before the next (day, product) iteration begins. Peak memory is ~1–2 reprojected Datasets at a time (~10–50 MB), NOT all 240 (day, product) pairs held concurrently. This matters: at Swiss ~1 km grid (~500 × 220 cells) × float64 + coords, each reprojected Dataset is ~10 MB in memory; pre-loading all 60 days × 4 products would be ~2–12 GB and would OOM default Prefect workers. Extractor call site: positional signature (`extract(grid, configs, basins, cycle_time, nwp_source)`), grid dim is `valid_time` (not `time`), handles missing `member` dim gracefully — no synthetic member wrap needed. | Keeps extractor CRS-agnostic. Per-station reprojection would burn compute on a per-day-product-grid that is station-independent. Per-(day, product) inner-loop cache is the natural grain — reuse across the station fan-out WITHIN one iteration; release between iterations. Streaming keeps worker memory bounded regardless of catchup-window length. |
| D6 | **`version` = content hash of asset bytes.** SHA-256 of the downloaded NetCDF bytes, truncated to 16 hex chars. Identical MeteoSwiss republications produce the same `version` → upsert is a no-op. Content corrections (different bytes) produce a new `version` → a new row lands alongside the old. A companion change (T2) adds a latest-version supersession filter to `fetch_forcing` so downstream reads are deterministic. `historical_forcing.version` is `sa.Text` (unbounded), so a 16-hex string fits easily. No STAC-timestamp fallback — SHA-256 over `bytes` cannot raise, and a failed download raises in the fetch layer before version computation. | A1 decision. Idempotent-on-identical-republish is a production correctness property — without it, downstream dataframes see non-deterministic values. Content hash is deterministic per source content; audit trail (older versions) preserved; latest-version filter ensures reads see one answer. |
| D7 | **Decoupled catchup flow; onboarding stays offline.** Onboarding flow is NOT modified. New standalone flow `catchup_weather_history_flow` runs post-onboarding (manual trigger in v0b; auto-wiring deferred to v0c). It catches up the rolling 60-day window per station via `task.map` (precedent: `flows/train_models.py:435`, `flows/onboard_model.py:735`). | Backfill failure does not block onboarding. Operator retries independently. Matches Plan 058 precedent. |
| D8 | **Scheduled ingest = one daily deployment.** Runs at 06:00 UTC daily. Fetches STAC items for `[last_persisted_valid_time + 1 day, today]` per station, upserts via natural key. Cron expressed as a plain string (`"0 6 * * *"`) on `DeploymentSpec.cron: str \| None` per current abstraction. No weekly, no monthly, no supersession (no daily RhresD to supersede). | Matches actual `DeploymentSpec` shape at `cli/register_deployments.py:24–30`. Simplest thing that works. |
| D9 | **CC-BY attribution via a module-level dict** `SOURCE_ATTRIBUTIONS: dict[ForcingSource, str]` at the top of `types/forcing_sources.py`. Keys: every `ForcingSource` member. Values: `"MeteoSwiss (CC-BY)"` for all four MeteoSwiss tags, `"CAMELS-CH (CC-BY 4.0)"` for CAMELS-CH, placeholder for `NWP_ARCHIVE`. Callers (future API code, future dashboard) look up by source tag. No dataclass field, no schema column. | D3 decision. License is a property of the source, not the row; the row's `source` tag IS the attribution key. Zero blast radius vs the dataclass-field option (10+ files). |
| D10 | **Canonical run-name templates** per `docs/standards/orchestration.md` §Run naming — **hyphen-only kebab-case** to match all ~30 existing templates (not the slash-delimited form drafted in rev 2):<br>- `catchup_weather_history_flow`: `catchup-weather-history-{station_id}-{start_date}-{end_date}`<br>- `catchup_weather_history_station_task`: `catchup-weather-history-{station_id}`<br>- `ingest_weather_history_daily_flow`: `ingest-weather-history-daily-{run_date}`<br>- `ingest_weather_history_day_task`: `ingest-weather-history-day-{run_date}`<br>Covered by `tests/unit/flows/test_run_names.py`. `cache_policy=NO_CACHE` on all `@task`s. Two new rows in `docs/standards/orchestration.md` §Flow-to-Prefect mapping. | Aligns with house convention (verified against 30+ existing templates). |
| D11 | **C2 guard: `converters.basin_avg_to_records` raises on reanalysis source tags.** Adds ~5 lines: if `basin_avg.nwp_source` resolves to a `ForcingSource` value matching any MeteoSwiss-reanalysis or CAMELS-CH tag (via a check against a small constant tuple), raise `ExtractionError` with an explanatory message. No call site today passes reanalysis tags — the guard is defensive against future developers copying the forecast-path pattern incorrectly. | Contains the `BasinAverageForecast.cycle_time` semantic-overloading risk at the one path that would cause on-disk corruption (writing a reanalysis valid-time into `weather_forecasts.cycle_time NOT NULL`). Lighter than a full `BasinAverageReanalysis` wrapper type (which would ripple 6+ files) while blocking the same failure mode. |
| D12 | **Error-class reuse.** Product unavailable → `AdapterError`. CRS mismatch / reprojection failure → `ExtractionError`. C2 guard → `ExtractionError`. Partial multi-day fetch failure → log `warning`, continue; raise `AdapterError` only when zero days succeed. | Aligns with existing hierarchy; no invented classes. |
| D13 | **Clock injection** for all date math. Functions accept `clock: Callable[[], UtcDatetime] = utc_now` (imported from `types/datetime.py`). Never `datetime.utcnow()`/`datetime.now()`. | CLAUDE.md testability + UtcDatetime newtype. |
| D14 | **DST / timezone handling.** Adapter reads `item["properties"]["datetime"]` from STAC (already UTC-labelled by MeteoSwiss) and stores as `UtcDatetime` via `ensure_utc()` at the adapter boundary. All downstream operations are UTC. Test matrix (T4) includes: (a) DST spring-forward day, (b) DST fall-back day, (c) leap-day (2024-02-29), (d) year-boundary. If an item's native daily aggregation is Europe/Zurich-local-day, the STAC `datetime` field is already the canonical UTC instant per MeteoSwiss metadata — the adapter does not reinterpret. | E1 decision. Pins the timezone semantic now; avoids a class of silent off-by-1h bugs at DST transitions. Four explicit tests in T4 make the coverage visible. |

---

## Task list

### Phase 1 — Registry + attribution + store changes

#### T1 — `ForcingSource` enum + `SOURCE_ATTRIBUTIONS`

**Scope (in)**: introduce `src/sapphire_flow/types/forcing_sources.py` with the six-member `ForcingSource(Enum)` per D2; module-level `SOURCE_ATTRIBUTIONS: dict[ForcingSource, str]` per D9; docstrings differentiating `ForcingSource` (data provenance) from `ForcingType` (WMO skill-interpretation).
**Scope (out)**: grep-and-replace of existing string literals; schema changes; changes to `ForcingType` or any dataclass.
**Verification**:
- `uv run ruff check src/sapphire_flow/types/forcing_sources.py`
- `uv run ruff format --check src/sapphire_flow/types/forcing_sources.py`
- `uv run pyright src/sapphire_flow/types/forcing_sources.py`
- `uv run pytest tests/unit/types/test_forcing_sources.py` — asserts six members, string values including `CAMELS_CH.value == "camels-ch"`, `SOURCE_ATTRIBUTIONS` covers every enum member.

**Exit**: Enum + attributions dict in place; no other file modified.

#### T2 — `fetch_forcing` latest-version supersession filter (A1)

**Scope (in)**: update the `HistoricalForcingStore.fetch_forcing` concrete implementation at `src/sapphire_flow/store/historical_forcing_store.py:55–80`. **Behaviour split on explicit `version` kwarg**: when the caller does NOT pass `version=` (the typical case), the query returns only rows with `MAX(created_at)` per `(station_id, source, valid_time, parameter, spatial_type, COALESCE(band_id, -1), COALESCE(member_id, -1))` via `ROW_NUMBER() OVER (... ORDER BY created_at DESC) = 1`. When the caller DOES pass `version="X"` (audit-trail queries — existing behaviour), the supersession filter is **skipped**: the caller gets the exact-version rows (all of them, even if non-latest). Order by `created_at` (monotonic server-default `NOW()` per `alembic/versions/0004_add_historical_forcing.py:51`), not by the `version` string (which is now a content hash and has no natural ordering). Tie-break on `id` UUID for determinism when two rows share a `created_at` microsecond. Protocol docstring updated: "returns the latest version per logical key when `version` is not specified; returns exact-version rows when `version` is passed." Regression tests: (a) single-version data — unchanged (b) two-version data without `version=` kwarg — returns latest only (c) two-version data with `version="oldest"` — returns the older row.
**Scope (out)**: changes to the Protocol signature; changes to the schema; changes to write-side behaviour.
**Verification**:
- `uv run pyright src/sapphire_flow/store/historical_forcing_store.py`
- `uv run pytest tests/unit/store/test_historical_forcing_store.py::TestFetchForcingSupersession` (new test class covering: single-version no-op, two-version latest-wins, three-version latest-wins, no rows empty).
- `uv run pytest tests/integration/store/test_historical_forcing_store.py` (existing regression — still green).

**Exit**: Supersession filter in place; no behaviour change for existing single-version data; multi-version data returns latest only.

#### T3 — C2 guard in `converters.basin_avg_to_records`

**Scope (in)**: in `src/sapphire_flow/preprocessing/converters.py:46–72`, at the entry of `basin_avg_to_records`, check `basin_avg.nwp_source` against a module-level `_REANALYSIS_SOURCE_TAGS` constant defined as a **self-contained literal tuple of string values** in `converters.py` — NOT imported from `types/forcing_sources.py`. This preserves the Phase-1 parallelism declared in the dependency graph (T1 and T3 land independently). Values: `("meteoswiss_rprelimd", "meteoswiss_tabsd", "meteoswiss_tmind", "meteoswiss_tmaxd", "camels-ch")`. Raise `ExtractionError("cannot route reanalysis basin-average to weather_forecasts; source=<tag>")` on match. **Drift-mitigation test** (T1 test file): assert `_REANALYSIS_SOURCE_TAGS == tuple(s.value for s in (ForcingSource.METEOSWISS_RPRELIMD, ForcingSource.METEOSWISS_TABSD, ForcingSource.METEOSWISS_TMIND, ForcingSource.METEOSWISS_TMAXD, ForcingSource.CAMELS_CH))` — catches enum value drift. **Symmetric guard** added to `point_forecast_to_records` at `converters.py:17` with the same constant and the same error message, defensive against the symmetric future-misuse path (`PointForecast.nwp_source` also flowing into `weather_forecasts`).
**Scope (out)**: refactoring `converters.py`; touching `BasinAverageForecast` type; touching the extractor.
**Verification**:
- `uv run pyright src/sapphire_flow/preprocessing/converters.py`
- `uv run pytest tests/unit/preprocessing/test_converters.py::TestBasinAvgToRecordsGuard` (new class: guard raises on each reanalysis tag; guard silent on forecast tags).

**Exit**: Guard in place; existing forecast-path callers untouched; new test passes.

### Phase 2 — Adapter

#### T4 — `MeteoSwissOpenDataReanalysisAdapter` — STAC + CRS + assembly

**Scope (in)**: the adapter class implementing `WeatherReanalysisSource`; STAC iteration (D4) including **pagination**: follow `links[].rel == "next"` when returned; **duplicate-item ordering**: within a single `datetime` range, rank items by `properties.updated DESC` + `properties.expires DESC` and iterate in that order (content-hash dedup in D6 makes exact duplicates no-ops; this rule picks the most-recently-published bytes when a day has revised content); NetCDF download with `httpx.Client` reused from `meteoswiss_nwp.py` pattern **with explicit timeouts** (`httpx.Timeout(connect=10.0, read=300.0, write=30.0, pool=10.0)`) and **bounded retry** (1 retry on HTTP 5xx; no retry on 4xx, which surfaces as `AdapterError`); LV95 → WGS84 reprojection via `rioxarray` `.rio.reproject` accessor (D5); extractor call (positional signature per verified code); **product→parameter-name mapping** written explicitly into the adapter (`rprelimd → "precipitation"`, `tabsd → "temperature"`, `tmind → "temperature_min"`, `tmaxd → "temperature_max"`) — these are the parameter strings stored in `historical_forcing.parameter`, consumed by Plan 072 D3; `RawHistoricalForcing` assembly with content-hash `version` (D6), `source` tag, `attribution` looked up from `SOURCE_ATTRIBUTIONS`; variable allowlist kwarg with v0b default `{rprelimd, tabsd, tmind, tmaxd}`; clock injection (D13); UTC handling via `ensure_utc` on STAC `properties.datetime` (D14) — if the STAC field is a naïve datetime (missing timezone info), `ensure_utc` raises `AdapterError` rather than silently assuming UTC; partial-failure handling (D12); reuse of existing exceptions.
**Scope (out)**: flow orchestration (T7, T8); recording tool (T5); documentation (T9); the per-(day, product) memory-lifecycle cache — that belongs in T6 (the flow) per D5, not in the adapter.
**Verification**:
- `uv run ruff check src/sapphire_flow/adapters/meteoswiss_open_data_reanalysis.py`
- `uv run pyright src/sapphire_flow/adapters/meteoswiss_open_data_reanalysis.py`
- `uv run pytest tests/unit/adapters/test_meteoswiss_open_data_reanalysis.py`

**Exit**: Adapter against replay fixture (T5) returns non-empty `list[RawHistoricalForcing]` for a 3-station × 4-product × 7-day window; rows have correct source tags, deterministic content-hash versions (same bytes → same version across runs), correct parameter names (`"precipitation"`, `"temperature"`, `"temperature_min"`, `"temperature_max"`), CRS reprojection verified on a basin straddling projection boundaries (Graubünden or Ticino), pagination test passes on a simulated >100-item day, duplicate-item ordering picks the most-recently-updated item.

#### T5 — Recording tool + unit tests

**Scope (in)**: recording tool at `src/sapphire_flow/tools/record_meteoswiss_open_data.py` following the `src/sapphire_flow/tools/record_fixtures.py` precedent; fixed-window fixture under `tests/fixtures/reference/meteoswiss_open_data/` (2026-04-10 → 2026-04-16 by default); replay harness monkey-patching the `httpx.Client`; unit tests for STAC iteration (D4), CRS reprojection (D5), version determinism (D6, including re-run idempotence), partial-failure (D12), attribution (D9 — lookup works for every source tag), DST matrix (D14 — 4 test cases). Tool README documents CC-BY acknowledgement requirement.
**Scope (out)**: integration tests (T9).
**Verification**:
- `uv run pytest tests/unit/adapters/test_meteoswiss_open_data_reanalysis.py` (all classes incl. DST matrix)
- `uv run pytest tests/unit/tools/test_record_meteoswiss_open_data.py`

**Exit**: Unit tests green. Fixture size ≤ 5 MB total. Recording tool module docstring includes the regeneration runbook.

### Phase 3 — Flows + scheduling

#### T6 — Decoupled catchup flow

**Scope (in)**: `src/sapphire_flow/flows/catchup_weather_history.py` implementing `catchup_weather_history_flow(station_id, forcing_store, adapter, clock)` — one-shot rolling-60-day catchup per station. **Flow structure (per D5 lifecycle)**: outer loop iterates (day × product) sequentially at the flow level; within each iteration, the adapter fetches + reprojects the grid once at the flow level, then `task.map` fans out per-station extraction against the in-scope reprojected `xr.Dataset`; the Dataset goes out of scope at end-of-iteration before the next (day, product) begins. This ensures peak memory stays bounded regardless of catchup window length. `task.map` precedent: `flows/train_models.py:435`, `flows/onboard_model.py:735`. Idempotent upsert via natural key (and idempotent-under-republication via D6 content-hash version); run-name template per D10.
**Scope (out)**: onboarding flow integration (D7: decoupled); scheduled daily ingest (T7).
**Verification**:
- `uv run pytest tests/unit/flows/test_catchup_weather_history.py`
- `uv run pytest tests/unit/flows/test_run_names.py -k catchup_weather_history`

**Exit**: Flow runs end-to-end against replay fixture; rows land in `historical_forcing`; run name matches `catchup-weather-history-<station_id>-<start>-<end>`; second run on same window is idempotent (rowcount unchanged).

#### T7 — Scheduled daily ingest flow

**Scope (in)**: `src/sapphire_flow/flows/ingest_weather_history_daily.py` implementing `ingest_weather_history_daily_flow(forcing_store, adapter, station_store, clock)` per D8 (one deployment, 06:00 UTC, plain cron string); `cache_policy=NO_CACHE`. Handles gap-days (MeteoSwiss not yet published today's file) by returning empty without error. Emits a structured `forcing.rolling_window_near_exhaustion` event at `warning` level if `last_persisted_valid_time < clock() - 50 days` and at `error` level if `< clock() - 55 days` (tighter alarm gives on-call a clear page before the rolling-window cliff at 60 days). **No-prior-data null case**: when `last_persisted_valid_time` is `None` (fresh deployment, first run post-deploy, empty local DB), the threshold check is **skipped entirely** — a fresh deployment must run the post-onboarding catchup flow (T6) to seed initial rows; firing the exhaustion alarm on empty state would be a spurious page. Test covers `None` explicitly: assert no alarm event emitted when store returns no prior rows.
**Scope (out)**: catchup flow (T6); weekly supersession (not applicable).
**Verification**:
- `uv run pytest tests/unit/flows/test_ingest_weather_history_daily.py` — covers happy-day, gap-day, and the rolling-window-near-exhaustion path.
- `uv run pytest tests/unit/flows/test_run_names.py -k ingest_weather_history_daily`

**Exit**: Flow runs end-to-end; single-day append idempotent; warning event fires when expected.

#### T8 — Deployment registration

**Scope (in)**: add two entries to `src/sapphire_flow/cli/register_deployments.py` `_build_specs()`: `catchup-weather-history` (no cron — manual/triggered) and `ingest-weather-history-daily` (`cron="0 6 * * *"`). Update `docs/standards/orchestration.md` §Flow-to-Prefect mapping with two new rows.
**Scope (out)**: changes to existing deployments; CLI subcommand or `--dry-run` flag (the module entry is `python -m sapphire_flow.cli.register_deployments` — invokes `main()` directly with no args).
**Verification**:
- `uv run pytest tests/unit/cli/test_register_deployments.py`
- `grep -c "catchup-weather-history\|ingest-weather-history-daily" src/sapphire_flow/cli/register_deployments.py` ≥ 2

**Exit**: `uv run python -m sapphire_flow.cli.register_deployments` lists both new deployments without error in the output.

### Phase 4 — Integration + docs

#### T9 — Integration test

**Scope (in)**: `tests/integration/test_meteoswiss_open_data_reanalysis_flow.py` running the full catchup flow for a real Swiss basin geometry against the replay fixture; asserts `historical_forcing` rows, source tags, attribution lookup via `SOURCE_ATTRIBUTIONS[...]`, deterministic versions, D14 DST cases end-to-end. Covers a Flow 4 monitoring hook — flow emits `forcing.catchup_completed` with expected fields; and H5 monitoring — emits `forcing.rolling_window_near_exhaustion` on simulated long-gap scenario.
**Scope (out)**: live MeteoSwiss hits in CI.
**Verification**:
- `uv run pytest tests/integration/test_meteoswiss_open_data_reanalysis_flow.py`

**Exit**: Integration test green in CI against replay harness; ≥ 95% of expected rows present.

#### T10 — Docs + memory

**Scope (in)**: update `docs/architecture-context.md` §ML-lookback (add MeteoSwiss open-data description, CC-BY note, `ForcingSource` vs `ForcingType` distinction); `docs/v0-scope.md` §A12 (document the 60-day rolling window + forward-accumulation posture); `docs/standards/logging.md` (register `forcing.catchup_completed`, `forcing.day_ingested`, `forcing.rolling_window_near_exhaustion`); `docs/standards/orchestration.md` mapping-table + naming-list; add "Data licenses" subsection to architecture-context.md citing `SOURCE_ATTRIBUTIONS` as the authoritative source of attribution strings; add a handoff note to `docs/plans/042-api-auth-client-sdk.md` (or a new v2 dashboard plan when it exists) recording that **API responses do not currently carry `attribution` fields** — a real CC-BY exposure gap that must be closed before any non-internal API consumer ships (boundary candidate: `src/sapphire_flow/api/routes/stations.py`); memory `project_weather_history_source.md` (new).
**Scope (out)**: dashboard UI doc (v2 scope); actual API response-envelope change (v2 scope).
**Verification**:
- `grep -c "MeteoSwissOpenDataReanalysisAdapter\|meteoswiss_rprelimd\|meteoswiss_tabsd\|SOURCE_ATTRIBUTIONS" docs/` ≥ 6
- `uv run ruff check` still clean

**Exit**: All doc files updated; memory file indexed in `MEMORY.md`; commit per conventional-commit + version-bump per CLAUDE.md.

---

## Priority order

| Rank | Task | Effect | Effort | Why |
|------|------|--------|--------|-----|
| 1 | **T1 + T2 + T3** (registry + supersession + guard) | High (enabler for rest; A1 is a production-correctness fix) | Low-medium (~1 day combined) | T1 ~40 LOC; T2 is a one-query change in the store + tests; T3 is 5 lines + tests. Together they remove correctness risks before any adapter ships. |
| 2 | **T4 + T5** (adapter + recording + unit tests) | High (core deliverable) | Medium (~3 days) | The actual adapter. Serialize within a single branch. |
| 3 | **T6 + T7 + T8** (flows + deployment) | High (operationalizes adapter) | Medium (~1-2 days) | Serial — T8 registers both flows from T6 and T7. |
| 4 | **T9 + T10** (integration + docs) | Medium | Low-medium | Parallel closeout. |

---

## Dependency graph

```json
{
  "phases": [
    {
      "id": "phase-1-foundations",
      "tasks": ["T1", "T2", "T3"],
      "parallel": true,
      "depends_on": []
    },
    {
      "id": "phase-2-adapter",
      "tasks": ["T4", "T5"],
      "parallel": false,
      "depends_on": ["phase-1-foundations"]
    },
    {
      "id": "phase-3-flows",
      "tasks": ["T6", "T7", "T8"],
      "parallel": false,
      "depends_on": ["phase-2-adapter"]
    },
    {
      "id": "phase-4-closeout",
      "tasks": ["T9", "T10"],
      "parallel": true,
      "depends_on": ["phase-3-flows"]
    }
  ]
}
```

Phase-1 parallelizes (T1, T2, T3 touch disjoint files). Phase-2 serializes (T5's fixture needs T4's adapter structure stable). Phase-3 serializes (T8 registers both T6 and T7 flows). Phase-4 parallelizes (T9 test, T10 docs — disjoint).

---

## Open questions for user review

1. **Catchup trigger mechanism** (D7): manual `uv run python -m sapphire_flow.cli run-flow catchup-weather-history --station <id>` in v0b; v0c may wire a post-onboarding Prefect event. Confirm "manual for v0b" is OK.
2. **Daily RhiresD watcher**: if MeteoSwiss later publishes daily RhiresD to open-data, it should slot in as a higher-priority source tag. Recommendation: defer until MeteoSwiss announces — watchdog for unknown arrival date is low-value.
3. **`NWP_ARCHIVE` enum reservation**: if Plan 072's B1 decision (drop NWP archive from v0b) proves premature (e.g. operational live-tail gaps cause too many forecast-cycle failures), a future plan re-introduces the source with a train/test-matched design. Keeping `NWP_ARCHIVE` in the enum reserves the slot. Confirm reserving-but-not-using is acceptable.
4. **CAMELS-CH attribution**: `SOURCE_ATTRIBUTIONS[ForcingSource.CAMELS_CH]` needs to be set correctly for CC-BY 4.0 per the CAMELS-CH license. Confirm the attribution string (`"CAMELS-CH (CC-BY 4.0, Höge et al. 2023)"` or equivalent) in T1.

---

## Changelog

- **2026-04-22 (rev 1)** — Initial DRAFT. Partner-credentialed daily
  RhresD/RprelimD/TabsD; 5-year backfill; weekly + daily flows;
  RhresD supersession of RprelimD.
- **2026-04-22 (rev 2)** — Rewritten after critical review and
  MeteoSwiss open-data STAC discovery. Anonymous CC-BY; rolling
  60-day window; no backfill (CAMELS-CH stays for training); four
  products (added TminD/TmaxD per user); CRS LV95; `ForcingSource`
  enum with hyphenated `camels-ch`; STAC `updated` as version;
  decoupled catchup; attribution TBD in T1.
- **2026-04-22 (rev 3, this document)** — Round-2 fixes:
  (A1) content-hash `version` + latest-version supersession filter
  in `fetch_forcing` to close the non-deterministic-duplicate-row
  bug. (B1) Dropped `NWP_ARCHIVE` from v0b scope per user
  direction; enum value reserved for v0c. (C2) 5-line guard in
  `converters.basin_avg_to_records` to prevent future misuse of
  reanalysis basin-averages on the forecast-write path. (D3)
  Module-level `SOURCE_ATTRIBUTIONS` dict instead of dataclass/schema
  change. (E1) DST/timezone promoted to D14 with explicit test
  matrix. Factual fixes: run-name templates use hyphen-only
  kebab-case (matches ~30 existing templates, not slash-delimited);
  `DeploymentSpec.cron` is plain string, not `CronSchedule`; STAC
  field is `item["properties"]["updated"]` not `item["updated"]`;
  CLI entry is `python -m sapphire_flow.cli.register_deployments`
  with no subcommand; `rioxarray` already a runtime dep (no
  pyproject change); `task.map` precedents are `train_models.py` +
  `onboard_model.py` (not Plan 068 DRAFT).
