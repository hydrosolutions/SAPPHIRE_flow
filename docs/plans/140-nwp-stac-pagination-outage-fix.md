---
status: READY
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

**READY (owner 2026-07-22) — production outage.** Diagnosed live on the mac-mini (as the T1 step of Plan
138's deploy) and grounded in live MeteoSwiss STAC probes; **T1 benchmark is DONE** (861 pages, sizing the
cap at 1500). The fix is small and low-risk — a cap constant + observability + tests — so it goes straight to
`/implement`. **Priority: this blocks all NWP-dependent forecasting, including Plan 138** — deploying Plan 138
cannot produce `seasonal` forecasts until this is fixed.

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
(`:69-76`). **T1 (below) re-benchmarked the live catalog: 861 pages now — still 4 cycles / 24 h retention, but
+56 % items-per-cycle** (MeteoSwiss added variables/members/timesteps per cycle). The 861 > 800 by ~7 % is the
whole outage.

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

### T1 — benchmark the current catalog + identify what grew — ✅ DONE (2026-07-22)

- **Scope:** a bounded, courteous walk of the live MeteoSwiss STAC for a recent cycle's
  `datetime=<cycle>/<cycle+120h>` query, following `next` links to exhaustion (2000-page safety cap).
- **Result:** **861 pages / 86,072 items** (reached exhaustion — the true count is 861, just past the 800 cap;
  that ~7 % overshoot is the entire outage). **Still exactly 4 distinct cycles** in the window (`07-21 12:00`,
  `07-21 18:00`, `07-22 00:00`, `07-22 06:00`) — retention is **unchanged** from Plan 067's 4-cycle/24 h
  baseline. What grew is **items-per-cycle**: ~13,800 → ~21,518, **+56 %** (MeteoSwiss added
  variables/members/timesteps per cycle, not more cycles). The target (newest) cycle's items are **last**
  (ref_dt ascending), confirming early-stop (T3) cannot help.
- **Files:** none (ad-hoc probe); recorded here.
- **Verification:** ✅ page count = 861, growth cause = items/cycle (+56 %). T2's cap is sized on this.

### T2 — raise the pagination cap on the benchmark + add observability

- **Scope:** raise `_MAX_PAGINATION_PAGES` (`meteoswiss_nwp.py:76`) from **800 → 1500** (T1's 861 × ~1.7 —
  generous headroom since items/cycle is *growing*; the cap is only a safety ceiling, normal fetches exhaust at
  ~861 well before it, so a higher ceiling does not slow the happy path). Update the comment `:69-76` with the
  new benchmark (861 pages, 4 cycles, +56 % items/cycle, 2026-07-22). **Add observability** so the next breach
  is not silent: log the **actual page count + matched target-cycle item count** at the end of each successful
  fetch, and emit a **WARNING when the page count exceeds ~80 % of the cap** (i.e. ≥1200) — an early warning
  that the treadmill is approaching the limit again. Keep the abort-on-cap behaviour (it must fail loudly, not
  silently truncate a cycle's NWP).
- **Files:** `src/sapphire_flow/adapters/meteoswiss_nwp.py`; `tests/unit/adapters/test_meteoswiss_nwp.py`
  (a faked multi-page STAC response that (a) completes under the new cap, (b) still raises `AdapterError` past
  the cap, (c) emits the ≥80 %-of-cap WARNING). Red-first.
- **Verification:** `uv run pytest tests/unit/adapters/test_meteoswiss_nwp.py`; the cap-exceeded path still
  raises; the near-cap WARNING fires at the threshold.

### T3 — early-stop — ❌ DEFERRED (T1 evidence: cannot help)

- **Decision (from T1):** the target cycle is the **newest** and items are **ref_dt ASCENDING**, so the target
  cycle's items are **last** in the walk (the 3 older, useless cycles come first, ~645 of the 861 pages). An
  early-stop "once the target cycle is complete" therefore stops only at the very end — it saves **nothing**.
  And `sortby` is ignored (probed), so we cannot reverse the order to bring the target first. **No safe
  early-stop exists** given MeteoSwiss's fixed ascending order; deferred to the § Follow-up redesign (which
  would need a different access path or upstream filtering). T2's raised cap is the fix.
- **Files:** none (no code — documented no-op).
- **Verification:** ✅ decision recorded with T1 evidence.

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
    { "id": "benchmark", "tasks": ["T1"], "parallel": false, "depends_on": [], "note": "DONE (861 pages)." },
    { "id": "fix", "tasks": ["T2"], "parallel": false, "depends_on": ["benchmark"],
      "note": "T2 (cap 800->1500 + observability + tests) is the entire code fix. T3 (early-stop) is DEFERRED — T1 proved it cannot help." },
    { "id": "deploy", "tasks": ["T4"], "parallel": false, "depends_on": ["fix"], "note": "On the mac-mini, after merge — restores NWP." }
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
