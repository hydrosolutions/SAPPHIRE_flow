# Reference dataset

7 BAFU stations covering major Swiss river basins (Aare, Reuss, Rhein, Ticino).

## Station selection rationale

- Mix of discharge-only (2) and discharge+water_level (5)
- Geographic spread: Alps (Andermatt, Brienzwiler), Mittelland (Bern, Brugg), Jura (Hagneck), Basel, Ticino (Bellinzona)
- All are river gauging stations from the BAFU network

## Files

- `stations.toml` -- station metadata for the recording tool and replay tests
- `bafu_observations.parquet` -- observation data (synthetic placeholder until recorded from live BAFU LINDAS)

## Recording

To record real data from live BAFU LINDAS, run:

```bash
uv run python -m sapphire_flow.tools.record_fixtures \
    --source bafu \
    --start 2026-04-01T00:00:00+00:00 \
    --end 2026-04-03T00:00:00+00:00
```

## Planned additions

- NWP reference fixtures will be added by Plan 021
