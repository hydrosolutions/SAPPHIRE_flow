# DHM ingest: recovery after a long outage

Routine DHM polling never looks further back than `polling_lookback_days` (default 7,
set in the `[adapters.river_stations]` table, see `docs/spec/config-reference.toml`).
A station silent for longer than that is polled for the last 7 days only, so a dead
station costs about 7 requests per run (at the default 24 h window) instead of re-requesting the whole gap. The cost: the
part of the outage older than the lookback is **abandoned**, not retried.

Each such station is listed in the `observation_ingest_fetch` health record under
`detail.lookback_capped` as `{station_id, start, end}`:

- `start`: the last stored reading before the gap.
- `end`: the first reading fetched after the gap. `null` means the station is still
  silent (the record is rewritten every run). Use the record from the run in which
  `end` first becomes non-null.

A capped station is not a failure and raises no WARNING.

## Which case applies

1. **The same gauge comes back** (repaired, power restored). The first routine poll
   stores new readings and moves the cursor forward, so the abandoned interval is now
   behind the cursor and the existing historical ingest can fill it. This is the only
   case needing an operator action.
   - Preconditions: the station is `operational` and gauged (a station suspended during
     the outage must be reactivated first); the operator identity is a writable-tenant,
     non-global-admin one; the request does not overlap enabled legacy-custody ratings
     and does not end in the future.
   - Run `ingest_observations_flow` in historical mode (`historical_tenant_code`,
     `historical_station_ids`, `historical_start`, `historical_end`; implementation:
     `run_historical_ingest`, `src/sapphire_flow/flows/historical_ingest.py`), **one
     call per slice of at most 31 days**, taking the dates from `start`/`end` in the
     health record. Without the record, use the last stored reading before the gap and
     the first after it.
2. **Station still silent, but DHM holds data we never fetched.** The capped poll
   fetches the latest 7 days; if any arrive, the cursor moves and case 1 applies. If
   none arrive, the cursor stays old and historical ingest refuses the station
   (it only fills holes behind the latest stored reading). Nothing to recover until
   the gauge reports again.
3. **Gauge rebuilt or replaced** (new location, datum or DHM station number). This is
   a new station, not a reactivation: use the onboarding tools
   ([new-station-by-hand.md](new-station-by-hand.md)). The old station is retired
   (#455) and its history is kept; a retired station is already outside ingest
   eligibility.
