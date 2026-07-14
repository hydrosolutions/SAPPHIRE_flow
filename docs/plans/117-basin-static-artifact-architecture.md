---
status: DRAFT
created: 2026-07-14
revised: 2026-07-14
plan: 117
title: Basin/static artifact architecture alignment
scope: Document the adjacent basin/static extraction artifact boundary; no extractor integration or importer implementation.
depends_on: []
blocks: [047]  # basin/static architecture cleanup only — 047 also needs its own re-scope (Plan 106 §"047 is stale")
---

# Plan 117 - Basin/static artifact architecture alignment

## Status

**DRAFT.** Do not implement or dispatch subagents until promoted to READY.

This is a documentation and architecture alignment plan. It does not integrate
the basin/static extraction code into SAP3 and does not build a SAP3 importer.

**The pre-READY multi-model review is a gate *on* this plan, not a task *inside* it**
(`docs/workflow.md` § Multi-Model Review). A post-implementation review runs on the
resulting doc diff before it merges. Neither is a phase in the dependency graph.

## Baseline

Phase 1 operates on **drafted documents that exist only in the working tree**:
`docs/requirements/04-basin-static-artifact-contract.md` (untracked, new) plus
unstaged edits to `01-data-gateway-requirements.md` and `requirements/README.md`.

**These drafts are Phase-1 input, not a committed baseline.** They are NOT committed
to `main` first. The drafted `04-…` is a collaborator-facing RFC-2119 contract that
this plan already knows is wrong in two places (Owner decision 3; Open question 1) —
landing it on `main` unfixed would publish a contract we know to be defective, even
briefly.

**Dispatch rule.** Phase 1 edits the drafts in place. Phase 1 + Phase 2 land together
as **one reviewed docs commit** to `main` — the corrected `04-…`, the `01-…`/README
edits, `00-…`, `architecture-context.md`, `047`, and the plan index. Nothing reaches
`main` until the post-implementation review passes.

**Consequence for Task 1A:** it reads "correct the drafted contract", not "update a
committed one". If the drafts are lost, 1A becomes an author-from-scratch task and
the plan must be re-reviewed — the drafts are load-bearing.

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

### Terminology — RESOLVED by the owner, 2026-07-14

The reason this plan exists was a four-way ambiguity in the word "name". The owner
resolved it:

| Term | Meaning | Where it appears |
|---|---|---|
| **Gateway HRU** | **the GeoPackage itself** — a `.gpkg` holding ≥1 polygon. **One Gateway HRU = one GeoPackage file.** | `manifest.gateway_hru_names`, `gateway_hru_name` column |
| **The HRUs proper** | **the polygons inside** that GeoPackage — the actual hydrological response units. One Gateway HRU holds **one or several** of them, **all of the same kind**: basin outlines **or** elevation bands, never both (Owner decision 4). | features in the `.gpkg` |
| Internal layer/table name | the table inside the GeoPackage (`polygons` recommended) | GDAL/OGR, Gateway validation |
| Feature `name` attribute | the per-polygon key the Gateway echoes in forcing columns | `name` column, forcing payload |

Two consequences the drafted `04-…` does not currently reflect:

1. **The letter/underscore rule binds BOTH the layer name AND the feature `name`
   values.** It is therefore a **confirmed, normative MUST** — not an assumption.
   Because gauge IDs may be composed entirely of digits (below), a bare gauge ID is
   **not** a legal feature `name`, which is what forces the `g_` prefix in Owner
   decision 2.
2. **One HRU = one GeoPackage** means "unique within the Gateway HRU" and "unique
   across all features in the GeoPackage" are the *same* statement. The contract
   should say it once, in the GeoPackage form, and additionally require that every
   row in a given `.gpkg` carries the same `gateway_hru_name`.
3. Because a Gateway HRU is **single-kind** (Owner decision 4), `spatial_type` is a
   property of the **HRU**, not of each polygon: every forcing column from one fetch
   shares it, and only band HRUs carry a `band_id`. There is no basin/band merge
   step, so `04-…` §5's "SAP3 MAY combine basin and band features into the
   Gateway-uploaded GeoPackage" clause is **deleted**.

### Gauge IDs — RESOLVED by the owner, 2026-07-14

Gauge IDs are **defined as strings**. They are typically not leading-zero, but:

- they **may be composed entirely of digits** (so they may start with a digit); and
- for some hydromets they contain characters such as `/`, `-`, `'`, `_`, and letters.

This makes normalization mandatory and makes the collision policy load-bearing
rather than theoretical: `KAR/01` and `KAR-01` both normalize to `kar_01`.

## Objective

Update the architecture and requirements docs so SAP3 clearly treats basin/static
extraction as adjacent helper tooling:

- SAP3 consumes a validated basin/static artifact package.
- SAP3 validates, imports, tracks provenance, and supplies static inputs to models.
- SAP3 does not own, vendor, or call the extraction tool's source code.
- The naming layers above are disambiguated in every collaborator-facing doc, using
  the owner's resolved definitions (a Gateway HRU *is* a GeoPackage).
- Static Parquet details are rolled back to TBD until the model developer
  communicates the expected schema.

## Non-goals

- No extraction-tool integration.
- No SAP3 importer implementation.
- No database migration or new persistence implementation.
- No change to the ForecastInterface contract.
- No attempt to finalize the static attribute feature set before the modeller
  provides it.
- **No change to the Swiss v0 CAMELS-CH static-attribute path.** Tasks 2A/2B edit
  deployment-generic Flow 0 / Flow 5.2 prose; the Swiss path must read exactly as
  it does today.

## Forbidden files and actions

This plan touches **no code, schema, or type spec**. Do not edit:

- `src/**`, `tests/**`, `flows/**`, `scripts/**`
- `alembic/**` (no migrations)
- `docs/spec/types-and-protocols.md`

Any task that appears to require one of these is an escalation, not a scope
extension.

## Owner decisions to preserve

1. **Artifact boundary, not code boundary.** The adjacent tool may be open source,
   source-available, a service, or internal helper tooling. SAP3's interface is the
   accepted artifact package plus a documented regeneration path.
2. **Feature `name` is one unconditional convention.** Every basin feature `name`
   is `g_<station_code_normalized>` (normalization: lowercase, non-alphanumeric
   runs → `_`). Bands are `g_<station_code_normalized>_band_<band_id>`. The raw
   gauge ID is **contained in** the feature name and is stored verbatim, as a
   **string**, in the `station_code` column.
   - **The prefix is required, not merely prudent.** The letter/underscore rule is
     confirmed to bind the feature `name` values, and gauge IDs may be all-digits.
     A bare gauge ID is therefore an illegal `name` for exactly those stations. The
     original round-1 wording ("the gauge ID as a string when valid") is dead: its
     "valid" branch excludes the most common DHM case, and a per-station conditional
     would produce a non-uniform key space for no benefit.
   - **Collision policy — load-bearing.** Gauge IDs may contain `/`, `-`, `'`, `_`
     and letters, so normalization is *not* injective: `KAR/01` and `KAR-01` both
     become `kar_01`. A station literally coded `g_5501` also collides with the
     derived name for station `5501`. A collision is a **package validation
     failure**: the producer MUST reject the package with an explicit error naming
     both colliding station codes. It MUST NOT be resolved silently — no suffixing,
     no truncation. Enforceable today via the existing `ids_unique` check in
     `04-…` §8.
   - String-typing `station_code` is load-bearing: gauge IDs are defined as strings,
     and an integer-typed GeoPackage column would corrupt any that are all-digits
     (a leading-zero ID such as `0439` silently becomes `439`).
3. **Static Parquet is pending.** The modeller owns the expected static feature
   names, units, and Parquet shape. SAP3 docs mark this **TBD (modeller-owned)**
   rather than inventing a schema. The drafted `04-…` §6 currently violates this —
   it already fixes one-row-per-basin, required columns, numeric-only types, and a
   boolean ban. Task 1A rolls that back to the generic FI/SAP3 static-feature
   matching rule.
4. **A Gateway HRU is single-kind: basins OR bands, never mixed.** (Owner,
   2026-07-14 — previously undefined; the Gateway itself does not care, so this is a
   SAP3-side constraint we adopt because it is free.)
   - **`spatial_type` becomes an HRU-level property.** Every forcing column returned
     for one HRU shares it; only band HRUs carry a `band_id`. This matches the
     existing DB invariant (`spatial_type = 'elevation_band'` ⟺ `band_id IS NOT
     NULL`) and `GridExtractor`'s `BasinAverageForecast | ElevationBandForecast`
     union — one spatial type per station per source. A mixed HRU would be the only
     container in the system holding both.
   - **No merge step.** The source package already separates `basins.gpkg` from
     `bands.gpkg` because their schemas differ (bands carry `band_id`,
     `min/max_elevation_m`). Single-kind HRUs carry that split through to the
     Gateway unchanged, so basin/band feature-name collisions cannot arise and the
     "MAY combine" clause in `04-…` §5 is deleted.
   - **Independent lifecycle.** Bands can be re-extracted or backfilled without
     refetching basin forcing, and vice versa.
   - **Cost accepted:** a station needing both basin-average *and* band forcing uses
     two HRUs, not one. No model in the FI contract declares both, so this is
     currently theoretical.

## Resolved questions (owner, 2026-07-14)

1. **Does the letter/underscore rule bind the layer name or the feature `name`?**
   **Both.** It is a confirmed normative MUST on each. See § Terminology.
2. **Is one Gateway HRU strictly one GeoPackage?** **Yes** — in the Gateway's sense
   an HRU *is* a GeoPackage containing ≥1 polygon, and the polygons inside are the
   HRUs proper. The two uniqueness scopes therefore coincide.
3. **Are gauge IDs numeric / leading-zero?** IDs are **strings**; typically no
   leading zeros, but they **may be all-digits**, and for some hydromets contain
   `/`, `-`, `'`, `_`, and letters.

These answers are now baked into Owner decision 2 and Task 1A. They do not reopen
any decision — they *confirm* the `g_` convention and promote the leading-digit rule
from assumption to normative requirement.

## Open questions (tracked, non-blocking)

1. Will the modeller require one static row per gauge, one row per basin, or a more
   complex multi-index Parquet layout?
2. Which actor owns the durable regeneration path: DHM, HSOL, or the basin/static
   extraction tool maintainer?

Neither blocks READY. Both are recorded as TBD in `04-…` rather than guessed.

## Relationship to Plan 047 and Flow 0

Plan 117 unblocks the **basin/static architecture** portion of Nepal onboarding
only. It does **not** make Plan 047 ready:

- Plan 047 (`docs/plans/047-nepal-v1-data-sources.md`) is a DRAFT stub scoped to
  IFS / DHM obs / ERA5-Land / elevation-band extraction. It carries no
  basin-geometry or static-attribute scope today and no reference to 117.
- Plan 106 additionally records that 047 is **stale** and must be re-scoped before
  READY (strip elevation-band extraction, the standalone ERA5-Land adapter, and the
  DHM obs adapter). That re-scope is 047's own work, not 117's.
- Flow 0 (deployment onboarding) still has no dedicated plan. 117 defines only the
  artifact boundary that Flow 0's static-attribute step may consume; it does not
  design Flow 0.

Task 2C records this relationship in both directions.

## Phase 1 - Requirements document alignment

### Task 1A - Fix the basin/static artifact contract

**Scope in:** Edit `docs/requirements/04-basin-static-artifact-contract.md`:

1. Add a terminology section carrying the owner's resolved definitions verbatim:
   **a Gateway HRU *is* a GeoPackage** containing ≥1 polygon; **one Gateway HRU =
   one GeoPackage**; the polygons inside are the HRUs proper; the internal
   layer/table name and the per-feature `name` attribute are distinct from both.
2. State the letter/underscore rule as a **confirmed normative MUST binding both the
   internal layer/table name and the feature `name` values** (owner, 2026-07-14).
   Apply it **identically** to the bands table in §5, which today omits both the
   lowercase and the leading-digit clause — the same field is currently specified
   two different ways in one document.
3. Restate feature-`name` uniqueness as **unique across all features in the
   GeoPackage**, noting that this is the same statement as "unique within the
   Gateway HRU" now that one HRU is one GeoPackage. Add the invariant that every row
   in a `.gpkg` MUST carry the same `gateway_hru_name`.
3b. Record Owner decision 4 in §5: a Gateway HRU is **single-kind** — it MUST hold
   basin polygons **or** band polygons, never both — so `spatial_type` is an
   HRU-level property. **Delete** the existing clause "SAP3 MAY combine basin and
   band features into the Gateway-uploaded GeoPackage…"; there is no merge.
4. Make the feature-name convention unconditional per Owner decision 2 — remove any
   "gauge ID when valid" phrasing and state `g_<station_code_normalized>` as the
   rule, with the raw string-typed gauge ID in `station_code`. Record *why*: gauge
   IDs are strings that may be all-digits, and a digit-leading `name` is rejected.
   Add the collision policy (reject the package, name both colliding station codes,
   never resolve silently) and note that gauge IDs may contain `/`, `-`, `'`, `_`
   and letters, so normalization is not injective.
5. Roll §6 back to **TBD (modeller-owned)**: keep only the generic rule that every
   name in `InputRequirement.static` / `ModelDataRequirements.static_features` must
   appear as a column with a non-null value. Remove the one-row-per-basin
   assertion, the fixed required-column table, the numeric-only type constraint,
   and the boolean ban.
6. Note the import target: static attributes land in `basins.attributes` JSONB and
   are surfaced to models as `Float64` (`docs/spec/types-and-protocols.md`
   `static` slot). Integer catalog columns are cast on import.

**Scope out:** Do not design the static Parquet schema. Do not add importer
implementation detail. Do not touch `docs/spec/types-and-protocols.md`.

**Verification:**

```bash
uv run python - << 'EOF'
from pathlib import Path

text = Path("docs/requirements/04-basin-static-artifact-contract.md").read_text()

must_appear = [
    # Owner-resolved terminology: an HRU IS a GeoPackage, holding >=1 polygon,
    # all of one kind (Owner decision 4).
    "one Gateway HRU = one GeoPackage",
    "unique across all features in the GeoPackage",
    "basin polygons or band polygons, never both",
    "internal layer/table name",
    # Static schema stays the modeller's.
    "TBD (modeller-owned)",
    "`basins.attributes`",
    "Float64",
    # Owner decision 2 — the naming convention and its collision policy.
    "g_<station_code_normalized>",
    "colliding station codes",
]
must_be_gone = [
    "contains one row per basin",
    "Static features MUST be numeric (`int` or `float`)",
    "Boolean static features MUST NOT be used",
    "MUST be lowercase, unique within the Gateway HRU, and MUST NOT start with a digit",
    # Owner decision 4 — HRUs are single-kind, so there is no basin/band merge.
    "SAP3 MAY combine basin and band features",
]

missing = [t for t in must_appear if t not in text]
lingering = [t for t in must_be_gone if t in text]
if missing:
    raise SystemExit(f"1A: missing required text: {missing}")
if lingering:
    raise SystemExit(f"1A: text that must be rolled back is still present: {lingering}")

# The leading-digit rule is a CONFIRMED MUST and must bind the band `name` too —
# today only the basin table carries it, which is the asymmetry being fixed.
if text.count("MUST NOT start with a digit") < 2:
    raise SystemExit("1A: leading-digit MUST applied to only one of the basin/band name rules")
EOF
```

### Task 1B - Disambiguate Gateway requirements terminology

**Scope in:** Edit `docs/requirements/01-data-gateway-requirements.md` so the
Gateway-facing wording never says a bare "layer". Rewrite the § Gateway-side
validation item 4 to state that the letter/underscore rule binds **both** the
**internal GeoPackage layer/table name** and the per-feature `name` **values**, and
record the owner's definition that a Gateway HRU *is* a GeoPackage (one HRU = one
`.gpkg`) whose polygons are the HRUs proper. Point at
`04-basin-static-artifact-contract.md` for the full naming rules.

**Scope out:** Do not reopen the Gateway API design. Do not add a programmatic
geometry-upload requirement.

**Verification:**

```bash
uv run python - << 'EOF'
from pathlib import Path

text = Path("docs/requirements/01-data-gateway-requirements.md").read_text()
required = [
    "internal GeoPackage layer/table name",
    "one Gateway HRU = one GeoPackage",
    "04-basin-static-artifact-contract.md",
]
missing = [t for t in required if t not in text]
if missing:
    raise SystemExit(f"1B: missing required text: {missing}")

# The ambiguous bare "Layer name" rule (§ Gateway-side validation, item 4) is the
# defect being fixed — it must be rewritten, not merely supplemented.
if "4. Layer name must start with a letter or underscore" in text:
    raise SystemExit("1B: the ambiguous bare 'Layer name' rule is still present")
EOF
```

### Task 1C - Update the requirements index

**Scope in:** In `docs/requirements/README.md`, describe `04-…` as the contract for
an **adjacent** (non-integrated) basin/static extraction tool, so the index itself
states the boundary.

**Scope out:** Do not reorganize the other requirements docs.

**Verification:**

```bash
uv run python - << 'EOF'
from pathlib import Path

text = Path("docs/requirements/README.md").read_text()
if "04-basin-static-artifact-contract.md" not in text:
    raise SystemExit("1C: requirements index does not list the artifact contract")
if "adjacent" not in text:
    raise SystemExit("1C: index does not state the adjacent-tool boundary")
EOF
```

### Task 1D - Correct the internal gap analysis

**Scope in:** `docs/requirements/README.md` names
`docs/requirements/00-internal-gap-analysis.md` the source of truth for the
requirements split, and that doc still records as RESOLVED that **SAP3 runs static
extraction** (§ geometry source-of-truth row; catchment auto-processing row; the
HydroATLAS/MERIT Flow 0 build items). Plan 117's boundary amends this. Update those
rows to read: SAP3 owns validation, import, and provenance; **extraction may be
SAP3-side or an accepted adjacent basin/static package**, per
`04-basin-static-artifact-contract.md`.

**Scope out:** Do not restate the whole artifact contract in `00-…`; cross-reference
it. Do not change the geometry source-of-truth decision itself (SAP3 = SoT stands).

**Verification:**

```bash
uv run python - << 'EOF'
from pathlib import Path

text = Path("docs/requirements/00-internal-gap-analysis.md").read_text()
if "04-basin-static-artifact-contract.md" not in text:
    raise SystemExit("1D: gap analysis does not reference the artifact contract")
if "adjacent" not in text:
    raise SystemExit("1D: gap analysis still implies SAP3-only static extraction")

# The contradicting sentences must actually be gone, not merely supplemented.
stale = [
    "rigorous validation + static extraction",
    "wire HydroATLAS/MERIT datasets for Nepal into Flow 0",
]
lingering = [s for s in stale if s in text]
if lingering:
    raise SystemExit(f"1D: SAP3-does-static-extraction wording survives: {lingering}")

# But the geometry source-of-truth decision must NOT be reopened.
if "SAP3 = full SoT" not in text:
    raise SystemExit("1D: geometry source-of-truth decision was altered — out of scope")
EOF
```

## Phase 2 - Architecture alignment

### Task 2A - Update Flow 0 / Flow 5.2 architecture language

**Scope in:** Update `docs/architecture-context.md` so Flow 0 (deployment
onboarding) and Flow 5 step 5.2 (catchment attributes) describe an accepted
**basin/static artifact package** produced by an **adjacent** tool as a valid
source, alongside the existing SAP3-side extraction from cached/global/national
datasets. State explicitly that SAP3 validates and imports the package and does not
call the extraction tool.

**Scope out:** Do not change runtime flow design. Do not add importer implementation
detail. Do not alter the Swiss/CAMELS-CH v0 path.

**Verification:**

```bash
uv run python - << 'EOF'
from pathlib import Path

text = Path("docs/architecture-context.md").read_text()
required = [
    "basin/static artifact package",
    "adjacent",
    "04-basin-static-artifact-contract.md",
    # The boundary itself — the whole point of the plan.
    "does not call the extraction tool",
]
missing = [t for t in required if t not in text]
if missing:
    raise SystemExit(f"2A: missing required text: {missing}")

# Both Flow 0 and Flow 5.2 must mention the package — one edit is not enough.
if text.count("basin/static artifact package") < 2:
    raise SystemExit("2A: package named in only one of Flow 0 / Flow 5 step 5.2")

# Swiss v0 path must be untouched.
if "CAMELS-CH" not in text:
    raise SystemExit("2A: Swiss CAMELS-CH static path disappeared — out of scope")
EOF
```

### Task 2B - Record the Gateway polygon-reference persistence gap

**Scope in:** Add a short, named note to `docs/architecture-context.md` — heading
**"Gateway polygon-reference persistence gap"** — stating that SAP3 must eventually
persist the mapping from Gateway forcing columns back to SAP3 stations and bands
(`station_id`, `basin_id`, `gateway_hru_name`, `name`, `spatial_type`, `band_id`),
and that the current `Basin` type and `basins` table define no first-class fields
for it. **Point at `04-…` §5a for the field list rather than re-listing it** — the
tuple is already written down there and in `00-…`; a third copy will rot.

This is a recorded implementation gap. It is **not** a schema change in this plan.

**Scope out:** Do not edit migrations, DB metadata, domain types, or the type spec.

**Verification:**

```bash
uv run python - << 'EOF'
from pathlib import Path

text = Path("docs/architecture-context.md").read_text()
if "Gateway polygon-reference persistence gap" not in text:
    raise SystemExit("2B: named gap note is absent")
if "04-basin-static-artifact-contract.md" not in text:
    raise SystemExit("2B: gap note does not cross-reference the contract")
# The substance: today's Basin type / basins table have nowhere to put this mapping.
if "no first-class fields" not in text:
    raise SystemExit("2B: gap note does not state that no first-class fields exist today")
EOF
```

### Task 2C - Wire the Plan 047 / Flow 0 relationship in both directions

**Scope in:**

- `docs/plans/047-nepal-v1-data-sources.md`: add a line noting that the basin/static
  artifact boundary is owned by Plan 117, and that 047 additionally needs its own
  re-scope per Plan 106.
- `docs/plans/README.md`: the Plan 117 entry already exists but currently reads as
  if 117 gates 047 wholesale. Reword it to say 117 unblocks the **basin/static
  architecture cleanup only**, and that 047 separately needs the Plan 106 re-scope.

**Scope out:** Do not rewrite Plan 106 sequencing. Do not fill in the 047 stub.

**Verification:**

```bash
uv run python - << 'EOF'
from pathlib import Path

p047 = Path("docs/plans/047-nepal-v1-data-sources.md").read_text()
if "117" not in p047 or "basin/static" not in p047:
    raise SystemExit("2C: Plan 047 does not back-reference Plan 117")

index = Path("docs/plans/README.md").read_text()
if "basin/static architecture cleanup only" not in index:
    raise SystemExit("2C: plan index still implies Plan 117 gates all of 047")
# Anchored to the Plan 117 entry — a bare "106" is pre-satisfied by 106's own entry.
if "re-scope per Plan 106" not in index:
    raise SystemExit("2C: plan index does not point 047's re-scope at Plan 106")
EOF
```

## Dependency graph

```json
{
  "phases": [
    {
      "id": "phase-1",
      "tasks": ["1A", "1B", "1C", "1D"],
      "parallel": false
    },
    {
      "id": "phase-2",
      "tasks": ["2A", "2B", "2C"],
      "parallel": false,
      "depends_on": ["phase-1"]
    }
  ]
}
```

The pre-READY multi-model review and the post-implementation review on the doc diff
are workflow gates, not phases (see § Status).
