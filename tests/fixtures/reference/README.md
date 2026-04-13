# Reference dataset

This directory holds recorded observation data from the Swiss BAFU LINDAS SPARQL
endpoint. The dataset drives the `ReplayStationAdapter` used in replay adapter
tests, integration tests, and the end-to-end forecast cycle test.

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
- `bafu_observations.parquet` — observation data (synthetic placeholder until
  recorded from live BAFU LINDAS — see Known limitations below)

## Prerequisites

- Network access to `https://lindas.admin.ch/query` (public, no auth required)
- Python environment synced: `uv sync`
- Run commands from the project root (the tool reads `config.toml` from the cwd)

## Recording BAFU observations

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

After recording, run the reference dataset tests:

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

Re-record when:

- `stations.toml` adds, removes, or changes a station
- A longer time window is needed (e.g. to cover a flood event)
- The LINDAS SPARQL endpoint changes its schema
- `RawObservation` or the Parquet schema changes structurally

Re-recording overwrites the existing file. Commit the new Parquet with a message
that states the recording window (e.g. `chore: re-record BAFU fixture 2026-04-01..08`).

## Known limitations

- **Synthetic placeholder**: the current `bafu_observations.parquet` was generated
  synthetically. It passes schema and structural tests but does not contain real
  hydrological time series. Replace it by running the recording command above.
- **No golden answers**: no expected forecast outputs are tied to this dataset yet.
- **NWP not recorded**: the `--source nwp` path is a stub. NWP reference fixtures
  (ICON-CH2-EPS cycles as Zarr) will be added in Phase 3 v0b.
