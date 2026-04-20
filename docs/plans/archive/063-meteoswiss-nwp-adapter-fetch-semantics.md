# Plan 063 — MeteoSwissNwpAdapter fetch semantics redesign

**Status**: DONE (archived 2026-04-20, core commit 992d3a6, tag v0.1.358; T4 live-STAC test d94ebab v0.1.359; T5 e2e script f856c2f v0.1.360)
**Date**: 2026-04-20
**Depends on**: Plan 045 DONE (ICON-CH2-EPS adapter landed), Plan 060 DONE (cache_policy + bootstrap).
**Blocks**: Plan 046 Stream A A3 step 8 (forecast-cycle direct-invoke) and all downstream D-stream validation.
**Scope**: Redesign `MeteoSwissNwpAdapter._fetch_grib_files()` so it fetches *exactly the GRIB files needed for a forecast cycle* — filtered by **cycle time** (issue-time) and a **variable allowlist** — instead of the current broken filter-by-valid-time + download-everything pattern. Includes a committed STAC-capabilities research artefact (T0) that locks down the chosen filter approach before any code is written. Addresses an A3 step-8 finding that surfaced during Plan 046 execution: a single forecast-cycle invocation downloaded 30+ unrelated GRIB files for ~44 minutes before filling the 8 GB `/tmp` tmpfs and aborting with "No space left on device."

---

## Context

### Why now

Plan 046 A3 step 8 ran `run_forecast_cycle_flow(adapter=MeteoSwissNwpAdapter(...), cycle_time='2026-04-19T15:00:00+00:00')` directly inside the worker container on 2026-04-19. After nine inline infrastructure fixes (commits `1f26d4b` → `707e31b`), the flow reached `_fetch_grib_files` and spent 2,652 seconds (44 min) downloading variables we don't need — *albedo*, *soil temperature*, *latent heat flux*, all 35+ ICON-CH2-EPS output fields — before the worker's 8 GB tmpfs filled up. Root cause: the STAC `datetime` filter matches `properties.datetime` which is the forecast **valid time** (e.g. "2026-04-19T15:00Z"), not the cycle (issue) time. A single valid-time query returns dozens of items per page for every meteorological field MeteoSwiss publishes, across pages, every ensemble member, every variable.

The adapter then downloads every asset with a `.grib2` path regardless of whether the pipeline uses it. Plan 045's `PARAM_GROUPS` list (`src/sapphire_flow/adapters/meteoswiss_nwp.py:30-37`) was scoped to six variables anticipating future model needs; **the v0 allowlist is narrowed to the two canonical hydrological drivers — `tp` (precipitation) and `t_2m` (temperature)** (see D2 + v0 scope note below). The filter operates on file extension, not variable name.

### Semantic errors of the current design

1. **Wrong temporal filter**. `datetime=cycle_time` → STAC returns items whose `properties.datetime == cycle_time`, i.e. forecasts *valid* at that instant from *any earlier cycle*. The adapter needs "all items *produced by* this cycle," which is a different STAC query.
2. **No variable filter**. The adapter relies on `.grib2` extension / `application/grib` media type, which lets every ICON field through. Of ~35 publicly-exposed variables, the pipeline uses 6.
3. **No step filter**. Even for the right variables, a forecast cycle produces 121 hourly steps. Some downstream models may only need a subset (e.g. the first 120 hours of `tp`), not every step.
4. **No cycle-discovery**. If the operator calls with a `cycle_time` that doesn't correspond to an actual published cycle (e.g. a time between 00Z/03Z/06Z release slots), STAC returns nothing from the intended cycle — caller has no way to find the latest available cycle.

### Inputs (verified during A3 step 8)

- MeteoSwiss STAC collection `ch.meteoschweiz.ogd-forecasting-icon-ch2` at `https://data.geo.admin.ch/api/stac/v1`.
- Item ID convention: `{MMDDYYYY}-{HHMM}-{STEP}-{VAR}-{TYPE}-{SUFFIX}` (e.g. `04182026-1800-21-tot_prec-ctrl-40sq9lzg`). The first two fields encode the cycle time; `STEP` is hours from cycle; `VAR` is the MeteoSwiss short-name; `TYPE` is `ctrl` or `perturb` (ensemble member type).
- Asset `type` is `application/grib` (not the STAC spec's `application/x-grib2`).
- Asset `href` is an S3 signed URL with `?AWSAccessKeyId=…&Signature=…&Expires=…` appended.
- STAC items for a single valid-time are spread across many pages (pagination). The current `page_count > 100` guard is a blunt safety net, not correctness.
- `PARAM_GROUPS` at `meteoswiss_nwp.py:30-37` must be reduced from 6 variables to the v0 minimum: `tp` (precipitation) + `t_2m` (temperature). The other four (`td_2m`, `u_10m`, `v_10m`, `sd`) are deferred until a downstream v0b/v1 model actually consumes them — no current v0 model does (`LinearRegressionDaily`, `ClimatologyFallback`, `PersistenceFallback` all have empty `past_dynamic_features` / `future_dynamic_features`). Adding variables later is a single PARAM_GROUPS row each.

### Problem statement

The adapter's fetch model is aligned with the wrong axis of the STAC dataset (valid-time instead of cycle-time) and has no client-side allowlist, causing disk exhaustion and correctness issues that block A3 step 8 and every downstream operational cycle.

---

## Architecture decisions

| # | Decision | Rationale |
|---|---|---|
| D1 | **T0 research pass**: before writing any code, probe MeteoSwiss STAC and commit findings to `docs/research/063-meteoswiss-stac-probe.md`. Subsequent tasks cite this document for every design choice. | Every prior A3 step-8 fix turned up a new STAC behaviour. Verify first; subsequent tasks must not re-probe from scratch. |
| D2 | **`PARAM_GROUPS` is a three-column tuple list**: `list[tuple[str, str, str]]` where each tuple is `(stac_token, cfgrib_short_name, type_of_level)`. Examples: `("tot_prec", "tp", "surface")`, `("h_snow", "sd", "surface")`. The STAC allowlist uses column 0 (substring match on `-{stac_token}-` in item IDs); `_parse_grib_files` unpacks columns 1–2 for cfgrib `filter_by_keys`. Both consumers updated atomically. | Single source of truth. Allowlist and parser can never drift apart. |
| D3 | **Preserve `_parse_grib_files`'s existing `xr.open_mfdataset` semantics** — Plan 063 does NOT touch the parse/merge path. The downstream `_deaccumulate_precipitation`, `_convert_units`, ensemble-axis construction all remain unchanged. | Contain scope. Plan 063 is "fetch the right files," not "rework the GRIB parser." Plan 052 handles the extractor path separately. |
| D4 | **Add a cycle-time resolution helper**: `MeteoSwissNwpAdapter.resolve_cycle_time(requested: UtcDatetime) -> UtcDatetime`. Snaps to the nearest past 3-hour boundary (00/03/06/09/12/15/18/21 UTC); probes for availability using the filter approach from T0 (NOT the broken valid-time `datetime=` query); falls back to the prior cycle if empty; raises `NoCycleAvailableError(AdapterError)` after 3 consecutive failed fallbacks. Emits `nwp.cycle_fallback_used` at WARNING level on any fallback. Tz-naive input raises immediately. In v0, `max_fallback_steps` is fixed at 3 (hard-coded, not configurable). If a future operational requirement demands configurability, a follow-up plan will expose it on the adapter constructor. | Removes the "no GRIB2 files found" failure mode when the operator passes `cycle_time=clock()`. |
| D5 | **Cap total download** via `max_download_bytes = 4 GB` (configurable). Exceeding raises `BudgetExceededError(AdapterError)` before any partial download. Also retain the `len > 500` file-count guard (raises `BudgetExceededError`). | Belt-and-braces guard; surfaces issues early instead of filling `/tmp`. |
| D6 | **Per-call unique scratch subdirectory**: `scratch_path / cycle_iso` (e.g. `/tmp/meteoswiss_nwp/2026-04-20T00:00:00Z`). Required for `task.map` safety (Plan 045 already introduced fan-out). Cleanup on adapter entry via `rmtree` + `mkdir` when `cleanup_scratch_on_fetch=True`. | Concurrent callers never share a scratch directory. Stale files from prior failed runs are removed. |
| D7 | **No STAC-schema changes; work with what MeteoSwiss publishes.** If the research finds MeteoSwiss STAC lacks the needed server-side filter primitives, fall back to client-side ID-prefix filter and accept the network cost of one extra pagination query. | We don't control MeteoSwiss. Design must work within their API surface. |
| D8 | **`httpx.Client` timeout contract**: callers must construct `httpx.Client(timeout=httpx.Timeout(connect=10.0, read=300.0, write=None, pool=5.0))`. Documented in the adapter constructor docstring. `httpx.TimeoutException` is caught and re-raised as `AdapterError`. | Prevents silent hangs during large GRIB downloads. |
| D9 | **Interleave pagination with download**: for each page of the STAC search, filter items by allowlist (column 0 of `PARAM_GROUPS`), download filtered items' assets immediately before moving to the next page. Prevents pre-signed URL expiry for cycles large enough that full pagination takes several minutes. | Pre-signed S3 URLs have a finite TTL; collect-all-pages first risks expiry on page 1 assets before page N downloads complete. |
| D10 | **GRIB2 magic-byte check**: after each download completes, read the first 4 bytes and assert `== b"GRIB"`; raise `AdapterError("truncated or non-GRIB2 download")` otherwise. | Surfaces truncation at the boundary instead of deep inside cfgrib with an opaque error. |
| D11 | **Tests**: one live-STAC integration test marked `@pytest.mark.live_stac` (default-excluded) that hits the real API through `adapter.fetch_forecasts(...)` and asserts on the returned `GriddedForecast`. Unit tests use `httpx.MockTransport` + recorded STAC fixtures. No unit test calls private methods. | Matches Plan 055's `deployment_destructive` pattern. Live test gated behind an explicit opt-in. CLAUDE.md testing conventions prohibit asserting on private attributes or calling private methods directly. |

---

## Phase ladder

### T0 — Research: probe MeteoSwiss STAC and commit findings

**Scope**: Run a series of HTTP probes against `https://data.geo.admin.ch/api/stac/v1` and commit the results as `docs/research/063-meteoswiss-stac-probe.md`. No code changes in this task.

The research document must cover all of the following, empirically verified against real STAC responses:

**(a) Filter/query/ids extension probe** — Does the collection `ch.meteoschweiz.ogd-forecasting-icon-ch2` advertise conformance to the STAC `filter` extension (CQL2)? Is there a `forecast:reference_datetime` or equivalent property on items? Does `?query={"forecast:reference_datetime":{"eq":"..."}}` succeed? Does `?ids=prefix*` work?

**(b) Pagination shape** — What is the page structure under `datetime=` interval queries? Under bbox-restricted queries? Include observed `links[rel=next]` shape and `numberMatched`/`numberReturned` fields if present.

**(c) Variable mapping table (v0 minimum + deferred)** — Verified empirically against real STAC item IDs:

| MeteoSwiss STAC token | cfgrib shortName | type_of_level |
|---|---|---|
| `tot_prec` | `tp` | `surface` |
| `t_2m` | `t_2m` | `heightAboveGround` |

(v0 minimum. `h_snow`/`sd`, `td_2m`, `u_10m`, `v_10m` are deferred — trivial to add as extra rows when a downstream model requires them.)

Confirm or correct each row. Record the actual item ID substring used for each variable.

**(d) Rate-limit / 429 behaviour** — Document any observed `Retry-After`, `X-RateLimit-*`, or `429` responses including headers.

**(e) `asset.size` availability** — Is `asset.size` (or `asset["size"]`) populated on item assets? If so, what units?

**(f) Cycle cadence** — Confirm the cycle release cadence (expected: 3-hourly 00/03/06/09/12/15/18/21 UTC). Record how many items/assets a single complete cycle produces for the v0 2-variable allowlist.

**(g) Publication lag** — When does a given cycle become available relative to its nominal issue time? Measure by probing for the most recent completed cycle at two different wall-clock times. Express as approximate lag in minutes.

**(h) `datetime=start/end` interval query semantics** — Issue a `datetime=T/T+1h` interval query and inspect returned items. Does the filter select by valid-time (`properties.datetime`) or by item issue/reference time? Include one exemplar URL and response excerpt.

**(i) Pre-signed asset URL TTL** — Fetch an asset href and inspect `Expires=` or `X-Amz-Expires=` in the URL or response headers. Measure TTL in seconds.

**Exit gate**: `docs/research/063-meteoswiss-stac-probe.md` is committed. T1 and T2 cite this document for every design decision that depends on STAC behaviour.

---

### T1 — Cycle-time resolution helper

**Scope**: Add `resolve_cycle_time` and `NoCycleAvailableError` to `src/sapphire_flow/adapters/meteoswiss_nwp.py`. No changes to `_fetch_grib_files` in this task.

**Depends on**: T0 (research doc committed; availability probe uses the filter approach documented there).

Implementation requirements:

**(a)** Snap `now_utc` to the most recent `_CYCLE_HOURS` floor (3-hourly 00..21 UTC).

**(b)** Probe for that cycle's availability using the filter approach selected in T0 — NOT the broken valid-time `datetime=` query.

**(c)** Treat `200 OK + empty features` as "not yet published" and fall back to the prior cycle (-3h); repeat for up to 3 fallback steps total.

**(d)** On any fallback, emit `nwp.cycle_fallback_used` at WARNING level per `docs/standards/logging.md`, with fields `snapped_cycle`, `resolved_cycle`, `fallback_steps`.

**(e)** After 3 consecutive failed fallbacks, raise `NoCycleAvailableError` (subclass of `AdapterError`).

**(f)** Tz-naive `now_utc` input raises `ValueError` immediately (do not silently coerce).

```python
_CYCLE_HOURS = (0, 3, 6, 9, 12, 15, 18, 21)

def resolve_cycle_time(self, now_utc: UtcDatetime) -> UtcDatetime:
    """Snap now_utc to the nearest past ICON-CH2-EPS cycle.

    Raises ValueError on tz-naive input.
    Raises NoCycleAvailableError if no cycle is available within 3 fallback steps.
    Emits nwp.cycle_fallback_used (WARNING) on each fallback.
    """
```

Unit tests (all via `httpx.MockTransport`, no real network):

- `test_resolve_cycle_time_snaps_to_nearest_past_cycle` — `2026-04-19T14:37:12+00:00` → `2026-04-19T12:00Z`.
- `test_resolve_cycle_time_falls_back_on_empty_features` — snapped cycle returns `200 OK + {"features": []}`, prior cycle returns `200 OK + {"features": [...]}` → returns prior cycle.
- `test_resolve_cycle_time_raises_after_three_fallbacks` — three consecutive `200 OK + empty features` → `NoCycleAvailableError`.
- `test_resolve_cycle_time_raises_on_tz_naive_input` — `datetime(2026, 4, 19, 12, 0, 0)` (no tzinfo) → `ValueError`.

**Exit gate**: all 4 new unit tests green.

---

### T2 — Rewrite `_fetch_grib_files`

**Scope**: Replace the broken implementation of `_fetch_grib_files` in `src/sapphire_flow/adapters/meteoswiss_nwp.py`. Also update `PARAM_GROUPS` to the three-column shape per D2.

**Depends on**: T0 (filter strategy), T1 (`resolve_cycle_time` available).

**`PARAM_GROUPS` shape** (D2):

```python
PARAM_GROUPS: list[tuple[str, str, str]] = [
    ("tot_prec", "tp", "surface"),
    ("t_2m", "t_2m", "heightAboveGround"),
]
# v0 minimum (2 variables). Additional variables are one row each:
#   ("h_snow", "sd", "surface"),
#   ("td_2m", "td_2m", "heightAboveGround"),
#   ("u_10m", "u_10m", "heightAboveGround"),
#   ("v_10m", "v_10m", "heightAboveGround"),
# Add them when downstream models consume them.
```

Column 0 is used for the STAC allowlist (substring match on `-{stac_token}-` in item IDs). `_parse_grib_files` must be updated to unpack columns 1–2 for cfgrib `filter_by_keys`.

**New `_fetch_grib_files` behaviour** (steps must execute in this order):

1. **Resolve cycle**: `cycle_time = self.resolve_cycle_time(cycle_time)`.
2. **Per-call scratch directory** (D6): construct `scratch_dir = self._scratch_path / cycle_time.isoformat()`. If `cleanup_scratch_on_fetch=True`, `rmtree(scratch_dir, ignore_errors=True)` then `mkdir(scratch_dir, parents=True, exist_ok=True)`.
3. **Build STAC query** per T0's chosen filter strategy. Worst-case D7 fallback: query by a date range spanning the cycle window, then client-side-filter by item-ID prefix `MMDDYYYY-HHMM-`.
4. **Interleaved pagination + download** (D9): for each page of the STAC search response, (a) filter the page's items by allowlist (column 0 of `PARAM_GROUPS`, substring `-{token}-`), (b) download each filtered item's assets immediately — do not collect all pages before downloading. This prevents pre-signed URL expiry for large cycles where full pagination takes several minutes.
5. **Pre-flight size check** (D5): before each individual download, accumulate `asset.size` if the STAC response provides it (T0 finding (e)); if the running total would exceed `max_download_bytes`, raise `BudgetExceededError` before the download starts.
6. **File-count guard** (D5): if `len(downloaded_files) > 500`, raise `BudgetExceededError`.
7. **GRIB2 magic-byte check** (D10): after each download completes, read the first 4 bytes; if `!= b"GRIB"`, raise `AdapterError("truncated or non-GRIB2 download: {path}")`.

The `httpx.Client` passed to the adapter must be constructed by the caller as:

```python
httpx.Client(timeout=httpx.Timeout(connect=10.0, read=300.0, write=None, pool=5.0))
```

This is a documented caller contract in the adapter constructor docstring.

---

### T3 — Unit tests

**Scope**: Extend `tests/unit/adapters/test_meteoswiss_nwp.py` with new tests for T1 and T2 behaviour. All via `httpx.MockTransport`. No real network calls.

New tests:

- `test_resolve_cycle_time_snaps_to_nearest_past_cycle`
- `test_resolve_cycle_time_falls_back_on_empty_features`
- `test_resolve_cycle_time_raises_after_three_fallbacks`
- `test_resolve_cycle_time_raises_on_tz_naive_input`
- `test_param_groups_three_column_shape` — assert every entry is a 3-tuple and that column-0 tokens form a set that exactly covers column-1 cfgrib short-names (round-trip integrity for the parser).
- `test_fetch_grib_files_skips_unallowed_variables` — mock STAC page with 6 allowlisted variables + 10 others; assert only the 6 get `_download_asset` calls.
- `test_fetch_grib_files_raises_on_budget_exceeded` — mock STAC with `asset.size` totals > `max_download_bytes`; assert `BudgetExceededError` before any download.
- `test_fetch_grib_files_creates_per_cycle_scratch_dir` — assert `scratch_path / cycle_iso` is created, not `scratch_path` directly.
- `test_fetch_grib_files_cleans_scratch_on_entry` — pre-populate `scratch_path / cycle_iso` with junk files; assert it is empty after a successful fetch.
- `test_download_asset_raises_on_truncated_grib` — mock download that returns non-GRIB bytes; assert `AdapterError` with "truncated or non-GRIB2" in message.
- `test_timeout_exception_surfaces_as_adapter_error` — mock `httpx.TimeoutException`; assert it is re-raised as `AdapterError`.

**Exit gate**: all 11 new unit tests (4 from T1 + 7 from T2/T3) green.

---

### T4 — Live-STAC integration test (gated)

**Scope**: New file `tests/integration/test_meteoswiss_nwp_live.py`. One test marked `@pytest.mark.live_stac` (default-excluded). Test calls through the public `adapter.fetch_forecasts(...)` surface and asserts on the returned `GriddedForecast` — it does NOT call `adapter._fetch_grib_files(cycle)` directly.

**Depends on**: T2.

```python
@pytest.mark.live_stac
def test_fetch_latest_cycle_returns_gridded_forecast(tmp_path):
    adapter = MeteoSwissNwpAdapter(
        stac_base_url="https://data.geo.admin.ch/api/stac/v1",
        stac_collection="ch.meteoschweiz.ogd-forecasting-icon-ch2",
        scratch_path=tmp_path,
        http_client=httpx.Client(
            timeout=httpx.Timeout(connect=10.0, read=300.0, write=None, pool=5.0)
        ),
    )
    now = datetime.now(UTC)
    cycle = adapter.resolve_cycle_time(now)
    # WeatherForecastSource.fetch_forecasts(station_configs, cycle_time) — see src/sapphire_flow/protocols/adapters.py
    from sapphire_flow.types.station import StationWeatherSource
    from sapphire_flow.types.ids import StationId
    station_configs = [StationWeatherSource(station_id=StationId("test-station-1"))]
    result = adapter.fetch_forecasts(station_configs=station_configs, cycle_time=cycle)
    assert isinstance(result, GriddedForecast)
    # Filenames on disk should only contain allowlisted stac_tokens
    downloaded = list(tmp_path.rglob("*.grib2"))
    allowlist = {tok for tok, _, _ in adapter.PARAM_GROUPS}
    for f in downloaded:
        assert any(f"-{tok}-" in f.name for tok in allowlist), f.name
```

Add marker config to `pyproject.toml`:

```toml
[tool.pytest.ini_options]
markers = [
    ...,
    "live_stac: requires live MeteoSwiss STAC API; excluded by default",
]
```

**Exit gate**: `pytest -m live_stac tests/integration/test_meteoswiss_nwp_live.py` passes when run with network access; default CI run skips it.

---

### T5 — End-to-end validation on the compose stack

**Scope**: Validate the complete fix on the dev Docker Compose stack. Exit criteria must be based on fetch-path assertions, not downstream proxy metrics.

**Depends on**: T2, T3.

Steps:

1. `docker compose build prefect-worker`
2. `docker compose up -d --force-recreate prefect-worker`
3. Run the validation script:

```bash
DB_PASSWORD=$(cat ./secrets/db_password)
docker compose exec -T \
  -e DATABASE_URL="postgresql+psycopg://sapphire:${DB_PASSWORD}@postgres:5432/sapphire" \
  -e SAPPHIRE_CONFIG=/app/config.toml \
  -e SAPPHIRE_DATA_DIR=/data \
  -e PREFECT_API_URL=http://prefect-server:4200/api \
  prefect-worker uv run python3 << 'EOF'
import shutil, pathlib, time, httpx
from datetime import datetime, UTC
from sapphire_flow.adapters.meteoswiss_nwp import MeteoSwissNwpAdapter

SCRATCH = pathlib.Path("/tmp/meteoswiss_nwp_e2e")
SCRATCH.mkdir(parents=True, exist_ok=True)

adapter = MeteoSwissNwpAdapter(
    stac_base_url="https://data.geo.admin.ch/api/stac/v1",
    stac_collection="ch.meteoschweiz.ogd-forecasting-icon-ch2",
    scratch_path=SCRATCH,
    http_client=httpx.Client(
        timeout=httpx.Timeout(connect=10.0, read=300.0, write=None, pool=5.0)
    ),
)

t0 = time.monotonic()
now = datetime.now(UTC)
cycle = adapter.resolve_cycle_time(now)
print(f"resolved cycle: {cycle.isoformat()}")

free_before = shutil.disk_usage(SCRATCH).free
# fetch_forecasts drives _fetch_grib_files internally.
# Signature from WeatherForecastSource Protocol (src/sapphire_flow/protocols/adapters.py):
#   fetch_forecasts(station_configs: list[StationWeatherSource], cycle_time: UtcDatetime)
# Supply at least one StationWeatherSource so the adapter has a basin to extract.
from sapphire_flow.types.station import StationWeatherSource
from sapphire_flow.types.ids import StationId
station_configs = [
    StationWeatherSource(station_id=StationId("test-station-1")),
]
result = adapter.fetch_forecasts(station_configs=station_configs, cycle_time=cycle)
elapsed = time.monotonic() - t0
free_after = shutil.disk_usage(SCRATCH).free

downloaded = list((SCRATCH / cycle.strftime("%Y%m%dT%H%M")).rglob("*.grib2"))
allowlist = {tok for tok, _, _ in adapter.PARAM_GROUPS}

# (a) file count within budget: 2 vars × 2 types × 121 steps = 484 max
assert len(downloaded) <= 484, f"too many files: {len(downloaded)}"

# (b) every filename contains an allowlisted stac_token
bad = [f for f in downloaded if not any(f"-{tok}-" in f.name for tok in allowlist)]
assert not bad, f"non-allowlisted files: {bad[:5]}"

# (c) /tmp usage within cap (must not have consumed >4 GB)
consumed_gb = (free_before - free_after) / 1024**3
assert consumed_gb <= 4.0, f"scratch consumed {consumed_gb:.1f} GB"

# (d) resolved cycle_time in output paths — per-cycle dir uses %Y%m%dT%H%M
cycle_dir = SCRATCH / cycle.strftime("%Y%m%dT%H%M")
assert cycle_dir.exists(), f"expected per-cycle dir {cycle_dir}"

# (e) wall time
assert elapsed < 600, f"fetch took {elapsed:.0f}s, expected <600s"

print(f"PASS: {len(downloaded)} files, {consumed_gb:.2f} GB, {elapsed:.0f}s")
EOF
```

Note: `stations_succeeded` is NOT a valid success signal for this plan. The exit criteria above measure the fetch path directly.

**Exit gate**: script exits 0 with "PASS" output; elapsed < 600 seconds (10 minutes); peak scratch usage ≤ 4 GB.

---

### T6 — Commit + bump + tag + archive

- `uv run ruff format` + `uv run ruff check --fix` on all touched files.
- `uv run pytest tests/unit/adapters/test_meteoswiss_nwp.py -q` — green.
- `uv run pytest tests/ -q` — full suite green.
- `uv run bump-my-version bump patch`. `uv sync`.
- Stage all modified files (see Files to modify below), plus `docs/research/063-meteoswiss-stac-probe.md`.
- Commit: `feat(plan-063): MeteoSwissNwpAdapter cycle-time + variable filter`.
- Tag. Archive (`git mv ... archive/`, second bump, chore commit, tag).

---

## Structlog event inventory

Per `docs/standards/logging.md`, the following canonical events are emitted by this plan's code:

| Event name | Level | Required kwargs |
|---|---|---|
| `nwp.cycle_resolved` | INFO | `snapped_cycle: str`, `resolved_cycle: str` |
| `nwp.cycle_fallback_used` | WARNING | `snapped_cycle: str`, `resolved_cycle: str`, `fallback_steps: int` |
| `nwp.variable_skipped` | DEBUG | `item_id: str`, `reason: str` (emitted once per skipped item) |
| `nwp.size_cap_exceeded` | ERROR | `accumulated_bytes: int`, `max_download_bytes: int`, `item_id: str` (emitted before raising `BudgetExceededError`) |
| `nwp.download_truncated` | ERROR | `path: str`, `first_bytes_hex: str` (emitted before raising `AdapterError`) |

---

## Files to modify

| Path | Task | Change |
|---|---|---|
| `docs/research/063-meteoswiss-stac-probe.md` | T0 | New file — committed STAC probe results |
| `src/sapphire_flow/adapters/meteoswiss_nwp.py` | T1, T2 | Restructure `PARAM_GROUPS` to 3-column tuples; add `resolve_cycle_time`; rewrite `_fetch_grib_files` per T0 + D2 + D6 + D9 + D10; update `_parse_grib_files` to unpack columns 1–2 |
| `src/sapphire_flow/exceptions.py` | T2 | Add `NoCycleAvailableError(AdapterError)` and `BudgetExceededError(AdapterError)` |
| `tests/unit/adapters/test_meteoswiss_nwp.py` | T3 | 11 new tests (4 cycle-resolve + 7 fetch/download) |
| `tests/integration/test_meteoswiss_nwp_live.py` | T4 | New file — one live-STAC gated test via `fetch_forecasts` public surface |
| `pyproject.toml` | T4 | Add `live_stac` marker |

No flow / store / extractor changes.

---

## Exit gates

1. `docs/research/063-meteoswiss-stac-probe.md` committed (T0).
2. `resolve_cycle_time` unit tests green — 4 scenarios including empty-features fallback, 3-consecutive-fallback raise, tz-naive raise (T1/T3).
3. `_fetch_grib_files` unit tests green — 7 scenarios including allowlist filter, budget cap, per-cycle scratch dir, scratch cleanup, truncated-download detection, timeout surfaces as `AdapterError`, `PARAM_GROUPS` round-trip shape (T2/T3).
4. `@pytest.mark.live_stac` test passes when explicitly run (`pytest -m live_stac`) via `fetch_forecasts` public surface — skipped by default (T4).
5. T5 e2e: script exits 0, file count ≤ 484, every filename in allowlist, scratch ≤ 4 GB (reached ~1 GB in practice for 2 vars), wall time < 600s.
6. Full pytest green at pre-063 count + 11 new tests.
7. Commit landed, plan archived.

---

## Risks

| Risk | Mitigation |
|---|---|
| MeteoSwiss STAC doesn't expose any server-side filter for cycle-time (D7 fallback). | Plan falls back to client-side ID-prefix filter. Extra page queries but correctness is preserved. |
| Pre-signed URL expiry during download — our 44-min run showed hundreds of seconds between first and last asset. | Interleaved pagination + download (D9) ensures each page's assets are downloaded before the next page is fetched, keeping per-URL time within the TTL measured in T0 finding (i). |
| `resolve_cycle_time` snap-then-fallback could loop if STAC is temporarily unreachable. | Fixed limit of 3 fallback steps then `NoCycleAvailableError`. No unbounded retry loop. |
| Per-cycle scratch directories accumulate across many cycles. | `cleanup_scratch_on_fetch=True` (default) deletes and recreates the per-cycle dir on each call. Caller responsible for top-level `scratch_path` retention policy. |
| Variable allowlist (column 0 of `PARAM_GROUPS`) gets out of sync with cfgrib keys (column 1). | `test_param_groups_three_column_shape` enforces the round-trip invariant at test time. |
| T5 live run still consumes significant disk even post-fix (worst case ~3 GB before cap triggers). | D5's 4 GB cap tolerates this; per-cycle scratch cleanup on next run reclaims space. |
| Live-STAC test (T4) becomes flaky when MeteoSwiss has a release-cycle gap or API changes. | Test is `live_stac`-marked, opt-in only, not part of default CI. |
| STAC `datetime=` interval query semantics differ from what we need (T0 finding (h) may confirm valid-time filtering). | D7 fallback (client-side ID-prefix) covers this; T0 finding documents the behaviour so the correct filter is used from the start. |

---

## Deferred to follow-up plans

- **Streaming / chunked fetch with per-asset retry** — handles transient S3 failures during long fetches. Out of scope; D5 cap makes long fetches rare.
- **Asset caching** (avoid re-downloading a cycle already on disk) — performance optimisation, not correctness.
- **Alternative NWP adapters** (Plan 014+ / v1 Nepal's ECMWF IFS) — separate plan when v1 scope opens.
- **STAC `filter` CQL2 adoption** if MeteoSwiss adds it mid-plan — can migrate in a follow-up; D7 fallback keeps us unblocked.
- **Step allowlist** — if downstream models only need a subset of the 121 forecast steps, a step-filter on top of the variable allowlist could reduce download by ~95%. Requires model interface audit; deferred pending T0 finding (f) confirming full step count.

---

## Open questions

Not blocking DRAFT → READY:

1. **Does the pipeline actually need all 121 forecast steps?** Most v0 models (linear_regression_daily, climatology_fallback, persistence_fallback) are daily-scale and may only use the 0, 24, 48, 72, 96, 120-hour steps. A step-allowlist on top of the variable-allowlist could cut the download by 95 %. Worth investigating in T0 finding (f).
2. **What is the operational SLA for stale cycles?** If `resolve_cycle_time` exhausts 3 fallback steps, the resolved cycle could be up to 9 hours old. Is a cycle from e.g. 21Z-previous-day (resolved at 02:30 UTC before 00Z is published) acceptable, or should the flow raise above a configurable staleness threshold? This threshold, if added, belongs in D4 and requires an explicit decision before READY.
3. **Should `MeteoSwissNwpAdapter` be a factory (`from_config(deployment_config)`) rather than a bare constructor?** Would help the A3 step-8 direct-invoke flow avoid the adapter-as-parameter pattern. Likely a follow-up plan; Plan 063 keeps the current interface.
4. **v0 variable allowlist (resolved 2026-04-20)**: Scoped to `tp` + `t_2m` only — the minimum hydrological drivers. All three current v0 models (`LinearRegressionDaily`, `ClimatologyFallback`, `PersistenceFallback`) have empty `past_dynamic_features` / `future_dynamic_features` — they consume zero NWP variables today. The 2-variable allowlist validates the end-to-end pipeline at minimum cost (~1 GB/cycle instead of ~2.9 GB). When a v0b or v1 model requires additional variables, add them one PARAM_GROUPS row at a time. Note: MeteoSwiss does not publish `relhum_2m` (confirmed in T0 research doc §c); any future model wanting RH must consume `td_2m` (dewpoint) and derive RH downstream via Magnus formula.

---

## Dependency graph

```json
{
  "phases": [
    {
      "id": "phase-1",
      "tasks": ["T0"],
      "parallel": false,
      "note": "Research committed before any code is written"
    },
    {
      "id": "phase-2",
      "tasks": ["T1"],
      "parallel": false,
      "depends_on": ["phase-1"],
      "note": "resolve_cycle_time uses filter approach from T0 research doc"
    },
    {
      "id": "phase-3",
      "tasks": ["T2"],
      "parallel": false,
      "depends_on": ["phase-2"],
      "note": "_fetch_grib_files rewrite depends on resolve_cycle_time (T1) and T0 filter strategy"
    },
    {
      "id": "phase-4",
      "tasks": ["T3", "T4", "T5"],
      "parallel": true,
      "depends_on": ["phase-3"],
      "note": "Unit tests, live integration test, and e2e validation all depend on T2 being complete; T3/T4/T5 can proceed in parallel"
    },
    {
      "id": "phase-5",
      "tasks": ["T6"],
      "parallel": false,
      "depends_on": ["phase-4"]
    }
  ]
}
```
