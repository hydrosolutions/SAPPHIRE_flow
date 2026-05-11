# Evidence Memo 06: Data And Forecast Product Readiness

**Primary legacy review**:
[06-datasets-and-nwp.md](../source-reviews/06-datasets-and-nwp.md)
**Focused top-up date**: `2026-04-23`

## Scope

This memo tracks the practical data landscape for the review: benchmark
datasets, stage availability, reanalysis and NWP products, reforecast archives,
and the structural constraints that still prevent clean evaluation of
ML-based ensemble streamflow forecasting at sub-daily resolution.

## Source Base

### Legacy synthesis carried forward

- [06-datasets-and-nwp.md](../source-reviews/06-datasets-and-nwp.md)
- [precipitation_products.md](../source-reviews/precipitation_products.md)

### Focused top-up sources

- Tran et al. (2025), CAMELSH:
  <https://doi.org/10.1038/s41597-025-05612-6>
- Coxon et al. (2025), CAMELS-GB v2 preprint:
  <https://doi.org/10.5194/essd-2025-608>
- CAMELS-GB v2 data record:
  <https://doi.org/10.5285/8344e4f3-d2ea-44f5-8afa-86d2987543a9>
- Sink and Brikowski (2026), MACH:
  <https://doi.org/10.1038/s41597-026-07162-x>
- Guan et al. (2022), GEFSv12 reforecast:
  <https://doi.org/10.1175/MWR-D-21-0245.1>
- Official GloFAS/ECMWF reforecast skill documentation:
  <https://confluence.ecmwf.int/display/CEMS/GloFAS%20forecast%20skill>

## Evidence Snapshot

| Claim area | Status | Notes |
|---|---|---|
| The benchmark ecosystem is improving at hourly resolution | Established finding | CAMELSH and CAMELS-GB v2 materially strengthen this |
| CAMELSH currently solves the sub-hourly benchmark gap | Not supported | The published paper is hourly, not sub-hourly |
| Hourly stage is still rare in large-sample benchmark datasets | Established finding | CAMELS-GB v2 is the strongest exception identified |
| Public NWP reforecast options exist for research | Established finding | GEFSv12 is the clearest open example; ECMWF reforecasts remain more constrained |

## What The Evidence Clearly Supports

- CAMELSH is a major benchmark advance, but the published 2025 paper reports
  hourly observed streamflow for 3,166 basins and a broader 9,008-basin
  resource overall. It should not be treated as a sub-hourly benchmark.
- CAMELS-GB v2 is important because it adds hourly river level and flow in a
  curated catchment dataset. This makes it unusually relevant for operational
  warning applications.
- MACH 2026 confirms that the benchmark landscape is still evolving quickly,
  especially in the United States, although its contribution is daily rather
  than sub-daily.
- GEFSv12 reforecast details are now clearer: the 2022 MWR paper supports the
  "5 daily / 11 weekly" description and confirms the longer 1989-2019
  reforecast history.
- ECMWF reforecast practice for hydrologic skill evaluation remains important,
  but access and direct ML-readiness are more constrained than the open GEFS
  case.

## What The Evidence Does Not Yet Show Directly

- No reviewed benchmark dataset packages **ensemble NWP forcing together with
  streamflow targets** for direct ML experimentation.
- No reviewed benchmark dataset closes the sub-hourly gap with matched forcing
  and broad basin coverage.
- No reviewed source identifies a public AI-weather-model reforecast archive
  that can already substitute for GEFS/ENS in this research area.
- No reviewed source removes the regional gap around Nepal and similar
  high-mountain monsoon settings.

## Counterevidence And Caution

- The CAMELSH numbers need careful version control. The published article, the
  Zenodo releases, and later internal summaries should not be conflated.
- The presence of more hourly datasets does not eliminate the evaluation problem
  for sub-hourly flood forecasting.
- Reforecast access and member-count descriptions should always be tied to
  explicit product versions because these product families evolve over time.

## Implications For The Scoping Review

- The data section should emphasize **benchmark readiness**, not just dataset
  inventory.
- A safe synthesis is:
  `The field now has several strong hourly benchmark resources and at least one
  major open NWP reforecast archive, but still lacks a standardized sub-hourly,
  ensemble-forced hydrologic benchmark.`
- Paper 2 should treat data assembly as part of the scientific contribution, not
  merely as preprocessing.

## Priority Extraction Targets

1. Tran et al. (2025), CAMELSH
2. Coxon et al. (2025), CAMELS-GB v2
3. Sink and Brikowski (2026), MACH
4. Guan et al. (2022), GEFSv12 reforecast

## Carry-Over Verification Items

- Pin the exact CAMELSH version and basin count that will be used in any paper
  draft.
- Keep checking whether ECMWF reforecast access or AI-weather reforecast
  availability changes.
- Treat claims about Nepal or Himalayan data absence as contingent on the exact
  search scope and date.
