# Evidence Memo 01: Operational Forecasting Landscape

**Primary legacy review**:
[01-operational-systems.md](../source-reviews/01-operational-systems.md)
**Focused top-up date**: `2026-04-23`

## Scope

This memo tracks what the literature and official system documentation currently
support about operational flood and streamflow forecasting systems, with
particular attention to whether ML is operational, whether uncertainty comes
from NWP ensembles or learned distributions, and whether any system already
implements ML hydrology with ensemble NWP pass-through.

## Source Base

### Legacy synthesis carried forward

- [01-operational-systems.md](../source-reviews/01-operational-systems.md)

### Focused top-up sources

- Nearing et al. (2024), *Nature*:
  <https://doi.org/10.1038/s41586-024-07145-1>
- ECMWF Newsletter 185, "AI takes CEMS flood forecasting into a new era"
  (Oct 2025): <https://www.ecmwf.int/node/29511>
- Taccari et al. (2026), AIFL preprint:
  <https://doi.org/10.48550/arXiv.2602.16579>

## Evidence Snapshot

| Claim area | Status | Notes |
|---|---|---|
| Operational ensemble flood systems remain process-based on the hydrology side | Established finding | Strongly supported by EFAS, GloFAS, NWM, and similar systems in the legacy review |
| Global operational ML streamflow exists | Established finding | Google Flood Hub is the clearest case, but it is not ensemble-NWP pass-through |
| ECMWF is moving AI into operational hydrometeorology | Established finding | AIFS Single entered EFAS and GloFAS in September 2025, but as deterministic meteorological input |
| Operational ML hydrology with ensemble NWP pass-through has been identified | Open question leaning negative | No such system was identified in the legacy review or focused top-up |

## What The Evidence Clearly Supports

- Major operational probabilistic flood systems still use deterministic
  hydrological models forced member-by-member by NWP ensembles. This remains
  the dominant operational pattern in the reviewed literature.
- Google Flood Hub is important as evidence that ML streamflow can operate at
  large scale, but it does **not** close the ensemble-hydrology gap because its
  published operational framing is daily and based on deterministic forcing plus
  learned uncertainty.
- ECMWF's 2025 operational updates materially change the context: AIFS Single
  was introduced into EFAS and GloFAS, which means AI is already entering the
  operational forecast chain, but the hydrological propagation step still
  remains process-based.
- AIFL strengthens the pre-operational ML-hydrology story inside ECMWF. It is a
  deterministic LSTM-based global daily streamflow model, not an operational
  ensemble-streamflow system.

## What The Evidence Does Not Yet Show Directly

- No reviewed source shows an operational system that propagates **individual
  NWP ensemble members through a standalone ML streamflow model**.
- No reviewed source shows an operational **sub-daily** ML ensemble streamflow
  service at multi-basin scale.
- No reviewed source provides a head-to-head operational comparison between
  process-based ensemble propagation and ML-based ensemble propagation.

## Counterevidence And Caution

- Claims framed as "no operational system exists" should remain careful. The
  space is moving quickly, and some operational practice may be documented only
  in newsletters, technical notes, or product pages before journal articles
  appear.
- "Operational", "pre-operational", "research prototype", and "public service"
  should be treated as separate categories. AIFL currently sits in the
  pre-operational category, not the operational one.
- The operational ML story is strongest for **daily** large-scale prediction.
  It should not be generalized to sub-daily ensemble forecasting without
  additional evidence.

## Implications For The Scoping Review

- The operational section should be written as a **taxonomy of deployed
  paradigms**, not as a search for a single winner.
- The strongest defensible statement at present is:
  `We did not identify an operational system that propagates ensemble NWP
  members through a standalone ML hydrological model.`
- Paper 2 should position itself against three operationally relevant reference
  classes:
  process-based ensemble propagation, deterministic ML with learned
  probabilistic output, and pre-operational deterministic ML baselines.

## Priority Extraction Targets

1. Nearing et al. (2024), *Nature*
2. ECMWF Newsletter 185 (2025) operational AIFS-in-flood update
3. Taccari et al. (2026), AIFL preprint
4. One cornerstone EFAS or GloFAS operational paper from the legacy review

## Carry-Over Verification Items

- Keep monitoring whether AIFS ENS, not just AIFS Single, becomes coupled to
  hydrology.
- Separate claims about Google Flood Hub's public service from claims about its
  underlying operational deployment details.
- Treat "no system found" as the preferred wording until a formal search log is
  complete.
