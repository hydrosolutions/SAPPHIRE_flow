# Plan 111b — Mac-mini deployment runbook: BAFU forecast collector

**Status:** READY (runbook) — dev collection validated 2026-07-10; deploy wiring in PR #73.
**Owner:** Bea (marti@hydrosolutions.ch)
**Companion to:** [Plan 111](111-bafu-forecast-benchmarking.md) (the route-C collector, merged #72).
**Related:** [Plan 091](091-macmini-nwp-on-data-collection.md) (mac-mini NWP-on runbook, same deploy shape); [[project_plan111_bafu_collector]].

> Operational steps to run the route-C BAFU forecast collector **hourly on the
> mac-mini**. Collection was validated end-to-end on the dev machine first (53
> variants, 32 638 rows, atomic writes clean, idempotent re-run). This runbook is
> the server rollout.

---

## What it does (recap)

Hourly, the `collect-bafu-forecasts` Prefect flow scrapes BAFU's **public** forecast
plots (`hydrodaten.admin.ch`, Plotly-JSON) for ~54 gauges and archives them to a
**quarantined** parquet store. Per station / issue-time it captures 5 traces — `Median`,
`25.-75. Percentile` (p25/p75 band), two Min/Max envelopes, and recent `Measured` obs —
over a **~5-day hourly horizon** (~118 steps). **Quantiles, not ensemble members** —
BAFU does not expose members through this channel (drives Plan 111 G2: pinball@q25/50/75,
not CRPS).

**Four safeguards are in the code**, no config needed: identifying `User-Agent` per
request; quarantine (writes only under the configured archive path; never the DB / a
`ModelId`; blank path ⇒ no-op); evaluation-only; polite client (1 req/s, retry cap,
raw-payload archival, atomic temp+rename writes).

---

## Prerequisites

- **#72 merged** (the collector code) — on `main`.
- **#73 merged** (this deploy wiring) — adds the deployment spec, the compose volume, and
  the overlay switch. Once merged, all three knobs below are already in the tree; the
  rollout is just a rebuild.

The three knobs #73 installs:
1. `register_deployments.py` — `collect-bafu-forecasts`, cron `SCHEDULE_COLLECT_BAFU_FORECASTS`
   (default `0 * * * *`), `concurrency_limit=1`, default work pool.
2. `docker-compose.yml` — `bafu_forecast_archive` named volume mounted at
   `/data/bafu_forecasts` on `prefect-worker` (mirrors `nwp_grids`).
3. `config/overlays/mac-mini.toml` — `[adapters.bafu_forecast].archive_base_path =
   "/data/bafu_forecasts"`. **This path is the enable switch** — unset ⇒ the flow no-ops.

---

## Deploy (on the mac-mini)

```bash
cd <sapphire checkout on the mac-mini>
git pull                       # main, with #72 + #73

# Version pin (live-host convention): bump VERSION in .env to the new tag.
$EDITOR .env                   # VERSION=0.1.563  (or the tag you are deploying)

# Rebuild + restart. The compose entrypoint runs register_deployments, so the
# hourly schedule registers itself — no manual Prefect step.
docker compose -f docker-compose.yml -f docker-compose.macmini.yml up -d --build
```

`register_deployments` is idempotent — re-running only creates/updates. (See
`docs/standards/cicd.md` §deploy.)

---

## Verify

1. **Deployment exists & scheduled**
   ```bash
   docker compose exec prefect-worker prefect deployment ls | grep collect-bafu-forecasts
   ```
   Expect the deployment present with cron `0 * * * *`.

2. **Trigger one run now** (don't wait for the top of the hour)
   ```bash
   docker compose exec prefect-worker prefect deployment run 'collect-bafu-forecasts/collect-bafu-forecasts'
   ```
   Watch the worker logs for the completion event:
   ```bash
   docker compose logs -f prefect-worker | grep bafu_forecast
   # expect: bafu_forecast.complete rows_archived=~32000 variants_fetched=~53 variants_failed=0
   ```

3. **Archive landed in the volume**
   ```bash
   docker compose exec prefect-worker sh -c 'ls /data/bafu_forecasts/parsed | wc -l; du -sh /data/bafu_forecasts'
   # expect ~53 parquet files, ~2 MB for one cycle
   ```

4. **Idempotency** — trigger a second run before BAFU re-issues; expect
   `variants_fetched=0 variants_skipped_dedup=53` and no file growth.

---

## Operate

- **Cadence** — retune without a redeploy by setting `SCHEDULE_COLLECT_BAFU_FORECASTS`
  in the worker environment (e.g. `*/30 * * * *` to catch mid-hour re-issues), then
  re-run `register_deployments` (or `up -d`).
- **Disk** — ~36 MB/day raw+parsed, **permanent retention by design** (forward-only; no
  pruning — the endpoint holds no history, so every cycle is unrecoverable if dropped).
  ~13 GB/year; comfortable on the SSD. Fold into the Plan 105 disk budget when convenient;
  no pruning needed near-term.
- **Health** — a fetch/parse failure raises and is logged per-station (`variants_failed`);
  a total failure (inventory unreachable) fails the flow run visibly in Prefect. The
  dedicated Flow 4 staleness hook is still deferred (`# TODO(plan-111)`).

---

## Disable / discard / rollback

- **Pause collection** (keep the archive): remove/blank
  `[adapters.bafu_forecast].archive_base_path` in the overlay and `up -d` — the flow
  no-ops on the next tick. Or `prefect deployment pause`.
- **Discard the whole archive** (the quarantine guarantee):
  ```bash
  docker compose down
  docker volume rm sapphire_bafu_forecast_archive   # exact name via `docker volume ls`
  ```
  One command wipes every collected forecast — nothing to unwind in the DB, because
  the collector never touches it.
- **Full rollback**: revert #73 (drops the spec + volume + overlay switch) and redeploy,
  or pin `.env` `VERSION` back to the prior tag and `up -d`.

---

## Out of scope (still gated)

- **Scoring / the benchmark itself** — the G3 scorer and any *published* comparison stay
  gated on the BAFU licence + publication-rights reply (Plan 111 Gate G1; the request is
  not yet sent). This runbook only starts the **collection** clock.
- **The BAFU letter / framing decision** — owner's, unresolved.
