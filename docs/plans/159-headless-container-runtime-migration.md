---
status: DRAFT
created: 2026-08-12
plan: 159
title: Headless container-runtime migration — move the engine off Docker Desktop so no GUI session is required
scope: Replace Docker Desktop with a headless, launchd-supervised container runtime (Colima recommended) on the mac-mini, including the full volume/database migration, application-image staging, and a defined rollback boundary. This is the step that actually removes the GUI-session dependency; Plan 158 delivers the detection and host-job independence it depends on. Split out of Plan 158 on 2026-08-12 after review showed the migration needs real engineering rather than a phase.
depends_on: [158]
blocks: []
supersedes: []
---

# Plan 159 — Headless container-runtime migration

## Status
**DRAFT.** Operational reliability (category **A**). Split from Plan 158 after a `/plan` review produced 3 blockers
and 12 majors against the migration phase — the objections were sound, and the work is a project rather than a phase.

**Relationship to Plan 158.** 158 makes the outage *visible* (alerting, watchdog and stack-starter in the system
domain) and survivable across reboots. It does **not** remove the dependency: with Docker Desktop still the engine, a
logout still darkens the feed. **159 is the fix; 158 is what makes attempting 159 safe** — migrating while alerting is
blind would repeat the original mistake.

## Problem

The engine is a GUI application. When the `sapphire` login session ended on 2026-07-29 the Docker Desktop engine went
with it, and nothing could restart it remotely: `open -a Docker` over SSH fails with `OSLaunchdErrorDomain Code=125`,
because macOS will not launch a GUI app into an Aqua session from the SSH launchd domain. The feed was dark for 14
days.

After Plan 158, the stack starter and watchdog live in the system domain — but `start-sapphire.sh` can only **wait**
for an engine it cannot start. Until the engine itself is launchd-owned, the system is one logout away from another
outage that only a human at the keyboard can end.

## Design

- **D1 — Headless, launchd-supervised runtime.** The mini is macOS 26.5.1 / **arm64**, so a Linux VM is required
  either way; the only question is whether launchd or a GUI app owns it. **Recommended: Colima** (Lima + `vz`).
  Not currently installed. Alternatives in Open items (D14).
- **D2 — The supervisor plist, specified.** "LaunchDaemon + `KeepAlive`" alone supervises nothing: `colima start`
  without `--foreground` daemonises and returns 0, so launchd sees a short-lived process and either throttle-loops or
  never notices the VM dying. Locked shape:
  - `/Library/LaunchDaemons/ch.hydrosolutions.colima.plist`, `root:wheel` 644, bootstrapped into `system`.
  - `UserName=sapphire`, `GroupName=staff` (the VM, its disk and its socket live under `/Users/sapphire`).
  - `ProgramArguments`: `<colima-path> start --foreground --profile default` — **foreground is mandatory**; it makes
    the launchd process's lifetime equal the VM's lifetime, so `KeepAlive=true` genuinely restarts a dead engine.
  - `EnvironmentVariables`: `HOME`, `COLIMA_HOME`, `DOCKER_CONFIG`, and an explicit `PATH` including the Homebrew
    prefix — daemons inherit essentially nothing.
  - `RunAtLoad=true`, `KeepAlive=true`, `ThrottleInterval=30`, logs to `/Users/sapphire/Library/Logs/colima.log`
    with a `newsyslog` conf alongside the existing watchdog one.
  - **Clean stop** = `launchctl bootout system/ch.hydrosolutions.colima`, which must stop the VM rather than orphan
    it; a hand-run `colima stop` is treated as a *fault* and must be restarted by launchd.
  - **Ordering:** launchd has no `depends_on`; `start-sapphire.sh`'s existing 240 s `docker info` wait remains the
    ordering mechanism (its comment must stop naming Docker Desktop).
- **D3 — The VM must be explicitly provisioned; defaults are not survivable.** `prefect-worker` carries
  `mem_limit: 8g` (`docker-compose.yml:91`) and a RAM-backed 4 GiB tmpfs at `/tmp/sapphire_nwp` (`:135-138`);
  `docker system df` reports **136.2 GB** of local volumes. Stock Colima defaults hold neither.
  **Sizing rationale, corrected** *(reviewer major-fix)*: the tmpfs lives **inside** the worker's own cgroup, so its
  pages count toward the same 8 GiB limit — it is **not** additive, and neither figure is a reservation. "8 + 4" does
  **not** establish 16 GiB. The defensible basis is empirical: the workload has been running successfully against a
  Docker Desktop VM of **~15.84 GiB** (`docker-compose.yml:84-90`), so **≥ 16 GiB reproduces a known-good envelope**
  rather than deriving a new one. Likewise `--disk` ≥ 2 × measured volume total is **migration headroom** (source and
  target coexist), not a growth model; growth retention is out of scope here. Before any data moves, pin an
  operator-audited profile: `--vm-type vz`, `--arch aarch64`, `--runtime docker`, explicit `--cpu`, `--memory`
  **≥ 16 GiB**, `--disk` ≥ 2 × measured volume total (floor **300 GiB**), explicit `--mount` for every host path.
  **Acceptance verifies *effective* values** (`colima status`/`list`) plus a representative NWP-on forecast run
  completing without a cgroup kill.
- **D4 — Application-image staging is a first-class step, not a side effect** *(reviewer blocker-fix)*. Compose pins
  `image: sapphire-flow:${VERSION}` alongside a `build:` section (`docker-compose.yml:81-82,150-151,195-196,286-287`),
  while `start-sapphire.sh` runs `up -d` **without `--build`** and never exports `RECAP_DG_CLIENT_TOKEN`. On an empty
  engine the image is absent, so the boot path would attempt a build without the required BuildKit secret and fail —
  **the cutover would not start the stack**. Therefore, before any downtime:
  - Transfer the exact running image between engines (`docker save` → `docker load`), **or** build it on Colima with
    the private token explicitly exported. Recording which was used is part of the evidence.
  - Preload every pinned third-party image (postgis, prefect, caddy — all digest-pinned in compose).
  - **Gate:** the image on Colima is the **same image the production stack is running**, verified by comparing image
    **ID/digest** against the running Desktop container's — `docker image inspect` on a *tag* proves only that
    something holds that tag *(reviewer major-fix)*. Record where `${VERSION}` came from; note `start-sapphire.sh`
    does **not** set it (`scripts/launchd/start-sapphire.sh:24`) while Compose requires it (`docker-compose.yml:82`).
- **D10 — Every dual-engine command names its endpoint explicitly** *(reviewer major-fix)*. Desktop and Colima
  coexist through staging, rehearsal and cutover, and Colima creates and activates its own Docker context. A silent
  context switch can dump the empty target, load an image back into Desktop, or copy the wrong volumes — all exiting
  **successfully**. Every command in the runbooks carries an explicit `--context` (or `DOCKER_HOST`) for source and
  target, and each migration step re-asserts which engine it is addressing before acting.
- **D5 — Volume and database migration.** The runtime owns nine named volumes (`docker-compose.yml:347-356`), but
  **the mac-mini overlay replaces `backups` with a host bind** (`/Volumes/sapphire-backup/pg_dumps:/data/backups`,
  `docker-compose.macmini.yml:26-29`), so the effective named-volume set for this deployment is **eight** *(reviewer
  major-fix)*. There are also **two** databases in one server — `sapphire` (created by Compose, `docker-compose.yml:23`) **and** `prefect`
  (`docker/init-db.sh:4-7`) — while the backup flow dumps **only** `sapphire`
  (`src/sapphire_flow/flows/backup.py:53-68`, which dumps whatever `DATABASE_URL` names — the worker's targets `sapphire`, `docker-compose.yml:99`), so restoring `sapphire_*.dump` alone silently abandons Prefect's
  entire history.

  | Volume | Contents | Disposition | Evidence |
  |---|---|---|---|
  | `pgdata` | `sapphire` **and** `prefect` DBs | **Migrate both** (per-DB `pg_dump --format=custom` + `pg_dumpall --globals-only`) | per-DB row counts, Alembic revision equality, `\du` role+grant diff, Prefect deployment/schedule counts |
  | `model_artifacts` | Trained binaries — **irreplaceable** | **Copy byte-for-byte** | file count + per-file SHA-256 vs DB metadata |
  | `nwp_grids` | NWP Zarr hot tier | **Copy** | object count + bytes; spot-read one store |
  | `bafu_forecast_archive` | Permanent, forward-only | **Copy** | file count + checksums |
  | `bafu_observation_archive` | Permanent, forward-only | **Copy** | file count + checksums |
  | `prefect_data` | Prefect server local state | **Inspect first, then decide** — `docs/handover/it-operations.md:93` calls it reconstructible and durable orchestration state lives in the `prefect` DB; do not expand the checksum matrix on an assumption *(reviewer minor-fix)* | contents listing + the disposition decision recorded |
  | `caddy_data` / `caddy_config` | ACME material / autosave | **Rebuild allowed** (LAN-only, re-issuable; config in-repo) | note in cutover log |

  **Host binds are verified separately**, not migrated: the repo, `/Users/sapphire/camels-ch`, and
  `/Volumes/sapphire-backup/pg_dumps` must each be readable/writable from Colima before cutover.
- **D6 — Postgres restore, specified** *(reviewer major-fix)*. A fresh target already has the bootstrap owner and the
  `prefect` database, so a blind `pg_dumpall --globals-only` restore collides with existing roles. Pin: restore order
  (globals → per-DB), how pre-existing bootstrap roles/databases are reconciled, `ON_ERROR_STOP=1`, ownership/ACL
  error policy, and a final idempotent `bootstrap-roles.sh` convergence with role/grant tests. The globals output
  contains **credential verifiers**. "Securely delete afterwards" is **not achievable on APFS/SSD** (copy-on-write,
  snapshots, wear levelling) *(reviewer major-fix)*, so the plan does not promise it: prefer **never persisting
  plaintext** — stream globals through a pipe directly into the target `psql`, or stage it on an encrypted ephemeral
  volume — and if a file is unavoidable, create it under `umask 077` and treat its disk residue as a known,
  documented exposure rather than a solved problem.
- **D7 — The rollback boundary, stated honestly** *(reviewer blocker-fix)*. "Keep the Desktop VM intact" is **not** a
  rollback plan once workers write to Colima: new observations, forecasts, Prefect state and archives would exist
  only on the new engine, and re-pointing at Desktop silently discards them. Choose and record one:
  - **(a) No-loss:** all writers stay stopped until cutover is accepted; rollback is a clean endpoint flip. Longer
    downtime, zero data loss. **Recommended.**
  **The point of no return (PONR) is a specific moment, not a mood** *(reviewer blocker-fix)*: it is **the first
  write by any worker to the Colima Postgres**. Option (a) is only coherent if every acceptance check performed
  *before* that moment is **read-only** — which is why T4 splits its verification in two. The write-bearing checks (a
  fresh observation, a forecast cycle) **are themselves past the PONR**: once they run, Desktop is stale and rollback
  is no longer lossless. The first draft demanded those checks *before* acceptance while promising a clean flip,
  which is not satisfiable. Under (a), rollback after the PONR means accepting the loss of everything written since
  it, or reverse-migrating.
  - **(b) Bounded-loss:** writers start immediately; rollback requires reverse-migrating both DBs and every changed
    volume. Faster, but rollback becomes a migration in itself.
  Either way the plan must name **the point of no return** and, if (b), an owner-approved maximum data-loss window.
- **D8 — Colima installation has an owner and a pinned version** *(reviewer major-fix)*. `/opt/homebrew` belongs to a
  different admin account and is **not writable by `sapphire`**, so installation is an ⚠️ admin task, not something
  the migration can assume. Pin Colima **and** Lima versions (foreground/signal behaviour is version-sensitive),
  record checksum/package evidence, verify the binary is arm64 and executable by `sapphire`, and record who owns
  future upgrades.
- **D9 — Retire the transitional shims.** After the agreed soak, disable auto-login and Docker-Desktop-at-login
  (Plan 158 T6), then repeat the no-GUI reboot acceptance. Leaving auto-login enabled once the runtime is headless
  keeps 158's physical-access trade-off forever for no benefit.

## Phases

> **Where the commands live** — as in Plan 158: this document owns decisions and acceptance; **each host task
> delivers a runbook in `docs/operations/`** with the exact commands, in order, each naming its source/target engine
> explicitly (D10). The migration especially cannot be executed from prose: `docs/operations/colima-cutover-runbook.md`
> must pin engine selection, Compose-prefixed volume names, destination volume creation, ownership/mode/symlink
> preservation, manifest generation, the per-database dump/restore invocations with `ON_ERROR_STOP=1`, and the
> evidence comparisons. A task is not complete until its runbook exists and has been walked once in rehearsal.

- **T1 — Runtime provisioned, empty (D1/D2/D3/D8).** Admin installs pinned Colima+Lima. Record `sysctl hw.memsize`
  and `df -h` **first** — the memory read is a go/no-go gate (D13). Commit the profile and
  `ch.hydrosolutions.colima.plist`; install via Plan 158's system-domain installer; bring the VM up empty.
  **Verify:** effective CPU/memory/disk/vm-type via `colima status` · `docker info` reaches Colima ·
  `sudo launchctl kill SIGKILL system/ch.hydrosolutions.colima` (which tests only the **supervisor link** — SIGKILL
  prevents cleanup, so the VM may survive or orphan; a passing `docker info` afterwards does **not** by itself prove
  launchd recovered a *dead engine*, reviewer major-fix) · a **separate VM-layer failure test** — kill the Lima VM /
  host-agent process directly and confirm launchd brings the engine back · and a hand-run `colima stop` — each return the VM within
  `ThrottleInterval` + boot · **`launchctl bootout system/ch.hydrosolutions.colima` leaves no orphaned VM, socket or
  port-forwarder**, then bootstraps cleanly again *(reviewer major-fix — the shutdown path was previously untested)* ·
  a reboot with **no GUI login** leaves it running.
  *Rollback trigger:* effective memory < 16 GiB, or the VM does not return.

- **T2 — Image staging (D4).** Transfer or build `sapphire-flow:${VERSION}` on Colima and preload the pinned
  third-party images. **Verify:** `docker image inspect` succeeds for every image compose references; record image
  IDs/digests on both engines. **No data has moved at this point.**

- **T3 — Migration rehearsal (D5/D6). No cutover.** Against the empty-but-imaged Colima VM, walk the full matrix
  once: quiesce writers (workers → server → api, postgres last), assert **zero active Prefect runs and no
  non-administrative DB writer sessions** before the final dumps *(reviewer major-fix)*, dump both DBs + globals, copy
  every retained volume with a checksum manifest, restore per D6, run the verification column. **Time it** — this
  measurement is what the downtime window is agreed against. **Then explicitly restart the Desktop stack and verify
  it healthy** *(reviewer major-fix — the first draft quiesced production and never said to restart it)*.
  *Rollback:* trivial; nothing was cut over.

- **T4 — Cutover (D5/D6/D7).**
  **T4.0 — RESET THE TARGET FIRST** *(reviewer blocker-fix)*. T3 restored real data into Colima, so
  "empty-but-imaged" is **only true before T3**. Restoring again over a populated target collides on primary keys,
  duplicates rows, or leaves rehearsal-only files in the copied archives. T4 therefore **begins** by destroying and
  recreating the Colima data state — drop/recreate both databases and remove every migrated volume — and **verifies
  emptiness** (zero rows in a named state-bearing table per DB; volume list empty) **before** any restore. The images
  from T2 are retained; only data is reset.
  Then re-run the migration for real inside the agreed window, re-point the endpoint contract (Plan 158 T4) at
  Colima, start the stack, re-verify.
  **Verification is split by the point of no return (D7):**
  - **Before the PONR — read-only:** the T3 evidence set reproduced (row counts, Alembic revisions, `\du` grants,
    Prefect deployment/schedule counts, `model_artifacts` SHA-256 set), plus the API health endpoint asserted on
    parsed JSON `status == "ok"` — **not** merely a 2xx from `curl -fsS`. A failure here is still a clean rollback.
  - **After the PONR — write-bearing:** a fresh `observations` row (asserted against a baseline timestamp captured
    pre-start, with an explicit timeout), a representative NWP-on forecast cycle completing with no cgroup kill
    (proves D3's sizing), and `prune-docker.sh` reaching the Colima daemon **under launchd** — asserted on its
    "stack is running" and terminal "done" log lines, **not** exit status 0 (the script deliberately exits 0 when the
    daemon is unreachable, `scripts/launchd/prune-docker.sh:38`).
  **Deliverable:** `docs/operations/colima-cutover-runbook.md` — the exact commands, in order, with the explicit
  source/target context on every dual-engine invocation (D10).

- **T5 — Prove independence and retire the shims (D9).** Disable auto-login and Docker-Desktop-at-login; optionally
  uninstall Docker Desktop after the soak.
  **Acceptance — the assertion this plan exists for:** with **no GUI session logged in at all** (`who` shows no
  console session), the stack runs, survives a reboot, and recovers from `colima stop`, with a fresh `observations`
  row after each.

## Phase dependency graph
```json
{
  "phases": [
    { "id": "T1", "tasks": ["T1"], "parallel": false },
    { "id": "T2", "tasks": ["T2"], "parallel": false, "depends_on": ["T1"] },
    { "id": "T3", "tasks": ["T3"], "parallel": false, "depends_on": ["T2"] },
    { "id": "T4", "tasks": ["T4"], "parallel": false, "depends_on": ["T3"] },
    { "id": "T5", "tasks": ["T5"], "parallel": false, "depends_on": ["T4"] }
  ]
}
```
Strictly sequential, and **the whole chain depends on Plan 158's T5** (stack starter in the system domain): T5's "no
GUI session at all" is undemonstrable while `ch.hydrosolutions.sapphire` lives in `gui/$(id -u)`, because the starter
that runs `docker compose up -d` will not launch without a session regardless of what the engine layer does.

## Non-goals
- **Plan 158's scope** — alerting, watchdog/starter domain conversion, auto-login. Prerequisites, not this plan.
- **Migrating off macOS.** A Linux host removes this class of problem entirely and remains the honest long-term
  answer; recorded as an alternative in D14, not attempted here.
- **The backup-target relocation** (`/Volumes/sapphire-backup` is a folder on the internal disk, not the USB) and the
  **3.7 TB vs 288 GB disk question** — both real, both separate.

## Open items
- **D13-host-RAM — ⚠️ GO/NO-GO, read before anything else.** If `sysctl hw.memsize` shows only 16 GiB physical, a
  ≥ 16 GiB VM is impossible and either `mem_limit`/tmpfs must be re-sized or the runtime choice changes.
- **D14-runtime-choice — ⚠️ OWNER DECISION.** Colima under launchd (recommended) vs staying on Docker Desktop with
  auto-login (no migration, but the dependency and a logout-darkening remain) vs a Linux host (largest change,
  removes the class).
- **D15-downtime-window — ⚠️ OWNER DECISION**, agreed against T3's **measured** rehearsal, not a guess.
- **D16-rollback-boundary — ⚠️ OWNER DECISION.** D7 option (a) no-loss or (b) bounded-loss, and if (b), the maximum
  acceptable data-loss window.
- **D17-colima-upgrade-ownership — ⚠️ OWNER.** Who owns pinned-version upgrades, given `/opt/homebrew` is another
  admin's.
