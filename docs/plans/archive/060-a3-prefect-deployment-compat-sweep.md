# Plan 060 — A3 Prefect/deployment compat sweep

**Status**: READY
**Revision**: 9 — eighth critical-review pass (2026-04-19). Round 8 found a **main-drift issue**: commit `289c5f8` (labelled `docs(plan-058/T4)` but scope-creep into infra) already merged the CHOWN + FOWNER cap_add additions into `docker-compose.yml` on main. T2's "commit working-tree edits" premise is therefore FALSE — T2 now reframes as **docs-only (security.md § Capabilities sub-section)**; no docker-compose.yml edit remaining. T0 precondition updated: `docker-compose.yml` expected CLEAN (not `M`); `M uv.lock` still expected. Minor: (a) added one-line note to T6 that this repo has **no `origin` remote** — `git fetch && git pull --ff-only` returns silently; subagent uses `git log --oneline -5 HEAD` for main-drift awareness. (b) T6 stale "Plan 055 may have bumped past 0.1.340+" comment updated (last bump was `v0.1.323` at `566c5db`). Plan 060 scope shrinks slightly; schema fact-check agent confirmed zero remaining Sev-1s.

**Revision**: 8 — seventh critical-review pass (2026-04-19). One last Sev-1: T5 step 5.5 SQL used uppercase `status = 'ACTIVE'`, but `ModelArtifactStatus` enum values + the DB CheckConstraint at `metadata.py:460-468` are lowercase (`'active'`). PostgreSQL string comparison is case-sensitive → the probe would have returned 0 rows and the subagent would have incorrectly aborted. Fixed. Dispatch-readiness agent confirmed 9/10 with no other execution-stopping issues; schema-fact-check agent confirmed all other rev-7 claims correct.

**Revision**: 7 — sixth critical-review pass (2026-04-19). Schema fact-check caught **2 more Sev-1 bugs** rev 6 missed: (a) T5 step 6 said `SELECT COUNT(*) FROM hindcasts` — table doesn't exist; actual is `hindcast_forecasts` (parent) + `hindcast_values` (child) per `src/sapphire_flow/db/metadata.py:709`; corrected. (b) T5 step 5 trigger-params table said `run-hindcast` with `station_id: None` means "all onboarded stations" — actually `run_hindcast.py:164-165` raises `ValueError("Either station_id or group_id must be provided")`; subagent must loop per-station with a concrete `station_id`. Reshaped T5: added step 5.5 (probe DB for full `(station_id, artifact_id)` list BEFORE step 6) + changed step 6 to loop per station. **Robustness**: (c) T0 `.env` setup heredoc hardened with `set -euo pipefail` and explicit `exit 2` on template-missing (the `|| echo "..."` fail-open was silently advisory). (d) T5 step 2 jq verify split into two assertions (positive: bind exists; negative: no `type=volume` for `/data/raw`) — old single-check could false-pass if a residual volume survived partial T3. (e) T5 step 5 adds one-line note: pass UUIDs as `str`, not `uuid.UUID(...)` — Prefect params JSON-serialise, UUID raises TypeError. (f) T6 `git pull --ff-only` failure path now says `git stash push -u -m 'plan-060-T6-ff-fail'` before handing back — preserves 25 uncommitted edits for orchestrator recovery. **Ordering**: (g) T3 step-numbering note promoted to top of T3 with explicit "(order N)" suffixes on each step header.

**Revision**: 6 — fifth critical-review pass (2026-04-18) caught 2 rev-5-introduced Sev-1 factual bugs plus housekeeping. **Sev-1 fixes**: (a) T5 step-7 + step-8 SQL probes used Python-style triple single-quotes `'''discharge'''` which are not shell-inside-`psql -c`-double-quote legal; corrected to single-quotes `'discharge'`. (b) T5 step-8 probe queried `SELECT station_id FROM stations` — `stations` table PK is `id` not `station_id`; corrected to `SELECT id AS station_id FROM stations`. (c) T5 trigger-params table entry for `run-hindcast` used `station_ids` (plural, doesn't exist) — actual signature at `run_hindcast.py:128-147` is `station_id: StationId | None = None` (singular) AND requires `artifact_id: ArtifactId` (no default); corrected. **T6 staging**: (d) `.env.example` was missing from the stage list despite T3 step 3 writing it; added. (e) `tests/unit/flows/` was in the stage list but no T-step modifies tests → scope-creep leak risk; removed. **Consistency**: (f) Dep-graph JSON `"tasks"` array previously listed T1-T6 only; now includes T0, T1b, T7. (g) T3 step-2 label confusion ("only AFTER step 1 is saved ... AND Step 3") clarified — ordering is `dev-overlay → .env.example → base-compose removal`. **T0 circularity**: (h) `.env` fallback to `.env.example` was circular because T3 adds CAMELS_CH_HOST_DIR to `.env.example` (pre-T3 the template is silent). Updated T0 to note orchestrator pre-stages `.env` BEFORE dispatch — the fallback is purely for documentation, not for subagent auto-create.

**Revision**: 5 — fourth critical-review pass (2026-04-18). **Sev 1 fixes**: (a) T3 atomicity — reversed step order so dev-overlay write comes BEFORE base-compose removal (partial-crash can no longer leave `/data/raw` unmounted); (b) T5 step 2 verify grep rewritten to `jq`-based check on `docker compose config --format json` — the old `grep` would false-positive pass even with the named volume still present; (c) `.env.example` adds an explicit "DO NOT quote the value" warning — Docker Compose reads `.env` literally and quoted paths become mount failures. **Executability**: (d) Added T0 preconditions section — branch, working-tree state, Docker-stack state, `.env` state. (e) T5 step 5 trigger-params table for all 7 deployments triggered (previously only showed 2). (f) T5 step 7 SQL probe embedded literally (`SELECT station_id, model_id, id AS artifact_id, parameter FROM model_artifacts LIMIT 1;`) so subagent doesn't reverse-engineer schema. (g) T6 explicit staging enumeration (forbid `git add -A` — avoids sweeping parallel workstream's `.claude/`, `performance_baseline.json`, plan drafts). (h) T6 adds `git pull --ff-only` protocol at start and before commit (main may have moved). (i) T7 fully spelled out — `git mv` target, commit message, second bump. **Housekeeping**: (j) Exit Gate 2 cap set expanded per-service (postgres has DAC_OVERRIDE too). (k) Exit Gate 6 `compute-combined-skills` wording changed from "real data side effects" to "bootstrap fires; COMPLETED state" (step 8 may legitimately be a no-op). (l) Dep-graph "T1-T3 parallelisable" softened — T2 and T3 both touch `docker-compose.yml` and `security.md`; single-subagent serial execution sidesteps, but the claim is no longer factually wrong. (m) Post-T5 text corrected: "A3 resumes at **step 8+9** (forecast-cycle direct-invoke + API spot checks)" — T5 itself re-runs steps 2-7 on a wiped stack.

**Revision**: 4 — third critical-review pass (2026-04-18) folded in 2 consistency + 6 system-impact findings. Exit Gate 3 and Files-to-modify table now list all T3 targets (incl. `docs/standards/cicd.md:38` and orphan `sapphire_data:` top-level removal). Added T3 step 6: remove `/data/raw` from `docker/entrypoint.sh:27`'s chown line + update comment (the chown was root-owned at boot; with T3's `:ro` bind-mount it fails EROFS silently — not breaking, but the entrypoint comment now contradicts reality). Added T3 step 7: `.gitignore` must cover `.data/` (the new default CAMELS-CH host path) to prevent accidental 786 MB commit. Added T4 bullet: Plan 046 Stream C2 mac-mini overlay MUST spec a `/data/raw` mount (bind-mount or named volume) — Plan 060 removes the base-compose `sapphire_data` mount and C2 has no replacement, so Plan 046 D2 would otherwise fail. Added `.env.example` clarification: the bind-mount target must contain an uppercase `CAMELS_CH/` subdirectory (container reads `/data/raw/CAMELS_CH/...`). Added T6 commit-message bullet: migration note for pre-060 dev boxes (`docker volume rm <project>_sapphire_data`). Empirical re-verification (Agent 2) confirmed zero @task line-number drift despite 6 Plan-055 T2 commits landing during the review cycle.

**Revision**: 3 — second critical-review pass (2026-04-18) caught three blocking issues in rev 2 plus housekeeping: (a) **T4 step-8 template adapter constructor was missing `scratch_path: Path`** (4th required kw-only arg per `MeteoSwissNwpAdapter.__init__` at `adapters/meteoswiss_nwp.py:83-91`) — added. (b) **T4 template used `asyncio.run(run_forecast_cycle_flow.fn(...))`** but the flow is sync (`def`, not `async def`) — removed the wrapper. (c) **T3 step 1 left orphan `sapphire_data` top-level declaration** at `docker-compose.yml:~227` and `cicd.md:38` row — both now cleanup-scoped. (d) T1b insertion point pinned to "between `## Concurrency controls` (L172) and `## Deployment registration` (L189)". (e) T2 `security.md` insertion point pinned to "after line 293, before `### Entrypoint pattern` at L295". (f) T3 docstring target pinned to `_download_task`'s docstring specifically. (g) T5 step 2 adds `docker compose config` merge-verification gate. (h) T5 step 0 (precondition) pins CAMELS-CH staging choice — `.env` override to orchestrator's host path, no data duplication. (i) T5 step 5 embeds the canonical trigger-loop Python template. (j) Stale Risks row on "per-site cache_policy check" rephrased.

**Revision**: 2 — fold in three-agent critical review (2026-04-18). Key corrections: (a) `onboard_model.py` has 9 @task sites, not 4 — actual counts for T1 per Agent-C inventory (23 @task decorators total across 7 lifecycle flow files; backup.py's 2 added for defensive NO_CACHE consistency = 25 total). (b) `CHOWN` + `FOWNER` already present in working-tree `docker-compose.yml` on all 4 services — T2 reframed as commit-only + security.md doc update, not a code edit. (c) `init` service does NOT mount `/data/*` — cap_add CHOWN/FOWNER on init is dead code; leaving for forward-proofing and to keep all four services aligned. (d) Only `prefect-worker` currently mounts `/data/raw` — T3 bind-mount scope corrected to worker only. (e) Compose-overlay `volumes:` list-merge behaviour is undefined for duplicate container-path targets on modern Compose; T3 restructured to remove the `sapphire_data:/data/raw:rw` base entry and let the dev overlay declare the bind-mount cleanly — verify with `docker compose config` before T5. (f) T4 `model_id → model_ids` rename target doesn't exist in Plan 046 §A3; drop the rename, keep the T5 trigger-command doc correction only. (g) Default CAMELS-CH host path changed from `bea`-specific to `./.data/camels_ch` project-relative + documented override. (h) Add T1b: `docs/standards/orchestration.md` § Caching posture to enshrine the NO_CACHE convention. (i) Add T3 note on `onboard.py:_download_task` incompatibility with `:ro` overlay. (j) Add C4 runbook bullet to T4 + second-rebase note for `staging-5-stations`.

**Date**: 2026-04-18
**Depends on**: Plan 059 DONE (bootstrap wiring), Plan 056 DONE (zarr-python 3).
**Blocks**: Plan 046 A3 steps 4-9 (train-models, run-hindcast, compute-skills, compute-combined-skills, forecast-cycle, API spot checks). A3 steps 1-3 already verified working on the running compose stack.
**Scope**: Close the cluster of systematic Prefect 3 + deployment-path compat gaps surfaced during A3 step-4 execution (train-models). Five targeted fixes; all are pattern-replication or one-line config changes, no behaviour changes beyond "the Prefect deployment worker can now run these flows against real data."

---

## Context

### Why now

Plan 046 A3 began execution on 2026-04-18 against the running compose stack with 5 stations onboarded. The first four A3 findings landed inline (healthchecks, FI removal, zarr migration, afrom_source, asyncpg→psycopg, Plan 059 bootstrap). Step 4 (`train-models`) now reports `COMPLETED` but silently trains **zero models** — the `determine-scope` task crashes on Prefect 3's cache-key hashing of its store-typed inputs, and the flow swallows the failure.

Further A3 execution will hit the same pattern in `run-hindcast`, `compute-skills`, `compute-combined-skills`, and `forecast-cycle` — they all pass stores to `@task`-decorated functions. The `forecast-cycle` path is additionally blocked by the same adapter-injection concern Plan 059 flagged for `onboard-model`'s `forcing_source`.

Beyond Prefect 3 compat, A3 surfaced two deployment-provisioning gaps: (a) the `/data/raw` named volume is empty on first boot (no CAMELS-CH data), and (b) the `/data/artifacts` + `/data/backups` volumes are root-owned despite the entrypoint's `chown app:app`, because `cap_drop: [ALL]` strips `CAP_CHOWN` and the entrypoint's fallback swallow (`|| true`) hides the failure.

These are all real operational-deployment issues. Fixing them in one coordinated plan is cheaper than scattering inline commits across A3 steps.

### Inputs (verified)

- `train-models` trigger run `37c8fb41-9eb5-49a4-8577-7c4c4e231ad3` crashed with `HashError: JSON error: Unable to serialize unknown type: <class 'sapphire_flow.store.station_group_store.PgStationGroupStore'>. Pickle error: cannot pickle 'weakref.ReferenceType' object` at `prefect/cache_policies.py:386 → prefect/task_engine.py:282`.
- A3 steps 1-3 **already working** against the running stack post-Plan-059.
- The CHOWN/FOWNER cap_add edit is currently in `docker-compose.yml` on the working tree (not yet committed); without the commit, a future fresh-compose boot will reproduce the permission-denied pattern.
- CAMELS-CH source directory at host path `/Users/bea/Library/Application Support/sapphire-flow/raw/CAMELS_CH` (786 MB). Currently `docker cp`'d into the running worker's `/data/raw` — a one-off workaround, not persistent across `down -v`.
- `train_models_flow` signature takes `model_ids: list[str]`, not `model_id`. Plan 046 §A3 step 4 references a prior version's parameter name.

### Problem statement

1. Prefect 3's default `cache_policy` attempts to hash every task input to compute a cache key. SAPPHIRE store classes (`PgModelStore`, `PgStationStore`, `PgStationGroupStore`, `PgObservationStore`, etc.) hold `sa.Connection` references — neither JSON nor pickle serialisable. Tasks that accept store params crash on `HashError` before executing. The error propagates through `compute_transaction_key` → `ValueError`, which the flow's `@task` wrapper catches and maps to a task failure state, which the flow may or may not propagate depending on the retry/error policy.
2. `onboard_model_flow`, `train_models_flow`, `run_hindcast_flow`, `compute_skills_flow`, `compute_combined_skills_flow`, and `run_forecast_cycle_flow` all have internal `@task`s that accept stores. Only one has been hit (`determine-scope` in train-models); the others will hit the same class of error in A3 steps 5-8.
3. `forecast-cycle` additionally requires an `adapter` (`MeteoSwissNwpAdapter` or equivalent) that cannot be passed through Prefect deployment parameters (not JSON-serialisable). Plan 059 noted this deferral for `onboard_model_flow.forcing_source`; the same applies here.
4. `/data/artifacts` + `/data/backups` + `/data/raw` volumes start with `root:root` ownership. The entrypoint's `chown app:app /data/backups /data/artifacts /data/raw 2>/dev/null || true` fails silently with `Operation not permitted` because `cap_drop: [ALL]` without `CHOWN` in `cap_add` blocks the syscall even for UID 0.
5. `/data/raw` needs CAMELS-CH static attributes + timeseries + basin attributes for onboard-stations to work. No provisioning mechanism exists — A3 used a one-off `docker cp`.

---

## Architecture decisions

| # | Decision | Rationale |
|---|---|---|
| D1 | **Blanket `cache_policy=NO_CACHE` on every `@task` in `src/sapphire_flow/flows/*.py`.** Stores, adapters, and DeploymentConfig are common task inputs across all lifecycle flows; they all fail the cache-key hasher; caching gains nothing for an operational pipeline where deterministic re-runs aren't desired. Extend defensively to `backup.py`'s two @tasks (no store inputs, so no HashError risk, but aligns the invariant "every lifecycle @task has a cache policy"). | Simplest correct pattern. Matches the current reality: zero @tasks in the 7 lifecycle files have a cache policy set, so the blanket add is strictly additive. `determine-scope` at `train_models.py:78-89` takes 3 store inputs and fails — that's enough to trigger the HashError; broader inventory (23 @tasks total, per Agent-C review, plus backup's 2) documents the scope. Alternative `cache_key_fn` excluding stores is fragile — every new task must remember; blanket NO_CACHE is safe. |
| D2 | **`CHOWN` + `FOWNER` are already present in the working tree on all 4 services (postgres, prefect-worker, api, init).** T2 formalises the commit + updates `security.md` § Container privilege model to document the cap_add pattern (which currently is silent on any cap_add). `init` mounts only `./config.toml` — CHOWN+FOWNER on init is technically dead code but kept for parity across the 4 services and forward-proofing in case init acquires additional mounts later. | Pattern consistency + minimum surprise. `security.md:291` currently prescribes `cap_drop: [ALL]` but does not document any cap_add. T2's security.md edit adds that sub-section with per-cap justifications (CAP_CHOWN for named-volume chown; CAP_FOWNER for chown over non-empty volumes; SETUID/SETGID for entrypoint's `gosu app` user drop). |
| D3 | **Replace — not append — the `/data/raw` mount in dev by removing `sapphire_data:/data/raw:rw` from `docker-compose.yml` worker spec and adding a bind-mount `${CAMELS_CH_HOST_DIR:-./.data/camels_ch}:/data/raw:ro` in `docker-compose.dev.yml`.** `mac-mini` and `prod` overlays will carry a separate named-volume or bind-mount spec as appropriate. Default host path is project-relative (`./.data/camels_ch`) so a fresh-clone contributor discovers the missing-data fail-mode immediately; the actual CAMELS-CH location is per-developer via the env var. | Docker Compose overlay merge for `volumes:` lists is **append**; duplicate container-path targets are undefined by spec and have last-wins fragility on Docker Desktop. Moving the base mount out is cleaner than relying on the fragile override. Project-relative default prevents a machine-specific path from living in a tracked file. `security.md:330`'s "named volumes for persistent data" rule covered by a new one-line carve-out for static read-only reference datasets. |
| D4 | **Defer the adapter-registry concern for `forecast-cycle`** (inherits Plan 059's same deferral for `onboard-model`'s `forcing_source`). For A3 step 8, invoke `run_forecast_cycle_flow.fn(...)` directly from a Python script inside the worker container with an injected adapter. A3's exit gate is satisfied by reaching `forecast.run_completed` with valid fields — identical code path to a Prefect-triggered flow run. **Consequence**: C4 runbook must note that `forecast-cycle`, non-empty `onboard-model`, and non-empty `train-models` need `docker compose exec prefect-worker python -c ...` invocation, not Prefect UI trigger. T4 captures the C4 input. | Adapter-registry is a substantial design task (future plan — 061+). A3 dress rehearsal doesn't need it. |
| D5 | **Drop the Plan 046 §A3 `model_id → model_ids` rename**. Agent-C verification: Plan 046 §A3 contains no parameter-name text to rename — line 129's `train-models — linear regression, 5 stations` has no param text at all. T4 instead records the correct trigger-command templates inline (step-4: `{"model_ids": ["linear_regression_daily"]}`) and adds the second-rebase note for `staging-5-stations`. | Keep T4 honest — don't rename text that doesn't exist. |
| D6 | **Land a one-paragraph § Caching posture in `docs/standards/orchestration.md`** in T1b. The NO_CACHE convention is orchestration doctrine; documenting only in 060's plan file means a future @task author has no standard to consult. | Prevents convention rot. Short paragraph: "default `cache_policy=NO_CACHE` on all lifecycle-flow @tasks; stores and adapters are not hash-serialisable; explicit `cache_key_fn` only with reviewed justification." |
| D7 | **`CAMELS_CH_HOST_DIR` env var → `docs/conventions.md § Environment variables`** (not cicd.md — canonical inventory lives in conventions.md per Agent-B finding). | Matches existing convention; keeps env-var inventory in a single authoritative location. |

---

## Phase ladder

### T0 — Preconditions (subagent verifies before any edit)

- **Branch**: `main`. `git status --short` shows only the expected uncommitted edits listed below; anything else is a parallel workstream and must be `git stash push --include-untracked -m 'plan-060-parallel-stash' -- <path>` or explicitly confirmed-and-committed by orchestrator before T1. Specifically expected on main at T0:
  - `docker-compose.yml` expected CLEAN (no `M`). Commit `289c5f8` (2026-04-19) already merged the CHOWN + FOWNER cap_add additions into main — T2's scope is now security.md-only.
  - Possibly `M uv.lock` — version drift only; include in T6.
  - Untracked: `.claude/`, `tests/fixtures/reference/performance_baseline.json`, other plan-draft `.md` files — **do not stage these in T6**.
- **Docker stack**: currently running with 5 services healthy. T5 does its own `down -v` + `up -d`; no pre-teardown needed.
- **`.env` file** (reproducible from a fresh clone — no orchestrator hand-wave): must exist and contain `CAMELS_CH_HOST_DIR` pointing at a host directory whose contents include an uppercase `CAMELS_CH/` subdirectory with `static_attributes/`, `timeseries/`, etc.

  **Setup procedure** (do this if `.env` is absent OR lacks `CAMELS_CH_HOST_DIR`):

  ```bash
  set -euo pipefail  # any unhandled failure aborts T0 immediately

  # 1. .env.example must exist (tracked template). If it doesn't, the repo is broken.
  test -f .env.example || { echo "ERROR: .env.example missing from repo root — corrupt clone"; exit 2; }

  # 2. If .env is missing entirely, seed it from the template.
  test -f .env || cp .env.example .env

  # 3. Template must carry the CAMELS_CH_HOST_DIR doc block.
  #    BEFORE Plan 060 T3 step 3 lands, .env.example has only DB_PASSWORD / DB_USER.
  #    On a fresh clone BEFORE Plan 060 T3 lands, the operator must add the line
  #    to .env (NOT .env.example) manually. Fail loudly rather than advising.
  if ! grep -q '^# CAMELS_CH_HOST_DIR=' .env.example; then
      echo "ERROR: .env.example predates Plan 060 T3 step 3 — it does not document CAMELS_CH_HOST_DIR."
      echo "Add a line of the form"
      echo "  CAMELS_CH_HOST_DIR=/absolute/path/to/camels_ch_parent"
      echo "to your .env file (NOT .env.example). The target directory must contain an"
      echo "uppercase CAMELS_CH/ subdirectory with static_attributes/, timeseries/, etc."
      echo "Do NOT quote the value. Paths with spaces work without quotes."
      exit 2
  fi

  # 4. .env must carry the actual value.
  if ! grep -q '^CAMELS_CH_HOST_DIR=' .env; then
      echo "ERROR: .env is missing CAMELS_CH_HOST_DIR. Append a line of the form:"
      echo "  CAMELS_CH_HOST_DIR=/absolute/path/to/camels_ch_parent"
      echo "to .env (no quotes). Path must contain an uppercase CAMELS_CH/ subdir."
      exit 2
  fi
  echo "T0 .env precondition: OK"
  ```

  **Subagent behaviour**: run the full block as a single `Bash` invocation. If it exits non-zero, STOP the plan execution and surface the error block to the human operator — do NOT continue to T1. Zero orchestrator hand-holding required; any future human running Plan 060 on a fresh clone sees the same reproducible setup.
- **Main not moved since session start**: `git fetch && git status` — if `origin/main` is ahead, `git pull --ff-only` before starting.
- **Precondition grep** — zero existing `cache_policy=` kwargs in `src/sapphire_flow/flows/*.py`: `grep -rn 'cache_policy' src/sapphire_flow/flows/` MUST return zero matches. If any match exists (late-arriving Plan-055 commit), stop and report.

### T1 — Blanket `cache_policy=NO_CACHE` across lifecycle flow tasks

Authoritative @task inventory (via critical-review Agent C, 2026-04-18 — none of the 25 sites below currently have any `cache_policy` kwarg):

| File | Lines (of `@task(...)` decorators) | Count |
|---|---|---|
| `src/sapphire_flow/flows/onboard_model.py` | 66, 94, 187, 226, 231, 274, 324, 343, 362 | 9 |
| `src/sapphire_flow/flows/train_models.py` | 78, 103, 142, 156 | 4 |
| `src/sapphire_flow/flows/run_hindcast.py` | 26, 68 | 2 |
| `src/sapphire_flow/flows/compute_skills.py` | 66, 153 | 2 |
| `src/sapphire_flow/flows/onboard.py` | 26 | 1 |
| `src/sapphire_flow/flows/ingest_observations.py` | 101, 110, 119 | 3 |
| `src/sapphire_flow/flows/run_forecast_cycle.py` | 84, 240 | 2 |
| `src/sapphire_flow/flows/backup.py` | 42, 88 | 2 (defensive NO_CACHE — no store inputs, no HashError risk, but keeps the invariant simple) |
| **Total** | | **25** |

Add `cache_policy=NO_CACHE` to each `@task(...)` decorator. `NO_CACHE` is imported from `prefect.cache_policies` (verified at `prefect==3.6.23` in `uv.lock:2528`):

```python
from prefect.cache_policies import NO_CACHE

@task(
    name="determine-scope",
    task_run_name="determine-scope-{model_id}",
    cache_policy=NO_CACHE,
)
def _determine_onboarding_scope_task(...): ...
```

Verification done by reviewer: all 25 sites are clean (zero existing `cache_policy=` kwargs in `src/sapphire_flow/flows/`). Subagent can proceed with blanket addition without per-site conditional checks.

### T1b — Document the NO_CACHE convention in `docs/standards/orchestration.md`

Append a short § Caching posture to `orchestration.md` that captures the decision and rationale for future @task authors. Proposed text:

> ## Caching posture
>
> All lifecycle-flow `@task` decorators default to `cache_policy=NO_CACHE` (imported from `prefect.cache_policies`). Prefect 3's default cache policy attempts to hash every task input to compute a cache key; SAPPHIRE stores (PgStore subclasses) hold SQLAlchemy `Connection` references that are neither JSON nor pickle serialisable, so default caching crashes with `HashError`. Operational pipelines rarely hit cache anyway (each run carries distinct `cycle_time` / `period_start` / `station_id` parameters).
>
> A targeted `cache_key_fn` excluding stores is only justified for a pure-compute @task with a small, hashable input set and a demonstrated recompute cost — add on a per-task basis with review.

One-line cross-reference from Plan 060 in the orchestration.md section so readers know which plan landed the convention.

**Insertion point** (verified): between `## Concurrency controls` (line 172) and `## Deployment registration` (line 189). New section is a top-level `## Caching posture` — matches existing heading hierarchy.

### T2 — Document `CHOWN`/`FOWNER` cap_add in `security.md` (docs-only)

Verification (2026-04-19):
- `docker-compose.yml:22-26` — postgres (CHOWN + SETUID + SETGID + FOWNER + DAC_OVERRIDE; committed)
- `docker-compose.yml:84-88` — prefect-worker (SETUID + SETGID + CHOWN + FOWNER; **committed** via `289c5f8` `docs(plan-058/T4)`)
- `docker-compose.yml:131-135` — api (same four; committed via `289c5f8`)
- `docker-compose.yml:201-205` — init (same four; committed via `289c5f8`; CHOWN is dead code here — init mounts only `./config.toml` — but kept for service parity)

**T2 scope shrinks to security.md only** (commit `289c5f8` pre-empted the compose edits):

- Add a new `### Capabilities` sub-section to `docs/standards/security.md` under `## Container privilege model` (which starts at line 285). Insertion point: immediately after the closing bullet of the current top-level list at line 293, before `### Entrypoint pattern` at line 295. Enumerates the accepted cap_add set across services:
  - SETUID + SETGID — required for `gosu app` user drop in the entrypoint
  - CHOWN — required to chown named-volume mount points at first boot
  - FOWNER — required once volumes accumulate state (`chown` over files with non-root owners)
  - DAC_OVERRIDE — postgres only (pre-existing)
  - NET_BIND_SERVICE — caddy only (ports 80/443)
- Cross-reference this plan (060) as the landing location of the documentation (the cap_add infra itself landed via commit `289c5f8`).

### T3 — CAMELS-CH host-path bind mount for dev (overlay merge-safe, atomic-ordered)

**Two-file change, ORDERED to avoid partial-crash leaving `/data/raw` unmounted**:

Step 1 — **`docker-compose.dev.yml`** FIRST: add a bind-mount for `prefect-worker` only:

```yaml
services:
  prefect-worker:
    volumes:
      - "${CAMELS_CH_HOST_DIR:-./.data/camels_ch}:/data/raw:ro"
```

Path default is project-relative (`./.data/camels_ch`) so a fresh-clone contributor fails fast if they haven't staged CAMELS-CH data there. Developers override with the full path to their local dataset (bea's is `/Users/bea/Library/Application Support/sapphire-flow/raw`) via `.env` or shell env. Path with spaces is fine inside the double-quoted YAML short-form spec.

(Step numbering note: Step 2 below comes AFTER Step 1 overlay write AND Step 3 `.env.example` write — the three are a logical unit. Sub-order within the unit is: (1) overlay, (3) `.env.example`, (2) base-compose removal. Labels kept as 1/2/3 for historical continuity with rev 5's reversal, but the execution order is 1 → 3 → 2 → 4 → 5a/5b → 6 → 7.)

Step 2 — **`docker-compose.yml`** (run AFTER Step 1 and Step 3 both land): remove the worker's `sapphire_data:/data/raw:rw` mount AND the now-orphan top-level `sapphire_data:` entry (currently at `docker-compose.yml:~227` under the `volumes:` block). Grep-verify `sapphire_data` has zero remaining references in `docker-compose*.yml` after removal. **Rationale for ordering**: if the subagent crashes between Step 1 and Step 2, the reversed order leaves `/data/raw` mounted `:ro` from the dev overlay rather than unmounted entirely. The only failure mode (both steps crash) leaves the working tree with an extra mount, which is safe.

Step 3 — **`.env.example`**: add this block:
```
# Dev-only: host path whose CONTENTS are bind-mounted into /data/raw inside the
# prefect-worker container (read-only). The target MUST contain an uppercase
# `CAMELS_CH/` subdirectory — the flow code reads /data/raw/CAMELS_CH/static_attributes/...
# so the bind-mount target is the PARENT of that CAMELS_CH/ dir, not the dir itself.
#
# IMPORTANT: do NOT quote the value. Docker Compose reads .env literally; quotes
# become part of the path string and the mount fails with "invalid mount config".
# Paths with spaces work without quotes.
#
# Case-sensitivity: macOS APFS is case-insensitive by default, but Linux hosts
# (Mac-mini, Nepal) are case-sensitive. The container path is always
# /data/raw/CAMELS_CH (uppercase). Pre-stage that exact casing.
#
# Expected layout under the target:
#   <target>/CAMELS_CH/static_attributes/
#   <target>/CAMELS_CH/timeseries/
#   <target>/CAMELS_CH/catchment_delineations/
#   ...
# CAMELS_CH_HOST_DIR=/path/to/your/camels_ch_parent
```

Step 4 — **`docs/conventions.md § Environment variables`**: add `CAMELS_CH_HOST_DIR` to the canonical inventory with a one-line description.

Step 5a — **`docs/standards/cicd.md:38`**: remove the `sapphire_data` row from the volume table (grep-verify no other docs reference `sapphire_data` after removal; if found, update).

Step 5b — **`docs/standards/security.md:~330`**: add a one-line carve-out to the "named volumes for persistent data" rule:
> Exception: dev-only overlays may bind-mount read-only static reference datasets (e.g. CAMELS-CH via `CAMELS_CH_HOST_DIR`) — the `:ro` mode sidesteps UID-write collisions. Production and staging overlays use named volumes or controlled host paths per the Mac-mini runbook.

**Caveat / incompatibility**: `src/sapphire_flow/flows/onboard.py:26-34` contains `_download_task` which writes under `/data/raw` when `download=True`. With T3's `:ro` overlay that call will fail. Options: (a) document that `download=True` is incompatible with the dev overlay (operator must pre-stage CAMELS-CH on host); (b) gate the download task behind a runtime writability check. Plan 060 takes option (a) — T3 adds a one-line note specifically in **`_download_task`'s docstring** (not the module docstring) pointing at `CAMELS_CH_HOST_DIR` and stating "dev overlay mounts `/data/raw` read-only; `download=True` is incompatible — pre-stage host-side via the env var."

**Mac-mini / prod overlay** is out of scope for Plan 060 (`docker-compose.macmini.yml` edits stay with Plan 046 Stream C2) — BUT because Plan 060 removes the base-compose `sapphire_data:/data/raw:rw` mount, Plan 046's C2 overlay will now have no `/data/raw` mount at all unless C2 adds one. T4 flags this as a blocking prerequisite for Plan 046 D2 (5-station mac-mini operation). Without a C2 `/data/raw` spec, mac-mini `onboard-stations` will hit the same "CAMELS-CH not found" error we hit on dev before A3 step 2.

Step 6 — **`docker/entrypoint.sh:27`** — current line:
```
chown app:app /data/backups /data/artifacts /data/raw 2>/dev/null || true
```
Remove `/data/raw` from the chown list (now `:ro` in dev, operator-staged in prod — the chown either fails silently with EROFS or chowns an empty dir). Update the preceding comment from `"Fix writable data directory ownership"` to `"Fix writable data directory ownership (backups + artifacts only — /data/raw is operator-staged, read-only in dev)"`. The entrypoint's `|| true` already swallowed any EROFS, but the line is misleading and future audits flag it.

Step 7 — **`.gitignore`** — add `.data/` to prevent accidental commit of a 786 MB CAMELS-CH dataset if a developer uses the project-relative default path. Check current `.gitignore`: `data/` (broad) already present at line 21, but `.data/` (dot-prefixed) is not covered by that glob. Add an explicit entry.

### T4 — Plan 046 §A3 doc reconciliation

File: `docs/plans/046-mac-mini-staging-deployment.md`.

- §A3 step 4: **no rename needed** (Agent-C verified the param-name text doesn't exist in the current plan). Instead, append a trigger-command example that uses the correct `model_ids` list form, e.g. `{"model_ids": ["linear_regression_daily"]}`, so future readers don't guess the param name.
- §A3 step 8: replace "trigger forecast-cycle via Prefect UI" with "invoke `run_forecast_cycle_flow.fn(...)` directly from a Python script inside the `prefect-worker` container with an injected `MeteoSwissNwpAdapter`" (per D4 deferral). Use the following template (subagent can adjust bbox / arg set to match actual adapter constructor; the intent is to run the flow body with all stores auto-resolved by Plan 059's bootstrap + a concretely-constructed NWP adapter):

  ```bash
  docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T prefect-worker \
    python -c "
  from pathlib import Path
  import httpx
  from sapphire_flow.adapters.meteoswiss_nwp import MeteoSwissNwpAdapter
  from sapphire_flow.flows.run_forecast_cycle import run_forecast_cycle_flow

  adapter = MeteoSwissNwpAdapter(
      stac_base_url='https://data.geo.admin.ch/api/stac/v1',
      stac_collection='ch.meteoschweiz.ogd-forecasting-icon-ch2',
      scratch_path=Path('/tmp/sapphire_nwp'),
      http_client=httpx.Client(timeout=60),
  )
  result = run_forecast_cycle_flow.fn(adapter=adapter)
  print('forecast-cycle result:', result)
  "
  ```

  Notes verified during critical review:
  - `MeteoSwissNwpAdapter.__init__` at `src/sapphire_flow/adapters/meteoswiss_nwp.py:83-91` requires **4 kw-only args**: `stac_base_url`, `stac_collection`, `scratch_path: Path`, `http_client`. All four present above. `/tmp` is the worker's tmpfs (per `docker-compose.yml` worker spec).
  - `run_forecast_cycle_flow` is **sync** (`def …`, not `async def` — `src/sapphire_flow/flows/run_forecast_cycle.py:281`). Do NOT wrap in `asyncio.run(...)`; call `.fn(...)` directly.
  - The flow body uses Plan 059's bootstrap block to resolve every store from `DATABASE_URL`.
- §A1 / commit-ordering section: add a step 8 to the Commit ordering list — "after Plan 060 archives, re-rebase `staging-5-stations` onto main to pick up the cache_policy / cap_add / dev-overlay changes before resuming A3 step 5." (The branch was rebased once before A3 started; Plan 060 lands during A3, so a second rebase is needed.)
- Stream C / C4 runbook (`docs/deployment/mac-mini-staging.md`): add a "Flows that require direct-invoke rather than Prefect UI trigger" section (content: `forecast-cycle`, non-empty `onboard-model`, non-empty `train-models` — all need `docker compose exec prefect-worker python -c "..."` until an adapter-registry plan lands). Plan 060 DOES NOT write the runbook (that's Plan 046 C4's job), but adds a bullet into Plan 046's C4 description stating this input.
- **Stream C / C2 overlay (`docker-compose.macmini.yml`) BLOCKING for D2**: Plan 060 removes `sapphire_data:/data/raw:rw` from base compose, which means Plan 046 C2's mac-mini overlay now has no `/data/raw` mount at all (current C2 scope covers `/data/backups` USB mount only). Add a bullet to Plan 046 C2's description stating that C2 MUST spec a `/data/raw` mount (either a named `sapphire_data_macmini` volume OR a bind-mount to a host path on the Mac mini where operators pre-stage CAMELS-CH). Without this, D2's `onboard-stations` hits the same "CAMELS-CH not found" error we hit on dev before A3 step 2. This is not a Plan 060 scope edit — it's a Plan 046 C2 precondition update.
- Add a revision note (rev 9) at the top of Plan 046 referencing Plan 060 as the resolver for cache-policy + volume-ownership + CAMELS-CH-data + second-rebase + direct-invoke-forecast-cycle findings.

### T5 — End-to-end validation

After T1-T4 land and the sapphire-flow image is rebuilt:

0. **Precondition**: operator stages CAMELS-CH dataset at `${CAMELS_CH_HOST_DIR}` before `up -d`. For the current dev machine, the orchestrator will set `CAMELS_CH_HOST_DIR=/Users/bea/Library/Application Support/sapphire-flow/raw` in `.env` (the existing dataset location). Subagent can either `cp` into `./.data/camels_ch/` OR rely on the orchestrator's `.env` override — either path works; orchestrator's `.env` is the simpler one, no data duplication. **Verify** `${CAMELS_CH_HOST_DIR}/CAMELS_CH/static_attributes` exists before proceeding.
1. `docker compose -f docker-compose.yml -f docker-compose.dev.yml down -v` (wipes prior state).
2. **Verify merged compose shows ONLY the bind-mount for `/data/raw`** (not the removed named volume). Two assertions — one positive (bind exists), one negative (no residual volume-type mount leaked from a partial T3 step 2). `jq -e` exits 1 on null/empty; we use that for the positive check and an inverted `test` for the negative:
   ```bash
   CONFIG=$(docker compose -f docker-compose.yml -f docker-compose.dev.yml config --format json)
   # Positive: exactly one bind mount on /data/raw
   echo "$CONFIG" | jq -e '.services["prefect-worker"].volumes[] | select(.target == "/data/raw") | select(.type == "bind")' >/dev/null || { echo "FAIL: /data/raw bind-mount absent"; exit 2; }
   # Negative: NO volume-type mount still targets /data/raw
   if echo "$CONFIG" | jq -e '.services["prefect-worker"].volumes[] | select(.target == "/data/raw") | select(.type == "volume")' >/dev/null 2>&1; then
       echo "FAIL: residual named-volume mount on /data/raw — T3 step 2 incomplete"
       exit 2
   fi
   echo "T5 step 2 verify: OK"
   ```
   If either assertion fails, stop and report — T3 was not fully applied.
3. `docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d`.
4. Verify: 5 services healthy, init exit 0, 9 deployments registered, health endpoint green (`curl -s http://localhost:8010/api/v1/health | jq -e '.status == "ok" and .prefect_status == "ok"'`), `/data/raw/CAMELS_CH` populated (via dev overlay bind mount — `docker compose exec -T prefect-worker ls /data/raw/CAMELS_CH/static_attributes | head`).
5. Re-run A3 steps 2-4 via the deployment-trigger API. **Trigger pattern** (use as the canonical template — same as the orchestrator used for A3 step 1; also visible in `git log --grep 'feat(plan-059)'` session transcripts):

   ```python
   # In a heredoc on the orchestrator's host:
   PREFECT_API_URL=http://localhost:4200/api uv run python3 << 'EOF'
   import asyncio, time
   from prefect.client.orchestration import get_client
   TERMINAL = {"COMPLETED", "FAILED", "CRASHED", "CANCELLED"}
   async def run_dep(c, name, params=None, timeout=240):
       deps = await c.read_deployments()
       dep = next(d for d in deps if d.name == name)
       fr = await c.create_flow_run_from_deployment(dep.id, parameters=params or {})
       t0 = time.time()
       while time.time() - t0 < timeout:
           s = (await c.read_flow_run(fr.id)).state
           stype = s.type.value if s else "PENDING"
           if stype in TERMINAL:
               return {"state": stype, "dur": round(time.time()-t0,1), "msg": (s.message or "")[:250]}
           await asyncio.sleep(3)
       return {"state": "TIMEOUT"}
   async def main():
       async with get_client() as c:
           r = await run_dep(c, "onboard-stations", timeout=240)
           print(f"onboard-stations: {r}")
           # ... repeat for each A3 step
   asyncio.run(main())
   EOF
   ```

   **Trigger parameters table** (authoritative; subagent uses these in `run_dep(c, name, params)` calls):

   | Deployment | Trigger params |
   |---|---|
   | `onboard-model` | `{"model_id": "linear_regression_daily"}` (repeat for `climatology_fallback`, `persistence_fallback`) |
   | `onboard-stations` | `{}` (no params) |
   | `ingest-observations` | `{}` |
   | `train-models` | `{"model_ids": ["linear_regression_daily"]}` (plural list) |
   | `run-hindcast` | Per-station loop (not a single trigger). `run_hindcast_flow` at `run_hindcast.py:164-165` raises `ValueError("Either station_id or group_id must be provided")` when both are None. Subagent runs the step-5.5 probe first (see below) to get a list of `(station_id, artifact_id)` tuples (one per onboarded station), then issues one `run_dep(c, "run-hindcast", {"model_id": "linear_regression_daily", "artifact_id": <str>, "station_id": <str>})` per tuple. Pass UUIDs as Python `str` (not `uuid.UUID(...)` — Prefect deployment params JSON-serialise and raw UUID objects raise `TypeError: Object of type UUID is not JSON serializable`). |
   | `compute-skills` | 4-tuple from DB query below |
   | `compute-combined-skills` | 3-tuple from DB query below |
   | `backup-database` | `{}` |

   **Expected outcomes per step**:
   - `onboard-stations` — must COMPLETE and create 5 stations. Table check: `SELECT COUNT(*) FROM stations;` → 5.
   - `ingest-observations` — must COMPLETE.
   - `train-models` — must COMPLETE and store ≥ 1 `model_artifacts` row per station. Table check: `SELECT COUNT(*) FROM model_artifacts WHERE model_id = 'linear_regression_daily';` → 5 expected.
5.5 **Step 5.5 (pre-probe for step 6 + step 7)** — after `train-models` COMPLETED, pull **all** `(station_id, artifact_id)` tuples for the linear regression model. This list drives step 6's per-station hindcast loop and feeds step 7:
   ```sql
   SELECT station_id, id AS artifact_id
   FROM model_artifacts
   WHERE model_id = 'linear_regression_daily'
     AND status = 'active';  -- lowercase per ModelArtifactStatus enum + CheckConstraint at metadata.py:460-468
   ```
   Run via `docker compose exec -T postgres psql -U sapphire sapphire -t -A -c "..."`. Expect 5 rows (one per onboarded station). Parse the `|`-separated output, keep UUIDs as `str`. If < 5 rows, train-models produced partial output — stop and report.
6. `run-hindcast` — **loop**: for each `(station_id, artifact_id)` from step 5.5, issue one trigger. Each must COMPLETE. Table check after the loop: `SELECT COUNT(*) FROM hindcast_forecasts;` → > 0 (parent table; `hindcast_values` carries the fanned-out ensemble rows).
7. **`compute-skills`** — must COMPLETE and store `skill_scores` rows. Pull the required 4-tuple from the DB first (use this tuple for both `run-hindcast` at step 6 and `compute-skills` here):
   ```sql
   SELECT station_id, model_id, id AS artifact_id, 'discharge' AS parameter
   FROM model_artifacts
   WHERE model_id = 'linear_regression_daily'
   LIMIT 1;
   ```
   Run via: `docker compose exec -T postgres psql -U sapphire sapphire -t -A -c "SELECT station_id, model_id, id AS artifact_id, 'discharge' AS parameter FROM model_artifacts WHERE model_id = 'linear_regression_daily' LIMIT 1;"`. Shell passes the double-quoted string literally to psql; SQL uses single-quotes for string literals — no triple-quote needed. Parse the `|`-separated output for the 4 values and use as `run_dep(c, "compute-skills", {...})` parameters.
8. **`compute-combined-skills`** — must COMPLETE. Pull tuple (note: the `stations` table PK is `id`, not `station_id` — alias for clarity):
   ```sql
   SELECT id AS station_id, 'discharge' AS parameter, 'bma' AS strategy
   FROM stations LIMIT 1;
   ```
   Run via: `docker compose exec -T postgres psql -U sapphire sapphire -t -A -c "SELECT id AS station_id, 'discharge' AS parameter, 'bma' AS strategy FROM stations LIMIT 1;"`. May be effectively a no-op if no combination models are assigned — still expects bootstrap path to fire and flow to reach COMPLETED state.
9. `backup-database` — must COMPLETE and write a `.dump` file to `/data/backups` (no Permission denied). Verify: `docker compose exec -T prefect-worker ls -la /data/backups/` shows a new `.dump` file.
10. For A3 step 8 (forecast-cycle per Plan 046 §A3): **NOT part of T5's gate**; the orchestrator handles forecast-cycle separately via the T4 direct-invoke template on D4 deferral.

### T6 — Commit + bump + tag

**Pre-commit**: this repo has **no `origin` remote** at the time of writing (`git remote -v` returns empty). `git fetch && git pull --ff-only` are no-ops but harmless. Use `git log --oneline -5 HEAD` to check for recent local commits instead. If a remote is added later, the fetch/pull protocol resumes: if ff-fails, run `git stash push -u -m 'plan-060-T6-ff-fail'` first to preserve unstaged edits, then STOP and hand back to orchestrator with the stash reference. Do NOT rebase or merge automatically.

- `uv run ruff format` + `uv run ruff check --fix` on all touched files.
- `uv run pytest tests/ -q` — must be green at or above pre-060 count (1164+).
- `uv run bump-my-version bump patch` (read `pyproject.toml` at exec time — last tagged version at rev-9 review time was `v0.1.323`; version may have moved since).
- `uv sync` to refresh `uv.lock`.
- **Stage — explicit file list** (DO NOT use `git add -A` — would sweep parallel workstream files like `.claude/`, `performance_baseline.json`, and untracked plan drafts into this commit):
  ```bash
  git add \
    src/sapphire_flow/flows/onboard_model.py \
    src/sapphire_flow/flows/train_models.py \
    src/sapphire_flow/flows/run_hindcast.py \
    src/sapphire_flow/flows/compute_skills.py \
    src/sapphire_flow/flows/onboard.py \
    src/sapphire_flow/flows/ingest_observations.py \
    src/sapphire_flow/flows/run_forecast_cycle.py \
    src/sapphire_flow/flows/backup.py \
    docker-compose.yml docker-compose.dev.yml docker/entrypoint.sh \
    .env.example .gitignore \
    docs/standards/orchestration.md docs/standards/security.md docs/standards/cicd.md \
    docs/conventions.md \
    docs/plans/046-mac-mini-staging-deployment.md \
    pyproject.toml src/sapphire_flow/__init__.py uv.lock
  ```
  (Plan 060 does NOT modify tests; `tests/unit/flows/` is intentionally absent from this list. If T5 surfaces a test-worthy regression, capture it in a follow-up plan.)
  After `git add`, run `git status --short` — every `M ` or `A ` entry must be one of the paths above; any other `M ` entry is a parallel workstream leak and must be `git restore --staged` to unstage.
- Single feat commit with message `feat(plan-060): wire Prefect/deployment compat for A3 lifecycle flows`. Include a migration-note bullet in the commit body:
  ```
  Migration: pre-060 dev boxes carry an orphan Docker volume. Discover the
  exact name with:
    docker volume ls | grep sapphire_data
  and reclaim disk via:
    docker volume rm <discovered_name>
  ```
- `git tag v$(uv run bump-my-version show current_version)`. If tag collision (local branch tagged the same version), use the chore-bump pattern from earlier session commits: `uv run bump-my-version bump patch` + commit `chore(version): bump X → Y` + tag that second commit.

### T7 — Archive

Separate commit after T6 lands cleanly:

```bash
git mv docs/plans/060-a3-prefect-deployment-compat-sweep.md docs/plans/archive/060-a3-prefect-deployment-compat-sweep.md
uv run bump-my-version bump patch  # yes, every commit bumps per CLAUDE.md
uv sync
git add docs/plans/archive/060-a3-prefect-deployment-compat-sweep.md pyproject.toml src/sapphire_flow/__init__.py uv.lock
git commit -m "docs(plan-060): archive completed plan"
git tag v$(uv run bump-my-version show current_version)
```

Handle tag collision with the same chore-bump pattern if needed.

---

## Dependency graph

```json
{
  "plan-060": {
    "tasks": ["T0", "T1", "T1b", "T2", "T3", "T4", "T5", "T6", "T7"],
    "parallel": {"single-subagent serial execution T0→T1→T1b→T2→T3→T4→T5→T6→T7 is the canonical path. T1-T3 touch overlapping files (docker-compose.yml, security.md, onboard.py) so they cannot parallelise safely in practice; serial order is cheap. T4 depends on T1's fact-check; T5 depends on T1+T2+T3 landed + image rebuilt; T6 depends on T5 green."},
    "depends_on": []
  }
}
```

Single-subagent pass; estimate 60-90 min end-to-end including T5 validation on the live stack.

---

## Files to modify

| Path | Task | Change |
|---|---|---|
| `src/sapphire_flow/flows/onboard_model.py` | T1 | Add `cache_policy=NO_CACHE` to each `@task` decorator |
| `src/sapphire_flow/flows/train_models.py` | T1 | Same |
| `src/sapphire_flow/flows/run_hindcast.py` | T1 | Same |
| `src/sapphire_flow/flows/compute_skills.py` | T1 | Same |
| `src/sapphire_flow/flows/onboard.py` | T1 | Verify + add where missing |
| `src/sapphire_flow/flows/ingest_observations.py` | T1 | Verify + add where missing |
| `src/sapphire_flow/flows/run_forecast_cycle.py` | T1 | Verify + add where missing |
| `src/sapphire_flow/flows/backup.py` | T1 | Add `cache_policy=NO_CACHE` to its 2 @tasks (defensive, invariant-preserving) |
| `docker-compose.yml` | T3 only | T3: remove `sapphire_data:/data/raw:rw` from `prefect-worker` volumes list AND the orphan top-level `sapphire_data:` entry from the `volumes:` block. (T2's compose edits were already merged via `289c5f8`; T2 is now docs-only.) |
| `docs/standards/cicd.md` | T3 | Remove `sapphire_data` row from the volume table at line ~38 |
| `docker/entrypoint.sh` | T3 | Remove `/data/raw` from the chown list on line 27; update comment to reflect backups + artifacts only |
| `.gitignore` | T3 | Add `.data/` to cover the project-relative CAMELS-CH default path |
| `docker-compose.dev.yml` | T3 | Add CAMELS-CH bind-mount to `prefect-worker` only (env-var driven, project-relative default) |
| `.env.example` | T3 | Document `CAMELS_CH_HOST_DIR` |
| `docs/conventions.md` | T3 | Add `CAMELS_CH_HOST_DIR` to § Environment variables |
| `docs/standards/security.md` | T2 + T3 | T2: new § Container privilege model sub-section enumerating cap_add set. T3: one-line carve-out to the "named volumes for persistent data" rule for dev-only static read-only reference datasets |
| `docs/standards/orchestration.md` | T1b | New § Caching posture paragraph |
| `src/sapphire_flow/flows/onboard.py` | T3 | One-line docstring note that `_download_task` is incompatible with the dev overlay's `:ro` CAMELS-CH mount (operator pre-stages host-side) |
| `docs/plans/046-mac-mini-staging-deployment.md` | T4 | Rev 9 note + §A3 step 4 trigger-command example + §A3 step 8 direct-invoke template + §A1 commit-ordering second-rebase step + Stream C4 runbook-input bullet |

No files deleted. No new dependencies.

---

## Exit gates for Plan 060

1. All 25 `@task` decorators across the 8 flow files (7 lifecycle + `backup.py`) carry `cache_policy=NO_CACHE`.
2. `docker-compose.yml` cap_add blocks present on main (verified post-`289c5f8` — landed out of scope during Plan 058). Per-service final cap sets: `postgres` (CHOWN + SETUID + SETGID + FOWNER + DAC_OVERRIDE), `prefect-worker` / `api` / `init` (SETUID + SETGID + CHOWN + FOWNER), `caddy` (NET_BIND_SERVICE). Plan 060's contribution: `security.md § Container privilege model` landed with a new `### Capabilities` sub-section enumerating + justifying each cap.
3. `docker-compose.yml` has `sapphire_data:/data/raw:rw` removed from `prefect-worker` AND the orphan top-level `sapphire_data:` entry removed from the `volumes:` block; `docs/standards/cicd.md:38` `sapphire_data` row removed from the volume table; `docker-compose.dev.yml` adds the `${CAMELS_CH_HOST_DIR:-./.data/camels_ch}:/data/raw:ro` bind-mount on `prefect-worker` only; `.env.example` + `docs/conventions.md § Environment variables` document the env var (env `.env.example` entry must explicitly state target path must contain `CAMELS_CH/` uppercase subdir); `.gitignore` covers `.data/`; `docker/entrypoint.sh:27` chown line has `/data/raw` removed with comment updated; `security.md` carries a one-line carve-out for dev-only read-only static reference-data overlays.
4. `docs/standards/orchestration.md` has a new § Caching posture paragraph establishing `cache_policy=NO_CACHE` as the default.
5. Plan 046 §A3 revised (rev 9): step-4 trigger-command example with `model_ids` list; step-8 direct-invoke template for forecast-cycle; §A1 commit-ordering step added for second rebase of `staging-5-stations` post-060; Stream C4 runbook-input bullet for hybrid-trigger flows.
6. T5 validation: `train-models`, `run-hindcast`, `compute-skills` reach COMPLETED with real data side effects (artifacts / hindcasts / skill scores). `compute-combined-skills` reaches COMPLETED with bootstrap path fired (may be a no-op if no combination models assigned — that is acceptable, not a regression). `backup-database` reaches COMPLETED and writes a `.dump` file to `/data/backups` (Permission-denied from the cap-add issue is gone). **Precondition**: operator has pre-staged CAMELS-CH host-side before `up -d` (the `:ro` overlay means `_download_task` in `onboard.py` cannot populate `/data/raw` at runtime).
7. Full pytest green at or above pre-060 count.
8. Plan archived.

After all gates pass, the A3 work remaining is **step 8 (forecast-cycle via direct-invoke per D4)** and **step 9 (API spot checks)**. T5 itself re-runs A3 steps 2-7 on a wiped stack, so those are already validated as part of Plan 060 exit. The orchestrator drives step 8 directly (T4 template).

---

## Risks

| Risk | Mitigation |
|---|---|
| Blanket `NO_CACHE` removes legitimate caching opportunities on pure-compute tasks (no stores). | SAPPHIRE's lifecycle flows are operational pipelines triggered on schedule; cache hits are rare because cycle_time / period_start differ per run. The perf loss is negligible; the correctness gain is total. |
| A @task added after rev-2 (via a parallel Plan-055 T2 commit landing mid-execution) carries a custom `cache_policy` the subagent overwrites. | Subagent does one `grep -n 'cache_policy' src/sapphire_flow/flows/*.py` at T1 start and confirms zero matches (rev-2 verification already shows zero; re-verify in case of in-flight drift). If any match surfaces, stop and report. |
| CHOWN + FOWNER widens the container's capability surface. | Matches `security.md`'s least-privilege model (narrow, justified cap_add). Both are widely accepted for services that init-chown named-volume mount points. |
| Host-path bind-mount makes dev compose machine-specific. | Env var default (`CAMELS_CH_HOST_DIR`) with the orchestrator's known path; documented in T3. |
| T5 surfaces **another** layer of compat issues in run-hindcast / compute-skills beyond cache-policy. | Plan 060 T5 report labels each finding as "Plan 060 regression" vs "new A3 finding"; new findings roll up to Plan 046 / a Plan 061 if needed. |
| `forecast-cycle` direct-invoke path (D4) still crashes because the worker's installed image doesn't include a live adapter factory. | Out of scope for T5; the orchestrator handles forecast-cycle step 8 separately via a Python heredoc with `MeteoSwissNwpAdapter` constructed explicitly. |
| Running `uv run ruff --fix` on the seven flow files drifts formatting beyond the `cache_policy=NO_CACHE` addition. | Subagent runs `ruff format` first, commits the formatting pass as a separate chore commit if churn exceeds ~20 lines — otherwise folds into the feat commit. |

---

## Deferred to follow-up plans

- **Deployment-trigger adapter registry** — lets `forecast-cycle` (and onboard-model + train-models at non-empty scope) be triggered purely via Prefect deployment params. Requires a Python-object registry keyed by a deployment-time slug. Substantial design.
- **CAMELS-CH data-provisioning automation** — download + extract + place into `/data/raw` via an init-time container (or as a first-step in `onboard-stations` flow). Mac-mini staging needs this; C4 runbook covers the operator-manual path today.
- **Dockerfile `RUN chown` pre-provisioning** — pre-create `/data/*` as app-owned in the image so the entrypoint chown becomes belt-and-braces rather than primary. Belt-and-braces is welcome but not critical; defer.
- **Prefect-cache-safe `cache_key_fn` for truly cacheable tasks** (if any emerge) — blanket NO_CACHE is safe now; if profile data later shows a task worth caching, a targeted policy can be added.

---

## Open questions

Resolved by rev-2 critical review:

1. ~~Existing cache_policy on any @task?~~ **No** — Agent-C grep across all 7 lifecycle files + backup.py returned zero matches. All 25 sites are clean for a blanket add.
2. ~~security.md cap_add documentation state?~~ **Currently undocumented** (Agent B). T2 adds the whole sub-section.
3. ~~Machine-specific default path?~~ **Changed** to project-relative `./.data/camels_ch` with `.env.example` + `docs/conventions.md` entry.

Still open, non-blocking:

4. T4 §A3 step 8 direct-invoke deferral (Agent A): acceptable for A3 exit? Plan takes yes — the `forecast.run_completed` event path is identical whether triggered by Prefect or direct `flow.fn()`, so the A2.5 exit gate is satisfied. C4 runbook inherits the hybrid-trigger model. Confirm user preference at READY.
5. Plan 053 (DRAFT, deployment-config-hygiene, depends on Plan 046 DONE) touches the same `docker-compose.yml` cap_add blocks + prefect-server user setting. Plan 060 lands first; Plan 053 rebases onto the cap_add changes when it starts. No blocker, just sequencing — note in Plan 053 draft at T6 archive time if Plan 053 hasn't already accounted for it.
6. Defensive NO_CACHE on `backup.py`'s 2 @tasks: current plan says yes (invariant-simple). Alternative: leave backup alone with a one-line exclusion comment. Plan takes inclusion by default.
