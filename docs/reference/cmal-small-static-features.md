# `cmal_small` — declared static features (vendored)

**78 static features**, read from `cmal_small/config.yaml` in the owner's model tree
(`2025-01-BARHKH/models/global/cmal_small/`, dated 2026-08-31). That file is **not** in this repo,
so this list is vendored to make the model's contract reviewable without it — an independent review
of the alias change could confirm the mechanism but explicitly could not ratify the 78-name
denominator.

## Resolution against the `caravan:` namespace

Resolved by the rule in `services/caravan_statics.py`: `caravan:` + (`CARAVAN_ALIAS[X]` if aliased,
else `X`), against the **216** `caravan:` keys imported onto all 148 mac-mini basins by Plan 188 T4
(2026-09-03).

**78 of 78 resolve.**

🪤 Values are **percent (0-100), not a 0-1 fraction**, despite aquacast's `*_fraction` naming.
The pinned aquacast revision declares them `percent`, and nothing in this pipeline rescales them.

| declared name | resolves to `caravan:` | via | present in DB |
|---|---|---|---|
| `area` | `area` | direct | yes |
| `slope` | `slp_dg_sav` | alias | yes |
| `stream_gradient` | `sgr_dk_sav` | alias | yes |
| `lake_fraction` | `lka_pc_sse` | alias | yes |
| `air_temperature` | `tmp_dc_syr` | alias | yes |
| `precip_annual` | `pre_mm_syr` | alias | yes |
| `pet_annual` | `pet_mm_syr` | alias | yes |
| `aet_annual` | `aet_mm_syr` | alias | yes |
| `aridity_index` | `ari_ix_sav` | alias | yes |
| `climate_moisture_index` | `cmi_ix_syr` | alias | yes |
| `snow_cover` | `snw_pc_syr` | alias | yes |
| `snow_cover_max` | `snw_pc_smx` | alias | yes |
| `glacier_fraction` | `gla_pc_sse` | alias | yes |
| `cropland_fraction` | `crp_pc_sse` | alias | yes |
| `pasture_fraction` | `pst_pc_sse` | alias | yes |
| `clay_fraction` | `cly_pc_sav` | alias | yes |
| `silt_fraction` | `slt_pc_sav` | alias | yes |
| `sand_fraction` | `snd_pc_sav` | alias | yes |
| `soil_organic_carbon` | `soc_th_sav` | alias | yes |
| `soil_water_content` | `swc_pc_syr` | alias | yes |
| `karst_fraction` | `kar_pc_sse` | alias | yes |
| `irrigated_fraction` | `ire_pc_sse` | alias | yes |
| `p_mean` | `p_mean` | direct | yes |
| `frac_snow` | `frac_snow` | direct | yes |
| `high_prec_freq` | `high_prec_freq` | direct | yes |
| `high_prec_dur` | `high_prec_dur` | direct | yes |
| `low_prec_freq` | `low_prec_freq` | direct | yes |
| `low_prec_dur` | `low_prec_dur` | direct | yes |
| `glc_pc_s01` | `glc_pc_s01` | direct | yes |
| `glc_pc_s02` | `glc_pc_s02` | direct | yes |
| `glc_pc_s03` | `glc_pc_s03` | direct | yes |
| `glc_pc_s04` | `glc_pc_s04` | direct | yes |
| `glc_pc_s05` | `glc_pc_s05` | direct | yes |
| `glc_pc_s06` | `glc_pc_s06` | direct | yes |
| `glc_pc_s07` | `glc_pc_s07` | direct | yes |
| `glc_pc_s08` | `glc_pc_s08` | direct | yes |
| `glc_pc_s09` | `glc_pc_s09` | direct | yes |
| `glc_pc_s10` | `glc_pc_s10` | direct | yes |
| `glc_pc_s11` | `glc_pc_s11` | direct | yes |
| `glc_pc_s12` | `glc_pc_s12` | direct | yes |
| `glc_pc_s13` | `glc_pc_s13` | direct | yes |
| `glc_pc_s14` | `glc_pc_s14` | direct | yes |
| `glc_pc_s15` | `glc_pc_s15` | direct | yes |
| `glc_pc_s16` | `glc_pc_s16` | direct | yes |
| `glc_pc_s17` | `glc_pc_s17` | direct | yes |
| `glc_pc_s18` | `glc_pc_s18` | direct | yes |
| `glc_pc_s19` | `glc_pc_s19` | direct | yes |
| `glc_pc_s20` | `glc_pc_s20` | direct | yes |
| `glc_pc_s21` | `glc_pc_s21` | direct | yes |
| `glc_pc_s22` | `glc_pc_s22` | direct | yes |
| `forest_fraction` | `for_pc_sse` | alias | yes |
| `permafrost_fraction` | `prm_pc_sse` | alias | yes |
| `pnv_pc_s01` | `pnv_pc_s01` | direct | yes |
| `pnv_pc_s02` | `pnv_pc_s02` | direct | yes |
| `pnv_pc_s03` | `pnv_pc_s03` | direct | yes |
| `pnv_pc_s04` | `pnv_pc_s04` | direct | yes |
| `pnv_pc_s05` | `pnv_pc_s05` | direct | yes |
| `pnv_pc_s06` | `pnv_pc_s06` | direct | yes |
| `pnv_pc_s07` | `pnv_pc_s07` | direct | yes |
| `pnv_pc_s08` | `pnv_pc_s08` | direct | yes |
| `pnv_pc_s09` | `pnv_pc_s09` | direct | yes |
| `pnv_pc_s10` | `pnv_pc_s10` | direct | yes |
| `pnv_pc_s11` | `pnv_pc_s11` | direct | yes |
| `pnv_pc_s12` | `pnv_pc_s12` | direct | yes |
| `pnv_pc_s13` | `pnv_pc_s13` | direct | yes |
| `pnv_pc_s14` | `pnv_pc_s14` | direct | yes |
| `pnv_pc_s15` | `pnv_pc_s15` | direct | yes |
| `wet_pc_s01` | `wet_pc_s01` | direct | yes |
| `wet_pc_s02` | `wet_pc_s02` | direct | yes |
| `wet_pc_s03` | `wet_pc_s03` | direct | yes |
| `wet_pc_s04` | `wet_pc_s04` | direct | yes |
| `wet_pc_s05` | `wet_pc_s05` | direct | yes |
| `wet_pc_s06` | `wet_pc_s06` | direct | yes |
| `wet_pc_s07` | `wet_pc_s07` | direct | yes |
| `wet_pc_s08` | `wet_pc_s08` | direct | yes |
| `wet_pc_s09` | `wet_pc_s09` | direct | yes |
| `wet_pc_sg1` | `wet_pc_sg1` | direct | yes |
| `wet_pc_sg2` | `wet_pc_sg2` | direct | yes |

## Provenance

- Declared names: `cmal_small/config.yaml` -> `static_features` (78 entries).
- Available keys: `select distinct jsonb_object_keys(attributes) from basins` on the mac mini,
  filtered to the `caravan:` namespace (216 keys, all 148 basins).
- `forest_fraction` and `permafrost_fraction` were the only two unresolved; the aliases added
  2026-09-04 (`for_pc_sse`, `prm_pc_sse`) close them. Regenerate this table after any
  `CARAVAN_ALIAS` change or a new statics import.
