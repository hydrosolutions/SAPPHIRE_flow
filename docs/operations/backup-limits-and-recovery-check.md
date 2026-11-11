# Local backup limits, measurements and recovery check

Operator record for Effort #482 (bounded local backup storage and duration), measured on the
mac-mini staging host on 2026-11-11. It reports what was measured and what was not. It is not a
recovery guarantee.

## Measured baseline (mac-mini, 2026-11-11)

Database: 222 GB. Largest tables (total size incl. indexes):

| Table | Size | Note |
|---|---|---|
| `forecast_evidence_blobs` | 82 GB | protected forecast evidence |
| `weather_forecasts` | 67 GB | NWP; 190 M rows; disposable, but replay may need it (Effort #483) |
| `historical_forcing` | 7.3 GB | |
| `observations` | 3.7 GB | |
| `forecast_values` | 2.3 GB | |

Nightly full `pg_dump` (custom format), run time from Prefect:

| Run | Dump size | Duration |
|---|---|---|
| 2026-11-02 | 104.8 GB | 89 min |
| 2026-11-05 | 110.3 GB | 94 min |
| 2026-11-10 | 122.3 GB | 102 min |
| 2026-11-11 | 124.9 GB | 104 min |

Growth is about 2.4 GB/day, roughly 0.8 min per GB. No compression or parallelism change was
measured, so none is recommended or assumed to help.

Retained on the backup volume at measurement time: **630 GiB** (six dumps), well above the
selected `keep_count=2`, because the setting took effect after the 2026-11-11 run. The next run
prunes to one copy before dumping (`keep_count - 1`), so steady state is two dumps (about 250 GB)
and peak temporary space is one extra dump in flight. This was **not yet observed**; check the
volume after the 2026-11-12 02:00 run.

No dump was published for 2026-11-06 to 2026-11-09. Those four flow runs sat in `Running` in
Prefect with no process behind them until an operator cancelled them on 2026-11-11 (the 512 MiB backup worker was the suspected cause; not
proven). Earlier failures: headroom errors (Oct 29-31) and OOM kills (Oct 31, Nov 1).

## Limits chosen

| Limit | Value | Where | Why |
|---|---|---|---|
| Copies retained | 2 (the minimum the flow allows; default 7) | `backup-database` deployment parameter `keep_count` | Six dumps used 630 GiB. Two keeps the last verified copy while a new one is built. |
| Backup worker memory | 2 GiB RAM, 4 GiB RAM+swap | `docker-compose.macmini.yml` | The 512 MiB base limit coincided with OOM kills and hung runs; 2 GiB gave 102-104 min runs. |
| Dump ceiling | 4 h (`SAPPHIRE_BACKUP_DUMP_TIMEOUT_S=14400`; code default stays 2 h) | `docker-compose.macmini.yml` | At the measured growth the 2 h default is reached in about a week (104 min now, 120 min ceiling). 4 h lasts about two months at the same growth. Reassess then. |

A malformed `SAPPHIRE_BACKUP_DUMP_TIMEOUT_S` fails the run before anything is pruned or written.

Not changed: schedule (02:00), full backup scope, the active weather-cache policy. Excluding
`weather_forecasts` from the nightly dump would save the most bytes and time, but replay may need
it; that waits for Effort #483.

## Failure behaviour (existing, unchanged)

A dump is written to a temporary file, validated with `pg_restore --list`, then published
atomically. A failed, timed-out or interrupted run deletes only its own temporary file and never
replaces a published dump. Retention never goes below one copy. A filesystem lock prevents two
runs overlapping, stale owned temporaries are swept, and a free-space check runs before dumping.

## Bounded recovery check

Restoring the whole 125 GB dump on this arm64 host needs x86 emulation and about 220 GB of scratch
space, so `scripts/restore-rehearsal.sh` (full restore, three content checks) was not used. Instead
the newest dump was restored into a disposable container of the same digest-pinned image
(4 CPUs, 8 GB RAM, dump mounted read-only, no named volume), with the data of four large tables
skipped via a `pg_restore -L` list: `weather_forecasts`, `forecast_evidence_blobs`,
`forecast_evidence`, `historical_forcing`. Schema, constraints and all other data were restored.

Result for `sapphire_20261111_020000`:

- Restore time **18 min 50 s**, exit 0 with `--exit-on-error`.
- All **129 foreign keys validated**; zero orphans for forecast values, forecasts, observations and
  skill scores; schema version 0082 (the dump predates that day's migrations).
- Row counts equal live for 29 of the 37 non-empty tables. Four were behind live only by growth
  since 02:00 (observations, forecasts, forecast values, pipeline health; never ahead) and four are
  the skipped tables.
- Content fingerprints (row count and hash of key columns, including QC status/flags and rule
  version) were **identical to live** for observations created before the dump
  (7,336,382 rows), forecast values issued before 11-10 (15,888,360), all 139,712 skill scores and
  all 904 model artifacts.
- Both tenants present. This database has no row-level-security policies, so tenant isolation
  rests on the application layer; nothing about it was weakened.

To repeat: start the container from the image pinned in `scripts/restore-rehearsal.sh`, mount the
dump read-only, `pg_restore -l` it, remove the four `TABLE DATA` lines, then `pg_restore -L`
the remainder into a fresh database. Remove the container with `docker rm -f -v`.

## Remaining risks

- **Not measured:** forecast availability and resource impact while the dump runs (no comparison
  was recorded), full-service recovery time, and a restore of the skipped tables.
- The restore check does not cover forecast evidence or weather data; evidence has its separate
  protected backup path (`docs/standards/cicd.md`).
- **No off-server copy exists.** Dumps share a failure domain with the database. Local copies do
  not protect against losing the server or its disk.
- Nightly dumps give a worst-case data loss of about 24 h, not the provisional one-hour target.
- Dump size and duration keep growing (about 2.4 GB/day); the 4 h ceiling is a stopgap, and the
  durable fix is the weather decision in Effort #483.
