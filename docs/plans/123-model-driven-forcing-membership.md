---
id: 123
title: Model-driven forcing membership (fetch what models require; requirement-aware cycle resolution)
status: DRAFT (stub)
depends_on: [082]
owner: unassigned
created: 2026-07-17
---

# Plan 123 — Model-driven forcing membership

> **Stub.** Surfaced by live-testing the merged Plan 082 adapter against the real
> Gateway HRU `12300` (2026-07-17). Needs the `plan` workflow (adversarial Codex +
> design review) before READY. Changes merged adapter behaviour and touches the
> ForecastInterface contract, so plan-first with an independent review.

## Problem

`RecapGatewayForecastAdapter.fetch_forecasts` (`src/sapphire_flow/adapters/recap_gateway.py`)
**hardcodes a 51-member fetch** — `fc` (member_id 0) plus `for member in range(1, 51)`
over all 50 `pf` members (`_FC_MEMBER_ID`/`_PF_MEMBER_MIN`/`_PF_MEMBER_MAX`, ~lines 743-757).
It never consults what the assigned models actually require. Two consequences, both
confirmed live:

1. **Wrong for the models we have.** Sandro's Nepal models currently consume the **`fc`
   control member only**, for efficiency. Fetching 50 `pf` members per cycle per variable
   per station is wasted work — and worse, it makes the cycle **hard-abort** when `pf`
   is unavailable even though the model needs none of it.
2. **`fc`-before-`pf` is a normal per-cycle window, by ECMWF design — not a backfill
   artifact.** ECMWF disseminates the control (HRES / "ENS control") **earlier than the
   perturbed members**, and IFS **Cycle 50r1** explicitly preserves this (the renamed
   control "continues to be made available earlier than the ensemble perturbed members").
   So every cycle has a window where `fc` is published and `pf` is not yet complete.

**Live evidence (HRU `12300`, 2026-07-17):** `resolve_latest_cycle` probes availability
using `fc` only, locks onto the newest `fc` cycle (`2026-07-17 00Z`, which had `fc` but no
`pf` yet), then the ensemble fetch demands the 50 `pf` members at that cycle → `AdapterError:
No IFS dataset found`, instead of using the newest **complete** ensemble cycle
(`2026-07-16 00Z`) — or, for an `fc`-only model, simply serving `2026-07-17 00Z`.

## Goal

Make forcing membership **model-driven**, per the ForecastInterface principle (models declare
requirements; SAP3 delivers exactly what is required):

- **Control-only models → fetch `fc`, skip `pf` entirely, never abort on missing `pf`.**
- **Ensemble models → fetch the full 1×`fc` + 50×`pf` set.**
- **Requirement-aware cycle resolution:** resolve to the newest cycle where the *required*
  members are available — a control-only model can ride the earliest control cycle; an
  ensemble model resolves to the newest *complete* cycle (since `pf` lags `fc`).

## Open design forks (decide in the `plan` workflow)

1. **Where does "required membership" come from?** The model's FI `input requirement` is the
   natural source, resolved per station→model assignment. But `fetch_forecasts` currently takes
   only `(station_configs, cycle_time)` — no requirement is threaded in. Options: (a) thread the
   resolved requirement into the adapter; (b) resolve membership upstream (flow) and pass a
   member spec; (c) a per-station-source membership field. Pick one.
2. **FI adherence (MANDATORY).** Check whether the FI `input requirement`
   (`input/requirement.py`) can already express deterministic/control-vs-ensemble membership.
   If it can → use it. **If it cannot → this is an FI gap: file an FI-repo issue and co-design
   with Sandro, do NOT work around it on the SAP3 side** (per CLAUDE.md FI rule). Sandro owns
   both the models and the FI, so this is a co-design point regardless.
3. **Reconcile with the "ensemble-first" principle** (`docs/architecture-context.md` /
   memory: "All forecasts are ensembles or quantiles; models reduce internally"). Is that a
   hard rule, or a default a model's FI requirement may narrow to control-only? A control-only
   model producing a single-member forecast must still fit the downstream ensemble-shaped
   storage/skill path (member_id=0). Clarify.
4. **Storage/provenance semantics** for a control-only forecast: it is a 1-member "ensemble"
   at `member_id=0`; confirm the forecast store, `NwpCycleSource`, and skill/verification paths
   handle a single-member cycle without special-casing.

## Non-goals

- Does not change the `fc`=member_id 0 / `pf`=1..50 identity (see
  [[project_recap_ifs_fc_hres_member0]]) or remove full-ensemble support.
- Does not re-open anything else 082 shipped beyond the forecast-membership + cycle-resolution
  path.

## References

- Merged adapter: `src/sapphire_flow/adapters/recap_gateway.py` (`fetch_forecasts`,
  `resolve_latest_cycle`, `_FC_MEMBER_ID`/`_PF_MEMBER_MIN`/`_PF_MEMBER_MAX`).
- FI contract: `interface/protocol.py`, `input/requirement.py`, `docs/model_interface.md`,
  `docs/input_requirement.md`.
- ECMWF timing: control disseminated ahead of perturbed members; Cycle 50r1 renames HRES →
  "ENS control" but preserves the earlier control dissemination.
  - https://www.ecmwf.int/en/about/media-centre/focus/2024/plans-high-resolution-forecast-hres-and-ensemble-forecast-ens
  - https://www.ecmwf.int/en/forecasts/datasets/set-i
  - https://confluence.ecmwf.int/display/DAC/Dissemination+schedule
