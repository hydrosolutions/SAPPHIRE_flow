---
status: DRAFT
created: 2026-07-14
plan: 117
title: Basin/static artifact architecture alignment
scope: Document the adjacent basin/static extraction artifact boundary; no extractor integration or importer implementation.
depends_on: []
blocks: [047]
---

# Plan 117 - Basin/static artifact architecture alignment

## Status

**DRAFT.** Do not implement or dispatch subagents until promoted to READY.

This is a documentation and architecture alignment plan. It does not integrate
the basin/static extraction code into SAP3 and does not build a SAP3 importer.

## Provenance

On 2026-07-14, HSOL communicated the currently known GeoPackage requirements to
the basin and static data extraction implementer. The model developer will
separately define the static Parquet shape needed by the model.

Known GeoPackage requirements communicated so far:

- GeoPackage format only.
- Readable and non-empty.
- CRS must be `EPSG:4326`.
- Layer name must start with a letter or underscore; `polygons` is recommended,
  `00003` is rejected.
- At least one polygon feature.
- Each feature must carry an attribute called `name`.
- Feature attribute `name` values are text, lowercase, and unique across all
  features.

Terminology risk surfaced in the same discussion: the extraction implementer
understands "Layer" as the GeoPackage name. SAP3/GIS tooling usually distinguishes
the GeoPackage file or Gateway HRU name from the internal GeoPackage layer/table
name. The architecture docs must disambiguate these terms before they become an
interface bug.

Owner preference: for basin polygons, the feature attribute `name` should contain
the gauge ID formatted as a string whenever that satisfies Gateway validation.

## Objective

Update the architecture and requirements docs so SAP3 clearly treats basin/static
extraction as adjacent helper tooling:

- SAP3 consumes a validated basin/static artifact package.
- SAP3 validates, imports, tracks provenance, and supplies static inputs to models.
- SAP3 does not own, vendor, or call the extraction tool's source code.
- The GeoPackage requirements and currently known naming conventions are recorded.
- Static Parquet details remain pending until the model developer communicates the
  expected schema.

## Non-goals

- No extraction-tool integration.
- No SAP3 importer implementation.
- No database migration or new persistence implementation.
- No change to the ForecastInterface contract.
- No attempt to finalize the static attribute feature set before the modeller
  provides it.

## Owner decisions to preserve

1. **Artifact boundary, not code boundary.** The adjacent tool may be open source,
   source-available, a service, or internal helper tooling. SAP3's interface is the
   accepted artifact package plus a documented regeneration path.
2. **Feature `name` preference.** Basin feature `name` should be the canonical
   gauge ID as a string when valid. If a gauge ID violates Gateway rules, use a
   deterministic normalized feature key and keep the raw gauge ID in
   `station_code`.
3. **Static Parquet is pending.** The modeller owns the expected static feature
   names, units, and Parquet shape. SAP3 docs should mark this as TBD rather than
   inventing a schema.

## Open questions

1. Does the Gateway requirement "Layer name must start with a letter or underscore"
   refer to the GeoPackage filename / Gateway HRU name, or to the internal
   GeoPackage layer/table name?
2. Are all DHM gauge IDs valid as lowercase text feature `name` values, or do any
   require normalization?
3. Will the modeller require one static row per gauge, one row per basin, or a more
   complex multi-index Parquet layout?
4. Which actor owns the durable regeneration path: DHM, HSOL, or the basin/static
   extraction tool maintainer?

## Phase 1 - Requirements document alignment

### Task 1A - Update the basin/static artifact contract

**Scope in:** Update `docs/requirements/04-basin-static-artifact-contract.md` with
the currently known GeoPackage requirements, the gauge-ID-as-string preference for
feature `name`, and the explicit terminology split between GeoPackage/HRU name,
internal layer/table name, and per-feature `name`.

**Scope out:** Do not finalize static Parquet columns beyond the generic model
feature matching rule.

**Verification:**

```bash
uv run python - << 'EOF'
from pathlib import Path

text = Path("docs/requirements/04-basin-static-artifact-contract.md").read_text()
required = [
    "GeoPackage format only",
    "EPSG:4326",
    "Layer name",
    "feature attribute `name`",
    "gauge ID",
    "static Parquet",
    "TBD",
]
missing = [item for item in required if item not in text]
if missing:
    raise SystemExit(f"missing expected terms: {missing}")
EOF
```

### Task 1B - Update Gateway requirements terminology

**Scope in:** Update `docs/requirements/01-data-gateway-requirements.md` so the
Gateway-facing wording no longer leaves "layer" ambiguous. It should distinguish
the GeoPackage filename / HRU name from the internal layer/table name and from the
feature `name` attribute echoed in forcing data.

**Scope out:** Do not reopen the Gateway API design or add a programmatic geometry
upload requirement.

**Verification:**

```bash
uv run python - << 'EOF'
from pathlib import Path

text = Path("docs/requirements/01-data-gateway-requirements.md").read_text()
required = ["GeoPackage", "HRU", "layer", "feature", "`name`"]
missing = [item for item in required if item not in text]
if missing:
    raise SystemExit(f"missing expected terms: {missing}")
EOF
```

### Task 1C - Update requirements index

**Scope in:** Keep `docs/requirements/README.md` aligned with the new role of
`04-basin-static-artifact-contract.md`.

**Scope out:** Do not reorganize existing requirements docs.

**Verification:**

```bash
uv run python - << 'EOF'
from pathlib import Path

text = Path("docs/requirements/README.md").read_text()
if "04-basin-static-artifact-contract.md" not in text:
    raise SystemExit("requirements index does not list plan 117 artifact contract doc")
EOF
```

## Phase 2 - Architecture alignment

### Task 2A - Update Flow 0 / Flow 5.2 architecture language

**Scope in:** Update `docs/architecture-context.md` so deployment onboarding and
station onboarding describe the basin/static artifact package as an accepted
source alongside SAP3-side extraction from cached/global/national datasets.

**Scope out:** Do not change runtime flow design or add implementation details for
an importer.

**Verification:**

```bash
uv run python - << 'EOF'
from pathlib import Path

text = Path("docs/architecture-context.md").read_text()
required = [
    "basin/static artifact",
    "adjacent",
    "Flow 5 step 5.2",
    "static attributes",
]
missing = [item for item in required if item not in text]
if missing:
    raise SystemExit(f"missing expected architecture terms: {missing}")
EOF
```

### Task 2B - Record the Gateway polygon-reference persistence gap

**Scope in:** Add a concise architecture note that SAP3 must eventually persist
the Gateway polygon-reference mapping:

```text
station_id
basin_id
gateway_hru_name
feature name
spatial_type
band_id
```

This is an implementation gap, not a schema change in this plan.

**Scope out:** Do not edit migrations, DB metadata, or domain types.

**Verification:**

```bash
uv run python - << 'EOF'
from pathlib import Path

text = Path("docs/architecture-context.md").read_text()
required = ["gateway_hru_name", "feature name", "band_id"]
missing = [item for item in required if item not in text]
if missing:
    raise SystemExit(f"missing Gateway polygon-reference terms: {missing}")
EOF
```

### Task 2C - Align v1 planning references

**Scope in:** Update `docs/plans/README.md` and any directly relevant active v1
plan references so Plan 117 is discoverable as the owner of the basin/static
artifact architecture alignment.

**Scope out:** Do not rewrite Plan 106 sequencing unless a contradiction is found.

**Verification:**

```bash
uv run python - << 'EOF'
from pathlib import Path

text = Path("docs/plans/README.md").read_text()
if "117" not in text or "basin/static" not in text:
    raise SystemExit("plan index does not mention Plan 117 basin/static work")
EOF
```

## Phase 3 - Review and readiness

### Task 3A - Self-review and collaborator-question check

**Scope in:** Check that the docs distinguish resolved requirements from open
questions, especially static Parquet shape and GeoPackage layer/filename
terminology.

**Scope out:** Do not promote to READY without user confirmation.

**Verification:**

```bash
uv run python - << 'EOF'
from pathlib import Path

plan = Path("docs/plans/117-basin-static-artifact-architecture.md").read_text()
if "status: DRAFT" not in plan:
    raise SystemExit("Plan 117 must remain DRAFT until owner confirmation")
if "Open questions" not in plan:
    raise SystemExit("Plan 117 must keep collaborator questions visible")
EOF
```

### Task 3B - Multi-model review before READY

**Scope in:** Run the repo-standard multi-model review before any READY promotion.
Review must check consistency with `docs/requirements/01-data-gateway-requirements.md`,
`docs/requirements/04-basin-static-artifact-contract.md`,
`docs/architecture-context.md`, `docs/spec/types-and-protocols.md`, and
`docs/v0-scope.md`.

**Scope out:** No implementation review is needed because this plan is docs-only
until a later importer/schema plan exists.

**Verification:**

```bash
uv run python - << 'EOF'
from pathlib import Path

text = Path("docs/plans/117-basin-static-artifact-architecture.md").read_text()
required = ["Multi-model review", "before READY", "docs-only"]
missing = [item for item in required if item not in text]
if missing:
    raise SystemExit(f"missing review gate terms: {missing}")
EOF
```

## Dependency graph

```json
{
  "phases": [
    {
      "id": "phase-1",
      "tasks": ["1A", "1B", "1C"],
      "parallel": false
    },
    {
      "id": "phase-2",
      "tasks": ["2A", "2B", "2C"],
      "parallel": false,
      "depends_on": ["phase-1"]
    },
    {
      "id": "phase-3",
      "tasks": ["3A", "3B"],
      "parallel": false,
      "depends_on": ["phase-2"]
    }
  ]
}
```
