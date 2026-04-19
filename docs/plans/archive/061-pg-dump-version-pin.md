# Plan 061 — Pin `pg_dump` to postgres-16 client in the sapphire-flow image

**Status**: READY
**Date**: 2026-04-19
**Depends on**: Plan 060 DONE (archived at commit `1c3589a`).
**Blocks**: Plan 046 Stream A A3 step 9 (API spot checks include a backup-database probe), Stream D2-D7 (scheduled backup is a runbook requirement), Plan 048 (restic wraps `pg_dump`).
**Scope**: Replace the unversioned `postgresql-client` apt package in `Dockerfile` stage 2 with the versioned `postgresql-client-16` from the official postgres apt repo, so the `pg_dump` invoked by `backup_database_flow` matches the postgres 16.4 server in `postgis/postgis:16-3.4`. One-file change + one-line Dockerfile RUN addition for the apt source, plus a minimal validation.

---

## Context

### Why now

Plan 060 T5 surfaced that `backup-database` fails with:

```
pg_dump: server version: 16.4 (Debian 16.4-1.pgdg110+2); pg_dump version: 15.16
pg_dump: error: aborting because of server version mismatch
```

The sapphire-flow image's Dockerfile installs `postgresql-client` (unversioned; Debian 11 bookworm ships 15.x) but the postgres service runs 16.4. `pg_dump` refuses to dump a newer-version server.

The cap_add / ownership fix landed in Plan 060 is **verified working** — the zero-byte dump file at `/data/backups/sapphire_YYYYMMDD_HHMMSS.dump` is owned by `app:app`, proving the chown and write permission are correct. The sole remaining failure mode is the version mismatch.

### Inputs (verified)

- `postgis/postgis:16-3.4` ships postgres 16.4 server.
- `Dockerfile:28` installs `postgresql-client` (unversioned; Debian resolves to 15.16 per the Plan 060 T5 subagent log).
- `docker/entrypoint.sh` and `src/sapphire_flow/flows/backup.py` invoke `pg_dump` (no version flags).
- Debian's postgres apt repo (`apt.postgresql.org`) provides versioned packages `postgresql-client-15`, `postgresql-client-16`, `postgresql-client-17` per the `PGDG` convention.

### Problem statement

`pg_dump` client < postgres server major version refuses to run. Needs the matching major.

---

## Architecture decisions

| # | Decision | Rationale |
|---|---|---|
| D1 | **Install `postgresql-client-16` from `apt.postgresql.org`** rather than hoping Debian backports 16.x. | Pinning the major version makes the Dockerfile declaration match the server container. The PGDG repo is the canonical source; Debian's default repo lags. |
| D2 | **Remove the unversioned `postgresql-client` line** — replaced entirely. | Avoids installing two `pg_dump` binaries and PATH-resolution surprise. |
| D3 | **Add the PGDG apt source + GPG key as a discrete RUN before the install line.** | Keeps the layer diff small + surfaces the trust root clearly. |
| D4 | **No client-library version env var or flag.** | `pg_dump --version` reports the installed version; match-by-binary is sufficient. |
| D5 | **Bump postgres server + client together in future versions.** Out of scope for Plan 061 — pin one major at a time. | Plan 061 is the "fix what's broken now" plan, not a postgres upgrade. |

---

## Phases

### T1 — Dockerfile change

File: `Dockerfile`. Replace line ~28 (`apt-get install … postgresql-client …`) with a two-step block:

```dockerfile
# Add the PostgreSQL Global Development Group (PGDG) apt source + GPG key for versioned client packages.
# Debian's default repo ships postgresql-client-15, which can't dump a postgres 16 server.
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates gnupg curl \
    && install -d /usr/share/postgresql-common/pgdg \
    && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
       -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
    && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt $(. /etc/os-release; echo $VERSION_CODENAME)-pgdg main" \
       > /etc/apt/sources.list.d/pgdg.list \
    && rm -rf /var/lib/apt/lists/*

RUN apt-get update && apt-get install -y --no-install-recommends \
      gosu postgresql-client-16 \
    && rm -rf /var/lib/apt/lists/*
```

Notes:
- `curl` is needed at build time for the key fetch; it's no longer in the final image by design (stage 1 already handles `uv`). **If the rest of the Dockerfile relies on `curl` at runtime**, keep `curl` in the runtime `apt-get install` list — the current line has it alongside `gosu`. **Verify by grep**: `grep -rn 'curl' Dockerfile docker/ src/sapphire_flow/` — if any runtime path uses curl, preserve it in the final install line. (At rev 1 drafting: docker-compose.yml healthchecks use `python urllib` per Plan 046 A2; runbook uses `curl` for manual health probes externally; subagent confirms before commit.)
- `ca-certificates` is required for HTTPS key fetch.
- The two-RUN split keeps the apt-cache clean between the source-add and the install, halving the image layer count vs a long single RUN.

### T2 — Dockerfile validation

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml build --no-cache prefect-worker 2>&1 | tail -20
# Expect: BUILD SUCCESS; apt install-y of postgresql-client-16 resolves.

# Verify the installed pg_dump version
docker compose -f docker-compose.yml -f docker-compose.dev.yml run --rm --entrypoint '' prefect-worker pg_dump --version
# Expect: pg_dump (PostgreSQL) 16.x
```

If pg_dump is still 15.x, stop and report — the apt source may have resolved to a Debian fallback.

### T3 — End-to-end validation

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml down -v
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d
# Wait for health gate.
```

Trigger `backup-database` via Prefect API (same pattern Plan 060 T5 used):

```python
PREFECT_API_URL=http://localhost:4200/api uv run python3 << 'EOF'
import asyncio, time
from prefect.client.orchestration import get_client

async def main():
    async with get_client() as c:
        deps = await c.read_deployments()
        dep = next(d for d in deps if d.name == "backup-database")
        fr = await c.create_flow_run_from_deployment(dep.id)
        t0 = time.time()
        while time.time() - t0 < 90:
            s = (await c.read_flow_run(fr.id)).state
            if s.type.value in {"COMPLETED", "FAILED", "CRASHED", "CANCELLED"}:
                print(f"backup-database: {s.type.value} in {round(time.time()-t0,1)}s  {s.message or ''}")
                break
            await asyncio.sleep(2)

asyncio.run(main())
EOF
```

Expected: COMPLETED. Verify dump file: `docker compose exec -T prefect-worker ls -la /data/backups/` — a non-zero-size `.dump` file with `app:app` ownership.

### T4 — Commit + bump + tag + archive

- `uv run ruff format` + `uv run ruff check --fix` on Dockerfile (no-op; ruff doesn't parse Dockerfile, but keeps habit).
- Stage: `Dockerfile`, `pyproject.toml`, `src/sapphire_flow/__init__.py`, `uv.lock`, `docs/plans/061-pg-dump-version-pin.md`.
- `uv run bump-my-version bump patch`; `uv sync`.
- Commit `fix(plan-061): pin pg_dump to postgresql-client-16 (A3 backup finding)`. Include a migration-note bullet: "rebuild the sapphire-flow image on every dev box: `docker compose build --no-cache prefect-worker api init`."
- Tag.
- Archive commit: `git mv docs/plans/061-pg-dump-version-pin.md docs/plans/archive/061-pg-dump-version-pin.md`, second bump, commit `docs(plan-061): archive completed plan`, tag.

---

## Files to modify

| Path | Task | Change |
|---|---|---|
| `Dockerfile` | T1 | Replace unversioned `postgresql-client` with PGDG source + `postgresql-client-16` |
| `pyproject.toml`, `src/sapphire_flow/__init__.py`, `uv.lock` | T4 | Version bump |
| `docs/plans/061-pg-dump-version-pin.md` | T4 | Archive move |

No docs/standards edits — the cap_add documentation from Plan 060 T2 already covers the privilege model.

---

## Exit gates

1. `docker compose build` for any sapphire-flow image succeeds with `postgresql-client-16` installed.
2. `pg_dump --version` inside a fresh worker container reports 16.x.
3. `backup-database` flow reaches COMPLETED when triggered.
4. `/data/backups/<timestamp>.dump` has non-zero size + `app:app` ownership.
5. Full pytest green (no test uses pg_dump directly; the image change shouldn't affect tests).
6. Commit landed, tag applied, plan archived.

After all gates pass, Plan 046 A3 step 9 (API spot checks + backup sanity) can exercise a real backup.

---

## Risks

| Risk | Mitigation |
|---|---|
| PGDG apt repo temporarily unavailable at build time | Build caches image locally; retry. If chronic, Plan 061 can pivot to a pre-built postgres-client-16 binary copy from `postgres:16` image via a multi-stage `COPY --from=postgres:16`. Fallback noted; not implemented. |
| `ca-certificates` + `gnupg` inflate the final image | Both are kept only in the apt-source layer; `rm -rf /var/lib/apt/lists/*` prevents carryover. Final image size increase < 10 MB. |
| Debian codename drift — PGDG repo URL depends on `$VERSION_CODENAME` | The shell-subshell `$(. /etc/os-release; echo $VERSION_CODENAME)` reads from the running base image, so drift is self-adjusting. If the base image changes from `python:3.11.12-slim` (bookworm) to something else, verify PGDG supports the new codename. |
| Other tools in the image used `pg_dump 15.x` features removed in 16 | Grep `src/` for any `pg_dump` reference — only `backup.py` uses it. `pg_dump 16` is backward-compatible. |
| Existing dev boxes need image rebuild before the fix takes effect | Migration note in T4 commit body instructs rebuild. |

---

## Deferred to follow-up plans

- **Bumping postgres server version** (16 → 17) — needs a coordinated client + server bump + migration.
- **Multi-stage `COPY --from=postgres:16` alternative** — if the PGDG apt route becomes fragile.
- **`backup.py` version-compat assertion** — could add a runtime `pg_dump --version | grep '^pg_dump (PostgreSQL) 16'` guard before each dump. Over-engineering for now.

---

## Open questions

Not blocking DRAFT → READY:

1. Does the runbook (Plan 046 C4) need a "if you rebuild the image, backups start working" note? Probably yes — add in T4's Plan 046 reconciliation if we batch it, or leave as a Plan 046 Stream C task.
2. Should `postgresql-client-16` pin shift to `postgresql-client` (unversioned) once the ecosystem stabilises? **No** — versioned pin is the right long-term default.
