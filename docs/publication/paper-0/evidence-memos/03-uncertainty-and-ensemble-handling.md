# Evidence Memo 03: Uncertainty And Ensemble Handling

**Primary legacy review**:
[03-uncertainty-paradigms.md](../source-reviews/03-uncertainty-paradigms.md)
**Focused top-up date**: `2026-04-23`

## Scope

This memo tracks the evidence around how uncertainty is represented in ML-based
streamflow forecasting, especially the status of NWP pass-through, learned
distribution heads, deep ensembles, and newly emerging generative approaches.

## Source Base

### Legacy synthesis carried forward

- [03-uncertainty-paradigms.md](../source-reviews/03-uncertainty-paradigms.md)

### Focused top-up sources

- Klotz et al. (2022), *HESS*:
  <https://doi.org/10.5194/hess-26-1673-2022>
- Zhang et al. (2023), *HESS*:
  <https://doi.org/10.5194/hess-27-4529-2023>
- Dong et al. (2025), *HESS*:
  <https://doi.org/10.5194/hess-29-2023-2025>
- Ou et al. (2025), DRUM, *GRL*:
  <https://doi.org/10.1029/2025GL115705>

## Evidence Snapshot

| Claim area | Status | Notes |
|---|---|---|
| Learned probabilistic heads are well evidenced in ML rainfall-runoff | Established finding | Klotz 2022 remains the strongest benchmark anchor |
| QRF is a credible non-neural comparator to CMAL-style models | Established finding | Zhang 2023 directly supports this in a post-processing setting |
| Standalone ML hydrology with member-by-member NWP propagation is already established | Not supported | Dong 2025 is close, but it is hybrid rather than pure ML hydrology |
| Diffusion-based probabilistic hydrology is emerging quickly | Promising but limited evidence | DRUM is a strong signal, but the literature is still very young |

## What The Evidence Clearly Supports

- Klotz et al. provides strong evidence that ML rainfall-runoff models can
  produce meaningful predictive uncertainty and that mixture-density-style heads
  are serious baselines rather than side experiments.
- Zhang et al. supports the argument that a non-neural baseline such as QRF can
  remain competitive for probabilistic hydrologic post-processing, especially
  when computational simplicity matters.
- Dong et al. is the most important focused top-up for the pass-through
  question. It shows that ensemble precipitation forecasts can be propagated
  through a **hybrid** deep-learning hydrologic chain, which narrows the gap but
  does not close the pure-ML question.
- DRUM materially upgrades the "Paradigm D" discussion from speculative to
  publishable evidence. Diffusion-based hydrologic forecasting is now a
  peer-reviewed signal, especially for extremes and early warning.

## What The Evidence Does Not Yet Show Directly

- No reviewed paper provides the missing head-to-head comparison between:
  NWP-member pass-through, learned distribution heads, and deep ensembles on a
  common streamflow task.
- No reviewed paper establishes that deep ensembles alone are an adequate proxy
  for forcing uncertainty in operational forecast settings.
- No reviewed paper closes the exact question of how best to ingest **ensemble
  NWP** into a standalone ML hydrological model at sub-daily resolution.

## Counterevidence And Caution

- Much of the best evidence for probabilistic ML still comes from daily
  rainfall-runoff or post-processing settings, not operational NWP ensemble
  forcing.
- Dong et al. should not be overused. It is a valuable bridge paper, but the
  hydrologic component is hybrid and the application is sub-seasonal daily.
- Diffusion models are now credible, but still early. They should be framed as
  emerging evidence rather than as the settled next standard.
- The AIFS-specific CRPS claims in the legacy review still require a cleaner
  primary source chain before they can anchor strong prose.

## Implications For The Scoping Review

- The uncertainty section should distinguish the **source of uncertainty** from
  the **implementation method**.
- A clean paper-ready structure is:
  pass-through uncertainty, learned predictive distributions, ensemble-of-models
  approaches, and generative trajectory models.
- The most defensible central open question remains:
  `Where should forecast uncertainty come from when ML hydrology is coupled to
  forecast forcing products?`

## Priority Extraction Targets

1. Klotz et al. (2022)
2. Zhang et al. (2023)
3. Dong et al. (2025)
4. Ou et al. (2025), DRUM

## Carry-Over Verification Items

- Keep the CRPS/AIFS discussion provisional until a cleaner peer-reviewed source
  is pinned down.
- Maintain the distinction between pure ML hydrology, hybrid hydrology, and
  post-processing of process-based ensembles.
- Treat `no head-to-head comparison found` as safer wording than `gap
  confirmed`.
