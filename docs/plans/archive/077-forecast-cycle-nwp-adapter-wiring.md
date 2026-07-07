# Plan 077 — forecast-cycle optional NWP adapter wiring

**Status**: READY
**Phase**: 10c (staging infrastructure / v0b operational wiring)
**Parent**: Plan 046 (Mac Mini Staging Deployment); surfaced by the Mac Mini
staging bring-up forecast-cycle run, 2026-06-24
**Related**: Plan 024 (forecast-cycle flow), Plan 045 (gridded NWP path),
Plan 067 (NWP cycle-fallback policy)
**Follow-up**: Plan 078 (forecast provenance for NWP-less forecasts; parked
until a grill-me design session)
**Created**: 2026-06-24

---

## Problem

The `forecast-cycle` Prefect deployment fails on the Mac Mini with:

```text
ValueError: adapter must be provided (no default NWP adapter in v0)
  at run_forecast_cycle.py:349
```

Root cause — **production wiring assumes dependency injection that the
deployment cannot provide**:

- `register_deployments.py:46-52` registers `forecast-cycle` with no
  parameters, so the Prefect worker calls `run_forecast_cycle_flow()` with
  `adapter=None`.
- `run_forecast_cycle.py:348-349` hard-raises when `adapter is None` instead
  of constructing a production adapter or intentionally skipping the NWP phase.
- Prefect deployment parameters are JSON-serialized. A live
  `MeteoSwissNwpAdapter` containing an `httpx.Client` cannot be injected by the
  deployment.
- Prior successful live runs injected the adapter from a script; the current
  production construction pattern is `scripts/063_e2e_verify.py:59-69`.

The important design constraint is that v0 forecasts do **not** consume NWP
weather-forecast features. The currently operational model set
(`linear_regression_daily`, `persistence_fallback`, `climatology_fallback`)
declares `future_dynamic_features = frozenset()`. The primary model forecasts
autoregressively from past discharge, and
`services/operational_inputs.py:197-205` skips a station for missing NWP only
when a model actually declares future dynamic features. The NWP fetch in the
forecast cycle is therefore a side-channel today: it builds the weather forecast
store / Zarr archive for future NWP-consuming models, but it does not feed any
current v0 forecast. Commit `19a31cb` already made the NWP phase no-op tolerant.

Downstream, `nwp_cycle` is currently used only for the abort check at
`run_forecast_cycle.py:500-512`; per-station forecasting records
`resolved_cycle_time` as the NWP reference time. The flow should support a
permanent runoff-only mode instead of treating NWP as mandatory.

### Secondary issue — NWP scratch + archive permissions

The Mac Mini deployment documentation must explain the filesystem model for NWP
when NWP is enabled:

- `/data/nwp_grids` is a named Docker volume (`docker-compose.yml:100`),
  `chown app:app`'d by `docker/entrypoint.sh:27` on container start.
- `/tmp/sapphire_nwp` is a 4 GiB sticky tmpfs
  (`docker-compose.yml:100-106`), writable by the non-root `app` user and
  ephemeral per container.

This documentation is only relevant for NWP-enabled operation. Runoff-only mode
must not require either path to be writable.

---

## Decisions

1. **Production NWP self-wiring is gated by an explicit `enabled` key under
   `[adapters.weather_forecast]`.** Operators should be able to tell from
   config whether the deployment will contact MeteoSwiss and build grid
   archives. Add `enabled = true` to the checked-in base `config.toml` so the
   production/canonical default remains NWP-on and the NWP archive accumulates;
   there is no NWP backfill for missed archive windows. The Mac Mini's initial
   runoff-only mode is selected by a dedicated minimal overlay,
   `config/overlays/mac-mini.toml`, containing only:

   ```toml
   [adapters.weather_forecast]
   enabled = false
   ```

   The scalar overlay is sufficient: `_deep_merge` in
   `config/_overlay.py:44-57` recurses into nested tables and overrides scalar
   leaves, so the Mac Mini overlay does not repeat `stac_base_url`,
   `stac_collection`, or `scratch_path`. The T1 helper must read the merged TOML
   via `load_merged_toml` + `_resolve_overlay_paths`, exactly like
   `load_config`, so `SAPPHIRE_CONFIG_OVERLAY` is honored at runtime. An absent
   `[adapters.weather_forecast]` section, an absent `enabled` key, or
   `enabled = false` means NWP is disabled; only the native TOML bool
   `enabled = true` enables production NWP self-wiring. A non-bool value such
   as `enabled = "false"` is a configuration error. The gate must not be
   inferred from model requirements because that would make operational behavior
   change implicitly when model assignments change. An injected `adapter`
   remains an explicit test/developer request to run the NWP phase and bypasses
   this production self-wiring gate.

2. **`DeploymentConfig.nwp_grid_archive_base_path` is ignored when NWP is
   disabled.** The archive path may remain populated in `config.toml`, but the
   flow must not construct `ZarrNwpGridStore` or
   `ExactExtractGridExtractor` unless NWP is enabled by either an injected
   adapter or `enabled = true`. This prevents a disabled NWP deployment from
   importing the arm64-sensitive `exactextract` dependency or touching
   `/data/nwp_grids`. When NWP is enabled, the existing archive path behavior
   is preserved.

3. **The no-NWP branch is a clean success no-op.** When `adapter is None` and
   NWP is disabled, the flow logs an explicit event such as
   `forecast_cycle.nwp_disabled` with `mode="runoff_only"` and
   `cycle_time=resolved_cycle_time.isoformat()`, skips `_fetch_nwp_task`
   entirely, sets `nwp_cycle = resolved_cycle_time`, and continues to station
   forecasting. NWP-enabled failures still log
   `forecast_cycle.nwp_fetch_failed_aborting` and return
   `errors=("NWP fetch failed",)`, so operators can distinguish an intentional
   runoff-only run from a failed NWP fetch.

4. **Runoff-only forecast provenance remains a known v0 limitation.** In
   runoff-only mode every stored forecast still records
   `nwp_cycle_source="primary"` and a non-null `nwp_cycle_reference_time`, even
   though no NWP was used. This is accepted for v0 because no current
   NWP-consuming models exist and `nwp_cycle_source` is not used for skill or
   filtering. The correct fix is a cross-cutting schema, API, and verification
   change owned by `docs/plans/078-forecast-provenance-no-nwp.md`; Plan 077
   must not change the enum, schema, API response, or `input_quality`.

5. **Disabling NWP does not affect current watchdog alerts.** The Mac Mini
   watchdog checks API health and backup staleness in
   `src/sapphire_flow/ops/watchdog.py`; it does not check NWP or
   weather-forecast freshness. The `[adapters.weather_forecast.monitoring]` and
   `[monitoring.forecast_cycle]` sections are dropped by `load_config` today
   and are not wired to runtime alerting code.

---

## Goal

`forecast-cycle` runs as a parameter-less Prefect deployment in two supported
production modes:

- **NWP enabled**: the flow self-constructs `MeteoSwissNwpAdapter` from config,
  fetches and archives NWP as today, and preserves the existing adapter
  injection contract for tests.
- **NWP disabled / runoff-only**: the flow skips NWP fetch, grid-store setup,
  and grid extraction entirely, then forecasts from past discharge and other
  non-NWP prerequisites.

The recommended first Mac Mini validation is the runoff-only mode. Its operator
preconditions are QC-passed past discharge observations (ingest + QC must have
run; `operational_inputs.py:145` fetches only `QC_PASSED` observations) and an
active station model assignment to a loaded model such as
`linear_regression_daily`. A station with RAW or failed-QC observations proceeds
with empty `past_targets`; the model run fails and is counted in
`stations_failed`, not silently skipped. These are operator preconditions, not
Plan 077 implementation tasks.

## Non-goals / preserve

- No change to model protocols, model data requirements, or the cycle-fallback
  policy from Plan 067.
- No change to v0 no-op NWP semantics from commit `19a31cb`.
- No change to `register_deployments.py`; `forecast-cycle` remains
  parameter-less and self-wires inside the flow.
- No removal of real NWP support. The MeteoSwiss adapter path remains available
  when `[adapters.weather_forecast].enabled = true`.
- No provenance fix in Plan 077. Plan 078 owns the eventual schema/API/input
  quality work for representing "no NWP used."
- No live HTTP / network access in unit tests.
- No new config keys beyond `[adapters.weather_forecast].enabled`.
- No attempt to fold in the pre-existing staging-5-stations onboarding overlay
  or decide whether the Mac Mini should use that overlay.

---

## Tasks

### T1 — read weather-forecast adapter config and NWP enabled state

- **Scope**: Add a small helper in `run_forecast_cycle.py`, mirroring
  `ingest_observations._load_adapter_endpoint`, that reads
  `[adapters.weather_forecast]` from the merged config rooted at
  `SAPPHIRE_CONFIG` via `load_merged_toml` + `_resolve_overlay_paths` because
  `load_config()` intentionally drops adapter sections except
  `archive_base_path`. The helper returns a typed value containing
  `enabled`, `stac_base_url`, `stac_collection`, and `scratch_path`; only a
  native TOML bool `true` enables NWP, absent section / absent key / `false`
  disables NWP, and a non-bool `enabled` value raises `ConfigurationError`. If
  `enabled = true` but required MeteoSwiss fields are missing, fail with a clear
  configuration error before making network calls. **Out**: changing
  `DeploymentConfig`, reading the gate from an injected `DeploymentConfig`
  object, or reading unrelated adapter sections.
- **Verification**: `uv run pytest tests/unit/flows/test_run_forecast_cycle.py`
  includes helper coverage for enabled true, explicit disabled false, absent
  section / absent key disabled, configured STAC / scratch values, overlay
  scalar override behavior, and non-bool `enabled = "false"` raising
  `ConfigurationError`.

### T2 — implement the three adapter branches in `run_forecast_cycle_flow`

- **Scope**: Replace the current `adapter is None` hard raise with three
  branches: injected adapter uses current behavior unchanged; `adapter is None`
  and merged config has NWP enabled self-constructs `MeteoSwissNwpAdapter` from
  T1 values using the `scripts/063_e2e_verify.py:59-69` adapter args
  (`stac_base_url`, `stac_collection`, configured `scratch_path`,
  `httpx.Timeout(connect=10.0, read=300.0, write=None, pool=5.0)`, and
  `max_fallback_steps = ceil(config.nwp_max_fallback_age_hours / 6.0)`);
  `adapter is None` and merged config has NWP disabled skips NWP setup and
  records runoff-only mode. Load `DeploymentConfig` before this branch so
  fallback-step derivation is available, but read the enabled gate only through
  the T1 merged-TOML helper. **Out**: changing task payload shapes, model
  execution, station filtering, or Prefect deployment registration.
- **Verification**: `uv run pytest tests/unit/flows/test_run_forecast_cycle.py`
  exercises all three branches without network access, and
  `uv run pyright src/sapphire_flow/flows/run_forecast_cycle.py` is clean.

### T3 — make grid/archive setup conditional on enabled NWP

- **Scope**: Change the `run_forecast_cycle.py:370-380` grid-store /
  grid-extractor construction so it runs only when NWP is enabled and the
  archive path is configured. In the disabled branch, ignore
  `config.nwp_grid_archive_base_path`, do not instantiate `ZarrNwpGridStore`,
  and do not import or instantiate `ExactExtractGridExtractor`. Preserve the
  existing behavior for an injected adapter or self-constructed adapter because
  both count as enabled NWP. **Out**: changing archive format, Zarr store
  internals, or exact-extract implementation.
- **Verification**: `uv run pytest tests/unit/flows/test_run_forecast_cycle.py`
  asserts the no-NWP branch does not construct grid machinery even when
  `nwp_grid_archive_base_path="/data/nwp_grids"`.

### T4 — skip the NWP task cleanly in runoff-only mode

- **Scope**: In runoff-only mode, do not submit `_fetch_nwp_task`; instead log
  an explicit `forecast_cycle.nwp_disabled` event and set
  `nwp_cycle = resolved_cycle_time` so the existing downstream
  `nwp_cycle is None` abort remains reserved for enabled-NWP failures. Keep the
  observation timestamp task and station forecast loop behavior unchanged.
  **Out**: changing result schema or adding a new public result field.
- **Verification**: `uv run pytest tests/unit/flows/test_run_forecast_cycle.py`
  asserts the no-NWP branch reaches forecasting, returns success, emits the
  disabled-mode log event, and does not call a fake/real adapter.

### T5 — update unit tests for all adapter branches

- **Scope**: Extend `tests/unit/flows/test_run_forecast_cycle.py` so branch 1
  keeps injecting `FakeWeatherForecastSource` unchanged, leaves
  `SAPPHIRE_CONFIG` unset, and relies on the injected-adapter bypass; branch 2
  writes a temp TOML with `[adapters.weather_forecast].enabled = true`, points
  `SAPPHIRE_CONFIG` at it with monkeypatch, and patches the source module
  `sapphire_flow.adapters.meteoswiss_nwp.MeteoSwissNwpAdapter` so no live HTTP
  call occurs; branch 3 uses a temp config with disabled NWP, an absent
  `[adapters.weather_forecast]` section, or an absent `enabled` key to assert
  the permanent runoff-only path. Add an explicit regression guard that no
  existing test points `SAPPHIRE_CONFIG` at base `config.toml` while calling
  `run_forecast_cycle_flow` without injecting an adapter, and confirm existing
  gridded tests that patch source grid components remain unaffected. **Out**:
  live STAC integration tests or changes to existing fake-store semantics.
- **Verification**: `uv run pytest tests/unit/flows/test_run_forecast_cycle.py`
  passes with no network access.

### T6 — add base config key and Mac Mini runoff-only overlay wiring

- **Scope**: Add `[adapters.weather_forecast].enabled = true` to base
  `config.toml`; create `config/overlays/mac-mini.toml` containing only
  `[adapters.weather_forecast]` plus `enabled = false`; and wire that overlay
  into `docker-compose.macmini.yml` by setting
  `SAPPHIRE_CONFIG_OVERLAY: /app/config/overlays/mac-mini.toml` and
  bind-mounting the overlay read-only on `prefect-worker`, `api`, and `init`,
  mirroring the env+mount pattern in `docker-compose.staging.yml:9-23`.
  `prefect-worker` is required because it runs `forecast-cycle`; `api` and
  `init` are included for consistency with the existing staging overlay and any
  config readers in those processes. **Out**: adding staging-5-stations content
  to the Mac Mini overlay, editing `register_deployments.py`, or changing the
  base `SAPPHIRE_CONFIG` path.
- **Verification**:
  `docker compose -f docker-compose.yml -f docker-compose.macmini.yml config`
  shows `SAPPHIRE_CONFIG_OVERLAY` and the read-only overlay mount on
  `prefect-worker`, `api`, and `init`, and
  `uv run pytest tests/unit/config/test_overlay.py` remains green.

### T7 — document NWP config, Mac Mini permissions, and modes

- **Scope**: Update `docs/deployment/mac-mini-staging.md` with a subsection
  explaining that runoff-only mode does not need NWP scratch/archive
  writability, while NWP-enabled mode uses `/data/nwp_grids` as a named Docker
  volume chowned `app:app` by `docker/entrypoint.sh:27` and
  `/tmp/sapphire_nwp` as a 4 GiB sticky tmpfs from
  `docker-compose.yml:100-106`. Include a writability verification command:
  `docker compose exec -u app prefect-worker sh -c 'touch /data/nwp_grids/.w /tmp/sapphire_nwp/.w && echo ok && rm /data/nwp_grids/.w /tmp/sapphire_nwp/.w'`.
  Also update `docs/spec/config-reference.toml` to document
  `[adapters.weather_forecast].enabled`, including strict bool handling,
  default semantics (absent section / absent key / false means disabled; true
  means enabled), and environment-specific disabling via overlays. **Out**:
  changing Docker mounts, entrypoint ownership logic, adding host `chown`
  instructions, or documenting the staging-5-stations overlay as part of the
  Mac Mini NWP gate.
- **Verification**:
  `uv run pre-commit run --files docs/deployment/mac-mini-staging.md docs/spec/config-reference.toml`
  and manual doc review confirm the command is copy-pasteable and references
  the actual `prefect-worker` service and paths.

### T8 — version bump, commit, and tag

- **Scope**: Run `uv run bump-my-version bump patch`, fold the version files
  into the same commit as code and docs, commit with a conventional message
  such as `fix(forecast-cycle): support optional NWP wiring`, then tag with
  `git tag v$(uv run bump-my-version show current_version)`. **Out**:
  pushing, merging, or triggering the live Mac Mini forecast cycle.
- **Verification**: `uv run pre-commit run --all-files` is clean and the tag
  exists locally.

---

## Operator gate (not a subagent task)

After T1-T8 land, an operator triggers the live `forecast-cycle` deployment on
the Mac Mini. The recommended first validation is runoff-only mode because
`docker-compose.macmini.yml` sets
`SAPPHIRE_CONFIG_OVERLAY=/app/config/overlays/mac-mini.toml`, and that overlay
sets `[adapters.weather_forecast].enabled = false`. Confirm the run reaches
per-station forecasting without constructing NWP grid machinery.

To enable NWP later, the operator removes the Mac Mini overlay wiring, removes
or flips the overlay line, or supplies another overlay that sets
`enabled = true`, then restarts the worker, runs the documented writability
check, and triggers another forecast-cycle run. The operator does **not** edit
base `config.toml` for the Mac Mini exception. Live triggering and hardware
validation are operator work, not subagent tasks.

---

## Dependency graph

```json
{
  "phases": [
    {
      "id": "phase-1-config-and-branching",
      "tasks": ["T1", "T2"],
      "parallel": false,
      "note": "Same flow module; T2 depends on the config helper from T1."
    },
    {
      "id": "phase-2-runoff-only-runtime",
      "tasks": ["T3", "T4"],
      "parallel": false,
      "depends_on": ["phase-1-config-and-branching"],
      "note": "T4 depends on the enabled/disabled runtime state and archive gating."
    },
    {
      "id": "phase-3-tests-and-infra",
      "tasks": ["T5", "T6"],
      "parallel": true,
      "depends_on": ["phase-2-runoff-only-runtime"]
    },
    {
      "id": "phase-4-docs",
      "tasks": ["T7"],
      "parallel": false,
      "depends_on": ["phase-3-tests-and-infra"]
    },
    {
      "id": "phase-5-release-bookkeeping",
      "tasks": ["T8"],
      "parallel": false,
      "depends_on": ["phase-4-docs"]
    }
  ]
}
```

---

## Affected files / Docs to update

- `src/sapphire_flow/flows/run_forecast_cycle.py` — config helper,
  adapter self-wiring, NWP-disabled branch, archive/grid gating.
- `tests/unit/flows/test_run_forecast_cycle.py` — coverage for injected
  adapter, self-constructed adapter, and runoff-only disabled mode.
- `config.toml` — add `[adapters.weather_forecast].enabled = true` for the
  production/canonical NWP-on default.
- `config/overlays/mac-mini.toml` — new minimal Mac Mini override setting only
  `[adapters.weather_forecast].enabled = false`.
- `docker-compose.macmini.yml` — wire the Mac Mini override through
  `SAPPHIRE_CONFIG_OVERLAY` and a read-only bind mount.
- `docs/deployment/mac-mini-staging.md` — document runoff-only vs NWP-enabled
  operation and NWP scratch/archive permissions.
- `docs/spec/config-reference.toml` — document
  `[adapters.weather_forecast].enabled` and overlay-based environment
  disabling.
- `pyproject.toml`, `src/sapphire_flow/__init__.py` — version bump from T8.
- MEMORY, after merge — note that base config keeps forecast-cycle NWP enabled
  by default, the Mac Mini uses `config/overlays/mac-mini.toml` for permanent
  runoff-only mode, and Plan 078 owns the NWP-less provenance follow-up.
