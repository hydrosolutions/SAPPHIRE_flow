# recap Data Gateway availability probe — mac-mini runbook

**Status:** READY (runbook) — reconciled with the container-exec deployment actually running on the
mac mini (Plan 132, 2026-07-20). Supersedes the original host-venv narrative shipped with PR #103,
which does not work on this host (no `recap_client` in the host `uv` venv, no host-level git
credential for the private `recap-dg-client` clone, and `uv` lives at `/Users/sapphire/.local/bin/uv`
not `/usr/local/bin/uv`).
**Owner:** Bea (marti@hydrosolutions.ch)
**Related:** [Plan 132](../plans/132-recap-probe-deployment-reconciliation.md) (this reconciliation);
[Plan 121](../plans/121-recap-flow6-and-integration-followons.md) §Live probe (the findings this
longitudinal run extends); [[project_recap_era5_probe_and_115_stac]];
[Plan 111b](../plans/111b-bafu-collector-macmini-deployment.md) (the collector-on-mac-mini precedent).

> A lightweight, launchd-scheduled probe that records **what the Gateway actually serves and when**
> — the ERA5-Land latency edge, IFS forecast availability/cadence, and whether the operational /
> gap-fill stitching works — so we can characterise coverage over a 1–2 week window instead of a
> single snapshot. This is an **exploratory experiment**, not a pipeline component: it writes an
> append-only JSONL log and touches **nothing** in the DB or the forecast path.

---

## What it does (recap)

Every 3 hours (`StartInterval=10800`), `scripts/launchd/run-recap-probe.sh` `docker exec`s
`scripts/recap_probe_loop.py` (fed via stdin — `scripts/` is never baked into the image, see
`Dockerfile` / [Plan 122](../plans/122-package-operational-scripts.md)) into the running
`sapphire_flow-prefect-worker-1` container as the non-root `app` user, for **test HRU `12300`**, and
appends one JSONL record per endpoint to `/Users/sapphire/Library/Logs/sapphire-recap-probe.jsonl`:

| endpoint | what it answers |
|---|---|
| `era5_latency_edge` | newest ERA5 date served today → `last_observed`, `lag_days` (bounded search, 2–14 d back) |
| `era5_reanalysis_to_today` | does the **pure** endpoint (the one our adapter calls) hard-error past the edge, or truncate? |
| `ifs_forecast_run-{0,1}` | IFS forecast availability + native cadence (`step_hours_counts`) for today / yesterday |
| `operational_stitched` | does the ERA5+IFS gap-fill bridge return data, and at what resolution/provenance? |
| `ifs_gap_fill` | can the recent lag window be filled, and from which IFS runs? |

Single-shot by design (like the watchdog): the script does one cycle and exits; launchd schedules
the cadence. The API key is read from env/file only and is **never** written to the log.

---

## Why container-exec, not a host venv

The probe runs **inside the same worker container the operational pipeline uses**
(`sapphire_flow-prefect-worker-1`), which already has `recap_client` baked in at image build time —
rather than in a host `uv` venv. This was a deliberate choice (see Plan 132 §Design decision), not the
default: a host-venv path (host-level `insteadOf` git auth + `RECAP_DG_CLIENT_TOKEN`, matching what CI
and the Docker build already do) was considered and rejected, to keep the token file-scoped and off the
host account's global git config, and because the container-exec path is already live and validated
against the same environment the operational pipeline runs in.

---

## Prerequisites

1. **⚠️ Mac-mini sleep fix — do this FIRST.** The host sleeps and drops off the network (the Plan 100
   blackout / Plan 115 A4 finding: no `pmset`/wake config). If the host naps, probe gaps become
   indistinguishable from Gateway gaps — which defeats the experiment. Keep it awake for the run:
   ```bash
   sudo pmset -a sleep 0 disksleep 0 displaysleep 10 womp 1
   pmset -g            # verify: sleep 0, womp 1
   ```
   (Or run the probe under `caffeinate -s`.) This is worth landing permanently regardless — it is the
   same host reliability gap Plan 115 A4 flagged.
2. **Docker up under `sapphire`, worker container running, `recap_client` importable in it as `app`:**
   ```bash
   docker exec --user app --workdir /tmp sapphire_flow-prefect-worker-1 python -c "import recap_client"
   ```
3. **The API key file**, `0600`, at the path the wrapper reads (never committed):
   ```bash
   mkdir -p /Users/sapphire/.config/sapphire
   printf '%s' '<RECAP_API_KEY>' > /Users/sapphire/.config/sapphire/recap_api_key
   chmod 600 /Users/sapphire/.config/sapphire/recap_api_key
   ```

---

## Install (launchd)

No `~/recap-probe/` copies — the wrapper and probe script run straight from the host git clone, so
`git pull` is the single sync mechanism for both (the drift this runbook exists to prevent).

```bash
cd /Users/sapphire/SAPPHIRE_flow
git pull                                    # main, with the Plan 132 reconciliation merged

cp scripts/launchd/ch.hydrosolutions.sapphire-recap-probe.plist \
   ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) \
   ~/Library/LaunchAgents/ch.hydrosolutions.sapphire-recap-probe.plist
launchctl enable gui/$(id -u)/ch.hydrosolutions.sapphire-recap-probe
```

(`launchctl load` also works on this host if preferred; `bootstrap`/`enable` is the modern equivalent
used elsewhere in this repo, e.g. `scripts/launchd/install-launchd.sh`.)

`RunAtLoad=true`, so the first cycle fires immediately on load. This agent is **not** registered in
`install-launchd.sh` — it stays manually loaded, appropriate for a time-boxed experiment.

---

## How it works (the four non-obvious mechanics)

- **`docker exec -i … python -`** — the probe script is piped into the container over stdin because
  `scripts/` is never present in the built image (only `.venv`, `src/`, `alembic.ini`, `alembic/` are
  `COPY`'d — see `Dockerfile`, [Plan 122](../plans/122-package-operational-scripts.md)). This is the
  permanent mechanism, not a stale-build stopgap.
- **`--user app`** — without it, `docker exec` bypasses the entrypoint's `gosu app` drop
  (`docker/entrypoint.sh`) and the probe would run as **root** inside the container, violating the
  non-root application-process invariant (`docs/standards/security.md`).
- **Key via `-e RECAP_API_KEY=…`** from the `0600` host key file — a documented trade-off: the key is
  briefly visible in host `ps` and the container's process env for the exec window. Accepted for a
  single-user staging host running a 3-hourly read-only probe. (`docker exec` does support
  `--env-file`, which would avoid the `ps` exposure; switch to it if this probe is ever promoted beyond
  an experiment.)
- **`RECAP_PROBE_LOG=/dev/stderr` → buffered → JSONL only when pure.** The container-side env var
  points the probe's JSONL sink at its own stderr. The wrapper captures that stderr into a buffer and
  appends it to the host JSONL **only if** the exec exited 0 **and** every non-empty buffered line
  parses as JSON. Otherwise the buffer + a banner go to the wrapper's own stderr (→ the launchd log)
  and the JSONL is left untouched — an infra failure (Docker error, `ImportError`, missing key) or a
  stray warning line must never write non-JSON into the JSONL log (it would break the pandas analysis
  below). Container stdout (the terse per-endpoint summary) always appends to a separate summary log.

---

## Verify

```bash
# it is registered
launchctl list | grep recap-probe

# the first cycle wrote records (each line = one endpoint result)
wc -l /Users/sapphire/Library/Logs/sapphire-recap-probe.jsonl
tail -3 /Users/sapphire/Library/Logs/sapphire-recap-probe.jsonl

# stdout summary (one line per endpoint, ok=True/False)
tail -20 /Users/sapphire/Library/Logs/sapphire-recap-probe.summary.log

# infra failures / impure runs land here, never in the JSONL
tail -20 /Users/sapphire/Library/Logs/sapphire-recap-probe.launchd.log
```

Expect ~6 JSONL records per cycle. A cycle where every endpoint is `ok=False source_data_missing` is a
**valid observation** (the Gateway had no data), not a harness failure — that is exactly what we are
measuring.

---

## Analyse (the payoff)

After a few days, read the whole series with pandas:

```python
import pandas as pd
df = pd.read_json("/Users/sapphire/Library/Logs/sapphire-recap-probe.jsonl", lines=True)

# ERA5 latency over time — is the edge advancing daily, or stalling?
edge = df[(df.endpoint == "era5_latency_edge") & df.ok]
print(edge[["run_ts", "last_observed", "lag_days"]].to_string())

# When does each day's IFS run become available? (arrival timing)
ifs = df[df.endpoint.str.startswith("ifs_forecast")]
print(ifs.groupby("run_ts")[["endpoint", "ok"]].apply(lambda g: g.to_dict("records")))

# Does the operational bridge ever stitch, and at what resolution?
op = df[(df.endpoint == "operational_stitched") & df.ok]
print(op[["run_ts", "step_hours_counts", "source_counts"]].to_string())
```

Questions the run should settle: the real ERA5 lag distribution (snapshot showed ~8 d, ragged); whether
`operational`/`ifs_gap_fill` ever succeed for a real basin (they did **not** for HRU 12300 in the
snapshot — aged-out IFS runs); and the actual fill-tail resolution (native 3 h/6 h vs resampled to
`subdaily_resolution`).

---

## Update

Edit `scripts/recap_probe_loop.py` and/or `scripts/launchd/run-recap-probe.sh` on `main`, then
`git pull` on the host clone (`/Users/sapphire/SAPPHIRE_flow`) — the next 3-hourly cycle runs the new
code automatically, no manual copy. A **plist** change (cadence, paths) is not picked up by `git pull`
alone and needs a manual reload:

```bash
cp scripts/launchd/ch.hydrosolutions.sapphire-recap-probe.plist ~/Library/LaunchAgents/
launchctl bootout gui/$(id -u)/ch.hydrosolutions.sapphire-recap-probe
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/ch.hydrosolutions.sapphire-recap-probe.plist
```

---

## Uninstall

```bash
launchctl bootout gui/$(id -u)/ch.hydrosolutions.sapphire-recap-probe
rm ~/Library/LaunchAgents/ch.hydrosolutions.sapphire-recap-probe.plist
```

Delete the log files to fully remove. Nothing else to unwind — the probe touches no DB, no volume, no
forecast state.

---

## Caveats

- **Runs inside a production worker container.** Keep it lightweight (read-only, ~seconds every 3 h);
  it is not gated by resource limits beyond that.
- **HRU `12300` is a test HRU** with sparse coverage. Re-point at a real subscribed HRU when available
  (Plan 121 follow-on).
- **Log growth** — ~KB/day, unbounded, hand-pruned (no rotation).
- **Container-name coupling** — `sapphire_flow-prefect-worker-1` is hardcoded in the wrapper; a
  Compose project rename breaks it.
- Restore host sleep settings after the experiment if you changed `pmset` and do not want it permanent.

---

## Out of scope

- Not a Prefect flow, not a pipeline component — deliberately lightweight for a time-boxed experiment.
  If it graduates to a standing monitor, re-home it on the Plan 111 collector shape (Prefect deployment
  + Flow 4 staleness hook).
- Coverage conclusions from HRU `12300` (a test HRU) do not necessarily generalise — re-point at a real
  subscribed basin when one is available (Plan 121 follow-on action 2).
- Not bringing HRU 12300 (or any gauge) live through the real ingestion path (Wave-1 milestone, gated
  elsewhere — see Plan 132 §Non-goals).
