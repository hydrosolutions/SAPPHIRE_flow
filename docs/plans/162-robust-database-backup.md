---
status: DRAFT
created: 2026-08-14
plan: 162
title: Robust database backup — complete, verified restorable, loud on failure, and safe to move off the machine
scope: Make the nightly database backup a backup in the operational sense rather than a file that appears. Covers the credential it runs as (a dedicated read-only role, since it currently runs on a deliberately partial one) AND the mechanism by which that credential reaches `pg_dump` without disturbing the shared worker connection, atomic + encrypted publication so a failed dump cannot masquerade as a good one and a stolen disk does not yield token hashes, a monitor that cannot be fooled by an empty file or by an unmounted backup volume, and a rehearsed restore in a disposable instance — because nothing here has ever been restore-tested. Encryption at rest (D5) is DECIDED here because it defines the published artifact; the off-box destination (D4) and the replication task are split into a follow-on, since an encrypted artifact is safe wherever it lands. Absorbs T3/T4/T5 from Plan 161. Continuous archiving/PITR and backing up the separate `prefect` database are named non-goals.
depends_on: []
blocks: []
supersedes: []
---

# Plan 162 — Robust database backup

## Status
**DRAFT** (2026-08-14). Operational reliability (category **A**). **Backups are live-broken**: the last successful
automated backup was **2026-08-13 02:01 UTC**. Absorbs **T3/T4/T5 of Plan 161** (which stay recorded there as the
incident trail; this plan is where they get built).

**Dependency note (revised).** `depends_on` is now empty, deliberately. Plan 161 **T1 is already merged and
deployed** (mini at 0.1.721), so it is history, not a dependency. Plan 161 **T3** (alert once, not 288×/day) is
absorbed here as **unconditional** scope — verified unshipped at `src/sapphire_flow/ops/watchdog.py:508-520`.
Plan 161 **T2** (construct-don't-splice) is **not** a prerequisite in either direction: T2 of *this* plan removes
URL construction and parsing from the backup path entirely, so the backup credential never becomes a URL. The
earlier draft asserted the opposite ("this plan's credential reaches `pg_dump` as `PG*` vars" as if that path
already existed) — it did not exist and no task created it. That gap is now T2 below.

**Task numbering changed in this revision.** Old T1 splits into **T1** (the role) + **T2** (how the flow gets the
role's credential — the mechanism the earlier draft assumed). Old T2 → **T3**, old T3 → **T4**, old T4 → **T5**,
old T5 (off-box copy) → **follow-on plan**. New **T6** = the local backup path must be the device it claims to be.
Old T6 (docs) → **T7**.

## Problem

Two independent defects broke backups on 2026-08-13, **both** introduced by Plan 147 Slice D, and the first
masked the second:

1. **Parse.** The worker DB password contains `/`; `urlparse` ended the netloc at it. **Fixed and deployed**
   (Plan 161 T1, PR #152, mini at 0.1.721).
2. **Privilege.** The backup runs inside `prefect-worker` as `sapphire_worker`, which can read **38 of 40**
   tables — `access_tokens` and `access_token_stations` are revoked back off it
   (`docker/bootstrap-roles.sql:189`) — so `pg_dump` fails on its `LOCK TABLE`. That grant layout is
   *correct*; running a whole-database backup on a deliberately partial role is not.

Fixing (2) alone would leave the real problem untouched. Measured on the host 2026-08-14, this backup lacks
**four** properties a backup needs:

| Property | Today | Closed by |
|---|---|---|
| **Complete** | Runs on a role that cannot read 2 of 40 tables | T1 + T2 |
| **Verified** | **No restore has ever been rehearsed.** `pg_restore --list` proves the TOC is readable, not that data restores | T3 (validation) + T5 (real restore) |
| **Loud on failure** | A failed dump leaves a **0-byte `.dump`**; the monitor globs `*.dump` by mtime (`watchdog.py:258-272`), so failure **clears** the stale alert | T3 (atomic publish) + T4 (monitor) |
| **Survivable** | Dumps sit on **`/dev/disk3s5` — the same device as the database** (`/Volumes/sapphire-backup` is a directory, not a mount), **unencrypted** (FileVault Off), and they contain `access_tokens` | **Partly.** T3 encrypts; T6 makes the wrong-device condition visible; **off-box replication is a follow-on** (see D4) |

**Stated plainly: this plan does not close "survivable" on its own.** It closes complete, verified and loud, it
makes the artifact **safe to move** (encrypted), and it makes "the dumps are on the boot disk" a *visible* state
instead of a silent one. Actually putting a copy off the machine is the follow-on plan, gated on D4.

The last row is still the one that makes "robust" more than a word: **7 dumps / 7.3 GB that die with the disk they
protect.** The 14-day July blackout and this outage share a root shape — *we inferred health from something that
was not evidence*. A file named `*.dump` is not evidence of a backup, and a backup on the protected disk is not
evidence of recoverability.

## Goal

A nightly backup that is **complete** (reads every table), **published atomically and encrypted** (an artifact
exists only if it validated, and it carries no plaintext token hashes), **monitored on evidence** (an empty
artifact, a missing artifact, or an unmounted backup volume never satisfies freshness), and **rehearsed** (a
restore is executed in a disposable instance, not assumed) — so that the remaining step, copying it off the
machine, is a transport problem rather than a correctness problem.

## Decisions

- **D1 — credential. ✅ DECIDED.** A dedicated read-only role `sapphire_backup` granted the built-in
  `pg_read_all_data`. **Not** more privilege for `sapphire_worker` (re-opens Slice D), and **not**
  `--exclude-table` (a silently incomplete backup is worse than a failed one).
  - **Revised: `sapphire_backup` is an `INHERIT` role**, unlike `sapphire_api`/`sapphire_worker`
    (`bootstrap-roles.sql:36,47,70-71`). The earlier draft kept `NOINHERIT` and compensated with
    `GRANT … WITH INHERIT TRUE` *plus* a re-grant after the blanket membership REVOKE (`:78-82`) on every
    deploy. That is an order-dependent step that, if dropped, silently reproduces exactly this outage's bug
    class. The `NOINHERIT` convention exists to stop a **write-capable** role from silently gaining an
    unrelated group's privileges; `sapphire_backup`'s entire purpose is unconditional broad `SELECT` via
    `pg_read_all_data` on every run, and `pg_dump` never calls `SET ROLE`. So: plain `INHERIT`, plain
    `GRANT pg_read_all_data TO sapphire_backup;`, and **out of** the api/worker blanket-revoke block — correct
    by construction, with its own targeted convergence block instead (T1).
- **D2 — where it runs. ✅ RE-DECIDED (owner, 2026-08-15): a DEDICATED backup component — option (b).**
  *Supersedes the 2026-08-14 decision to mount into the existing `prefect-worker`.* The owner's directive was
  explicit: **this ships to customers, so pick the right option, not the cheap one.** That changes the analysis:
  - **The product is multi-tenant.** `access_tokens` carries `tenant_id`, `token_hash`, `role`, `key_prefix`
    (verified on the live schema). A component able to read that table can enumerate **every tenant's**
    credential metadata. On a single-operator LAN staging box that is a nuisance; in a shipped multi-tenant
    system it is a cross-tenant exposure path, and the first thing a customer security review will ask about.
  - **Option (a) reverses a deliberate hardening decision.** `bootstrap-roles.sql:189` explicitly
    `REVOKE SELECT ON access_tokens, access_token_stations FROM sapphire_worker`, documented at `:178-188`:
    *"a Prefect worker running flows has no business reading token hashes/scopes"* — and noted as *"caught by a
    live docker-compose deploy rehearsal, not static review"*. Mounting a `pg_read_all_data` credential into
    that same container restores the capability to the **process**, defeating the revoke's intent by the back
    door.
  - **The "disjoint credential path" argument does not cover this.** Round 1 kept (a) because T2 gives the
    backup a credential path disjoint from the container-wide `DATABASE_URL`. That correctly addresses *identity
    confusion* — but the risk here is a **mounted secret**: the read-everything credential would sit in the
    container that also runs flow code, model artifacts and adapters touching external data. Disjoint variables
    do not help once that process is compromised.
  - **Honest bound on today's risk:** `access_token_pepper` is mounted **only into `api`** (verified), so (a)
    would leak peppered hashes and metadata, not directly usable tokens. It is a loss of defence-in-depth, not
    an immediate breach — which is why (a) was defensible for staging and is not defensible for a shipped
    product.
  - **Option (c) — host-scheduled `docker exec` — is DISQUALIFIED for a shipped product**, independent of
    security: it is macOS/launchd-specific glue. Customers deploy the Compose topology, likely on Linux.
    Anything that works only on our mini is not a product.
  - **✅ Sub-choice RESOLVED (2026-08-15, review blocker): a Prefect `backup` work pool.** (Was: decide at
    build time — that ambiguity meant Phase A had no defined execution component.) Rationale: consistent
    with the repo's flows-and-observability standard, and preserves the failure-detection signal T4 depends on)
    with the repo's flows-and-observability standard and it preserves the failure-detection signal. (A minimal
    one-shot container has a smaller attack surface — no flow code, no adapters — but must
    not lose that signal). Either satisfies D2; the security property is *"the only component holding a
    read-everything credential is the one whose sole job is backup"*.
  - **Cost, stated:** the pool wiring round 1 correctly enumerated — pool creation **before** deployment
    registration, routing, volume, mounts, resource limits, `depends_on: init` (`docker-compose.yml:80-83`,
    `:149-152`, `:288-293`). Roughly half a day more than option (a).
  - **Regression guard (required):** a CI assertion that the backup secret **never** appears in
    `prefect-worker`'s mounts, so this cannot silently revert. The property must be testable, not merely
    documented.

- **D3 — success signal. ✅ DECIDED: make the filesystem trustworthy instead of adding a second channel.** With
  atomic publication, a file *only* carries the published name if it already validated — so "newest artifact"
  becomes real evidence and the monitor can keep reading it. The `pipeline_health` record proposed in Plan 161 T5
  is therefore **deferred**: it needs a write path the read-only backup identity does not have (only
  `sapphire_worker` holds `INSERT` on `pipeline_health`, `bootstrap-roles.sql:161`) and would otherwise be
  best-effort, i.e. not proof. "The flow never ran at all" is still covered — no new file → stale.
  *(**PHASE B ONLY** — in Phase A the published name is `sapphire_*.dump`, plaintext. When D5's encryption
  lands the name becomes `sapphire_*.dump.age`, so T4's glob must change with it or the
  monitor goes permanently, falsely stale.)*
- **D4 — off-box destination: ⏸ DEFERRED to a follow-on plan (was OPEN).** Rationale for splitting rather than
  deciding: with D5 the artifact is encrypted at creation, so it is safe wherever it lands — the destination
  choice no longer gates correctness, and it needs owner input on cost, EU/CH data residency and who holds the
  bucket credentials. The candidates and their trade-offs are preserved for that plan:
  - **(a) External disk on the mini** — cheapest, survives disk failure but not theft/fire/ransomware, and it is
    a physical dependency on an office machine. **If (a) is ever chosen, T6's device verification is a hard
    prerequisite** — the current sentinel-file check is precisely what let dumps land on the boot disk.
  - **(b) NAS / another host over the LAN** — good, but shares the office's fate.
  - **(c) Object storage (S3-compatible; a Swiss/EU provider given the deployment)** — *recommended*. Survives
    the site, supports versioning and object-lock, and costs pennies at 1 GB/night with sane retention.
  - Whichever is chosen, keep the local copy as the fast-restore path: off-box is for disaster, local is for
    "someone dropped a table". **Local retention therefore must never reach zero** (T3).
- **D5 — encryption at rest. ✅ DECIDED: encrypt the artifact itself, with `age`, as part of publication.**
  Dumps contain `access_tokens` and every observation. **FileVault is Off and stays Off** — it requires a
  password at boot, which would break unattended reboot recovery: the mini restarts through a launchd agent that
  waits for Docker and runs `docker compose … up -d` with **no human present**
  (`scripts/launchd/start-sapphire.sh:13-28`, spec'd in `docs/plans/046-mac-mini-staging-deployment.md` §C1), and
  a FileVault boot password blocks login before any of that runs. *(The earlier draft cited "Plan 158 T6" for the
  2026-08-13 unattended-reboot proof; **no Plan 158 document exists** under `docs/plans/` or its archive —
  verified 2026-08-14. The argument above stands on tracked files; T7 records the 2026-08-13 observation in the
  runbook so it stops being an untracked claim.)*
  - **Key custody (PHASE B):** the age **recipient (public) key** lives on the mini
    (`./secrets/backup_age_recipient.pub` — not a secret); the **identity (private) key** deliberately does
    **not** live on the mini (team password manager + one offline copy, per `docs/standards/security.md`
    § Backup encryption). Otherwise a stolen disk yields both the ciphertext and the key.
  - **Accepted trade-offs, recorded rather than hidden:** (i) a local "someone dropped a table" restore now
    requires an operator to fetch the identity — minutes slower, and impossible for anyone without key access;
    (ii) the backup flow **cannot** prove decryptability at publication time (it has no identity), so
    decryptability is proven by T5's rehearsal, which is part of Host acceptance; (iii) a plaintext local copy
    is **not** retained — that would defeat the whole decision on an un-encrypted disk.
- **⚠️ D6 — OPEN: is a 24 h RPO acceptable?** Nightly dumps mean up to a day of lost observations and forecasts.
  Probably fine for Swiss staging; **likely not for Nepal v1 production**, where continuous archiving (WAL
  shipping / PITR) is the standard answer. Explicitly a **non-goal to build** here — but a conscious deferral,
  not an oversight.


## Phase A — BUILD NOW (single authoritative spec)

> **Structural note (2026-08-15).** Earlier revisions layered "corrections that override the text below" on top of
> task bodies. That produced **two contradictions in a row** — D2 re-decided but tasks still naming
> `prefect-worker`, then encryption deferred but T3/T4 still requiring `.dump.age`. Both were caught by review,
> neither should have existed. **The Phase A tasks below are now the single source of truth; there is no override
> layer.** Phase B/C sections are clearly marked as not-in-scope specs.

**Phase A closes exactly two of the four properties: COMPLETE and LOUD ON FAILURE.** It does **not** close
*verified* (no rehearsal until Phase B) or *survivable* (no encryption, no off-box copy).
**Ship gate: Phase B is REQUIRED before any customer release** — dumps contain `access_tokens` and `tenant_id`
in plaintext on an unencrypted disk (FileVault Off). Phase A restores an internal capability that is currently
broken; it does not make the backup customer-grade.

**No encryption in Phase A.** Confirmed by review: this introduces **no new plaintext exposure** versus today,
*provided* the temp is created `0600` on the same volume and stale temps are swept — today's direct-write file is
already plaintext there and can already survive a failure. The hard link adds a directory entry to the same
inode; it neither duplicates nor broadens access. The new path is strictly safer, because incomplete output never
matches the published glob.

**Rollout order is operational, not just logical: T1 → T2+T3 (one deploy) → T4.**

### T1 — a backup identity that can actually read the database

*In:* `docker/bootstrap-roles.sql`, `docker/bootstrap-roles.sh` (`SAPPHIRE_BACKUP_DB_PASSWORD_FILE` +
`-v backup_password=`), `docker-compose.yml` (secret `sapphire_backup_db_password`, mounted to **`init`** and
**`prefect-worker-backup`** only), `tests/integration/db/test_role_bootstrap.py`.
*Out:* the api/worker grant layout; `docker/entrypoint.sh`.

- `CREATE ROLE sapphire_backup LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE **INHERIT** NOREPLICATION`, using the
  existing `format(… %L)` + `\gexec` CREATE-or-ALTER-PASSWORD shape.
- `GRANT pg_read_all_data TO sapphire_backup;` — plain membership; `INHERIT` makes it effective without
  `SET ROLE`, which `pg_dump` never issues.
- `GRANT CONNECT ON DATABASE sapphire TO sapphire_backup;` — `pg_read_all_data` does not confer it.
- **Keep it OUT of the api/worker blanket-revoke block** (`bootstrap-roles.sql:78-101`); give it its **own**
  convergence block.
- **`REVOKE CREATE … FROM sapphire_backup` is NOT sufficient** — it cannot defeat a `CREATE` grant held by
  `PUBLIC`; the existing script makes this same mistaken claim at `:97-101`. Require
  **`REVOKE CREATE ON SCHEMA public FROM PUBLIC`**, keep the named-role revoke, and **assert
  `has_schema_privilege('sapphire_backup','public','CREATE') = false`**.
- **Fail loudly if a pre-existing `sapphire_backup` owns any object** (define the query and its scope) — direct
  ACL revocation cannot strip owner-intrinsic privileges.
- **Preflight, fail loudly:** `pg_class.relrowsecurity` count must be 0 and `pg_largeobject_metadata` empty
  (measured 2026-08-14: both 0). Otherwise the dump silently goes partial.

**Red-first / acceptance:** a **real login as `sapphire_backup` performing `SELECT` on `access_tokens`** — a
`pg_auth_members` row proves nothing. Plus a test with a **pre-created role owning an object**, asserting the
bootstrap fails rather than proceeding.

### T2 — the backup flow gets its own credential, with no URL

*In:* `src/sapphire_flow/flows/backup.py`, `docker-compose.yml` (`prefect-worker-backup` `environment:`),
`tests/unit/flows/test_backup.py`. *Out:* `docker/entrypoint.sh`; `DATABASE_URL` for every other flow.

`dump_database_task` reads `SAPPHIRE_BACKUP_PGHOST/PGPORT/PGUSER/PGDATABASE` + the password file and passes
`PGHOST/PGPORT/PGUSER/PGDATABASE/PGPASSWORD` to the child. **No URL is constructed or parsed anywhere in the
backup path.**

- **Build the child environment from an ALLOWLIST**, not by mutating `os.environ`. Inherited `PGSERVICE`,
  `PGOPTIONS`, `PGPASSFILE`, `PGSSLMODE` etc. must **not** reach libpq. Permit only the required `PG*` values
  plus deliberately-allowed process values (`PATH`, `LC_ALL=C`).
- Every required variable **missing or empty** is a loud failure, not a default.

**Red-first:** seed unrelated `PG*` variables in the environment and assert they are **absent** from the child;
assert the backup identity is used even when a conflicting worker credential is present.

### T3 — atomic publication: an artifact exists only if it is good

*In:* `src/sapphire_flow/flows/backup.py`. **Publishes exactly one file: `sapphire_<timestamp>_<8hex>.dump`,
mode `0600`, plaintext.** No `age`, no `.age` suffix, no sidecar — those are Phase B.

**Transaction, entirely under a filesystem advisory lock** (a Prefect concurrency limit does **not** cover manual
invocation, duplicate deployments, or a second scheduler — keep the random suffix and no-clobber as defence in
depth):

1. **Sweep stale temps**, then **pre-prune** retention *while preserving at least one valid artifact*, then
   **check headroom** — before consuming space, not after.
2. `pg_dump` → a `0600` temp whose name **does not match `sapphire_*.dump`**, same filesystem.
3. **Validate**: `pg_restore --list` must contain an **anchored, schema-qualified `TABLE DATA public
   access_tokens`** entry — this is the direct evidence the privilege fix worked. Tests must include **decoys**:
   another schema, `TABLE` without `DATA`, ACL/comment entries, and a similarly-named table.
4. `fsync` the file → **`os.link`** to the final name (atomic, no-clobber) → unlink the temp → `fsync` the
   **directory** → **this is the logical commit point**.
5. **Everything after the commit point is explicitly best-effort and MUST NOT change a successful result.**
   Retention/cleanup failing after a valid artifact exists must not mark the flow failed
   (today's separate cleanup task can, `flows/backup.py:115-121`).

**Concrete timeouts** for dump and validation, with termination **reaping the child before cleanup**.
**Red-first:** the fake subprocess must **write partial content before failing** (a fake that writes nothing
passes against today's code); plus tests for a timed-out child, lock contention from a second process, and
`fsync` failure.

### T4 — a monitor that cannot be fooled

*In:* `src/sapphire_flow/ops/watchdog.py` (`newest_backup_mtime` + the staleness block). Matches exactly
`sapphire_*.dump`.

- Freshness requires a **regular file with size > 0**, using **`lstat()` + `stat.S_ISREG`** — `Path.is_file()`
  follows symlinks, so a fresh symlink or directory must not satisfy it.
- **Notification state machine, including failed delivery:** clearing incident state after *attempting* recovery
  loses the recovery permanently if the post fails. Add a persistent **recovery-pending** state, retried each
  tick until delivery succeeds. Handle the legacy `last_backup_alert_iso` field (currently written at `:520`,
  never consulted).
- **Alert once, not every tick** — absorbs Plan 161 T3 (`watchdog.py:508-520`, ~288 alerts/day today).

**Red-first:** stale → repeated-stale → recovered → stale; **failed recovery delivery followed by a successful
retry**; a 0-byte file; a symlink.

### Phase A infrastructure — the dedicated backup component

**Decided, not left to the implementer:** a Prefect **`backup` work pool** with a dedicated
**`prefect-worker-backup`** service.

- Specify the **complete** service: pool command, Prefect env, backup `PG*` env, secrets, the `backups` volume,
  tmpfs, resource limits, network, logging, restart policy, `depends_on: init`.
- **Create the `backup` pool BEFORE deployment registration** (`init` registers deployments at
  `docker-compose.yml:288-293`, before any worker starts); route **only** `backup-database` to it.
- **Remove `backups:/data/backups` from the default `prefect-worker`** (`docker-compose.yml:130`).
- **Resolve the startup contract explicitly:** the shared image's entrypoint expects
  `DATABASE_URL_TEMPLATE`/`DB_PASSWORD_SECRET`; this service has neither. Choose and **test** a no-URL startup
  mechanism (e.g. a Compose entrypoint override).
- **Secret rotation requires container recreation** — file-backed Compose secrets
  (`docker-compose.yml:358-371`) do not reliably hot-reload. Procedure: re-run `init`, recreate
  `prefect-worker-backup`. **Acceptance-test that the old password fails and the next backup succeeds.**
- **CI assertion:** parse rendered `docker compose config` and assert the backup secret's consumer set is
  **exactly `{init, prefect-worker-backup}`**; that it is absent from api and ingest; that the default worker no
  longer mounts `backups`; and that the backup deployment targets only the `backup` pool.

### Phase A acceptance (host)

Trigger **one** `backup-database` run post-deploy and **record its run ID and start time**. Require: state
`COMPLETED`; an artifact whose mtime falls **inside that window**; `pg_restore --list` on **that exact artifact**
showing the schema-qualified `access_tokens` `TABLE DATA` entry; and **exactly one** recovery post measured
against a captured baseline. Explicitly **not** "the alerts went quiet".

---

## Phase B / C — DEFERRED, specs retained (NOT in this build)

**Phase B (required before customer release):** encryption at publication + key custody, the snapshot manifest
bound to the artifact digest, T5 restore-rehearsal-as-real-recovery-path (roles/ACLs + denial properties),
T6 mount validation (evaluated **before** freshness), full retention/age/byte policy, and migrating the existing
plaintext dumps. **Phase C:** off-box replication (D4) and RPO/PITR (D6).

### T5 — rehearse the restore (Repo + Host) — *the task that makes it a backup*

*In:* new `scripts/restore-rehearsal.sh`; runbook coverage in T7.

`pg_restore --list` proves only that the archive TOC is readable. This rehearsal restores a real artifact and
compares real evidence.

- **In a disposable instance, never the live cluster.** A throwaway container on a **temporary volume** using the
  pinned image from `docker-compose.yml:19` (`postgis/postgis:16-3.4@sha256:44126d…`). The earlier draft said
  "scratch database", which reads as *another database in the running cluster* — that would consume production
  `pgdata` (`docker-compose.yml:25`) and compete with the live service for I/O. This also matches
  `docs/architecture-context.md` § Restore rehearsal ("start a temporary PostgreSQL instance from the dump").
- **Preflight:** disk-headroom check (free space ≥ ~3× artifact size) before anything starts; abort with a clear
  message otherwise.
- **Steps:** decrypt with the operator-supplied age identity (`age --decrypt --identity <path>`) into a `0600`
  temp → `createdb --template=template0` in the throwaway instance → `pg_restore --single-transaction
  --exit-on-error --no-owner --no-acl` → **check the exit status** (`pg_restore` continues past SQL errors by
  default; neither flag is implied).
  - **Role/ACL handling — deliberate, with the trade-off named.** `--no-owner --no-acl` is chosen *for the
    rehearsal* because the throwaway instance has none of the cluster roles, and `docker/bootstrap-roles.sql`
    cannot create a usable grant set on an empty schema — it is written to run **after** `alembic upgrade head`
    (`bootstrap-roles.sql:3-6`) and its per-table matrix (`:132-176`) fails against a schemaless database.
    Consequence, stated: the rehearsal proves **schema + data** restorability, **not** ACL restorability.
  - **Real recovery ordering** is the other path, documented and walked through once in T7: create roles/owner →
    restore **with** owners and ACLs into an empty database → re-run the role bootstrap to converge grants. Do
    **not** migrate the target first unless collisions are explicitly handled.
- **Evidence compared** (not "it exited 0"): row count and a representative row from **`access_tokens`** (the
  table that was failing) and `access_token_stations`; object counts by class from the catalog (tables, views,
  sequences, indexes, constraints); `last_value` for both sequences; and `alembic_version` matching the source.
- **Cleanup under `trap`/`finally`**, unconditionally including on failure: remove the container, the temporary
  volume, and the decrypted plaintext.
- **Cadence: operator-run** — on demand, and after any change to the backup path. Automating it monthly stays
  with Plan 048 (see follow-ons), because an unattended rehearsal would require the age identity to live on the
  mini and would undo D5.

### T6 — the local backup path must be the device it claims to be (Host + Repo)

*In:* `scripts/bootstrap-mac-mini.sh`, `scripts/launchd/start-sapphire.sh`, `src/sapphire_flow/ops/watchdog.py`,
`docs/deployment/mac-mini-staging.md`.

The local dumps are *supposed* to live on an external volume — `docker-compose.macmini.yml:27` binds
`/Volumes/sapphire-backup/pg_dumps`, and `DEFAULT_BACKUP_DIR` is the same path (`watchdog.py:56`). But the only
check anywhere is a **sentinel file an operator can create by hand** (`scripts/bootstrap-mac-mini.sh:222-233`;
the runbook literally instructs `mkdir -p` + `touch` at `docs/deployment/mac-mini-staging.md:396-410`), and the
unattended start path performs **no disk check at all** before `docker compose … up -d`
(`scripts/launchd/start-sapphire.sh:24-28`). That is exactly how 7 dumps ended up on `/dev/disk3s5`, the same
device as the database.

- **Verification predicate**, shared by all three call sites: the backup directory's device id differs from `/`'s
  (`stat -f %d`) **and** the path is a real mount point of an attached volume (`mount` / `diskutil info -plist`),
  not merely a directory. The sentinel file survives only as a *label*, never as the proof.
- **`bootstrap-mac-mini.sh`: fails closed** on a failed check — interactive, so blocking is safe.
- **`start-sapphire.sh`: checks, records, and proceeds — deliberately NOT fail-closed.** Trade-off recorded:
  refusing to start the whole stack because a removable disk is absent trades a backup outage for a *forecasting*
  outage, which is precisely the mistake this plan exists to avoid. It writes a machine-readable marker beside
  the compose files and continues; visibility comes from the next bullet.
- **Watchdog: an independent device check each tick**, raising a **distinct** condition — "backup volume not
  mounted; dumps would land on the boot disk" — with its own message and its own incident/notification state
  (T4's split). Never folded into staleness: a mounted-but-stale volume and an unmounted volume need different
  operator actions, and the current failure mode is that the second is invisible.
- **Tests:** shell tests for the predicate (real mount vs plain directory vs missing path) and watchdog unit tests
  for the new condition, including that it neither suppresses nor duplicates the staleness alert.

### T7 — reconcile the authoritative docs, and the runbook (Docs)

*In:* `docs/deployment/mac-mini-staging.md`, `docs/v0-scope.md` §A10, `docs/standards/security.md`
§ Backup encryption, `docs/standards/cicd.md` § DB role bootstrap, `docs/conventions.md` § Service users,
`docs/touchpoint-maps.md` § Prefect / Docker / deployment (`:535`), `docs/architecture-context.md` § backup /
restore rehearsal, `docker-compose.yml` comments, `docs/plans/048-restic-restore-rehearsal.md`.

Every one of these currently contradicts what this plan ships:

- `docs/v0-scope.md:193` — "v0: `pg_dump` to local disk. No restic, no encrypted backup chain, no restore
  rehearsal." → v0 now encrypts the dump (with `age`, **not** restic) and has a rehearsed restore.
- `docs/standards/security.md:367` and § Backup encryption — say encryption is v1-only and handled by restic.
  Must describe `age`, where the recipient public key lives (on the mini) and where the identity does **not**
  (team password manager + one offline copy), and the recovery consequence of losing it.
- `docs/standards/cicd.md:744` — "Three DB password secrets exist, one per credential tier" → **four**, with the
  `sapphire_backup_db_password` row and its mounts (`init` + `prefect-worker-backup` only), plus the new
  `SAPPHIRE_BACKUP_*` connection variables and why they are separate from `DATABASE_URL`.
- `docs/conventions.md:322-334` § Service users — documents exactly **two** scoped roles. Add `sapphire_backup`,
  its read-only grant matrix, the **INHERIT divergence and its rationale** (D1), the cross-database connectivity
  policy including the accepted `postgres`-database residual, and the rule that every new database must
  `REVOKE CONNECT … FROM PUBLIC`.
- `docs/architecture-context.md:3022-3027` — describes the restore rehearsal as monthly, automated, and logged to
  `pipeline_health`. Record what now exists (operator-run script, evidence compared, disposable instance) and what
  stays deferred, so the doc stops describing an aspiration in the present tense.
- `docs/touchpoint-maps.md:535` § Prefect / Docker / deployment — add the backup credential path and the
  mount-verification behaviour as touchpoints.
- **Runbook** (`docs/deployment/mac-mini-staging.md`): restore both ways ("recover one table" and "rebuild the
  host"); fresh-cluster ordering (roles → restore **with** owners/ACLs → re-run the role bootstrap; do not
  migrate the target first unless collisions are handled); age key custody and identity rotation; rotating the
  `sapphire_backup` password (edit the secret → re-run `init` → effective next run, no restart); the D4/D5
  trade-offs; T6's mount-verification behaviour, replacing the "just `mkdir` + `touch`" instructions at
  `:396-410`; and the 2026-08-13 unattended-reboot observation, so the FileVault argument rests on a tracked
  record rather than an absent Plan 158.
- **Plan 048 relationship — previously undeclared.** `docs/plans/048-restic-restore-rehearsal.md` § Scope
  currently owns encrypted backup *and* monthly restore rehearsal. This plan takes **encryption of the dump** and
  **the restore rehearsal itself**; 048 is **narrowed**, in its own file, to restic snapshots, full-filesystem
  coverage (artifacts / Parquet), the automated monthly cadence, and the off-site target. Not a supersession —
  front matter stays `supersedes: []` — but 048's scope note must say so explicitly.

## Exit gates (END STATE — Phase A's subset is every gate below EXCEPT the T6 / restore-rehearsal items)

Full `uv run pytest`; `ruff format --check` + `ruff check`; pyright ratchet; `shellcheck` + `bash -n` for
`docker/bootstrap-roles.sh`, `scripts/restore-rehearsal.sh`, `scripts/bootstrap-mac-mini.sh` and
`scripts/launchd/start-sapphire.sh`; `docker compose config -q`; and **each locking test named in T1, T2, T3, T4
and T6 proven RED against current committed code** (T1's are integration tests against a real Postgres via the
existing harness at `tests/integration/db/test_role_bootstrap.py:41`).

## Host acceptance — END STATE (Phase B/C)

> **Phase A's acceptance is the one under "Phase A acceptance (host)" above — this section is the END-STATE
> bar and is NOT satisfiable in Phase A** (no encryption, no sidecar, no rehearsal, no mount to validate).

`backup-database` reaches **`COMPLETED`**; `pg_stat_activity` / the Postgres log shows the dump connecting as
**`sapphire_backup`**; exactly one `sapphire_*.dump.age` with a **current** timestamp and mode `0600` appears,
with its `.sha256` sidecar and **no** plaintext or `.part` remnant in the directory; **T5's rehearsal decrypts and
restores that exact artifact** and reports matching evidence including `access_tokens`; the watchdog posts a
single recovery message and then goes quiet; and the watchdog reports the mount condition truthfully in both the
mounted and unmounted states. **Not** "the alerts went quiet" — that is precisely the evidence that failed us
twice.

## Non-goals / follow-ons

- **Off-box replication + D4 (follow-on plan).** Publish each validated artifact to the chosen destination;
  verify the remote copy by **size + digest against the `.sha256` sidecar**, never by the upload command's exit
  code; define remote retention **independently** of local retention; alert if the off-box copy is missing or
  stale, or this silently becomes a local-only backup again. Split out because D4 needs an owner decision on
  cost/residency/credentials, and because D5 already makes the artifact safe in transit and at rest — this plan's
  acceptance criteria never depended on it. **Consequence stated: until that plan lands, a site loss still loses
  the database.**
- **Continuous archiving / PITR** (D6) — the real answer to a 24 h RPO, deliberately deferred.
- **Automated monthly restore rehearsal** — stays with Plan 048; blocked by D5's key custody (see T5).
- **Backing up the separate `prefect` database** — flow-run history and deployment state have **no backup at
  all** today.
- **Rotating the worker DB password** (its first two characters leaked into a Prefect state message; Plan 161).
- **Plan 161 T2** (construct-don't-splice) — independent; after T2 here, the backup path constructs no URL at
  all, so neither plan gates the other.

