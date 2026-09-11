---
id: 127
title: fc-first minimal unblock — tolerant pf fetch + SINGLE-model bare columns
status: DRAFT
depends_on: [082]
owner: unassigned
created: 2026-07-18
---

# Plan 127 — fc-first minimal unblock

> **Scope-locked (owner, 2026-07-18) — the minimal, adapter-local fix that makes Sandro's
> control-only (`SINGLE`) Nepal models forecast on the live Gateway.** Chosen over the full
> model-driven membership machinery (which keeps escalating — [[Plan 123]]) because it needs no
> flow-level membership aggregation, no shared-protocol change, and no NONE-provenance work.
> Two localized fixes. Implement directly with red-first tests + Codex review.

## Problem (two concrete symptoms; both confirmed against code + live HRU `12300`)

1. **The `pf` loop aborts the whole fetch when `pf` is unavailable.**
   `RecapGatewayForecastAdapter.fetch_forecasts` fetches `fc` then loops
   `for member in range(_PF_MEMBER_MIN, _PF_MEMBER_MAX + 1)` (`recap_gateway.py:747-759`),
   `_accumulate_member` → `_guarded_fetch` → the Gateway. ECMWF disseminates `fc` **before** `pf`
   (Cycle 50r1 preserves this), so during that window `pf` is absent and the member fetch raises,
   propagating out of `fetch_forecasts` and failing the NWP fetch. A control-only model needs no
   `pf`.
2. **`SINGLE`-model forcing gets member-suffixed columns it can't read.** `_pivot_nwp_records`
   (`operational_inputs.py:181`) emits `precipitation_0` (etc.) whenever any record has a
   `member_id`, but FI `SINGLE`'s `_frame_with_column` (`forecast_interface.py:987`) needs the
   **bare** `precipitation` → `ConfigurationError`. This happens **whether or not `pf` is present**
   (a `SINGLE` model on a complete-ensemble cycle still gets suffixed columns), so the fix must key
   on the **model requirement**, not on which members were fetched.

## Fix (two localized changes)

### 1. Tolerant `pf` fetch (adapter)

In the `pf` member loop (`recap_gateway.py:747-759`): if a `pf` member fetch raises a
**data-unavailable** error, log and **`break`** — keep the `fc` (+ any `pf` members already
accumulated); do not propagate/abort. Only data-unavailable is tolerated; config/auth/other errors
still propagate.
- **Verify the exact exception first:** `_map_recap_error` (`recap_gateway.py:~296-311`) maps the
  structured `source_data_missing` code → `RecapDataUnavailableError`; live probing showed a
  missing-`pf` "No IFS dataset found" surfacing as `RecapDataUnavailableError`. Confirm against the
  installed client's error shape and catch exactly that class (NOT bare `Exception`, NOT config/auth).
- Break on the **first** missing `pf` member (during the fc-before-`pf` window all `pf` are absent
  together, so one probe is enough) → ~1 wasted call/cycle, not 50. (Never-probing `pf` for a
  control-only run is the efficiency win deferred to [[Plan 123]].)

### 2. `SINGLE`-model bare columns (input assembly)

Key the bare-vs-suffixed column decision on the **model's `ensemble_mode`**, which already flows
through to input assembly (`ModelDataRequirements.ensemble_mode`, default `SINGLE`,
`types/model.py:271`), NOT on which members are present:
- Thread `reqs.ensemble_mode` into `_pivot_nwp_records` (currently takes only
  `future_dynamic_features`).
- When `ensemble_mode == SINGLE`: select the **control** rows (`member_id ∈ {None, 0}` — snow is
  `None`, IFS control/`fc` is `0`) and emit **bare** columns (`precipitation`, not
  `precipitation_0`); drop any `pf` members. Detection is on the requirement; the pivot only
  applies column naming (per the review: don't infer control-only from post-aggregation rows).
- When `ensemble_mode == ENSEMBLE`: **unchanged** — member-suffixed columns for fan-out.

## Acceptance (red-first)

- **Abort fix (RED first):** a `fetch_forecasts` where `pf` is unavailable returns `fc`-only
  records instead of raising/aborting. (Red against current: it raises.)
- **SINGLE bare columns (RED first):** with `ensemble_mode == SINGLE`, input assembly yields a
  BARE `precipitation` column and the FI `SINGLE` model forecasts — **both** when `pf` is absent
  (fc-only records) AND when `pf` is present (fc + 50 pf records; bare column taken from the
  control member). (Red against current: suffixed → `ConfigurationError`.)
- **ENSEMBLE unchanged (no regression):** `ensemble_mode == ENSEMBLE` still yields member-suffixed
  columns and a full-member fetch; existing ensemble tests stay green.

## Non-goals (deferred)

- **Model-driven membership fetch** (skip `pf` entirely for `SINGLE`/`NONE` runs; NONE skip +
  staleness/provenance) → [[Plan 123]] (efficiency/completeness; not deployment-critical now).
- **ENSEMBLE cycle resolution / mixed runs** → [[Plan 126]].
- Does NOT change the shared `WeatherForecastSource` protocol, the flow's membership logic, or the
  drift/health/provenance paths.
