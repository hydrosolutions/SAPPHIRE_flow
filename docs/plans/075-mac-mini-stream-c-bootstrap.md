# Plan 075 — Mac Mini Stream C: glue + one-command bootstrap (audit + harden)

**Status**: READY
**Phase**: 10c (staging infrastructure)
**Parent**: Plan 046 (Mac Mini Staging Deployment), Stream C
**Roadmap**: `docs/v0-launch-roadmap.md` §1.5 (§1.5a building blocks, §1.5b one-command bootstrap)
**Created**: 2026-06-01

---

## Decisions (locked 2026-06-01)

The open questions from the DRAFT are resolved as follows:

1. **Framing = AUDIT + HARDEN, not greenfield.** Stream C is already implemented
   and green on `main` — commit `514ff36` (tag `v0.1.403`, *"feat(mac-mini): Plan
   046 Stream C — launchd glue + one-command bootstrap"*) landed every §1.5a/§1.5b
   deliverable. Current state verified locally: both `.plist` files `plutil -lint`
   OK; all shell scripts `shellcheck`-clean; `ops/watchdog.py` 0 pyright errors,
   27/27 unit tests pass; ruff clean. Plan 075 audits these artifacts against the
   §1.5 spec, reconciles the §1.5a/§1.5b docs, and adds a rot-protection gate.
   **No rewrite — and no task changes runtime behaviour** (see "Deferred" below).

2. **caddy/TLS = DROPPED from Stream C scope.** §1.5a lists "caddy TLS" but the
   committed `docker-compose.macmini.yml` has no caddy block, and the box is
   LAN-only on a trusted network (Plan 046 D1; `security.md` §Network policy —
   without `SAPPHIRE_DOMAIN`, Caddy serves plain HTTP). **T4** closes the §1.5a
   spec mismatch *on paper*: it records the LAN-only / plain-HTTP decision
   explicitly and notes that public HTTPS is owned by **Plan 049** (Cloudflare
   public URL). No caddy service block is added in this plan.

3. **Rot-protection = INCLUDED NOW.** The shell + plist artifacts have zero CI /
   pre-commit coverage today — the "wired but unrun gate" class Plan 070 set out
   to close. **T7** adds a `shellcheck` pre-commit hook + CI lint step over
   `scripts/launchd/*.sh` and `scripts/bootstrap-mac-mini.sh`, and a **macOS-only**
   `plutil -lint` hook for the two `.plist` files, wired check-only to match the
   Plan 070 pre-commit convention.

4. **Operator gate = Phase 4 hand-off.** Subagent-executable phases stop at a
   full `bootstrap-mac-mini.sh --dry-run` (T9). The live fresh-Mac-mini run and
   the §1.6 Stream D 7-day soak are **operator work on physical hardware**,
   documented in the runbook — not subagent tasks.

### Roadmap-edit investigation (decision-required item, resolved)

**Why `514ff36`'s roadmap edit didn't leave the §1.5 boxes ticked:** it was not a
later revert. `514ff36` itself **rewrote** §1.5 — `git show 514ff36 --
docs/v0-launch-roadmap.md` shows it *replaced* the old coarse 4-item checklist
(`- [ ] Write scripts/launchd/*.plist …`) with the detailed §1.5a/§1.5b
sub-lists, authoring those new boxes as unchecked `- [ ]`. The delivering commit
wrote its own boxes fresh as a *spec checklist* while shipping the code, and
never ticked them. The only two later roadmap commits (`efba810`, `642c0c2`)
touched §1.4/deps, not §1.5. So the boxes were never ticked in the first place —
**T10** ticks them now and cites `v0.1.403`. (Definitive — not "needs check".)

### Deferred

- **Watchdog backup-staleness alert dedupe** — intentionally deferred, not a
  Plan 075 task. `ops/watchdog.py:284-287` documents the trade-off (alert every
  tick on staleness; *"Dedupe-by-day would add complexity without operational
  value — revisit if alert fatigue is seen"*). That revisit-trigger has not
  fired: Slack is log-only in staging, so there is no alert fatigue to address.
  Reopen only when alert fatigue is observed on hardware with the Slack webhook
  wired. Consequently **every Plan 075 task is audit / docs / CI-config only —
  none changes runtime behaviour.**

---

## Context

Plan 046 Stream C wires the SAPPHIRE Flow Docker Compose stack into a Mac mini so
it survives reboots, self-heals, alerts on failure, and comes up from a single
command. Locked deployment parameters (roadmap §1.5, *Decisions locked
2026-04-23*):

- Hostname `sapphire-staging.local`; user `sapphire`; repo path
  `/Users/sapphire/SAPPHIRE_flow` (hard-coded in both plists, `start-sapphire.sh`,
  and the bootstrap's `EXPECTED_PATH`).
- Slack webhook **skipped at install** — the watchdog runs log-only until
  `secrets/slack_webhook_url` is populated.
- CAMELS-CH via **host bind-mount** of pre-staged `~/camels-ch/`.
- **Linux is out of scope** for v0 — `launchd` / `plutil` / Apple-Silicon checks
  are macOS-specific by design; cross-platform is a v1/Nepal follow-up.

Every task is framed as **audit the committed artifact against the §1.5 spec →
close any gap → prove it with a concrete check**. Subagents read the committed
code; this plan does not restate signatures.

**Testability invariants (CLAUDE.md):** `ops/watchdog.py` business logic keeps its
dependency-injected `clock: Callable[[], datetime]` and `structlog` events — no
`datetime.now()` or `print()` in `run_once` / `should_alert_health` / probe
helpers. The only `datetime.now(UTC)` call is the boundary helper `_utc_now()`
passed into `run_once` from `main()`. Any change pushing a real clock into
business logic is a regression and fails T3.

> **pyright note:** the repo drives strict mode through `pyrightconfig.json`
> (Plan 069), so the verification command is `uv run pyright src/sapphire_flow/ops/`
> — the `--strict` *flag* in `docs/workflow.md` §Task Exit Gate is rejected by the
> pinned pyright and must not be passed on the CLI.

---

## Phase 1 — Audit existing artifacts against §1.5a/§1.5b (parallel)

Read-and-verify each artifact family; leave behind a green regression check. A
task changes runtime behaviour only if it finds a concrete spec violation, which
it then fixes in-scope and records in its commit message.

### T1 — launchd plists

- **Scope**: confirm both plists carry `Label`, `UserName=sapphire`,
  `WorkingDirectory=/Users/sapphire/SAPPHIRE_flow`, `~/Library/Logs` log paths,
  main agent `RunAtLoad=true`+`KeepAlive{SuccessfulExit=false}`+`ThrottleInterval`,
  watchdog agent `StartInterval=300`+`RunAtLoad=false` invoking `uv run python -m
  sapphire_flow.ops.watchdog`; **out**: changing launchd cadence or label namespace.
- **Verification**: `plutil -lint scripts/launchd/ch.hydrosolutions.sapphire.plist scripts/launchd/ch.hydrosolutions.sapphire-watchdog.plist` — both report `OK`.

### T2 — launchd shell wrappers + installer

- **Scope**: confirm `start-sapphire.sh` (bounded wait-for-Docker-Desktop →
  `compose -f docker-compose.yml -f docker-compose.macmini.yml up -d`) and
  `watchdog.sh` (manual wrapper) use `set -e` (adequate for single-command
  `exec` wrappers), `install-launchd.sh` uses `set -euo pipefail` (idempotent
  `plutil -lint` → copy → `launchctl bootout`/`bootstrap`/`enable`), and all
  carry the hard-coded repo path; **out**: a configurable install prefix (Linux
  out of scope); **out**: retrofitting `-uo pipefail` onto the two exec-wrappers
  (`set -e` is correct for them).
- **Verification**: `shellcheck scripts/launchd/start-sapphire.sh scripts/launchd/watchdog.sh scripts/launchd/install-launchd.sh` — exit 0, no findings.

### T3 — watchdog module (`ops/watchdog.py`, `ops/__init__.py`)

- **Scope**: confirm the four §1.5a behaviours — health probe, backup-staleness
  check, hysteresis (`should_alert_health`: 1st failure, every 6th consecutive,
  recovery), and Slack-if-present-else-log-only — plus `structlog` usage and the
  injected `clock`; **out**: changing alert-cadence constants, the backup-staleness
  alert cadence (dedupe is deferred — see "Deferred"), or the state-file schema.
  This is a **read-only audit** — if it surfaces no spec violation, the green
  verification command is the only artefact.
- **Verification**: `uv run pytest tests/unit/ops/test_watchdog.py -q` — all pass; and `uv run pyright src/sapphire_flow/ops/` — 0 errors.

### T4 — macmini overlay audit (closes §1.5a caddy/TLS mismatch in the overlay header)

- **Scope**: confirm `docker-compose.macmini.yml` carries the USB backup
  bind-mount (`/Volumes/sapphire-backup/pg_dumps:/data/backups:rw`), CAMELS-CH
  host bind-mount (`/Users/sapphire/camels-ch:/data/raw:ro`), and the API
  host-port publish (`8000:8000`); and add a one-line **LAN-only / plain-HTTP**
  pointer to the **overlay header comment only** (no caddy TLS in staging per
  Plan 046 D1; public HTTPS owned by Plan 049); **out**: adding any caddy service
  block (dropped per Decision 2); **out**: editing `mac-mini-staging.md` — the
  runbook Network-posture note is owned solely by **T6** (no two parallel tasks
  write the same file).
- **Verification**: `VERSION=latest docker compose -f docker-compose.yml -f docker-compose.macmini.yml config -q` — parses with no error (overlay merge valid without starting containers). The base compose mandates `${VERSION:?}` and `.env` is gitignored, so the command supplies `VERSION=latest` exactly as `bootstrap-mac-mini.sh` Step 8 does — a static parse only, no containers started and no runtime/config change.

### T5 — one-command bootstrap (`scripts/bootstrap-mac-mini.sh`)

- **Scope**: confirm every §1.5b behaviour — Apple-Silicon guard; Docker-Desktop
  present-and-running detection; Homebrew/uv detect-and-install; `db_password`
  create-if-absent (`chmod 600`) + optional `slack_webhook_url` detection;
  USB-sentinel + CAMELS-CH checks; `compose … up -d`; health-poll to `status=ok`
  (≤300s); `install-launchd.sh` call; final report; and the `--dry-run` /
  `--uninstall` / `--help` flags + `ERR` trap; **out**: adding interactive prompts
  (must stay non-interactive for launchd/CI use).
- **Verification**: `bash -n scripts/bootstrap-mac-mini.sh && shellcheck scripts/bootstrap-mac-mini.sh` — syntax OK and no shellcheck findings.

### T6 — runbook (`docs/deployment/mac-mini-staging.md`)

- **Scope**: confirm the runbook covers TL;DR, §1.4b prerequisites, install
  (`git pull` → bootstrap), what the bootstrap does, LaunchAgents +
  watchdog-log-rotation (manual `sudo cp` of the newsyslog template),
  post-install verification, LAN SSH-tunnel access, troubleshooting, upgrade,
  uninstall; **sole owner of `mac-mini-staging.md`** — add the short "Network
  posture" note here (LAN-only / plain-HTTP, no caddy TLS in staging per Plan 046
  D1; public HTTPS owned by Plan 049); confirm no leaked `sapphire` password or
  internal version churn (MEMORY: handover-docs hygiene); **out**: the §D2
  rehearsal procedure (lives in Plan 046 Stream D).
- **Verification**: `uv run python -c "import pathlib,sys; t=pathlib.Path('docs/deployment/mac-mini-staging.md').read_text(); req=['bootstrap-mac-mini.sh','install-launchd','sapphire-watchdog','/api/v1/health','newsyslog']; missing=[k for k in req if k not in t]; sys.exit('MISSING: '+', '.join(missing) if missing else 0)"` — exits 0.

---

## Phase 2 — Harden: rot-protection gate (depends on Phase 1)

### T7 — shell + plist lint gate (rot-protection, Decision 3)

- **Scope**: add a `shellcheck` pre-commit hook covering `scripts/launchd/*.sh`
  and `scripts/bootstrap-mac-mini.sh`, mirror it as a CI `run:` step in the
  `lint` job of `.github/workflows/ci.yml`, and add a **macOS-only** local
  pre-commit hook running `plutil -lint` over `scripts/launchd/*.plist`. The
  plutil hook must **self-guard so it no-ops (does not fail) on Linux
  contributors' machines** — the hook entry runs `command -v plutil >/dev/null
  2>&1 || exit 0` before linting, so a missing `plutil` is a clean skip, not a
  failure. Both hooks are check-only, consistent with the Plan 070 convention
  (the self-guarded skip still satisfies check-only: it never mutates files and
  never blocks on absence of the tool). Extend the cicd.md CI-tiers table with
  the new row(s); **out**: running `plutil` in GitHub Linux CI (impossible —
  macOS tool; the hook is local-only).
- **Verification**: `uv run pre-commit run shellcheck --all-files` — passes; and `shellcheck scripts/launchd/start-sapphire.sh scripts/launchd/watchdog.sh scripts/launchd/install-launchd.sh scripts/bootstrap-mac-mini.sh` — exit 0.

---

## Phase 3 — Integration dry-run + reconciliation (parallel, depends on Phase 2)

### T9 — full bootstrap `--dry-run` exercise (furthest subagent-executable proxy for the live gate)

- **Prerequisite**: run on the **arm64 dev host with Docker Desktop running** —
  the script's Apple-Silicon guard and `docker info` check
  (`bootstrap-mac-mini.sh:133-159`) are *not* dry-guarded and exit early on a
  non-arm64 host or with the daemon down, before any `would run:` lines print.
- **Scope**: run `./scripts/bootstrap-mac-mini.sh --dry-run` on the dev machine
  and confirm it prints the full step sequence (arch → Docker → Homebrew/uv →
  repo path → secrets → USB → CAMELS-CH → VERSION → compose up → health wait →
  LaunchAgent install → summary) without mutating state, and that `--uninstall
  --dry-run` and `--help` behave; **out**: any non-dry run touching
  `~/Library/LaunchAgents`, Docker, or `/Volumes` (operator gate, Phase 4).
- **Verification**: `bash scripts/bootstrap-mac-mini.sh --dry-run >/tmp/bootstrap_dryrun.log 2>&1; grep -q 'dry run complete' /tmp/bootstrap_dryrun.log && grep -q 'would run:' /tmp/bootstrap_dryrun.log` — exit 0.

### T10 — roadmap refresh + parent-plan closeout + memory (Decision-required item)

- **Scope**: tick the §1.5a/§1.5b boxes in `docs/v0-launch-roadmap.md` to reflect
  Stream C DONE, annotating that commit `514ff36` (tag `v0.1.403`) delivered them
  and recording the roadmap-edit investigation finding (the boxes were authored
  unchecked by the delivering commit, never reverted). **Do not blindly flip the
  overlay box that reads "…caddy TLS" to `[x]`** — that would assert undelivered
  work as delivered; instead **amend the box text** to "`docker-compose.macmini.yml`
  — overlay (USB backup bind-mount, CAMELS-CH host bind-mount; **caddy TLS dropped
  per Plan 046 D1 — public HTTPS → Plan 049**)" before marking it done. Add a
  Stream C close-out note to Plan 046 referencing this plan; record the caddy/TLS
  LAN-only decision and the T7 lint-gate in the roadmap "Decisions locked" block;
  update the MEMORY index line for Stream C; **out**: flipping Plan 046 to DONE
  (Stream D §1.6 soak still open) or rewriting Plan 046's Revision history beyond
  one close-out line.
- **Verification**: `grep -q 'v0.1.403' docs/v0-launch-roadmap.md && uv run python -c "import pathlib,sys; t=pathlib.Path('docs/v0-launch-roadmap.md').read_text(); blk=t.split('### 1.5 ')[1].split('### 1.6')[0]; sys.exit('unticked 1.5 boxes remain' if '- [ ]' in blk else 0)"` — exits 0.

---

## Phase 4 — Operator gate (hand-off, NOT subagent-executed)

> Tracked for completeness; **out of subagent scope** (Decision 4). This is the
> roadmap §1.5 **Gate** and the §1.6 Stream D soak, run on physical hardware.

### T11 — live fresh-Mac-mini bootstrap (operator)

- **Scope**: on `sapphire-staging.local` with §1.4b prerequisites met (hostname,
  `sapphire` user, Docker Desktop, USB SSD at `/Volumes/sapphire-backup`, repo
  cloned, CAMELS-CH staged): `cd /Users/sapphire/SAPPHIRE_flow && git pull &&
  ./scripts/bootstrap-mac-mini.sh`; **out**: the 7-day soak (Stream D §1.6, a
  separate operator milestone).
- **Verification** (operator-run, recorded in the runbook / Stream D report):
  `curl -sf http://localhost:8000/api/v1/health | jq -e '.status=="ok"'` returns
  true, and `launchctl list | grep hydrosolutions` shows both agents loaded.

---

## Dependency graph

```json
{
  "phases": [
    {
      "id": "phase-1-audit",
      "tasks": ["T1", "T2", "T3", "T4", "T5", "T6"],
      "parallel": true
    },
    {
      "id": "phase-2-harden",
      "tasks": ["T7"],
      "parallel": false,
      "depends_on": ["phase-1-audit"]
    },
    {
      "id": "phase-3-integrate",
      "tasks": ["T9", "T10"],
      "parallel": true,
      "depends_on": ["phase-2-harden"]
    },
    {
      "id": "phase-4-operator-gate",
      "tasks": ["T11"],
      "parallel": false,
      "depends_on": ["phase-3-integrate"],
      "note": "Operator-run on physical hardware; not dispatched to a subagent."
    }
  ]
}
```

> **Task-level dependency note:** Phase 2 is now a single task — **T7** touches
> CI/pre-commit config only and depends solely on the Phase-1 audits confirming
> the artifacts it gates are green. (The former T8 watchdog backup-staleness
> dedupe was cut — see "Deferred".)

---

## Task Exit Gate (per task, per `docs/workflow.md`)

1. Task verification command passes.
2. `uv run ruff check src/ tests/` and `uv run ruff format --check src/ tests/` clean.
3. `uv run pyright src/sapphire_flow/ops/` — 0 errors (config-driven strict; do
   **not** pass `--strict` on the CLI). Applies to any task touching `src/`
   (only T3, and only if its audit surfaces a real violation).
4. **`uv run pytest` (full suite) applies only to tasks that modify `src/`** —
   in this plan that is **only T3**, and only if its audit surfaces a real
   violation requiring a code change. Doc / shell / CI tasks (T1, T2, T4, T5,
   T6, T7, T9, T10) gate on their own targeted verification command + ruff
   (items 1–2); they do **not** run the full suite, which pulls integration
   tests requiring a Postgres `DATABASE_URL`. A T3 fix re-runs at minimum
   `uv run pytest tests/unit/ops/test_watchdog.py -q`.
5. Affected docs updated in the same change (runbook Network-posture note for T6,
   overlay header for T4, cicd.md for T7, roadmap §1.5 + Plan 046 close-out for
   T10).
6. Commit includes a patch version bump + tag (CLAUDE.md §Version Bumping).

---

## Runtime-behaviour confirmation

**No remaining Plan 075 task changes runtime behaviour.** T1/T2/T3/T4/T5 are
read-only audits whose only artefacts are green verification checks (plus, for
T4, an overlay *comment*); T6/T10 are docs; T7 is CI/pre-commit *config*; T9 is a
no-mutation `--dry-run`; T11 is operator-run on hardware. The one candidate
runtime change (watchdog backup-staleness dedupe) was cut and recorded under
"Deferred". If T3's audit surfaces a genuine spec violation, fixing it is the
only path that could touch `src/` — and that is gated by Exit Gate items 3–4.
