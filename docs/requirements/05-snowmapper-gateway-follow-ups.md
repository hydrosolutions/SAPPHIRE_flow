# SnowMapper integration: Bea's internal notes

13 November 2026 · Maintainer: Bea Marti

These notes support the [agreement](05-snowmapper-gateway-contract.md). They are
not an additional task list for Joel, Vatro or Sandro.

## Recovering the Nepal daily-boundary discussion

The repository records the discussion in [Plan 252, OD-3](../plans/252-time-grids-are-step-and-phase.md):

- The owner decision dated 9 September was to express DHM's conventional observation
  day at the **closest whole UTC hour**, recording the displacement.
- Pending clarification, the provisional boundary is **18:00 UTC**, rather than
  civil midnight at 18:15 UTC: a day runs **23:45–23:45 Nepal time**. This avoids
  splitting hourly or 3-hourly forcing at a quarter-hour boundary; source timestamps
  are not shifted. The plan also notes that SnowMapper's UTC+6 solar-day display
  corroborates this choice but does not determine our input semantics.
- Plan 252 remains DRAFT. [Plan 254](../plans/254-phase-aware-execution.md) holds the
  execution work. [Plan 268](../plans/268-dhm-barkhk-runoff-delivery.md) records that
  the grid/phase work was halted; its historical DHM imports separately use exact
  `Asia/Kathmandu` dates, including the historical timezone change.
- Current [shared aggregation](../../src/sapphire_flow/services/training_data.py)
  still uses UTC-calendar days. The [tests](../../tests/unit/services/test_training_data.py)
  enforce midnight UTC. Exact local dates in the
  [DHM import helper](../../src/sapphire_flow/adapters/nepal_local_day.py) are a
  separate concern, not evidence that the proposed operational boundary is implemented.

**Next step — Bea:** clarify the daily boundary and SWE aggregation with Sandro,
including consistency between training and operations and any outstanding DHM
confirmation. **Only after that discussion**, reconcile Plans 252/254 and affected
conventions with the agreed scope and actual code. Do not restart the halted work
or change observation timestamps through this documentation PR.

## Work we manage ourselves

Bea handles the required deployment areas, Central Asia client/`hs` compatibility,
Nepal local-storage coordination with NHS, payment/contact details, and cutover.
Keep these in normal integration work; escalate a specific blocker only if one is
found. Old-stream archive/deletion arrangements follow cutover approval.

Bea's acceptance checklist: compare source and gateway values for three geometries,
including partial/no coverage, with agreed numeric tolerances; check interval joins,
actual source cycles and a missing-cycle case. Check seven consecutive days of
complete, timely delivery and compare old/new forecasts in both deployments before
cutover. Sandro accepts the models.

For the suspected one-hour offset in the legacy Nepal stream, Bea first compares
a source interval with the served value and takes any demonstrated mismatch to
Vatro. Do not present the suspicion as a confirmed defect or block new-format
samples on it.

## Separate weather enhancement

Vatro reportedly agreed on 13 Nov to make the weather tail selectable, with
stitching in the gateway, after the snow work. This does not settle snow-tail
selection. Bea follows up separately:

| Weather cycle | Requested horizon | Status from v0.3 |
| --- | --- | --- |
| 00Z and 12Z | 11 days | Bea's request; Vatro to confirm |
| 06Z and 18Z | 4 days | Reported confirmed by Vatro, 13 Nov |

The reported 00Z availability is about 10:00 UTC. Its horizon needs reconciliation
(10 days observed versus about 15 documented); 12Z timing is unconfirmed. The goal
is an export at any hour when an eligible complete cycle covers the requested
period, showing its actual cycle, or a clear unavailable response otherwise.

## Migration reference

Old Central Asia uses `joel-snow-model/` (daily SWE/HS/ROF/HS24); Nepal uses
`joel-snow-model-nepal/` (reported hourly, four daily cycles, ten-day horizon,
end stamps). The new mosaic reportedly holds two reanalysis years; the 6 Nov upload
covered Sep 2025–Aug 2026. Joel's sample/history check establishes actual coverage.
