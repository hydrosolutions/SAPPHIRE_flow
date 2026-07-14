---
status: READY
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

**READY** (owner confirmed, 2026-07-14). Phase 1 may be dispatched.

This is a documentation and architecture alignment plan. It does not integrate
the basin/static extraction code into SAP3 and does not build a SAP3 importer.

**Pre-READY review: converged.** Multi-model review ran across several rounds
(Claude design + Codex repo-grounded), final verdict APPROVE with no open findings.
The review is a gate *on* this plan, not a task *inside* it (`docs/workflow.md`
§ Multi-Model Review) — it is not a phase in the dependency graph. A
post-implementation review still runs on the resulting doc diff before it merges.

**Task 1A is partially executed already** (items 5–9, applied to the working-tree
`04-…` draft at the owner's direction on 2026-07-14, together with the collaborator
brief). Items 1–4 remain. See Task 1A.

## Baseline

Phase 1 operates on **drafted documents that exist only in the working tree**:
`docs/requirements/04-basin-static-artifact-contract.md` (untracked, new) plus
unstaged edits to `01-data-gateway-requirements.md` and `requirements/README.md`.

Plus the collaborator brief `docs/requirements/basin-static-extraction-brief.md`
(untracked, new).

**These drafts are Phase-1 input, not a committed baseline.** They are NOT committed
to `main` first. The drafted `04-…` is a collaborator-facing RFC-2119 contract still
carrying known defects (the §4/§5 name rules, the "MAY combine" clause) — landing it
on `main` unfixed would publish a contract we know to be wrong, even briefly.

**Dispatch rule.** Phase 1 edits the drafts in place. Phase 1 + Phase 2 land together
as **one reviewed docs commit** to `main` — the corrected `04-…`, the `01-…`/README
edits, `00-…`, `architecture-context.md`, `047`, and the plan index. Nothing reaches
`main` until the post-implementation review passes.

**Consequence for Task 1A:** it reads "correct the drafted contract", not "update a
committed one". If the drafts are lost, 1A becomes an author-from-scratch task and
the plan must be re-reviewed — the drafts are load-bearing.

## Provenance

On 2026-07-14, HSOL communicated the currently known GeoPackage requirements to
the basin and static data extraction implementer. Later the same day the model
developer supplied their **actual producers** (`compute_forcing_attributes.py`,
`extract_hydroatlas.py`, `hydroatlas.py`, `static_attributes.md`), which settled the
static Parquet **shape** (Owner decision 3) and the ERA5-on-S3 question (Owner
decision 6).

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
- The static Parquet **shape** is stated as confirmed (wide, one row per gauge,
  string key, `Float64` attributes); only the feature **list** stays the modeller's.

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
3. **Static Parquet: the modeller owns the feature LIST; SAP3 owns the SHAPE.**
   *(Revised 2026-07-14 after the model developer supplied their actual producers —
   `compute_forcing_attributes.py`, `extract_hydroatlas.py`, `hydroatlas.py`,
   `static_attributes.md`.)*
   - **The shape is CONFIRMED, not TBD.** Both producers write
     `schema = {"gauge_id": pl.Utf8, **{name: pl.Float64 for name in source_names}}`
     — a wide table, one row per gauge, one column per attribute, every attribute
     `Float64`. That is exactly SAP3's static-input contract
     (`docs/spec/types-and-protocols.md`: "Single row per station. Values are
     `Float64`"). One-row-per-gauge, numeric-only, and the boolean ban are **SAP3
     facts**, not modeller impositions — `04-…` §6 **states and keeps** them.
   - **The Parquet carries exactly one identity column, `gauge_id`.** `network`,
     `station_code` and `basin_code` live in `basins.gpkg` (which also carries
     `gauge_id`); SAP3 joins on `gauge_id`. This makes the Parquet byte-for-byte the
     shape the modeller's producers already emit — no reshaping asked of the
     extractor, and no second place for the identity columns to drift.
   - **Categoricals are already numeric.** The majority-class attributes (climate
     zone, land cover, lithology) resolve to a `float` class code in the modeller's
     own extractor, so "no string statics in v1" is satisfied, not violated.
   - **Only the feature LIST stays the modeller's** — which attributes, their units,
     and which a given model requires. `04-…` §6's generic matching rule already
     handles an unknown list: every name in `InputRequirement.static` must appear as
     a non-null column for the assigned stations.
   - **FI note (no escalation).** FI permits string statics
     (`StationInputs.static: dict[str, int | float | str]`); SAP3 narrows to
     `Float64`. Narrowing is allowed. A future string static is a **SAP3** widening,
     not an FI gap.
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
   - **This costs no flexibility — it mirrors a constraint SAP3 already has.**
     `station_weather_sources` is keyed `(station_id, nwp_source)` with
     `extraction_type` as a column (`src/sapphire_flow/db/metadata.py:186`), so one
     station already gets **exactly one extraction type per weather source**. Mixing
     basins and bands in one `.gpkg` would not buy simultaneous basin+band forcing —
     that PK blocks it first, regardless of how polygons are packed.
   - **Retained flexibility:** a station may be basin-average from one source and
     elevation-band from another; bands may be added, or a station migrated from
     basins to bands, by registering a second HRU and flipping `extraction_type`.
     Single-kind HRUs make that *easier* (independent lifecycle, no refetch of the
     other kind).
   - **What would be an architecture change** (explicitly out of scope): letting one
     station consume basin-average **and** elevation-band forcing from the **same**
     source simultaneously. That needs the `station_weather_sources` PK widened to
     include `extraction_type`, `GridExtractor` to return a collection rather than a
     `BasinAverageForecast | ElevationBandForecast` union
     (`src/sapphire_flow/protocols/grid_extractor.py:24`), and
     `ModelDataRequirements.spatial_input_type` to become a set rather than a scalar
     (`src/sapphire_flow/types/model.py:270`). Nothing in Nepal v1 requires it —
     Plan 106 §0 says Nepal forcing arrives as basin/band time-series from the
     Gateway with no SAP3-side extraction.
5. **Static attributes live in the DB, not as a file pointer.** SAP3 imports the
   Parquet into `basins.attributes` JSONB; the package is the *interchange* artifact,
   not the operational store. Storing only a path would force `ModelDataRequirements`
   validation and model-onboarding compatibility checks to open a Parquet in a
   directory layout the modeller owns and rewrites in place, add a filesystem
   dependency to the forecast cycle, and lose per-basin provenance. Keep the
   `package_id` + checksum as provenance.
6. **The static package is self-contained — no SAP3/Gateway forcing dependency.**
   *(Owner, 2026-07-14 — this REVERSES the ordering dependency drafted earlier today.)*

   The Caravan climate indices (`p_mean`, PET mean, aridity, snow fraction, moisture
   index, seasonality, high/low precipitation frequency and duration) are indeed
   **forcing-derived rather than geometry-derived** — but the extraction implementer
   has **the full global ERA5 archive on S3** and computes them from it directly.
   They do **not** need SAP3's forcing, the Data Gateway, or a back-extraction step.

   Consequences:
   - **The package is complete in one delivery.** No Group-A/Group-B staging, no
     two-package path, no `null` climate indices awaiting a later forcing run.
   - **No Flow 0 ordering constraint.** Delineation and static extraction are one
     step, independent of Gateway registration and historical back-extraction.
   - **PET is not a SAP3 concern.** It comes from the extractor's own ERA5 archive,
     not from the deployment's forcing source, so the "no PET ⇒ permanently null
     aridity" risk drafted earlier does not apply.
   - The indices are a **climatology descriptor**, computed per Caravan's definitions
     over Caravan's fixed 1981-01-01 … 2020-12-31 window. They are deliberately
     *not* tied to whatever forcing SAP3 later runs operationally — that is what
     makes them comparable across Caravan datasets.

   One thing to confirm with the implementer (Open question 2): the modeller's column
   names encode **ERA5-Land** (`pet_mean_ERA5_LAND`, `aridity_ERA5_LAND`,
   `moisture_index_ERA5_LAND`), and Caravan's published values are ERA5-Land. If the
   S3 archive is plain **ERA5** (0.25°) rather than **ERA5-Land** (0.1°), the values
   will not reproduce Caravan and the column names will be misleading. This is a
   labelling/provenance question, not a blocker — `feature_catalog.json` records
   `source_dataset` per column either way.

## Resolved questions (owner, 2026-07-14)

1. **Does the letter/underscore rule bind the layer name or the feature `name`?**
   **Both.** It is a confirmed normative MUST on each. See § Terminology.
2. **Is one Gateway HRU strictly one GeoPackage?** **Yes** — in the Gateway's sense
   an HRU *is* a GeoPackage containing ≥1 polygon, and the polygons inside are the
   HRUs proper. The two uniqueness scopes therefore coincide.
3. **Are gauge IDs numeric / leading-zero?** IDs are **strings**; typically no
   leading zeros, but they **may be all-digits**, and for some hydromets contain
   `/`, `-`, `'`, `_`, and letters.
4. **Can the extractor produce the forcing-derived climate indices without SAP3's
   forcing?** **Yes.** The implementer holds the **full global ERA5 archive on S3**
   and computes the Caravan indices from it directly. No Gateway round-trip, no
   back-extraction step, no staged package. See Owner decision 6 — this reverses the
   ordering dependency drafted earlier the same day.
5. **Does the deployment's forcing source need PET?** **Not for these attributes.**
   PET comes from the extractor's own ERA5 archive.

These answers are now baked into Owner decisions 2, 3 and 6, and into Task 1A. They
do not reopen any decision.

## Open questions (tracked, non-blocking)

1. Which actor owns the durable regeneration path: DHM, HSOL, or the basin/static
   extraction tool maintainer?
2. **ERA5 or ERA5-Land?** The modeller's column names encode ERA5-**Land**
   (`pet_mean_ERA5_LAND`, `aridity_ERA5_LAND`, `moisture_index_ERA5_LAND`), and
   Caravan's published values are ERA5-Land (0.1°). If the S3 archive is plain ERA5
   (0.25°), the values will not reproduce Caravan and the column names mislead. A
   labelling/provenance question — `feature_catalog.json` records `source_dataset`
   per column either way, so it does not block.
3. Which string does SAP3 pass as the FI station key — the raw `station_code`, or
   the modeller's region-prefixed `gauge_id` (`nepal_5501`)? It MUST match whatever
   the trained artifact's station embeddings were fitted on. Resolved via the
   existing `station_code_resolver` seam
   (`src/sapphire_flow/adapters/forecast_interface.py:170`) — a wiring decision at
   model onboarding, not an FI change.

**The static Parquet layout is no longer open** — the model developer supplied their
producers on 2026-07-14 and the shape is confirmed (see Owner decision 3).

None of these block READY.

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
5. State the **confirmed** static shape in §6 (Owner decision 3): wide, one row per
   gauge, `gauge_id` as the sole identity column, every attribute `Float64`; keep the
   numeric-only and no-boolean constraints. Only the feature *list* stays the
   modeller's. **DONE 2026-07-14.**
6. Note the import target: static attributes land in `basins.attributes` JSONB and
   are surfaced to models as `Float64` (`docs/spec/types-and-protocols.md`
   `static` slot). Integer catalog columns are cast on import. **DONE 2026-07-14.**
7. Record the **three-identifier mapping** (`station_code` / modeller `gauge_id`,
   region-prefixed / Gateway feature `name`) and require `gauge_id` as a column so
   SAP3 need not re-derive the prefix rule. **DONE 2026-07-14.**
8. Add to §4 the columns the modeller's extractor **hard-requires** of the basin
   GeoPackage — `gauge_id`, `latitude`, `longitude` — which `read_basin_polygons()`
   raises on if absent. **DONE 2026-07-14.**
9. Add §6.3: the **geometry-derived vs forcing-derived** split, recording that the
   forcing-derived Caravan indices come from the **extractor's own global ERA5
   archive (S3)** — so the package is **self-contained**: no Gateway round-trip, no
   back-extraction step, no staged/two-package path, no PET dependency on the
   deployment's forcing source (Owner decision 6). **DONE 2026-07-14** — but note the
   first draft of §6.3 asserted the opposite (an ordering dependency); it MUST be
   rewritten, not merely extended.

**Already executed (working-tree draft, 2026-07-14).** At the owner's direction,
items 5–9 above were applied to the `04-…` draft ahead of READY, together with the
collaborator brief `docs/requirements/basin-static-extraction-brief.md`. Items 1–4
(terminology section, name-rule normativity, uniqueness, single-kind HRUs, collision
policy) are **still outstanding** and remain this task's work.

**Scope out:** Do not invent the static feature *list* — that is the modeller's. Do
not add importer implementation detail. Do not touch
`docs/spec/types-and-protocols.md`.

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
    # Owner decision 2 — the naming convention and its collision policy.
    "g_<station_code_normalized>",
    "colliding station codes",
    # Owner decision 3 — the CONFIRMED static shape (NOT TBD). Keep the numeric
    # constraints: they are SAP3 facts, satisfied by the modeller's own producers.
    "one row per gauge",
    "Float64",
    "`basins.attributes`",
    "static features MUST be numeric",
    "boolean",
    # Owner decision 6 — the package is SELF-CONTAINED: the forcing-derived indices
    # come from the extractor's own global ERA5 archive, not from SAP3/the Gateway.
    "forcing-derived",
    "ERA5",
    "self-contained",
    # Identity: the modeller's key must be carried, not re-derived.
    "gauge_id",
]
must_be_gone = [
    "MUST be lowercase, unique within the Gateway HRU, and MUST NOT start with a digit",
    # Owner decision 4 — HRUs are single-kind, so there is no basin/band merge.
    "SAP3 MAY combine basin and band features",
    # Owner decision 6 REVERSED the ordering dependency that the first §6.3 draft
    # asserted. These phrasings must not survive.
    "ORDERING MATTERS",
    "PET is a hard dependency",
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

Also reconcile the **mixed-GeoPackage language** that Owner decision 4 contradicts.
Three places currently assume one `.gpkg` may hold basins *and* bands:

| Line | Current text |
|---|---|
| `01:30` | "a gpkg may hold several catchments and/or elevation-band polygons" |
| `01:80` (G5) | "A submitted gpkg MAY contain several polygons (multiple catchments **and/or** band polygons)" |
| `01:104` (G9) | "Spatial granularity MUST be basin-average **and**, where bands are defined, per elevation band" |

**Do not retract the Gateway's capability.** G5 is a requirement *on the Gateway*,
AGREED 2026-06-18, and the owner confirms the Gateway does not care whether kinds
are mixed. Renegotiating a capability they have already built buys nothing. Instead
**add SAP3's narrowing convention alongside it**: SAP3 submits **single-kind**
GeoPackages only (basins or bands, never both — Owner decision 4), so a mixed `.gpkg`
never actually reaches the Gateway. Reword `01:30` and G9 so they describe per-HRU
granularity rather than implying both kinds coexist in one file.

**Scope out:** Do not reopen the Gateway API design. Do not add a programmatic
geometry-upload requirement. Do not weaken or remove G5's Gateway-side permission.

**Verification:**

```bash
uv run python - << 'EOF'
from pathlib import Path

text = Path("docs/requirements/01-data-gateway-requirements.md").read_text()
required = [
    "internal GeoPackage layer/table name",
    "one Gateway HRU = one GeoPackage",
    "04-basin-static-artifact-contract.md",
    # Owner decision 4 — SAP3's narrowing convention on top of G5.
    "SAP3 submits single-kind GeoPackages",
]
missing = [t for t in required if t not in text]
if missing:
    raise SystemExit(f"1B: missing required text: {missing}")

# The mixed-kind phrasings that contradict Owner decision 4 must be reworded.
mixed = [
    "a gpkg may hold several catchments and/or elevation-band polygons",
    "(multiple catchments and/or",  # G5 — line-wrapped in the source, match the head
]
lingering = [m for m in mixed if m in text]
if lingering:
    raise SystemExit(f"1B: mixed basin/band GeoPackage wording survives: {lingering}")

# ...but G5's Gateway-side capability must NOT be retracted.
if "G5." not in text:
    raise SystemExit("1B: G5 was removed — the Gateway capability must stay")

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

## Review History

All rounds 2026-07-14. Reviewers: **C** = Claude design/architecture perspective,
**X** = Codex repo-grounded perspective (`file:line` evidence required). No model
approved its own output; every revision was reviewed by a model that did not author
it.

| Round | Reviewer | Outcome | Blocking | Status | Key findings |
|---|---|---|---|---|---|
| 1 | C + X | NEEDS_CHANGES | 3 | resolved | DRAFT plan already partially executed; Phase 3 inverted the readiness gate (review ran *after* the work it authorized); `04-…` finalized the static Parquet schema that Owner decision 3 said must stay open. Plus: 4 of 8 gates passed with zero work done; `00-internal-gap-analysis.md` contradicted the boundary and was in no task; the "gauge ID as feature `name`" preference had an empty satisfying branch. |
| 2 | X | ESCALATE | 1 | resolved | The round-1 fix itself introduced a blocker: the Baseline section directed committing the known-defective `04-…` contract to `main` before repairing it. |
| 3 | X | NEEDS_CHANGES | 0 | resolved | Blockers closed. Gates for 1A/1B/2A/2B/2C under-verified their own scope; right-sizing cut requested. |
| 4 | X | APPROVE | 0 | superseded | Converged. Superseded by the owner's answers to the open questions, which changed plan content. |
| 5 | X | NEEDS_CHANGES | 1 | resolved | Owner decision 4 (single-kind HRUs) verified SOUND against the repo. But three sites in `01-data-gateway-requirements.md` still assumed mixed basin/band GeoPackages, contradicting it, and were in no task. |
| 6 | X | APPROVE | 0 | superseded | Converged. Superseded by the model developer's producers landing, which changed Owner decisions 3 and 6. |
| 7 | X | NEEDS_CHANGES | 2 | resolved | `04-…` demanded four Parquet identity columns the modeller's producers do not emit — contradicting the plan's own claim that the shape matched them byte-for-byte. Leftover "roll back to TBD" prose. |
| 8 | X | APPROVE | 0 | user-confirmed | Both findings closed. FI claim independently verified TRUE (`gauge_id` is consistent with the ForecastInterface — no escalation). All 7 gates verified to fail against the current tree. Collaborator brief judged sendable. Owner promoted to READY. |

**FI adherence check (round 8, verified).** `gauge_id` does **not** violate the
ForecastInterface. The FI station key is an opaque string
(`forecast_interface/input/bundle.py`, `ModelInputs.stations: dict[str,
StationInputs]`) and SAP3 already owns the mapping seam (`station_code_resolver`,
`src/sapphire_flow/adapters/forecast_interface.py:170`). FI permits string statics
(`StationInputs.static: dict[str, int | float | str]`) while SAP3 narrows to
`Float64` — narrowing is allowed. A future string static would therefore be a **SAP3**
widening, not an FI gap, so no FI-repo issue is required (`CLAUDE.md`
§ ForecastInterface Adherence).

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
