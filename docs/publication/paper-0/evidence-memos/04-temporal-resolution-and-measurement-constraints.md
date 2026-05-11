# Evidence Memo 04: Temporal Resolution And Measurement Constraints

**Primary legacy review**:
[04-sub-hourly-resolution.md](../source-reviews/04-sub-hourly-resolution.md)
**Focused top-up date**: `2026-04-23`

## Scope

This memo tracks what the current literature supports about hourly versus
sub-hourly forecasting value, the operational meaning of finer time steps, and
the measurement constraints that limit how confidently sub-hourly improvements
can be evaluated.

## Source Base

### Legacy synthesis carried forward

- [04-sub-hourly-resolution.md](../source-reviews/04-sub-hourly-resolution.md)

### Focused top-up sources

- Tran et al. (2025), CAMELSH:
  <https://doi.org/10.1038/s41597-025-05612-6>
- Coxon et al. (2025), CAMELS-GB v2 preprint:
  <https://doi.org/10.5194/essd-2025-608>
- CAMELS-GB v2 dataset record:
  <https://doi.org/10.5285/8344e4f3-d2ea-44f5-8afa-86d2987543a9>

## Evidence Snapshot

| Claim area | Status | Notes |
|---|---|---|
| The large-sample ML literature still largely stops at hourly resolution | Established finding | Strengthened by CAMELSH and CAMELS-GB v2 |
| There is already a curated sub-hourly benchmark for ML streamflow | Not supported | No such benchmark was identified in the current search |
| Stage is becoming more visible in benchmark resources | Promising but limited evidence | CAMELS-GB v2 is important, but the field is still thin here |
| Sub-hourly prediction clearly doubles effective lead time | Not supported | The legacy review already treated this as an unsupported slogan |

## What The Evidence Clearly Supports

- The literature has become stronger at the **hourly** level, not yet at the
  truly sub-hourly level. CAMELSH and CAMELS-GB v2 both strengthen the hourly
  benchmark ecosystem.
- CAMELSH is particularly important because it confirms that the field now has a
  large-sample hourly dataset, but the published paper still reports hourly
  streamflow for 3,166 basins, not a ready-made sub-hourly benchmark.
- CAMELS-GB v2 matters because it adds **hourly river level** alongside river
  flow, which makes the stage-versus-discharge question much more concrete than
  it was before.
- The strongest evidence for why sub-hourly might matter still comes from
  hydrologic response-time reasoning and process-based studies, not from
  large-sample ML comparisons.

## What The Evidence Does Not Yet Show Directly

- No reviewed source provides a large-sample ML benchmark below hourly
  resolution with matched forcing.
- No reviewed source provides a large-sample, direct ML comparison of stage
  prediction versus discharge prediction.
- No reviewed source quantifies the headline claim that 15-minute prediction
  effectively doubles warning lead time.
- No reviewed source resolves whether sub-hourly skill gains remain visible once
  rating-curve uncertainty is propagated into evaluation.

## Counterevidence And Caution

- The field should not treat hourly datasets as evidence that the sub-hourly
  problem is already solved. Hourly is a major step forward, but it does not
  remove the benchmark gap below 1 hour.
- The presence of stage data in CAMELS-GB v2 should not be generalized to the
  rest of the benchmark landscape.
- The sub-hourly value proposition is physically plausible for flashy basins,
  but the current ML literature is still too thin to treat it as an established
  empirical result.

## Implications For The Scoping Review

- The temporal-resolution section should be written as a combination of
  evidence mapping and measurement critique.
- The strongest safe wording is:
  `Hourly benchmark infrastructure is now improving, but large-sample ML
  evidence below hourly resolution remains very limited.`
- The stage-versus-discharge question should be framed as an important research
  opportunity enabled by newer datasets, not as a settled methodological shift.

## Priority Extraction Targets

1. Tran et al. (2025), CAMELSH
2. Coxon et al. (2025), CAMELS-GB v2
3. Ficchi et al. (2016), already central in the legacy review

## Carry-Over Verification Items

- Replace any lingering CAMELSH claim that implies current hourly water-level
  availability in the published dataset.
- Keep the "lead time doubling" statement out of any publication draft unless a
  direct source is found.
- Track whether a true sub-hourly curated benchmark appears after the current
  search window.
