---
status: READY
created: 2026-08-14
plan: 162
title: Robust database backup — complete, verified restorable, loud on failure, and safe to move off the machine
scope: Make the nightly database backup a backup in the operational sense rather than a file that appears. Covers the credential it runs as (a dedicated read-only role, since it currently runs on a deliberately partial one) AND the mechanism by which that credential reaches `pg_dump` without disturbing the shared worker connection, atomic + encrypted publication so a failed dump cannot masquerade as a good one and a stolen disk does not yield token hashes, a monitor that cannot be fooled by an empty file or by an unmounted backup volume, and a rehearsed restore in a disposable instance — because nothing here has ever been restore-tested. Encryption at rest (D5) is DECIDED here because it defines the published artifact; the off-box destination (D4) and the replication task are split into a follow-on, since an encrypted artifact is safe wherever it lands. Absorbs T3/T4/T5 from Plan 161. Continuous archiving/PITR and backing up the separate `prefect` database are named non-goals.
depends_on: []
blocks: []
supersedes: []
---

# Plan 162 — Robust database backup

## 📌 Owner context 2026-08-18 — the off-box destination is DEPLOYMENT-DEPENDENT

Recorded from the owner, and it changes Phase B's shape:

- **DHM (on-prem):** backups go to a **different drive on the same network** — i.e. a second local disk or a
  network mount.
- **AWS:** **S3**, or **Cloudflare R2**.
- **Staging (this mini):** same-disk today, and that is tolerable *because it is staging* — the work still has
  to be correct here so it is proven before DHM.

**Consequence 1 — D4 is not a single choice, it is a CONFIGURABLE SINK.** Do not hardcode a destination. Two
shapes cover every case above: a **filesystem path** (second drive / network mount) and an **S3-compatible
endpoint** (S3, R2). Same validated artifact, same publish step, different sink.

**⚠️ Consequence 2 — this REVIVES T6 (mount validation), which Phase A cut.** It was cut because
`/Volumes/sapphire-backup` is a plain directory on the boot disk, so there was no mount to lose. With a real
drive or network mount that reverses into the classic failure: **the volume unmounts and backups write into the
mount POINT on the boot disk** — correct filenames, fresh timestamps, monitor green, and every copy sitting on
the disk you were trying to escape. Mount validity must be checked **before** artifact freshness (the ordering
already specified), and it becomes load-bearing exactly when a real destination exists.

**Consequence 3 — sequencing.** Off-box replication is now primarily **deployment-readiness work for DHM/AWS**
rather than an urgent staging fix. It should land before the DHM deployment, not necessarily before anything
else. Encryption (D5) keeps its own justification independent of destination: dumps carry `access_tokens` and
`tenant_id`, and an artifact that is safe at rest is safe on any of these sinks.

## 🔬 VERIFIED RESTORE PROCEDURE (2026-08-18) — the rehearsal found two real DR blockers

**The backup IS restorable — proven with data**, against the live 1.1 GB artifact
(`sapphire_20260818_081448_40ee7bc2.dump`) on the mac-mini:

```
tokens | forecasts | observations | alembic
     1 |       206 |        83292 |    0048
```

`access_tokens` present — the table whose absence started this whole thread — and `alembic_version` matching
production.

**But the obvious path does NOT work. Two independent blockers, invisible until someone actually tried:**

| Attempt | Result |
|---|---|
| restore into the image's default `postgres` DB | ✗ `ERROR: schema "tiger" already exists` — the PostGIS image pre-initialises `tiger`/`tiger_data`/`topology` there |
| fresh DB, owners preserved | ✗ `ERROR: role "sapphire" does not exist` — `pg_dump` does **not** back up cluster roles, but the dump is full of `OWNER TO` / ACL statements |
| **fresh DB + `--no-owner --no-acl`** | **✓ restored clean, content verified** |

**The verified procedure — put this in the DR runbook:**
1. `createdb` a **fresh** database (never the PostGIS image's default).
2. `pg_restore --single-transaction --exit-on-error --no-owner --no-acl`.
3. Run `bootstrap-roles.sh` afterwards to recreate roles and converge grants.

**This is exactly what an earlier review of this plan predicted** (*"pg_dump does not back up cluster roles,
while the dump can reference object owners and app-role ACLs"*) and which I cut from T5's small scope as "not
needed to answer *can we restore?*". It **was** the answer. Had the mini died this week and been restored onto
new hardware, both failures would have hit with no documented answer — the dump was always fine, the
**procedure** was missing.

⚠️ **`scripts/restore-rehearsal.sh` as merged in #180 CANNOT SUCCEED against any real dump** — it restores into
`-d postgres` without `--no-owner --no-acl`, and discards `pg_restore`'s stderr so the reason is invisible.
Fix required: `createdb` + the two flags + surface stderr + a multi-arch digest (the current pin is amd64 and
runs under emulation on arm64).

**Fix implemented (2026-08-18), branch `fix/plan-162-t5-restore-path`, committed locally, not yet
merged/deployed — NOT yet re-run against the real mac-mini artifact; that live run is the acceptance bar for
this fix.** `createdb`s a fresh database and restores into it with `--no-owner --no-acl`; `pg_restore`'s and
`createdb`'s stderr are now captured and surfaced in the `FAIL:` message (previously discarded via
`>/dev/null 2>&1`); the container is launched with `--network none` (holds a fully-restored dump, including
`access_tokens` across every tenant — nothing needs container-initiated network access). An intermediate round
re-pinned the image to `imresamu/postgis:16-3.4@sha256:6da75969...`, a genuine multi-arch manifest
(linux/amd64 + linux/arm64), because the `docker-compose.yml`/`ci.yml` `postgis/postgis:16-3.4` pin is
single-platform amd64 for every `16-3.4*` tag that vendor has ever published — the mac-mini runs it under
emulation. **The owner reverted that swap**: the image pin is back to the same `postgis/postgis:16-3.4@sha256:
44126d872...` digest as compose/CI, accepting the emulation cost rather than adding a second, less-audited
vendor namespace for a container that holds every tenant's access-token hashes (`RESTORE_IMAGE` still overrides
for anyone who wants native arm64 locally). See `docs/standards/security.md` "Image pinning" for the full
rationale. Locking tests in `tests/unit/ops/test_restore_rehearsal.py` cover fresh-database targeting (name
equality across `createdb`/`pg_restore`/content queries, not just "some createdb happened"), exact
`--no-owner`/`--no-acl` argv tokens, stderr surfacing for both `createdb` and `pg_restore`, and `--network none`
— proven red against the buggy/absent behaviour and green against the fix.

**Second fixer round (2026-08-18, same branch), an independent Codex pass over that diff found three more
gaps, all fixed:** (1) **blocker** — the post-restore sequence-collision check queried
`access_tokens_id_seq`, but migration 0047 gives `access_tokens.id` a UUID primary key, so that sequence
never exists; the real query would have failed after every genuinely successful restore, including the
verified 1.1 GB mac-mini run above. Retargeted to `audit_log_id_seq`/`audit_log` (migration 0045's real
BIGINT `autoincrement=True` column). (2) **major** — the fresh-database locking test treated "the last
`createdb` argv token" as the database name, which a broken invocation like
`createdb rehearsal --maintenance-db template1` (creates/restores into `template1`) would still have
satisfied; `createdb` now calls with an explicit `--` end-of-options marker, and the test asserts the
database name is the sole positional token after it. (3) **minor** — the `--network none` test checked
token membership only, not position, so `docker run IMAGE --network none` (which Docker parses as the
container's *command*, not a `run` option) would have passed; the test now also asserts `--network none`
precedes the image argument. Items (1) and (2) proven red against the pre-fix script itself and green
after. **Correction (fourth round, below): item (3) was not, in fact, proven red against the pre-fix
production script** — that script already built `docker run -d --network none --name ... IMAGE` in the
correct order, so a position assertion could only pass against it. It was proven red via a deliberately
misordered/duplicated invocation constructed for the test (a mutation test), not against the actual
pre-fix production script — the record above overstated that.

**Third fixer round (2026-08-18, same branch):** the second round's retarget to `audit_log_id_seq`/`audit_log`
was itself replaced with **`pipeline_health_id_seq`/`pipeline_health`** — the owner's call: exactly two tables
in this schema use `BIGINT ... autoincrement=True` (`audit_log`, migration 0045; `pipeline_health`, migration
0001 — independently corroborated by `docker/bootstrap-roles.sql:139`, "the BIGSERIAL-keyed tables (audit_log,
pipeline_health)"), and `pipeline_health` is guaranteed non-empty in any real dump because the forecast/BAFU
flows write health records continuously, whereas `audit_log` may be sparse. Also added: a guard that asserts
the sequence relation **exists** (`to_regclass('pipeline_health_id_seq')` non-null) before querying it, so a
missing relation fails loudly and names itself rather than surfacing as some other query error — the same
class of bug (a check that silently targets a relation that has never existed) must not be able to recur
undetected. The `--` end-of-options `createdb` test and the `--network none` position test from the second
round needed no further change; both already satisfy this round's requirements. All new/changed locking tests
proven red against the pre-fix script and green after; doc correction: the T5 build spec's "`--network none`
is not required" sentence (§ "T5 — rehearse the restore", step 1) was wrong and is corrected above — this
container holds a fully-restored production dump including every tenant's access-token hashes, so egress
isolation IS required.

**Fourth fixer round (2026-08-18, same branch), an independent Codex pass over the third round's diff found
four further gaps, all fixed:** (1) **major** — the sequence-collision predicate in (n) above only ever
checked the `is_called=false` branch (`last_value == MAX(id)`); when `is_called=true`, `nextval()` has
already handed out `last_value`, so the *next* call emits `last_value + 1`, which independently colliding
with `MAX(id)` was never checked. The script now derives the actual next-emitted value from `is_called`
first. (2) **major** — the test fake's `to_regclass` case matched ANY relation query
(`*"to_regclass"*`), not specifically `pipeline_health_id_seq`, so a regression that reverted the real
guard to query `access_tokens_id_seq` (while leaving the `pipeline_health` error text untouched) would
still have passed every test — the exact vacuous-fake pattern this round's own existence guard was added
to stop recurring, just moved one query over. The fake now matches the exact query text; anything else
falls through to its "unrecognised exec" branch and fails the run. (3) **major** — the fresh-database
test asserted only the sole positional token after `--` in the `createdb` argv, ignoring stray positional
tokens before it (`createdb -U postgres other -- rehearsal` would have passed); the test now pins the
full expected argv exactly. (4) **minor** — the `--network none` position test derived the image's index
as "the last token in argv", so a doubled-image invocation (`docker run IMAGE --network none IMAGE`,
which Docker actually runs with no isolation at all — the same failure mode the second-round item (3)
test above targets, just via a different, unguarded derivation) would have passed; the test now requires
the sentinel image to appear exactly once and uses its real index. Proof: (1) is a real bug in the
actually-committed third-round script — proven red by stashing this round's script fix (keeping the new
test) and confirming the `is_called=true` collision test fails against the unmodified pre-fix script,
then restoring the fix. (2), (3), and (4) are test-rigor tightenings, not bugs the committed script ever
shipped — each proven red by constructing a deliberately regressed copy of the script/invocation the
new, tighter assertion is meant to catch (a query against `access_tokens_id_seq`; a stray `createdb`
positional before `--`; a doubled `docker run` image token) and confirming the new test fails against
that copy, then confirming the real (unmodified) script/tests still pass.

**Outstanding (not resolved by this round): the mandatory live acceptance run of the exact committed
script against the real mac-mini artifact has still not been re-performed since the fourth-round fixes.**
This fixer round ran in a sandboxed environment with no network path to the mac-mini (`ssh
sapphire@192.168.1.136` times out from here) and no copy of the 1.1 GB dump, so it could not execute
`scripts/restore-rehearsal.sh` against real data — doing so, and recording
`access_tokens=1 forecasts=206 observations=83292 alembic=0048`, remains the acceptance bar before this
branch merges, per hold-at-PR. The "VERIFIED RESTORE PROCEDURE" section above recorded those exact numbers
from a *manual* run of the restore procedure (`createdb` + `--no-owner --no-acl` by hand), not from an
automated run of this specific script revision — the two are the same procedure but not the same
artifact-under-test, which is why the automated run is still owed.

**Minor, accepted as-is:** commit `d6cf850` on this branch predates its version bump (the message
self-discloses this as an interrupted-session checkpoint); the immediately following commit brought the
version current, so branch HEAD is not out of sync, but the individual checkpoint commit does not itself
carry a bump. Left as documented history rather than rewritten via interactive rebase (out of reach in
this environment); squash before merge if the team wants every individual commit to carry its own bump.

## Status

> **Status vocabulary note (2026-08-18):** this briefly read `PARTIAL`, which is **not** a recognised status
> in this repo (DRAFT → READY → COMPLETE) and caused `/implement`'s preflight to refuse the plan. What is
> done versus open is stated in prose below; the status line stays READY while any task remains buildable.
**PARTIAL — Phase A merged 2026-08-16 as PR #161 (`a9239b6a`). NOT yet deployed.**
⛔ **Phase B is a hard gate before any customer release**: dumps still sit on the same device as the database,
unencrypted (FileVault off), containing `access_tokens` and `tenant_id`.

**READY** (2026-08-15) — **build scope is PHASE A only** (T1-T4 + the backup component; see "Phase A — BUILD
NOW"). Phase B/C specs are retained below but explicitly **not** in this build.
Reviewed three times: two full rounds on the whole plan plus a focused pass on the narrowed Phase A scope; the
last round's blockers were coherence, not design. Operational reliability (category **A**). **Backups are
live-broken**: the last successful
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

### T5 — rehearse the restore — ⚠️ RE-SCOPED SMALL 2026-08-18 (first build was 504 lines and unsafe)

**The first attempt produced 504 script + 989 test lines and stalled on 2 blockers.** Both came from machinery
this task does not need, and **my own spec caused them** by asking for a "disposable container **on a throwaway
volume**":

- a **named volume** is silently reused by `docker volume create` if it already exists, and ownership was only
  validated at teardown — so a mistaken argument could have started Postgres **against a real volume**, on the
  machine holding our only good backups;
- the same volume produced the teardown/interrupt race and most of the majors.

**Rebuild without a named volume at all.** That deletes both blockers by construction rather than guarding
against them.

**Target: well under 150 lines.**

**`scripts/restore-rehearsal.sh <dump-path>`**
1. `docker run -d` a Postgres **pinned by `tag@sha256` digest** (repo policy, `docs/standards/security.md:554`),
   **no named volume** — ephemeral container filesystem only — and **`--network none`**: this container holds a
   fully-restored production dump, including every tenant's `access_tokens` hashes, so egress isolation IS
   required, even though every *intended* interaction runs *inside* the container via `docker exec`. (An
   earlier draft of this spec said `--network none` was "not required because nothing needs to reach it" —
   that was my error; it closes an exfiltration path, not a reachability need.)
2. **Wait for the FINAL server, not the temporary one.** Postgres's official entrypoint starts a temp server,
   runs init scripts, stops it, then starts the real one — so `pg_isready` alone can return true against a
   server that is about to disappear mid-restore. Wait for the initialisation-complete marker **and** require a
   successful SQL query.
3. `docker cp` **only the selected dump file** into the container (never bind-mount its directory).
4. `pg_restore --single-transaction --exit-on-error` into an empty database.
5. **Assert on CONTENT:** row counts for representative tables **including `access_tokens`** (a restore silently
   lacking it MUST fail), `alembic_version` present and non-empty, and for sequences read **both `last_value`
   and `is_called`** — `last_value == MAX(id)` with `is_called = false` passes a naive check but the next
   `nextval()` collides.
6. `docker rm -f` in a **trap**, and mark the container as "may exist" **before** the `docker run` returns, so a
   signal arriving between creation and the flag assignment cannot skip cleanup.
7. Exit non-zero naming **which** assertion failed. Print the final `PASS` **only after** teardown succeeds, so
   an operator can never see `PASS` and a cleanup failure together.

**CUT from the first build — do not reintroduce:** named volumes and their ownership labels, network isolation
plumbing, and the **source-database comparison** (`SOURCE_PG*`). That last one is not merely extra: it is
**unusable in the real topology** — the restore container cannot reach production Postgres, which is
backend-only — and semantically wrong for a historical dump, since it compares against a live source that has
moved on. Assert plausibility plus `alembic_version` instead.

**Acceptance — run it for real:** against the live artifact on the mac-mini
(`sapphire_20260818_081448_40ee7bc2.dump`, 1.1 GB, the first good backup since 13 August). **Do not run the
504-line version there** — its volume handling is exactly the hazard described above.

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
