# Plan 029 — LINDAS Adapter Fix (Lake Stations + Since Removal)

**Status**: DONE  
**Phase**: 3 (Adapters) / 6 (Observation Ingest)

## Context

Live testing of the LINDAS SPARQL endpoint revealed three issues:

1. **LINDAS only stores the latest observation per station** — each station URI holds exactly one observation (the current reading). The `since` time-filter in the SPARQL query is useless and should be removed to avoid confusion. Polling + store-layer dedup (ON CONFLICT DO NOTHING) handles repeated fetches correctly.

2. **Lake stations are invisible** — the adapter hardcodes `river/observation/{code}` as the URI. LINDAS has 34 lake stations at `lake/observation/{code}`. Lake stations measure `waterLevel` only (no `discharge`, no `waterTemperature`). The v0 scope includes 33 lake stations for water_level experiments.

3. **The ingest flow excludes lake stations** — filters to `StationKind.RIVER` only and hardcodes `"discharge"` when building the since-dict.

### LINDAS data model (confirmed via live queries 2026-04-13)

| Aspect | River | Lake |
|--------|-------|------|
| URI pattern | `river/observation/{code}` | `lake/observation/{code}` |
| Count | 199 | 34 |
| Parameters | discharge, waterLevel, waterTemperature | waterLevel |
| Observations per URI | 1 (latest only) | 1 (latest only) |

### Not in scope

- `dangerLevel` — BAFU's own alert classification, not a measurement we store
- `isLiter` — misleading flag; values are already in m³/s (confirmed)
- SMN weather adapter — deferred (not needed for v0)
- Water temperature QC — deferred to v3+
- `StationDataSource` protocol change — `since` kept on interface (replay adapter uses it)

---

## Tasks

### Task 1: Fix `HydroScraperAdapter`

**Scope**: Remove `since` FILTER from SPARQL query; support lake station URIs; adjust fetched parameters per station kind (river: discharge + waterLevel + waterTemperature; lake: waterLevel only). Skip WEATHER stations with a warning log. The `since` parameter stays on the method signature for protocol conformance but is not used.

**Out of scope**: Protocol changes, replay adapter changes, unit conversion.

**Files**: `src/sapphire_flow/adapters/hydro_scraper.py`

**Verification**: `uv run pytest tests/integration/adapters/test_hydro_scraper.py -x -q`

### Task 2: Fix `ingest_observations_flow`

**Scope**: Fetch both RIVER and LAKE stations (not WEATHER). Fix the since-dict construction that hardcodes `"discharge"` — use `"water_level"` for lake stations, keep `"discharge"` for river stations.

**Out of scope**: QC rule changes, alert logic changes, new service code.

**Files**: `src/sapphire_flow/flows/ingest_observations.py`

**Verification**: `uv run pytest tests/unit/flows/test_ingest_observations.py -x -q`

### Task 3: Update adapter tests

**Scope**: Add lake station test (correct URI, waterLevel-only parse). Add mixed river+lake batch test. Add lake LINDAS fixture file for contract test. Update mock transport to handle both URI patterns. Verify `since` value does not affect SPARQL query.

**Out of scope**: Live endpoint tests, replay adapter tests.

**Files**: `tests/integration/adapters/test_hydro_scraper.py`, `tests/fixtures/lindas_lake_sample_response.json`

**Verification**: `uv run pytest tests/integration/adapters/test_hydro_scraper.py -x -q`

### Task 4: Update flow tests

**Scope**: Add test with lake-only stations (water_level observations). Add test with mixed river+lake stations. Update existing tests if the since-dict parameter change affects assertions.

**Out of scope**: New QC tests, alert tests.

**Files**: `tests/unit/flows/test_ingest_observations.py`

**Verification**: `uv run pytest tests/unit/flows/test_ingest_observations.py -x -q`

---

## Dependency Graph

```json
{
  "phases": [
    {
      "id": "phase-1",
      "tasks": ["1", "2"],
      "parallel": true
    },
    {
      "id": "phase-2",
      "tasks": ["3", "4"],
      "parallel": true,
      "depends_on": ["phase-1"]
    }
  ]
}
```

## Verification (full)

```bash
uv run pytest tests/integration/adapters/test_hydro_scraper.py tests/unit/flows/test_ingest_observations.py -x -q
uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/
uv run pytest --tb=short -q
```
