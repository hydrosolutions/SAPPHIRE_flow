# Plan 078 — forecast provenance for NWP-less forecasts

**Status**: DONE (grill-me resolved 2026-07-01; core fix shipped as epic-088 M4
— see "Resolution" below. The mixed-deployment verification/skill segmentation
(open question 4) remains a v1 follow-on, tracked there.)
**Phase**: v1 (forecast provenance / verification)
**Parent**: Plan 077 (optional NWP adapter wiring) — surfaced finding F2
**Related**: `NwpCycleSource` enum, forecast persistence schema, API forecast
endpoints, `input_quality` messaging
**Created**: 2026-06-24

---

## Resolution (epic-088 M4, 2026-07-01)

The grill-me session ran during epic-088 M4 (NWP-on operational forecasting),
which made NWP-consuming models real and thus made this fix load-bearing. The
converged design (implemented + tested SAP3-side, no ForecastInterface change —
provenance is orchestrator knowledge, not a model contract):

1. **Representation — hybrid of open-question 1's options (a)+(b)+(c).** Added
   `NwpCycleSource.RUNOFF_ONLY = "runoff_only"` (third enum value) AND made
   `nwp_cycle_reference_time` nullable (null ⇔ no NWP cycle) AND introduced a
   small forward-compatible `ForecastProvenance` record
   (`types/forecast.py`: `nwp_cycle_source` + `nwp_cycle_reference_time | None`,
   exposed via `OperationalForecast.provenance`) as the seed of the broader
   input-provenance object option (c) for v1.
2. **Schema migration** — `alembic/versions/0026_forecast_provenance_runoff_only.py`:
   `nwp_cycle_reference_time` → nullable, CHECK extended to
   `IN ('primary','fallback','runoff_only')`. Existing rows left as-is
   (pre-cleanup staging decision, per open-question 2). The downgrade coerces
   runoff-only rows back to `primary` + backfills the reference time from
   `issued_at` before restoring the two-value CHECK / NOT NULL (reversibility).
3. **API contract** — `ForecastDetail.nwp_cycle_reference_time` is now
   `datetime | None`; `nwp_cycle_source` gains the `runoff_only` value. Additive
   for consumers (no v0 consumers today).
4. **`input_quality` messaging** — the runoff-only branch emits a distinct
   NWP-category "No NWP forcing: runoff-only mode" detail, separate from
   primary/fallback (open-question 5).
5. **Recording** — `run_forecast_cycle.py` stamps `RUNOFF_ONLY` + null reference
   time in runoff-only mode; `PRIMARY`/`FALLBACK` (threaded from the adapter's
   resolved-cycle fallback signal) with the resolved cycle time otherwise.

**Deferred to v1 (open-question 4, the real downstream driver):** how skill
computation / hindcast verification / the review dashboard segment NWP-less
forecasts in a mixed deployment. Not needed until Nepal v1 mixes NWP and
runoff-only models operationally.

---

## Why this plan exists (and why it is parked)

Plan 077 makes `forecast-cycle` support a permanent **runoff-only / NWP-disabled**
mode. In that mode every stored forecast still records
`nwp_cycle_source = "primary"` and a non-null `nwp_cycle_reference_time` equal to
the cycle time — even though **no NWP was used**. This is semantically wrong and
is visible to API consumers.

Plan 077 only **documents** this as an accepted v0 limitation (its F2 Decision);
it deliberately does **not** fix it, because a correct fix is a cross-cutting
schema + API + verification change that is out of proportion to an unblock plan,
and because the limitation does not bite in v0 (no NWP-consuming models exist,
and the field is not used for skill or filtering today).

This plan owns the real fix. It is **parked** until:
1. an NWP-consuming model is on the horizon (v1 Nepal / a matured bias-correction
   archive), where **mixed** deployments — some models consume NWP, some do
   not — make provenance correctness load-bearing; and
2. a grill-me design session resolves the open branches below.

No subagent runs from this plan in its current state.

---

## Problem detail (verified against code, 2026-06-24)

- `NwpCycleSource` has exactly two values: `PRIMARY = "primary"`,
  `FALLBACK = "fallback"` (`src/sapphire_flow/types/enums.py:173-175`). There is
  no value for "no NWP".
- The forecast row enforces this at the DB boundary:
  `nwp_cycle_source` is `Text NOT NULL`, `server_default "primary"`, with
  `CheckConstraint("nwp_cycle_source IN ('primary', 'fallback')")`, and
  `nwp_cycle_reference_time` is `DateTime NOT NULL`
  (`src/sapphire_flow/db/metadata.py` ~614-619).
- The forecast cycle records `NwpCycleSource.PRIMARY` and
  `nwp_cycle_reference_time = resolved_cycle_time` unconditionally
  (`src/sapphire_flow/flows/run_forecast_cycle.py:518, 623-624`).
- Consumers:
  - `api/routes/api_forecasts.py:60` and `api/routes/api_stations.py:104` emit
    `nwp_cycle_source.value` in API responses.
  - `services/input_quality.py:22,70,84` only special-cases `FALLBACK` in
    human-readable messaging; it does not branch on a "no NWP" state.

So a runoff-only forecast is indistinguishable, via the API and the schema, from
one genuinely driven by the primary NWP cycle.

---

## Open design questions (for the grill-me session — not yet decided)

1. **Representation.** Pick among (and the session should pressure-test each):
   - add a `NwpCycleSource` value such as `NONE` / `NOT_APPLICABLE`;
   - make `nwp_cycle_reference_time` nullable and let null mean "no NWP";
   - introduce a broader **input-provenance** record (which input classes a
     forecast actually consumed: past target, past dynamic, future dynamic/NWP,
     static), of which "NWP cycle source" becomes one facet. This may supersede
     the narrow enum question and is the more future-proof option for v1.
2. **Schema migration.** Any of the above touches the `NOT NULL` +
   `CHECK (... IN ('primary','fallback'))` constraint → Alembic migration,
   `server_default` reconsideration, and a decision on **existing rows** (the
   pre-cleanup staging rows; leave as-is vs backfill).
3. **API contract.** `nwp_cycle_source` is currently a non-null string in the
   forecast and station responses. Decide the new contract (new enum value vs
   nullable field vs nested provenance object) and whether it is a breaking
   change for external consumers (API-first export; no v0 consumers today, but
   confirm before v1).
4. **Downstream semantics — the real driver.** How should skill computation,
   hindcast/verification, and the review dashboard treat NWP-less forecasts in a
   **mixed** deployment? E.g. should NWP-skill metrics exclude runoff-only
   forecasts; should verification segment by provenance.
5. **`input_quality` messaging.** Extend the human-readable input-quality string
   to express "no NWP (runoff-only)" distinctly from primary/fallback.

## Non-goals

- Re-opening the Plan 077 runoff-only mode itself — that ships independently with
  the documented limitation.
- Any change before v1 NWP-consuming models are real.

## Process

This plan is intentionally a scoping stub. Before it becomes actionable:
1. Run a **grill-me** session on the open design questions above to converge on a
   representation and migration strategy.
2. Re-draft this file with concrete phase/task structure + a JSON dependency
   graph, flip to `DRAFT` → `READY` per `docs/workflow.md`.

## Affected surfaces (preliminary — for scoping only)

- `src/sapphire_flow/types/enums.py` — `NwpCycleSource`
- `src/sapphire_flow/db/metadata.py` + a new Alembic migration
- `src/sapphire_flow/flows/run_forecast_cycle.py` — what is recorded per mode
- `src/sapphire_flow/api/routes/api_forecasts.py`, `api_stations.py` — response
  contract
- `src/sapphire_flow/services/input_quality.py` — messaging
- skill / hindcast / verification consumers — TBD in the grill-me
