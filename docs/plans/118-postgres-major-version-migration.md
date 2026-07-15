---
status: DRAFT
created: 2026-07-15
plan: 118
title: PostgreSQL major-version migration (16 → 17) — safe procedure for stateful deployments
scope: A repeatable, verified data-directory migration across a PG major version. Unblocks Dependabot #78.
depends_on: []
blocks: []
---

# Plan 118 — PostgreSQL major-version migration

## Status

**DRAFT.** Do not execute against any deployment until promoted to READY and the dry-run (§4) has passed.

## Provenance

Dependabot **PR #78** (`postgis/postgis:16-3.4 → 17-3.4`) bumps the PostgreSQL **major** version. It
passed all CI, but CI starts every job from an **empty** database and therefore cannot catch the real
failure: **PostgreSQL does not auto-upgrade a data directory across major versions.** A PG17 container
started against a PG16 `pgdata` volume refuses to boot (`database files are incompatible with server`).

**Verified 2026-07-15:** the mac-mini staging host runs **PostgreSQL 16.4** (`postgis/postgis:16-3.4`)
on a persistent named volume `sapphire_flow_pgdata` holding live data — the CAMELS forcing archive,
forecasts, station config, **and Prefect's own state** (there are two logical databases on this server,
`sapphire` and `prefect`; both migrate together or neither does).

Merging #78 without this procedure takes staging down until a manual migration is run. This plan is that
procedure, made repeatable and verified — because PG18, 19, … will each pose the identical problem.

## Objective

A **repeatable, reversible, verified** procedure to migrate a stateful SAPPHIRE deployment across a
PostgreSQL major version, with an explicit rollback path and a data-integrity check that proves the
migration did not lose or corrupt rows.

## Non-goals

- Not a routine `up -d`. This is a deliberate, supervised maintenance operation with the stack **down**.
- Not automated in CI. CI stays on fresh databases; this runs against real volumes only.

## The two viable methods — choose one at READY

### Method A — `pg_upgrade` (in-place, fast, uses hard links)

The `tianon`/official images ship `pg_upgrade`. Fastest for a large archive (the 66M-row v0 target),
because `--link` avoids copying the data. But it is the fiddlier path in a containerised world: it needs
**both** the old (16) and new (17) binaries present simultaneously, which the single-version postgis
image does not provide — so it requires a purpose-built migration image or the `pgautoupgrade` image.

### Method B — dump / restore (simple, robust, slower) — **recommended default**

`pg_dumpall` (or per-database `pg_dump -Fc`) from a PG16 container → restore into a fresh PG17 volume.
Slower and needs transient disk for the dump, but it is **version-agnostic, trivially scriptable, and
its rollback is "do nothing — the old volume is untouched."** For the current data size (tens of
thousands to low millions of rows) it completes in minutes. At the 1000-station / 66M-row scale, measure
the dump/restore time in the dry-run before committing to it.

**Recommendation:** Method B for now (small data, simplest rollback); revisit Method A only if the
dump/restore window becomes operationally unacceptable at scale.

## Procedure (Method B) — to be turned into `scripts/migrate-postgres-major.sh`

Every step idempotent-or-resumable; the script takes `--dry-run` and refuses to run if the stack is up.

1. **Pre-flight:** record source version, both database names, and a **row-count census** per table
   (`SELECT schemaname,relname,n_live_tup FROM pg_stat_user_tables` in each DB) — this is the integrity
   baseline for §5.
2. **Quiesce:** stop the app/worker/api/prefect services; leave **only** `postgres` running. Confirm no
   active connections.
3. **Dump:** `pg_dumpall` (globals + both DBs) — or `pg_dump -Fc` per database — into a file on the
   **USB backup disk** (`/Volumes/sapphire-backup`), not the same disk as `pgdata`. Checksum it.
4. **Snapshot the old volume — do NOT delete it.** Rename `sapphire_flow_pgdata` →
   `sapphire_flow_pgdata_pg16_<datestamp>` (or `docker run --rm -v … tar` it to the backup disk). **This
   is the rollback anchor.**
5. **New volume:** bring up the PG17 image against a **fresh empty** `pgdata`; let it `initdb`.
6. **Restore:** load globals then each database into PG17. Watch for extension version mismatches —
   **PostGIS in particular** (`16-3.4` → `17-3.4` keeps PostGIS 3.4, so the extension version is stable,
   but confirm `SELECT postgis_full_version()` matches pre/post).
7. **Migrate app schema forward if needed:** run `alembic upgrade head` and confirm the head matches what
   the app expects (currently `0029`; `0030` if 115a has merged by then).
8. **Verify (§5).**
9. **Bring the stack up** on PG17; confirm `/api/v1/health` = ok and the Prefect worker is polling.

## §5 — Verification (the migration is not "done" until this passes)

- **Row-count census matches** the §1 baseline, table for table, in **both** databases. Any mismatch is
  a stop.
- **PostGIS geometry survives:** a spot-check that `basins.geometry` rows are valid and non-empty
  (`ST_IsValid`, `ST_NPoints > 0`) — geometry is the most likely thing a dump/restore mangles.
- **`historical_forcing` spot-check:** `SELECT source, COUNT(*), MAX(valid_time)` matches pre-migration
  (this is the archive; it must be byte-identical in effect).
- **Alembic head** is correct and the app boots against it.
- **Prefect** deployments/flow-run history are intact (its state lives in the `prefect` DB).
- `/api/v1/health` returns ok; a forecast cycle can be triggered and completes.

## §6 — Rollback

Because the old volume is renamed, not deleted (step 4): **stop the stack, point the compose volume back
at `sapphire_flow_pgdata_pg16_<datestamp>`, revert the image tag to `16-3.4`, `up -d`.** Back to the
exact prior state, no data loss. Only after §5 passes **and** a soak period should the old volume be
retired.

## §7 — Unblock #78

Once this procedure exists, is dry-run-verified, and has been executed on staging with §5 green,
**Dependabot #78 can be merged** and the mac-mini redeployed on PG17. Until then #78 stays held (comment
posted). Note the ordering: the **migration runs first on the live host**, then the image bump merges —
not the reverse.

## Verification / exit gate

```bash
# a full dry-run against a COPY of the staging volume (not the live one):
scripts/migrate-postgres-major.sh --dry-run --source-tag 16-3.4 --target-tag 17-3.4
# must complete, and its §5 checks must pass, before this plan is READY.
```

## References

- Dependabot PR #78 (the trigger).
- `docker-compose.yml` (postgres service, `pgdata` volume, the two logical DBs).
- `docs/standards/cicd.md` (deployment + rollback conventions).
- `scripts/bootstrap-mac-mini.sh` (the host this first runs against).
- Related: **Plan 119** (making the deps-CI catch dangerous major bumps so this is never a surprise
  again).
