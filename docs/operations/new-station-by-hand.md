# Onboard a new station by hand (until the tools exist)

**Who this is for:** the hydrosolutions facilitator doing a first onboarding for an
organization, or anyone rehearsing it in a sandbox. **What this is:** the steps of the
[onboarding guide](onboarding-start-here.md) that have no tool yet, written out so they can
be repeated exactly. **What this is not:** a finished admin route. Each section names the
missing tool; when the tool exists, the section is replaced by one command.

Rehearsed end to end on 2026-11-16 in a throwaway sandbox with the synthetic Trishuli
station (code `00001`, #452): basin imported, weather history stored, observations and
discharge checked, `cmal_small` imported, one 10-day forecast stored. The sandbox forecast
used a Swiss-trained model on synthetic data: it proves the plumbing, **not** a forecast
anyone should read.

## Rules

- Do this in a **sandbox first**, never against a shared host without the owner's go-ahead.
- Keep the evidence folder, API key file and delivered data **outside any Git checkout**.
  Point the tools at the Gateway key's mode-600 file; never type the key into a
  command or message: `export RECAP_API_KEY_FILE=/path/to/key_file` (the path, not the key).
- One station at a time. Write down every manual step you ran in the evidence folder.
- Run everything from a checkout of this repository (`uv sync` once). All Python snippets
  below use `uv run python - <<'EOF' ... EOF`.

## Values you need first

| Value | Example (Trishuli) | Where from |
|---|---|---|
| Tenant code | `chwrr` (sandbox: use your own sandbox tenant) | Deployment config |
| Station code, network | `00001`, `research` | The approved basin handover (`package/manifest.json` network, `basins.gpkg` station code) |
| Name, longitude, latitude | Trishuli, 85.33996, 28.16434 | Station register |
| Timezone | `Asia/Kathmandu` | Station register |
| Gauge datum (m above sea level) | 1480 (invented for the sandbox) | **Ask for it.** The elevation of the gauge zero. Without it, water-level QC skips its range checks |
| Gateway name | `trishuli_00001` | What you registered in the [Gateway runbook](recap-gateway-runbook.md) |
| Rating table | invented for the sandbox | The organization's real rating table. A sandbox may invent one |

## Part 0: a throwaway sandbox (rehearsal only)

```bash
docker run -d --rm --name sapphire-onboarding-sandbox-db -e POSTGRES_PASSWORD=sandbox \
  -e POSTGRES_DB=sapphire -p 55452:5432 postgis/postgis:16-3.4
export DATABASE_URL=postgresql+psycopg://postgres:sandbox@127.0.0.1:55452/sapphire
uv run alembic upgrade head
```

Use the `16-3.4` image: `16-3.5` has no arm64 build, and Apple-silicon laptops run it under
emulation (slow but fine). Create a config file outside the checkout, for example
`sandbox-config.toml`:

```toml
[tenants.chwrr]
name = "Sandbox tenant"

[deployment]
writable_tenants = ["chwrr"]
operator = "admin test"

[adapters.recap_gateway]
base_url = "https://recap.ieasyhydro.org/sdk"
timeout_s = 300
verify_tls = true
staleness_threshold_hours = 6.0
hru_metadata_source = "manual_gpkg_upload"
max_retries = 3
```

Create the tenant: `SAPPHIRE_CONFIG=sandbox-config.toml uv run python -m sapphire_flow.cli.provision_tenants`.

**Prefect (needed for the history job and the model import).** A separate server, so
nothing else is touched:

```bash
export PREFECT_HOME=/path/outside/checkout/prefect-home PREFECT_API_URL=http://127.0.0.1:4299/api
uv run prefect server start --host 127.0.0.1 --port 4299 &
```

Register the deployments you need. The registrar assumes the Docker image path `/app`; in a
sandbox point it at your checkout and register only the two deployments this runbook uses:

```bash
SAPPHIRE_FLOW_SOURCE_ROOT=$PWD \
SAPPHIRE_ONLY_DEPLOYMENTS=ingest-operational-era5-history,import-model-artifact \
uv run python -m sapphire_flow.cli.register_deployments
```

Start one worker per pool (`ingest` and `default`) in the environment the jobs need:
`DATABASE_URL`, `SAPPHIRE_CONFIG`, `RECAP_API_KEY_FILE` (the path to the key file), `SAPPHIRE_DATA_DIR` and
`SAPPHIRE_INCOMING_DIR` (both folders outside the checkout):
`uv run prefect worker start --pool ingest --type process` (and `--pool default`).

Tear down when done: stop the workers and server, `docker stop sapphire-onboarding-sandbox-db`.

## Step 3: create the station record

One command. It is a dry run until you add `--apply`. Ask the gauge type first: a staff or
pressure gauge gives the height above the gauge zero; a bridge radar/ultrasonic sensor gives
the distance down to the water, so it needs the sensor elevation as well. In both cases the
datum is the elevation of the gauge zero in m a.s.l. Without a TTY pass `--gauge-type`,
`--datum-masl` (and `--sensor-masl`), or `--datum-unknown` (water-level range checks stay off).

```bash
export SAPPHIRE_CONFIG=/path/to/config.toml   # or pass --config
uv run python -m sapphire_flow.cli.create_station list    # tenants and networks that exist
uv run python -m sapphire_flow.cli.create_station --tenant chwrr --code 00001 \
  --network research --name Trishuli --lon 85.33996 --lat 28.16434 \
  --timezone Asia/Kathmandu --parameters water_level,discharge \
  --gauge-type staff --datum-masl 1480 --gateway-name trishuli_00001 [--apply]
```

`--code` and `--network` must equal the handover package's, or the basin import holds the
basin. The pair is unique across all tenants. The Gateway name is only checked and printed
into the next steps; it is not stored on the station.

## Step 4: import the basin

This one has a tool: `uv run python -m sapphire_flow.cli.import_basin_package --package-dir <handover>/package`
([importer runbook](basin-static-importer-runbook.md)). Expect `imported=1`, `onboarding_held=0`.

## Step 5: group, weather scope, weather history

**5a. Create a group, or add the station to an existing one.** One command, any tenant your
host may write (`--tenant` and `--network` are required, there is no default). Dry run is the
default; add `--apply` to write. Re-running with an existing group name adds only the missing
members. Keep listing **all** members in the scope below (the scope must match the group exactly).

```bash
SAPPHIRE_CONFIG=sandbox-config.toml uv run python scripts/create_station_group.py \
    --tenant chwrr --network research --name my-group --station-code 00001 --apply
```

It prints the group id and the station codes it added; look up the station id in the database
for 5b. An unknown code prints the networks that have stations in the tenant.

**5b. Weather scope.** Add to the config file (ids from 5a):

```toml
[weather_history.scopes.trishuli]
group_id = "<group id>"
original_gateway_hru_name = "<name inside the handover package>"
runtime_gateway_hru_name = "trishuli_00001"      # the name you registered
station_polygons = [{station_id = "<station id>", polygon_name = "g_00001"}]
```

The package may carry a different Gateway name from the one you registered; the runtime name
is what every later tool uses, so keep it identical in the Gateway, the scope and your notes.
Then (dry run first, then without `--dry-run`):

```bash
uv run python -m sapphire_flow.cli.onboard_nepal weather-bindings --config sandbox-config.toml \
  --tenant chwrr --confirm-t8 --weather-scope trishuli --confirm-package-joins --dry-run
```

**5c. Weather history.** Submit through the deployed job (the Prefect worker must be running):

```bash
uv run python -m sapphire_flow.cli.onboard_nepal history --config sandbox-config.toml --tenant chwrr \
  --confirm-t8 --weather-scope trishuli --start 2022-03-01T00:00:00Z --end 2026-11-09T00:00:00Z
```

Success means the job was submitted, not finished: wait for the worker to log `Completed`, then
check `select parameter, count(*), min(valid_time), max(valid_time) from historical_forcing group by 1`.
The Gateway's ERA5 trails real time by about 8 days: that gap is filled at forecast time by the
operational product (5d).

**5d. Forecast and operational weather bindings (no step creates them).** Without these the
forecast cycle stops with "0 FORECAST weather-source binding(s), expected exactly 1". The names
below are the ones the Nepal staging host uses.

```bash
uv run python - <<'EOF'
import os, sqlalchemy as sa
from sapphire_flow.store.station_store import PgStationStore
from sapphire_flow.types.station import StationWeatherSource
from sapphire_flow.types.enums import SpatialRepresentation, WeatherSourceStatus, WeatherSourceRole
CODE, NETWORK = "00001", "research"
with sa.create_engine(os.environ["DATABASE_URL"]).begin() as c:
    ss = PgStationStore(c); st = ss.fetch_station_by_code(CODE, NETWORK)
    for src, role in (("ifs_ecmwf", WeatherSourceRole.FORECAST),
                      ("recap_ecmwf_operational", WeatherSourceRole.OPERATIONAL)):
        ss.store_weather_source(StationWeatherSource(station_id=st.id, nwp_source=src,
            extraction_type=SpatialRepresentation.BASIN_AVERAGE, status=WeatherSourceStatus.ACTIVE, role=role))
    print([(w.nwp_source, w.role.value) for w in ss.fetch_weather_sources(st.id)])
EOF
```

## Step 6: observations, datum, rating, quality checks (no general tool yet)

Three facts decide whether this works:

1. **Ask for the gauge datum** (metres above sea level of the gauge zero). Store it on the
   station (`water_level_datum_masl`). Without it the range and gross-outlier checks are
   skipped, and the QC rule version reads `1.2-datum-skip` while every value "passes".
2. **Stored water level is metres above sea level.** QC subtracts the datum to get gauge height.
   If the data arrive as gauge height (or as the distance from a bridge down to the water),
   convert first: `stored = datum + gauge_height`. A value stored as gauge height fails the
   range check (it reads about `-datum`).
3. **Discharge needs a rating table.** Load the organization's table as a rating curve, then
   derive discharge from water level. A sandbox may invent a table (the Trishuli rehearsal used
   `Q = 60 * (h + 0.2) ** 1.8`, h = gauge height in m, labelled SYNTHETIC in `rating_type_label`).

**Loading observations (Plan 540 M3a).** `load_station_observations` replaces the load part of
the script below. It reads the station's gauge type and datum (set at station creation), converts
the reading (staff gauge: `datum + value`; bridge sensor: `sensor elevation - value`), stores it
and writes one audit row, then runs the same Stage 1 quality checks as live ingest (in memory on
a dry run). Dry run by default; `--apply` writes; exit 3 means refused: the station has no datum
(range and outlier checks would be skipped), no quality rule applies to the series (too few
readings per day), or the stored data changed while the checks ran. `--allow-unchecked` accepts
the first two:

```bash
uv run python -m sapphire_flow.cli.load_station_observations water-level \
  --tenant sandbox --station-code 00001 --network research --csv gauge_heights.csv \
  --time-column timestamp_npt --value-column water_level_m   # add --apply to write
uv run python -m sapphire_flow.cli.load_station_observations discharge ...   # same flags
```

It prints one worked example (`reading 2 m -> stored 1482 m a.s.l. ...`), the first and last UTC
instant, the QC rule version and counts (passed, failed, suspect, unchecked), and "N of 30 daily
values usable (passed or unchecked)". A new station has no history baseline yet, so it prints
"history-based check not run: no baseline yet" (that check is not counted as passed). The time zone defaults to the station's; override with
`--utc-offset-minutes 345` or `--zone Asia/Kathmandu`. If the values already are metres above sea
level, pass `--value-kind level-masl`.

Set the datum, then load and check the observations (CSV with a local-time column and a gauge
height column; adjust `CSV`, `OFFSET_MIN`, `DATUM`):

```bash
uv run python - <<'EOF'
import os, dataclasses, collections
import pandas as pd, sqlalchemy as sa
from datetime import UTC, datetime, timedelta, timezone
from sapphire_flow.store.observation_store import PgObservationStore
from sapphire_flow.store.station_store import PgStationStore
from sapphire_flow.services.qc import Stage1QualityChecker
from sapphire_flow.services.qc_datum import (obs_skipped_rules, obs_qc_rule_version,
    shift_observations_for_water_level_datum, add_observation_datum_details)
from sapphire_flow.config.qc_rules import resolve_qc_rules
from sapphire_flow.types.domain import aggregate_qc_status
from sapphire_flow.types.enums import ObservationSource
from sapphire_flow.types.observation import RawObservation
from sapphire_flow.types.datetime import ensure_utc
CODE, NETWORK, DATUM, OFFSET_MIN = "00001", "research", 1480.0, 345   # Nepal = UTC+5:45
CSV, TIME_COL, VALUE_COL = "gauge_heights.csv", "timestamp_npt", "water_level_m"
rules, _ = resolve_qc_rules()
df = pd.read_csv(CSV)
ts = pd.to_datetime(df[TIME_COL]).dt.tz_localize(timezone(timedelta(minutes=OFFSET_MIN))).dt.tz_convert("UTC")
with sa.create_engine(os.environ["DATABASE_URL"]).begin() as c:
    ss = PgStationStore(c); st = ss.fetch_station_by_code(CODE, NETWORK)
    ss.update_station(dataclasses.replace(st, water_level_datum_masl=DATUM, water_level_unit="m",
        updated_at=ensure_utc(datetime.now(UTC))))
    st = ss.fetch_station_by_code(CODE, NETWORK); sid, datum = st.id, st.water_level_datum_masl
    store = PgObservationStore(c)
    raws = [RawObservation(station_id=sid, timestamp=ensure_utc(t.to_pydatetime()), parameter="water_level",
        value=round(datum + float(v), 2), source=ObservationSource.MEASURED) for t, v in zip(ts, df[VALUE_COL])]
    store.store_raw_observations(raws)
    qs = store.qc_context_reader()
    raw = qs.fetch_observations(sid, "water_level", raws[0].timestamp, raws[-1].timestamp + timedelta(hours=1))
    sh = shift_observations_for_water_level_datum(raw, parameter="water_level", datum=datum)
    fl = Stage1QualityChecker().check(sh, rules, overrides=[], baselines=[], station_networks={sid: st.network},
        skipped_rule_ids=obs_skipped_rules("water_level", datum))
    fl = add_observation_datum_details(fl, raw_observations=raw, shifted_observations=sh,
        parameter="water_level", datum=datum)
    ups = [(o, aggregate_qc_status(fl[o.id]), fl[o.id]) for o in raw]
    print(qs.update_qc_batch_if_current(ups, qc_rule_version=obs_qc_rule_version("water_level", datum)),
          collections.Counter(s.value for _, s, _ in ups))
EOF
```

Check the result: `select qc_rule_version, qc_status, count(*) from observations group by 1,2`
must show `1.2-datum`, not `1.2-datum-skip`.

**Rating curve and discharge (Plan 540 M3c).** The same command family stores the table and
derives discharge. Both dry run by default; `--apply` writes. The table's stage column is read like
the observations (`--level-kind gauge-reading`, the default, uses the station's gauge type and
datum; `level-masl` takes metres above sea level as they are) and is validated (at least 2
points, strictly increasing stage, non-decreasing discharge, discharge above 0 for `log_linear`).
A sandbox may invent a table: label it so (`--label SYNTHETIC`):

```bash
uv run python -m sapphire_flow.cli.load_station_observations rating \
  --tenant sandbox --station-code 00001 --network research --csv rating.csv \
  --level-column stage_m --discharge-column q_m3s --label SYNTHETIC   # add --apply
uv run python -m sapphire_flow.cli.load_station_observations derive-discharge \
  --tenant sandbox --station-code 00001 --network research             # add --apply
```

An active curve is refused (exit 3) unless you pass `--supersede` (the old curve ends at
`--valid-from`, default now; the new one is the next version). `rating` prints the curve's stage
range next to the stored water-level range and warns when they do not overlap at all (usually a
wrong datum). For a bridge sensor a larger reading means a lower level, so discharge must fall
as the reading rises (the error names the file line). `derive-discharge` converts every stored
measured water level in `--start`/`--end` whose quality status is passed or unchecked (the same
"usable" rule as the forecast cycle) with the active curve (or `--curve-id`), stores it as
`rating_curve_derived` with the curve id, **skips and counts** levels that failed or are
suspect, levels not quality-checked yet (raw), and levels outside the table (never stores a
clamped value). It applies the active curve to every level in the window, so re-running it
after `--supersede` rewrites the derived values. Then runs the same
Stage 1 quality checks (rule version `1.2`, no datum) and prints "N of 30 daily values usable".
Exit 0 ok, 2 usage/validation, 3 refused (including a refused quality check, which rolls the
whole run back).

## Step 7: readiness (no general tool yet)

`onboard_nepal qualify` accepts only the six fixed DHM gauges ("missing DHM station 447") and
only discharge. Until the general report exists, gather these facts yourself and hand them to
the hydrologist:

```sql
select code, network, station_status, measured_parameters, water_level_datum_masl, forecast_targets from stations;
select parameter, source, qc_rule_version, qc_status, count(*), min(timestamp), max(timestamp)
  from observations group by 1,2,3,4;
select parameter, count(*), min(valid_time), max(valid_time) from historical_forcing group by 1;
select count(*) from rating_curves;
```

Ready for a model means: observations and weather history overlap for the training or lookback
window, QC ran with the datum, a rating curve exists if the target is discharge, and the model's
static attributes resolve (the aliases in `services/caravan_statics.py` map the model's names to
the package's HydroATLAS names).

## Step 8: switch the station on (no tool; planned as an admin-dashboard action)

Collection and the forecast cycle pick up only `operational` stations. In a sandbox:

```bash
uv run python - <<'EOF'
import os, dataclasses
import sqlalchemy as sa
from datetime import UTC, datetime
from sapphire_flow.store.station_store import PgStationStore
from sapphire_flow.types.enums import StationStatus
from sapphire_flow.types.datetime import ensure_utc
with sa.create_engine(os.environ["DATABASE_URL"]).begin() as c:
    ss = PgStationStore(c); st = ss.fetch_station_by_code("00001", "research")
    ss.update_station(dataclasses.replace(st, forecast_targets=frozenset({"discharge"}),
        updated_at=ensure_utc(datetime.now(UTC))))
    ss.update_station_status(st.id, StationStatus.OPERATIONAL)
EOF
```

⛔ On a shared host, promotion is the owner's decision: it starts live collection and puts the
station into the forecast cycle, and demoting it later can lose observations.

## Model and first forecast

1. **Import the trained artifact** (an existing, trusted file; for `cmal_small` the one already
   in use). Follow the [artifact import runbook](model-artifact-import-runbook.md). In a
   sandbox, put the file in the staging folder (`SAPPHIRE_INCOMING_DIR`), check its SHA-256
   against the source, and read the six provenance values from the source host's
   `model_artifact_provenance` record. Run the `import-model-artifact` deployment with
   `group_id` set to your group.
2. **Assign the model to the group** (after the artifact import; dry run first, then `--apply`):

   ```bash
   uv run python -m sapphire_flow.cli.assign_group_model --tenant chwrr \
     --group-name my-group --model cmal_small --time-step-hours 24 [--priority 1] [--apply]
   ```

   It refuses a model without a `models` row (import the artifact first), an unknown group
   and an INACTIVE assignment. Re-running an identical ACTIVE assignment changes nothing; an
   ACTIVE one that differs is overwritten (time step, priority, `created_at`).
3. **Forecast configuration (not in the guide before):** run with the base `config.toml`, the
   overlays `nepal-cloud.toml` and `nepal-daily-weather.toml`, and a last overlay of your own:

   ```toml
   [operational_weather]
   route = "recap_combined"
   automatic_issue_timezone = "Asia/Kathmandu"

   [adapters.recap_gateway]            # an overlay replaces this table whole: repeat all keys
   base_url = "https://recap.ieasyhydro.org/sdk"
   timeout_s = 300
   verify_tls = true
   staleness_threshold_hours = 6.0
   hru_metadata_source = "manual_gpkg_upload"
   max_retries = 3
   max_cycle_age_hours = 60.0

   [adapters.weather_forecast]
   enabled = true
   type = "recap_gateway"
   ```

**Run one cycle** (explicit UTC-midnight `cycle_time`, no older than the 60-hour limit):

```bash
SAPPHIRE_CONFIG=config.toml \
SAPPHIRE_CONFIG_OVERLAY=config/overlays/nepal-cloud.toml,config/overlays/nepal-daily-weather.toml,/path/to/my-last-overlay.toml \
  uv run python - <<'EOF'
from sapphire_flow.flows.run_forecast_cycle import run_forecast_cycle_flow as f
r = f(cycle_time="2026-11-16T00:00:00Z"); print(r.health, r.forecasts_stored, r.errors)
EOF
```

`forecasts_stored=1` and a `forecasts` row (10 days, 7 quantiles) is the pass. "Degraded" with a
staleness note is normal for synthetic data. The cycle needs **observations up to the issue
date**; a washed-away or test gauge has none, so a sandbox must load a synthetic series that
runs to today.

## What the cycle told us when it stopped (so you can recognize it)

| Message | Cause |
|---|---|
| `0 FORECAST weather-source binding(s), expected exactly 1` | Step 5d not done |
| `missing_required_target` / `operational_inputs.no_observations` | No observations up to the issue date, or no discharge (step 6) |
| `nwp_disabled ... mode=runoff_only` | `[adapters.weather_forecast]` still disabled by the Nepal overlay |
| `selected future weather requires a candidate-aware source` | `type = "recap_gateway"` missing |
| `[adapters.recap_gateway] missing required field(s)` | An overlay replaced the Gateway table with fewer keys |
| `missing DHM station 447` | `qualify` used for a station that is not one of the six |
