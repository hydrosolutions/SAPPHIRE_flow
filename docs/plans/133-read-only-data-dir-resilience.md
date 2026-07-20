---
status: DRAFT
created: 2026-07-20
plan: 133
title: resolve_data_dir must not crash on a read-only data root (API data-layer outage)
scope: Fix the eager-mkdir crash under read_only containers + close the test gap that let it ship to every deployment.
depends_on: []
blocks: []
---

# Plan 133 — read-only data-dir resilience

## Status

**DRAFT.** For `/plan` adversarial review before READY. Do not implement until READY.

## Context — a live, prod-relevant API outage

The revived watchdog (PR #108) fired a "BAFU forecast collector STALE" alert. Investigation showed the
collector is **healthy** (heartbeat `ok` at 13:00, 3,748 archived parquet files) — the alert was a false
positive that surfaced a **real, larger** problem: the staging API's **entire data layer returns 500**.

```
GET /api/v1/health                 -> 200   (basic; does not use get_stores)
GET /api/v1/stations               -> 500
GET /api/v1/health/detail?...      -> 500
```

The container's healthcheck only probes basic `/health`, so Docker reports it **healthy** while every
store-backed endpoint is down — the same green-but-broken pattern as prior dark-feed incidents.

## Root cause — code assumes a writable `/data`; the container is read-only

`config/paths.py::resolve_data_dir` eagerly creates **all three** subdirs on every call:

```python
_SUBDIRS = ("raw", "artifacts", "cache")
for subdir in _SUBDIRS:
    (root / subdir).mkdir(parents=True, exist_ok=True, mode=0o750)   # <- crashes
```

`api/deps.py::get_stores` calls `resolve_artifact_dir()` → `resolve_data_dir()` on every request. The
`api` service (base `docker-compose.yml`) is `read_only: true`, `SAPPHIRE_DATA_DIR=/data`, and mounts
**only** `model_artifacts:/data/artifacts:ro` — no writable `/data`, no `/data/raw`. So
`mkdir('/data/raw')` raises `OSError: [Errno 30] Read-only file system` → `get_stores` crashes → 500.

Verified traceback (host): `api/deps.py:52 → config/paths.py:28 → config/paths.py:23 → mkdir → OSError`.

**This is structural, not a recent regression.** It has been latent since the `read_only` hardening
(`7994c5d`) met the eager-mkdir resolver (`7d103e4`); the API data layer fails on **every** container
start. Because `read_only: true` lives in **base** compose, **every deployment is affected — prod and
Nepal too**, not just staging.

**Classification: a CODE bug.** The compose hardening (read-only root + explicit `tmpfs`/mounted
volumes, `docs/standards/security.md:292`) is correct and stays. The code wrongly assumes `/data` is
writable and over-creates subdirs the API never uses (it only *reads* `/data/artifacts`).

## Why it shipped (the gap this plan must close)

1. The container healthcheck hits only basic `/health` (200) → Docker reports "healthy".
2. No data-endpoint consumers on staging, so nobody saw the 500s.
3. **Test gap:** `tests/unit/config/test_paths.py` exercises only a *writable* root
   (`test_creates_raw_artifacts_cache`) — it never runs against a read-only root, and **no test at all
   exercises the app under the `read_only: true` container posture.** CI is green on a bug live in every
   deployment.

## Objective

Fix the crash **and** close the class of failure so a read-only data root can never again take down the
API. Two deliverables of equal weight: the code fix, and the regression coverage that would have caught
it.

## Non-goals

- **Not** weakening the `read_only` container hardening — it is correct and stays.
- **Not** mounting `/data/raw`/`/data/cache` writable into the API — the API does not use them; provisioning
  dirs a service never needs is the wrong fix.
- **Not** the BAFU collector (healthy) or the watchdog (fixed in #108).

## Scope

### 1. Make data-dir resolution safe under a read-only root (`config/paths.py`)

Two changes, together:

- **Create per-need, not eagerly.** `resolve_data_dir` stops creating all three subdirs on every call.
  Each resolver ensures only the subdir it returns: `resolve_artifact_dir` ensures `artifacts`; a
  `raw`/`cache` consumer ensures its own. So the API's `resolve_artifact_dir()` path never touches
  `raw`/`cache`.
- **Tolerate a read-only filesystem.** The ensure-subdir helper: if the dir exists, return it (no
  write); if it is missing, attempt `mkdir`; on `OSError` with `errno == errno.EROFS`, **do not crash** —
  return the path and let a caller that actually *writes* there fail at point-of-use with a clear error.
  Re-raise other `OSError`s (a genuine permission/config fault must stay loud). Rationale: a read-only
  root means the deployment pre-provisions exactly the dirs that container needs (the API mounts
  `artifacts`); a dir this process cannot create is a dir it is not meant to write.

Writable deployments are unchanged in effect: the dir a caller needs is created on first use exactly as
before (just lazily, and by the specific resolver rather than the shared eager loop).

### 2. Rewire the two caller shapes (`api/deps.py`, `flows/_db.py`, `flows/onboard.py`)

- `resolve_artifact_dir()` (API `get_stores` `api/deps.py:52`; `flows/_db.py:46`) — ensures only
  `artifacts`. On the read-only API, `/data/artifacts` is a mounted volume that already exists → no
  write, no crash.
- `flows/onboard.py::_resolve_default_camels_dir` (`:78`) uses `resolve_data_dir()/"raw"/"CAMELS_CH"`
  to locate CAMELS **input** data it **reads**; the `raw` tree is deployment-provisioned (the worker
  mounts it). Post-fix it resolves the path without the shared eager-create; confirm onboarding still
  finds CAMELS. (If a writable-dev path needs `raw` created, that is the `raw` resolver's job, invoked
  by the writer — not the reader.)

### 3. Regression coverage — the "never again" (the core of this plan)

- **Unit (`tests/unit/config/test_paths.py`):** a read-only-root case — `resolve_data_dir` and
  `resolve_artifact_dir` against a `chmod 0o555` root **do not raise** and return the correct paths;
  when `artifacts` pre-exists under the read-only root, it is returned without a write. Update the
  existing `test_creates_raw_artifacts_cache` to the new per-need contract (behaviour change: subdirs
  are created lazily by their resolver, not eagerly by `resolve_data_dir`). *Soundness: the read-only
  test must FAIL against today's eager-mkdir code.* (skip on root — `os.geteuid() == 0` bypasses perms.)
- **Dependency-level regression (the test that would have caught this):** `api/deps.py::get_stores`
  resolves under a read-only data root (`SAPPHIRE_DATA_DIR` → a read-only dir containing only
  `artifacts`) **without raising**. This reproduces the exact container condition in a unit test.
  *Soundness: must FAIL against the current code.*

### 4. Prevent recurrence beyond this call site (docs + a posture guard)

- **Document the invariant** in `docs/standards/security.md` (near the `read_only` note, `:292`) and
  `docs/conventions.md`: *application code must not `mkdir` on the data root at import- or request-time;
  containers run `read_only: true`, so only `tmpfs` and explicitly-mounted volumes are writable. Resolve
  paths; create a dir only where you write, and tolerate a read-only root.* This is what stops a future
  resolver/store from reintroducing the class.
- **Posture guard (decide scope in review):** the durable guarantee is an integration/e2e check that
  boots the API under `read_only: true` (or `SAPPHIRE_DATA_DIR` at a read-only path) and asserts a
  store-backed endpoint returns 200. The e2e capstone does not currently run the hardened posture (the
  gap). Options to weigh in `/plan`: (a) the dependency-level unit regression in §3 is sufficient and
  cheap; (b) add a `read_only`-posture integration smoke. Lean (a) unless review argues the class needs
  the full-stack guard.

## Deploy

Code change → image rebuild + redeploy on the mac mini (`docker compose … up -d --build`, VERSION bump
per convention), unlike #108's plist copy. **Verify on the host:** `/api/v1/stations` and
`/api/v1/health/detail?check_type=bafu_forecast_freshness` return **200**, and the watchdog's BAFU check
goes green (the collector was never the problem). The same fix protects prod/Nepal on their next deploy.

## Risks / review points

- **Behaviour change (lazy vs eager creation):** subdirs are now created by the resolver that needs
  them, not eagerly by `resolve_data_dir`. Any implicit reliance on "all three exist after any
  `resolve_data_dir` call" must be found (grep) and made explicit. The existing eager-create test
  encodes that reliance and is updated in §3.
- **EROFS-skip could mask a real misconfig** — mitigated by re-raising non-EROFS `OSError` and by the
  point-of-use write still failing loudly for a writer that genuinely needs a missing dir.
- **onboard `raw`** — reader, not writer; confirm no regression.

## Exit gates

```bash
uv run ruff format --check src/ tests/ && uv run ruff check src/ tests/
uv run pyright src/          # must not exceed baseline
uv run pytest                # incl. the new read-only + get_stores regression tests
```

## Verification

- Unit: the read-only-root and `get_stores`-under-read-only tests pass, and each **fails** against the
  pre-fix code (soundness).
- Host (post-rebuild): the 500 endpoints return 200; watchdog BAFU check green.

## References

- PR #108 (revived watchdog) — surfaced this.
- `config/paths.py` (`resolve_data_dir` / `resolve_artifact_dir`), `api/deps.py::get_stores`.
- `docker-compose.yml` `api` service (`read_only: true`, `SAPPHIRE_DATA_DIR=/data`,
  `model_artifacts:/data/artifacts:ro`); `docs/standards/security.md:292` (the read-only invariant).
- `tests/unit/config/test_paths.py` (the coverage gap).
