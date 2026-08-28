---
status: DRAFT
created: 2026-08-28
plan: 208
title: Backups must leave the box — the off-box sink Plan 162 deferred
scope: The follow-on Plan 162 D4 named but never created — replicate the validated dump to a configurable off-box sink (filesystem path or S3-compatible endpoint), verify it actually landed, and monitor that it keeps landing. Encryption at publication (162 D5, Phase B, never shipped) is a PREREQUISITE, not a companion. No PITR, no continuous archiving, no prefect-database backup, no AWS migration work beyond this sink.
depends_on: [162]
blocks: []
source: docs/plans/162-robust-database-backup.md § D4 + "Owner context 2026-08-18"; owner decision 2026-08-28 (AWS, S3)
---

# Plan 208 — backups must leave the box

## Status

**DRAFT.** Not for implementation until the owner confirms.

## Owner context, 2026-08-28

- **No separate backup drive for the mac mini, now or later.** Proper backup separation arrives with
  the **AWS deployment**, expected in a few weeks. This is a closed decision — the mini stays
  same-disk knowingly, and "attach a disk" is not an outstanding errand.
- **On AWS the sink is S3.** (162 D4 also allows Cloudflare R2; S3 is the chosen target.)

## ⛔ PROPORTIONALITY IS A BINDING CONSTRAINT ON THIS PLAN AND ON ITS REVIEW

**This is one publication step, one verification predicate per sink shape, and one monitor
condition.** The dump already exists, is already validated, and is already published atomically
(162 T3). This plan moves a file and proves it arrived. It needs no new service, no backup framework,
no vendor abstraction beyond the two sink shapes 162 D4 already named, and no change to what is
dumped or how often.

**Rules for reviewers** (carried from Plans 194/195, which they served well):

1. **"No findings" is a complete and welcome review.**
2. **A finding must name a CONCRETE FAILURE** — data that would be lost, an alert that would fire
   wrongly, a call site that would break.
3. **A missing specification is NOT a finding** unless leaving it unsaid produces wrong behaviour.
4. **Do not propose new apparatus.** No pluggable-provider registry, no generalised "storage
   backend" layer, no retention/lifecycle engine.
5. **Adding length is a cost.** Prefer deleting to adding.

## ⚠️ The premise that justified deferring this is currently FALSE

Plan 162 split the off-box destination into a follow-on with this reasoning, in its own scope line:

> *"the off-box destination (D4) and the replication task are split into a follow-on, **since an
> encrypted artifact is safe wherever it lands**."*

**The artifact is not encrypted.** Verified 2026-08-28:

- `grep -rl encrypt src/sapphire_flow/` → **nothing**. There is no encryption code in the repo.
- On the mini, `file` on the newest dump (`sapphire_20260828_020000_407f99da.dump`) reports
  **`PostgreSQL custom database dump - v1.15-0`** — plaintext.
- 162 D5 decided `age` encryption at publication, but scoped it **Phase B only**
  (`docs/plans/162-robust-database-backup.md:342`: *"in Phase A the published name is
  `sapphire_*.dump`, plaintext"*). Phase A shipped; Phase B did not.

Dumps contain `access_tokens` and `tenant_id` (162:216). Replicating today's artifact to S3 would put
that in object storage in the clear.

**Therefore encryption is a PREREQUISITE of this plan, not a parallel nicety.** Either 162's D5
lands first as its own task, or T1 below carries it. Do not ship replication without it.

*(Note: `docs/plans/archive/194-...md`'s non-goals list read "backup encryption (162 T3, shipped)".
That conflated two things — 162 **T3** is *atomic publication*, which did ship; encryption is **D5**,
Phase B, which did not. Corrected alongside this plan, because a plan that records a safety property
as already shipped is how it stops being built.)*

## ⚠️ Plan 194's predicate does NOT port to either AWS shape

Plan 194 (shipped) answers *"is the backup target the device it claims to be?"* by comparing `stat`
device ids and requiring the mount root be a real mount. That predicate is correct for the mac mini
and **meaningless or misleading on AWS**:

- **On S3 there is no device.** No `st_dev`, no mount, nothing for the predicate to compare. It
  cannot be ported; it must be replaced by a different question.
- **On EBS it would pass trivially and still be wrong.** A separately-mounted EBS volume has a
  different device id and is a real mount, so the predicate returns *verified* — while the volume
  still shares an **availability zone**, and often an instance, with the database it protects. The
  predicate would report separation that does not exist. That is a worse failure than no predicate,
  because it is a green light.

**The question generalises differently per sink shape:**

| Sink | The real question | Evidence available |
|---|---|---|
| Filesystem (DHM second drive / network mount) | is it a different device, really mounted? | Plan 194's predicate, unchanged |
| S3 / R2 | did the object actually land, intact? | HEAD the object: exists, expected size, checksum matches |

Separation on S3 is **architectural, not probeable** — S3 is off-instance by construction, so there
is nothing to verify about separation and everything to verify about *arrival*. Do not build a
predicate that pretends otherwise.

## Decisions to make (the grill-me)

These are genuine forks the owner should settle before this goes READY:

- **D1 — does the mini replicate at all?** It has no second drive and its sink would be the same
  disk. Options: (a) feature-off on the mini (no sink configured → skip, no alert), (b) the mini
  replicates to S3 too, giving real off-box backup for staging today. (b) costs an AWS credential on
  a staging host; (a) leaves staging unproven until the AWS cutover, which is exactly how Plan 194's
  guard came to be built against a condition nobody had ever seen.
- **D2 — retention off-box.** Local retention is `keep_count` (162). Does S3 mirror it, keep more, or
  defer to an S3 lifecycle policy set outside this repo? A lifecycle policy is the smaller answer.
- **D3 — credential shape on AWS.** Instance role vs static key in `secrets/`. The watchdog and
  backup worker are separate processes with different privileges today; say which holds the write
  credential, and confirm it is write-only (no delete) so a compromised host cannot erase history.
- **D4 — does a failed replication block or warn?** The dump itself already succeeded and is on local
  disk. Failing the flow would turn a replication outage into a backup outage. Warn-and-latch mirrors
  what Plans 194/195 do for every other condition, and is the likely answer.

## Sketch of the work (three tasks, pending the decisions above)

### T1 — encrypt at publication (162 D5, Phase B)
`age` encryption as part of the atomic publish, so the artifact is safe before anything moves it.
Key management is the real content here, not the encryption call — a key the restore path can reach
without a human, that is not stored beside the backups it protects.
**Red-first:** a published artifact must fail a plaintext `file`/`pg_restore --list` probe before the
encryption exists.

### T2 — replicate to a configurable sink
Two shapes only, per 162 D4: a filesystem path and an S3-compatible endpoint. Runs **after** the
existing validated publish, never in place of it — the local artifact stays authoritative.
**Red-first:** with a sink configured and the object absent, the run must report failure.

### T3 — verify arrival, and monitor that it keeps arriving
Per the table above: filesystem sinks reuse Plan 194's predicate; S3 sinks HEAD the object and
compare size + checksum against the local artifact. Then one watchdog condition, transition-latched,
in the same shape as the four already there (`backup_device` / staleness / launchd / disk).
**Red-first:** a sink that silently accepts writes and returns nothing must fail verification.

## Non-goals

PITR / continuous archiving · backing up the `prefect` database · the AWS migration itself (compute,
networking, RDS-vs-container Postgres) · S3 lifecycle/retention policy as repo code (see D2) ·
restic (Plan 048) · changing what is dumped, how often, or local retention · replacing Plan 194's
filesystem predicate, which stays correct for the DHM on-prem shape.

## Exit gates

```bash
uv run pytest tests/unit/flows/ -k backup
uv run pytest tests/unit/ops/ -k watchdog
uv run ruff format --check src/ tests/ && uv run ruff check src/ tests/
uv run pyright
```

**Doc sync:** `docs/standards/cicd.md` (backup + deployment sections) · `docs/touchpoint-maps.md`
(the backup surface) · `docs/deployment/mac-mini-staging.md` if D1 says the mini replicates ·
Plan 162 § D4 gains a pointer here (its `blocks:` is empty and should name this plan).
