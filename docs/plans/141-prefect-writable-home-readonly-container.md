---
status: SUPERSEDED
created: 2026-07-22
plan: 141
title: Give Prefect a writable home under the read-only container (eliminate the EROFS result-write warning)
scope: Stop the recurring `Failed to write result: [Errno 30] Read-only file system: '/home/app/.prefect'` warning that fires on every Prefect flow/task on the mac-mini. The read-only container (Plan 133) leaves Prefect's default home on the read-only root FS. Point PREFECT_HOME at a writable path. Infra.
depends_on: []
blocks: []
supersedes: []
superseded_by: 103
---

# Plan 141 — Prefect writable home under the read-only container

## Status

**SUPERSEDED by Plan 103 (owner decision 2026-07-22).** This plan was drafted without noticing that
**Plan 103** (`docs/plans/103-prefect-worker-observability-and-home.md`, DRAFT, high-priority, "Supersedes
062") **already owns this fix**: its **D1** is the identical `PREFECT_HOME=/tmp/prefect` change, and Plan 103
*also* fixes the higher-value companion defect — **flow-run logs are not persisted to Prefect** (`prefect
flow-run logs <id>` returns empty, which is what made the 2026-07-03 incident and the 2026-07-22 NWP-outage
diagnosis rely on raw container stdout). Pursuing three overlapping drafts (062/103/141) made no sense. **Do
the work in Plan 103** (grill-me on its D2 log-persistence mechanism → `/plan` → `/implement`); the EROFS
warning fix rides along as 103's D1. Kept for provenance only; not to be implemented.

*(Original DRAFT text below is retained for reference — the design in it is correct but is a subset of Plan
103's D1.)*

## Context — a benign-but-recurring warning that hides real result-write failures

Every Prefect flow/task on the mac-mini logs, repeatedly:

```
WARNING  Failed to write result: [Errno 30] Read-only file system: '/home/app/.prefect'
         Execution will continue, but the result has not been written
```

It is currently **non-fatal** — Prefect passes results in-memory within a run and continues — but it is
noisy, it means **no task/flow result is ever persisted**, and it is a latent hazard: any Prefect feature
that *depends* on a persisted result (a cross-task cache, a retry that reloads a prior result, a resumable
flow, a future flow that reads another's result) will silently misbehave, and it muddies log-based ops
triage (a real result-write failure is indistinguishable from this constant noise). It should be fixed once,
cleanly.

## Root cause — read-only container + Prefect's default home on the root FS

Plan 133 hardened the containers to **`read_only: true`** (`docker-compose.yml:112,164,211,250,292` — the
`prefect-worker`, `prefect-worker-ingest`, `api`, and `init` services all run Prefect). Their only writable
mounts are `tmpfs: [/tmp, /data/cache]` plus the specific `/data/*` named volumes (artifacts, backups,
nwp_grids, bafu archives) — see the worker block `docker-compose.yml:112-129`. **No `PREFECT_HOME` is set**
(the env has `PREFECT_API_URL` / `PREFECT_LOGGING_LEVEL` only, `:92-97`), so Prefect falls back to its
default **`~/.prefect` = `/home/app/.prefect`**, which lives on the **read-only root filesystem** → every
write there is `EROFS`. The worker uses the remote `prefect-server` (via `PREFECT_API_URL`) for
orchestration, so `PREFECT_HOME` here is only local scratch (result/local storage, profiles) — it just needs
to be *writable*.

## Objective

No `EROFS`/`Failed to write result` warnings from any Prefect service on the mac-mini; Prefect result/local
storage lands on a writable path; all flows (forecast-cycle, ingest, collectors, onboard-model, train-models,
hindcast) continue to run correctly.

## Non-goals

- **Not** relaxing the `read_only: true` hardening (Plan 133 security posture stays).
- **Not** changing Prefect orchestration (the server/postgres backend is untouched).
- **Not** enabling/disabling result *persistence semantics* beyond giving it a writable target (if we later
  want durable cross-run results, that's a separate decision — this plan only removes the EROFS wall).

## Design decision — LOCKED to Option A (tmpfs `/tmp/prefect`), owner 2026-07-22

**`PREFECT_HOME=/tmp/prefect`** on all four Prefect-running services, reusing the existing `/tmp` tmpfs. Zero
new mounts, minimal diff, guaranteed writable. **Ephemeral** — cleared on container restart, which is correct
for SAPPHIRE: flows run to completion in one process, and the durable state lives in `prefect-server`'s
postgres + our own `/data` artifact/forecast stores, **not** in Prefect's local result storage. `/plan`
should still sanity-check that no flow depends on a Prefect result surviving a restart (none is known), but
the target path is decided; T1 below no longer carries the Option-B volume/chown/doc branches.

*(Option B — a persistent `prefect_home` named volume at `/data/prefect` — was considered and rejected as
over-built for a transient scratch dir: it would add a volume + 4 mounts + an entrypoint chown + a cicd
doc row for no benefit SAPPHIRE currently needs.)*

## Tasks

### T1 — set PREFECT_HOME to a writable path on all Prefect services

- **Scope:** add `PREFECT_HOME=/tmp/prefect` to the `environment:` of `prefect-worker`,
  `prefect-worker-ingest`, `api`, and `init` in `docker-compose.yml` (and the mac-mini overlay if it overrides
  env). `/tmp` is already a writable tmpfs on each service (`docker-compose.yml:113-115` etc.), so the
  `/tmp/prefect` subdir is auto-created writable — **no new volume, no entrypoint chown, no cicd doc change**
  (that is the whole point of choosing tmpfs). Sanity-check that all four services do mount a `/tmp` tmpfs
  (they do per Plan 133).
- **Files:** `docker-compose.yml` (+ overlay if applicable); a compose-config assertion in `tests/` if one
  exists for env wiring.
- **Verification:** `docker compose -f … -f docker-compose.macmini.yml config` parses; `PREFECT_HOME` resolves
  on the worker (`printenv PREFECT_HOME`).

### T2 — deploy + verify the warning is gone

- **Scope:** deploy via the standard overlay upgrade sequence (`docs/standards/cicd.md`;
  `-f docker-compose.yml -f docker-compose.macmini.yml`, token, `run --rm --build init`, `up -d` — never a
  bare `up`, see `reference_macmini_ssh_access`). Trigger a representative flow (e.g. a forecast-cycle or a
  small onboard/train run) and confirm **no** `Failed to write result` / `Read-only file system:
  '/home/app/.prefect'` log lines, and that Prefect now writes under `PREFECT_HOME` (or that the write path is
  simply gone from the logs).
- **Files:** deploy actions (version bump only).
- **Verification:** live — a full flow run with **zero** EROFS/result-write warnings in the worker logs; the
  flow completes normally.

## Dependency graph

```json
{
  "phases": [
    { "id": "wire", "tasks": ["T1"], "parallel": false, "depends_on": [] },
    { "id": "deploy", "tasks": ["T2"], "parallel": false, "depends_on": ["wire"] }
  ]
}
```

## Exit gates

```bash
uv run ruff format --check src/ tests/ && uv run ruff check src/ tests/
uv run pyright src/
uv run pytest
```

## References

- `docker-compose.yml` (read_only services `:112,164,211,250,292`; worker env `:92-97`; worker tmpfs
  `:113-115`; worker `/data/*` volume mounts `:118-129`).
- `docker/entrypoint.sh:30` (the `chown app:app /data/*` line — extended for Option B's named volume).
- Plan 133 (the read-only-data-dir / read-only container hardening this interacts with);
  `docs/plans/133-read-only-data-dir-resilience.md`.
- Observed 2026-07-22 during Plan 138 `onboard-model` (`tidy-mastiff`) — the warning fires on every task.
- memory `reference_macmini_ssh_access` (deploy overlay), `feedback_worktree_discipline_parallel_sessions`.
