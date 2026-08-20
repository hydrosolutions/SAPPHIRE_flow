# Mac-mini deploy runbook

The procedure for deploying a new version to the mac-mini staging host, written against the
**0.1.758 → 0.1.775** deploy of 2026-08-20 and kept as the template for the next one. Companion to
[`../deployment/mac-mini-staging.md`](../deployment/mac-mini-staging.md) (first-time install) and
[`../standards/cicd.md`](../standards/cicd.md) (the gate + rollback contract).

**EXECUTED 2026-08-20. All 7 acceptance checks passed, including the 12:00Z forecast cycle.**
Kept as the procedure template for the next deploy. Rollback anchor `sapphire-flow:rollback-backup`
-> 0.1.758 is still on the host.

## What you are actually deploying

**Not two plans — nine PRs and ~3,900 changed lines.** The mini has been on 0.1.758 since
Plan 176; five code-bearing PRs have landed since:

| PR | What it changes at runtime |
|---|---|
| #188 Plan 186 | **Whole-graph ingest** — one LINDAS request per run instead of one per station |
| #193 Plan 189 | Audit publish-lag horizon; **collector cron 18 → 24 runs/hour**; staleness thresholds |
| #192 Plan 151 T5–T7 | **~3,000 lines of new track-resolution/assembly services.** Commit says *dormant* — VERIFY before assuming it is inert |
| #186 Plan 162 T5 | Backup restore-rehearsal fix |
| #187/#191/#185 | CI only — no runtime effect |
| #189/#196/#194 | added 2026-08-19/20: restore-rehearsal `is_called` fix, Plan 151 **T8a** (dormant), ERA5-Land multi-variable safety |

**No DB migrations** (`alembic/versions/` unchanged since v0.1.758) — so this is code-only, and
rollback does not cross a schema boundary. That is the single biggest risk removed.

**Compose changes are one line**: the collector cron. No topology change, no new service.

## Threshold changes that alter alerting behaviour

| Constant | Was | Now |
|---|---|---|
| `_STALE_MEASUREMENT_THRESHOLD` (flow) | 3 h | **30 min** |
| `BAFU_OBS_STALE_THRESHOLD` (watchdog) | 3 h | **15 min** |

Expect **faster, louder** alerting after this deploy. A stall that used to sit silent for hours
now pages in ~15 min. If an alert fires shortly after deploy, check whether it is a real stall
before assuming the deploy broke something — the alert may simply be working for the first time.

## Procedure

    ssh sapphire@192.168.1.136
    cd ~/SAPPHIRE_flow
    export PATH=/usr/local/bin:$PATH
    export DOCKER_HOST=unix:///var/run/docker.sock

    # 0. Rollback anchor FIRST (cicd.md step 0) — images are local-only, nothing to pull back
    docker tag sapphire-flow:$(grep '^VERSION' .env | cut -d= -f2) sapphire-flow:rollback-backup

    # 1. Update + set VERSION (both, or the image tag lies about what is running)
    git pull --ff-only origin main
    sed -i "" "s/^VERSION=.*/VERSION=0.1.775/" .env

    # 2. Deploy — BOTH overlays, RECAP token exported, --build because code changed
    export RECAP_DG_CLIENT_TOKEN=$(cat secrets/recap_dg_client_token)
    docker compose -f docker-compose.yml -f docker-compose.macmini.yml up -d --build

## Acceptance — in this order

    # a. Running code is what you think (read from INSIDE the container, not the image tag)
    docker compose exec -T prefect-worker python -c "import sapphire_flow; print(sapphire_flow.__version__)"
    #    expect 0.1.775

    # b. init succeeded
    docker inspect sapphire_flow-init-1 --format "{{.State.ExitCode}}"    # expect 0

    # c. Collector cron picked up the 24-run list, and both deployments are on the ingest pool
    docker compose exec -T prefect-worker prefect deployment inspect \
      collect-bafu-observations/collect-bafu-observations | grep -A2 cron
    for d in ingest-observations collect-bafu-observations; do
      docker compose exec -T prefect-worker prefect deployment inspect "$d/$d" | grep work_pool_name
    done

    # d. Plan 186's whole-graph ingest: ONE request per run, stations_polled unchanged
    docker compose logs --since 10m prefect-worker-ingest | grep -E "whole_graph_fetch_completed|ingest.starting"

    # e. Plan 189's audit — window ending 30+ MIN IN THE PAST, or trailing slots read as skipped
    docker compose exec -T prefect-worker python -m sapphire_flow.cli.bafu_observation_audit \
      --base-path /data/bafu_observations \
      --start "$(date -u -v-2H +%Y-%m-%dT%H:00:00Z)" --end "$(date -u -v-1H +%Y-%m-%dT%H:00:00Z)" \
      | grep -E "complete|skipped"
    #    ⚠️ THE `Z` IS REQUIRED. A naive timestamp dies with
    #    `ValueError: Naive datetime not allowed` before the audit runs.
    #    expect ~6 slots/hour present, 0 missing, and a skipped_too_recent count of 0 for a past window

    # f. Plan 151 T5-T7 is genuinely dormant — no new flow runs, no new deployments
    docker compose exec -T prefect-worker prefect deployment ls | wc -l   # compare to before

## Rollback (code-only — no schema step)

    docker compose stop prefect-worker prefect-worker-ingest
    sed -i "" "s/^VERSION=.*/VERSION=0.1.758/" .env      # or the rollback-backup tag from step 0
    docker compose -f docker-compose.yml -f docker-compose.macmini.yml up -d
    docker compose run --rm init                          # re-register deployments at the old cron
    # Verify BOTH deployments' pools afterwards — a deployment left on a workerless pool is
    # silently dead and only surfaces later as a stale-heartbeat alert (cicd.md § rollback).

## Known traps, all previously hit on this host

- **Docker engine cannot be started over SSH.** It needs the mini's GUI session. If `docker` is
  unreachable, that is the problem — check `curl -m8 http://192.168.1.136:8000/api/v1/health` first,
  which needs no socket.
- **Both overlays, every time.** A plain `docker compose up` silently drops the mac-mini overlay
  and with it NWP, both BAFU collectors, and the API port binding.
- **`.env` VERSION must be set or the image tag lies.** It sat at 0.1.710 while 0.1.753 ran.
- **`docker compose ps` reports the tag, not the running code.** Always read `__version__` from
  inside the container.
