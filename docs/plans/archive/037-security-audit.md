# Plan 037 — Security Audit: Findings and Remediation

**Status**: DONE (all findings reviewed; remaining items tracked in Plans 038/039 and CAUTION backlog)
**Dedicated plans**: Plan 038 (store write atomicity), Plan 039 (alert DATA_UNAVAILABLE status)
**CAUTION backlog**: M-1 (gosu/no-new-privileges), M-31 (BMA member count), M-41 (model_id UUID validation)
**Phase**: Cross-cutting (all phases)
**Scope**: v0 hardening before any network exposure; v1 prerequisites marked

## Context

A file-by-file security review of the entire SAPPHIRE Flow codebase (~100 source
files, 24 migrations, Docker infrastructure, and configuration) was conducted on
2026-04-15. Findings are organized by severity and grouped thematically. Each
finding includes the affected file(s), line numbers, and a recommended fix.

**Methodology**: Every `.py` file under `src/sapphire_flow/`, all infrastructure
files (`Dockerfile`, `docker-compose.yml`, `Caddyfile`, entrypoints, init scripts),
and configuration files (`config.toml`, `alembic.ini`, `.env.example`, `pyproject.toml`)
were reviewed for OWASP Top 10 vulnerabilities, supply-chain risks, data integrity
issues, and domain-specific safety concerns (false-negative alert suppression).

### Severity definitions

| Level | Meaning |
|-------|---------|
| CRITICAL | Exploitable now; can lead to RCE, full data breach, or silent flood alert suppression |
| HIGH | Exploitable with moderate effort or likely to cause significant operational harm |
| MEDIUM | Defense-in-depth gap; exploitable under specific conditions or with insider access |
| LOW | Minor hardening opportunity; unlikely to be exploited but worth fixing |

### Summary

| Severity | Count |
|----------|-------|
| CRITICAL | 5 |
| HIGH | 30 |
| MEDIUM | 45 |
| LOW | 40+ |
| **Total** | **~120** |

---

## CRITICAL Findings

### C-1. No authentication on any API endpoint — ACCEPTED RISK (v1)

**Files**: `api/__init__.py:14`, all files under `api/routes/`
**Impact**: Every endpoint is publicly accessible with zero credentials.
**Decision**: ACCEPTED RISK — deferred to v1. v0 data is Swiss public data
(MeteoSwiss, BAFU), all endpoints are read-only GETs, no flow-trigger endpoints
exist. The security standard explicitly defers auth: *"v0 defers auth —
single-user, no access control."* Full OAuth2/JWT/MFA auth is designed for v1.

### C-2. Unsafe deserialization — `np.load()` without `allow_pickle=False` — FIXED

**File**: `models/linear_regression_daily.py:250`
**Fix applied**: Added `allow_pickle=False` to `np.load()` and post-deserialization
schema validation (key presence, array shapes, n_steps consistency, finiteness
check). All 8 existing tests pass. No migration needed — existing artifacts are
pure-numeric NPZ.

### C-3. Secrets file world-readable — FIXED

**File**: `secrets/db_password` (filesystem)
**Fix applied**: `chmod 600 secrets/db_password`. File is now owner-only read/write.
The `secrets/` directory is already in `.gitignore`. No password rotation needed
(repo was never pushed to a shared location with secrets included).

### C-4. Prefect UI exposed without authentication — ACCEPTED RISK (v1)

**Files**: `Caddyfile:9-12`, `docker-compose.yml:36-62`
**Decision**: ACCEPTED RISK — deferred to v1. v0 runs on localhost or internal
network only. Single-operator setup; Prefect flow triggering via UI is acceptable.

### C-5. Caddy serves HTTP only — all traffic unencrypted — ACCEPTED RISK (v1)

**File**: `Caddyfile:1`
**Decision**: ACCEPTED RISK — deferred to v1. No domain name or external exposure
planned for v0. TLS will be configured when a production domain is provisioned.

---

## HIGH Findings

### Authentication & Authorization

**H-1 through H-5: API/infra hardening — DEFERRED TO v1**

H-1 (CORS), H-2 (security headers), H-3 (rate limiting), H-4 (stored XSS in
templates), H-5 (SRI hashes on CDN scripts) — all deferred to v1.

**H-19. Schema validation after `np.load` — FIXED (in C-2)**

### Path Traversal

**H-6. `zarr_nwp_grid_store.py` — `nwp_source` in file paths — FIXED**
Added `_safe_zarr_path()` helper: strips directory separators via `Path(nwp_source).name`,
validates resolved path is under `base_path`. Both `archive()` and `load()` use it.
8/8 tests pass.

**H-7. `model_artifact_store.py` — `artifact_path` and `model_id` in file paths — FIXED**
`_read_and_verify()`: added `is_relative_to(self._artifact_dir)` check before
`read_bytes()`. `store_artifact()`: sanitizes `model_id` via `Path(str(model_id)).name`.
52/52 tests pass (14 integration + 38 fakes).

**H-8. `config/paths.py` — `SAPPHIRE_DATA_DIR` creates directories — FIXED**
Added `mode=0o750` to `mkdir()` call. Root-directory validation not added (env var
is legitimately different across dev/Docker/CI — real defense is container isolation).
17/17 tests pass.

**H-9. `meteoswiss_nwp.py` — filename from server-supplied `href` — FIXED**
`_download_asset()`: strips directory components via `Path(file_name).name`,
validates resolved path is under `scratch_path`. 13/13 tests pass.

### SSRF (Server-Side Request Forgery)

**H-10. `meteoswiss_nwp.py` — pagination "next" URL — FIXED**
Validates `link["href"]` starts with `self._stac_base_url` before following.

**H-11. `meteoswiss_nwp.py` — asset download `href` — FIXED**
Validates `href` starts with `https://` in `_download_asset()`. Blocks `http://`,
`file://`, and metadata-service URLs.

**H-12. `hydro_scraper.py` — caller-supplied SPARQL endpoint — FIXED**
`__init__` now validates `endpoint` starts with `https://`.

### Environment Variable Exfiltration

**H-13. `_resolve_env_vars()` has no allowlist — FIXED**
Both copies (`config/qc_rules.py` and `config/deployment.py`) now enforce
`SAPPHIRE_`-prefix allowlist. Covers all downstream importers
(`forecast_qc_rules.py`, `onboarding.py`). Tests updated to use `SAPPHIRE_`-
prefixed env var names. 72 tests pass.

### Unbounded Queries (DoS)

**H-14. Multiple API endpoints return unbounded result sets — REVIEWED**
- `forecasts.py` — per-forecast values: **DEFER** (physically bounded ~2500 rows)
- `stations.py` observations.json/forcing.json/hindcasts.json: **FIXED** — 25-year
  max date range at route level (HTTP 400 if exceeded).
- `models.py` model list: **DEFER** (O(10) models operationally)
- `models.py` skill-chart.json: **CAUTION** — DO NOT add row LIMIT (silently
  truncates chart series). Needs query-level aggregation instead.
- `tables.py` page param: **DEFER** (data always capped at 50 rows)

**H-15. Multiple store methods return unbounded result sets — REVIEWED**
- **DO NOT ADD LIMIT to store methods.** Skill computation, training, and hindcast
  callers explicitly need ALL data for their time ranges. A LIMIT silently
  corrupts model outputs and skill scores.
- `forecast_store._fetch_by_ids`: CAUTION — safe to chunk IN clause (50 IDs/batch)
  as a perf optimization, but no LIMIT.
- `hindcast_store` / `observation_store`: DEFER — callers pass bounded ranges by
  design; the range is the constraint, not a row limit.

### Infrastructure

**H-16. DB password in prefect-server shell command — CAUTION**
Proposed fix (reuse `docker/entrypoint.sh`) will NOT work: Prefect image has no
`gosu`, no `app` user, and needs `PREFECT_API_DATABASE_CONNECTION_URL` not
`DATABASE_URL`. Requires a Prefect-specific wrapper script. Low urgency — password
is visible in process env only, not in logs or API.

**H-17. `sed` URL construction breaks on special characters — FIX (low urgency)**
Current password is safe (`+` but no `|`). Replace two `sed` calls with a Python
one-liner that percent-encodes the password. Both psycopg and asyncpg handle
percent-encoded URLs correctly. `gosu` structure unchanged.

**H-18. `logging.py` — env-var-controlled log levels — DEFER**
INFO fallback is the correct safe default. Not a meaningful security finding.
Making it raise would bring down production on a log-level typo.

### Model Safety

**H-19. `linear_regression_daily.py` — schema validation — FIXED (in C-2)**
Already addressed: `allow_pickle=False` + shape/ndim/finiteness validation.

**H-20. `model_registry.py` — entry-point supply chain — DEFER**
v0 has zero external model packages (all three entry points are internal). Document
`uv sync --require-hashes` as a pre-v1 deployment gate. No code changes needed.

### Data Integrity

**H-21. AUTOCOMMIT with no transactions — DEFERRED TO Plan 038**
Two-phase inserts (`store_forecast`, `store_hindcast`, `store_group`) need atomic
wrapping. Investigation revealed `conn.begin()` on AUTOCOMMIT provides zero
atomicity and `begin_nested()` requires a real transaction. Needs engine injection
+ per-method transactional connection. See Plan 038.

**H-22. DDL privileges in application code — FIXED**
Removed `run_migrations()` from `setup_production_stores()`. Init container in
docker-compose handles migrations. For local dev: `alembic upgrade head`.

### Alert Safety

**H-23. Silent false negatives in alert checking — REVIEWED**
- H-23a (CDF flat): **DEFER** — already guarded; QUANTILES path not reachable in v0.
- H-23b (empty ensemble → 0.0): **FIXED** — added `log.warning` when `max()`
  returns None. The 0.0 fallback is the correct conservative choice.
- H-23c (stale alerts on sensor failure): **DEFERRED TO Plan 039** — auto-resolve
  would mask active floods. Needs new `DATA_UNAVAILABLE` alert status.
- H-23d (ensemble too small skip): **DEFER** — already logged at WARNING.

### Sensitive Data Leakage

**H-24. Exception messages stored in result structs — DEFER**
Result structs never reach API clients or external storage in v0. Prefect result
storage is local. When Phase 9 exposes status APIs, create separate response schemas
that omit/sanitize the `error` field.

**H-25. `tools/record_fixtures.py` — CWD-relative config — DEFER**
Developer-only CLI tool, not a production path. Public NWP data in `/tmp`.

### Timezone Handling

**H-26. Timezone clobber in cycle time parsing — CAUTION**
Real bug but naive fix breaks existing callers. `ensure_utc()` raises on naive
datetimes, which is the common Prefect input. Safe fix is two-step:
```python
parsed = datetime.fromisoformat(cycle_time_str)
if parsed.tzinfo is None:
    parsed = parsed.replace(tzinfo=UTC)  # convention: naive = UTC
return ensure_utc(parsed)               # converts non-UTC aware to UTC
```
DO NOT simply replace `.replace(tzinfo=UTC)` with `ensure_utc()`.

### Reproducibility

**H-27. Non-reproducible hash seeds — REVIEWED**
- `model_onboarding.py:264` (smoke test): **FIXED** — replaced `hash()` with
  `hashlib.sha256` for deterministic, cross-process-stable seeding.
- `forecast_combination.py:155` (BMA): **CAUTION** — changes member labeling on
  deployment. Schedule for a deployment boundary. Should also incorporate
  `cycle_time` in the seed for per-run reproducibility.

---

## MEDIUM Findings

### Infrastructure (M-1 through M-7)

**M-1.** CAUTION — `no-new-privileges` breaks `gosu` (SUID). Needs gosu
replacement before enabling. Backlog item.

**M-2.** FIXED — Added backend/frontend Docker network segmentation.

**M-3.** FIXED — Pinned `python:3.11.12-slim`, `uv:0.7.3`, `caddy:2.9`.

**M-4.** FIXED — Deleted stale root-level `entrypoint.sh`.

**M-5.** DEFER — Env var fallback is intentional for local dev without Docker
Secrets. Fails loudly if neither secret file nor env var is set.

**M-6.** DEFER — False positive. `literal_binds=True` is required for offline
Alembic mode. Migrations use only schema constants, never user input.

**M-7.** DEFER — CSP would break Prefect UI (React SPA with CDN + inline scripts).
Needs path-scoped Caddy audit.

### API (M-8 through M-14)

**M-8.** DEFER — `PREFECT_UI_URL` is container-internal, not user-controlled.

**M-9.** DEFER — Dashboard queries are cheap aggregates. Caching changes freshness
contract. Not a meaningful v0 risk.

**M-10.** DEFER — JSON endpoint truncation would break charts. Needs pagination
design, not a naive LIMIT.

**M-11.** FIXED — Replaced `"Forecasts table not found"` with generic `"Not found"`.

**M-12.** DEFER — `MetaData.reflect()` is idempotent. Race produces identical
results. Threading lock would be correct but low urgency.

**M-13.** FIXED — Wrapped `fromisoformat()` in try/except, returns HTTP 400 on
malformed datetime.

**M-14.** DEFER — Duplicate of Caddyfile header (already set there).

### Database/Store (M-15 through M-22)

**M-15.** DEFER — Pool limits safe to add standalone; SSL needs coordinated
postgres + engine change (separate infra work).

**M-16.** DEFER — v1 concern. v0 models have trivially small state (few KB).

**M-17.** DEFER — Documented as v1. Needs `stations.ownership` column in same
expression for a CHECK constraint.

**M-18.** DEFER — ~1000 rows/day, only latest read. Schedule purge in maintenance.

**M-19.** DEFER — ~7 rows/day. Negligible growth.

**M-20.** DEFER — Related to Plan 039 alert lifecycle redesign.

**M-21.** DEFER — No `users` table in v0. FK waits for v1 auth.

**M-22.** DEFER — Orphan `.zarr.old` is wasted disk only, not corruption. Sweep
in maintenance cron.

### Adapters (M-23 through M-27)

**M-23.** DEFER — `httpx.Client()` defaults to 5s timeout. Only risk is explicit
`timeout=None` (infinite). Low priority.

**M-24.** DEFER — Legitimate SPARQL responses are <10 KB. Guard adds complexity
with no real v0 risk.

**M-25.** FIXED — Added 100-page cap on STAC pagination loop.

**M-26.** FIXED — Added 500-file cap on GRIB2 downloads.

**M-27.** DEFER — Test-only replay adapter. Path traversal in a test component
with no HTTP surface is not a risk.

### Services (M-28 through M-40)

**M-28.** FIXED — Replaced 13 `assert` guards with `ConfigurationError` raises
in `run_forecast_cycle.py` and `ingest_observations.py`.

**M-29.** FIXED — Added None guard on `latest.value` in
`observation_alert_checker.py`. Logs warning and skips instead of crashing.

**M-30.** FIXED — Added `total_weight == 0.0` guard in BMA weight normalization.
Returns empty dict (already handled by callers).

**M-31.** CAUTION — Clamping negative member count changes total count semantics.
Needs careful design. Backlog item.

**M-32.** DEFER — `KeyError` on misconfigured QC threshold keys. Low priority;
QC rules come from validated TOML, not user input.

**M-33.** FIXED — Spike QC logic bug: `ref == 0.0` now returns None (no flag)
instead of false-flagging every non-zero reading after zero flow.

**M-34.** FIXED — Replaced `assert ref_ensemble is not None` with `raise
ValueError` in `alert_strategy.py`.

**M-35.** FIXED — Added column presence check in climatology artifact
deserialization. Raises descriptive ValueError on missing columns.

**M-36.** DEFER — JSON is trivially small. `.get()` fallback would silently hide
corruption worse than `KeyError`.

**M-37.** DEFER — sklearn validates alpha itself with a clear error.

**M-38.** DEFER — Arrays are kilobytes at v0 scale (21 members, 5 steps).

**M-39.** FIXED — NaN scores now filtered in BMA weights via `math.isfinite()`.
Previously NaN bypassed `> 0` guard and became weight `1e10`.

**M-40.** FIXED — Negative `nwp_age_hours` clamped to 0.0 with warning log.

### Flows/Config (M-41 through M-45)

**M-41.** CAUTION — `model_id` bound to log context before UUID validation.
Need to verify parsing order. Backlog item.

**M-42.** FIXED — Added 1900–tomorrow bounds on `period_start`/`period_end`.

**M-43.** DEFER — Container threat model: env control implies full compromise.

**M-44.** DEFER — Parameter values are DB-originating. Enum would break extensible
parameter design.

**M-45.** FIXED (in H-8) — `mode=0o750` already applied.

---

## Implementation Summary

### Fixed (33 findings across 3 commits)

| Commit | Findings | Files |
|--------|----------|-------|
| `0133fff` | C-2, C-3, H-6–H-13 | 13 |
| `410658e` | H-14 (obs/forcing/hindcast), H-22, H-23b, H-27b | 7 |
| `9f1760d` | M-2–M-4, M-11, M-13, M-25–M-26, M-28–M-30, M-33–M-35, M-39–M-40, M-42 | 21 |

### Accepted risk / deferred to v1 (with rationale in each finding above)

C-1, C-4, C-5, H-1–H-5, H-15, H-18, H-20, H-24–H-25, M-5–M-10, M-12,
M-14–M-22, M-23–M-24, M-27, M-32, M-36–M-38, M-43–M-44

### Deferred to dedicated plans

| Plan | Finding | Issue |
|------|---------|-------|
| 038 | H-21 | Store write atomicity (AUTOCOMMIT → transactions) |
| 039 | H-23c | Alert DATA_UNAVAILABLE status for sensor/model failure |

### CAUTION backlog (needs careful design before implementation)

| ID | Issue | Risk if implemented naively |
|----|-------|-----------------------------|
| M-1 | `no-new-privileges` on containers | Breaks `gosu` SUID privilege drop |
| M-31 | BMA negative member count clamp | Changes total member count semantics |
| M-41 | Validate model_id as UUID before log binding | Must verify parsing order |
| H-16 | Prefect-specific entrypoint wrapper | Prefect image has no gosu/app user |
| H-17 | sed → Python URL construction | Low urgency; current password is safe |
| H-26 | Timezone clobber two-step fix | Naive `ensure_utc()` breaks naive datetime inputs |
| H-27a | BMA seed `hashlib.sha256` | Changes member labeling; schedule at deployment boundary |

---

## Files Reviewed

### Infrastructure (11 files)
- `Dockerfile`, `docker-compose.yml`, `docker-compose.dev.yml`
- `Caddyfile`, `entrypoint.sh`, `docker/entrypoint.sh`, `docker/init-db.sh`
- `alembic.ini`, `alembic/env.py`, `.env.example`, `config.toml`, `pyproject.toml`

### Source code (87 files)
- `api/` (9 files) + HTML templates
- `adapters/` (11 files)
- `config/` (6 files)
- `db/` (3 files)
- `flows/` (9 files)
- `models/` (4 files)
- `preprocessing/` (3 files)
- `protocols/` (7 files)
- `services/` (22 files)
- `store/` (17 files)
- `types/` (16 files)
- `logging.py`, `exceptions.py`, `tools/record_fixtures.py`
