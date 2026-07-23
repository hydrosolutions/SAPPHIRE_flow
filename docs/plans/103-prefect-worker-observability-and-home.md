# Plan 103 — Writable `PREFECT_HOME` under the read-only container

**Status**: DRAFT
**Priority**: high — the worker logs a `Failed to create the Prefect home
directory` warning on every start, and the in-container Prefect CLI (e.g.
`prefect flow-run logs <id>`) fails until you manually pass `-e
PREFECT_HOME=/tmp/prefect`. Non-fatal but latent: any Prefect feature that
writes to home breaks silently. First hit 2026-07-03 (mini incident), again on
the local stack 2026-07-06, and it recurs as a `WARNING Failed to write result:
[Errno 30] Read-only file system: '/home/app/.prefect'` during long runs
(seasonal retrain, 2026-07-22).
**Phase**: v0b — orchestration / observability
**Parent**: the operational reliability track (Plans 098/100)
**Related**:
- `docker-compose.yml` — `prefect-worker` (`:80`, `read_only: true` `:112`, **no
  `PREFECT_HOME`**), `prefect-worker-ingest` (`:139`, `read_only: true` `:164`),
  and `init` (`:261`, uses the Prefect client to register deployments).
- `api` service (`:180`, `read_only: true` `:211`) shares the **same base image**
  but is **intentionally excluded** — it is an HTTP-only Prefect touchpoint
  (`src/sapphire_flow/api/routes/health.py:23` reads `PREFECT_API_URL` and checks
  Prefect over HTTP; **no Prefect client import**), so it never writes to
  `PREFECT_HOME`.
- `docs/standards/security.md:370` — a stale note that defers the write-path
  footprint to "Plan 062"; this plan settles it.
**Created**: 2026-07-06
**Supersedes**: Plans 062 and 141 (both older `PREFECT_HOME` drafts).
**Split from**: the flow-run-**log-persistence** half of the original Plan 103
moved to **Plan 142** (2026-07-23 owner decision) — it turned out to be a
load-bearing deployment-registration change, not the env-only fix this plan is,
and deserves its own careful `/plan`/`/implement` in isolation.

---

## Problem

The worker runs `read_only: true` (`docker-compose.yml:112`) with no
`PREFECT_HOME` set, so Prefect defaults to `/home/app/.prefect` — on the
read-only root filesystem. It logs `UserWarning: Failed to create the Prefect
home directory`, and Prefect result-persistence / CLI writes fail with
`[Errno 30] Read-only file system`. The worker still runs, but the in-container
Prefect CLI is unusable without a manual `-e PREFECT_HOME=/tmp/prefect`.

## Goal

No `PREFECT_HOME` warning; the in-container Prefect CLI works with no manual env
override; no `[Errno 30]` write failures against `/home/app/.prefect`.

## Design decision (SETTLED via grill-me 2026-07-22)

Set `PREFECT_HOME=/tmp/prefect` on the **three Prefect-client services:
`prefect-worker` (`docker-compose.yml:80`), `prefect-worker-ingest` (`:139`),
and `init` (`:261`)**. **NOT `api`** — HTTP-only, no Prefect client import
(verified at `src/sapphire_flow/api/routes/health.py:23`). `/tmp` is already a
writable tmpfs on each (`read_only: true` + `tmpfs: [/tmp]` at `:113`/`:165`/`:293`),
so **no new mount, no volume, no chown**.

**Why ephemeral (tmpfs) is correct — and strictly better than today.** The
**authoritative** orchestration state (flow/deployment/run records) is
**PostgreSQL-backed** (`prefect-server`, `docker-compose.yml:54`); the files
Prefect writes under `PREFECT_HOME` (CLI profile, and Prefect's local
*result-persistence* storage) are **intentionally non-authoritative** here. Note
this is not a regression: today those same result-persistence writes target the
**read-only** `/home/app/.prefect` and **fail with `[Errno 30]`**, so nothing
currently relies on them surviving anyway; `/tmp/prefect` makes them *succeed*
(just ephemeral across container recreation). Our one large forecast task already
disables result persistence explicitly
(`src/sapphire_flow/flows/run_forecast_cycle.py:840`). The verification below adds
a gate that no flow depends on cross-restart local result retrieval.

## Non-goals

- **Persisting flow-run logs to Prefect's run-log store** (`prefect flow-run logs
  <id>` returns empty). That is **Plan 142** — a separate, load-bearing change to
  deployment registration + structlog wiring.
- Central log aggregation; the Flow-4 watchdog.

## Phases & tasks

> Commands run from repo root. Version bump + hold-at-PR per CLAUDE.md §Version
> Bumping.

**Phase 1 — writable `PREFECT_HOME` (`docker-compose.yml`).**
- Task 1.1: Add `PREFECT_HOME: /tmp/prefect` to the `environment:` of
  `prefect-worker` (`:80`), `prefect-worker-ingest` (`:139`), and `init`
  (`:261`). Do **not** touch `api`.
- Task 1.2: Update the stale note in `docs/standards/security.md:370` ("Re-evaluate
  after **Plan 062** establishes the full write-path footprint (currently unknown
  because `PREFECT_HOME` is not set)"): the write-path is now known —
  `PREFECT_HOME=/tmp/prefect` (writable tmpfs) on the three client services,
  CLI/profile scratch only, durable state in Postgres; no `user:` override needed.
  Point it at Plan 103.

**Verification (local stack up).**
1. `docker compose config` parses.
2. After `docker compose up -d --build` (healthy):
   `docker compose logs prefect-worker | grep -c "Failed to create the Prefect home"`
   → `0`.
3. In **each** of the three target services, assert `PREFECT_HOME` is set and
   writable: `docker compose exec <svc> sh -c 'printenv PREFECT_HOME && test -w
   "$PREFECT_HOME"'` for `svc` in `prefect-worker`, `prefect-worker-ingest`, `init`.
4. In-container CLI works with **no** manual `-e PREFECT_HOME` override — resolve a
   real run id first: `id=$(docker compose exec -T prefect-worker prefect flow-run
   ls --limit 1 -o json | ...)` (or take one from `prefect flow-run ls`), then
   `docker compose exec prefect-worker prefect flow-run logs "$id"` returns without
   a home error.
5. No `[Errno 30] Read-only file system: '/home/app/.prefect'` in worker logs
   under a triggered run.
6. **Cross-restart result gate (D1 durability check):** trigger a `forecast-cycle`
   run to completion, `docker compose restart prefect-worker`, and confirm no
   subsequent run **fails** trying to read a now-gone local result (i.e. nothing
   depends on `PREFECT_HOME` result-persistence surviving restart). If any flow does,
   configure result persistence off for it (cf. Plan 046) before deploy.

## Process

**Grill-me DONE (2026-07-22).** D1 settled here; the D2/D3/D4 log-persistence
work was split to **Plan 142** (2026-07-23) after three `/plan` rounds showed it
requires changing all 12 deployment entrypoints (file-path → module-path) — a
load-bearing change that a `/plan`-verified colon-vs-dot Prefect landmine made
too risky to bundle with this trivial env fix. Plans 062 + 141 folded in as
`SUPERSEDED by 103`.

**Independent Codex review of the narrowed D1 plan (2026-07-23): no blocker —
"scope correct and complete".** Folded its findings: (major) narrowed the
durability wording — authoritative state is Postgres, local Prefect
result-persistence files are non-authoritative and tmpfs is strictly better than
today's EROFS-failing writes — plus a cross-restart result gate; (minor) the
verify steps now resolve a real run id and assert `test -w "$PREFECT_HOME"` in all
three services; (nit) fixed the stale `api` line citation (`:180`/`:211`, not
`:250` which is Caddy). Codex independently confirmed: all three targets have
`read_only: true` + a writable `/tmp` tmpfs; `api` never imports the Prefect
client; `prefect-server` is the only other Prefect-CLI container and is not
read-only; no pre-existing `PREFECT_HOME` in compose/Dockerfile/entrypoint; and the
mac-mini overlay does not redeclare these services so no duplicate overlay edit is
needed.

**Next: READY → `/implement` → deploy** (mac-mini, overlay stack
`-f docker-compose.yml -f docker-compose.macmini.yml`), verifying no home warning
and a working in-container `prefect flow-run logs` with no manual `PREFECT_HOME`.
