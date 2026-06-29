# ICON-CH2-EPS static mesh-coordinate asset (`icon_ch2_eps_grid.npz`)

`icon_ch2_eps_grid.npz` provides the per-cell coordinates of the ICON-CH2-EPS
unstructured triangular icosahedral mesh, derived once from the MeteoSwiss OGD
horizontal-constants asset. Three float32 arrays, each length **283 876**:

| key    | meaning                         | empirical range          |
|--------|---------------------------------|--------------------------|
| `tlat` | cell-centre latitude (deg)      | [42.0786, 50.4792]       |
| `tlon` | cell-centre longitude (deg)     | [-0.7691, 17.6776] (already [-180, 180], negatives present) |
| `h`    | orography / surface height (m)  | [-5.1356, 4205.3644]     |

The arrays span the **full ICON-CH2 model domain** (much wider than the Swiss
band lat ~45–48 / lon ~5–11). Loaded at runtime via
`importlib.resources.files("sapphire_flow.data") / "icon_ch2_eps_grid.npz"`;
no GRIB/ecCodes is read at runtime.

## Source / licence

Source: MeteoSwiss / Federal Office of Meteorology and Climatology MeteoSwiss,
Open Government Data, CC-BY 4.0. Derived from the collection-level static asset
`horizontal_constants_icon-ch2-eps.grib2` on STAC collection
`ch.meteoschweiz.ogd-forecasting-icon-ch2`
(`https://data.geo.admin.ch/api/stac/v1`). Attribution retained.

## Ordering invariant (`uuidOfHGrid`)

The cell ordering of `tlat`/`tlon`/`h` matches the `values` dimension of the
forecast GRIBs **by construction**, guaranteed by ICON's definitive horizontal-grid
identity `uuidOfHGrid == bbbd5a09855499243c7a4aa4c8762920`. The mesh is fixed
across cycles, so these coordinates never change.

cfgrib **drops** `uuidOfHGrid` from the runtime cube, so the ordering
correspondence cannot be self-verified at runtime — it is pinned at
**regeneration time**: the recipe asserts the UUID and **refuses to write** on
mismatch. A future ICON grid revision (new UUID) therefore fails LOUDLY at
regeneration instead of silently corrupting every basin mean.

## Regeneration recipe

```bash
uv run python scripts/regenerate_icon_grid_asset.py \
    path/to/horizontal_constants_icon-ch2-eps.grib2
```

The script (`scripts/regenerate_icon_grid_asset.py`) parses the GRIB, asserts
`uuidOfHGrid == bbbd5a09855499243c7a4aa4c8762920` and
`numberOfDataPoints == 283876`, extracts `tlat`/`tlon`/`h` as float32, and writes
this `.npz` via `np.savez_compressed`. It refuses to write on any mismatch.
