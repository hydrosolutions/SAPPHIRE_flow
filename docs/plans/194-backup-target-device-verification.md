---
status: DRAFT
created: 2026-08-20
plan: 194
title: The backup target must be the device it claims to be (extracted from Plan 162 T6)
scope: Make the wrong-device condition VISIBLE at three existing call sites — bootstrap (fail closed), unattended start (record and proceed), watchdog (a distinct condition). No new subsystem, no new service, no backup-logic change, no off-box replication.
depends_on: [162]
blocks: []
source: docs/plans/162-robust-database-backup.md § T6
---

# Plan 194 — the backup target must be the device it claims to be

## Status

**DRAFT.** Not for implementation until the owner confirms.

Extracted verbatim-in-substance from **Plan 162 T6**, which is fully specified there but was cut from
Phase A and never built.

**⚠️ `/plan` was run on this doc 2026-08-20 and ESCALATED** — stalled after 4 rounds, 0 blockers,
2 residual majors, and it grew the document **134 → 321 lines** despite the binding proportionality
block below. **Codex failed in 3 of the 4 rounds**, so the required independent reviewer barely ran and
the escalation is weak evidence about the plan itself. Reconstructed here at the size the work is,
keeping the findings that survived checking and discarding the apparatus the loop invented (a
`backup_mount_root` config field + CLI flag duplicating `backup_dir.parent`, and a hand-typed
`--allow-unverified-backup-volume` opt-out).

**Do not re-run `/plan` on this plan expecting a different result — review it by hand.** This is the
third plan in this repo to hit that (see 184 and 188). It is extracted rather than built in place because 162 is a large READY plan
whose other tasks have shipped; editing that doc risks rewriting merged work.

## ⛔ PROPORTIONALITY IS A BINDING CONSTRAINT ON THIS PLAN AND ON ITS REVIEW

**Owner instruction, 2026-08-20: do not over-engineer this.** This is a standing constraint on every
reviewer and every revision, not a preference. It is stated here because the review workflow reads
**only** the plan document — per-run instructions passed as workflow arguments are silently discarded.

**This is three guard clauses and their tests.** The predicate is two shell commands. All three call
sites already exist and already run. It needs no new module, no registry, no abstraction layer, no
configuration framework, no new service, and no new file format.

**Rules for reviewers:**

1. **"No findings" is a complete and welcome review.** Do not manufacture findings to justify the pass.
2. **A finding must name a CONCRETE FAILURE** — a condition that would go undetected, an alert that
   would fire wrongly, a call site that would break. Cite the decision it breaks.
3. **A missing specification is NOT a finding** unless leaving it unsaid produces wrong behaviour.
4. **Do not propose new apparatus.** No frameworks, no plugin seams, no generalisation to "any mount".
5. **Adding length is a cost.** A revision that grows this document without removing a concrete error
   has made it worse. Prefer deleting to adding.
6. **Plan 162's T6 decisions are load-bearing and already reviewed.** Challenge them only if today's
   measured evidence contradicts them.

## Why this matters now — measured, not hypothetical

Verified on the mac mini 2026-08-20:

- `mount | grep sapphire-backup` → **nothing mounted at that path**
- `diskutil list external` → **empty; no external disk is attached at all**
- `stat -f %d` → `/Volumes/sapphire-backup` and `/Users/sapphire` report the **same device id**

So `/Volumes/sapphire-backup` is a plain directory on the boot disk, and every nightly dump (1.1 GB,
correct filename, fresh timestamp) lands on the same device as the database it protects. **The watchdog
reported healthy throughout**, because it checks freshness, not target. Docker silently creates a
missing bind-mount host path, which is how an absent disk becomes a healthy-looking directory.

**The owner has ACCEPTED this on the mini** (2026-08-20): it is a staging host and the disk will be
present for operational deployments. **That acceptance is precisely why the guard is worth building.**
Today there is no mount to lose, so the failure is merely invisible. On a host that *has* a real
volume, the same condition becomes the classic silent failure — the volume unmounts, writes land in the
mount point on the boot disk, filenames and timestamps stay correct, and the monitor stays green.

## Decisions carried from Plan 162 T6 (already reviewed there)

**Call sites, verified against `main` on 2026-08-20** (Plan 162's own citations had drifted — two were
wrong and are corrected here):

| Site | Today | Correct citation |
|---|---|---|
| `scripts/bootstrap-mac-mini.sh` | checks a hand-creatable **sentinel file**, not the device | `:92` (path), `:222-236` (check) |
| `scripts/launchd/start-sapphire.sh` | **no disk check at all** before starting the stack | `:24-27` — note it is `exec docker compose`, so any check must precede that line |
| `src/sapphire_flow/ops/watchdog.py` | freshness only; no device check | `DEFAULT_BACKUP_DIR` at **`:102`** (162 said `:56` — stale) |
| `docs/deployment/mac-mini-staging.md` | instructs `mkdir -p` + `touch` | **`:453-454`** (162 said `:396-410` — stale) |
| `docker-compose.macmini.yml` | binds the path into the backup worker | the `prefect-worker-backup` volumes entry |

- **D1 — One shared predicate, three call sites.** The backup directory's device id differs from the
  device id of **the path that actually holds the data** (`/Users/sapphire`, i.e. `REPO_ROOT` —
  **not `/`**) **and** the path is a real mount point of an attached volume (`mount` /
  `diskutil info -plist`), not merely a directory. The existing sentinel file survives **only as a
  label, never as the proof**.
  **Accepted limitation, stated rather than implied:** `stat -f %d` yields a *volume* id, not a
  *physical disk* id. A second APFS volume added to the boot container and mounted at the backup path
  would satisfy the predicate while still sharing the boot disk's single point of hardware failure.
  Detecting that needs `diskutil info -plist` parentage, which is more apparatus than this guard is
  worth; the predicate catches the failure we have actually seen (no volume at all) and is honest about
  the one it cannot.
  *(Measured 2026-08-20: on this host `stat -f %d` returns the SAME id — `16777234` — for `/`,
  `/System/Volumes/Data`, `/Users/sapphire` and `/Volumes/sapphire-backup`. A review round asserted `/`
  and `/Users` necessarily differ on post-Catalina macOS; on the target host they do not. The switch to
  the data path is for honesty about intent, not because `/` misfires here.)*
- **D2 — `bootstrap-mac-mini.sh` fails closed.** It is interactive, so blocking is safe.
- **D3 — `start-sapphire.sh` checks, records, and PROCEEDS.** Deliberately not fail-closed: refusing to
  start the stack because a removable disk is absent trades a backup outage for a *forecasting* outage,
  which is the mistake this plan exists to avoid. It writes a machine-readable marker beside the compose
  files and continues.
- **D4 — The watchdog raises a DISTINCT condition** — "backup volume not mounted; dumps would land on
  the boot disk" — with its own message and its own incident/notification state. **Never folded into
  staleness**: a mounted-but-stale volume and an unmounted volume need different operator actions, and
  the whole defect is that the second is currently invisible. Mount validity is evaluated **before**
  artifact freshness.
- **D5 — Tests.** Shell tests for the predicate (real mount vs plain directory vs missing path) and
  watchdog unit tests for the new condition, including that it neither suppresses nor duplicates the
  staleness alert.

## ⚠️ OPEN DECISION FOR THE OWNER — the one thing today's evidence adds

**On the mini, D4's condition would be TRUE forever.** There is no external disk and the owner has
accepted that, so a correct implementation alerts on every watchdog tick, indefinitely, on the one host
we run. That is alarm fatigue by construction, and it would train the operator to ignore the exact
condition the plan exists to surface.

Options, for the owner to pick — **the reviewers should not invent a sixth**:

- **(a) Declare the expectation in config.** ⛔ **REFUTED — do not choose this. It cannot work.**
  The draft recommended it; a review round disproved it with citations, and I verified them.
  `SAPPHIRE_CONFIG_OVERLAY` is set only **inside containers** (`docker-compose.macmini.yml:25`, `:33`),
  whereas the watchdog is a launchd **host** process
  (`scripts/launchd/ch.hydrosolutions.sapphire-watchdog.plist`) whose `main()` builds `WatchdogConfig`
  from **CLI arguments only** (`src/sapphire_flow/ops/watchdog.py:1397-1407`). A config flag would never
  reach the process that raises the alert. Doing it "properly" means a new host-facing CLI flag threaded
  through the plist and `scripts/launchd/watchdog.sh`, plus tests for both — more apparatus than the
  problem needs, and it encodes "this host does not back up" where nobody looks.
- **(b) Alert on transition, then latch.** Raise when the condition appears (and on recovery), not every
  tick. **Recommended** — it needs no new config surface at all, only state the watchdog already keeps,
  and it survives the mini's permanently-diskless state without training the operator to ignore it.
- **(c) Log-only on staging, alert on operational**, keyed off the config overlay. Fails for the same
  reason as (a): the host watchdog cannot read that overlay.
- **(d) Accept the permanent alert** until the disk arrives.

## Non-goals

Off-box replication (Plan 162 D4 follow-on) · backup encryption (162 T3, shipped) · restore rehearsal
(162 T5, shipped) · generalising the predicate beyond the backup path · any change to what is dumped,
how often, or its retention · fixing the mini's missing disk (accepted risk; a physical errand).

## Exit gates

```bash
uv run pre-commit run shellcheck --files scripts/bootstrap-mac-mini.sh scripts/launchd/start-sapphire.sh
shellcheck scripts/bootstrap-mac-mini.sh scripts/launchd/start-sapphire.sh
uv run pytest tests/unit/ops/ -k "watchdog or backup"
uv run ruff format --check src/ && uv run ruff check src/
uv run pyright
```

**Red-first:** the watchdog condition test must fail against current committed code (today it cannot
detect the wrong-device state at all), and the predicate test must fail for a plain directory before
the predicate exists.

**Doc sync:** `docs/deployment/mac-mini-staging.md` — replace the `mkdir -p` + `touch` sentinel
instructions at `:453-454` with the verification behaviour; `docs/touchpoint-maps.md` § Prefect /
Docker / deployment. Plan 162 T6 gains a pointer here.
