---
status: DRAFT
created: 2026-08-14
plan: 162
title: Robust database backup — complete, verified restorable, loud on failure, and not on the disk it protects
scope: Make the nightly database backup a backup in the operational sense rather than a file that appears. Covers the credential it runs as (a dedicated read-only role, since it currently runs on a deliberately partial one), atomic publication so a failed dump cannot masquerade as a good one, a monitor that cannot be fooled by an empty file, and a rehearsed restore — because nothing here has ever been restore-tested. Off-box copy and encryption at rest are owner decisions inside this plan, not follow-ons, since a backup on the same unencrypted disk as the database does not survive the failure it exists for. Absorbs T4/T5 from Plan 161. Continuous archiving/PITR and backing up the separate `prefect` database are named non-goals.
depends_on: [161]
blocks: []
supersedes: []
---

# Plan 162 — Robust database backup

## Status
**DRAFT** (2026-08-14). Operational reliability (category **A**). **Backups are live-broken**: the last successful
automated backup was **2026-08-13 02:01 UTC**. Absorbs **T4/T5 of Plan 161** (which stay recorded there as the
incident trail; this plan is where they get built).

## Problem

Two independent defects broke backups on 2026-08-13, **both** introduced by Plan 147 Slice D, and the first
masked the second:

1. **Parse.** The worker DB password contains `/`; `urlparse` ended the netloc at it. **Fixed and deployed**
   (Plan 161 T1, PR #152, mini at 0.1.721).
2. **Privilege.** The backup runs inside `prefect-worker` as `sapphire_worker`, which can read **38 of 40**
   tables — `access_tokens` and `access_token_stations` are denied — so `pg_dump` fails on its `LOCK TABLE`.
   That grant layout is *correct*; running a whole-database backup on a deliberately partial role is not.

Fixing (2) alone would leave the real problem untouched. Measured on the host 2026-08-14, this backup lacks
**four** properties a backup needs:

| Property | Today |
|---|---|
| **Complete** | Runs on a role that cannot read 2 of 40 tables |
| **Verified** | **No restore has ever been rehearsed.** `pg_restore --list` proves the TOC is readable, not that data restores |
| **Loud on failure** | A failed dump leaves a **0-byte `.dump`**; the monitor globs `*.dump` by mtime, so failure **clears** the stale alert |
| **Survivable** | Dumps sit on **`/dev/disk3s5` — the same device as the database** (`/Volumes/sapphire-backup` is a directory, not a mount), **unencrypted** (FileVault Off), and they contain `access_tokens` |

The last row is the one that makes "robust" more than a word: **7 dumps / 7.3 GB that die with the disk they
protect.** The 14-day July blackout and this outage share a root shape — *we inferred health from something that
was not evidence*. A file named `*.dump` is not evidence of a backup, and a backup on the protected disk is not
evidence of recoverability.

## Goal

A nightly backup that is **complete** (reads every table), **published atomically** (a `.dump` exists only if it
validated), **monitored on evidence** (an empty or partial artifact never satisfies freshness), **rehearsed**
(a restore is executed, not assumed), and **survivable** (at least one copy off the machine, encrypted).

## Decisions

- **D1 — credential. ✅ DECIDED.** A dedicated read-only role `sapphire_backup` granted the built-in
  `pg_read_all_data`. **Not** more privilege for `sapphire_worker` (re-opens Slice D), and **not**
  `--exclude-table` (a silently incomplete backup is worse than a failed one).
- **D2 — where it runs. ✅ DECIDED (owner, 2026-08-14): mount into the existing `prefect-worker`.** Accepted
  cost: that container gains a read-everything (read-only) credential. A dedicated backup pool is **deferred**,
  not rejected — it needs pool creation before deployment registration, routing, volume, mounts, limits and
  `depends_on: init`, which is more wiring than this fix warrants today.
- **D3 — success signal. ✅ DECIDED: make the filesystem trustworthy instead of adding a second channel.** With
  atomic publication, a file *only* carries the `sapphire_*.dump` name if it already validated — so "newest
  `.dump`" becomes real evidence and the monitor can keep reading it. The `pipeline_health` record proposed in
  Plan 161 T5 is therefore **deferred**: it needs a write path the read-only backup identity does not have
  (only `sapphire_worker` holds `INSERT` on `pipeline_health`) and would otherwise be best-effort, i.e. not
  proof. "The flow never ran at all" is still covered — no new file → stale.
- **⚠️ D4 — OPEN: where does the second copy live?** Same-device storage is the single largest gap.
  - **(a) External disk on the mini** — cheapest, survives disk failure but not theft/fire/ransomware, and it is
    a physical dependency on an office machine.
  - **(b) NAS / another host over the LAN** — good, but shares the office's fate.
  - **(c) Object storage (S3-compatible; a Swiss/EU provider given the deployment)** — *recommended*. Survives
    the site, supports versioning and object-lock, and costs pennies at 1 GB/night with sane retention.
  - Whichever is chosen, keep the local copy as the fast-restore path: off-box is for disaster, local is for
    "someone dropped a table".
- **⚠️ D5 — OPEN: encryption at rest.** Dumps contain `access_tokens` and every observation. **FileVault is Off.**
  - **Do NOT simply enable FileVault.** It requires a password at boot, which would **break the unattended
    reboot recovery Plan 158 T6 just proved** (verified 2026-08-13: the stack returned 21 s after boot with no
    human present). That is a real conflict, not a theoretical one.
  - **Recommended: encrypt the dump itself** (e.g. `age`, or `gpg --encrypt`) as part of publication, so the
    artifact is safe wherever it lands — local disk, NAS or bucket — and the host stays auto-bootable. This
    makes key management the new obligation: the decryption key **must not live only on the mini**, or the
    backup is unrecoverable exactly when it is needed.
- **⚠️ D6 — OPEN: is a 24 h RPO acceptable?** Nightly dumps mean up to a day of lost observations and forecasts.
  Probably fine for Swiss staging; **likely not for Nepal v1 production**, where continuous archiving (WAL
  shipping / PITR) is the standard answer. Deciding now shapes whether this plan is the end state or a stepping
  stone. Explicitly a **non-goal to build** here — but it should be a conscious deferral, not an oversight.

## Tasks

### T1 — a backup identity that can actually read the database (Repo)

*In:* `docker/bootstrap-roles.sql`, `docker/bootstrap-roles.sh`, `docker-compose.yml` (new
`sapphire_backup_db_password` secret; mount to `init` and `prefect-worker`). *Out:* the api/worker grant layout.

- **Membership must be INHERITABLE.** Both existing roles are created *and re-converged* as `NOINHERIT`
  (`bootstrap-roles.sql:36,47,70-71`). `pg_read_all_data` is a **membership**, so a role copied from that shape
  would hold it and still be denied — `pg_dump` does not `SET ROLE`. Grant it **per-membership**
  (`GRANT pg_read_all_data TO sapphire_backup WITH INHERIT TRUE`, PostgreSQL 16), keeping the role attribute
  `NOINHERIT` and the convention intact.
- **Grant `CONNECT` explicitly** — `pg_read_all_data` does not confer it.
- **Join the convergence block, and re-grant AFTER cleanup.** The block blanket-REVOKEs memberships for its named
  roles (`:78-82`); adding `sapphire_backup` without re-granting afterwards would strip the privilege on **every
  deploy** and re-break backups at the next `up --build`.
- **Preflight assertions.** `pg_read_all_data` covers tables/views/sequences + schema `USAGE`, but not large
  objects or `BYPASSRLS`. Measured 2026-08-14: **RLS tables = 0, large objects = 0, sequences = 2** — sufficient
  today. The backup must **fail loudly** if either count becomes non-zero, or the dump silently goes partial.
- **Restrict connectivity to other databases deliberately** — the membership is cluster-wide, and the bootstrap
  already documents that revoking a named role cannot override `PUBLIC` connect (`:91-112`).

**Red-first:** set **conflicting** worker and backup credentials, capture `pg_dump`'s argv/environment, and prove
the **backup** identity wins. (Merely pointing `DATABASE_URL` at a backup URL passes against today's code.)
**Acceptance:** a **real login as `sapphire_backup` performing `SELECT` on `access_tokens`** — a
`pg_auth_members` row proves nothing here.

### T2 — atomic publication: a `.dump` exists only if it is good (Repo)

*In:* `src/sapphire_flow/flows/backup.py`.

`pg_dump` currently writes straight to the final path and a failure leaves the file behind. Deleting on non-zero
exit is **not sufficient** — it cannot run under `SIGKILL`/OOM, and misses exceptions from `subprocess.run`,
from post-dump filesystem operations, or from cleanup itself.

**Write to a uniquely named temporary file that does NOT match the `*.dump` glob**, on the same filesystem →
validate → set mode `0600` → **atomically rename** to `sapphire_*.dump`. Best-effort deletion goes in `finally`.
**A crash remnant must never match the glob** — that is the guarantee no cleanup code can make.
Add **bounded cleanup of stale temporaries** (retention only considers finalised dumps, `backup.py:96-107`).

**Red-first:** the fake subprocess must **write partial content before failing** — a fake that writes nothing
passes against today's code and proves nothing.

### T3 — a monitor that cannot be fooled (Repo)

*In:* `src/sapphire_flow/ops/watchdog.py` (`newest_backup_mtime` + the staleness block).

Reject non-viable artifacts (size > 0 at minimum; T2 makes name-implies-valid true, so state plainly that this is
the chosen policy rather than letting it drift). **Define the predicate and its state transitions**, which the
Plan 161 draft did not: stale → repeated-stale → recovered → stale. **An invalid or partial artifact must never
cause recovery or reset alert state.**

Fold in **Plan 161 T3** here if not already shipped: the alert currently fires **every tick** (~288/day,
`watchdog.py:510-513`), and `last_backup_alert_iso` is persisted but never consulted.

### T4 — rehearse the restore (Repo + Host) — *the task that makes it a backup*

`pg_restore --list` proves only that the archive TOC is readable. Restore the newest dump into a **scratch
database** and verify representative rows from a **protected** table (`access_tokens` — the one that was failing),
plus sequence state and schema objects.

**Document and rehearse fresh-cluster recovery ordering**, which is missing today: `pg_dump` does **not** back up
cluster roles, yet the dump references owners and role ACLs, and the bootstrap assumes migrations already created
every table (`bootstrap-roles.sql:3-6`). Order: create roles/owner → restore into an empty database → re-run role
bootstrap to converge grants. Do **not** migrate the target first unless collisions are explicitly handled.

### T5 — a copy that survives the machine (Host + Repo, gated on D4/D5)

Publish each validated dump to the chosen off-box destination, encrypted per D5. Verify the remote copy by
**size + digest**, not by the upload command's exit code. **Alert if the off-box copy is missing or stale**, or
this silently becomes a local-only backup again.

### T6 — runbook (Docs)

Restore procedure (both "recover one table" and "rebuild the host"), where keys live, how to rotate the backup
credential, and the D4/D5 choices with their trade-offs. In `docs/deployment/`.

## Exit gates

Full `uv run pytest`; `ruff format --check` + `ruff check`; pyright ratchet; `shellcheck` + `bash -n` for the
bootstrap scripts; and **each of T1–T3's locking tests proven RED against current committed code**.

## Host acceptance

`backup-database` reaches **`COMPLETED`**; a dump with a **current** timestamp appears; the T4 scratch-restore
succeeds; and the watchdog posts a single recovery message. **Not** "the alerts went quiet" — that is precisely
the evidence that failed us twice.

## Non-goals / follow-ons

- **Continuous archiving / PITR** (D6) — the real answer to a 24 h RPO, deliberately deferred.
- **Backing up the separate `prefect` database** — flow-run history and deployment state have **no backup at
  all** today.
- **Rotating the worker DB password** (its first two characters leaked into a Prefect state message; Plan 161).
- **Plan 161 T2** (construct-don't-splice) — independent; this plan's credential reaches `pg_dump` as `PG*`
  vars and so is never parsed, which composes cleanly with it.
