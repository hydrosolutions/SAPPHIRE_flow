# Basin + static attribute extraction — what SAPPHIRE needs

> **To:** the basin and static data extraction implementer
> **From:** HSOL (SAPPHIRE Flow / SAP3)
> **Date:** 2026-07-14
> **Status:** DRAFT for your review — please challenge anything that does not fit your tooling.

You produce basin outlines and static catchment attributes. SAPPHIRE (SAP3) **consumes**
them as a file package. We do not call, vendor, or integrate your code, and you do not
need to know anything about SAP3 internals. This note tells you exactly what the files
must look like.

The full contract is `04-basin-static-artifact-contract.md`. This is the short version.

---

## 1. The two files that matter

```text
basins.gpkg                 # one polygon per catchment
static_attributes.parquet   # one row per catchment
```

Plus a `manifest.json`, a `feature_catalog.json` (what each attribute means and its unit),
and a `validation_report.json`. Those are described in the full contract.

---

## 2. `basins.gpkg`

**Format and CRS**

- GeoPackage only. Readable, non-empty.
- CRS **EPSG:4326**.
- Geometries: 2-D `Polygon` or `MultiPolygon`, OGC-valid. No Z/M dimension.
- At least one polygon feature.

**Naming — please read this carefully, it is where we expect confusion**

Four different things get called some variant of "name". We mean them as follows:

| Term | What it is |
|---|---|
| **Gateway HRU** | the GeoPackage **file** itself. One HRU = one `.gpkg`. |
| **the HRUs proper** | the **polygons inside** it — the actual catchments (or elevation bands). |
| **layer / table name** | the table inside the GeoPackage. `polygons` is fine. |
| **feature `name`** | a per-polygon attribute. This is the key echoed back in forcing data. |

The rule *"must start with a letter or underscore"* binds **both the layer name and the
per-feature `name` values**. That is why `00003` is rejected and `polygons` is fine.

**One GeoPackage holds one kind of polygon** — catchments **or** elevation bands, never
both mixed. (Your tooling does not care; we keep them separate so we can regenerate one
without the other.)

**Required per-feature columns**

| Column | Type | Notes |
|---|---|---|
| `name` | string | **`g_<station_code>`**, lowercased, non-alphanumeric runs → `_`. E.g. station `5501` → `g_5501`. |
| `station_code` | string | The **raw** gauge ID, verbatim, **as a string**. |
| `gauge_id` | string | Your region-prefixed key, e.g. `nepal_5501`. **Must be byte-identical to the `gauge_id` in the Parquet** — see watch-out 1. |
| `latitude`, `longitude` | float | Gauge coordinates. |
| `basin_code` | string | Unique within the network. |
| `area_km2` | float | > 0. |
| `network` | string | e.g. `dhm`. |

**Why the `g_` prefix rather than the bare gauge ID.** Gauge IDs are strings that may be
composed **entirely of digits** (`5501`), and a `name` starting with a digit is rejected.
A single unconditional convention beats a per-station rule that only sometimes applies.
The raw ID is still carried, untouched, in `station_code`.

**Please keep `station_code` string-typed.** Some hydromets use IDs containing `/`, `-`,
`'`, `_` or letters; some may have leading zeros. An integer column silently turns `0439`
into `439`.

**Name collisions must fail loudly.** Normalisation is not injective — `KAR/01` and
`KAR-01` both become `kar_01`. If two stations collide, **reject the package** with an
error naming both station codes. Do not silently suffix, truncate, or rename.

---

## 3. `static_attributes.parquet`

**Shape — this is fixed, please match it exactly**

- **Wide table, one row per gauge.**
- One **column per attribute**.
- Identity key is a **string**.
- **Every attribute column is `Float64`.**

This is the schema your own producers already write:

```python
schema = {"gauge_id": pl.Utf8, **{name: pl.Float64 for name in source_names}}
```

We cannot ingest long/multi-index layouts or one-row-per-(gauge, attribute).

**Categoricals are numeric class codes, not strings.** Majority-class attributes
(climate zone, land-cover class, lithology class) should arrive as their `float` class
code — which is what your extractor already emits. No string-valued attributes, no
booleans (use 0/1).

**Nulls are correct, zeros are not.** If an attribute cannot be computed for a basin,
write `null`. Never `0`, never a sentinel.

**Identity — exactly one column**

| Column | Notes |
|---|---|
| `gauge_id` | your key, region-prefixed. **The only identity column we need.** |

Everything else (`station_code`, `basin_code`, `network`, the Gateway `name`) lives in
`basins.gpkg`, which also carries `gauge_id`. We join the two files on `gauge_id`.

In other words: **the Parquet is exactly what your producers already write** —
`gauge_id` plus the `Float64` attribute columns. No extra join, no reshaping, nothing
to add.

**Which attributes?** The feature *list* is the model developer's call, not ours. Send us
whatever you extract; `feature_catalog.json` records each column's meaning, unit, and
source dataset. SAP3 validates at model-onboarding time that every feature a given model
declares is actually present and non-null for its stations.

---

## 4. The climate indices — you own these end to end

Your attributes split into two groups by input, but **you produce both**:

| Group | Input | Source |
|---|---|---|
| **A — geometry-derived** | basin polygon + HydroATLAS / DEM | your global raster archive |
| **B — forcing-derived** (Caravan climate indices) | catchment-averaged daily precipitation, temperature, PET | **your global ERA5-Land archive on S3** |

Group B is `p_mean`, mean PET, aridity, snow fraction, moisture index, seasonality, and
the high/low precipitation frequency and duration indices.

**You do not need anything from us to compute these.** No SAPPHIRE forcing, no Data
Gateway round-trip, no waiting for us to back-extract a historical record. Delineation
and static extraction are one self-contained step, and the package arrives complete in a
single delivery.

**Climatology window — please do NOT lock to the Caravan 1981–2020 window.** The main
use case here is the v1 DHM deployment, where there is no Caravan dataset to reproduce,
so comparability with published Caravan is not the goal. What we need instead is a
**single fixed ~30-year window applied to every basin and region**, so the indices are
internally comparable across the whole deployment. Our default is
**`1991-01-01 … 2020-12-31`** (the WMO 30-year normal, fully covered by ERA5-Land) — use
that unless we agree otherwise. The critical thing is that it is the **same window
everywhere**: please don't default to each basin's own record length, which would make
the indices incomparable. Record it once in `manifest.json` as
`"climatology_window": {"start": "1991-01-01", "end": "2020-12-31"}`, and set the same
object on each forcing-derived entry in `feature_catalog.json`.

The source is **ERA5-Land** (confirmed). Please still record `source_dataset` and the
climatology window per column in `feature_catalog.json`, so the provenance survives if
the product or window ever changes.

---

## 5. Three things most likely to go wrong — please watch for these

**1. `gauge_id` must be the SAME string in both files. This one fails silently — please
confirm our reading of your code.**

Reading `extract_hydroatlas.py`, it looks to us as though it reads `gauge_id` from the
basin GeoPackage and then passes every ID through `_prefixed_gauge_id()` before writing
the Parquet — so the Parquet would come out region-prefixed (`nepal_5501`) whether or not
the GeoPackage was. **Is that right?** If so:

- GeoPackage `5501` → Parquet `nepal_5501` → **the two files no longer share a key.** We
  join on `gauge_id`, so the join yields all-null statics. Nothing raises; it just
  quietly produces nothing.
- GeoPackage `nepal_5501` → the prefix function sees the prefix already present and
  leaves it alone → both files agree.

**So please write the already-prefixed form into `basins.gpkg`** — making the prefixing a
no-op and keeping both files aligned. To be explicit about what we require:

- `gauge_id` MUST be **region-prefixed** (`nepal_5501`), and
- MUST be **byte-identical** in `basins.gpkg` and `static_attributes.parquet`.

If we have misread your code, say so and we will correct the contract. If you think a
different convention is better, that is a conversation to have — not something to change
unilaterally, because we validate against the rule above.

On our side, import fails loudly if any `gauge_id` in one file has no counterpart in the
other; we will not silently accept a partial join.

**2. `area` vs `area_km2` — tell us if they disagree.**

Your Parquet carries `area` (from the clipped geometry); our GeoPackage carries
`area_km2`. They should describe the same polygon. Your `merge_into_native()` already
treats a native `area` as authoritative and discards the extracted one — that is sensible
in your hive, but for us a **material disagreement between the two is a signal that the
geometries have diverged**, and we would rather see it than have it silently reconciled.
Please surface any basin where they differ beyond a tolerance you consider normal.

**3. Send an immutable package, not a pointer into the hive.**

Both of your scripts write `data.parquet` **in place** (backing up to `.bak`). That is a
working hive, not a deliverable. We need a **snapshot**: a directory or archive with a
`manifest.json` carrying a `package_id` and checksums, which never changes after we
accept it. If the attributes change, that is a **new package with a new `package_id`** —
we re-import rather than diffing a file that moved under us. This matters because we
record which package produced every attribute value a model was trained on.

---

## 6. Questions back to you

1. Do you agree with the `g_<station_code>` feature-name convention, or does your tooling
   already impose a different one?
2. Can you emit `gauge_id`, `latitude`, `longitude` **and** `name`, `station_code`,
   `basin_code`, `area_km2`, `network` in one `basins.gpkg`? (They coexist fine — we just
   need both sets.)
2b. **Is our reading of `_prefixed_gauge_id()` correct** — does the Parquet come out
   region-prefixed regardless of what the GeoPackage carried? And can you write the
   already-prefixed `gauge_id` into `basins.gpkg` so both files agree? (Watch-out 1 —
   this is the one that fails silently.)
3. Can you compute the climate indices over a **fixed `1991-01-01 … 2020-12-31`
   window** (WMO normal), applied identically to every basin and region? If ERA5-Land
   coverage or your pipeline suggests a better fixed ~30-year window, propose it — the
   only hard rule is that it is the same everywhere and documented.
4. Do you have a view on who regenerates packages after handover — you, HSOL, or DHM?
5. Are there DHM gauge IDs that are **not** unique after lowercasing and normalisation?
   (This is the collision case in §2 — we need to know before you build the package.)
6. Elevation bands: will you produce them, and if so from which DEM and at what band width?
