# Plan 087 — ICON-CH2-EPS unstructured-mesh basin extraction (mesh-aware GridExtractor, no regrid)

**Status**: READY
**Phase**: 8 v0b / 10c (NWP path — Flow 1 weather ingest → archive → **extract**)
**Parent**: Plan 086 **Open Item E** (the mesh-extraction gap 086 explicitly does
NOT fix — `086-nwp-memory-bounded-streaming.md:549-553`, Risk 7 / D5), Plan 045
(NWP path wired into Flow 1 — GridExtractor + Zarr archiving). Together with Plan
086, both are needed for an NWP-on `forecast-cycle` to work end-to-end: 086 makes
the path **memory-safe + the archive valid**; 087 makes **extraction functional**.
The two are **independent** (087 does not depend on 086's lazy/dask change, and
086 does not depend on 087) — flagged in Risks.
**Related**: Plan 067 (MeteoSwiss STAC adapter configurability — owns the STAC
fetch/pagination logic; 087 adds a one-shot collection-asset fetch only as a
rejected alternative, see D-prov), Plan 077 (optional-NWP / runoff-only mode — the
path 087 makes functional is only reached when NWP is enabled; runoff-only
sidesteps it entirely), Plan 047 (Nepal v1 — the orography `h` coord 087 ships
enables the future elevation-band path, out of scope here).
**Created**: 2026-06-29
**Intended execution**: WF2 **new-capability** (`vision-build`) — a NEW mesh-aware
extractor + a coord-attach step, no single crisp current-vs-expected bug delta
(though a fix-mode framing is reproducible against the committed mesh fixture — see
the WF2 section). Claude authors the LOCKED known-answer + schema-lock tests first;
Codex implements against them.

---

## Problem

The NWP-on `forecast-cycle` cannot produce basin-average forcing from **real**
ICON-CH2-EPS data. ICON-CH2-EPS is an **unstructured triangular icosahedral mesh**:
the MeteoSwiss adapter parses GRIB2 into dims `(valid_time, member, values)` where
`values` is a single dim of **283 876** triangular cell centres — there are **NO
`latitude`/`longitude` dims** (verified: `tests/unit/adapters/test_meteoswiss_nwp_real.py:98-108`,
`"values" in ds.dims`, `ds.sizes["values"] == 283876`).

The only extractor, `ExactExtractGridExtractor`, requires a **2-D regular lat/lon
raster**: it calls `grid.rio.set_spatial_dims(x_dim="longitude", y_dim="latitude")`
(`preprocessing/exact_extract_grid_extractor.py:95-97`) and then runs `exactextract`
on 2-D `(latitude, longitude)` slices (`:110-134`). On real mesh data it **aborts at
`set_spatial_dims`** before any extraction — there is no lat/lon dim to set. This is
**Plan 086 Open Item E** (`086-…:549-553`). Because v0 models currently consume
**zero** NWP features (project memory), this is **forward-readiness**, not a live
operational outage — but it is a hard blocker for any NWP-driven model.

**The missing piece is purely the mesh→polygon spatial relation.** Everything
else is already plumbed: the catchment polygons flow CAMELS-CH shapefile →
`Basin{geometry: shapely MultiPolygon}` (`adapters/camelsch_adapter.py:199-281`,
`types/basin.py:11-23`) → PostGIS `PgBasinStore.fetch_basin` (EPSG:4326,
`store/basin_store.py:19-25,49,67`) → `station_basins: dict[StationId, Basin]` built
and handed to the extractor in the flow (`flows/run_forecast_cycle.py:616-625`,
passed at `:260-266`). What is absent is (a) per-cell lat/lon coordinates on the
parsed cube and (b) an extractor that assigns each mesh cell to a basin and takes a
per-basin mean.

**The per-cell coordinates exist and are publicly published.** A live STAC probe
confirmed they ship as a **collection-level STATIC asset** on the *same* collection
the adapter already uses (`config.toml:355,361-362`: base
`https://data.geo.admin.ch/api/stac/v1`, collection
`ch.meteoschweiz.ogd-forecasting-icon-ch2`): asset key
**`horizontal_constants_icon-ch2-eps.grib2`** (~2.84 MB GRIB2). Parsed, it carries
5 messages × 283 876 points with shortNames **`tlat`, `tlon`** (cell-centre lat/lon
— exactly what extraction needs), **`h`** (orography → enables a future elevation-band
path), `lsm`, and one `unknown`. The mesh is **fixed across cycles** ⇒ the
coordinates are static and fetched/derived **once**. The per-item fetch path filters
to grib2 **item** assets only (`_is_grib_asset`, `meteoswiss_nwp.py:81-89`) and does
**not** fetch collection-level assets, so today nothing brings these coordinates into
the pipeline.

### Scope

- **In**: (1) **Provision the static mesh coordinates** — commit a compact derived
  fixture (`tlat`/`tlon`/`h` for the 283 876 cells) as a packaged data asset; (2)
  **Attach `latitude`/`longitude` (and `orography`) as 1-D coords on the `values`
  dim** in the adapter's `convert_raw_dataset` (`meteoswiss_nwp.py:130-137`),
  normalising longitude to `[-180, 180]`; (3) a **NEW `MeshBasinExtractor`**
  (`GridExtractor`) that does point-in-polygon cell→basin assignment + a
  count-weighted per-basin mean **with NO regrid**, emitting the **identical**
  `BasinAverageForecast` schema, with `out_of_extent` parity and a small-basin
  nearest-cell fallback; (4) a **config selector** so the flow picks the
  mesh extractor for ICON and keeps `ExactExtractGridExtractor` for regular-grid
  sources.
- **Out**: **regridding the mesh to a lat/lon raster** (xESMF/ESMF C-lib or CDO
  binary — arm64 build burden mirroring `exactextract`, rejected below); NWP
  bias-correction / post-processing; elevation-band extraction (the `h` coord is
  shipped but `ElevationBandForecast` emission is Plan 047 / a later plan); the
  Plan 086 memory-bounding / OOM work (independent); changing the STAC fetch /
  pagination logic (Plan 067); area-weighted means (count-weighting chosen, see
  D-weight); any change to `ExactExtractGridExtractor` beyond it staying the
  regular-grid path.
- **Runtime consequence (disclosed):** after BOTH 086 and 087 land, an NWP-on
  forecast-cycle parses (memory-safe, 086) → archives (086) → **extracts** (087)
  end-to-end. 087 alone (without 086) makes extraction *correct* but the path can
  still OOM at parse on the dev host (086's concern); 086 alone makes the path
  memory-safe but it still aborts at `set_spatial_dims` (this plan's concern).

---

## Decisions / ground truth (verified against the codebase + the live STAC probe, 2026-06-29)

| # | Decision | Evidence |
|---|---|---|
| D1 | **Real parsed dims are `(valid_time, member, values)` with `values` = 283 876 unstructured cells and NO `latitude`/`longitude` dims.** ecCodes exposes the ICON triangular mesh as one `values` dim. | `tests/unit/adapters/test_meteoswiss_nwp_real.py:98-108` (`"values" in ds.dims`, `ds.sizes["values"] == 283876`). |
| D2 | **The only extractor needs a 2-D regular lat/lon raster and aborts on the mesh.** `extract` calls `grid.rio.set_spatial_dims(x_dim="longitude", y_dim="latitude")` then runs `exact_extract` on 2-D `.sel(valid_time, member)` slices. On the `values`-only mesh `set_spatial_dims` raises before any extraction. | `preprocessing/exact_extract_grid_extractor.py:95-97` (set_spatial_dims), `:110-134` (2-D slice loop, `exact_extract` `:121`). |
| D3 (probe) | **Per-cell coords ship as a collection-level STATIC asset on the SAME collection.** Asset key `horizontal_constants_icon-ch2-eps.grib2` (~2.84 MB GRIB2) on collection `ch.meteoschweiz.ogd-forecasting-icon-ch2` (base `…/api/stac/v1`). Parsed: 5 messages × 283 876 pts, shortNames `tlat`, `tlon` (cell-centre lat/lon), `h` (orography), `lsm`, + 1 `unknown`. Mesh is **fixed across cycles** ⇒ static, fetch/derive **once**. | Live STAC probe (data-source fact, NOT code); collection + base verified at `config.toml:355,361-362`. |
| D4 | **The per-item fetch path filters grib2 ITEM assets only — it does NOT fetch collection-level assets, and `convert_raw_dataset` adds no coords today.** So nothing currently brings `tlat`/`tlon` into the cube. | `_is_grib_asset` `adapters/meteoswiss_nwp.py:81-89`; `convert_raw_dataset` `:130-137` (rename `number`→`member`, no lat/lon). |
| D5 | **The `GridExtractor` Protocol is `@runtime_checkable`** with `extract(grid, configs, basins, cycle_time, nwp_source) -> dict[StationId, BasinAverageForecast \| ElevationBandForecast]`. A new extractor satisfies it structurally (no inheritance). | `protocols/grid_extractor.py:15-25`. |
| D6 | **Output schema to replicate: `BasinAverageForecast(nwp_source, cycle_time, values)` where `values` is a polars frame `{valid_time: Datetime("us","UTC"), parameter: Utf8, member_id: Int64, value: Float64}`. All-NaN-members for a basin ⇒ `out_of_extent` → `ExtractionError("polygon(s) outside grid extent: …")`.** | `exact_extract_grid_extractor.py:171-184` (schema), `:136-166` (out_of_extent → `ExtractionError`); `types/weather.py:45-49` (`BasinAverageForecast`). |
| D7 | **Catchment polygons are already plumbed end-to-end** (only the mesh→polygon relation is missing). CAMELS-CH → `Basin{geometry: MultiPolygon}` (force_2d, Polygon→MultiPolygon) → PostGIS round-trip at **EPSG:4326** → `station_basins` built per operational station and passed to `extract(basins=…)`. | `adapters/camelsch_adapter.py:199-281`; `types/basin.py:11-23`; `store/basin_store.py:19-25` + `:49` `from_shape(..., srid=4326)` + `:67` `to_shape`; flow build `flows/run_forecast_cycle.py:616-625`, passed at `:260-266`. |
| D8 | **NO new dep at all. `geopandas`, `shapely`, `numpy`, `dask`, `xarray`, `rioxarray`, `exactextract` are already DIRECT deps. The nearest-cell fallback uses `shapely.STRtree(centroid_points).nearest(point)` (shapely is already a dep) — `scipy` is NOT used, so no transitive-dep promotion and no new footprint.** | `pyproject.toml:22` `geopandas>=1.1.3`, `:38` `shapely`, `:27` `numpy>=2.4.3`, `:16` `dask[array]`, `:43` `xarray`, `:36` `rioxarray`, `:18` `exactextract`. `shapely.STRtree.nearest` is the documented spatial-index nearest-geometry API. |
| D9 | **The coord-attach point is `convert_raw_dataset` (`meteoswiss_nwp.py:130-137`)**, called once after the combine at `:629`; the parse entry is `_parse_grib_files` (`:564`, invoked `:372`). Attaching 1-D coords of length 283 876 (~2 MB of float64) is cheap and stays lazy/dask-compatible (no `.load`). | `adapters/meteoswiss_nwp.py:130-137`, `:564`, `:372`, `:629`. |
| D10 | **The extractor is selected in the flow at `run_forecast_cycle.py:533-538`, defaulting to `ExactExtractGridExtractor()`; there is no extractor knob in config today.** The `config.toml` `[adapters.weather_forecast]` block carries `type`/`archive`/`max_cache_age_hours`/`archive_format`/`archive_base_path` etc., but the dataclass `_WeatherForecastAdapterConfig` **loads only a subset** — `enabled`, `stac_base_url`, `stac_collection`, `scratch_path`. The selector is a new config field threaded loader → dataclass → the `:533-538` default. | `flows/run_forecast_cycle.py:533-538` (default instantiation), `:70-74` (`_WeatherForecastAdapterConfig`), `:84-145` (`_load_weather_forecast_adapter_config`); `config.toml:354-365`. |
| D11 (licence) | **Shipping a derived MeteoSwiss OGD asset is licence-compatible** — the existing real GRIB fixtures in the same tree are already vendored under **CC-BY** with documented provenance; the new derived `tlat`/`tlon`/`h` asset inherits the same open-data terms (attribution retained). | `tests/unit/adapters/test_meteoswiss_nwp_real.py:1-7` (CC-BY docstring); `tests/fixtures/meteoswiss_nwp/README.md` (provenance). |
| D12 | **The existing synthetic extractor fixture is a lat/lon GRID (`member, valid_time, latitude, longitude`), NOT a mesh** — it cannot exercise the mesh path, so mesh tests need a NEW `values`-dim fixture (mirrors Plan 086 D3/D11's "fixtures must use the real layout"). | `tests/unit/preprocessing/test_exact_extract_grid_extractor.py:25-65` (`_make_grid`, lat/lon dims). |
| D13 (empirical, HARD GUARANTEE) | **Cell ordering is GUARANTEED identical between the `horizontal_constants` asset and the forecast GRIBs by `uuidOfHGrid`.** Parsing both files empirically: `uuidOfHGrid` is **byte-identical** (`bbbd5a09855499243c7a4aa4c8762920`) across the grid file and the forecast fixture; both report `gridDefinitionTemplateNumber=101` (unstructured), `numberOfGridUsed/InReference=2/1`, `numberOfDataPoints=283876`. `uuidOfHGrid` is ICON's **definitive horizontal-grid identity** ⇒ the `tlat`/`tlon` `values` ordering **== forecast `values` ordering by construction**. This UPGRADES old Risk 7 from "verified-by-assumption" to a hard guarantee. **Caveat:** cfgrib DROPS `uuidOfHGrid` from the runtime cube, so runtime cannot self-verify ordering — the invariant is instead pinned at **regeneration time** (Task 1c asserts the UUID; MINOR-4). | Empirical parse of `horizontal_constants_icon-ch2-eps.grib2` + the committed forecast fixture (2026-06-29). |
| D14 (empirical, verified ranges) | **The mesh coordinate arrays span the FULL ICON-CH2 MODEL DOMAIN, much wider than Switzerland.** Empirically: `tlat` ∈ **[42.0786, 50.4792]**, `tlon` ∈ **[-0.7691, 17.6776]** — already on **[-180,180]** with **NEGATIVES present** (NOT 0..360), so the `((lon+180)%360)-180` normalisation is a **verified no-op** here (kept as a defensive guard). `h` ∈ [-5.14, 4205.36] m. The Swiss band (lat ~45–48, lon ~5–11) is a **strict subset** — it applies ONLY to basin/extraction-OUTPUT checks (a CH basin centroid), **never** to the full coordinate-array range checks. | Empirical parse of `horizontal_constants_icon-ch2-eps.grib2` (2026-06-29). |

---

## Key design decisions (made + justified)

### D-approach — mesh-aware extraction, NO regrid

Assign **each mesh cell centre to a basin** by point-in-polygon, then take a
per-basin mean over its member cells — directly on the `values` mesh, never building
a raster. Concretely: attach `tlat`/`tlon` as 1-D coords on `values` → build
283 876 cell-centre points → `geopandas.sjoin` (or a shapely `STRtree` /
`prepared_geometry` over basins for speed) to map `cell → station_id` once per
cycle → for each `(station, member, valid_time)` take the mean of `value` over that
station's member cells → emit the **identical** `BasinAverageForecast` polars schema
(D6).

**Reject regridding** (xESMF / ESMF C-library, or the CDO binary): both add a
heavyweight native/C dependency with the **same arm64 source-build burden that
already bit `exactextract`** (project memory: Dockerfile builder stage installs
`build-essential`/`cmake`/`libgeos-dev` for the `exactextract` arm64 sdist). A regrid
also **discards information** (interpolation error) and needs a target-grid spec we
do not have. Point-in-polygon on the native mesh is exact-by-construction, uses
**only deps we already ship** (D8), and is the minimal change.

Implement as a **NEW** `MeshBasinExtractor`; `ExactExtractGridExtractor` **stays** for
regular-grid sources (a future regridded path, or Nepal v1 raster forcing). Both
satisfy the same `GridExtractor` Protocol (D5); the flow picks one via config
(D-selector).

### D-prov — grid-coords provisioning: **COMMIT a compact static asset** (not fetch-cache)

**Decision: commit a derived static asset** shipped with the package and loaded by
the extractor — NOT a runtime "fetch-once + cache" of the collection asset. Rationale:

- The mesh is **fixed across cycles** (D3) — the coordinates never change, so there is
  nothing to refresh; a fetch path adds runtime network failure modes for a constant.
- **Deterministic + offline** — unit tests and the dev pipeline need no network; the
  STAC fetch path (`_is_grib_asset` would need extending to collection assets, D4)
  stays untouched (that surface is Plan 067's).
- **Smallest change** — no new fetch/cache code, no cache-invalidation policy.
- Ship a **derived compact form, not the 2.84 MB GRIB**: extract `tlat`/`tlon`/`h`
  (283 876 × 3 float arrays) to a single `.npz` (or parquet), ~3–7 MB uncompressed /
  ~2–3 MB on disk, with **no GRIB/ecCodes read at runtime** (just `np.load`). A
  one-shot regeneration heredoc (parse the OGD GRIB → write the npz) is documented in
  the asset's provenance note so it is reproducible.
- **Package-data vs test-fixture**: ship it as **package data** under
  `src/sapphire_flow/` (e.g. `src/sapphire_flow/data/icon_ch2_mesh_coords.npz`),
  located via `importlib.resources` — it is needed at **runtime** by the adapter's
  coord-attach (D9), not only by tests, so it must be inside the installed wheel, not
  under `tests/`. (A separate tiny synthetic mesh lives under `tests/` for the
  known-answer unit test — D12.)
- **Licence (D11, RESOLVED)**: MeteoSwiss OGD is open data (the sibling GRIB fixtures
  are already vendored CC-BY); the derived asset inherits the same CC-BY/OGD terms. Ship
  the **standard MeteoSwiss OGD attribution string** in the in-wheel README next to the
  asset ("Source: MeteoSwiss / Federal Office of Meteorology and Climatology, OGD,
  CC-BY 4.0"). Minor; no longer an open item.

**Rejected alt — fetch-once + cache**: extend the adapter to GET the collection-level
`horizontal_constants_icon-ch2-eps.grib2`, parse `tlat`/`tlon`, cache under
`scratch_path`. More moving parts, a network dependency for a constant, and it
reopens the Plan-067-owned STAC fetch surface. Documented and rejected.

### D-weight — count-weighted mean (not area-weighted)

ICON-CH2 cells are ~2 km and **near-equal-area**, so a count-weighted mean
(arithmetic mean over a basin's member cells) ≈ an area-weighted mean to within the
mesh's small area variance. **Per-cell areas are NOT in the published
`horizontal_constants` asset** (it carries `tlat`/`tlon`/`h`/`lsm` + 1 unknown — D3,
no cell-area field); deriving them needs the mesh **connectivity** (a separate grid
file we do not ship). Count-weighting therefore needs **no extra data source** and is
**accepted for v0** (Q-weight RESOLVED — ICON-CH2 cells are near-equal-area; deriving
per-cell areas would need mesh connectivity not in the asset). **Flag**: schedule a
cell-area-sourcing follow-up **only if** a downstream NWP-consuming model later demands
area-weighting — not a v0 blocker.

### D-fallback — small-basin nearest-cell assignment (`shapely.STRtree.nearest`), with a named-distance domain check for `out_of_extent` parity

A basin smaller than one ~2 km² cell can capture **zero** cell centroids. Rather than
failing it (an honest small Swiss headwater would get no forcing), assign it the
**nearest** cell centre via **`shapely.STRtree(centroid_points).nearest(basin_point)`**
(shapely is already a dep, D8 — **no scipy**). To preserve the existing `out_of_extent`
→ `ExtractionError` contract (D6), apply a **domain guard with a NAMED module constant**:
a basin that captures zero centroids is treated as `out_of_extent` (and raises the
identical `ExtractionError` message) **iff its nearest cell-centroid distance EXCEEDS a
named module constant** — `_MAX_NEAREST_CELL_DEG ≈ 0.04°` (≈ **2× the mean cell spacing**;
~2 km cell ≈ 0.018°). Otherwise it snaps to the nearest cell. So: ≥1 centroid →
count-mean; 0 centroids with nearest distance ≤ `_MAX_NEAREST_CELL_DEG` → nearest-cell;
0 centroids with nearest distance > `_MAX_NEAREST_CELL_DEG` → `ExtractionError`. The
distance criterion is chosen over **bounding-box containment** because the mesh domain
(D14) is **non-rectangular** — bbox containment would over-admit basins in the corners
outside the actual mesh. The constant + this branch are pinned by Task 4f (in-domain
snap) and Task 4b (out-of-domain raise). **(Q-domain RESOLVED.)**

### D-selector — minimal config flag on the NWP source

Add one optional field to `[adapters.weather_forecast]` (e.g.
`grid_extractor = "mesh" | "exactextract"`, **default `"mesh"`** for the ICON v0
source), read it in `_load_weather_forecast_adapter_config` with a `Literal`/string
guard (raise `ConfigurationError` on an unknown value, mirroring the existing
`enabled` bool guard), carry it on `_WeatherForecastAdapterConfig`, and branch the
`run_forecast_cycle.py:533-538` default between `MeshBasinExtractor()` and
`ExactExtractGridExtractor()`. An explicitly-injected `grid_extractor=` (tests) still
wins. Keep it minimal — no registry, no entry-points.

---

## Phases

### Phase 1 — Static mesh coords + coord attach (ROOT)

#### Task 1a — Derive + commit the compact static mesh-coords asset

- **Scope**: From the OGD `horizontal_constants_icon-ch2-eps.grib2` (D3), extract the
  283 876-length `tlat`, `tlon`, `h` arrays and commit them as a packaged data asset
  under `src/sapphire_flow/data/` (e.g. `icon_ch2_mesh_coords.npz`), located at
  runtime via `importlib.resources`. **Store `tlat`/`tlon`/`h` as `float32` via
  `np.savez_compressed`** (MINOR-6 — ~2 km resolution needs <6 sig digits so float32 is
  ample; ≈1.1 MB/array, compressed ≈2 MB total, ~half the float64 footprint). Add a
  provenance/licence README next to it (the **standard MeteoSwiss OGD/CC-BY attribution
  string**, D11) including the one-shot regeneration heredoc (parse GRIB → write npz);
  the recipe MUST assert `uuidOfHGrid` (Task 1c). Out: any GRIB/ecCodes read at runtime;
  fetching the asset from STAC (D-prov rejects fetch-cache); cell-area derivation
  (D-weight).
- **Verification**: `uv run python -c "import numpy as np, importlib.resources as r;
  d=np.load(r.files('sapphire_flow.data')/'icon_ch2_mesh_coords.npz');
  assert d['tlat'].shape==(283876,) and d['tlon'].shape==(283876,);
  assert d['tlat'].dtype==np.float32 and d['tlon'].dtype==np.float32;
  assert -1.0 < d['tlon'].min() < 0.0 and 17.0 < d['tlon'].max() < 18.0;
  assert 42.0 < d['tlat'].min() < 42.5 and 50.0 < d['tlat'].max() < 50.5"`;
  `uv run ruff check src/`.
- **Exit gate**: the asset loads via `importlib.resources` with `tlat`/`tlon`/`h`
  each length 283 876, dtype `float32`; the coordinate arrays span the **MESH DOMAIN**
  — `tlat` ∈ ~[42.0, 50.5], `tlon` ∈ ~[-0.8, 17.7] **(min lon NEGATIVE)** — NOT the
  narrower Swiss band (D14); the asset is inside the installed package (not under
  `tests/`); provenance + the standard CC-BY/OGD attribution + the
  `uuidOfHGrid`-asserting regeneration recipe documented.

#### Task 1c — Regeneration recipe pins `uuidOfHGrid` (ordering invariant, MINOR-4)

- **Scope**: The documented one-shot regeneration recipe (the heredoc that parses
  `horizontal_constants_icon-ch2-eps.grib2` → writes the committed `.npz`) **MUST assert
  `uuidOfHGrid == "bbbd5a09855499243c7a4aa4c8762920"`** before writing the asset. This
  is the invariant that guarantees `tlat`/`tlon` ordering == forecast `values` ordering
  (D13). Because cfgrib DROPS `uuidOfHGrid` from the runtime cube (the runtime can only
  length-guard `len(tlat) == ds.sizes["values"]`, which does NOT catch a reorder), the
  ordering correspondence cannot be self-verified at runtime — it is pinned **at
  regeneration**. A future ICON grid revision (new UUID) MUST then **fail LOUDLY at
  regeneration** rather than silently corrupting every basin mean. Document the UUID
  invariant explicitly in the asset's provenance README. Out: any runtime UUID check
  (cfgrib drops it); changing the forecast parse path.
- **Verification**: run the documented regeneration heredoc against the OGD GRIB and
  confirm the `uuidOfHGrid` assertion passes (and that flipping the expected UUID makes
  it raise) — e.g. `uv run python <<'EOF'` parsing the grib and asserting the UUID.
- **Exit gate**: the regeneration recipe parses the GRIB, asserts
  `uuidOfHGrid == "bbbd5a09855499243c7a4aa4c8762920"`, and refuses to write the asset on
  mismatch; the UUID invariant + this fail-loud behaviour are documented in the
  provenance README.

#### Task 1b — Attach `latitude`/`longitude` (+ `orography`) as 1-D `values`-dim coords

- **Scope**: In `convert_raw_dataset` (`meteoswiss_nwp.py:130-137`), when the cube
  has a `values` dim, attach `latitude`/`longitude` 1-D coords (from the Task-1a
  asset's `tlat`/`tlon`) on `values`, **normalising longitude to `[-180, 180]`**
  (`((lon + 180) % 360) - 180`) so it matches EPSG:4326 basin polygons (Risk-lon);
  optionally attach `orography` from `h` for the future band path. Guard that the
  asset length equals `ds.sizes["values"]` (raise `AdapterError` on mismatch). Stays
  lazy — no `.load()`/`.compute()` on the data cube. Out: regridding; any change to
  the GRIB filter, member-count gate, units, deaccumulation, or output values; the
  regular-grid path (which has no `values` dim → attach is a no-op).
- **Verification**: `uv run pytest tests/unit/adapters/test_meteoswiss_nwp_real.py
  tests/unit/adapters/test_meteoswiss_nwp.py`;
  `uv run pyright src/sapphire_flow/adapters/meteoswiss_nwp.py`.
- **Exit gate**: after parse of the real fixture, the cube carries `latitude` and
  `longitude` coords on the `values` dim, each length 283 876, with values spanning the
  **MESH DOMAIN** — lat ∈ ~[42.0, 50.5], lon ∈ ~[-0.8, 17.7] (**min lon NEGATIVE**,
  already [-180,180] so normalisation is a no-op, D14) — NOT the narrower Swiss band;
  all existing adapter tests (dims, sorted valid_time, units, member count) stay green;
  a length mismatch raises `AdapterError`.

### Phase 2 — `MeshBasinExtractor` (depends on Phase 1; needs the `values`-dim coords)

#### Task 2a — New mesh-aware `GridExtractor`: point-in-polygon + count-weighted mean, with parity + fallback

- **Scope**: Add `MeshBasinExtractor` (new module under `preprocessing/`,
  e.g. `mesh_basin_extractor.py`) implementing the `GridExtractor` Protocol (D5). It:
  (1) reads `latitude`/`longitude` coords off the `grid`'s `values` dim and builds
  283 876 cell-centre points; (2) maps each cell to a basin via `geopandas.sjoin`
  (or shapely `STRtree`/prepared geometries) **once per cycle**, reusing the same
  station-validation/skip logic shape as the existing extractor
  (`exact_extract_grid_extractor.py:62-93`); (3) for each `(station, member,
  valid_time)` takes the **count-weighted mean** (D-weight) over the station's member
  cells; (4) emits the **identical** `BasinAverageForecast` polars schema (D6); (5)
  mirrors the existing extractor's **per-member NaN parity exactly** (MINOR-3,
  `exact_extract_grid_extractor.py:136-166`): for a given basin **DROP an individual
  member that is all-NaN** for that basin, and raise `out_of_extent` →
  `ExtractionError("polygon(s) outside grid extent: …")` **only when ALL members are
  missing** — same message contract; (6) for a basin capturing zero centroids but
  **in-domain** (nearest distance ≤ `_MAX_NEAREST_CELL_DEG`, D-fallback), assigns the
  **nearest cell via `shapely.STRtree(centroid_points).nearest(...)`** (NO scipy), and
  treats zero-centroid basins with nearest distance > `_MAX_NEAREST_CELL_DEG` as
  out-of-domain → `ExtractionError`. Define `_MAX_NEAREST_CELL_DEG` (≈0.04°) as a NAMED
  module constant (D-fallback). Reuse `_to_utc_datetime` / `ensure_utc` boundary
  handling (tz-aware contract, `exact_extract_grid_extractor.py:32-41`). Out: regridding;
  elevation bands / `ElevationBandForecast`; area-weighting; any change to
  `ExactExtractGridExtractor`.
- **Verification**: `uv run pytest tests/unit/preprocessing/test_mesh_basin_extractor.py`;
  `uv run pyright src/sapphire_flow/preprocessing/mesh_basin_extractor.py`.
- **Exit gate**: the known-answer synthetic-mesh test (Task 4a) gives the
  hand-computed basin mean per member/valid_time; an out-of-domain basin raises the
  same `ExtractionError` message AND per-member all-NaN parity holds (a single all-NaN
  member is dropped; all-members-missing raises) (Task 4b); an in-domain sub-cell basin
  gets nearest-cell forcing via `shapely.STRtree.nearest` (Task 4f);
  `isinstance(MeshBasinExtractor(), GridExtractor)` is `True` (Task 4c); output is a
  `BasinAverageForecast` with the exact polars schema (Task 4d). No `scipy` import.

### Phase 3 — Selector / config wiring + real-mesh E2E (depends on Phases 1 + 2)

#### Task 3a — Config selector: pick mesh vs regular-grid extractor

- **Scope**: Add an optional `grid_extractor` field (`"mesh" | "exactextract"`,
  default `"mesh"`) to `[adapters.weather_forecast]` (`config.toml:355-365`); read it
  in `_load_weather_forecast_adapter_config` (`run_forecast_cycle.py:84-145`) with an
  unknown-value `ConfigurationError` guard (mirroring the `enabled` bool guard);
  carry it on `_WeatherForecastAdapterConfig` (`:70-74`); branch the default
  extractor instantiation at `:533-538` between `MeshBasinExtractor()` and
  `ExactExtractGridExtractor()`. An explicitly-injected `grid_extractor=` argument
  still wins (`:435`, `:533`). Out: a registry/entry-points mechanism; changing the
  injected-extractor precedence; touching `ExactExtractGridExtractor`.
- **Verification**: `uv run pytest tests/unit/flows/test_run_forecast_cycle.py
  -k "extractor or config"`; `uv run pyright src/sapphire_flow/flows/run_forecast_cycle.py`.
- **Exit gate**: `[adapters.weather_forecast].grid_extractor = "mesh"` (or default)
  selects `MeshBasinExtractor`; `"exactextract"` selects `ExactExtractGridExtractor`;
  an unknown value raises `ConfigurationError`; an injected extractor overrides
  config; ruff + pyright clean.

#### Task 3b — Real-mesh end-to-end smoke (parse → attach → extract)

- **Scope**: An integration-style test that parses the committed real ICON fixture
  (`tests/fixtures/meteoswiss_nwp/icon_ch2_eps_202604231200`), runs the Phase-1
  coord-attach, and feeds the cube to `MeshBasinExtractor` with a real (or
  realistic CH) basin polygon, asserting a non-empty `BasinAverageForecast` with the
  correct schema and member set. Confirms the mesh path works on real data — the
  capability 086 deferred. Out: a live STAC fetch (uses the committed fixture);
  asserting exact physical values (the known-answer lock is Task 4a).
- **Verification**: `uv run pytest tests/unit/preprocessing/test_mesh_basin_extractor.py
  -k real`.
- **Exit gate**: the real fixture extracts to a `BasinAverageForecast` whose `values`
  frame has rows for each `(member, valid_time)` of the covering basin, schema
  `{valid_time, parameter, member_id, value}`, no `set_spatial_dims` error.

### Phase 4 — Locked tests + verification gate (depends on Phases 1–3)

Per WF2, the LOCKED known-answer + schema-lock tests (4a–4f) are authored BEFORE
implementation (Claude writes them; Codex makes them pass). They are fast and
deterministic — a tiny synthetic mesh, no real GB, no network.

#### Task 4a — Locked known-answer synthetic-mesh test

- **Scope**: In a NEW `tests/unit/preprocessing/test_mesh_basin_extractor.py`, build a
  tiny mesh (e.g. 4 cells with known `tlat`/`tlon`, `value` 10/30 split) on a `values`
  dim with `(valid_time, member, values)` layout, plus one basin polygon covering
  **exactly 2** cells (the two `value=10` and `value=30` cells, or the 10/10 pair
  giving 10, etc.) → assert the basin mean equals the hand-computed value (e.g. 20.0)
  for every member/valid_time. Hook near the existing
  `test_exact_extract_grid_extractor.py:25-65` style but with a NEW mesh `_make_mesh`
  helper (D12 — do NOT reuse the lat/lon-grid `_make_grid`).
- **Verification**: `uv run pytest tests/unit/preprocessing/test_mesh_basin_extractor.py -k known_answer`.
- **Exit gate**: the basin mean equals the hand-computed constant per member/valid_time.

#### Task 4b — Locked `out_of_extent` + per-member NaN parity test

- **Scope**: Lock BOTH halves of the existing extractor's missing-data contract
  (`exact_extract_grid_extractor.py:136-166`, MINOR-3):
  (1) **out-of-domain** — a basin enclosing **no** cell centroid and whose nearest cell
  is **beyond `_MAX_NEAREST_CELL_DEG`** (e.g. far outside CH) → assert
  `MeshBasinExtractor.extract` raises `ExtractionError` matching the message contract
  (`"polygon(s) outside grid extent: …"`, D6 / D-fallback);
  (2) **per-member NaN parity** — for a covered basin where ONE member is all-NaN over
  that basin's cells, assert that member is **dropped** while the others are retained,
  and that when **ALL** members are missing for the basin it raises the **same**
  `ExtractionError` — byte-identical to the existing extractor's behaviour.
- **Verification**: `uv run pytest tests/unit/preprocessing/test_mesh_basin_extractor.py -k "out_of_extent or nan_parity"`.
- **Exit gate**: out-of-domain basin → `ExtractionError` with the matching message;
  a single all-NaN member is dropped (others retained); all-members-missing →
  the same `ExtractionError`; fails if the extractor silently returns empty or snaps to
  a far cell.

#### Task 4c — Locked Protocol-conformance test

- **Scope**: `isinstance(MeshBasinExtractor(), GridExtractor)` is `True`
  (`@runtime_checkable`, D5).
- **Verification**: `uv run pytest tests/unit/preprocessing/test_mesh_basin_extractor.py -k conformance`.
- **Exit gate**: the conformance assertion passes.

#### Task 4d — Locked output-schema test

- **Scope**: Assert the returned object is a `BasinAverageForecast` whose `values`
  polars frame has exactly schema `{valid_time: Datetime("us","UTC"), parameter: Utf8,
  member_id: Int64, value: Float64}` (byte-for-byte the existing extractor's schema,
  D6) — so the archive/store path is unchanged.
- **Verification**: `uv run pytest tests/unit/preprocessing/test_mesh_basin_extractor.py -k schema`.
- **Exit gate**: schema matches exactly; member/valid_time/parameter coverage as
  produced by the existing extractor for an equivalent input.

#### Task 4e — Locked real-fixture coord-attach test

- **Scope**: After the Phase-1 coord-attach, assert the parsed **real** dataset
  (`test_meteoswiss_nwp_real.py:98-108` style) carries `latitude`/`longitude` coords
  on the `values` dim, each length 283 876, with ranges spanning the **MESH DOMAIN**
  (D14): `latitude` ∈ ~[42.0, 50.5], `longitude` ∈ ~[-0.8, 17.7] — assert
  `longitude.min()` is **NEGATIVE** (~-0.77) and `longitude.max()` ~17.68, confirming
  the values are already on `[-180,180]` (normalisation is a verified no-op). **Do NOT
  assert the narrow Swiss band (5–11 / 45–48) on the full coordinate array** — that band
  is a strict subset that applies only to basin/extraction OUTPUT, not the model-domain
  coordinate arrays (MAJOR-1).
- **Verification**: `uv run pytest tests/unit/adapters/test_meteoswiss_nwp_real.py -k coords`.
- **Exit gate**: real cube carries mesh-domain lat/lon on `values`, length 283 876,
  with `longitude.min()` negative (~-0.77) and `longitude.max()` ~17.68; the Swiss-band
  assertion is NOT used on the coordinate array.

#### Task 4f — Locked small-basin nearest-cell fallback test

- **Scope**: An **in-domain** basin smaller than one cell (captures zero centroids,
  nearest distance ≤ `_MAX_NEAREST_CELL_DEG`) → assert it still receives forcing from
  the nearest cell via `shapely.STRtree(centroid_points).nearest(...)` (non-empty
  `BasinAverageForecast`, value == that cell's value), distinguishing it from the
  out-of-domain `out_of_extent` case (Task 4b, nearest > `_MAX_NEAREST_CELL_DEG`).
  (Q-domain RESOLVED to the named-distance criterion — the fallback stays.)
- **Verification**: `uv run pytest tests/unit/preprocessing/test_mesh_basin_extractor.py -k nearest`.
- **Exit gate**: in-domain sub-cell basin → nearest-cell value; no `ExtractionError`.

#### Task 4h — Wheel-packaging check: asset ships + loads from the INSTALLED wheel (MINOR-5)

- **Scope**: Build the wheel and confirm the committed data asset
  (`src/sapphire_flow/data/icon_ch2_mesh_coords.npz`) is **actually included in the
  wheel and loadable via `importlib.resources` FROM THE INSTALLED WHEEL**, not just from
  the source tree. This is a **real packaging risk**: the build backend is `uv_build`
  (`pyproject.toml:50-52`) and the only existing non-`.py` package file is
  `src/sapphire_flow/py.typed`; `uv_build` may **not** ship arbitrary data files by
  default, so `pyproject.toml` likely needs an explicit package-data/include directive.
  The full pytest suite runs **from source** and would NOT catch a missing-wheel-data
  bug — hence a dedicated build+install check. Out: publishing the wheel; changing the
  build backend.
- **Verification**: `uv build --wheel`, then in a throwaway venv `python -m pip install
  dist/sapphire_flow-*.whl` (or `uv run --isolated`) and
  `python -c "import importlib.resources as r, numpy as np;
  d=np.load(r.files('sapphire_flow.data')/'icon_ch2_mesh_coords.npz');
  assert d['tlat'].shape==(283876,)"`; alternatively `unzip -l dist/*.whl | grep
  icon_ch2_mesh_coords.npz`.
- **Exit gate**: the built wheel CONTAINS `sapphire_flow/data/icon_ch2_mesh_coords.npz`
  and it loads via `importlib.resources` from the installed wheel with `tlat` length
  283 876; if absent, `pyproject.toml` is updated with the package-data/include
  directive until it ships.

#### Task 4g — Full verification gate

- **Scope**: Full-suite + typecheck + lint gate per `docs/workflow.md` Task Exit Gate.
  Confirm the coord-attach is correct, the mesh extractor passes known-answer +
  parity + schema, the selector wires both extractors, and the real-mesh E2E runs.
- **Verification**: `uv run pytest`; `uv run ruff check src/ tests/`;
  `uv run ruff format --check src/ tests/`; `uv run pyright src/`.
- **Exit gate**: all green; affected docs updated; no production file outside the
  Affected-files list changed; the committed asset is inside the package.

---

## WF2 milestone (for `vision-build`)

**Recommended invocation mode: NEW-CAPABILITY**, not fix-mode. Rationale: the
deliverable is a NEW `MeshBasinExtractor` + a coord-attach step + a selector — there
is no single pre-existing function whose output is "wrong" in a crisp
current-vs-expected sense; the natural acceptance is **known-answer + schema-lock**
construction tests, which `vision-build` authors as `acceptanceCriteria` without an
`issue` field. (A **fix-mode framing is available** if the harness prefers an
`issue`: "`ExactExtractGridExtractor.extract` raises at `set_spatial_dims` on the
committed real mesh fixture today" is a reproducible delta — but the fix is not to
the existing extractor, it is a new extractor + selector, so new-capability is the
honest mode.) **Ordering vs Plan 086**: 087 is **independent** of 086 (no shared
files except both touch `meteoswiss_nwp.py` — 086 at the cfgrib open `:578` /
combine, 087 at `convert_raw_dataset` `:130-137`; non-overlapping edits) and may run
**in parallel or in either order**; BOTH are required before a real NWP-on E2E run is
functional. If both land in the same branch window, coordinate the two
`meteoswiss_nwp.py` edits to avoid a textual conflict.

```text
milestone:
  id: icon-mesh-basin-extraction
  title: ICON-CH2-EPS unstructured-mesh basin extraction (mesh-aware GridExtractor, no regrid)
  goal: >
    Make the NWP-on forecast-cycle produce basin-average forcing from real
    ICON-CH2-EPS unstructured-mesh data. Ship the fixed per-cell mesh coordinates
    (tlat/tlon, plus orography h) as a committed static package asset; attach them
    as latitude/longitude 1-D coords on the values dim in the adapter
    (normalising longitude to [-180,180]); add a NEW MeshBasinExtractor that assigns
    each of the 283876 cell centres to a basin by point-in-polygon and takes a
    count-weighted per-basin mean WITH NO REGRID, emitting the identical
    BasinAverageForecast {valid_time, parameter, member_id, value} schema, with
    out_of_extent + per-member NaN parity and a small-basin nearest-cell fallback via
    shapely.STRtree.nearest; select mesh vs the existing regular-grid
    ExactExtractGridExtractor via a config flag. NO new dep at all (geopandas/shapely
    already direct; scipy NOT used); regridding (xESMF/CDO) rejected. Cell ordering is
    guaranteed by uuidOfHGrid (bbbd5a09855499243c7a4aa4c8762920), pinned at regeneration.
    Resolves Plan 086 Open Item E. Independent of Plan 086; both needed for E2E.
  acceptanceCriteria:
    - A committed static package asset (src/sapphire_flow/data/icon_ch2_mesh_coords.npz)
      provides tlat/tlon/h (float32, np.savez_compressed) for the 283876 ICON-CH2 cells,
      loadable via importlib.resources, with the standard CC-BY/OGD attribution + a
      regeneration recipe; no GRIB/ecCodes read at runtime.  [Task 1a]
    - The regeneration recipe asserts uuidOfHGrid == bbbd5a09855499243c7a4aa4c8762920
      (ICON horizontal-grid identity) and refuses to write on mismatch, so a future
      grid revision fails LOUDLY rather than silently corrupting basin means; cfgrib
      drops the UUID at runtime so the invariant is pinned at regeneration.  [Task 1c]
    - convert_raw_dataset attaches latitude/longitude (and orography) as 1-D coords
      on the values dim, longitude normalised to [-180,180] (a verified no-op: real
      tlon is already [-180,180] with negatives); after parse of the real fixture the
      cube carries MESH-DOMAIN lat/lon (lat ~42-50.5, lon ~-0.8-17.7, min lon NEGATIVE)
      of length 283876 -- NOT the narrow Swiss band; existing adapter tests (dims,
      units, member count, sorted valid_time) stay green; a coord-length mismatch
      raises AdapterError.  [Task 1b, 4e]
    - MeshBasinExtractor satisfies the @runtime_checkable GridExtractor Protocol;
      isinstance(MeshBasinExtractor(), GridExtractor) is True.  [Task 4c]
    - A known-answer synthetic mesh (4 cells, values 10/30) with a basin covering
      exactly 2 cells yields the hand-computed basin mean (e.g. 20.0) per
      member/valid_time.  [Task 4a]
    - Output is a BasinAverageForecast whose polars frame is exactly
      {valid_time: Datetime(us,UTC), parameter: Utf8, member_id: Int64,
       value: Float64} -- identical to ExactExtractGridExtractor.  [Task 4d]
    - A basin out of the mesh domain (nearest cell > _MAX_NEAREST_CELL_DEG) raises
      ExtractionError matching "polygon(s) outside grid extent: ..."; per-member NaN
      parity holds -- a single all-NaN member is dropped, all-members-missing raises
      the same ExtractionError (mirrors exact_extract_grid_extractor.py:136-166).
      [Task 4b]
    - An in-domain basin smaller than one cell (nearest <= _MAX_NEAREST_CELL_DEG)
      receives nearest-cell forcing via shapely.STRtree.nearest (NO scipy), not an
      ExtractionError.  [Task 4f]
    - A config grid_extractor flag selects mesh vs exactextract; unknown value raises
      ConfigurationError; an injected extractor overrides config.  [Task 3a]
    - The committed real ICON fixture parses -> coord-attach -> MeshBasinExtractor
      end-to-end to a non-empty BasinAverageForecast with no set_spatial_dims error
      (the capability Plan 086 deferred).  [Task 3b]
    - The built wheel CONTAINS sapphire_flow/data/icon_ch2_mesh_coords.npz and it loads
      via importlib.resources from the INSTALLED wheel (uv_build may not ship arbitrary
      data by default -- the from-source suite would miss this).  [Task 4h]
    - Locked tests (4a-4f) authored first and passing; full suite + ruff + pyright
      green; NO new dependency added (geopandas/shapely already direct; scipy NOT used
      anywhere in the extractor).  [Task 4g]
```

---

## Risks / unknowns

1. **`tlon` longitude convention (RESOLVED — verified, now a defensive no-op).**
   ICON GRIB longitudes are *commonly* 0..360, but the `horizontal_constants` asset was
   parsed empirically: `tlon` ∈ **[-0.7691, 17.6776]** — **already [-180,180] with
   NEGATIVES present** (NOT 0..360), `tlat` ∈ [42.0786, 50.4792] (D14). So the
   `((lon+180)%360)-180` normalisation at attach (Task 1b) is a **verified no-op** here,
   kept as a **defensive guard** against a future convention change. Task 4e asserts the
   real mesh-domain ranges (min lon NEGATIVE) rather than the Swiss band. Q-lon settled.
2. **Weighting choice (D-weight, RESOLVED for v0).** Count-weighting ≈ area-weighting
   for ~2 km near-equal-area cells, but per-cell areas are **not** in the published asset
   and need mesh connectivity to derive. Count-weighting is accepted for v0; if a model
   later requires area-weighting, cell areas must be sourced separately — a follow-up,
   not a v0 blocker.
3. **Small-basin fallback semantics (D-fallback, RESOLVED).** The nearest-cell fallback
   keeps honest small headwaters fed, but it **diverges** from `ExactExtractGridExtractor`,
   which has no nearest-cell behaviour (a polygon off the raster is `out_of_extent`).
   Q-domain is **settled**: the out-of-domain criterion is a **named-distance threshold**
   `_MAX_NEAREST_CELL_DEG ≈ 0.04°` (≈2× mean cell spacing) — a zero-centroid basin snaps
   to its nearest cell iff within that distance, else `ExtractionError`. The distance
   rule is chosen over mesh-bbox containment because the mesh domain is **non-rectangular**
   (D14) — bbox would over-admit corner basins. Implemented via `shapely.STRtree.nearest`
   (NO scipy, D8); pinned by Tasks 4b/4f.
4. **Shipping a MeteoSwiss OGD asset (licence, RESOLVED).** Open data (sibling fixtures
   already CC-BY, D11); ship the **standard MeteoSwiss OGD/CC-BY 4.0 attribution string**
   in the in-wheel README next to the asset. Settled — no longer an open item.
5. **`sjoin` performance at scale.** 283 876 cells × ~1000 basins via `geopandas.sjoin`
   is a one-shot spatial join per cycle; a prepared-geometry / `STRtree` path may be
   needed for the ~1000-station target. The cell→basin map is **cycle-invariant**
   (mesh fixed, basins fixed) so it can be computed once and cached — flag as a
   perf follow-up, not a v0 blocker (v0 dev runs are 2 stations, Plan 084).
6. **Independence from Plan 086 (ordering).** 087 makes extraction *correct* but does
   not address the eager-parse OOM (086); 086 makes the path memory-safe but still
   aborts at `set_spatial_dims` (087). **Both** are required for a real NWP-on E2E.
   The two `meteoswiss_nwp.py` edits are non-overlapping (086 `:578`/combine, 087
   `convert_raw_dataset` `:130-137`) — coordinate if landing in the same window.
7. **GRIB coord ordering correspondence (HARD GUARANTEE — was a risk, now resolved).**
   The committed `tlat`/`tlon` (from the `horizontal_constants` asset) are in the **same
   cell order** as the `values` dim of the forecast GRIBs — **guaranteed by
   `uuidOfHGrid`**. Empirically both files report a byte-identical
   `uuidOfHGrid = bbbd5a09855499243c7a4aa4c8762920` (plus `gridDefinitionTemplateNumber=101`,
   `numberOfDataPoints=283876`); `uuidOfHGrid` is ICON's definitive horizontal-grid
   identity, so identical UUID ⇒ identical ordering by construction (D13). cfgrib DROPS
   the UUID from the runtime cube (runtime can only length-guard, which does NOT catch a
   reorder), so the invariant is pinned at **regeneration**: Task 1c asserts the UUID and
   refuses to write on mismatch, making a future ICON grid revision fail LOUDLY instead
   of silently corrupting every basin mean.

---

## Open items / resolved decisions

**Resolved (folded into the plan as settled decisions):**

- **D-approach** — mesh-aware point-in-polygon extraction, **NO regrid** (xESMF/CDO
  rejected: arm64 native-build burden like `exactextract`, plus interpolation loss).
- **D-prov** — **COMMIT a compact static derived asset** (`tlat`/`tlon`/`h` → npz,
  package data, `importlib.resources`), NOT runtime fetch-cache (mesh is fixed;
  deterministic; offline; smallest change; keeps the Plan-067 STAC surface untouched).
- **D-weight** — **count-weighted** mean (cells ~2 km near-equal-area; per-cell areas
  not published).
- **D-fallback** — **nearest-cell via `shapely.STRtree.nearest`** (NO scipy) for
  in-domain sub-cell basins, gated by a named `_MAX_NEAREST_CELL_DEG ≈ 0.04°`;
  out-of-domain basins (nearest beyond the threshold) keep the `out_of_extent` →
  `ExtractionError` parity. Per-member NaN parity mirrors the existing extractor.
- **D-selector** — a single `grid_extractor` config flag (default `"mesh"`), no
  registry.
- **Q-lon (RESOLVED)** — empirically `tlon` ∈ [-0.7691, 17.6776] (already [-180,180],
  negatives present); the Task-1b normalisation is a verified defensive no-op (Risk 1 /
  D14).
- **Q-domain (RESOLVED)** — out-of-domain criterion is a **named-distance threshold**
  `_MAX_NEAREST_CELL_DEG ≈ 0.04°` (≈2× mean cell spacing), chosen over bbox containment
  because the mesh domain is non-rectangular (Risk 3 / D-fallback).
- **Q-weight (RESOLVED for v0)** — count-weighting accepted (ICON-CH2 near-equal-area;
  per-cell areas not in the asset); cell-area sourcing scheduled only if a model demands
  it (Risk 2).
- **Q-licence (RESOLVED)** — ship the standard MeteoSwiss OGD/CC-BY attribution string
  in the in-wheel README (Risk 4 / D11).
- **Ordering correspondence (RESOLVED)** — guaranteed by `uuidOfHGrid`
  (`bbbd5a09855499243c7a4aa4c8762920`), pinned at regeneration (Task 1c, D13 / Risk 7).

**Open (follow-ups, NOT v0 blockers):**

- **Q-perf** — whether the cell→basin map needs caching for the ~1000-station target
  (Risk 5); a perf follow-up, not a v0 blocker (v0 dev runs are 2 stations).

---

## Dependency graph

```json
{
  "phases": [
    {
      "id": "phase-1-coords-and-attach",
      "tasks": ["1a", "1c", "1b"],
      "parallel": false,
      "note": "ROOT: 1a commits the compact static float32 tlat/tlon/h asset (np.savez_compressed) as package data (importlib.resources); 1c pins the regeneration recipe's uuidOfHGrid assertion (bbbd5a09855499243c7a4aa4c8762920) so the ordering invariant fails loud on a grid revision; 1b attaches latitude/longitude (+orography) as 1-D values-dim coords in convert_raw_dataset, normalising lon to [-180,180] (a verified no-op). The extractor + E2E build on these coords. 1c documents the recipe behind 1a's asset; 1b depends on 1a (needs the asset)."
    },
    {
      "id": "phase-2-mesh-extractor",
      "tasks": ["2a"],
      "parallel": false,
      "depends_on": ["phase-1-coords-and-attach"],
      "note": "NEW MeshBasinExtractor (GridExtractor): point-in-polygon cell->basin (geopandas.sjoin / shapely STRtree), count-weighted per-basin mean, identical BasinAverageForecast schema, out_of_extent + per-member NaN parity, nearest-cell small-basin fallback via shapely.STRtree.nearest (NO scipy) gated by _MAX_NEAREST_CELL_DEG. Needs the values-dim lat/lon coords from phase-1. No regrid. ExactExtractGridExtractor stays for regular grids."
    },
    {
      "id": "phase-3-selector-and-e2e",
      "tasks": ["3a", "3b"],
      "parallel": false,
      "depends_on": ["phase-1-coords-and-attach", "phase-2-mesh-extractor"],
      "note": "3a config selector (grid_extractor flag, default mesh) threaded loader -> _WeatherForecastAdapterConfig -> the run_forecast_cycle.py:533-538 default; 3b real-fixture parse->attach->extract E2E (the capability Plan 086 deferred). Needs the extractor (phase-2) and the coords (phase-1)."
    },
    {
      "id": "phase-4-locked-tests-and-gate",
      "tasks": ["4a", "4b", "4c", "4d", "4e", "4f", "4h", "4g"],
      "parallel": false,
      "depends_on": [
        "phase-1-coords-and-attach",
        "phase-2-mesh-extractor",
        "phase-3-selector-and-e2e"
      ],
      "note": "Per WF2 the locked tests (4a-4f) are AUTHORED before implementation: 4a known-answer mesh mean, 4b out_of_extent + per-member NaN parity, 4c Protocol conformance, 4d output-schema lock, 4e real-fixture coord-attach (mesh-domain ranges, min lon NEGATIVE), 4f small-basin nearest-cell fallback (shapely.STRtree.nearest); 4h builds the wheel and confirms the .npz asset ships + loads from the installed wheel (uv_build packaging risk); 4g is the full verification gate."
    }
  ]
}
```

---

## Affected files

- `src/sapphire_flow/data/icon_ch2_mesh_coords.npz` (NEW package data) — Task 1a: the
  committed compact static mesh coords (`tlat`/`tlon`/`h`, 283 876 cells, **float32 via
  `np.savez_compressed`**) derived from the OGD `horizontal_constants_icon-ch2-eps.grib2`;
  loaded at runtime via `importlib.resources`. Plus a sibling provenance/licence README
  (standard CC-BY/OGD attribution + the `uuidOfHGrid`-asserting regeneration recipe,
  Task 1c).
- `pyproject.toml` — Task 1a/4h: ensure `src/sapphire_flow/data/*.npz` is included as
  package data in the **uv_build** wheel (uv_build may not ship arbitrary data by
  default — verified by the Task-4h wheel build+install check). No new runtime
  dependency is added (shapely/geopandas already direct; scipy NOT used).
- `src/sapphire_flow/adapters/meteoswiss_nwp.py` — Task 1b: in `convert_raw_dataset`
  (`:130-137`) attach `latitude`/`longitude` (+ `orography`) 1-D coords on the
  `values` dim from the Task-1a asset, normalising lon to `[-180,180]`; guard length
  vs `ds.sizes["values"]` (raise `AdapterError`). **Non-overlapping with Plan 086's
  edit** (086 is at the cfgrib open `:578` / combine).
- `src/sapphire_flow/preprocessing/mesh_basin_extractor.py` (NEW) — Task 2a:
  `MeshBasinExtractor` (point-in-polygon cell→basin, count-weighted mean,
  `out_of_extent` + per-member NaN parity, nearest-cell fallback via
  `shapely.STRtree.nearest` gated by the named `_MAX_NEAREST_CELL_DEG` constant,
  identical `BasinAverageForecast` schema). **No `scipy` import.**
  `ExactExtractGridExtractor` is **unchanged**.
- `src/sapphire_flow/flows/run_forecast_cycle.py` — Task 3a: add `grid_extractor` to
  `_WeatherForecastAdapterConfig` (`:70-74`), read it (with a `ConfigurationError`
  guard for unknown values) in `_load_weather_forecast_adapter_config` (`:84-145`),
  and branch the default extractor at `:533-538` between `MeshBasinExtractor()` and
  `ExactExtractGridExtractor()`. Injected `grid_extractor=` still wins.
- `config.toml` — Task 3a: add the optional `grid_extractor` field under
  `[adapters.weather_forecast]` (`:355-365`), default `"mesh"`, commented with the
  allowed values.
- `tests/unit/preprocessing/test_mesh_basin_extractor.py` (NEW) — Tasks 4a–4d, 4f, 3b:
  known-answer mesh mean, out_of_extent parity, Protocol conformance, output-schema
  lock, small-basin nearest-cell fallback, real-fixture E2E; a NEW `_make_mesh`
  helper (do NOT reuse the lat/lon-grid `_make_grid`, D12).
- `tests/unit/adapters/test_meteoswiss_nwp_real.py` — Task 4e: assert the coord-attach
  yields CH-range `latitude`/`longitude` on `values` (length 283 876).
- `tests/unit/flows/test_run_forecast_cycle.py` — Task 3a: the `grid_extractor`
  selector test (mesh / exactextract / unknown→`ConfigurationError` / injected wins).
- `docs/spec/config-reference.toml` — document the new `grid_extractor` field if the
  reference enumerates `[adapters.weather_forecast]`.
- `docs/v0-scope.md` and/or `docs/architecture-context.md` — note that NWP basin
  extraction from the ICON unstructured mesh is now supported (the data-flow §
  referencing the GridExtractor / NWP path), and that elevation-band extraction (the
  shipped `h`/orography coord) remains a Plan 047 follow-up.
- `docs/plans/086-nwp-memory-bounded-streaming.md` — cross-reference: mark **Open Item
  E** as addressed by Plan 087 (a one-line pointer; do not rewrite 086's scope).
- `docs/plans/087-icon-mesh-basin-extraction.md` (this plan),
  `docs/plans/README.md` (index entry).
- No other production files.
```
