# recap Data Gateway availability probe — mac-mini runbook

**Status:** READY (runbook) — harness dry-run validated on the dev machine 2026-07-20
(reproduced the live findings exactly: ERA5 `lag_days=8`; IFS forecast `3h×48 + 6h×35`).
**Owner:** Bea (marti@hydrosolutions.ch)
**Related:** [Plan 121](../plans/121-recap-flow6-and-integration-followons.md) §Live probe
(the findings this longitudinal run extends); [[project_recap_era5_probe_and_115_stac]];
[Plan 111b](../plans/111b-bafu-collector-macmini-deployment.md) (the collector-on-mac-mini precedent).

> A lightweight, launchd-scheduled probe that records **what the Gateway actually serves and when**
> — the ERA5-Land latency edge, IFS forecast availability/cadence, and whether the operational /
> gap-fill stitching works — so we can characterise coverage over a 1–2 week window instead of a
> single snapshot. This is an **exploratory experiment**, not a pipeline component: it writes an
> append-only JSONL log and touches **nothing** in the DB or the forecast path.

---

## What it does (recap)

Every 3 hours (`StartInterval=10800`), `scripts/recap_probe_loop.py` runs **one** probe cycle against
the live Gateway for **test HRU `12300`** and appends one JSONL record per endpoint to
`~/Library/Logs/sapphire-recap-probe.jsonl`:

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
2. **The recap-dg-client must be importable in the venv** the plist calls. On a host synced with the
   git-pin (needs `RECAP_DG_CLIENT_TOKEN` at `uv sync` time) `uv run python` has it. Verify:
   ```bash
   cd /Users/sapphire/SAPPHIRE_flow && uv run python -c "import recap_client; print('ok')"
   ```
3. **The API key file**, `0600`, at the path the plist points to (never committed):
   ```bash
   mkdir -p /Users/sapphire/.config/sapphire
   printf '%s' '<RECAP_API_KEY>' > /Users/sapphire/.config/sapphire/recap_api_key
   chmod 600 /Users/sapphire/.config/sapphire/recap_api_key
   ```

---

## Install (launchd)

```bash
cd /Users/sapphire/SAPPHIRE_flow
git pull                                    # main, with the harness merged

cp scripts/launchd/ch.hydrosolutions.sapphire-recap-probe.plist \
   ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/ch.hydrosolutions.sapphire-recap-probe.plist
```

`RunAtLoad=true`, so the first cycle fires immediately on load. Adjust HRU / cadence / log path via the
plist's `EnvironmentVariables` + `StartInterval` (then `launchctl unload && launchctl load`).

---

## Verify

```bash
# it is registered
launchctl list | grep recap-probe

# the first cycle wrote records (each line = one endpoint result)
wc -l ~/Library/Logs/sapphire-recap-probe.jsonl
tail -3 ~/Library/Logs/sapphire-recap-probe.jsonl

# stdout summary (one line per endpoint, ok=True/False)
tail -20 ~/Library/Logs/sapphire-recap-probe.stdout.log
```

Expect ~6 JSONL records per cycle. A cycle where every endpoint is `ok=False source_data_missing` is a
**valid observation** (the Gateway had no data), not a harness failure — that is exactly what we are
measuring.

---

## Analyse (the payoff)

After a few days, read the whole series with pandas:

```python
import pandas as pd
df = pd.read_json("~/Library/Logs/sapphire-recap-probe.jsonl", lines=True)

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

## Operate / teardown

- **Cadence** — retune via `StartInterval` in the plist; reload. Every 3 h catches the four daily IFS
  cycles (00/06/12/18 UTC).
- **Disk** — negligible (~6 short JSONL rows per cycle; a couple of KB/day).
- **Stop** — `launchctl unload ~/Library/LaunchAgents/ch.hydrosolutions.sapphire-recap-probe.plist`.
  Delete the plist + the log to fully remove. Nothing else to unwind — the probe touches no DB, no
  volume, no forecast state.
- **Restore host sleep** after the experiment if you changed `pmset` and do not want it permanent.

---

## Out of scope

- Not a Prefect flow, not a pipeline component — deliberately lightweight for a time-boxed experiment.
  If it graduates to a standing monitor, re-home it on the Plan 111 collector shape (Prefect deployment
  + Flow 4 staleness hook).
- Coverage conclusions from HRU `12300` (a test HRU) do not necessarily generalise — re-point at a real
  subscribed basin when one is available (Plan 121 follow-on action 2).
