---
status: DRAFT
created: 2026-07-14
plan: 115c
parent: 115
title: Weather-source identity cleanup — 0031 NOT NULL, API/dashboard role, doc sync
scope: Tightening + surfaces. Non-gating; ships after the rollback window closes.
depends_on: [115a, 115b]
blocks: []
---

# Plan 115c — Cleanup: `0031`, surfaces, docs

> Shared context and locked decisions live in the umbrella:
> [Plan 115](115-weather-source-identity-model.md).

## Status

**DRAFT.** Non-gating. Neither 081 nor 082 waits on this — they need the **field** (115a), not the
constraint.

## Scope

### 1. Revision `0031` — tighten

Ships **after the rollback window closes** (i.e. once no pre-115a image remains in the rollback
path — a deployment judgement, not a timer).

1. Re-run the **allowlist guard** over any remaining NULL rows *before* the final backfill — an
   unknown source name is still a human decision at this point, not a `CASE` fallthrough.
2. Re-run the backfill for stragglers.
3. `alter_column role nullable=False`.
4. Tighten the check to `role IN ('forecast','reanalysis')`.
5. **Delete the `_row_to_weather_source` NULL shim** (marked `# Plan 115c: delete with revision 0031`).

### 2. API + dashboard surface the role

The station-detail page is the operator surface for verifying a station's FORECAST vs REANALYSIS
bindings — the whole point of the track. Pyright **cannot** catch this gap
(`WeatherSourceResponse` is a separate Pydantic model, not a `StationWeatherSource` construction
site), so it is an explicit task:

- `api/schemas.py::WeatherSourceResponse` — add `role: str`.
- `api/routes/api_stations.py::_to_weather_source_response` — populate `role=ws.role.value`.
- `api/templates/stations/detail.html` — add a **Role** column to the Weather Sources table,
  alongside Extraction / Status.

Note `api/routes/stations.py:266` reflects the table and returns raw row dicts, so it will surface
`role` automatically — including `NULL` during the migration window. Confirm that renders sanely.

### 3. Doc sync — `0031` only

*(The `role` column's docs — `database-schema.md`, `architecture-context.md`, `conventions.md`,
`touchpoint-maps.md` — moved to **115a**, where the column is actually added. Review round 6 found
that deferring them here violated "every code change updates affected docs" and made 115a
non-standalone. 115c keeps only what `0031` itself changes.)*

- `docs/standards/cicd.md` — close out the `0030`→`0031` sequence: the rollback window is over, the
  NULL shim is gone, `role` is NOT NULL.
- `docs/spec/database-schema.md` — flip `role` from nullable to NOT NULL in the column description.

### 4. Stale-plan cleanup

`docs/plans/091-macmini-nwp-on-data-collection.md` is stale against the code: it claims Plan 090 is
unmerged and that `config/overlays/mac-mini.toml` disables NWP. Neither is true (090 P1 shipped;
`mac-mini.toml:10` enables it). Correct or archive it — a stale plan that contradicts the code is a
trap for the next agent.

## Tests

- `WeatherSourceResponse` exposes `role`; the detail page renders the Role column.
- `0031` refuses to complete if any NULL row carries an unknown source name.
- After `0031`, a `StationWeatherSource` row cannot be written without a role (DB-level).

## Exit gates

```bash
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run pyright src/
uv run pytest
```
