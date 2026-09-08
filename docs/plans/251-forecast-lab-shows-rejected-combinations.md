---
status: DRAFT
created: 2026-09-04
plan: 251
title: The Forecast Lab should show a rejected combination, not hide it
scope: Add quality status and flags to the combined-forecast objects in the Forecast Lab snapshot format, which requires a v2 to v3 transition across every producer and checker of the version string. Explicitly NOT the storage of a failed combination (Plan 253 T2a), NOT any change to QC rules, NOT the member-model objects.
depends_on: [253]
blocks: []
source: 2026-09-04 — owner decision OD-1a in Plan 253, taken once review established the cost of surfacing
---

# Plan 251 — the Forecast Lab should show a rejected combination

## Status

**DRAFT — not reviewed.** Split out of Plan 253 on 2026-09-04 by owner decision, so that a versioned
external-format change does not ride on a persistence fix.

## Why this exists

Plan 253 makes a combined (`_pooled`) forecast that fails QC **stored, marked failed** rather than
dropped (OD-1), on the reasoning that the combination exists to compare models and a dropped row
leaves no record of what was rejected. Plan 253 then has to **exclude that row from the Forecast
Lab** (OD-1a), because surfacing it costs more than that plan should carry.

So the record exists in the database and is invisible in the surface built to read it. This plan
closes that gap.

## Why it costs what it costs

The Lab's snapshot is a published format with a version string, `forecast-lab-snapshot/v2`. Its
combined-forecast objects are **strict** — `additionalProperties: false`, with exactly eight
permitted fields (`available`, `combination_strategy`, `ensemble_size` or `native_step_seconds`,
`forecast_id`, `horizon_end`, `horizon_start`, `issued_at`, `model_key`) at
`$defs/CombinedForecastMembersSchema` and `$defs/CombinedForecastQuantilesSchema` in
`docs/spec/forecast-lab-snapshot-v2.schema.json`. Neither carries `qc_status` or `qc_flags`, and the
spec states that a v2 validator rejects unknown fields (`docs/spec/forecast-lab-snapshot.md:31`).

Adding two fields is therefore a format version change, and the version string is stamped or checked
in eight places:

| File | Role |
|---|---|
| `src/sapphire_flow/api/forecast_lab_schemas.py` | the Pydantic literal and the combined models |
| `src/sapphire_flow/api/routes/forecast_lab.py` | the API response stamp |
| `src/sapphire_flow/cli/export_forecast_lab.py` | the exporter stamp |
| `src/sapphire_flow/services/forecast_lab/snapshot.py` | the snapshot identifier and availability logic |
| `docs/spec/forecast-lab-snapshot-v2.schema.json` | the authoritative schema |
| `docs/spec/forecast-lab-snapshot.md` | the written specification |
| `tests/fixtures/forecast_lab/forecast_lab_snapshot_example.json` | the example fixture |
| `tests/unit/services/forecast_lab/test_snapshot.py` and `tests/unit/api/test_forecast_lab_schema.py` | the schema-sync tests |

## Open decisions — the owner's, before this is scoped further

- **D1 — does v3 replace v2, or coexist?** Replacing is simpler and matches the v1→v2 precedent;
  coexisting protects any external consumer already reading v2. We do not currently know of an
  external consumer, but that should be confirmed rather than assumed.
- **D2 — are the new fields required or optional in v3?** Required is cleaner to validate; optional
  keeps a v3 reader tolerant of a producer that has not caught up.
- **D3 — what does `available` mean for a rejected combination?** It is currently a plain
  renderability flag (`services/forecast_lab/snapshot.py:453-504`). A failed combination is
  renderable but should not be treated as a usable forecast, so either `available` gains a defined
  meaning relative to QC, or a separate field carries it and `available` is left alone.

## Non-goals

- Storing the failed combination — that is Plan 253 T2a and must land first.
- Any change to QC rules, thresholds, or which combinations fail.
- The member-model snapshot objects; only the combined objects gain the fields.
- Surfacing input quality in the Lab. Related seam, different signal, not this plan.

## Tasks

Every code task carries the Task Exit Gate (`docs/workflow.md:198`).

### T1 — decide and record the version strategy

**Outcome:** D1, D2 and D3 are answered in this plan with rationale, and the answer names whether any
external consumer of v2 exists.

**In:** this plan document; a search of the repo and deployment config for v2 consumers.

**Out:** any code change. This task exists so the format decision is made once, in the open, rather
than inside an implementation diff.

**Pre-change:** N/A — decision task; the three decisions are recorded as open above.

**Verification:** N/A — decision task. Each answer cites the evidence it rests on.

### T2 — add the fields and bump the format

**Outcome:** the combined-forecast objects carry `qc_status` and `qc_flags`, the snapshot stamps v3,
and the committed schema, spec, fixture and sync tests all agree.

**In:** the eight files in the table above, together in one change — the sync tests exist precisely
to fail when they drift apart. Depends on T1.

**Out:** the member-model objects. Any change to what is stored.

**Pre-change:** adding `qc_status` to a combined object and validating the snapshot against `docs/spec/forecast-lab-snapshot-v2.schema.json` fails, because both combined `$defs` set `additionalProperties: false`.

**Verification:** `uv run pytest tests/unit/api/test_forecast_lab_schema.py tests/unit/services/forecast_lab/test_snapshot.py` — the emitted snapshot validates against the committed v3 schema, the fixture matches, and the version string is identical in every producer.

### T3 — stop excluding the rejected combination

**Outcome:** a `QC_FAILED` combination appears in the Lab, visibly marked failed, and Plan 253's
exclusion filter is removed rather than left dormant.

**In:** `services/forecast_lab/db_sources.py:184-204` (the exclusion Plan 253 T2a adds) and the
availability logic at `services/forecast_lab/snapshot.py:453-504`, per D3. Depends on T2.

**Out:** re-litigating OD-1. The row is stored; this task only decides how it is shown.

**Pre-change:** with Plan 253 landed, a snapshot built over a cycle whose combination failed QC contains no combined entry for it at all.

**Verification:** `uv run pytest tests/unit/services/forecast_lab/test_snapshot.py` — a failed combination is present, carries its `qc_status` and flags, and is distinguishable from a passing one by a consumer reading only the snapshot.

## Exit gates

```bash
uv run ruff format --check src/ tests/ && uv run ruff check src/ tests/
uv run pyright src/
uv run pytest
uv run python scripts/check_readiness.py docs/plans/251-forecast-lab-shows-rejected-combinations.md
```

Two conditions hold in addition:

1. **No producer stamps a version that disagrees with the committed schema.** The sync tests are the
   mechanism; they must be run, not merely present.
2. **Plan 253 has landed first.** Surfacing a row that is not yet stored would test nothing.

## Dependency graph

```json
{
  "nodes": [
    {"id": "T1", "phase": 1, "depends_on": []},
    {"id": "T2", "phase": 1, "depends_on": ["T1"]},
    {"id": "T3", "phase": 1, "depends_on": ["T2"]}
  ]
}
```
