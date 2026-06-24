# ForecastInterface — Model Implementer Requirements (Nepal v1)

> The model-author-facing contract **and its open design questions now live in the
> ForecastInterface repo** — this file is a SAP3-side index, not a duplicate.
>
> **Live docs** (`/Users/bea/Documents/GitHub/ForecastInterface`):
> - `docs/open_design_questions.md` — **the live tracker**: resolved decisions + the
>   questions still owed by the model developer (Sandro).
> - `docs/model_interface.md`, `docs/input_requirement.md` — the contract.
> - `forecast_interface/` — the implemented contract (`input` / `interface` / `output`).
>
> *(The earlier `nepal-model-requirements.md` was removed in 2026-06; its content was
> consolidated into the contract docs and implemented in code.)*

## Open questions still owed (tracked in FI `open_design_questions.md`)

Owed by the model developer (Sandro):

- **Q8 — `config` contents** *(main open item)*: enumerate train-time config
  (hyperparameters, emitted quantile levels, sample count, validation-split,
  early-stopping) so FI/SAP3 can partition model-private vs operationally-shared. `config`
  stays `Any` until then.
- **Q9 — SnowMapper availability-lag values**: concrete reduced per-variable `future_steps`
  for SWE / RoF vs the ECMWF horizon (mechanism settled; numbers owed).
- **Q10 — station-code confirmation**: confirm trained artifacts key on the station/gauge
  **code** (not a per-DB UUID); re-key if not.

Plus a list of **deviations from Sandro's original proposal** awaiting his sign-off (see
that doc's "Deviations" section).

## SAP3-side items these decisions imply (our build work, not open design questions)

The FI decisions assign concrete work to our side — tracked in the build list of
[`00-internal-gap-analysis.md`](00-internal-gap-analysis.md); adapter-level specifics:

- Build the **`ForecastInterfaceAdapter`** (planned, not yet built), with the **GROUP path
  load-bearing from day one** — the eastern regional group ships first (FI 1.1 / 1.6).
- Check **operational floors at integration time** (≥7 quantiles with tail coverage, ≥20
  members) and reject non-operational output loudly (FI 1.5).
- **Deliver inputs in the model's declared unit** (or reject at integration) and enforce
  **`max_nan` as a pre-`predict` gate** — SAP3 currently delivers raw NaNs (FI 1.11 / 1.13).
- Map **station code ↔ `StationId` UUID** at the adapter boundary (FI 1.10 / Q10).
- Co-design PR: lift `target_parameters` + `spatial_input_type` into FI's input spec
  (FI 1.2).
- Re-evaluate a deferral: the embedding-key / station-set-mismatch contract is likely
  **v1, not Phase 4**, since GROUP artifacts + east→west transfer ship from the start
  (FI 1.10 timing flag).

## No SAP3-side open *design* questions outstanding

Everything our requirements interview surfaced (targets, group output, banded SnowMapper
forcing, cold-retrain/warm-optional, determinism, artifact portability, ID-embedding) is
now resolved in the FI contract docs/code. What remains on our side is the adapter build
above, not open design questions.
