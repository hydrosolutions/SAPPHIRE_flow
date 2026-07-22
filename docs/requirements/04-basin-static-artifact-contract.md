# Basin + Static Attribute Artifact Contract

> **Status: DRAFT (2026-07-14)** - proposed SAP3-side data package contract for
> basin geometry and static catchment attributes.
>
> **Audience:** HSOL, DHM, model developers, and any upstream basin/static extraction
> tool maintainer.
>
> **Scope:** Nepal v1 and any future deployment where basin outlines and static
> catchment attributes are produced outside SAP3. Swiss v0 continues to use
> CAMELS-CH as its current reference-data source.
>
> Requirement keywords **MUST**, **SHOULD**, and **MAY** are used in the RFC-2119
> sense.

## 1. Purpose

SAP3 needs basin geometry and static catchment attributes before it can onboard
forecast stations, register Gateway HRUs, prepare model inputs, train models, and
run hindcasts. This document defines the file-based artifact package that an
external basin/static extraction process must produce for SAP3 to ingest.

For deployments that use this package, the package is the Flow 5.2 source for
basin geometry and static catchment attributes. SAP3 remains responsible for
validating and importing the package. The upstream extraction work MAY happen
inside SAP3, inside a separate HSOL/DHM tool, or through a maintained service.

The contract deliberately separates responsibilities:

- **ForecastInterface (FI)** declares what static scalar inputs a model needs, via
  `InputRequirement.static`.
- **SAP3** validates and stores basin geometry and static attributes, then supplies
  the FI-declared static values to the model.
- **The extraction tool** produces a reproducible package matching this contract.

This contract is a data-artifact boundary. It does **not** require SAP3 to import,
call, vendor, or own the extraction tool's source code.

## 2. Package layout

A basin/static package is a directory or archive with this structure:

```text
basin_static_package/
  manifest.json
  basins.gpkg
  static_attributes.parquet
  feature_catalog.json
  validation_report.json
  README.md
```

Optional files:

```text
  bands.gpkg
  official_geometry_comparison.parquet
  checksums.sha256
```

`manifest.json`, `basins.gpkg`, `static_attributes.parquet`,
`feature_catalog.json`, and `validation_report.json` are mandatory.

## 3. `manifest.json`

The manifest identifies the package, the producing tool, the source datasets, and
the files SAP3 must ingest.

Required top-level fields:

| Field | Type | Requirement |
|---|---|---|
| `contract_version` | string | MUST be `basin-static-artifact/v1` for this contract. |
| `package_id` | string | MUST be stable and human-readable within the deployment. |
| `created_at` | string | MUST be an ISO-8601 UTC timestamp. |
| `network` | string | MUST match the SAP3 station network, for example `dhm`. |
| `crs` | string | MUST be `EPSG:4326`. |
| `extractor` | object | MUST identify the producing tool and version. |
| `source_datasets` | array | MUST list every dataset used for delineation or attributes. |
| `gateway_hru_names` | array | MUST list the Gateway HRU/GPKG names referenced by basin or band rows. |
| `climatology_window` | object | MUST be present when any forcing-derived climate index is included (§6.3). Fixed window applied to **every** basin. Shape: `{"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"}`. Default `{"start": "1991-01-01", "end": "2020-12-31"}`. |
| `files` | object | MUST map logical names to relative file paths. |
| `checksums` | object | SHOULD contain SHA-256 checksums for every package file. |

Example:

```json
{
  "contract_version": "basin-static-artifact/v1",
  "package_id": "nepal-dhm-basins-2026-07-14",
  "created_at": "2026-07-14T10:30:00Z",
  "network": "dhm",
  "crs": "EPSG:4326",
  "extractor": {
    "name": "global-basin-extractor",
    "version": "1.4.2",
    "git_commit": "abc1234"
  },
  "gateway_hru_names": ["nepal_dhm_v1"],
  "climatology_window": { "start": "1991-01-01", "end": "2020-12-31" },
  "source_datasets": [
    {
      "name": "MERIT Hydro",
      "version": "2023-xx",
      "purpose": "basin delineation"
    },
    {
      "name": "HydroATLAS",
      "version": "1.0",
      "purpose": "static attributes"
    },
    {
      "name": "MERIT DEM",
      "version": "2023-xx",
      "purpose": "static attributes"
    },
    {
      "name": "ERA5-Land",
      "version": "2024-xx",
      "purpose": "forcing-derived climate indices"
    }
  ],
  "files": {
    "basins": "basins.gpkg",
    "static_attributes": "static_attributes.parquet",
    "feature_catalog": "feature_catalog.json",
    "validation_report": "validation_report.json"
  },
  "checksums": {
    "basins.gpkg": "sha256:...",
    "static_attributes.parquet": "sha256:..."
  }
}
```

## 3a. Terminology — read this before §4

Four different things in this system are called some variant of "name". Conflating
them is the most likely way this contract gets implemented wrongly, so they are
defined here once and used consistently throughout.

| Term | What it is |
|---|---|
| **Gateway HRU** | **the GeoPackage file itself** — a `.gpkg` holding one or more polygons. **One Gateway HRU = one GeoPackage.** Named in `manifest.gateway_hru_names` and in the `gateway_hru_name` column. |
| **the HRUs proper** | **the polygons inside** that GeoPackage — the actual hydrological response units. One Gateway HRU holds one or several of them. |
| **internal layer/table name** | the table *inside* the GeoPackage (`polygons` is recommended). A GDAL/OGR concept. |
| **feature `name`** | a **per-polygon attribute**. This is the key the Gateway echoes back as the column header in forcing payloads. |

Two rules follow, and both are **normative**:

1. **The "must start with a letter or underscore" rule binds BOTH the internal
   layer/table name AND the per-feature `name` values.** This is confirmed, not
   assumed. It is why `polygons` is an acceptable layer name and `00003` is not, and
   why a bare all-digit gauge ID is **not** a legal feature `name` (see §4).
2. **One Gateway HRU is one GeoPackage**, so "unique within the Gateway HRU" and
   "unique across all features in the GeoPackage" are the *same* statement. This
   contract uses the GeoPackage form. Every row in a given `.gpkg` MUST carry the
   same `gateway_hru_name`.

**A Gateway HRU is single-kind.** It MUST hold
**basin polygons or band polygons, never both**.
The Gateway itself does not care, but SAP3 requires it: it makes
`spatial_type` a property of the HRU rather than of each polygon, mirroring SAP3's
existing constraint of one extraction type per station per weather source. It also
means basin and band feature names can never collide, and that bands can be
regenerated without touching basins.

## 4. `basins.gpkg`

`basins.gpkg` contains one feature per SAP3 forecast basin. The file MUST be a
GeoPackage in CRS `EPSG:4326`. Geometries MUST be two-dimensional, OGC-valid
`Polygon` or `MultiPolygon` geometries. SAP3 stores them as 2-D `MultiPolygon`.

The internal layer/table name MUST start with a letter or underscore; `polygons` is
recommended.

Required columns:

| Column | Type | Requirement |
|---|---|---|
| `network` | string | MUST match `manifest.network`. |
| `station_code` | string | MUST match the deployment station code used by SAP3 and FI station-code mapping. |
| `basin_code` | string | MUST be unique within `network`. |
| `gateway_hru_name` | string | MUST reference a name in `manifest.gateway_hru_names`. |
| `name` | string | Gateway-echoed polygon key. MUST be lowercase, **unique across all features in the GeoPackage**, and **MUST NOT start with a digit** (§3a rule 1). See the naming convention below. |
| `display_name` | string | Human-readable basin/station display name. |
| `area_km2` | float | MUST be positive. |
| `outlet_lon` | float | Outlet longitude in EPSG:4326. |
| `outlet_lat` | float | Outlet latitude in EPSG:4326. |
| `delineation_method` | string | Tool- or dataset-specific method label. |
| `geometry` | geometry | 2-D valid `Polygon` or `MultiPolygon`, EPSG:4326. |

**Columns required by the model developer's extraction toolchain.** The HydroATLAS
extractor reads the basin GeoPackage directly and **hard-fails** if these are absent,
so `basins.gpkg` MUST carry them alongside the SAP3/Gateway columns above. They are
duplicates of values already present under SAP3 names — carry both; a GeoPackage may
hold extra columns freely.

| Column | Type | Requirement |
|---|---|---|
| `gauge_id` | string | The model developer's station key, region-prefixed (e.g. `nepal_5501`). |
| `latitude` | float | Gauge latitude. Same value as `outlet_lat`. |
| `longitude` | float | Gauge longitude. Same value as `outlet_lon`. |

Recommended columns:

| Column | Type | Purpose |
|---|---|---|
| `regional_basin` | string or null | Display grouping label such as `Karnali` or `Gandaki`. |
| `outlet_snap_distance_m` | float or null | Distance between supplied gauge coordinate and snapped river-network outlet. |
| `official_geometry_id` | string or null | Identifier of compared official/DHM geometry, if any. |
| `geometry_source` | string | For example `dhm_official`, `global_extractor`, or `manual_reviewed`. |
| `review_status` | string | For example `auto_passed`, `manual_review_required`, or `manual_approved`. |

`gateway_hru_name` is the Gateway-side GPKG/HRU identifier SAP3 uses when fetching
forcing. The GeoPackage `name` column is the polygon key SAP3 expects the SAPPHIRE
Data Gateway to echo in forcing payloads. `display_name` is the value SAP3 may use
for `Basin.name` or user-facing station/basin display.

### 4a. Feature-name convention — unconditional

Every basin feature `name` MUST be:

```text
g_<station_code_normalized>
```

where normalization lowercases the code and replaces runs of non-alphanumeric
characters with a single underscore.

**This is a single unconditional rule, not a preference.** The raw gauge ID is *not*
used as the feature `name`, because:

- gauge IDs are **strings that may be composed entirely of digits** (`5501`), and a
  `name` starting with a digit is rejected (§3a rule 1);
- a per-station conditional ("use the raw ID when it happens to be legal") would
  produce a non-uniform key space for no benefit.

The raw gauge ID is preserved verbatim, **string-typed**, in `station_code`. Keeping
it a string is load-bearing: an integer-typed column silently turns `0439` into
`439`.

**Collision policy.** Normalization is **not injective** — gauge IDs may contain `/`,
`-`, `'`, `_` and letters, so `KAR/01` and `KAR-01` both normalize to `kar_01`. A
station literally coded `g_5501` would also collide with the derived name for station
`5501`. A collision is a **package validation failure**: the producer MUST reject the
package with an explicit error naming **both colliding station codes**. It MUST NOT
be resolved silently — no suffixing, no truncation, no renaming. This is the
`ids_unique` check in §8.

## 5. `bands.gpkg` and elevation-band geometry

`bands.gpkg` is optional. It is required only when the deployment or model needs
elevation-band forcing.

If present, it MUST follow the same CRS and geometry rules as `basins.gpkg`.
Required columns:

| Column | Type | Requirement |
|---|---|---|
| `network` | string | MUST match `manifest.network`. |
| `basin_code` | string | MUST reference a basin in `basins.gpkg`. |
| `station_code` | string | MUST match the parent basin's station code. |
| `band_id` | integer | MUST be unique within `network + basin_code`. |
| `gateway_hru_name` | string | MUST reference a name in `manifest.gateway_hru_names`. |
| `name` | string | Gateway-echoed polygon key. MUST be lowercase, **unique across all features in the GeoPackage**, and **MUST NOT start with a digit** — identical rules to the basin `name` (§3a rule 1, §4a). |
| `display_name` | string | Human-readable band display name. |
| `min_elevation_m` | float | Lower bound of the band. |
| `max_elevation_m` | float | Upper bound of the band and MUST be greater than `min_elevation_m`. |
| `area_km2` | float | MUST be positive. |
| `geometry` | geometry | 2-D valid `Polygon` or `MultiPolygon`, EPSG:4326. |

Band feature naming — unconditional, same rules as §4a:

```text
g_<station_code_normalized>_band_<band_id>
```

Band polygons for a basin SHOULD be non-overlapping and SHOULD cover the parent
basin within a documented tolerance. Any gap or overlap MUST be reported in
`validation_report.json`.

**Basins and bands are never merged into one GeoPackage.** A Gateway HRU is
single-kind (§3a), so `bands.gpkg` is uploaded as its own HRU, distinct from the
basin HRU. `spatial_type` is therefore a property of the HRU: every forcing column
returned for a band HRU is an elevation-band series carrying a `band_id`, and every
column from a basin HRU is a basin-average series. This also means basin and band
feature names cannot collide, and bands can be regenerated without re-registering or
re-extracting basins.

## 5a. Gateway polygon-reference persistence

SAP3 MUST persist enough metadata to map Gateway forcing columns back to SAP3
stations and bands:

```text
station_id
basin_id
gateway_hru_name
name
spatial_type
band_id
```

**RESOLVED — persistence target (Plan 082 Task 2D schema + Plan 120 Task
0A/2B store layer).** The persistence target is an additive table keyed by
`station_id + gateway_hru_name + name` — `recap_gateway_polygon_bindings`
(`db/metadata.py:310-345`; base six columns owned by Plan 082, `package_id`/
`imported_at` provenance columns owned by Plan 120 Task 0A). The reader/writer
is `RecapGatewayPolygonStore` (`store/recap_gateway_polygon_store.py`);
`GatewayPolygonBindingRow`/`store_binding` carry and write the provenance
columns, with a DELETE-then-INSERT replace path for `basin_average` rows so a
correction (HRU/name rename) never violates
`uq_recap_gateway_polygon_bindings_one_basin_average_per_station`. **Still
open:** the package loader/dissolve/importer that actually DRIVES
`store_binding` from an accepted basin/static package (Plan 120 Phase 1 —
Task 1A/1B — and Task 2A/2C) is not yet built; until it lands, the store
layer above has no production caller and 082's resolver keeps returning
`None` for every station (see Plan 120 "Production-gate note").

## 6. `static_attributes.parquet`

### 6.1 Shape — CONFIRMED, not negotiable

The **shape** of this file is fixed by SAP3 and by the model developer's existing
producers. It is not a per-deployment or per-modeller choice.

`static_attributes.parquet` is a **wide table, one row per gauge**:

- exactly **one row per basin/station**;
- **one column per attribute**;
- the identity key is a **string**;
- **every attribute column is `Float64`**.

This matches SAP3's static-input contract (`docs/spec/types-and-protocols.md`, the
`static` slot: "Single row per station. Values are `Float64`. Sourced from
`basins.attributes` JSONB") and the model developer's producers, which write exactly
this schema:

```python
schema = {"gauge_id": pl.Utf8, **{name: pl.Float64 for name in source_names}}
```

Long/multi-index layouts, one-row-per-(gauge, attribute), and non-`Float64` value
columns are **rejected**.

**Categorical attributes are encoded as numeric class codes, not strings.** The
model developer's majority-class attributes (climate zone, land-cover class,
lithology class) already resolve to a `float` class code. Accordingly:

- static features MUST be numeric (`int` or `float`) for `basin-static-artifact/v1`;
- **boolean** features MUST NOT be used — encode binary features as documented 0/1;
- **string-valued** static attributes are out of scope for v1.

> **Note on the FI boundary.** The ForecastInterface *permits* string statics
> (`StationInputs.static: dict[str, int | float | str]`). SAP3 is deliberately
> **narrower** (`Float64` only), which is allowed. If a model ever needs a genuine
> string static, that is a **SAP3** widening (static slot + `basins.attributes`
> handling), not an FI gap — raise it against SAP3, not against the FI repo.

**Nulls are legitimate.** An attribute that cannot be computed for a basin MUST be
`null`, never `0` and never a sentinel. See §6.3 — the forcing-derived indices are
routinely null where the required forcing does not exist.

### 6.2 Identity columns and the three-identifier mapping

One station carries **three** identifiers in this system. They are not
interchangeable, and conflating them is the most likely source of a silent
wrong-station join:

| Identifier | Owner | Example | Where it appears |
|---|---|---|---|
| `station_code` | SAP3 | `5501` | SAP3 station definition; `basins.gpkg` only — **not** in the Parquet |
| `gauge_id` | model developer | `nepal_5501` | **both** package files: `basins.gpkg` and `static_attributes.parquet` |
| Gateway feature `name` | Gateway | `g_5501` | `basins.gpkg` `name` column; echoed in forcing columns |

**The Parquet carries exactly ONE identity column.**

| Column | Type | Requirement |
|---|---|---|
| `gauge_id` | string | The model developer's station key, **region-prefixed** (e.g. `nepal_5501`). The sole join key. |

Everything else — `network`, `station_code`, `basin_code`, the Gateway feature
`name` — is carried in `basins.gpkg` (§4), which MUST also carry `gauge_id`. SAP3
joins the two files on `gauge_id`.

> **Invariant — `gauge_id` MUST be byte-identical in `basins.gpkg` and
> `static_attributes.parquet`, and MUST carry the region prefix in both.**
>
> This is a silent-failure risk, not a cosmetic one: the join is a left join, so a
> mismatch produces **all-null static attributes** without raising.
>
> The prefixed form is required (rather than left to the producer's choice) because
> the model developer's extractor is understood to read `gauge_id` from the basin
> GeoPackage and then apply the region prefix when writing the Parquet — an
> *unprefixed* GeoPackage therefore yields a *prefixed* Parquet, and the two files
> stop sharing a key. Writing the already-prefixed form into the GeoPackage makes
> that prefixing a no-op and keeps both files aligned. *(To be confirmed with the
> extraction implementer — see `basin-static-extraction-brief.md` §5.)*
>
> SAP3 import MUST fail loudly when any `gauge_id` in one file has no counterpart in
> the other. It MUST NOT silently import a partial join.

This is deliberate: it makes the Parquet **byte-for-byte the shape the model
developer's producers already emit** (`gauge_id` + `Float64` attribute columns), so
no extra join or reshaping step is imposed on the extractor. Duplicating the identity
columns into the Parquet would add work and create a second place for them to drift
out of sync with the GeoPackage.

`gauge_id` is **not** an FI contract element — the FI station key is an opaque
string (`ModelInputs.stations: dict[str, StationInputs]`), and SAP3 maps
`StationId → str` through its `station_code_resolver`. Carrying `gauge_id` in both
package files makes that mapping data, not a convention someone has to re-derive.

Every additional column is a static catchment attribute. Attribute names MUST match
`feature_catalog.json` entries exactly.

For any model intended to run on the package, every name declared in
`InputRequirement.static` / SAP3 `ModelDataRequirements.static_features` MUST appear
as a column in this file and MUST have a non-null value for every basin assigned to
that model, unless the model-specific onboarding plan explicitly allows missing
values.

### 6.2a What SAP3 stores — the Parquet is interchange, not the store

The Parquet is an **import artifact**. SAP3 does not keep it as the operational
source. On import, SAP3 dissolves it into existing storage:

| What | Where it lands | Form |
|---|---|---|
| Each basin's attribute **values** (every `Float64` column, including the climate indices) | `basins.attributes` **JSONB** (one dict per basin) | `{attribute_name: value}` |
| Basin **geometry** (from `basins.gpkg`) | `basins.geometry` | 2-D `MultiPolygon`, EPSG:4326 |
| `area_km2`, `regional_basin` | existing `basins` columns | scalar |
| Band geometries (from `bands.gpkg`, if present) | `basins.band_geometries` JSONB | list |

What SAP3 does **not** store:

- **The Parquet or GeoPackage files themselves** — they are consumed and discarded
  (checksums and `package_id` are retained as provenance; see §11).
- **The daily ERA5-Land forcing series** behind the climate indices — only the reduced
  scalar indices are kept. SAP3 never sees that series; the extractor computed it.
- **`gauge_id` as an operational key** — it is used only to join the two package files
  at import time; SAP3's operational identity is `station_code`.

Two consequences worth stating:

- **The full attribute set is stored, not just what current models use.** Models
  filter `basins.attributes` down to their declared `static_features` at read time
  (see the `static` slot in `docs/spec/types-and-protocols.md`), so an attribute no
  model needs yet is stored, cost-free, against a future model that does.
- **Per-attribute provenance (which `package_id`, which climatology window) has no
  first-class field yet.** `basins.attributes` is a flat value dict — it holds the
  values but not their origin. This is a **distinct** gap from the Gateway
  polygon-reference persistence gap in §5a (that one maps forcing columns back to
  stations/bands; this one records which package produced each stored attribute). §11
  already states the provenance questions SAP3 SHOULD be able to answer; giving them a
  first-class home is left to the implementing plan.

### 6.3 Geometry-derived vs forcing-derived attributes — the package is self-contained

The attribute set splits into two groups by **input**, but **both are produced by the
extractor alone**. The package does **not** depend on SAP3, on the SAPPHIRE Data
Gateway, or on any historical back-extraction step.

| Group | Input | Source |
|---|---|---|
| **A — geometry-derived** | basin polygon + HydroATLAS / DEM / global rasters | the extractor's global raster archive |
| **B — forcing-derived** (Caravan-style climate indices) | catchment-averaged daily precipitation, mean temperature, and PET, over a fixed ~30-year window | **the extractor's own global ERA5-Land archive (S3)** |

Group B is the Caravan-style climate index set — mean daily precipitation, mean PET,
aridity, snow fraction, moisture index, seasonality, and the high/low precipitation
frequency and duration indices — computed per Caravan's *definitions*.

**Climatology window — a fixed, documented ~30-year window, NOT the Caravan window.**
The primary use case for this package is the **v1 DHM deployment, where there is no
Caravan dataset to reproduce**, so comparability with published Caravan is not the
objective. Instead:

- The extractor MUST compute Group B over a **single fixed ~30-year window applied to
  every basin and every region**, so the indices are internally comparable across the
  whole deployment.
- **Default window: `1991-01-01 … 2020-12-31`** — the current WMO 30-year
  climatological normal period, fully covered by ERA5-Land. The deployment owner MAY
  set a different documented window, but it MUST be the same for all basins.
- The chosen window MUST be recorded in `manifest.json` and per-column in
  `feature_catalog.json`. Do **not** silently vary the window per basin or per region
  (e.g. by defaulting to each basin's own record length) — that makes the indices
  incomparable.

Consequences:

- **One delivery, complete package.** No staged Group-A-then-Group-B path, no
  `null` climate indices awaiting a later forcing run.
- **No ordering constraint on deployment onboarding.** Delineation and static
  extraction are independent of Gateway registration and of historical forcing
  back-extraction. They may run in any order.
- **PET is not a deployment concern.** It comes from the extractor's ERA5-Land
  archive, not from the forcing source SAP3 later runs operationally.

These indices are a **climatology descriptor**, deliberately decoupled from whatever
forcing SAP3 uses operationally — do not "fix" a divergence between an index and the
deployment's operational forcing.

The Group-B source is **ERA5-Land** (confirmed 2026-07-14). `feature_catalog.json`
MUST record `source_dataset` and the climatology window per column, so the provenance
survives a future product or window change.

## 7. `feature_catalog.json`

The feature catalog defines the meaning, unit, and provenance of every static
attribute column.

Required fields per feature:

| Field | Type | Requirement |
|---|---|---|
| `name` | string | MUST match a column in `static_attributes.parquet`. |
| `type` | string | MUST be `float` or `integer`. |
| `unit` | string or null | MUST be present; use `null` only for unitless numeric values. |
| `source_dataset` | string | MUST reference a dataset in `manifest.source_datasets`. |
| `aggregation` | string | MUST describe how the basin value was derived. |
| `description` | string | Human-readable meaning. |
| `climatology_window` | object or null | For a **forcing-derived** index (§6.3), the fixed window it was computed over — same shape as `manifest.climatology_window`. `null` for geometry-derived attributes. When present it MUST equal `manifest.climatology_window`. |
| `required_by_models` | array | SHOULD list known model IDs that require this feature. |

Example:

```json
{
  "features": [
    {
      "name": "mean_elev_m",
      "type": "float",
      "unit": "m",
      "source_dataset": "MERIT DEM",
      "aggregation": "area_weighted_mean",
      "description": "Area-weighted mean elevation inside the basin polygon.",
      "climatology_window": null,
      "required_by_models": ["nepal_lstm_v1"]
    },
    {
      "name": "p_mean",
      "type": "float",
      "unit": "mm/day",
      "source_dataset": "ERA5-Land",
      "aggregation": "catchment_mean_daily_over_window",
      "description": "Mean daily precipitation (Caravan definition).",
      "climatology_window": { "start": "1991-01-01", "end": "2020-12-31" },
      "required_by_models": ["nepal_lstm_v1"]
    }
  ]
}
```

The catalog is the semantic bridge between FI static feature names and the upstream
attribute extraction process. FI owns the required names; this catalog owns the
meaning, units, and source provenance for those names in a SAP3 deployment.

## 8. `validation_report.json`

The validation report records package-level and per-basin validation outcomes. It
MUST be produced by the package creator and MAY be extended by SAP3 import
validation.

Required top-level fields:

| Field | Type | Requirement |
|---|---|---|
| `summary` | object | MUST include counts for `passed`, `failed`, and `warnings`. |
| `basins` | array | MUST include one entry per `basins.gpkg` feature. |

Required per-basin fields:

| Field | Type | Requirement |
|---|---|---|
| `network` | string | Basin network. |
| `basin_code` | string | Basin code. |
| `station_code` | string | Station code. |
| `gateway_hru_name` | string | Gateway HRU/GPKG name. |
| `name` | string | Gateway-echoed polygon key. |
| `status` | string | MUST be `passed`, `warning`, or `failed`. |
| `checks` | object | MUST include the checks listed below. |
| `warnings` | array | Human-readable warning strings. |
| `errors` | array | Human-readable error strings. |

Minimum checks:

| Check | Meaning |
|---|---|
| `geometry_present` | Geometry exists and is non-empty. |
| `geometry_valid` | Geometry is OGC-valid. |
| `crs_epsg_4326` | Geometry CRS is EPSG:4326. |
| `geometry_2d` | Geometry has no Z/M dimension. |
| `area_positive` | `area_km2 > 0`. |
| `ids_unique` | `network + basin_code`, Gateway HRU names, and Gateway feature `name` values are unique. |
| `static_row_present` | Static attribute row exists. |
| `required_static_features_present` | Required static features are present and non-null for intended models. |
| `outlet_snap_distance_m` | Numeric snap distance or null if not applicable. |
| `coverage_status` | One of `inside`, `partial`, `outside`, or `unknown`. |

Example:

```json
{
  "summary": {
    "passed": 118,
    "failed": 2,
    "warnings": 9
  },
  "basins": [
    {
      "network": "dhm",
      "basin_code": "5501",
      "station_code": "5501",
      "gateway_hru_name": "nepal_dhm_v1",
      "name": "g_5501",
      "status": "passed",
      "checks": {
        "geometry_present": true,
        "geometry_valid": true,
        "crs_epsg_4326": true,
        "geometry_2d": true,
        "area_positive": true,
        "ids_unique": true,
        "static_row_present": true,
        "required_static_features_present": true,
        "outlet_snap_distance_m": 75.2,
        "coverage_status": "inside"
      },
      "warnings": [],
      "errors": []
    }
  ]
}
```

## 9. SAP3 import acceptance rules

SAP3 MUST reject the entire package when:

- `manifest.contract_version` is unsupported.
- Mandatory files are missing.
- Any required file checksum is present and does not match.
- `manifest.network` is empty or conflicts with package file contents.
- Any geometry file is not EPSG:4326.
- Package-level IDs are duplicated in a way that prevents deterministic import.
- `feature_catalog.json` omits a static attribute column found in
  `static_attributes.parquet`.

SAP3 MUST reject or keep an individual station/basin in `onboarding` when:

- Basin geometry is missing, empty, invalid, or not convertible to 2-D
  `MultiPolygon`.
- `area_km2` is non-positive.
- `station_code` cannot be matched to a SAP3 station definition.
- Required static features for the assigned model are missing or null.
- Gateway feature `name` values are missing, duplicated, or violate Gateway naming
  rules.
- Gateway HRU names are missing or are not declared in the manifest.
- The basin lies outside required forcing/static coverage.

SAP3 SHOULD allow package import with per-basin warnings when the basin is not yet
assigned to a model requiring the missing feature, but such warnings MUST remain
visible in onboarding reports.

## 10. Failure handling

Expected failure categories:

| Category | SAP3 behavior |
|---|---|
| Invalid package schema | Reject package before writing basin/station data. |
| Invalid basin geometry | Keep station in `onboarding`; do not register Gateway HRU. |
| Missing required static feature | Do not train or assign models requiring that feature. |
| Partial coverage | Block training for affected model/station until coverage is resolved or explicitly waived. |
| Uncertain outlet snap | Require manual review before marking station operational. |
| Official/global geometry disagreement | Require manual review when difference exceeds deployment tolerance. |

The importer MUST NOT silently synthesize missing static attributes, modify basin
geometry to make it pass validation, or fall back to a different basin without a
recorded operator decision.

## 11. Versioning and corrections

The package is immutable once accepted. If basin geometry or attributes change, the
producer MUST create a new package with a new `package_id`.

SAP3 SHOULD store enough provenance to answer:

- Which package produced a basin geometry?
- Which package produced a static attribute value?
- Which basin/Gateway HRU name and feature `name` were used for historical and
  operational forcing?
- Which model artifacts were trained using those static attributes and basin
  geometries?

When a basin geometry changes after training or operational forecasts exist, SAP3
SHOULD treat it as a material data change:

1. Re-register or update the Gateway HRU if needed.
2. Re-extract historical forcing for the corrected geometry.
3. Recompute static attributes.
4. Retrain affected model artifacts.
5. Recompute hindcast skill before promoting the corrected station/model path.

## 12. Maintenance and deliverable boundary

This contract does not decide whether the upstream extraction tool is open source,
source-available, a hosted service, or an internal HSOL tool. That is a project and
licensing decision outside the SAP3 runtime interface.

For DHM handover, the deployment MUST nevertheless have a durable regeneration path.
At least one of the following MUST be true:

1. DHM can run a documented, licensed extractor release and produce this package.
2. HSOL or the tool owner provides a documented service/SLA to regenerate packages.
3. The project delivers all basin/static packages needed for the agreed operational
   station set, and later basin additions are explicitly out of scope.

The chosen option MUST be documented in the deployment handover runbook before
production go-live.

## 13. Relationship to other contracts

- `01-data-gateway-requirements.md`: defines how validated basin/band polygons are
  registered with and addressed by the SAPPHIRE Data Gateway.
- `02-forecast-interface-requirements.md`: points to FI model contract docs; FI
  defines the names of static inputs a model requires.
- `docs/spec/types-and-protocols.md`: defines SAP3's `Basin`,
  `StationModelInputs.static`, `GroupModelInputs.static`, and
  `ModelDataRequirements.static_features`.
- `docs/v0-scope.md`: defines current v0 simplifications and the v0b point where
  static attributes become load-bearing for ML models.

## 14. Open questions

1. Which static feature names and units will the Nepal v1 model declare in
   `InputRequirement.static`?
2. What tolerance triggers manual review when global and official basin geometries
   disagree?
3. Which actor owns the first operational Nepal package: DHM, HSOL, or the basin-tool
   maintainer?
4. Which regeneration path from section 12 is part of the DHM handover?
5. Should the SAP3 importer support CSV as a temporary bridge, or require Parquet from
   the start?
6. Should a future contract version support categorical/string static features, and
   what SAP3/FI type changes would that require?
