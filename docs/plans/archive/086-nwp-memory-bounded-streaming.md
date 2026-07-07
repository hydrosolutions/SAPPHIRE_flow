# Plan 086 — NWP forecast-cycle: memory-bounded (lazy/dask) streaming to fix the OOM

**Status**: READY
**Phase**: 8 v0b / 10c (NWP path hardening — Flow 1 weather ingest → archive → extract)
**Parent**: Plan 084 (dev-deployment validation — its Phase-5 **Finding NWP-OOM** is
the repro this plan fixes,
`084-dev-deployment-validation-2-station-runoff.md:527-548`), Plan 045 (NWP path
wired into Flow 1 — GridExtractor + Zarr archiving), Plan 056 (zarr-python 3
migration; on-disk format v2 retained — the dask↔cfgrib↔zarr-3 interaction this
plan relies on must be validated against it)
**Related**: Plan 067 (MeteoSwiss STAC adapter configurability — owns moving NWP
knobs into config; this plan adds `max_files` alongside), Plan 077 (optional-NWP /
runoff-only mode — runoff-only sidesteps this path entirely)
**Created**: 2026-06-29
**Intended execution**: WF2 fix-mode (`vision-build`) — Claude authors the LOCKED
laziness + archive-round-trip non-regression tests first; Codex implements against
them. See the "WF2 milestone" section (the repro is a **laziness-property** +
**archive-round-trip** assertion, not a live OOM — justified below).

---

## Problem

The NWP-on `forecast-cycle` is **OOM-killed** (SIGKILL `-9`, worker container
`"OOMKilled": true`) on the dev host. Plan 084 Phase 5 reproduced it: clean stack,
2 operational stations, NWP-on (`config.toml [adapters.weather_forecast].enabled =
true`) → `prefect deployment run forecast-cycle/forecast-cycle` → flow **CRASHED**
after ~320 s, no forecasts stored, no graceful `nwp_fetch_failed_aborting`
(`084-…:527-548`).

**Root cause — one fully-materialized (eager) grid cube built during parse.** One
ICON-CH2-EPS cycle stages **484 GRIB2 / 2.7 GB compressed** (21 members × ~120
hourly steps × {`t_2m`, `tot_prec`} × {ctrl, perturb}). The adapter then
decompresses and concatenates the **entire** cube into RAM as **eager numpy** in
`_parse_grib_files`. The parsed dims are `(valid_time, member, values)` — note
**`valid_time` is the leading axis** and the spatial dimension is a **single
`values` dim of 283 876 unstructured ICON triangular-mesh cells**; there are **NO
`latitude`/`longitude` dims** (verified: `tests/fixtures/meteoswiss_nwp/icon_ch2_eps_202604231200`,
`tests/unit/adapters/test_meteoswiss_nwp_real.py:98-108`; ecCodes logs `"provides
no latitudes/longitudes for gridType='unstructured_grid'"`). The eager cube is
wrapped verbatim into `GriddedForecast(…, values=ds)` and handed downstream.

1. **Download is fine** — streamed to disk in 8 KiB chunks
   (`adapters/meteoswiss_nwp.py:553-557`). **But on the deployed worker the scratch
   path `/tmp/sapphire_nwp` is a 4 GiB RAM-backed `tmpfs`**
   (`docker-compose.yml`, `prefect-worker` volumes: `tmpfs … size: 4294967296`),
   so the 2.7 GB of compressed GRIB also sits in RAM — a compounding factor.
2. **HOTSPOT — eager parse (where the OOM actually occurs).** `_parse_grib_files`
   opens each GRIB message with `xr.open_dataset(p, engine="cfgrib",
   backend_kwargs={…})` and **no `chunks=`** (`adapters/meteoswiss_nwp.py:578`), so
   every array is eager numpy. The per-param `_combine_cfgrib_datasets`
   (`:156-231`) `xr.concat`s members then valid_times, and `xr.merge(datasets,
   compat="override")` (`:628`) builds the **full** multi-variable cube — peaking
   ≈2× during the concat/merge. Order-of-magnitude: 283 876 cells × 21 members ×
   ~120 valid_times × 2 vars × 4 B ≈ **~5.7 GB**, **~11 GB at the 2× concat/merge
   peak** (exact step count is runtime — see Risks), which exceeds the ~15.84 GiB
   Docker VM once the 2.7 GB tmpfs working set is added. **This eager
   materialization happens inside `fetch_forecasts` — BEFORE archive or extract is
   ever reached.** The eager cube is wrapped into `GriddedForecast(…, values=ds)`
   (`:380-384`; `types/weather.py:60-63`, `values: xr.Dataset`).
3. **Held alive through archive.** `ZarrNwpGridStore.archive` takes `ds =
   forecast.values` (`store/zarr_nwp_grid_store.py:105`) and writes
   `ds.to_zarr(…, encoding={… "chunks": (1, *ds[v].shape[1:]) …})` (`:106-125`).
   With `valid_time` leading, that encoding = `(1, 21, 283876)` — chunk-1 along
   `valid_time`, **full** along `member` and `values`. **A naively-lazy source
   (chunks `(1,1,283876)`) does NOT stream cleanly into this encoding — `to_zarr`
   RAISES** (see D4 / the blocker). The fix therefore requires an explicit
   source rechunk in `archive` (Phase 2).
4. **Extract is not reached on real data.** `ExactExtractGridExtractor.extract`
   calls `grid.rio.set_spatial_dims(x_dim="longitude", y_dim="latitude")`
   (`preprocessing/exact_extract_grid_extractor.py:95-97`), which requires
   `latitude`/`longitude` **dims**. Real ICON output has **none** (only the
   `values` mesh dim) → on real data the extractor would **error at
   `set_spatial_dims` before any memory pressure**, and in the dev repro the flow
   OOMs upstream at parse (step 2) before extract runs at all. **Extraction from
   the unstructured mesh is a SEPARATE, pre-existing concern — NOT the OOM, and
   NOT fixed (or blocked) by this plan.** See Risk 7 + Open Item E. (Note: per
   project memory, current v0 models consume **zero** NWP features, so this
   extraction gap is not yet on the operational critical path.)
5. **No worker `mem_limit`.** `prefect-worker` declares no `mem_limit` /
   `deploy.resources.limits.memory` (`docker-compose.yml`), so the cgroup cannot
   bound the subprocess — the host OOM killer takes the flow. A `mem_limit` does
   **not** make the failure graceful (cgroup OOM is still a SIGKILL of the worker);
   it **bounds the blast radius** to the container instead of the host (D7).

NWP-on task wiring lives in `flows/run_forecast_cycle.py` — `_fetch_nwp_task` body
(`:213` fetch, `:224` archive, `:260-266` extract, `:292` store) submitted at
`:651`. The adapter already supports a `max_files` cap
(`adapters/meteoswiss_nwp.py:256` ctor param, `:281` stored, `:504-513` honored in
`_fetch_grib_files`) but it is **not wired to config** — `config.toml`
`[adapters.weather_forecast]` (`:355-365`) exposes only `scratch_path`,
`archive_base_path`, STAC fields; there is no `max_files` knob, and the flow's
`_WeatherForecastAdapterConfig` (`:70-76`) does not carry one.

### Scope

- **In** (user choice: "core fix + guardrails folded in"): (1) **Core** — make the
  parsed cube **lazy/dask-backed** (open cfgrib with `chunks={}`) so the OOM-prone
  eager parse/merge becomes memory-bounded, output **byte-identical**; (2) **Archive
  rechunk** — in `ZarrNwpGridStore.archive`, rechunk the lazy source to match the
  on-disk encoding so `to_zarr` streams a small bounded number of `valid_time` slabs
  (tens–low-hundreds of MB) instead of raising the dask-chunk-overlap `ValueError`
  (THE BLOCKER); (3) **Guardrail A** —
  wire the existing `max_files` cap from config → adapter; (4) **Guardrail B** —
  add a worker `mem_limit` to `docker-compose.yml` to bound the blast radius
  (container, not host).
- **Out**: changing the STAC fetch/pagination logic (Plan 067); NWP bias-correction
  / post-processing; per-member parallel download; `task.map` of the forecast cycle
  (Phase-8 v0b remainder); any change to runoff-only mode (Plan 077); raising the
  Docker VM size (host config, not code); **fixing extraction-from-mesh** (separate
  pre-existing concern — Risk 7 / Open Item E); moving NWP scratch off `tmpfs`
  (sized follow-up — Open Item D / Risk 4).
- **Runtime consequence (disclosed):** after this fix the NWP-on flow still aborts
  at **extract** on real ICON mesh data (`set_spatial_dims`,
  `exact_extract_grid_extractor.py:95-97`) — 086 makes the path memory-safe and the
  archive valid, **NOT** NWP-on functional end-to-end (that is Open Item E). Because
  acceptance is synthetic-fixture unit tests, the milestone goes GREEN while a real
  NWP-on E2E run is still non-functional at extract (no longer an OOM).

---

## Decisions / ground truth (verified against the codebase + real GRIB fixtures, 2026-06-29)

| # | Decision | Evidence |
|---|---|---|
| D1 | **The parse is eager.** `xr.open_dataset(p, engine="cfgrib", backend_kwargs={filter_by_keys, indexpath:""})` is called with **no `chunks=`** → eager numpy. The combine (`xr.concat` × 2, `xr.merge`) materializes the full cube. **The OOM occurs here, in `fetch_forecasts`, before archive/extract.** | `adapters/meteoswiss_nwp.py:578` (open, no `chunks`); `_combine_cfgrib_datasets` `:156-231` (concat `:204-211`, `:224-231`); `xr.merge` `:628`. Empirically: parsing the fixture yields `.chunks is None`, dims `(valid_time, member, values)`. |
| D2 | **The eager cube is the shared object across stages.** `fetch_forecasts` returns `GriddedForecast(values=merged)`; `GriddedForecast.values: xr.Dataset` is passed by reference to `archive(forecast)` and `extract(grid=forecast.values)`. | `adapters/meteoswiss_nwp.py:380-384`; `types/weather.py:60-63`; flow archive `flows/run_forecast_cycle.py:224`, extract `:260-266`. |
| D3 | **Real parsed dims are `(valid_time, member, values)`, `valid_time` FIRST.** The spatial dim is a single unstructured-mesh `values` dim of **283 876** cells; there are **NO `latitude`/`longitude` dims** (ICON triangular icosahedral mesh; ecCodes exposes cells as one `values` dim and provides no lat/lon for `gridType='unstructured_grid'`). The existing synthetic test fixtures use a **different** layout (`member`-first, `latitude`/`longitude`) and therefore do NOT reproduce the real behaviour — locked tests must use the real dim order (D11, MAJOR-4 fix). | `tests/unit/adapters/test_meteoswiss_nwp_real.py:98-108` (`"values" in ds.dims`, `ds.sizes["values"] == 283876`); empirical parse: `precipitation.dims == ('valid_time','member','values')`. |
| D4 | **`to_zarr` does NOT stream cleanly from a naively-lazy source — it RAISES (THE BLOCKER).** `archive` builds `encoding={v: {"chunks": (1, *ds[v].shape[1:])}}` → with `valid_time` leading, `(1, 21, 283876)`. Opening cfgrib with `chunks={}` gives a lazy cube chunked **`(1, 1, 283876)`** (one GRIB message per file ⇒ one member, one step). Feeding that `(1,1,N)` dask source to `to_zarr` with the `(1,21,N)` encoding raises `ValueError: Specified Zarr chunks encoding['chunks']=(1, 21, …) … would overlap multiple Dask chunks … on axis 1`. **FIX:** in `archive`, immediately before building the encoding/`to_zarr`, rechunk the source so each var's dask chunks equal its on-disk encoding `(1, *shape[1:])` — i.e. **size 1 along the leading `valid_time` axis, full (`-1`) along all other dims**. On-disk layout/encoding is **unchanged**; `to_zarr` then streams a small bounded number of `valid_time` slabs — the default threaded scheduler computes several `(1, 21, N)` chunks concurrently, so the peak is ≈ (thread count) × one slab (one slab ≈ 21 × 283876 × 4 B × 2 vars ≈ **~48 MB**), i.e. **tens–low-hundreds of MB**, not the ~5.7 GB eager cube. **Empirically verified:** without the rechunk → `ValueError` overlap; with `ds.chunk({"valid_time":1,"member":-1,"values":-1})` → `to_zarr` succeeds. | `store/zarr_nwp_grid_store.py:105-125`; empirical to_zarr repro (both branches) against the real fixture. |
| D5 | **The extractor is a SEPARATE concern, not the OOM, and not fixed here.** On real ICON mesh data the extractor errors at `grid.rio.set_spatial_dims(x_dim="longitude", y_dim="latitude")` (`:95-97`) because there are no lat/lon **dims** — and in any case the dev repro OOMs upstream at parse before extract runs. There is **no mesh→lat/lon regrid step anywhere** between fetch and extract (confirmed: the only `set_spatial_dims` call in `src/` is the extractor's; no `regrid`/`interp`/`reproject`/`mesh` step in `adapters/`, `preprocessing/`, or `flows/`; `convert_raw_dataset` adds no lat/lon coords). The extractor's per-slice memory bound (one `.sel` → one slice; on mesh a 1-D `(283876,)` ≈ 1.1 MB vector, on a lat/lon grid a 2-D raster) is a genuine but **secondary** improvement, only realised on the lat/lon-grid path (e.g. a future regridded/Nepal v1 source). Phase 3 keeps that laziness improvement but **does NOT claim to fix mesh extraction**; the mesh gap is flagged as Risk 7 / Open Item E. | `preprocessing/exact_extract_grid_extractor.py:95-97, :110-134`; grep over `adapters/`, `preprocessing/`, `flows/` (no regrid/interp/mesh/reproject); `convert_raw_dataset` `:130-137` (no lat/lon added). |
| D6 | **`max_files` exists but is NOT config-wired.** The adapter ctor accepts `max_files: int | None = None` and `_fetch_grib_files` honors it (`>= max_files` → graceful stop, `nwp.fetch_cap_reached`). The flow constructs `MeteoSwissNwpAdapter(...)` **without** `max_files`, and `_WeatherForecastAdapterConfig` / `_load_weather_forecast_adapter_config` / `config.toml` carry no such field. Guardrail A is a 4-point plumbing edit (toml → loader → dataclass → ctor call) plus an `int|None` type-guard in the loader. | `adapters/meteoswiss_nwp.py:256, 281, 504-513`; flow ctor call `flows/run_forecast_cycle.py:502-510` (no `max_files`); dataclass `:70-76`; loader `:83-143`; `config.toml:355-365`. |
| D7 | **No worker `mem_limit` today, and a `mem_limit` bounds blast radius — it is NOT a graceful failure.** `prefect-worker` has `cap_drop`, `read_only`, `tmpfs`, `logging`, but no `mem_limit`. A container `mem_limit` makes the cgroup OOM-kill the worker (still a SIGKILL `-9`) instead of the host OOM killer taking an arbitrary host process — i.e. it **bounds the blast radius to the container**, it does NOT produce a graceful `nwp_fetch_failed_aborting`. The genuinely-graceful path is the size / `max_files` caps that raise `BudgetExceededError` **before** decompression (`adapters/meteoswiss_nwp.py:477-503`). **This is a compose change, NOT pytest-lockable** — its "test" is a manual/runbook check. | `docker-compose.yml` `prefect-worker` block (no memory limit); `adapters/meteoswiss_nwp.py:477-503` (pre-decompress budget raise). |
| D8 | **The deployed NWP scratch is RAM-backed `tmpfs` (4 GiB).** `prefect-worker` mounts `/tmp/sapphire_nwp` as `tmpfs … size: 4294967296`, so the 2.7 GB of staged compressed GRIB consumes RAM too. The lazy + archive-rechunk fix shrinks the **decompressed** peak to a few tens of MB, so **after the fix the tmpfs working set (~2.7 GB) becomes the dominant RAM driver**. Any `mem_limit` near 4 GiB therefore risks killing normal runs; the durable fix is moving scratch to the disk-backed `nwp_grids` volume (Open Item D / Risk 4). | `docker-compose.yml` `prefect-worker` `tmpfs` mount; `config.toml:365` `scratch_path="/tmp/sapphire_nwp"`. |
| D9 | **`dask` is ALREADY a dependency — no install-footprint change.** `pyproject.toml` declares `"dask[array]>=2024.1.0"`; `uv.lock` resolves `dask`. The core fix adds **no** new package. | `pyproject.toml:16`; `uv.lock:731, 3606, 3661`. |
| D10 | **The regression signal is BEHAVIORAL laziness + archive round-trip, not a live OOM.** Fast, deterministic, committed-fixture proxies (D3 fixtures, <1 s): (a) after parse `GriddedForecast.values` is dask-backed (`.chunks is not None`) — fails today, passes after; (b) archiving a **real-dim-order** lazy source round-trips via `load()` — fails today (the D4 `ValueError`), passes after the rechunk. The codebase has **no precedent for peak-RSS / memory assertions** — these are the lockable proxies. | xarray dask semantics; empirical D4 repro; existing tests assert values/shape, never RSS. |
| D11 | **Locked tests MUST use the real dim order, or they miss the blocker.** The existing synthetic fixtures (`test_zarr_nwp_grid_store.py:15-30` and `test_exact_extract_grid_extractor.py:25-56`) are `member`-first with `latitude`/`longitude` and stay **eager** (single numpy chunk), so `to_zarr` never hits the dask-chunk-overlap path — they would pass even on broken `main`. Task 6b must archive a **dask source with dims `(valid_time, member, values)` chunked `(1,1,N)`** and assert `archive → load()` round-trips (this catches the BLOCKER). (Task 6c is verify-only — the extractor doesn't change, so it is NOT a lockable fix-mode red anchor; see Task 6c.) | `tests/unit/store/test_zarr_nwp_grid_store.py:15-30`; `tests/unit/preprocessing/test_exact_extract_grid_extractor.py:25-56`. |

---

## The fix (four parts)

### Core (primary) — lazy/dask-backed cube at parse

Open cfgrib with **`chunks={}`** at `_parse_grib_files` (`:578`) so each parsed
message is dask-backed; keep `xr.concat` / `xr.merge` / `convert_raw_dataset` lazy
(verify no stray `.values`/`.load()`/`.compute()`). This removes the eager
parse/merge that OOMs (D1). The returned `GriddedForecast.values` is dask-backed
with chunks `(1,1,283876)`. Output (values, member count, units) **unchanged**.

`chunks={}` is committed (≡ `{"number":1}` here, since each file is one GRIB
message → both give `(1,1,N)`); neither aligns with the archive encoding, which is
exactly why the archive rechunk below is mandatory (D4, Open Item A).

### Archive rechunk (THE BLOCKER) — make `to_zarr` stream, not raise

In `ZarrNwpGridStore.archive` (`store/zarr_nwp_grid_store.py`), **before** building
the `encoding` and calling `to_zarr` (`:105-125`), rechunk the source so each var's
dask chunks equal its on-disk encoding `(1, *shape[1:])` — size 1 along the leading
`valid_time` axis, full (`-1`) elsewhere. Concretely for the real adapter output:
`ds = ds.chunk({"valid_time": 1, "member": -1, "values": -1})`. **On-disk
layout/encoding is unchanged** (still `zarr_format=2`, Plan 056); `to_zarr` now
streams a small bounded number of `valid_time` slabs (one slab ≈ 48 MB; peak ≈
thread-count × slab = tens–low-hundreds of MB, since the default threaded scheduler
computes several `(1, 21, N)` chunks concurrently) and releases them. Without this,
`to_zarr` raises `ValueError … would overlap multiple Dask chunks` (D4, empirically
verified).

> Implementation note: derive the leading axis from a **data variable's axis-0**,
> NOT from `ds.dims`. `xr.Dataset.dims` is a Frozen *mapping* whose iteration order
> is **not** the per-variable axis order, so `next(iter(ds.dims))` can return the
> wrong axis (empirically it returns `'member'`, while the per-var encoding
> `(1, *shape[1:])` chunks axis-0 = `valid_time` to 1 → `to_zarr` raises the SAME
> dask-chunk-overlap `ValueError`). Use the variable's own dim order:
> `lead = ds[next(iter(ds.data_vars))].dims[0]; ds = ds.chunk({lead: 1, **{d: -1 for d in ds.dims if d != lead}})`.
> Verified: `ds[var].dims[0]` is `valid_time` for the real ICON data and `member`
> for the synthetic fixture — in **both** cases exactly the axis the per-var
> encoding chunks to 1, so the rechunk is correct AND name-agnostic across layouts.
> (Acceptable simpler alternative: hard-code
> `ds.chunk({"valid_time": 1, "member": -1, "values": -1})` and state that the
> rechunk is coupled to the encoding's leading dim.) `ds.chunk({...})` **raises** if
> a key is absent, so the keys must match the actual dims (verified).

### Guardrail A — wire `max_files` into config

Add `max_files` to `[adapters.weather_forecast]` (`config.toml`), read it in
`_load_weather_forecast_adapter_config` with an `int|None` type-guard, carry it on
`_WeatherForecastAdapterConfig`, and pass it to the `MeteoSwissNwpAdapter(...)`
ctor (D6). Default **`None` / unlimited** (production behaviour); a conservative cap
belongs only in the mac-mini overlay (Open Item C). Hard-code the Core `chunks={}`
value (no config chunk knob — Open Item B). Document that the cap caps members·steps
(an operator escape hatch / sampled runs), NOT a correctness control.

### Guardrail B — worker `mem_limit` (compose; bounds blast radius)

Add a `mem_limit` (Compose v2 `mem_limit:` or `deploy.resources.limits.memory`) to
`prefect-worker` so an over-budget run is **cgroup-killed inside the container**
instead of the host OOM killer taking an arbitrary host process — **bounds the
blast radius, NOT a graceful failure** (D7). Recommended **6–8 GiB**: above the
4 GiB tmpfs cap + worker base (D8), below the ~15.84 GiB VM. The value cannot be
derived statically — confirm against a real NWP-on run (Open Item C / Risk 1).
**NOT pytest-lockable** — `docker compose config` parse + a manual NWP-on run only.

---

## Phases

### Phase 1 — Lazy dask-backed cube in the adapter (ROOT)

#### Task 1a — Open cfgrib with `chunks={}`; keep concat/merge/convert lazy

- **Scope**: In `adapters/meteoswiss_nwp.py`, add `chunks={}` to the
  `xr.open_dataset(engine="cfgrib")` call (`:578`) so each parsed message is
  dask-backed, and verify `_combine_cfgrib_datasets` (`:156-231`),
  `xr.merge(…compat="override")` (`:628`) and `convert_raw_dataset` (`:130-137`)
  preserve dask-backing (no hidden `.values`/`.load()`/`.compute()`). The returned
  `GriddedForecast.values` must be dask-backed (chunks `(1,1,283876)`). Out: any
  change to the GRIB filter, pagination, units, member-count gate, or output values.
- **Verification**: `uv run pytest tests/unit/adapters/test_meteoswiss_nwp.py
  tests/unit/adapters/test_meteoswiss_nwp_real.py` (the new Task 6a laziness
  assertion + all existing parse/units/member tests stay green);
  `uv run pyright src/sapphire_flow/adapters/meteoswiss_nwp.py`.
- **Exit gate**: after `fetch_forecasts`, `GriddedForecast.values` is dask-backed
  (every data var `.chunks is not None`); all existing adapter tests (units,
  member count, deaccumulation, parse-skip, real-fixture dims) pass unchanged; the
  parsed cube's values are numerically identical to the eager path (compare
  `.compute()`).

### Phase 2 — Streaming archive via source rechunk (CODE; depends on Phase 1; THE BLOCKER)

#### Task 2a — Rechunk the source in `archive` so `to_zarr` streams (not raises)

- **Scope**: In `ZarrNwpGridStore.archive` (`store/zarr_nwp_grid_store.py:105-125`),
  add a source rechunk immediately before building `encoding`/calling `to_zarr` so
  each var's dask chunks equal its on-disk encoding `(1, *shape[1:])` (size 1 along
  the leading `valid_time` dim, `-1` elsewhere; derive the leading axis from a data
  variable's axis-0 — `ds[var].dims[0]` — see implementation note). On-disk
  encoding/chunk shape and `zarr_format=2`
  (Plan 056) are **unchanged**. This is a **real code change** (NOT verify-only): a
  naively-lazy `(1,1,N)` source raises `ValueError` overlap without it (D4). Out:
  the versioned-swap / cleanup logic; on-disk chunk shape change.
- **Verification**: `uv run pytest tests/unit/store/test_zarr_nwp_grid_store.py`;
  `uv run pyright src/sapphire_flow/store/zarr_nwp_grid_store.py`.
- **Exit gate**: the locked Task 6b test — archiving a **dask source with real dim
  order `(valid_time, member, values)` chunked `(1,1,N)`** → `load()` round-trips
  value-equal — **fails pre-fix** (the D4 `ValueError`) and **passes post-fix**;
  the existing eager `test_round_trip` stays green.

### Phase 3 — Extractor laziness on the lat/lon-grid path (depends on Phase 1)

> **Scope reality (D5):** This phase does NOT fix mesh extraction (Risk 7 / Open
> Item E) and is NOT the OOM fix. It is a secondary laziness improvement realised
> only when the extractor receives a grid with `latitude`/`longitude` dims (e.g. a
> future regridded source). On real v0 ICON mesh data the extractor errors at
> `set_spatial_dims` regardless of laziness.

#### Task 3a — Extractor consumes a dask-backed grid one slice at a time

- **Scope**: Ensure `ExactExtractGridExtractor.extract`
  (`preprocessing/exact_extract_grid_extractor.py:110-134`) computes exactly one
  slice per `(param, member, valid_time)` from a dask-backed `grid` and releases it
  before the next — i.e. it does NOT call `.values`/`.compute()`/`.load()` on the
  **whole** cube. `grid[param].sel(...)` (`:117`) must stay lazy until
  `exact_extract(da_slice, …)` (`:121`) pulls the single slice. Out: the
  `set_spatial_dims`/mesh question (Open Item E); the row/skip/out-of-extent logic;
  the `member_id` / value math; output schema.
- **Verification**: `uv run pytest tests/unit/preprocessing/test_exact_extract_grid_extractor.py`;
  `uv run pyright src/sapphire_flow/preprocessing/exact_extract_grid_extractor.py`.
- **Exit gate**: extraction OUTPUT is unchanged vs the existing
  `test_dataframe_row_count` (`:186`) and `test_ensemble_members_preserved`
  (n_members=21) — identical row count and member set; the locked Task 6c
  incremental-materialization assertion (peak materialization = one slice, no
  whole-cube `.compute()`) passes against a dask-backed lat/lon grid; all existing
  extractor tests green.

### Phase 4 — Config-wired `max_files` cap (Guardrail A; parallel to Phase 1)

> Independent of the `chunks=`/rechunk change — touches the loader/dataclass/ctor
> wiring, not the parse or archive laziness — so it may run **in parallel with
> Phase 1** (no `depends_on`).

#### Task 4a — Plumb `max_files` from config → adapter

- **Scope (all four points + type-guard, D6)**: (1) add `max_files` to `config.toml`
  `[adapters.weather_forecast]` (commented/optional, default = unlimited =
  production behaviour); (2) read it in `_load_weather_forecast_adapter_config`
  (`flows/run_forecast_cycle.py:83-143`) with an `int|None` type-guard (raise
  `ConfigurationError` on a non-int, non-None value, mirroring the existing
  `enabled` bool guard); (3) add the field to `_WeatherForecastAdapterConfig`
  (`:70-76`); (4) pass it to `MeteoSwissNwpAdapter(...)` (`:502-510`). Preserve the
  config-overlay precedence (mac-mini overlay can set a cap). Out: changing the
  adapter's existing `max_files` honoring logic (`:504-513`); STAC fields; any chunk
  knob (Open Item B — hard-coded, no config knob).
- **Verification**: `uv run pytest tests/unit/flows/test_run_forecast_cycle.py
  tests/unit/adapters/test_meteoswiss_nwp.py -k "max_files or config"`;
  `uv run pyright src/sapphire_flow/flows/run_forecast_cycle.py`.
- **Exit gate**: a config with `[adapters.weather_forecast].max_files = N`
  constructs an adapter whose cap is `N` (locked Task 6d); absent/None → unlimited
  (unchanged production behaviour); a non-int value raises `ConfigurationError`; an
  overlay override wins; ruff + pyright clean.

### Phase 5 — Worker `mem_limit` (Guardrail B; compose-only, independent)

#### Task 5a — Add a `mem_limit` to `prefect-worker`

- **Scope**: Add a `mem_limit` (or `deploy.resources.limits.memory`) to the
  `prefect-worker` service in `docker-compose.yml`, **6–8 GiB** (above the 4 GiB
  tmpfs cap + worker base, below the ~15.84 GiB VM; D7/D8). Document the value +
  rationale inline, including that it **bounds blast radius, not graceful failure**
  (D7) and that a value near 4 GiB risks killing normal runs because of the tmpfs
  working set (D8 / Open Item D). Out: changing the `tmpfs` size, other services,
  the dev/macmini overlays (note if they need a matching limit).
- **Verification**: **NOT pytest-lockable** (D7). `docker compose -f
  docker-compose.yml config` parses clean; a runbook note records the manual check
  (NWP-on run is cgroup-killed inside the container — not host OOM — if it ever
  exceeds the limit). Flag in the plan that no automated test covers this.
- **Exit gate**: `docker compose config` validates; the `mem_limit` is present with
  a documented value + rationale; the limitation (manual verification only, blast-
  radius-not-graceful) is recorded in the Affected files / runbook note.

### Phase 6 — Locked tests + full verification gate (depends on Phases 1-5)

Per WF2 fix-mode, the LOCKED tests (6a-6d) are authored BEFORE implementation
(Claude writes them; Codex makes them pass). They assert behavioral laziness +
archive round-trip + non-regression — no real OOM / large data needed (D10).

#### Task 6a — Locked adapter laziness test

- **Scope**: In `tests/unit/adapters/test_meteoswiss_nwp.py` (using the committed
  GRIB fixtures / parse path), assert that after `fetch_forecasts` (or
  `_parse_grib_files`) the returned `GriddedForecast.values` is **dask-backed**:
  every data var has `.chunks is not None`. FAILS on `main` (eager numpy →
  `.chunks is None`), PASSES after Phase 1 — the laziness regression anchor.
- **Verification**: `uv run pytest tests/unit/adapters/test_meteoswiss_nwp.py -k lazy`.
- **Exit gate**: the assertion fails pre-fix, passes post-fix; no other adapter
  test regresses.

#### Task 6b — Locked archive round-trip on REAL dim order (THE BLOCKER anchor)

- **Scope**: In `tests/unit/store/test_zarr_nwp_grid_store.py`, archive a
  **dask-backed `GriddedForecast` with the real dim order `(valid_time, member,
  values)` chunked `(1, 1, N)`** (a small synthetic e.g. `valid_time=2, member=21,
  values=8`, built eager then `.chunk({"valid_time":1,"member":-1,"values":-1})`
  **inverted** to `(1,1,N)` via `.chunk({"valid_time":1,"member":1,"values":-1})`
  to reproduce the naive-lazy source) and assert `archive → load()` round-trips
  value-equal. This **FAILS on `main`** with the D4 dask-chunk-overlap `ValueError`
  and **PASSES** after the Phase 2 rechunk — it is the BLOCKER anchor (D11). Do NOT
  reuse the existing `member`-first synthetic fixture for this case (it stays eager
  and would pass on broken `main`).
- **Verification**: `uv run pytest tests/unit/store/test_zarr_nwp_grid_store.py`.
- **Exit gate**: round-trip of the real-dim-order chunked source is value-equal;
  fails pre-fix (overlap `ValueError`), passes post-fix; existing eager
  `test_round_trip` stays green.

#### Task 6c — Extractor non-regression (VERIFY-ONLY — NOT a locked fix-mode red anchor)

- **NOTE (WF2 fix-mode):** The extractor requires **no code change** in 086 (Phase 3
  is verify-only): fed a dask-backed lat/lon grid, the existing `.sel` loop already
  streams one 2-D slice at a time on `main`. So an "extractor streams" test is
  **green on `main`** and therefore CANNOT be a fix-mode red anchor (every locked
  test must be red-on-main → green-after). An instrumented peak-materialization
  test here was correctly rejected by the cross-vendor test-review as not-sound.
  **Do NOT lock an extractor test.** The milestone's locked red anchors are **6a**
  (parse laziness), **6b** (archive round-trip), **6d** (config cap) only.
- **Scope**: Extractor OUTPUT non-regression (identical row count / ensemble members
  on the lat/lon-grid path) is already covered by the EXISTING extractor tests
  (`test_dataframe_row_count` `:186`, `test_ensemble_members_preserved` n_members=21),
  which run in the milestone acceptance gate (Task 6e). Mesh extraction is out of
  scope (Open Item E).
- **Verification**: the existing `tests/unit/preprocessing/test_exact_extract_grid_extractor.py`
  stays green in the full suite (Task 6e) — no new locked test added here.
- **Exit gate**: peak-materialization-is-one-slice assertion passes against a
  dask-backed grid; row count + member set identical to the eager path.

#### Task 6d — Locked config-cap test

- **Scope**: A test asserting `[adapters.weather_forecast].max_files = N` in config
  flows into the constructed `MeteoSwissNwpAdapter` (cap == N), absent → None
  (unlimited), and a non-int value raises `ConfigurationError`. Lives with the
  forecast-cycle / adapter-config tests. May reuse `FakeWeatherForecastSource`
  (`tests/fakes/fake_adapters.py:26-41`) only where the real adapter is not under
  test; the cap assertion targets the real adapter ctor.
- **Verification**: `uv run pytest tests/unit/flows/test_run_forecast_cycle.py -k "max_files or config"`.
- **Exit gate**: config `max_files` round-trips to the adapter cap; default
  unlimited; non-int raises.

#### Task 6e — Full verification gate

- **Scope**: Full-suite + typecheck + lint gate per `docs/workflow.md` Task Exit
  Gate. Confirm the parse is lazy, the archive round-trips on real dim order, the
  extractor output is unchanged, and the compose file parses.
- **Verification**: `uv run pytest`; `uv run ruff check src/ tests/`;
  `uv run ruff format --check src/ tests/`; `uv run pyright src/`;
  `docker compose -f docker-compose.yml config` (compose validity).
- **Exit gate**: all green; affected docs updated; no production file outside the
  Affected-files list changed.

---

## WF2 milestone (for `vision-build`)

**Recommended invocation mode: FIX-MODE (`issue` present)**, NOT new-capability.
Rationale: there are two concrete, fast, deterministic **current-vs-expected
deltas** — (1) the parsed cube is eager today (`.chunks is None`) and must be lazy
(`.chunks is not None`); (2) archiving a real-dim-order lazy source RAISES today
(the D4 `ValueError`) and must round-trip after the rechunk — both lockable by
committed-fixture regression tests. This matches Plan 085's fix-mode precedent. The
`issue` is a **laziness-property + archive-round-trip** repro, NOT a live OOM (a
real OOM needs real GB and is non-deterministic, D10). If the harness prefers Claude
to author acceptance tests without an `issue` field, the fallback is a
new-capability milestone with the same `acceptanceCriteria[]`.

```text
milestone:
  id: nwp-memory-bounded-streaming
  title: NWP forecast-cycle is memory-bounded (lazy/dask) — fixes the OOM
  goal: >
    Make the ICON-CH2-EPS NWP path stream a lazy/dask-backed grid cube through
    parse -> archive so the eager parse/merge that OOMs is memory-bounded
    (decompressed peak ~tens of MB, not the full ~5.7 GB / ~11 GB-peak
    valid_time x member x values cube), eliminating the OOM kill, with
    byte-identical output; rechunk the source in archive so to_zarr streams a small
    bounded number of valid_time slabs (tens-low-hundreds of MB) instead of raising
    a dask-chunk-overlap ValueError; wire the
    existing max_files cap into config; add a worker mem_limit to bound blast
    radius. Extraction-from-mesh is a SEPARATE pre-existing concern, out of scope.
  issue:
    Repro (laziness property + archive round-trip — a live OOM needs real GB and
    is non-deterministic):
      (1) parse one ICON-CH2-EPS cycle via the MeteoSwiss adapter (committed
          fixture tests/fixtures/meteoswiss_nwp/icon_ch2_eps_202604231200);
          inspect GriddedForecast.values.
      (2) archive a dask-backed GriddedForecast with REAL dim order
          (valid_time, member, values) chunked (1,1,N) via ZarrNwpGridStore.
    Expected:
      - GriddedForecast.values is dask-backed (every data var .chunks is not None).
      - ZarrNwpGridStore.archive rechunks the source to (1, *shape[1:]) and
        to_zarr streams a small bounded number of valid_time slabs (tens-low-
        hundreds of MB); archive -> load() round-trips value-equal.
      - extraction OUTPUT (row count, ensemble members) is unchanged on the
        lat/lon-grid path.
    Actual (current):
      - _parse_grib_files calls xr.open_dataset(engine="cfgrib") with NO chunks=
        (meteoswiss_nwp.py:578), so values is eager numpy (.chunks is None);
        xr.merge (:628) builds the full cube eagerly -> ~5.7 GB (~11 GB at the
        concat/merge peak) on a ~15.84 GiB VM (plus 2.7 GB tmpfs) -> SIGKILL -9 /
        OOMKilled at PARSE, before archive/extract (Plan 084 Phase 5).
      - A naively-lazy (1,1,N) source fed to archive's (1,21,N) encoding RAISES
        ValueError "would overlap multiple Dask chunks" (the blocker the rechunk
        fixes).
  acceptanceCriteria:
    - After parse, GriddedForecast.values is dask-backed: every data var
      .chunks is not None (fails on main, passes after; Task 6a anchor).
    - The cfgrib open passes chunks={}; xr.concat/xr.merge/convert_raw_dataset
      preserve dask-backing (no hidden .values/.load()/.compute()).  [Task 6a]
    - ZarrNwpGridStore.archive rechunks the source to (1, *shape[1:]); archiving a
      dask source with REAL dim order (valid_time, member, values) chunked (1,1,N)
      round-trips value-equal via load() -- this FAILS on main with the dask-chunk
      -overlap ValueError and passes after (would have caught the blocker).
      On-disk encoding/chunks and zarr_format=2 unchanged.  [Task 6b]
    - (NOT a locked test — VERIFY-ONLY, Task 6c) The extractor does NOT change in
      086 (it already streams a lazy lat/lon grid one slice at a time on main), so
      no extractor red anchor is lockable in fix-mode. Extractor OUTPUT
      non-regression (row count, ensemble members) is covered by the EXISTING
      extractor tests passing in the full-suite acceptance gate (below). Mesh
      extraction is out of scope (Open Item E).
    - max_files flows from [adapters.weather_forecast].max_files in config into the
      constructed MeteoSwissNwpAdapter (cap honored); absent -> None (unlimited);
      non-int -> ConfigurationError; config-overlay override wins.  [Task 6d]
    - Worker mem_limit (6-8 GiB) added to docker-compose.yml prefect-worker (bounds
      blast radius, NOT graceful failure; manual/runbook verification only -- NOT
      pytest-lockable); docker compose config validates.  [Task 5a]
    - Locked tests (6a-6d) authored first and passing; full suite + ruff + pyright
      green; no new dependency added (dask already present).  [Task 6e]
```

---

## Risks / unknowns

1. **Exact ICON-CH2-EPS step count is runtime.** The ~5.7 GB / ~11 GB-peak
   decompressed estimate uses 283 876 mesh cells × 21 members × ~120 valid_times ×
   2 vars × 4 B; the cell count and member count are fixture-verified, the step
   count is runtime. The fix is correct regardless (laziness bounds the
   decompressed peak to a small number of concurrent slabs), but the **`mem_limit`
   value (Phase 5)** must be
   tuned against a real run (Open Item C), confirming headroom above the 2.7 GB
   tmpfs working set (D8).
2. **dask ↔ cfgrib ↔ zarr-python 3 interaction (Plan 056).** The fix assumes
   `chunks={}` on the cfgrib backend yields dask arrays that survive `xr.concat` /
   `xr.merge` / `convert_raw_dataset` and, after the Phase-2 rechunk, stream cleanly
   through `to_zarr(zarr_format=2)` under zarr-python 3 + numcodecs 0.16.
   **Empirically validated on the real fixture** (parse stays lazy; rechunked
   `to_zarr` succeeds; un-rechunked raises) — but a stray eager op in a future
   refactor of the combine path would silently re-materialize the cube; Phase 1 +
   Task 6a guard it.
3. **dask is ALREADY a dependency (D9).** `pyproject.toml:16` `"dask[array]>=2024.1.0"`.
   Flagged so it is not silently removed; nothing to add.
4. **tmpfs RAM pressure persists and becomes the dominant driver (D8).** The fix
   shrinks the decompressed peak to ~tens of MB, so the **2.7 GB compressed GRIB in
   the 4 GiB RAM-backed tmpfs** is now the largest RAM consumer. **Elevated from a
   footnote to a sized follow-up — Open Item D**: move NWP scratch to the
   disk-backed `nwp_grids` volume (or shrink tmpfs / add `max_files`). Until then, a
   `mem_limit` near 4 GiB risks killing normal runs — size it 6–8 GiB (Phase 5).
5. **`exact_extract` eagerly reads its 2D/1D slice input.** Acceptable and intended
   (one bounded slice); Task 6c asserts only that the **whole cube** is never
   materialized.
6. **No RSS-assertion precedent (D10).** Locked tests assert laziness (`.chunks`),
   archive round-trip, and output-equality as the memory-bounded proxy. Recommend
   one real-data smoke run on the dev host as a manual post-merge check (mirrors
   Plan 084's validation-run table), not a CI gate.
7. **Extraction from the unstructured ICON mesh is broken — SEPARATE pre-existing
   concern, NOT fixed here.** `ExactExtractGridExtractor` requires
   `latitude`/`longitude` **dims** (`set_spatial_dims`, `:95-97`); real ICON output
   has only the `values` mesh dim, so extraction errors before any memory work, and
   there is no regrid step between fetch and extract (D5). This plan deliberately
   does NOT fix it (the OOM is upstream at parse; v0 models consume zero NWP
   features today). **Track separately — Open Item E.** This plan must not claim to
   deliver working end-to-end mesh extraction. **Concrete runtime consequence:**
   after 086 a real NWP-on forecast-cycle will parse (OK) → archive (OK) → then
   raise at `set_spatial_dims` (extract), and because acceptance is synthetic-fixture
   unit tests the milestone goes GREEN while real NWP-on E2E is still non-functional
   (now at extract, not OOM). 086 makes the path memory-safe and the archive valid,
   NOT NWP-on functional end-to-end (Open Item E).
   **UPDATE: Open Item E is ADDRESSED by Plan 087** (`MeshBasinExtractor` +
   `values`-dim coord-attach + `grid_extractor` selector) — mesh extraction is now
   functional; 086 and 087 together deliver the NWP-on E2E path.

---

## Open items / resolved decisions

**Resolved (folded into the plan as settled decisions):**

- **A — `chunks=` value**: **`chunks={}`** at the cfgrib open (≡ `{"number":1}`
  here; both give `(1,1,N)`), **plus the mandatory archive rechunk** to
  `(1, *shape[1:])` (D4 / Phase 2). Neither open value aligns with the `(1,21,N)`
  encoding, so the rechunk is what makes `to_zarr` valid — not the open value.
- **B — chunk knob exposure**: **hard-code** the `chunks={}` value now; no config
  chunk knob (add one only if a deployment ever needs to tune it).
- **C — `max_files` default**: **`None` / unlimited** committed base default
  (production-safe); a conservative cap belongs **only in the mac-mini overlay**.
  The 4-point plumbing (`config.toml:355-365` → `run_forecast_cycle.py:83-143` →
  `_WeatherForecastAdapterConfig:70-76` → ctor `:502-510`) is sound; add an
  `int|None` type-guard in the loader.

**Open (require a real run / follow-up, do NOT block this plan):**

- **C (cont.) — `mem_limit` value**: cannot be derived statically. Recommend
  **6–8 GiB** (above the 4 GiB tmpfs cap + worker base, below the ~15.84 GiB VM);
  verification is `docker compose config` parse **+ a manual NWP-on run only**.
  Confirm the dev/macmini overlays inherit or override it.
- **D — move NWP scratch off `tmpfs`**: the durable fix for the residual ~2.7 GB
  RAM working set (Risk 4 / D8). Sized follow-up: point `scratch_path` at the
  disk-backed `nwp_grids` volume (or shrink the tmpfs). Tracked here so a
  `mem_limit` near 4 GiB is not set without it.
- **E — extraction from the ICON unstructured mesh** (Risk 7 / D5): the extractor
  cannot consume the `values`-mesh layout (needs lat/lon dims; no regrid step
  exists). A SEPARATE, pre-existing gap — needs its own plan (mesh→lat/lon regrid,
  or a mesh-aware extractor). Out of scope here; flagged so this plan does not over-
  claim.

---

## Dependency graph

```json
{
  "phases": [
    {
      "id": "phase-1-lazy-cube",
      "tasks": ["1a"],
      "parallel": false,
      "note": "ROOT: add chunks={} to the cfgrib open so GriddedForecast.values is dask-backed (chunks (1,1,N)); verify combine/merge/convert stay lazy. Archive + extractor build on this."
    },
    {
      "id": "phase-2-streaming-archive",
      "tasks": ["2a"],
      "parallel": false,
      "depends_on": ["phase-1-lazy-cube"],
      "note": "THE BLOCKER: rechunk the source in archive to (1, *shape[1:]) (leading axis from a data var's axis-0, ds[var].dims[0]) so to_zarr streams a small bounded number of valid_time slabs instead of raising the dask-chunk-overlap ValueError. CODE change; existing encoding/chunks + zarr_format=2 (Plan 056) unchanged. Needs phase-1's lazy source."
    },
    {
      "id": "phase-3-streaming-extractor",
      "tasks": ["3a"],
      "parallel": false,
      "depends_on": ["phase-1-lazy-cube"],
      "note": "SECONDARY (lat/lon-grid path only): extractor computes one slice at a time from a dask-backed grid; OUTPUT unchanged. Does NOT fix mesh extraction (Open Item E). Sibling of phase-2 (both depend only on phase-1)."
    },
    {
      "id": "phase-4-config-cap",
      "tasks": ["4a"],
      "parallel": false,
      "note": "Guardrail A: wire existing max_files (D6) through config.toml -> loader (int|None guard) -> _WeatherForecastAdapterConfig -> ctor. Single task. Independent of the chunks=/rechunk change -> may run cross-phase IN PARALLEL with phase-1 (no depends_on)."
    },
    {
      "id": "phase-5-worker-memlimit",
      "tasks": ["5a"],
      "parallel": false,
      "note": "Guardrail B: compose-only mem_limit (6-8 GiB, bounds blast radius not graceful failure); single task, independent of the code phases (no depends_on). NOT pytest-lockable (D7)."
    },
    {
      "id": "phase-6-locked-tests-and-gate",
      "tasks": ["6a", "6b", "6c", "6d", "6e"],
      "parallel": false,
      "depends_on": [
        "phase-1-lazy-cube",
        "phase-2-streaming-archive",
        "phase-3-streaming-extractor",
        "phase-4-config-cap",
        "phase-5-worker-memlimit"
      ],
      "note": "Per WF2 fix-mode the locked tests (6a-6d) are AUTHORED before implementation; 6a is the laziness anchor, 6b is the archive-round-trip BLOCKER anchor (real dim order); 6e is the full verification gate."
    }
  ]
}
```

---

## Affected files

- `src/sapphire_flow/adapters/meteoswiss_nwp.py` — **Core (Phase 1)**: add
  `chunks={}` to the `xr.open_dataset(engine="cfgrib")` call (`:578`) so the parsed
  cube is dask-backed; verify `_combine_cfgrib_datasets` / `xr.merge` (`:628`) /
  `convert_raw_dataset` (`:130-137`) preserve laziness. Guardrail A: accept the
  config-supplied `max_files` at the ctor call site (param already exists, D6) — no
  logic change to `:504-513`.
- `src/sapphire_flow/store/zarr_nwp_grid_store.py` — **CODE (Phase 2, THE
  BLOCKER)**: rechunk `ds` to `(1, *shape[1:])` (leading `valid_time` → 1, rest →
  `-1`, leading axis derived from a data variable's axis-0 `ds[var].dims[0]`)
  immediately before the `encoding`/`to_zarr` block
  (`:105-125`) so `to_zarr` streams instead of raising the dask-chunk-overlap
  `ValueError`; on-disk encoding/chunks/`zarr_format=2` unchanged.
- `src/sapphire_flow/preprocessing/exact_extract_grid_extractor.py` — Phase 3
  (secondary, lat/lon-grid path): ensure `grid[param].sel(...)` stays lazy until
  `exact_extract` pulls one slice; output math unchanged. Does NOT touch the
  `set_spatial_dims` mesh gap (Open Item E).
- `src/sapphire_flow/flows/run_forecast_cycle.py` — Guardrail A: add `max_files` to
  `_WeatherForecastAdapterConfig` (`:70-76`), read it (with an `int|None`
  type-guard) in `_load_weather_forecast_adapter_config` (`:83-143`), pass it to the
  `MeteoSwissNwpAdapter(...)` ctor (`:502-510`).
- `config.toml` — Guardrail A: add `max_files` under `[adapters.weather_forecast]`
  (`:355-365`), commented/optional, default unlimited.
- `docker-compose.yml` — Guardrail B (Phase 5): add a 6–8 GiB `mem_limit` to
  `prefect-worker` with an inline rationale (bounds blast radius, not graceful;
  sized above the tmpfs working set). **NOT pytest-lockable (D7)** — manual/runbook
  verification only.
- `tests/unit/adapters/test_meteoswiss_nwp.py` — locked laziness test (Task 6a) +
  config-cap test (Task 6d, if the adapter-ctor assertion lives here).
- `tests/unit/store/test_zarr_nwp_grid_store.py` — locked **real-dim-order**
  archive-round-trip test (Task 6b, the BLOCKER anchor); do NOT reuse the existing
  `member`-first eager fixture for it.
- `tests/unit/preprocessing/test_exact_extract_grid_extractor.py` — locked
  instrumented peak-materialization + non-regression test (Task 6c); add a
  chunked/instrumented variant of `_make_grid` (`:25-56`).
- `tests/unit/flows/test_run_forecast_cycle.py` — config `max_files` plumbing test
  (Task 6d), incl. the non-int → `ConfigurationError` case.
- `tests/fakes/fake_adapters.py` — only if the cap/laziness fakes need a chunked
  `GriddedForecast` helper (`FakeWeatherForecastSource` `:26-41`); avoid if the real
  adapter path covers it.
- `docs/spec/config-reference.toml` — document the new `max_files` field if the
  reference enumerates `[adapters.weather_forecast]`.
- `docs/standards/cicd.md` and/or `docs/standards/orchestration.md` — note the
  `prefect-worker` `mem_limit` (blast-radius bound) and the NWP memory-bounding
  behaviour if either pins the worker resource model.
- `docs/plans/084-dev-deployment-validation-2-station-runoff.md` — cross-reference:
  mark Finding NWP-OOM as addressed by Plan 086 (one-line pointer); note that
  mesh-extraction (Open Item E) remains open.
- `docs/plans/086-nwp-memory-bounded-streaming.md` (this plan),
  `docs/plans/README.md` (index entry).
- No other production files.
```
