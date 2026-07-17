# recap Data Gateway operations runbook

Nepal v1 operational procedures for the recap Data Gateway integration
(Plan 082). Scope: manual GeoPackage upload, historical back-extraction,
coverage-manifest recording, live smoke execution, `NWP_DELIVERY` watchdog
triage, API-key handling, and snow-variable status. Does **not** document the
upstream `recap-dg-client` fixes as complete — see § Snow-variable status for
what remains unconfirmed.

## Manual GeoPackage upload

The Gateway has no self-serve basin/HRU registration API (v1) — every basin
and band GeoPackage is registered by manual upload, coordinated with the
Gateway operator.

1. Build the GeoPackage per
   `docs/requirements/04-basin-static-artifact-contract.md` §3a/§4a: lowercase
   `g_<station_code_normalized>` feature names, no leading digit, unique
   across the file, valid `Polygon`/`MultiPolygon` geometry in EPSG:4326.
   `tests/fixtures/recap/compliant_test_basins.gpkg` is a small worked
   example (basin + 2 bands) proven against these rules by
   `tests/integration/live/test_recap_compliant_gpkg.py`.
2. Send the `.gpkg` to the Gateway operator for upload, along with the
   intended `gateway_hru_name`.
3. Record the returned HRU name and every per-polygon `name` in a JSON
   fixture (see `tests/fixtures/recap/compliant_test_basins.json` for the
   shape) — this is the source for both the §5a mapping-table rows (below)
   and any live-smoke test fixtures.
4. Populate the §5a `recap_gateway_polygon_bindings` table (schema owned by
   082; population owned by the Plan 120 basin/static importer) with
   `station_id, basin_id, gateway_hru_name, name, spatial_type, band_id` for
   every station this basin covers. Until this table has rows for a station,
   `StoreBackedGatewayPolygonResolver.resolve()` returns `None` for it and
   the station is silently skipped from Recap fetches (logged as
   `recap.station_unmapped`).

## Historical back-extraction

Historical back-extraction populates `historical_forcing` for model training
and MUST use the Gateway's `reanalysis` endpoints (`era5_land_reanalysis`,
`snow.reanalysis`) — **never** `ifs_gap_fill`/`snow.gap_fill`, which target
operational gap-filling against a known recent window, not a leakage-free
multi-year training series (see Plan 082 "Client caveats").

1. Confirm the §5a mapping table has bindings for every target station
   (above).
2. Run `ingest_weather_history_flow` with an explicit `window_days` covering
   the desired history (e.g. `window_days=3650` for 10 years) — the Swiss
   rolling-ingest default (`window_days` unset, 60 days) is too short for
   initial Nepal back-extraction.
3. The reanalysis adapter's leakage guard (`adapters/recap_gateway.py
   ._drop_forecast_fill_rows`) drops any `ifs`/`jsnow_forecast`-sourced rows
   the Gateway's `reanalysis` endpoint may still echo — only `era5_land` /
   `jsnow_reanalysis` rows are admitted. No operator action needed; this is
   automatic.
4. After a back-extraction run completes, record the actually-covered span
   in the coverage manifest (next section) — back-extraction does not update
   the manifest automatically; the manifest is a SUPERVISED record.

## Coverage manifest

The Gateway exposes no coverage metadata (Resolved Gateway Question 3) — SAP3
maintains a supervised manifest recording which historical span has actually
been back-extracted and verified, per `(gateway_hru_name, name, dataset,
variable, band_id)` key (`services/gateway_coverage.py`). `member_id` is
deliberately **not** part of the key — coverage is member-agnostic.

- Build manifest rows with `sapphire_flow.services.gateway_coverage
  .build_coverage_manifest()`; each row requires an explicit `start`/`end` —
  there is no constructor path that infers a span from row counts or
  non-empty data.
- `coverage_spans_window()` is the **training-readiness gate**: Flow 6
  refuses automatic training unless the manifest's declared span contains
  the requested training window for every required key. A required key
  absent from the manifest is treated as no-coverage (refuse).
- This is a signal, not an irreversible block: a head hydrologist retains
  the existing manual-promotion authority (`promote_artifact`,
  `ModelArtifactStatus.PENDING_APPROVAL`) to declare a model operational
  despite short auto-coverage.
- `assert_returned_span_covers_request()` is a separate, PER-CYCLE check:
  it HARD-BLOCKS (raises) when a *training* fetch returns less than the
  requested window, but *operational* forecast fetches only log a WARNING
  and continue (a short horizon is still usable).
- **Recording a manifest entry is a manual operator action** after each
  verified back-extraction run — there is no automatic writer.

## Live smoke execution (`live_recap`)

Live Gateway smoke tests (`tests/integration/live/test_recap_gateway_live.py`)
are gated behind the `live` + `live_recap` pytest markers and skip
automatically when `RECAP_API_KEY` is unset — never part of default CI.

```bash
# Collection check only (no network) — must yield zero by default:
uv run pytest tests/integration/live/test_recap_gateway_live.py --collect-only -m 'not live'

# Credentialed run:
RECAP_API_KEY=... uv run pytest tests/integration/live/test_recap_gateway_live.py -m 'live and live_recap' -v
```

Covers: `fc`/`pf member=1` shape, member-bound rejections (member 0 and 51
must be rejected), precip/temperature plausible range after unit conversion,
snow endpoint shape (`hs`/`rof`/`swe`), the compliant-GeoPackage fixture's
`g_<...>` column echo + one-column-per-band behavior, and the
`source`/`source_run` provenance columns.

## NWP_DELIVERY watchdog triage

`_fetch_nwp_task` (Flow 1) categorizes every Recap adapter error into a
distinct `NWP_DELIVERY` pipeline-health record with `detail.reason` before
falling through to the generic catch-all. Four categories, each with a
different operator response:

### config_error

**Status**: CRITICAL. **Outcome**: flow-fatal `None` (cycle aborts).
`RecapConfigurationError` — the Gateway rejected a request parameter
(HRU/variable). `detail.field` names the rejected field. Check the §5a
mapping table for a stale/incorrect `gateway_hru_name` or polygon `name`, or
confirm the variable catalog (`RECAP_VARIABLES`) still matches what the
Gateway accepts.

### all_unmappable

**Status**: CRITICAL. **Outcome**: flow-fatal `None` (cycle aborts).
`GatewayResolutionError` — every station in the batch was unmappable to a
Gateway polygon (the §5a table has zero `BASIN_AVERAGE` rows for any
in-scope station). Confirm the Plan 120 importer has actually populated the
table for this deployment.

### auth

**Status**: CRITICAL. **Outcome**: flow-fatal `None` (cycle aborts).
`RecapAuthError` (HTTP 401/403) — the API key is missing, expired, or
revoked. Rotate `./secrets/sapphire_dg_api_key` (or the `RECAP_API_KEY` env
var in local dev) with a valid key and confirm the Docker secret file exists
and is readable by the worker containers. (This category only arises on a
Nepal deployment started with the `docker-compose.recap.yml` overlay — see
§ API-key handling.)

### source_data_missing

**Status**: WARNING. **Outcome**: degrades to runoff-only for THIS cycle
(native/fallback models still forecast); NOT flow-fatal.
`RecapDataUnavailableError` (`code="source_data_missing"`) — the requested
IFS cycle is not yet published. Usually transient; no action needed unless it
repeats across many consecutive cycles, which may indicate an upstream IFS
publication delay or outage.

### Long-outage note

A long Gateway outage repeats the relevant hard-abort every cycle by design
(no circuit-breaker/kill-switch in v1 — deferred to Flow 4 pipeline
monitoring). Sustained CRITICAL records across many consecutive cycles are
the operator signal to escalate upstream rather than treat each record as a
one-off.

### Staleness (source-aware)

Independently of the four categories above, `_check_nwp_grid_staleness`
checks `weather_forecasts` for the ACTIVE forecast source
(`ifs_ecmwf` on a Recap deployment, `icon_ch2_eps` on MeteoSwiss) — never the
other source, so an IFS-only Nepal deploy never raises a permanent false
CRITICAL from the (permanently absent) MeteoSwiss grid.

## API-key handling (`RECAP_API_KEY`)

The recap API-key secret is **Nepal-only**, provided by the
`docker-compose.recap.yml` overlay — it is NOT in the base `docker-compose.yml`.

- **Nepal deployment**: start with
  `docker compose -f docker-compose.yml -f docker-compose.recap.yml up`. The
  overlay declares the top-level `secrets.sapphire_dg_api_key.file:
  ./secrets/sapphire_dg_api_key` and adds the secret to both `prefect-worker`
  and `prefect-worker-ingest` (Compose merges service `secrets` additively, so
  each worker ends up with both `db_password` and `sapphire_dg_api_key`).
  Create `./secrets/sapphire_dg_api_key` with the Gateway API key. The secret
  is mounted at `/run/secrets/sapphire_dg_api_key` and read via
  `config.recap_gateway.load_recap_api_key()`. Never logged, never returned in
  any object that might be logged.
- **Swiss hosts**: omit the overlay (plain `docker compose up` / only the base
  file). No `./secrets/sapphire_dg_api_key` file is needed at all — the base
  compose declares no such secret, and the Recap adapters are never
  constructed on a Swiss deployment (`type` selector stays `meteoswiss_nwp`).
- Local dev fallback: `RECAP_API_KEY` env var (same pattern as
  `DB_PASSWORD`/`db_password`).
- Rotation (Nepal / overlay in use): per Gateway operator schedule; rotate by
  replacing the `./secrets/sapphire_dg_api_key` file and restarting the worker
  containers — unchanged by moving the secret into the overlay.

## Snow-variable status

`hs` (snow depth), `rof` (snowmelt), `swe` (SWE) are confirmed variable
names (Resolved Gateway Question 4), but their Gateway source-unit magnitudes
are **UNCONFIRMED** — `RecapVariable.convert` is deliberately `None` for all
three (`adapters/recap_gateway.py`). Do not assume a unit conversion factor
without live-verifying against the Gateway response (§ Live smoke execution).
The deterministic snow-forecast fetch path (`fetch_snow_forecast`) and the
daily-snow → sub-daily 51-member IFS broadcast (model-input service) are
built and unit-tested (Plan 082 Task 2H-snow), but are **not** wired into the
main Flow-1 forecast-cycle storage path — a model consuming snow forecast
features currently needs a separate integration step to persist
`fetch_snow_forecast`'s output before the model-input service's broadcast can
see it operationally.
