---
status: DRAFT
created: 2026-07-22
plan: 140
title: Restore ICON-CH2-EPS NWP fetch — the STAC pagination cap is exceeded (production outage)
scope: Fix the live NWP forecast outage on the mac-mini — the MeteoSwiss STAC fetch aborts at the 800-page pagination cap, so no fresh ICON-CH2-EPS has landed since 2026-07-21 18:00 and every NWP-dependent model is failing. Re-benchmark + raise the cap, add observability, and evaluate a bounded fetch. Swiss.
depends_on: []
blocks: []
supersedes: []
---

# Plan 140 — Restore ICON-CH2-EPS NWP fetch (STAC pagination cap exceeded)

## Status

**DRAFT — production outage.** Diagnosed live on the mac-mini (2026-07-22, as the T1 step of Plan 138's
deploy) and grounded in live MeteoSwiss STAC probes. For a `/plan` round before READY. **Priority: this
blocks all NWP-dependent forecasting, including Plan 138** — deploying Plan 138 cannot produce `seasonal`
forecasts until this is fixed.

## Context — a live NWP outage, not a model problem

On the mac-mini (verified 2026-07-22 ~12:50 UTC): the latest `weather_forecasts` NWP cycle is **2026-07-21
18:00** — **~19 h stale** — and the `00/06/12` ICON-CH2-EPS cycles are missing. The forecast cycle keeps
COMPLETING, but its NWP fetch aborts every run:

```
nwp.fetch_failed  error='STAC pagination exceeded 800 pages'   (adapters/meteoswiss_nwp.py:707)
forecast_cycle.nwp_fetch_failed_aborting                       (flows/run_forecast_cycle.py:1674)
```

With no fresh NWP, **every NWP-dependent model fails** — `nwp_regression`, `seasonal_precip_runoff_regression`,
`nwp_rainfall_runoff` — and both BAFU stations (2009, 2091) have fallen through the PRIMARY chain to
`linear_regression_daily`@30 as the stored primary since 2026-07-22 00:00 (nothing stored after). *This is the
root cause of `seasonal`'s "0 forecasts" that Plan 138 set out to diagnose — it is an operational forcing
outage, not a model defect or a priority question.*

## Root cause — the 120 h window returns too many pages, and MeteoSwiss gives no server-side lever

`_fetch_grib_files` (`adapters/meteoswiss_nwp.py:661`) queries
`/collections/ch.meteoschweiz.ogd-forecasting-icon-ch2/items?datetime=<cycle>/<cycle+120h>&limit=100`
(`:680-682`) and walks every page (`:703-708`), filtering client-side to the target cycle
(`forecast:reference_datetime == cycle`, `:736`) and the 2-variable allowlist (`tp`, `t_2m` —
`PARAM_GROUPS` `:56-62`; `not_in_allowlist` skip `:738-743`).

The 120 h valid-time window overlaps **many** ICON cycles (each cycle's late timesteps fall in it), and each
cycle carries ~40 variables × ~21 members × hourly timesteps — so the result set is enormous. Confirmed by
live STAC probes (2026-07-22):

- **No server-side filtering** — MeteoSwiss ignores `filter=` on `/items` and `POST /search` 400s
  (`meteoswiss_nwp.py:684-690,722-732`, Plan 067 T1.e). The allowlist must stay client-side.
- **`sortby` is ignored** — `sortby=-datetime` / `±forecast:reference_datetime` all return identical
  ordering. Items are **ref_dt ASCENDING** (`meteoswiss_nwp.py:79-83`), so the target cycle (the newest) is
  **last** — we cannot reverse-order to early-stop.
- **Page size is capped at 100** — `limit=1000` still returns 100 features.

So there is **no server-side way to narrow the query**; the fetch must paginate the whole window. Plan 067
T1.f measured **552 pages for a 4-cycle overlap at MeteoSwiss's 24 h retention** and set the cap at 800
(`:69-76`). The cap is now exceeded — **something grew** (most likely MeteoSwiss extended retention, so the
120 h window overlaps more cycles; possibly more variables/members). Determining what grew is Task T1.

## Objective

Fresh ICON-CH2-EPS NWP flowing again on the mac-mini within the next scheduled forecast cycle, and the
NWP-dependent models (`nwp_regression`, `seasonal`, `nwp_rainfall_runoff`) producing stored forecasts —
with the pagination bound re-sized to the current catalog and instrumented so the next breach is caught
early, not as a silent outage.

## Non-goals

- **Not** a redesign of the NWP access strategy (a caching/incremental STAC walk, or petitioning MeteoSwiss
  for server-side filtering) — flagged as a follow-up if the treadmill worsens (§ Follow-up).
- **Not** changing the variable allowlist, the GRIB parsing, or any downstream model.
- **Not** the Plan 138 model deploy — that follows once NWP is restored (separate, already-merged work).

## Tasks

### T1 — benchmark the current catalog + identify what grew (sizes the fix)

- **Scope:** with a bounded, courteous probe against the live MeteoSwiss STAC (`data.geo.admin.ch/api/stac/v1`,
  collection `ch.meteoschweiz.ogd-forecasting-icon-ch2`), for a recent cycle's `datetime=<cycle>/<cycle+120h>`
  query, measure: (a) the **actual page count** to the target cycle's last item (walk to exhaustion or a hard
  probe cap), (b) how many **distinct `forecast:reference_datetime`** cycles the window returns (was 4 at 24 h
  retention — is it now more?), and (c) the **per-cycle item count** (variables × members × timesteps). Compare
  to Plan 067 T1.f's 552 pages / 4 cycles to pinpoint what increased (retention vs variables vs members). Do
  this as an ad-hoc probe (heredoc), not committed code.
- **Files:** none (investigation); findings recorded in this plan.
- **Verification:** the current page count + the growth cause are stated with numbers, so T2 sizes the cap on
  evidence, not a guess.

### T2 — raise the pagination cap on the benchmark + add observability

- **Scope:** raise `_MAX_PAGINATION_PAGES` (`meteoswiss_nwp.py:76`) to **T1's measured page count × ~1.5
  margin** (mirroring Plan 067's sizing rationale; update the comment `:69-76` with the new benchmark + date).
  **Add observability** so the next breach is not silent: log the **actual page count + matched target-cycle
  item count** at the end of each successful fetch, and emit a **WARNING when the page count exceeds ~80 % of
  the cap** (an early-warning that the treadmill is approaching the limit again). Keep the abort-on-cap
  behaviour (it must fail loudly, not silently truncate a cycle's NWP).
- **Files:** `src/sapphire_flow/adapters/meteoswiss_nwp.py`; `tests/unit/adapters/test_meteoswiss_nwp.py`
  (a faked multi-page STAC response that (a) completes under the new cap, (b) still raises `AdapterError` past
  the cap, (c) emits the ≥80 %-of-cap WARNING). Red-first.
- **Verification:** `uv run pytest tests/unit/adapters/test_meteoswiss_nwp.py`; the cap-exceeded path still
  raises; the near-cap WARNING fires at the threshold.

### T3 — evaluate a bounded fetch (early-stop) — investigate, implement only if provably safe

- **Scope:** the target cycle is the **newest** and items are **ref_dt ascending**, so it is **last** — a
  naive "stop when the target cycle is seen" does not help. Evaluate whether a **safe** early-stop exists:
  e.g. stop once the matched target-cycle item count reaches the **expected** count (2 allowlisted vars × the
  member count × the timestep count) — but ONLY if that expected count is reliably derivable (member/timestep
  counts are stable and known) so early-stop can never truncate a cycle. If it is not provably safe (variable
  member/timestep counts), **defer** it to the § Follow-up redesign and rely on T2's raised cap. Record the
  decision + evidence in this plan; do not ship a fragile early-stop that risks a silently-short NWP cycle.
- **Files:** `meteoswiss_nwp.py` + tests **only if** T3 concludes early-stop is provably safe; otherwise a
  documented no-op.
- **Verification:** either a red-first early-stop test proving it collects the full target cycle and stops, or
  a recorded "deferred — not provably safe" decision.

### T4 — deploy + verify fresh NWP restored

- **Scope:** deploy to the mac-mini via the **repo standard upgrade sequence in overlay form**
  (`docs/standards/cicd.md`; `docker compose -f docker-compose.yml -f docker-compose.macmini.yml …`, token
  exported, `run --rm --build init`, `up -d` — never a bare `docker compose up`, see
  `reference_macmini_ssh_access`). Trigger a forecast cycle; confirm a **fresh NWP cycle** lands in
  `weather_forecasts` (cycle_time advances past 2026-07-21 18:00) and the NWP-dependent models produce stored
  `forecasts` again for 2009 + 2091.
- **Files:** deploy actions (version bump only).
- **Verification:** live — `weather_forecasts` max(cycle_time) is current; `nwp_regression` (and the others)
  reappear as stored primaries; the `nwp.fetch_failed` / `nwp_fetch_failed_aborting` errors stop.

## Dependency graph

```json
{
  "phases": [
    { "id": "benchmark", "tasks": ["T1"], "parallel": false, "depends_on": [] },
    { "id": "fix", "tasks": ["T2", "T3"], "parallel": false, "depends_on": ["benchmark"],
      "note": "T2 (cap + observability) is the guaranteed fix; T3 (early-stop) only lands if provably safe, else defers." },
    { "id": "deploy", "tasks": ["T4"], "parallel": false, "depends_on": ["fix"] }
  ]
}
```

## Follow-up (out of scope — separate plan if the treadmill worsens)

A raised cap is a stopgap: the fetch walks the whole 120 h window every cycle (tens of thousands of items,
~98 % discarded) and the page count grows with MeteoSwiss retention. A durable fix would need one of: (a)
MeteoSwiss adding server-side `forecast:reference_datetime` filtering (petition upstream); (b) a
caching/incremental STAC walk that reuses prior pages; or (c) a different ICON access path. File this when
T2's WARNING starts firing regularly.

## Exit gates

```bash
uv run ruff format --check src/ tests/ && uv run ruff check src/ tests/
uv run pyright src/
uv run pytest
```

## References

- `adapters/meteoswiss_nwp.py` (`_fetch_grib_files` `:661`; STAC query `:680-682`; pagination cap check
  `:703-708`; `_MAX_PAGINATION_PAGES=800` `:76` w/ Plan 067 benchmark comment `:69-76`; ref_dt-ascending note
  `:79-83`; client-side ref_dt + allowlist filter `:736-743`; PARAM_GROUPS `:56-62`).
- `flows/run_forecast_cycle.py` (`nwp.fetch_failed` `:1009`, `forecast_cycle.nwp_fetch_failed_aborting`
  `:1674`).
- `config.toml` (`stac_base_url` `:382`, `stac_collection` `:383`).
- Live evidence (2026-07-22): NWP stale since 07-21 18:00; STAC probes — no `filter=`, `sortby` ignored,
  page-size capped at 100.
- memory `project_nwp_v0_variable_allowlist` (tp + t_2m only), `project_nwp_off_on_restart_blackout`
  (the prior NWP-dark pattern), `reference_macmini_ssh_access` (deploy overlay).
- Plan 067 (the STAC query design + the 552-page/4-cycle/24h-retention benchmark this supersedes).
