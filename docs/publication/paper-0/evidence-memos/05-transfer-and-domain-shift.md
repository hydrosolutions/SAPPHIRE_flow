# Evidence Memo 05: Transfer And Domain Shift

**Primary legacy review**:
[05-transfer-learning.md](../source-reviews/05-transfer-learning.md)  
**Focused top-up date**: `2026-04-23`

## Scope

This memo tracks what the evidence currently supports about transfer learning,
regionalization, domain shift, and generalization in ML hydrology, with
specific emphasis on what remains unknown once the problem is moved from daily
benchmarking toward sub-daily and forecast-forcing settings.

## Source Base

### Legacy synthesis carried forward

- [05-transfer-learning.md](../source-reviews/05-transfer-learning.md)

### Focused top-up sources

- Nearing et al. (2024), *Nature*:
  <https://doi.org/10.1038/s41586-024-07145-1>
- Heudorfer et al. (2025), *GRL*:
  <https://doi.org/10.1029/2024GL113036>
- Taccari et al. (2026), AIFL preprint:
  <https://doi.org/10.48550/arXiv.2602.16579>
- Park et al. (2025), *Hydrology*:
  <https://doi.org/10.3390/hydrology12100261>

## Evidence Snapshot

| Claim area | Status | Notes |
|---|---|---|
| Large-scale daily transfer is well established | Established finding | Strong support from Nearing 2024 and the legacy review |
| Static attributes clearly deliver physical generalization | Not supported as a blanket statement | Heudorfer 2025 weakens this interpretation |
| Domain shift from reanalysis to forecast forcing is operationally important | Promising but limited evidence | AIFL makes this concrete, but it is still preprint evidence |
| Sub-daily transfer is well evidenced | Not supported | The legacy review remains correct that this area is very thin |

## What The Evidence Clearly Supports

- Daily large-sample transfer is no longer a fringe claim. Nearing et al. shows
  that global ML flood prediction in ungauged watersheds is possible at
  meaningful scale.
- Heudorfer et al. is a critical top-up because it challenges a common hidden
  assumption in the transfer literature: that performance gains from
  entity-aware models automatically imply physically grounded generalization.
- Park et al. is a useful counterbalance. It shows that entity-aware LSTM can
  still be operationally useful in a large, heterogeneous, transboundary basin.
  Together, Heudorfer and Park argue for a more careful interpretation rather
  than a simple pro- or anti-EA stance.
- AIFL makes the reanalysis-to-forecast domain shift more concrete for this
  review. Even though it is deterministic and daily, it directly supports the
  argument that two-stage training matters when moving from reanalysis to
  operational forecast products.

## What The Evidence Does Not Yet Show Directly

- No reviewed study establishes robust transfer-learning evidence at true
  sub-daily resolution.
- No reviewed study shows whether probabilistic calibration survives transfer
  across regions or climates in a sub-daily setting.
- No reviewed study resolves how much sub-daily data is needed for useful
  fine-tuning in new basins.
- No reviewed study closes the question of transfer into monsoon, glacier-fed,
  or data-sparse Himalayan settings.

## Counterevidence And Caution

- The field should stop equating "regional model works" with
  "static attributes encode transferable hydrologic physics". Heudorfer 2025 is
  the main reason.
- Park 2025 shows that entity-aware models may still be practically useful even
  if the theoretical interpretation is weaker than previously assumed.
- AIFL is still a preprint and should be used as strong contextual evidence, not
  as the sole source for precise performance claims.

## Implications For The Scoping Review

- The transfer section should separate three different questions:
  whether transfer works, why it works, and whether it survives operational
  domain shift.
- The safest synthesis is:
  `Daily transfer evidence is mature enough to motivate regional training, but
  the mechanisms of generalization and the extension to sub-daily forecasting
  remain unresolved.`
- Paper 2 should avoid assuming that daily transfer results carry over unchanged
  to sub-daily ensemble forecasting.

## Priority Extraction Targets

1. Nearing et al. (2024)
2. Heudorfer et al. (2025)
3. Taccari et al. (2026), AIFL
4. Park et al. (2025)

## Carry-Over Verification Items

- Keep the AIFL domain-shift numbers provisional until directly extracted from
  the source text into a study record.
- Do not use "foundation model" language loosely; define it if retained.
- Continue searching for true hourly or sub-hourly transfer papers before making
  any stronger negative statement.
