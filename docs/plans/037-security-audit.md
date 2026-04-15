# Plan 037 — Security Audit: Findings and Remediation

**Status**: READY (all HIGH findings reviewed; MEDIUM pending)
**Deferred to dedicated plans**: H-21 (AUTOCOMMIT→transactions), H-23c (DATA_UNAVAILABLE alert status)
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
- `stations.py` observations.json/forcing.json: **FIX** — add 25-year max date
  range at route level (HTTP 400 if exceeded). Generous enough for any legitimate
  dashboard use; prevents accidental epoch-to-now queries. Route-level only — no
  store or flow changes.
- `stations.py` hindcasts.json: **CAUTION** — cap range with HTTP 400, not silent
  LIMIT; truncation would corrupt the chart.
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

**H-21. AUTOCOMMIT with no transactions — FIX (logic change needed)**
Only `store_forecast` and `store_hindcast` have two-phase inserts (header + values)
that must be atomic. All other store methods are single-statement. **Minimal safe
fix**: wrap those two methods in a savepoint (`with conn.begin_nested()`), keeping
AUTOCOMMIT for everything else. DO NOT switch the whole connection to transactional
— no store method calls `conn.commit()`, so all writes would silently roll back.

**H-22. DDL privileges in application code — FIX (just logging)**
The `init` service in docker-compose already runs `alembic upgrade head`. The
in-flow `run_migrations()` calls are redundant. Remove from flows; add a read-only
schema-version check (`SELECT version_num FROM alembic_version`) that warns or
raises on mismatch. Flag for v1: init container needs
`condition: service_completed_successfully` in depends_on.

### Alert Safety

**H-23. Silent false negatives in alert checking — REVIEWED**
- H-23a (CDF flat): **DEFER** — already guarded; QUANTILES path not reachable in v0.
- H-23b (empty ensemble → 0.0): **FIX (just logging)** — add `log.warning` when
  `max()` returns None. The 0.0 fallback is the correct conservative choice.
- H-23c (stale alerts on sensor failure): **FIX (logic change needed)** — alerts
  persist forever when sensor goes offline. Both observation and forecast checkers
  need a "data unavailable" resolve path when evaluated_parameters is empty.
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
- `model_onboarding.py:264` (smoke test): **FIX** — ephemeral synthetic data, nothing
  persisted. `hashlib.sha256` makes CI reproducible. Zero risk.
- `forecast_combination.py:155` (BMA): **CAUTION** — changes member labeling on
  deployment. Schedule for a deployment boundary. Should also incorporate
  `cycle_time` in the seed for per-run reproducibility.

---

## MEDIUM Findings

### Infrastructure (M-1 through M-7)

**M-1.** No `security_opt: [no-new-privileges:true]` on any container.
`docker-compose.yml` — all services.

**M-2.** No user-defined Docker network. All containers share default bridge.
No segmentation between DB, API, and worker.

**M-3.** Unpinned image tags: `caddy:2`, `prefecthq/prefect:3-python3.11`,
`python:3.11-slim`, `ghcr.io/astral-sh/uv:latest`.

**M-4.** Stale orphaned `entrypoint.sh` at project root (Dockerfile uses
`docker/entrypoint.sh`).

**M-5.** `DB_PASSWORD` env var fallback bypasses Docker secrets security benefit.
`docker/entrypoint.sh:9`.

**M-6.** `literal_binds=True` in Alembic offline mode renders bind params as
literal SQL strings. `alembic/env.py:23`.

**M-7.** No `Content-Security-Policy` header. `Caddyfile:1-18`.

### API (M-8 through M-14)

**M-8.** `PREFECT_UI_URL` from env injected into templates as trusted URL.
`api/__init__.py:12-16`.

**M-9.** Dashboard issues ~20 DB queries per page load with no caching.
`api/routes/dashboard.py:21-233`.

**M-10.** `forecast_detail` loads all values into memory, template limits to
200 rows. Apply LIMIT at SQL level. `api/routes/forecasts.py:96-104`.

**M-11.** Internal schema details leaked in HTTPException messages (e.g.,
"Forecasts table not found"). `api/routes/forecasts.py:79`.

**M-12.** `_get_reflected` global cache: race condition on concurrent init,
never invalidated. `api/routes/tables.py:27-34`.

**M-13.** Unhandled `datetime.fromisoformat()` raises 500 with traceback.
`api/routes/stations.py:401-402,443-444,534-535`.

**M-14.** No `X-Content-Type-Options: nosniff` at application layer.

### Database/Store (M-15 through M-22)

**M-15.** No connection pool limits, timeouts, or SSL enforcement on engine.
`db/engine.py:9-10`.

**M-16.** `model_states.state_bytes` — no size limit (BYTEA), no hash
verification (unlike artifacts). `db/metadata.py:576-583`.

**M-17.** `model_assignments` ownership invariant enforced only at app layer.
`db/metadata.py:511-513`.

**M-18.** `model_states` table grows unbounded — no retention policy.

**M-19.** `pipeline_health` table grows unbounded — no retention policy.

**M-20.** `alert_store.upsert_alert()` — resolved alerts bypass dedup.
`store/alert_store.py:26-30`.

**M-21.** `alert_store.acknowledge_alert` — no FK on `acknowledged_by`.
`store/alert_store.py:93-101`.

**M-22.** Zarr store atomic swap is not crash-safe. `store/zarr_nwp_grid_store.py:46-50`.

### Adapters (M-23 through M-27)

**M-23.** No timeout enforcement contract in `hydro_scraper.py:51-53`.

**M-24.** No response size limit on SPARQL JSON responses.
`adapters/hydro_scraper.py:90-93`.

**M-25.** Unbounded pagination loop in `meteoswiss_nwp.py:134`.

**M-26.** No cap on total GRIB2 files downloaded.
`adapters/meteoswiss_nwp.py:150-168`.

**M-27.** `replay/station.py:31-39` — fixture path not restricted to expected
directory. Parameter values from Parquet not validated.

### Services (M-28 through M-40)

**M-28.** `assert` guards stripped by `python -O`. Used as runtime invariants in
`flows/ingest_observations.py:225-227`, `flows/run_forecast_cycle.py:251-259`.

**M-29.** `observation_alert_checker.py:65` — `latest.value` accessed without
None guard after QC_PASSED assumption. TypeError crashes alert check.

**M-30.** `forecast_combination.py:129-130` — Division by zero in BMA weight
normalization when `total_weight == 0.0`.

**M-31.** `forecast_combination.py:132-141` — BMA member count can go negative
for heaviest model.

**M-32.** `forecast_qc.py:29,68,113,170` — Unchecked `thresholds["key"]` access.
KeyError crashes QC.

**M-33.** `qc.py:148-152` — Spike detection divides by reference value; when
`prev.value == 0`, any non-zero value is flagged as spike.

**M-34.** `alert_strategy.py:124` — `assert` used for runtime invariant; stripped
by `-O`.

**M-35.** `climatology_fallback.py:182-184` — No schema validation on
deserialized IPC DataFrame.

**M-36.** `persistence_fallback.py:118-122` — No size or schema validation on
`json.loads(raw)`.

**M-37.** `linear_regression_daily.py:134` — Ridge `alpha` not validated as
positive.

**M-38.** `skill/service.py:207,221` — `np.stack` on unbounded ensemble lists
can cause OOM.

**M-39.** `skill/metrics.py` — NaN propagation: `compute_crps` on empty ensemble,
`compute_kge` with zero-variance input, `compute_bss` with zero climatology.
NaN scores leak to BMA weights via `bma_weights.py` (NaN > 0 → False → epsilon
weight → maximum BMA influence for meaningless scores).

**M-40.** `operational_inputs.py:204` — Negative `nwp_age_hours` (future-dated
NWP cycle) silently passes quality assessment.

### Flows/Config (M-41 through M-45)

**M-41.** `flows/onboard_model.py:296,326` — `model_id` raw string bound to
structlog context without sanitization. Log injection risk.

**M-42.** `flows/onboard_model.py:334-346` — `period_start`/`period_end` have
no bounds check. Year-1000 start triggers massive DB queries.

**M-43.** `config/deployment.py:253-255` — Config file path from env not checked
against trusted directory.

**M-44.** `flows/compute_skills.py:69-70` — `parameter` string from Prefect not
validated against allowlist.

**M-45.** `config/paths.py:23` — `mkdir` without explicit `mode`, defaults to
`0o777 & ~umask`.

---

## Remediation Roadmap

### Before any non-localhost deployment (v0 gate)

| ID | Finding | Effort |
|----|---------|--------|
| C-1 | Add authentication to all API routes | M |
| C-3 | Fix secrets file permissions | S |
| C-4 | Add Prefect UI auth in Caddy | S |
| C-5 | Enable HTTPS in Caddy | S |
| H-1 | Add CORS middleware | S |
| H-2 | Add security headers middleware | S |
| H-3 | Add rate limiting | M |
| H-4 | Fix stored XSS (`tojson` in templates) | S |
| H-5 | Add SRI hashes to CDN resources | S |
| H-14 | Add query limits to all API endpoints | M |
| H-24 | Sanitize exception messages in result structs | M |

### Before v0 production (high priority)

| ID | Finding | Effort |
|----|---------|--------|
| C-2 | `allow_pickle=False` + schema validation | S |
| H-6,7 | Path traversal in zarr + artifact stores | S |
| H-8 | Path validation in `config/paths.py` | S |
| H-9 | Path traversal in NWP adapter | S |
| H-10,11,12 | SSRF fixes in adapters | M |
| H-13 | Env var allowlist in `_resolve_env_vars` | S |
| H-15 | Unbounded store queries | M |
| H-16 | DB password in prefect-server command | S |
| H-17 | Fix sed URL construction | S |
| H-21 | Add explicit transactions for atomic ops | L |
| H-23 | Alert false-negative logging + watchdog | M |
| H-26 | Fix timezone clobber | S |
| M-15 | Connection pool + SSL on engine | S |

### Before v1 / Nepal deployment

| ID | Finding | Effort |
|----|---------|--------|
| H-20 | Entry-point supply chain hardening | M |
| H-22 | Separate migration user | M |
| H-27 | Deterministic RNG seeds | S |
| M-1 | `no-new-privileges` on containers | S |
| M-2 | Docker network segmentation | M |
| M-3 | Pin all image tags/digests | S |
| M-16 | State bytes integrity + size limit | M |
| M-18,19 | Retention policies for growing tables | M |
| M-28 | Replace `assert` with explicit guards | S |
| M-39 | NaN propagation → BMA weight safety | M |

### Effort key: S = small (<1h), M = medium (1-4h), L = large (4h+)

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
