# Reference dataset

This directory holds observation data for the Swiss BAFU LINDAS stations used
by the `ReplayStationAdapter` in replay adapter tests, integration tests, and
the end-to-end forecast cycle test.

## Station selection

7 BAFU river gauging stations covering major Swiss basins and a mix of parameters:

| Code | Name | Parameters |
|------|------|------------|
| 2004 | Bern, Schönau (Aare) | discharge, water_level |
| 2009 | Brugg (Aare) | discharge, water_level |
| 2033 | Andermatt (Reuss) | discharge |
| 2044 | Hagneck (Aarezufluss zum Bielersee) | discharge |
| 2091 | Brienzwiler (Aare) | discharge, water_level |
| 2159 | Basel, Rheinhalle (Rhein) | discharge, water_level |
| 2085 | Bellinzona (Ticino) | discharge, water_level |

Selection rationale: geographic spread across the Alps (Andermatt, Brienzwiler),
Mittelland (Bern, Brugg), Jura foothills (Hagneck), the Rhine at Basel, and the
southern Ticino catchment. Two stations are discharge-only; five carry both
discharge and water level, exercising the multi-parameter ingest path.

## Files

- `stations.toml` — station metadata for the recording tool and replay tests
- `bafu_observations.parquet` — synthetic stand-in observation data (see
  [Synthetic fixture — by design](#synthetic-fixture--by-design) below)
- `bafu_forecast_stations.geojson` / `bafu_q_forecast_2135.json` — real,
  live-captured payloads (2026-07-10) from the `hydrodaten.admin.ch` route-C
  forecast collector (Plan 111); used by
  `tests/unit/adapters/test_bafu_forecast.py` to test
  `BafuForecastAdapter` parsing. Unrelated to the LINDAS dataset above —
  BAFU's *forecast* endpoint, not the *observation* endpoint.

## Synthetic fixture — by design

`bafu_observations.parquet` is a **deliberately synthetic** stand-in, not a
recording from the live BAFU LINDAS endpoint. This is a known, intentional
design choice — not a bug or a gap waiting to be fixed today.

**Why re-recording is not possible now.**
The BAFU LINDAS SPARQL endpoint is real-time only. The adapter
(`hydro_scraper.py:173-192`) binds a single current-reading subject URI and
ignores any time range — there is no way to request historical observations
through this endpoint or this adapter. Every call returns the reading at the
moment of the request.

Building a fixture that represents real BAFU data therefore requires
*collecting the current reading repeatedly over time* — not issuing a
one-off recording command. Until a scheduled collection pipeline
(see `docs/plans/058-bafu-lindas-archive-collection.md`) accumulates ≥6
months of real readings (the gate defined in `docs/v0-scope.md` §E1), the
synthetic fixture is the right stand-in.

**What the synthetic fixture guarantees.**
The current `bafu_observations.parquet` passes full schema and structural
tests (`tests/unit/adapters/test_reference_dataset.py`): correct columns,
correct dtypes, valid `RawObservation` values, correct `ObservationSource`
enum, and file-size bounds. It gives the replay adapter and the e2e test a
valid, schema-conformant input to exercise the pipeline — which is all
Tier 2 fixtures are required to do at this stage.

**When to replace it.**
Per `docs/v0-scope.md` §E1: once the archive-collection pipeline (Plan 058)
has accumulated ≥6 months of real readings, promote the archive to
`bafu_observations.parquet` and update this note to reflect the real
recording window. See [Recording BAFU observations](#recording-bafu-observations)
and [Refreshing the dataset](#refreshing-the-dataset) below for the
commands to run at that point.

## Prerequisites

- Network access to `https://lindas.admin.ch/query` (public, no auth required)
- Python environment synced: `uv sync`
- Run commands from the project root (the tool reads `config.toml` from the cwd)

## Recording BAFU observations

> **Note**: the commands below require the LINDAS archive-collection pipeline
> (Plan 058) to have promoted ≥6 months of real readings first. They do not
> work today because the LINDAS endpoint is real-time only — see
> [Synthetic fixture — by design](#synthetic-fixture--by-design).

Once the archive is ready, record a reference window with:

```bash
uv run python -m sapphire_flow.tools.record_fixtures \
    --source bafu \
    --start 2026-04-01T00:00:00+00:00 \
    --end   2026-04-03T00:00:00+00:00
```

What the command does, step by step:

1. Reads `config.toml` → `[adapters.river_stations].endpoint` (`https://lindas.admin.ch/query`).
2. Parses `tests/fixtures/reference/stations.toml` to build `StationConfig` objects for all 7 stations.
3. For each station, posts a SPARQL query to the LINDAS endpoint requesting discharge, waterLevel, and waterTemperature triples since `--start`.
4. Trims the results to exclude any timestamps at or after `--end`.
5. Writes all observations to `tests/fixtures/reference/bafu_observations.parquet` (overwrites any existing file).

The `--stations` and `--output` arguments can override the defaults:

```bash
uv run python -m sapphire_flow.tools.record_fixtures \
    --source   bafu \
    --start    2026-04-01T00:00:00+00:00 \
    --end      2026-04-08T00:00:00+00:00 \
    --stations tests/fixtures/reference/stations.toml \
    --output   tests/fixtures/reference/
```

A 2-day window for 7 stations typically returns a few hundred rows and completes
in under 60 seconds. The resulting Parquet file should be well under 500 KB.

## Recording NWP forecasts

Not yet implemented. `--source nwp` is parsed but logs a warning and exits
without writing any file. NWP fixture recording will be added in Phase 3 v0b.

## Verifying the dataset

After recording (or after any change to the Parquet), run the reference dataset tests:

```bash
uv run pytest tests/unit/adapters/test_reference_dataset.py -v
```

The tests check:

- The Parquet loads without error via `ReplayStationAdapter`
- All rows contain valid `RawObservation` values and `ObservationSource` enums
- The schema has exactly the expected columns with correct dtypes
- File size is below 500 KB

Tests skip automatically if `bafu_observations.parquet` does not exist, so CI
passes even before the first recording.

## Dataset schema

| Column | Polars type | Description |
|--------|-------------|-------------|
| `station_code` | `Utf8` | BAFU station code (e.g. `"2004"`) |
| `timestamp` | `Datetime("us", "UTC")` | Observation time, UTC microsecond precision |
| `parameter` | `Utf8` | `"discharge"`, `"water_level"`, or `"water_temperature"` |
| `value` | `Float64` | Observed value in SI units (m³/s, m, °C) |
| `source` | `Utf8` | Always `"measured"` for BAFU LINDAS data |

## Refreshing the dataset

> **Note**: the triggers below assume the archive-collection pipeline (Plan 058)
> has promoted ≥6 months of real readings to this directory. Until then, the
> synthetic fixture stands in by design — see
> [Synthetic fixture — by design](#synthetic-fixture--by-design).

Once real data is available, re-record when:

- `stations.toml` adds, removes, or changes a station
- A longer time window is needed (e.g. to cover a flood event)
- The LINDAS SPARQL endpoint changes its schema
- `RawObservation` or the Parquet schema changes structurally

Re-recording overwrites the existing file. Commit the new Parquet with a message
that states the recording window (e.g. `chore: re-record BAFU fixture 2026-04-01..08`).

## Gap budget and no-catchup property

This section documents the constraints that govern how and when `bafu_observations.parquet`
can be promoted from synthetic to real data. It exists to prevent silent incorrect
assumptions during the 6-month accumulation phase.

### LINDAS is real-time only

The BAFU LINDAS SPARQL endpoint serves only the **current reading** at query time.
`hydro_scraper.py:173-192` binds a single current-reading subject URI; there is no
historical-window parameter, no pagination, and no replay capability. A call at
09:15 returns the 09:15 reading — the 09:10 reading is permanently unretrievable.

### Every minute of downtime is permanent data loss

There is no catchup mechanism. If the Prefect worker, the network, or the database is
unavailable during a polling window, the observations for that window are **gone**.
Gap causes include:

- Prefect worker outage (restart, deploy, crash)
- Network partition between the Mac Mini and `lindas.admin.ch`
- PostgreSQL downtime (migration, vacuum, backup lock)
- LINDAS endpoint downtime (maintenance, rate-limiting)

**Action during the accumulation phase**: log all known downtime events in an ops journal
(date, time, duration, cause). This allows the team to correlate archive gaps to known
outages and distinguish systematic adapter bugs from one-off events.

### 95% interval coverage threshold

A candidate 6-month promotion window is accepted only when **every (station, parameter)
pair** achieves ≥95% interval coverage over the window.

Coverage formula:

```
coverage = actual_observation_count / expected_observation_count
expected = window_hours × (60 / cadence_minutes)
```

At 10-minute cadence over 6 months (~4380 hours): expected ≈ 26 280 observations per
(station, parameter) pair. A 95% threshold allows ≈1314 missed polls — roughly 9 days of
total downtime spread across 6 months.

Windows that fall below the threshold are skipped — the synthetic placeholder stays in
place. There is no "good enough" relaxation: a sub-threshold fixture would introduce
silent gaps into integration tests and replay-adapter runs that depend on a continuous
time series.

### Gap detection during accumulation

Until Flow 4 (pipeline monitoring) is implemented, gaps accumulate silently.
Two band-aid mechanisms are active during the accumulation phase:

- **Weekly schema-drift check** (`tests/integration/live/test_lindas_live_schema.py`,
  `live_lindas` marker, `.github/workflows/live-lindas-weekly.yml`) — confirms the LINDAS
  response structure is still parseable. Failure = schema drift = potential silent
  data corruption since the last green run.
- **Daily coverage summary** (`sapphire_flow.tools.observation_coverage_summary`) —
  queries the `observations` table for the last 24 h and emits per-station gap
  percentages to structlog. If any station falls below 90% coverage for two consecutive
  days, an ops check is due.

Both are described in `docs/plans/058-bafu-lindas-archive-collection.md` (T5 and T6).

## Known limitations

- **No golden answers**: no expected forecast outputs are tied to this dataset yet.
- **NWP not recorded**: the `--source nwp` path is a stub. NWP reference fixtures
  (ICON-CH2-EPS cycles as Zarr) will be added in Phase 3 v0b.
