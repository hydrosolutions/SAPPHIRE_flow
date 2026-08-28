# Nepal 12300 gateway-forcing feed — mac-mini runbook

**Status:** READY. **Owner:** Bea (marti@hydrosolutions.ch).
**Spec:** [Plan 192](../plans/192-recap-second-stack-12300-operational-test.md) (Stage B, light shape).
**Related:** [recap probe runbook](recap-probe-runbook.md) — the source-side twin of this feed.

> A once-daily, unattended fetch of recap Data Gateway IFS forcing for Nepal test basin **12300**,
> into a **standing, isolated Postgres** on the mac mini. Its purpose is to accumulate operating
> experience with the Gateway and to produce the evidence needed to report Gateway issues upstream.
> It touches **nothing** in the Swiss/BAFU stack.

## Why this shape (and not a second full stack)

`[adapters.weather_forecast].type` is a deployment-wide selector, so 12300 cannot co-host with the
Swiss stations on one stack. But a full second Compose stack (second Prefect server, worker,
deployment registration) buys nothing here: the daily run passes an **explicit 00Z cycle**, which
Prefect's own scheduler cannot do (the flow floors its cycle from `clock()` at *execution* time), so
the trigger is a host timer either way. What is left is one Postgres plus a script — which also
removes the ports-concatenation trap, the backup cron, and the shared `sapphire-flow:${VERSION}`
image-tag risk a second stack would carry. See Plan 192 D8.

## What runs

| Piece | Path |
|---|---|
| standing store | `docker-compose.nepal-forcing.yml` (project **`sapphire-nepal`**, Postgres only, no host ports) |
| one-time seed | `scripts/nepal_forcing_seed.sql` (idempotent) |
| the daily run | `scripts/nepal_forcing_run.py` (single-shot; fed via stdin — `scripts/` is never in the image) |
| timer wrapper | `scripts/launchd/run-nepal-forcing.sh` |
| schedule | `scripts/launchd/ch.hydrosolutions.sapphire-nepal-forcing.plist` — **16:00 local** |

**Why 16:00 local (≈14:00 UTC):** the 00Z ensemble is not complete early. Measured 2026-08-20: `pf`
absent at 09:27 UTC, all 50 members present by 12:59 UTC. An early run gets `fc` only.

## Install

> ### 🔴 Run Compose from the repo checkout — never a staging copy
>
> Every `docker compose` command for this project **must** be run with
> `/Users/sapphire/SAPPHIRE_flow` as the working directory. The compose file declares its secret
> as a **relative** path (`file: ./secrets/nepal_db_password`), which Compose resolves to an
> **absolute** bind-mount source and **bakes into the container** at create time, along with
> `com.docker.compose.project.working_dir`. Deploy from a scratch directory and that absolute path
> is what the container keeps forever — the repo copy is never consulted again.
>
> This is not hypothetical: the 2026-08-20 install ran from `/tmp/nepal-stage`, macOS cleaned
> `/tmp`, and on the next Docker restart the container died with **exit 127** and could not be
> recreated. See [Troubleshooting](#troubleshooting-container-wont-start-exit-127).

```bash
cd /Users/sapphire/SAPPHIRE_flow && git pull

# 1. the store's password (never committed)
printf '%s' "$(openssl rand -hex 24)" > secrets/nepal_db_password && chmod 600 secrets/nepal_db_password

# 2. bring up the standing Postgres (its own project — Swiss stack untouched)
#    MUST be run from /Users/sapphire/SAPPHIRE_flow — see the warning above.
docker compose -p sapphire-nepal -f docker-compose.nepal-forcing.yml up -d

#    Confirm the container recorded the PERSISTENT path, not a scratch one:
docker inspect sapphire-nepal-postgres-1 \
  --format '{{index .Config.Labels "com.docker.compose.project.working_dir"}}'
#    -> must print /Users/sapphire/SAPPHIRE_flow

# 3. migrate + seed (IMAGE = whatever the Swiss stack runs)
IMAGE=$(docker inspect --format '{{.Config.Image}}' sapphire_flow-prefect-worker-1)
docker run --rm --network sapphire-nepal_nepal \
  -e DB_PASSWORD="$(cat secrets/nepal_db_password)" \
  -e DATABASE_URL_TEMPLATE=postgresql+psycopg://sapphire@postgres:5432/sapphire \
  "$IMAGE" alembic upgrade head
docker exec -i sapphire-nepal-postgres-1 psql -U sapphire -d sapphire \
  -v ON_ERROR_STOP=1 < scripts/nepal_forcing_seed.sql

# 4. prove it end to end BEFORE scheduling it
bash scripts/launchd/run-nepal-forcing.sh; echo "exit=$?"

# 5. schedule
cp scripts/launchd/ch.hydrosolutions.sapphire-nepal-forcing.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/ch.hydrosolutions.sapphire-nepal-forcing.plist
launchctl enable gui/$(id -u)/ch.hydrosolutions.sapphire-nepal-forcing
```

> `init` is **never** run for this project — it would also call `register_deployments`, which needs a
> Prefect server this stack deliberately does not have.

## Verify

```bash
launchctl list | grep nepal-forcing
tail -3 ~/Library/Logs/sapphire-nepal-forcing.jsonl        # one record per run
tail -5 ~/Library/Logs/sapphire-nepal-forcing.summary.log  # terse ok=/rows=
tail -20 ~/Library/Logs/sapphire-nepal-forcing.launchd.log # infra failures only
```

A healthy record looks like `ok=true`, `members=51`, `steps=84`, `horizon_days≈14.75`, `rows=8568`
(2 parameters × 51 members × 84 steps).

## Reading the record — this is the point of the feed

The run **fails loudly** (non-zero exit, `ok=false`) on three distinct conditions, each of which is a
reportable Gateway observation rather than a bug in us:

| `degraded_reason` / `error_code` | Meaning |
|---|---|
| `no_rows_stored` | the fetch returned nothing for the requested cycle |
| `short_horizon_<n>d` | the Gateway served a **short** cycle (a 06Z-style ~2-day run) where a 15-day 00Z run was expected |
| `source_data_missing` | the 00Z cycle was not published, or has aged past retention (~4 days) |

`members < 51` in an otherwise-ok record means the ensemble was incomplete at run time — evidence
that the schedule is too early, not that the Gateway is broken.

## Known Gateway issues worth reporting upstream

**Re-confirm every item immediately before sending — this Gateway's behaviour moved twice within a
single day on 2026-08-20, in both directions.** Two examples from that day, both worth citing *as
intermittency* rather than as fixed defects:

- **The stitched endpoints are INTERMITTENT, not broken.** At 09:27 UTC `ecmwf.operational` and
  `ifs_gap_fill` both hard-failed (`No IFS dataset found for run_date 2026-08-14 and run_hour 12`),
  because ERA5-Land ended 08-13 while IFS runs were retained only from 08-16 — the intervening days
  were coverable by nothing. By 13:18 UTC the **same call succeeded**: 348 rows, 2026-08-05 →
  2026-09-03, `era5_land` 216 + `ifs` 132. The Gateway had backfilled the missing cycles. So
  retention is **not** a simple sliding window, and "operational is dead" is a conclusion with a
  shelf life of hours.
- **`pf` completeness is time-of-day dependent.** Absent at 09:27 UTC, all 50 members present by
  12:59 UTC for the same 00Z cycle.

Still believed to hold (but re-check):

- `subdaily_resolution=3` returns **HTTP 500**.
- The client writes its temp parquet to `Path.cwd()`, which fails in any read-only container.
- Pure `era5_land_reanalysis` past its edge hard-fails rather than truncating (expected; the edge was
  ~7 days back on 2026-08-20).

## Troubleshooting: container won't start (exit 127)

**Symptom.** `docker ps -a` shows `sapphire-nepal-postgres-1  Exited (127)`, and the daily run fails
because the network exists but nothing is listening on it. `docker inspect` gives the real reason:

```
failed to fulfil mount request: open
/host_mnt/private/tmp/nepal-stage/secrets/nepal_db_password: no such file or directory
```

**Cause.** The container was created from a working directory that no longer exists (typically a
staging copy under `/tmp`, which macOS cleans). Compose froze that absolute path into the container's
bind-mount config, so `restart: unless-stopped` retries forever against a path that is gone. Exit 127
here is the OCI runtime failing to *create* the container — it is **not** a Postgres error, and
nothing is wrong with the database.

**The data is safe.** The store lives in the named volume `sapphire-nepal_nepal_pgdata`, which is
independent of the container. Confirm before touching anything:

```bash
docker volume inspect sapphire-nepal_nepal_pgdata --format '{{.Name}} {{.Mountpoint}}'
docker system df -v | grep nepal_pgdata      # expect a non-trivial size (~140 MB after a week)
```

**Fix — recreate the container from the repo checkout.** `docker compose up -d` alone is *not*
enough: it reuses the existing container's frozen config and fails identically. The stale container
must be removed first. `docker rm` never deletes named volumes, so this does not touch the data:

```bash
docker rm -f sapphire-nepal-postgres-1

cd /Users/sapphire/SAPPHIRE_flow          # the working directory IS the fix
docker compose -p sapphire-nepal -f docker-compose.nepal-forcing.yml up -d

# verify the new container points at the persistent path
docker inspect sapphire-nepal-postgres-1 \
  --format '{{index .Config.Labels "com.docker.compose.project.working_dir"}}'
```

**Then prove the feed end to end** rather than assuming — a healthy DB and a working fetch are
different claims:

```bash
bash scripts/launchd/run-nepal-forcing.sh; echo "exit=$?"     # expect exit=0, ~20-40 s
tail -1 ~/Library/Logs/sapphire-nepal-forcing.jsonl           # expect ok=true, rows=8568, members=51

docker exec sapphire-nepal-postgres-1 psql -U sapphire -d sapphire -c \
  "SELECT cycle_time, count(*) rows, count(DISTINCT member_id) members, max(valid_time) horizon_end
     FROM weather_forecasts GROUP BY cycle_time ORDER BY cycle_time DESC LIMIT 5;"
```

Running the wrapper by hand is safe and is the fastest way to close a missed day — it stores the
same 00Z cycle the timer would, so the scheduled run later that day is simply a no-op re-fetch.

**Diagnosing the same class of failure elsewhere.** Any Compose project deployed from a scratch
directory carries this fault. To audit:

```bash
docker ps -a --format '{{.Names}}' | while read -r c; do
  printf '%s\t%s\n' "$c" \
    "$(docker inspect "$c" --format '{{index .Config.Labels "com.docker.compose.project.working_dir"}}')"
done | grep -vE '\t/Users/|\t$'
```

Anything printing a path under `/tmp` or `/private/tmp` is one Docker restart away from this failure.

## Caveats

- **Unmonitored.** The host watchdog probes `localhost:8000` (the Swiss API) and knows nothing about
  this feed; an explicit `cycle_time` also suppresses the `FORECAST_FRESHNESS` heartbeat. The JSONL
  record and the launchd log are the only signals. Check them, or wire a dead-man later.
- **No backups.** Deliberate — the store is reproducible from the seed plus a re-fetch.
- **Not restarted by `start-sapphire.sh`** (that manages the Swiss file set only). After a host
  reboot, `docker compose -p sapphire-nepal ... up -d` again **from the repo checkout**;
  `restart: unless-stopped` covers a Docker restart but not a fresh boot with the engine down —
  and it only helps if the container's baked-in mount sources still exist (see
  [Troubleshooting](#troubleshooting-container-wont-start-exit-127)).
- **This feed fails silently and invisibly.** It is unmonitored (below), so a dead store surfaces
  only as a stale JSONL. When something reports "12300 is dead", separate the two questions before
  concluding anything: *is the Gateway serving?* (the recap probe answers this — it is independent
  of this store) and *is our store accepting writes?* On 2026-08-28 the Gateway was fully healthy
  and only the local Postgres was down.
- **Private-seam coupling.** The run calls `run_forecast_cycle._fetch_nwp_task.fn(...)` — the seam
  that both fetches and persists. Re-check it whenever that flow changes shape.
- **Placeholder geometry.** The seeded basin outline is a stub; the recap path is basin-average by
  polygon name and never reads it. Replace before this basin feeds a model or a grid extractor.

## Uninstall

```bash
launchctl bootout gui/$(id -u)/ch.hydrosolutions.sapphire-nepal-forcing
rm ~/Library/LaunchAgents/ch.hydrosolutions.sapphire-nepal-forcing.plist
docker compose -p sapphire-nepal -f docker-compose.nepal-forcing.yml down -v   # -v drops the data
```
