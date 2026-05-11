# Evidence Memo 02: Architectures And Temporal Representation

**Primary legacy review**:
[02-ml-architectures.md](../source-reviews/02-ml-architectures.md)
**Focused top-up date**: `2026-04-23`

## Scope

This memo distils what the literature currently supports about ML architectures
for hydrologic forecasting, especially their relevance to sub-daily prediction,
multi-timescale inputs, future forcing, and the specific mismatch between coarse
NWP inputs and finer streamflow targets.

## Source Base

### Legacy synthesis carried forward

- [02-ml-architectures.md](../source-reviews/02-ml-architectures.md)

### Focused top-up sources

- Liu et al. (2025), *HESS* benchmark:
  <https://doi.org/10.5194/hess-29-6811-2025>
- Acuna Espinoza et al. (2025), MF-LSTM technical note:
  <https://doi.org/10.5194/hess-29-1749-2025>
- Shams Eddin et al. (2025), RiverMamba preprint:
  <https://doi.org/10.48550/arXiv.2505.22535>

## Evidence Snapshot

| Claim area | Status | Notes |
|---|---|---|
| LSTM remains a strong hydrologic baseline | Established finding | Strong support from the legacy review and the 2025 benchmark paper |
| Multi-timescale LSTM variants are the clearest architectural fit for sub-daily hydrology | Promising but limited evidence | Relevant and well motivated, but still not tested at the exact target setting |
| Transformers dominate hydrology broadly | Not supported | The current evidence is more task-dependent and less universal |
| A tested architecture already exists for sub-hourly streamflow with ensemble NWP forcing | Open question leaning negative | No direct example identified in the reviewed evidence |

## What The Evidence Clearly Supports

- The 2025 benchmark by Liu et al. supports a more nuanced view than
  "transformers win everything". LSTM-family models remain extremely strong for
  standard regression and short-horizon hydrologic forecasting tasks.
- The most directly relevant architecture family for the current review question
  is still the **multi-timescale LSTM line**. MTS-LSTM and now MF-LSTM are
  specifically designed to combine coarse and fine temporal information without
  forcing all inputs onto a single timescale.
- MF-LSTM is a meaningful top-up because it simplifies the multi-frequency idea
  and makes it more computationally practical. That matters for any later
  ensemble or operational implementation discussion.
- RiverMamba expands the frontier of hydrologic architecture research and shows
  that state-space approaches are entering the field, but the evidence remains
  anchored in global daily forecasting rather than sub-hourly catchment-scale
  prediction.

## What The Evidence Does Not Yet Show Directly

- No reviewed study demonstrates an architecture tested on the exact target
  configuration of **ensemble NWP forcing plus sub-hourly streamflow output**.
- No reviewed study directly compares:
  multi-timescale modeling versus temporal disaggregation versus
  autoregressive interpolation for the same forecast task.
- No reviewed study resolves whether spatial representation matters more than
  temporal refinement for steep, fast-responding basins such as those relevant
  to SAPPHIRE.

## Counterevidence And Caution

- Architecture conclusions are highly task-dependent. The Liu et al. benchmark
  is valuable, but it is not a dedicated sub-hourly flood benchmark.
- RiverMamba should be cited as an emerging frontier model, not as direct
  evidence that state-space models solve the sub-daily operational problem.
- Some strong claims in the legacy architecture review still depend on studies
  that were context-specific, recently published, or previously flagged for
  verification. The memo should be treated as the safer layer.

## Implications For The Scoping Review

- The review should not frame architecture choice as "LSTM versus transformer".
  The more defensible framing is:
  `Which architectures can represent multi-timescale forcing and forecast
  structure with acceptable operational complexity?`
- The architecture section should be organized around four capability questions:
  temporal aggregation, future forcing, spatial representation, and uncertainty
  compatibility.
- The current best-supported design candidates for Paper 2 remain:
  standard LSTM as baseline, a multi-timescale LSTM family member, and one
  spatial architecture for gridded forcing.

## Priority Extraction Targets

1. Liu et al. (2025), *HESS* benchmark
2. Acuna Espinoza et al. (2025), MF-LSTM
3. Gauch et al. (MTS-LSTM), already covered in the legacy review
4. Shams Eddin et al. (2025), RiverMamba

## Carry-Over Verification Items

- Keep a strict distinction between hourly evidence and true sub-hourly
  evidence.
- Do not upgrade RiverMamba from frontier evidence to direct design precedent
  for sub-hourly catchment modeling.
- Remove or quarantine architecture claims that still depend on previously
  flagged unverified references from the legacy review.
