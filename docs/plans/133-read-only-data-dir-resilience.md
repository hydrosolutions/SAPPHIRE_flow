---
status: READY
created: 2026-07-20
plan: 133
title: resolve_data_dir must not crash on a read-only data root (API data-layer outage)
scope: Fix the eager-mkdir crash under read_only containers + close the test gap that let it ship to every deployment.
depends_on: []
blocks: []
---

# Plan 133 — read-only data-dir resilience

## Status

**READY** (owner, 2026-07-20). Implementation authorised; hold at PR. `/plan` ran (2 rounds, escalated:
2 blockers + 2 majors + 2 minors) and chose design (A); all findings folded; final independent Codex
re-verify **READY-FOR-OWNER**. **Posture-guard fork (§4) resolved by owner: option (a)** — the
dependency-level `get_stores`-under-read-only unit regression; no full-stack integration smoke.

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
- **Not** rewiring `resolve_data_dir`'s eager-creation contract or its three call sites — see the
  design decision below.

## Design decision — smallest correct diff (EROFS-tolerant eager loop)

Two shapes of fix were weighed:

- **(A, chosen) EROFS-tolerant eager loop.** Keep `resolve_data_dir`'s existing eager "ensure all three
  subdirs" contract (`config/paths.py:22-23`) and all **five** call sites (the §2 table) **completely
  untouched**. Only wrap each subdir `mkdir` so that `OSError` with `errno == errno.EROFS` is swallowed
  and every other error re-raised.
- **(B, rejected) Per-need lazy creation + caller rewiring.** Move subdir creation out of the shared
  loop into each resolver (`resolve_artifact_dir` ensures `artifacts`, a `raw`/`cache` consumer ensures
  its own) and change what every caller can assume exists.

**Why (A).** The crash is specifically `mkdir("raw")` on a root that exists nowhere in the API
container: `raw` is **first** in `_SUBDIRS` (`config/paths.py:8`), so it is attempted before `artifacts`.
`artifacts` is the mounted volume and already exists, so *its* `mkdir(exist_ok=True)` is a no-op
(EEXIST) regardless of read-only-ness — meaning EROFS-tolerance alone produces exactly the same net
effect for the API path as the per-need refactor would (the API touches only `artifacts`, which exists),
at a fraction of the blast radius. (A) fully satisfies the Objective — "a read-only data root can never
again take down the API" — without changing behaviour for **any** writable-deployment caller, so it
introduces none of (B)'s "who can now assume which subdir exists?" grep-audit risk. (B)'s only extra
merit is least-privilege hygiene (the API stops trying to create `raw`/`cache` it never uses); that is
not worth the caller-rewiring risk for a hotfix to a live, every-deployment outage. Hygiene is instead
handled by the documented invariant in §4.

## Scope

### 1. Make data-dir resolution safe under a read-only root (`config/paths.py`)

One change: **tolerate a read-only filesystem in the existing eager loop.** Replace the bare
`(root / subdir).mkdir(...)` at `config/paths.py:23` with a wrapped call: attempt the `mkdir` (same
`parents=True, exist_ok=True, mode=0o750`); on `OSError` with `errno == errno.EROFS`, **do not crash** —
**emit a `structlog` debug/info line** (`data_dir.subdir_skipped_read_only`, with the subdir path +
reason) so a later point-of-use failure is traceable back to this skip, then continue. Re-raise every
other `OSError` — importantly `EACCES` (13), so a genuine permission/config fault stays loud. The eager
"ensure all three" contract, the loop, and the call sites are otherwise unchanged.

Rationale: a read-only root means the deployment pre-provisions exactly the dirs that container needs
(the API mounts `artifacts`, which pre-exists → its `mkdir(exist_ok=True)` is an EEXIST no-op and never
hits the EROFS branch); a subdir this process cannot create under a read-only root is a subdir it is not
meant to write. Writable deployments are **bit-for-bit unchanged**: every `mkdir` still succeeds eagerly
exactly as before.

### 2. Callers unchanged — full audit (5 sites, 2 containers)

No caller changes; the central resolver fix covers them all. The complete call-site set (the DRAFT
missed the two in `scripts/onboard.py`):

| call site | resolver | container | affected? |
|---|---|---|---|
| `api/deps.py:52` (`get_stores`) | `resolve_artifact_dir` | `api` (read_only) | **the live crash** — but `artifacts` is a mounted rw/ro volume that pre-exists → EEXIST no-op; the crash is `raw` (attempted first) |
| `flows/_db.py:46` | `resolve_artifact_dir` | worker (read_only) | protected by the fix |
| `flows/onboard.py:78` | `resolve_data_dir()/"raw"/…` | worker (read_only) | protected by the fix |
| `scripts/onboard.py:190` | `resolve_data_dir()/"raw"/…` | worker / CLI | protected by the fix |
| `scripts/onboard.py:274` | `resolve_artifact_dir` | worker / CLI | protected by the fix |

**The worker (`prefect-worker`) is also read_only** and also calls `resolve_data_dir` (onboarding), so it
has the same latent crash on `mkdir(/data/raw)` in **base** compose — masked on the mac mini only because
the Swiss `docker-compose.macmini.yml` binds `camels-ch:/data/raw:ro` (Plan 060, which removed the base
`/data/raw` volume). `/data/cache` is `tmpfs` and `/data/artifacts` is a rw volume on the worker, so only
`raw` bites. The EROFS-tolerant fix protects the worker too.

**Onboarding's `raw`:** read, not written — CAMELS input is provisioned as the `camels-ch:/data/raw:ro`
mount (Swiss overlay, Plan 060). Post-fix, a worker without that mount (base compose / a non-CAMELS
deployment like Nepal) simply EROFS-skips creating `raw` (logged, §1); CAMELS onboarding legitimately
requires the mount and would fail loudly at read time if run without it — correct behaviour, not a
regression.

### 3. Regression coverage — the "never again" (the core of this plan)

**Test methodology — simulate `EROFS`, not `chmod`.** A read-only *root filesystem* (`read_only: true`)
raises `OSError` with `errno == EROFS` (30). A `chmod 0o555` dir raises `PermissionError`/`EACCES` (13) —
a **different** errno the fix deliberately does **not** swallow (a permission fault stays loud). Verified
empirically: `chmod 0o555` then `mkdir` → `errno 13`, not 30. So the tests must simulate EROFS directly,
via `unittest.mock.patch.object(Path, "mkdir", ...)` raising `OSError(errno.EROFS, "Read-only file
system")` for the absent subdir(s) while letting a pre-existing `artifacts` succeed (or a real read-only
bind mount on Linux CI). **Do not use `chmod` for the read-only-root cases** — it exercises the wrong
branch and would still raise after the fix.

- **Unit (`tests/unit/config/test_paths.py`):** a new read-only-root case — with `Path.mkdir` patched to
  raise `OSError(errno.EROFS, …)` for `raw`/`cache` (absent) and succeed/no-op for a pre-existing
  `artifacts`, `resolve_data_dir` and `resolve_artifact_dir` **do not raise** and return the correct
  paths. *Soundness: this test must FAIL against today's bare-`mkdir` code (the un-tolerated EROFS
  propagates).* Add a companion **EACCES stays loud** case: with `mkdir` patched to raise
  `OSError(errno.EACCES, …)`, `resolve_data_dir` **does** raise — locking that the tolerance is
  EROFS-only, not "any mkdir failure". The existing writable-root tests
  (`test_creates_raw_artifacts_cache:110`, `test_idempotent_on_repeat_calls:122`,
  `test_creates_nested_parents:133`) are **left unchanged** — the eager contract is preserved, so they
  hold verbatim. No existing test needs a contract rewrite (a direct benefit of design (A)).
- **Dependency-level regression (the test that would have caught this):** `api/deps.py::get_stores`
  resolves under a read-only data root — `SAPPHIRE_DATA_DIR` at a dir containing only `artifacts`, with
  `Path.mkdir` patched to raise `OSError(errno.EROFS, …)` for the absent `raw`/`cache` — **without
  raising**. This reproduces the exact container condition in a unit test. *Soundness: must FAIL against
  the current code.*

### 4. Prevent recurrence beyond this call site (docs + a posture guard)

- **Document the invariant in ONE authoritative place** — `docs/conventions.md` `## Invariants`
  (existing section, `:489`), matching that section's one-bullet precedent. The wording must **match the
  chosen design (A)**, not contradict it (A keeps the request-time eager `mkdir`): *"`resolve_data_dir`
  performs idempotent, best-effort eager `mkdir` of its data subdirs and **must tolerate a read-only root
  (swallow `EROFS`, re-raise every other error)** — it never assumes `/data` is writable. Code that
  **writes** must target an explicitly writable mounted volume or `tmpfs`, never rely on the data root
  being writable."* Add a **short cross-reference** (not a restatement) from the `security.md:292`
  `read_only` bullet → `conventions.md §Invariants`, so the two don't drift.
- **Posture guard — OWNER FORK (residual).** The durable full-stack guarantee is an integration/e2e check
  that boots the API under real `read_only: true` (or `SAPPHIRE_DATA_DIR` at a read-only path) and asserts
  a store-backed endpoint returns 200; the e2e capstone does not currently run the hardened posture (the
  gap that let this ship). **Recommendation: (a)** the dependency-level `get_stores`-under-read-only unit
  regression in §3 is sufficient and cheap — it reproduces the exact failure without standing up Docker.
  **(b)** a `read_only`-posture integration smoke is the stronger guard but costs a compose/CI harness for
  a one-line code delta. Owner decides (a)/(b) at READY; the plan implements (a) unless told otherwise.

## Deploy

Code change → image rebuild + redeploy on the mac mini (`docker compose … up -d --build`, VERSION bump
per convention), unlike #108's plist copy. **Verify on the host:** `/api/v1/stations` and
`/api/v1/health/detail?check_type=bafu_forecast_freshness` return **200**, and the watchdog's BAFU check
goes green (the collector was never the problem). The same fix protects prod/Nepal on their next deploy.

## Risks / review points

- **No behaviour change for writable deployments.** The eager "ensure all three subdirs" contract is
  preserved, so the "all three exist after any `resolve_data_dir` call" assumption that every caller
  and the existing tests rely on stays true on writable roots — no grep-audit of implicit reliance is
  needed (this is the reason design (A) was chosen over per-need lazy creation; see the Design decision).
- **EROFS-skip could mask a real misconfig** — mitigated three ways: re-raise every non-EROFS `OSError`
  (incl. `EACCES`, with a test locking that); the `structlog` skip line (§1) makes it traceable; and a
  point-of-use write still fails loudly for a writer that genuinely needs a missing dir. The skip fires
  only under a read-only root — a deliberate, provisioned posture.
- **onboard `raw`** — covered in §2: reader not writer; provisioned as the `camels-ch:/data/raw:ro`
  mount (Plan 060). No regression.

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
