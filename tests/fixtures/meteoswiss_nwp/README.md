# MeteoSwiss NWP fixtures

Real ICON-CH2-EPS GRIB2 messages used by the integration tests in
`tests/unit/adapters/test_meteoswiss_nwp_real.py` to exercise cfgrib + xarray
semantics that mock fakes silently mask.

## Contents

`icon_ch2_eps_202604231200/` — six files from the 2026-04-23T12:00Z cycle:

| File                                                        | Variant | Step | Var       | Size   |
|-------------------------------------------------------------|---------|------|-----------|--------|
| `icon-ch2-eps-202604231200-0-t_2m-ctrl.grib2`               | ctrl    | 0    | t_2m      | 568 KB |
| `icon-ch2-eps-202604231200-0-t_2m-perturb.grib2`            | perturb | 0    | t_2m      | 11 MB  |
| `icon-ch2-eps-202604231200-0-tot_prec-ctrl.grib2`           | ctrl    | 0    | tot_prec  | 199 B  |
| `icon-ch2-eps-202604231200-0-tot_prec-perturb.grib2`        | perturb | 0    | tot_prec  | 4 KB   |
| `icon-ch2-eps-202604231200-1-t_2m-ctrl.grib2`               | ctrl    | 1    | t_2m      | 568 KB |
| `icon-ch2-eps-202604231200-1-tot_prec-ctrl.grib2`           | ctrl    | 1    | tot_prec  | 568 KB |

Total: ~12 MB.

The set covers:
- ctrl (scalar `number` coord, value=0) and perturb (1-D `number` of length 20,
  values 1..20) — i.e. the 21-member ensemble split MeteoSwiss uses;
- step 0 and step 1 for ctrl (multi-step concat along `valid_time`);
- two variables (`t_2m`, `tot_prec`) with different `typeOfLevel`
  (`heightAboveGround` vs `surface`) exercising multi-variable `xr.merge`.

## Source & licence

Captured from <https://data.geo.admin.ch/api/stac/v1> collection
`ch.meteoschweiz.ogd-forecasting-icon-ch2` on 2026-04-23. Published by the
Federal Office of Meteorology and Climatology MeteoSwiss under **CC-BY 4.0**
(Open Government Data policy). Attribution: MeteoSwiss — ICON-CH2-EPS.

The files were downloaded by the SAPPHIRE Flow prefect-worker during a routine
forecast cycle (`/tmp/sapphire_nwp/20260423T1200/`) and copied verbatim; no
resampling or re-encoding was performed.

## Why these and not fakes

Prior xarray/cfgrib fixes in v0.1.405–v0.1.411 each broke on real data because
the test suite only used `MagicMock`/`DatasetFake` stubs. Real fixtures catch
cfgrib surprises (e.g. `filter_by_keys`-vs-`indexpath=""` interaction,
unstructured grids without lat/lon, scalar-vs-vector `number` coord on the
same variable across different GRIB messages) that mocks cannot replicate.

## Handling

- `.idx` sidecar files must **not** be committed — see `.gitignore`.
- Tests should use `engine="cfgrib"` and real `xr.open_dataset` calls (no mocks).
- When MeteoSwiss retention cycles the cycle out of the live STAC catalogue
  these fixtures remain valid — they are snapshots.
