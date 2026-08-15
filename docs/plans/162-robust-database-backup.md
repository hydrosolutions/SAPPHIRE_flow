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

## ⛔ Round-2 independent review — corrections that OVERRIDE the text below

Round 2 ran 2026-08-15 (round 1's rerun died to machine sleep). Verdict **NEEDS-CHANGES**: 5 blockers, ~13
majors. **Confirmed sound and unchanged:** D1's `INHERIT` + `pg_read_all_data` mechanism (and that nothing in the
existing bootstrap inadvertently touches `sapphire_backup`); D2's security argument; T2's explicit `PG*` child
environment; T3's non-matching temp names + validate-before-publish + `os.link` no-clobber; T4's
regular-file-and-size-positive predicate; T5's disposable-instance rehearsal.

**B1 — D2 was not propagated (my error).** I re-decided D2 to a dedicated component but left every task pointing
at `prefect-worker`. Corrected inline above. **Additionally required:** choose the **Prefect `backup` work pool**
now (not "pool or one-shot, decide later"), and specify the complete `prefect-worker-backup` service — pool
command, **pool creation before deployment registration**, deployment routing, Prefect env, backup `PG*` env,
recipient-file mount, secrets, backup volume, tmpfs, limits, network, logging, restart policy, `depends_on: init`
— **remove `backups:/data/backups` from the default worker (`docker-compose.yml:130`)**, and resolve how the
shared image starts without its usual `DATABASE_URL_TEMPLATE`/`DB_PASSWORD_SECRET` entrypoint contract.

**B2 — plaintext staging must not live on the persistent backup volume.** `finally` cannot run after SIGKILL/OOM/
power loss, so a plaintext `.part` **containing token hashes** could persist indefinitely — worst of all while
later backups keep failing. Stage plaintext on a **size-bounded tmpfs** available only to the backup component;
encrypt into a `0600` temp on the backup volume; sweep the tmpfs at task **start** as well as in cleanup. Do not
claim "no plaintext survives" while persistent plaintext staging exists.

**B3 — mount validity must be evaluated BEFORE artifact freshness.** As written the goal, T4 and T6 conflict: T6
makes mount status an independent alert while T4 would still accept a fresh artifact written into the *fallback
boot-disk directory*. Required: while unmounted, do **not** inspect fallback files, do **not** clear the stale
incident, and do **not** emit recovery. Backup health = `mount_ok AND valid_fresh_artifact` (separate messages are
fine). Prefer blocking only the backup component from publishing, not forecasting/API startup.

**B4 — "evidence matches the source" is undefined and unbuildable as written.** Querying the live source during a
later rehearsal will *legitimately* disagree with an older dump. Required: generate a **manifest from the same
exported snapshot `pg_dump` uses** (counts, object inventory, sequence values, migration version, non-sensitive
row digests), encrypt it, **bind it to the artifact digest**, and compare the restored database against *that*.

**B5 — the rehearsal must BE the real recovery path.** It currently excludes roles/ACLs while a separate prose
path describes real recovery. `pg_dump` does not back up cluster roles, and the bootstrap cannot create roles
before schema restore because it grants against tables. Required: provision the canonical owner, restore as that
owner with `--no-owner --no-acl`, run role bootstrap **after**, then test live api/worker/backup logins **and the
denial properties** (e.g. worker still cannot read `access_tokens`).

**Majors** (~13) are recorded in the review and must be folded before READY. The load-bearing ones: the new role
needs its **own convergence block** and `REVOKE CREATE` cannot override `PUBLIC` (the existing script makes the
same mistaken claim at `bootstrap-roles.sql:97-101`); the `age` contract is not buildable as written
(`--recipients-file`, not `--recipient-file`; pin the version; stream into an open `0600` descriptor); publication
needs an **fsync + a defined commit point**; secret rotation needs container recreation, not live refresh; a
**filesystem advisory lock** (a Prefect concurrency limit does not cover manual invocation); retention needs
age/byte policy, paired sidecar deletion and pre-dump headroom checks; the **existing plaintext dumps need a
migration** or the "no plaintext" acceptance is untrue; `3× artifact size` is the wrong restore-headroom model;
and the CI assertion should pin the secret's **complete consumer set** to `{init, prefect-worker-backup}`.

## ⚠️ D7 — OPEN (raised by me, not the reviewer): should this plan ship in phases?

Backups have now been broken since **2026-08-13 02:01 UTC**. This plan has taken two review rounds and is still
NEEDS-CHANGES, and its scope keeps growing (encryption, key custody, manifests, rehearsal, retention, disk
exhaustion, locking, RPO). Every day spent perfecting it is another day with **no automated backup**.
Proposed split — same end state, earlier safety:
- **Phase A (restore correctness):** T1 role + T2 `PG*` + T3 atomic publish + T4 monitor. Backups run again,
  complete, and fail loudly. Encryption included — it defines the artifact and B2 makes it urgent.
- **Phase B (prove it):** T5 rehearsal-as-recovery-path, T6 mount check, retention/headroom, key-custody contract.
- **Phase C (survive the site):** off-box replication (D4), RPO/PITR (D6).
Recommendation: **take the split.** Phase A is the part that stops the bleeding; B and C are what make it a
product. The alternative — one big READY — is defensible but keeps the current gap open for days.

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
  - **⚠️ Sub-choice for the implementer, decide at build time:** a **Prefect `backup` work pool** (consistent
    with the repo's flows-and-observability standard, and preserves the failure-detection signal T4 depends on)
    **vs a minimal one-shot backup container** (smaller attack surface — no flow code, no adapters — but must
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
  *(Amended by D5: the published name is now `sapphire_*.dump.age`, so T4's glob must change with it or the
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
  - **Key custody:** the age **recipient (public) key** lives on the mini
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

## Tasks

### T1 — a backup identity that can actually read the database (Repo)

*In:* `docker/bootstrap-roles.sql`, `docker/bootstrap-roles.sh`, `docker-compose.yml` (new
`sapphire_backup_db_password` secret; mount into `init` and **`prefect-worker-backup`** — NOT the default worker),
`tests/integration/db/test_role_bootstrap.py`. *Out:* the api/worker grant layout, `docker/entrypoint.sh`.

- **Create as `INHERIT`** (D1): `CREATE ROLE sapphire_backup LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
  NOREPLICATION NOBYPASSRLS INHERIT PASSWORD %L`, via the same `format(...) + \gexec` create-or-alter pair used
  for the other two roles (`bootstrap-roles.sql:31-51`), so a password rotation is picked up on re-run.
- **Its own convergence block**, and **not** the api/worker one. The blanket membership REVOKE at
  `bootstrap-roles.sql:78-82` is scoped `WHERE member.rolname IN ('sapphire_api','sapphire_worker')` and stays
  that way. `sapphire_backup` instead gets, unconditionally on every deploy:
  1. `ALTER ROLE sapphire_backup NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS INHERIT;`
     (demotes a pre-existing over-privileged role — same guarantee as `:70-71`);
  2. a targeted membership revoke — every membership **except** `pg_read_all_data`, generated with `\gexec` in
     the same shape as `:78-82`;
  3. `REVOKE ALL PRIVILEGES ON ALL TABLES / ALL SEQUENCES / SCHEMA public / DATABASE sapphire / DATABASE prefect
     FROM sapphire_backup;` then the intended grants below.
  No re-grant-after-revoke choreography exists, so there is no ordering step for a future deploy to drop.
- **Intended grants — read only:** `GRANT pg_read_all_data TO sapphire_backup;`,
  `GRANT CONNECT ON DATABASE sapphire TO sapphire_backup;`, `GRANT USAGE ON SCHEMA public TO sapphire_backup;`
  (`pg_read_all_data` confers `SELECT` on tables/views/sequences and schema `USAGE`, but **not** `CONNECT`), and
  `REVOKE CREATE ON SCHEMA public FROM sapphire_backup;`. No `INSERT`/`UPDATE`/`DELETE`, ever.
- **Cross-database connectivity policy — stated, not hand-waved.** `pg_read_all_data` is **cluster-wide**, so
  connectivity is the control, not the grant:
  - `sapphire` — `CONNECT` granted (above).
  - `prefect` — denied, and **already enforced** by the existing `REVOKE CONNECT ON DATABASE prefect FROM PUBLIC`
    (`bootstrap-roles.sql:112`). A per-role `REVOKE CONNECT` would **not** be sufficient on its own — the file
    itself documents why (`:104-111`: every role inherits PUBLIC's ACL). Do not add one and call it the control;
    the blanket `REVOKE ALL PRIVILEGES ON DATABASE prefect` above is defense-in-depth only.
  - `postgres` (default maintenance DB) — **stays connectable via PUBLIC**, accepted: it holds no application
    data, and revoking PUBLIC `CONNECT` there risks breaking `psql`/admin tooling. Recorded as a documented
    residual (T7).
  - **Rule for future databases** (T7 → `docs/conventions.md` § Service users): any new database must
    `REVOKE CONNECT … FROM PUBLIC` at creation, because this role can read anything it can reach.
- **Preflight assertions** (implemented in the flow by T3, specified here): fail loudly, before dumping, if
  RLS-enabled tables > 0 or large objects > 0. Measured 2026-08-14: **RLS tables = 0, large objects = 0,
  sequences = 2**.
  - **Correction to the earlier draft:** an RLS table would **not** make `pg_dump` silently partial. PostgreSQL
    16 dumps with `row_security = off` and *errors* when the dumping role cannot bypass RLS; a policy-filtered
    partial dump requires `--enable-row-security`, which we never pass. The preflight is kept purely as an
    **earlier, clearer fail-fast** (an alert naming the offending tables at 02:00, instead of a mid-dump server
    error) — and must be described and tested that way, not as protection against silence.
  - Large objects are the genuinely uncovered case (`pg_read_all_data` does not include them), so the count check
    earns its place there.

**Red-first / acceptance — real connections only.** A `pg_auth_members` row proves nothing; every assertion below
is a live login executing a real statement, extending the existing harness
(`tests/integration/db/test_role_bootstrap.py:41`). All fail today because the role does not exist:

1. `sapphire_backup` logs in and `SELECT`s from **`access_tokens`** and `access_token_stations` — the tables that
   broke — while `sapphire_worker` still cannot (`:353-364` stays green).
2. reads sequence state (`SELECT last_value FROM …`).
3. **cannot** `INSERT`/`UPDATE`/`DELETE` on `stations` and `observations`; **cannot** `CREATE TABLE`;
   **cannot** `DROP TABLE` (mirrors `:189-213`).
4. `pg_roles` shows `rolsuper`/`rolcreatedb`/`rolcreaterole`/`rolreplication`/`rolbypassrls` all false **and
   `rolinherit` true** (mirrors `:166-188`, with the deliberate INHERIT divergence asserted, not assumed).
5. **cannot** connect to the `prefect` database (mirrors `:215-227`).
6. password rotation invalidates the old password (mirrors `:427-448`).
7. a second bootstrap run leaves the membership **and a live protected `SELECT`** working (idempotent re-grant —
   mirrors `:388-426`).
8. a deliberately pre-created **over-privileged** `sapphire_backup` (SUPERUSER + CREATEDB + write grants)
   converges to the least-privilege shape (mirrors `:608-672`).

### T2 — the backup flow gets its own credential without touching the shared one (Repo)

*In:* `src/sapphire_flow/flows/backup.py`, `docker-compose.yml` (**`prefect-worker-backup`** `environment:` + the T1
secret mount; and **remove `backups:/data/backups` from the default `prefect-worker` at `docker-compose.yml:130`**), `tests/unit/flows/test_backup.py`. *Out:* `docker/entrypoint.sh` (**deliberately untouched**),
`DATABASE_URL` / `DATABASE_URL_TEMPLATE` for every other flow.

**This task exists because the earlier draft assumed a mechanism that does not exist.** `dump_database_task` reads
`os.environ["DATABASE_URL"]` (`backup.py:57`) and derives every connection argument and `PGPASSWORD` from it
(`:59-68`). That single URL is built **once at container start** by `docker/entrypoint.sh:20-22` from one
`DATABASE_URL_TEMPLATE` + `DB_PASSWORD_SECRET` pair, set to the **worker** credential at
`docker-compose.yml:99-100`, inside the same process that serves **every** deployment on the `default` pool
(`docker-compose.yml:83`; e.g. the forecast cycle and ingest read `os.environ["DATABASE_URL"]` directly). There is
no per-deployment credential override anywhere: `DeploymentSpec` carries only
flow_module/flow_attr/deployment_name/cron/concurrency_limit/work_pool_name
(`src/sapphire_flow/cli/register_deployments.py:26-32`).

- **Rejected: repoint the container-wide `DATABASE_URL` at `sapphire_backup`.** It would silently downgrade every
  other flow in the pool to read-only mid-run — a worse and more silent outage than the one this plan fixes.
- **Chosen: a second, backup-only path that never touches `DATABASE_URL` and never builds a URL.**
  - `docker-compose.yml` → `prefect-worker.environment`, all **non-secret**: `SAPPHIRE_BACKUP_PGHOST: postgres`,
    `SAPPHIRE_BACKUP_PGPORT: "5432"`, `SAPPHIRE_BACKUP_PGUSER: sapphire_backup`,
    `SAPPHIRE_BACKUP_PGDATABASE: sapphire`, `SAPPHIRE_BACKUP_DB_PASSWORD_FILE:
    /run/secrets/sapphire_backup_db_password`, plus the T1 secret mount. (`prefect-worker-ingest`,
    `docker-compose.yml:158-159`, gets **none** of these — it never runs this flow.)
  - `dump_database_task` reads those five **at run time**, reads the password **from the file, per run**, and
    builds an isolated child environment `PGHOST/PGPORT/PGUSER/PGDATABASE/PGPASSWORD`.
  - **Every worker-derived connection input is removed**: `os.environ["DATABASE_URL"]`, `_to_libpq_url`,
    `make_url`, and the `--host/--port/--username/--dbname` arguments (`backup.py:57-68`). `pg_dump` is invoked
    with format/file/timeout arguments only.
  - **No fallback.** A missing or empty variable, or an unreadable/empty secret file, fails the task immediately
    with a message naming the variable. Falling back to `DATABASE_URL` is exactly how this bug class survives.
  - **Rotation semantics:** because the password is read per run — unlike `DATABASE_URL`, spliced once at
    container start (`entrypoint.sh:20-22`) — rotating `./secrets/sapphire_backup_db_password` and re-running
    `init` takes effect on the **next scheduled run, with no container restart**. Documented in T7. (Rotating the
    *worker* password still needs a restart; unchanged, out of scope.)
  - **Relation to Plan 161 T2:** independent in both directions. The secret reaches `pg_dump` as `PGPASSWORD` and
    is never placed in a URL, so no encoding/parsing question arises for backups at all.

**Red-first** (fails today — `backup.py:57` reads `DATABASE_URL`):
1. Set `DATABASE_URL` to a **worker** credential and `SAPPHIRE_BACKUP_*` to a **conflicting backup** credential;
   capture the fake subprocess's **argv and env**; assert `PGUSER`/`PGPASSWORD`/`PGHOST`/`PGDATABASE` come from
   the backup pair, and that **no** `--host/--port/--username/--dbname` argument and no `DATABASE_URL`-derived
   value appears anywhere in argv or env.
2. Unset `SAPPHIRE_BACKUP_DB_PASSWORD_FILE` (or point it at a missing file) → the task raises naming the
   variable, and **no subprocess is launched**.

**Host acceptance (T1+T2 together):** the dump connects as `sapphire_backup` — visible in `pg_stat_activity` /
the Postgres log `user=` field during the run — and the resulting artifact contains `access_tokens` (proven by
T5's rehearsal, not by the exit code).

### T3 — atomic, encrypted publication: an artifact exists only if it is good (Repo)

*In:* `src/sapphire_flow/flows/backup.py`, `src/sapphire_flow/cli/register_deployments.py`,
`tests/unit/flows/test_backup.py`, `tests/unit/cli/test_register_deployments.py`.

`pg_dump` currently writes straight to the final path and a failure leaves the file behind (`backup.py:62,71-76`).
Deleting on non-zero exit is **not sufficient** — it cannot run under `SIGKILL`/OOM, and misses exceptions from
`subprocess.run`, from post-dump filesystem operations, or from cleanup itself.

**One artifact lifecycle, end to end.** Exactly one published artifact per successful run:
**`sapphire_<UTC-timestamp>_<8-hex>.dump.age`**, mode `0600`. **No plaintext dump ever survives the run.**

1. **Create the plaintext temp already protected.** `mkstemp(dir=backup_dir, prefix=".sapphire-tmp-",
   suffix=".part")` → mode `0600` **before `pg_dump` writes a byte**, and a name that deliberately does not match
   the published glob. (Today the chmod happens only *after* the dump completes — `backup.py:71` then `:78` — so
   token hashes sit at the process umask for the entire dump and validation window. Set a restrictive umask
   around the call as belt-and-braces.)
2. **Dump** with T2's environment and a bounded timeout.
3. **Validate — concretely.** (a) the temp is a regular file with size > 0; **and** (b) `pg_restore --list <temp>`
   exits 0 within a bounded timeout **and** its TOC output contains an entry for `access_tokens`. Size > 0 alone
   cannot establish archive structure, and the name-implies-valid invariant T4 depends on has to have a testable
   meaning. Any non-zero exit, timeout, or raised exception → nothing is published. (T5 remains the stronger,
   real-restore proof; this is the cheap gate that runs every night.)
4. **Encrypt** to a second temp: `age --recipient-file "$SAPPHIRE_BACKUP_AGE_RECIPIENT_FILE" --output <temp2>
   <temp>`; check the exit code.
5. **Verify the encrypted artifact:** regular file, size > 0, begins with the `age-encryption.org/v1` header;
   compute the sha256, log it, and write it to a sidecar `<final>.sha256` (the off-box follow-on verifies against
   this rather than trusting an upload command's exit code).
6. **Publish no-clobber and atomically:** `os.link(temp2, final)` — which raises `FileExistsError` if the name is
   taken, where `os.rename` would silently clobber — then unlink `temp2`. Same filesystem by construction (both
   temps are created inside `backup_dir`).
7. **`finally`: best-effort delete of both temps**, so a plaintext dump never outlives the run and a crash remnant
   can never match the published glob — the guarantee no post-hoc cleanup code can make.

**Recorded trade-off:** publication cannot prove *decryptability*, because the age identity deliberately is not on
the mini (D5). T5's rehearsal supplies that proof and is part of Host acceptance.

**Retention is a validated boundary, not a raw int.** Local retention **must be ≥ 1**; `0` or negative raises
`ValueError` at the flow boundary (a frozen value type with `__post_init__`, per CLAUDE.md § Type Driven
Development). Today `keep_count=0` deletes the dump the run just created (`backup.py:96-107`) and the suite
**locks** that behaviour (`tests/unit/flows/test_backup.py:96` `test_keep_zero_removes_all`) — that test is
**replaced** by rejection tests. Deliberate contract change: D4 makes the local copy the fast-restore path, so a
configuration that deletes it is invalid, not merely unusual. Remote retention is defined independently by the
off-box follow-on. Cleanup globs the **new** final pattern and additionally sweeps `.sapphire-tmp-*.part`
temporaries older than a bounded age (today retention only considers finalised dumps).

**Concurrency.** Add `concurrency_limit=1` to the `backup-database` `DeploymentSpec`
(`register_deployments.py:75-80`; the suite currently asserts `is None` at
`tests/unit/cli/test_register_deployments.py:73` — update it, matching `forecast-cycle`'s precedent at `register_deployments.py:73`).
Independently of the limit, the `_<8-hex>` suffix plus no-clobber `os.link` make an overlap non-destructive rather
than mutually-overwriting (final names had only second-level uniqueness). Retention runs after publication within
the same flow and, with the limit, is never concurrent with another run's publication.

**Red-first** (each must fail against current committed code):
(a) fake `pg_dump` **writes partial content and then exits non-zero** → no `*.dump.age`, no plaintext, no `.part`
remnant (a fake that writes nothing passes today and proves nothing);
(b) validator (`pg_restore --list`) exits non-zero on a **non-empty** temp → nothing published;
(c) the subprocess call raises → nothing published;
(d) the plaintext temp's mode is `0o600` **as observed from inside the fake `pg_dump`**, not only afterwards;
(e) publishing into a directory that already holds the target name → `FileExistsError`, existing artifact byte-
identical afterwards;
(f) `keep_count` of `0` and `-1` → `ValueError`, and nothing is deleted;
(g) the encryption step fails → nothing published and no plaintext left behind.

### T4 — a monitor that cannot be fooled (Repo)

*In:* `src/sapphire_flow/ops/watchdog.py` (`newest_backup_mtime` `:258-272`, `WatchdogState` `:101-110`, the
staleness block `:499-520`) and the watchdog unit tests. **Unconditional** — the earlier draft said "if not
already shipped"; verified unshipped: the alert fires on **every tick** (~288/day, `:508-520`) and
`last_backup_alert_iso` is written at `:520` but never read.

- **Follow T3's rename.** The glob becomes the published `*.dump.age` pattern (today `*.dump`, `:263`). Without
  this the monitor matches nothing after T3 and goes permanently, *falsely*, stale — a cross-task invariant, not
  an optional tidy-up.
- **Freshness predicate:** the newest **regular file with size > 0** matching the published pattern. Stated as
  policy: T3 makes name-implies-valid true; the size floor is the cheap independent check so a truncated or
  zero-byte artifact can never satisfy freshness. An invalid, zero-size or absent artifact must **never** clear
  an incident or reset alert state.
- **Split incident state from notification state.** `default_slack_poster` already reports delivery failure by
  returning `False` (`:306-320`), but state advances regardless (`:515-520`), so a transient Slack failure on the
  first stale tick would suppress the next attempt for a day — the opposite of "loud on failure".
  - `backup_incident_since_iso` — set on the first stale tick, cleared **only** on recovery.
  - `last_backup_alert_posted_iso` — advanced **only after a successful post**.
  - Cadence: while stale with nothing delivered yet, retry **every tick**; after a successful post, re-notify at
    most every ~24 h; on the transition back to fresh, post exactly **one** recovery message and clear both
    fields. A missing webhook counts as *not delivered* (log-only path, `:518-519`), so configuring the webhook
    later still produces an alert.
  - Transitions to lock: stale → repeated-stale → recovered → stale.

**Red-first** (each fails against current committed code):
(a) a **0-byte** file with a fresh mtime matching the published pattern → still reported stale (fails today:
`:258-272` checks mtime only);
(b) two consecutive stale ticks → **exactly one** Slack post (fails today: `:508-520` posts every tick);
(c) a stale tick where the poster returns `False`, then a second tick → a **second** post is attempted (fails
today: state advances regardless of `posted`);
(d) stale → fresh → **exactly one** recovery message, then silence (fails today: there is no recovery message at
all).

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

## Exit gates

Full `uv run pytest`; `ruff format --check` + `ruff check`; pyright ratchet; `shellcheck` + `bash -n` for
`docker/bootstrap-roles.sh`, `scripts/restore-rehearsal.sh`, `scripts/bootstrap-mac-mini.sh` and
`scripts/launchd/start-sapphire.sh`; `docker compose config -q`; and **each locking test named in T1, T2, T3, T4
and T6 proven RED against current committed code** (T1's are integration tests against a real Postgres via the
existing harness at `tests/integration/db/test_role_bootstrap.py:41`).

## Host acceptance

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
