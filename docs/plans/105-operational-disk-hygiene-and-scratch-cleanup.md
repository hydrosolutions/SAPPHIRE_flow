# Plan 105 — operational disk hygiene & NWP scratch cleanup (stop a full disk silently killing the feed)

**Status**: DRAFT — **grill-me COMPLETE (2026-07-06)**: D1 finally-cleanup +
prune-all-stale (safe, `forecast-cycle` is `concurrency_limit=1`); D2 **tiered**
(soft → warn+degrade to fallback, hard → fail-closed red run) on **absolute free-GB**
thresholds; D3 **weekly HOST-level launchd cron** for `docker image/builder prune`
(NOT a Prefect flow — no Docker socket in the worker); D4 keep 4 GiB scratch, no
`max_files` cap. See DECIDED DESIGN. Next: `plan-review` (WF1) → READY → implement.
**Priority**: high — on 2026-07-06 a full disk **silently stopped the operational
forecast feed** on the mac-mini: `nwp.fetch_failed: no space left on device`, the
forecast-cycle completed **green** (the Prefect run returned normally — but the flow
**aborted** with `ForecastCycleHealth.FAILED, stations_attempted=0`, NOT a runoff-only
degrade), and no forecast was written — the same silent-failure class as the NWP-off
blackout (Plan 100). Two
root causes: Docker image/build-cache accumulation from our own rebuilds (~15 GB
reclaimable) **and** a scratch-tmpfs clog from un-cleaned failed-fetch leftovers.
**Phase**: v0b — operational reliability / pipeline monitoring
**Parent**: the mac-mini operational test (Plan 091); companion to Plan 100
(forecast-feed resilience) and its Flow-4 monitoring
**Related**:
- `src/sapphire_flow/adapters/meteoswiss_nwp.py:500-504` (`_fetch_grib_files` — owns
  `scratch_dir = self._scratch_path / cycle.strftime("%Y%m%dT%H%M")` (`:501`) and
  cleans it **on entry** (`:502-503`, `_cleanup_scratch_on_fetch`) for the **current**
  `cycle_time` dir only; `scratch_dir` is a **local**, invisible to `fetch_forecasts`).
  `fetch_forecasts` (`:459-498`) calls `_fetch_grib_files` then `_parse_grib_files`
  (`:479-480`) inside a `try/except`; `:494-495` `except AdapterError: raise`,
  `:496-497` logs `nwp.fetch_failed` + re-raises `AdapterError` on any other exception.
  **All budget/timeout failures (`BudgetExceededError` `:593,609`, `AdapterError`
  STAC-timeout `:545`, pagination `:538`) raise from inside `_fetch_grib_files`.**
- `src/sapphire_flow/flows/run_forecast_cycle.py:669` (`nwp.fetch_failed` logged
  inside `_fetch_nwp_task`, which then **returns `None`** `:670`); `:680-685`
  `nwp.archive_failed` (non-fatal archive handler — **unrelated** to degrade);
  `:660-666` `NoCycleAvailableError` → `_NwpFetchOutcome(nwp_unavailable=True)` is
  the actual **runoff-only degrade signal**; the flow reads it at `:1231-1241`
  (`nwp_unavailable_runtime` → `effective_runoff_only`). A `None` return instead
  aborts the cycle RED-equivalent at `:1210-1222`
  (`ForecastCycleHealth.FAILED, stations_attempted=0`).
- `docker-compose.yml:75,110-114` (`/tmp/sapphire_nwp` tmpfs, **4 GiB**),
  `config/overlays/mac-mini.toml` (`[adapters.weather_forecast].max_files` cap,
  currently unset — the config comments anticipate a mini cap)
- `src/sapphire_flow/types/enums.py:156` (`PipelineCheckType.DISK_USAGE = "disk_usage"`
  — a monitoring metric member already scaffolded → hook the tripwire here). NOTE:
  the emit is via `_append_pipeline_health_record` (`run_forecast_cycle.py:390-426`),
  which **silently no-ops when `pipeline_health_store is None`** (`:400-401`) — the
  event is dropped, not raised; a structured `log.warning`/`log.error` MUST also fire
  so the operator sees the tripwire even without the store wired.
- `scripts/launchd/start-sapphire.sh`, `scripts/bootstrap-mac-mini.sh`,
  `docs/standards/cicd.md` (upgrade runbook — where a deploy-time prune belongs)
- Plan 100 M4 (NWP-staleness tripwire — the *symptom*; this plan attacks the *cause*)
- Plan 095 (`nwp_grid_retention_days=3` — bounds the *archive*, not the scratch or images)
**Created**: 2026-07-06

---

## Problem (observed live on the mac-mini, 2026-07-06)

After the NWP overlay was restored (Plan 100 incident #1), the fetch still failed:
`nwp.fetch_failed: no space left on device`. The disk was full from two sources,
**neither operational data** (a few days of two stations is megabytes):

1. **Docker image / build-cache accumulation.** Every version bump builds a fresh
   ~1.9 GB `sapphire-flow` image; dozens accumulated (~15 GB reclaimable). On a
   Docker-Desktop-for-Mac VM disk this fills fast. Nothing prunes them.
2. **NWP scratch-tmpfs clog (a real bug).** `meteoswiss_nwp.py:502-503` cleans only
   **the current cycle's** scratch dir (`scratch_path / cycle_time`), and only at
   the *start* of a fetch. When a fetch **fails mid-download** (raised from inside
   `_fetch_grib_files`, re-raised at `:496-497`), its partial files are left in
   `scratch/<that-cycle>/`. The next fetch is usually a **different** cycle, so it
   creates a new dir and **never cleans the failed one**. Failed-cycle leftovers
   therefore **accumulate until the 4 GiB tmpfs is full**, after which *every* fetch
   fails instantly on "no space" — a self-perpetuating clog. (Cleared on the mini
   only by recreating the worker, which resets the ephemeral tmpfs.)

**Corrected failure semantics (reviewer, verified against code):** a disk-full fetch
does **NOT** fall to runoff-only today. `fetch_forecasts` raises `AdapterError`
(`:498`), `_fetch_nwp_task` catches it and **returns `None`** (`:668-670`), and the
flow then **aborts** at `:1210-1222` with `ForecastCycleHealth.FAILED,
stations_attempted=0` — **no forecast written**. The Prefect run state is COMPLETED
(green) only because the flow *returned* normally; this is NOT the runoff-only
fallback path (which requires `_NwpFetchOutcome(nwp_unavailable=True)` at `:660-666`
→ `:1231-1241`, and DOES produce native/fallback forecasts). Both are **silent** —
no alert, no signal, exactly like the NWP-off blackout — but the disk-full case is
worse: zero forecasts, not degraded ones. D2's soft-degrade is therefore a **new
behaviour** (route disk-soft to the `nwp_unavailable=True` path), not "reuse an
existing branch".

## Goal

- A full or filling disk **cannot silently kill the feed** — it is detected and
  surfaced loudly *before* (or at) the point it would cause a failed fetch.
- The NWP scratch **cannot clog** — failed-fetch leftovers are always cleaned, and
  stale cycle dirs are pruned.
- Deploys **don't accumulate** unbounded Docker images/build cache on the host.

## DECIDED DESIGN (grill-me 2026-07-06)

- **D1 — scratch self-cleanup on failure + prune ALL stale cycle dirs (the bug fix).**
  **Location correction (blocker): `scratch_dir` is a LOCAL inside `_fetch_grib_files`
  (`:501`) and is invisible to `fetch_forecasts`.** The fix must NOT recompute the
  path in `fetch_forecasts` (that would duplicate the `%Y%m%dT%H%M` format string and
  silently diverge if it ever changes). Two **additive** operations, both anchored to
  where `scratch_dir` is already bound:
  1. **KEEP cleanup-on-entry unchanged** at `_fetch_grib_files:502-503` (the existing
     `rmtree(scratch_dir, ignore_errors=True)` before `mkdir`). This wipes a **prior
     same-cycle** partial before re-download — a distinct purpose from failure
     cleanup; do **not** move it. The existing test `test_cleans_scratch_on_entry`
     (`tests/unit/adapters/test_meteoswiss_nwp.py:975-989`) calls `_fetch_grib_files`
     directly and MUST still pass unmodified.
  2. **ADD stale-cycle sweep** at the **top of `_fetch_grib_files`** (before
     `scratch_dir.mkdir` at `:504`): remove **every** child **directory** of
     `self._scratch_path` that is not the active `scratch_dir` (filtered to
     `child.is_dir()` so stray files/symlinks are left untouched — see the D1 impl
     vision), draining an already-clogged scratch without a worker recreate. **Gating
     (OWNER DECISION):** gate the stale sweep on a **NEW** keyword-only adapter-ctor flag
     `disk_guard_enabled: bool = True` (NOT on `cleanup_scratch_on_fetch`). The
     `disk_guard_enabled` flag is the single opt-out for the operational disk-hygiene
     behaviours added by this plan — the D1 stale sweep and the D2 pre-fetch disk-check
     gate both branch on `if self._disk_guard_enabled:`. The pre-existing on-entry
     `rmtree` (`meteoswiss_nwp.py:502-503`) stays gated on `cleanup_scratch_on_fetch`
     (its original, distinct purpose — wipe a prior same-cycle partial before
     re-download) and is unchanged by this plan; op 3's failure cleanup also stays
     unconditional. This keeps the fixture/CI recording tool out of the operational disk
     thresholds without disturbing the semantics of `cleanup_scratch_on_fetch`.
     **Correction (blocker/major — reviewer-confirmed):** the recording tool
     (`src/sapphire_flow/tools/record_fixtures.py:299`) does **NOT** pass
     `cleanup_scratch_on_fetch=False` today, and no such argument exists — a full-repo
     grep shows the literal `cleanup_scratch_on_fetch=False` appears nowhere in
     `src/`/`tests/`, and `record_fixtures.py:299-305` constructs `MeteoSwissNwpAdapter`
     with only
     `stac_base_url`/`stac_collection`/`scratch_path`/`http_client`/`max_fallback_steps`,
     inheriting the ctor default `cleanup_scratch_on_fetch=True` (`meteoswiss_nwp.py:324`).
     So this plan **adds a required one-line change**:
     `src/sapphire_flow/tools/record_fixtures.py:299` gains `disk_guard_enabled=False`
     (the NEW flag — NOT `cleanup_scratch_on_fetch=False`), making the opt-out real (see
     the D2 recording-tool call-site bullet and the D1 file inventory). Consequence for
     tests: the stale-sweep unit test must construct the adapter with
     `disk_guard_enabled=True` (the production default) to exercise the sweep.
  3. **ADD failure cleanup for the current cycle.** Because on the SUCCESS path the
     downloaded GRIB files in `scratch_dir` are still consumed by `_parse_grib_files`
     (called in `fetch_forecasts:480`, AFTER `_fetch_grib_files` returns), a plain
     `finally: rmtree(scratch_dir)` inside `_fetch_grib_files` would delete the files
     before parsing. **Chosen resolution:** wrap only the download body of
     `_fetch_grib_files` in `try/except Exception: rmtree(scratch_dir,
     ignore_errors=True); raise` (clean the current cycle on ANY raise — including
     the `AdapterError`/`BudgetExceededError` paths at `:538,545,593,609` — then
     re-raise). The success return leaves files intact for `_parse_grib_files`.
     **Implementation note (minor fix):** the `except Exception` block **intentionally**
     catches `AdapterError` subclasses too (`BudgetExceededError` `:593,609`, pagination
     `AdapterError` `:538`, STAC timeout/failure `:545-547`) — cleanup-on-any-raise is
     the desired invariant. Do **not** write a narrower guard that skips cleanup for
     `AdapterError` (e.g. `if not isinstance(exc, AdapterError): rmtree...`) — that
     would leave partial downloads behind on the budget-exceeded path, defeating D1.
     `fetch_forecasts` then re-propagates those `AdapterError`s unchanged via its own
     `except AdapterError: raise` (`:494-495`), so cleanup happens without altering the
     error type the flow sees.
     (Alternatives considered and rejected: moving `_parse_grib_files` inside
     `_fetch_grib_files`, or returning `scratch_dir` up to `fetch_forecasts` for a
     post-parse cleanup — both larger blast radius than the localized except-and-clean.
     Trade-off NOTE: the success-path `scratch_dir` is left in place and is reclaimed
     by op 2's sweep on the *next* fetch, matching today's steady-state behaviour.)
  - **Safe: no concurrent-fetch race** — `forecast-cycle` is `concurrency_limit=1`
    (`register_deployments.py:57`), so only one fetch touches the scratch at a time
    (record this invariant in a comment; if v0b ever parallelises forecast-cycle,
    revisit).
- **D2 — pre-fetch disk tripwire: TIERED, ABSOLUTE free-GB, first real use of
  `DISK_USAGE`.** Before starting the ~2.8 GB download, check **absolute free GB** on
  the **scratch mount** (`/tmp/sapphire_nwp`, always available) and — when configured —
  the **`/data/nwp_grids` persistent volume**. `PipelineCheckType.DISK_USAGE`
  (`types/enums.py:156`) is currently a **defined-but-never-emitted** member — this
  wires it up.
  - **Location (blocker): put the disk check in the adapter, not `_fetch_nwp_task`.**
    The scratch path is only in the flow via `weather_forecast_config.scratch_path`,
    loaded ONLY on the production path (`run_forecast_cycle.py:965,983,1000`, gated on
    `adapter is None`); on the injected/test path (`adapter` provided directly) that
    config is never loaded, so the flow has no scratch path to check. The adapter,
    `MeteoSwissNwpAdapter`, **always** owns `self._scratch_path` regardless of call
    path. **Chosen resolution (reviewer option a):** perform the scratch-mount check
    inside `MeteoSwissNwpAdapter.fetch_forecasts` (`:459`) at the top, before
    `_fetch_grib_files`. Thread the two threshold values + an optional
    `nwp_grid_archive_path: Path | None` into the adapter constructor (`:997-1007`
    call-site) for the persistent-volume check. This makes the check testable via the
    adapter's normal fake-HTTP tests and keeps `_fetch_nwp_task`'s signature
    (`:624-634`) unchanged — no `pipeline_health_store` param needs adding to the task.
  - **DISK_USAGE health emit — wire-through mechanism (BLOCKER fix).** The earlier
    draft claimed "the flow emits the DISK_USAGE record following the
    `_check_nwp_grid_staleness` pattern" — that analogy was **FALSE and the emit was
    unreachable**: `_check_nwp_grid_staleness` (`:508-546`) is called directly by the
    flow and returns synchronously, whereas the disk check fires deep inside the
    adapter and signals **only via exception**. On the soft path the adapter raises
    `NoCycleAvailableError`, which `_fetch_nwp_task:660-666` converts to
    `_NwpFetchOutcome(nwp_unavailable=True)` — dropping all disk context (`path`,
    `free_gb`, `threshold_gb`) before the flow ever sees it. On the hard path
    `AdapterError` → `_fetch_nwp_task:668-670` returns `None` — same data loss. The
    flow at `:1210-1241` receives only `_NwpFetchOutcome | None` and cannot construct
    a meaningful record. **Chosen resolution (reviewer's lowest-blast-radius option):**
    thread the disk metadata back through the outcome so the emit still lives in the
    flow (which has `pipeline_health_store` in scope, `:878`, and
    `_append_pipeline_health_record`, `:390`) **without** adding
    `pipeline_health_store` to the task signature:
    1. **Two disk-specific exception subclasses** (in `exceptions.py`, alongside the
       existing `AdapterError:29` / `NoCycleAvailableError:33` / `BudgetExceededError:37`):
       - `DiskSoftLimitError(NoCycleAvailableError)` — carries `path: str`,
         `free_gb: float`, `threshold_gb: float`, and `subject:
         Literal["scratch", "nwp_archive"]` attributes.
       - `DiskHardLimitError(AdapterError)` — same four attributes.
       Subclassing `NoCycleAvailableError`/`AdapterError` keeps the **existing**
       `_fetch_nwp_task` except-order semantics intact: a `DiskSoftLimitError` would
       still be caught by `except NoCycleAvailableError` (`:660`) and a
       `DiskHardLimitError` by `except Exception` (`:668`) — but the task now adds a
       **more-specific** `except DiskSoftLimitError`/`except DiskHardLimitError` clause
       **ABOVE** those generic branches so it can read the metadata.
    2. **New optional field on `_NwpFetchOutcome`** (`run_forecast_cycle.py:126-145`):
       `disk_check_detail: _DiskCheckDetail | None = None`, where `_DiskCheckDetail`
       is a small frozen dataclass carrying `path: str`, `free_gb: float`,
       `threshold_gb: float`, `severity: PipelineHealthStatus`, `subject:
       Literal["scratch", "nwp_archive"]`, and `abort_requested: bool = False`.
       `_fetch_nwp_task` populates it when it catches a disk-triggered exception:
       - `except DiskSoftLimitError as exc:` → `log.warning("nwp.disk_soft_limit", ...)`
         then return `_NwpFetchOutcome(cycle_time=cycle_time, fallback_used=False,
         nwp_unavailable=True, disk_check_detail=_DiskCheckDetail(...,
         severity=WARNING))`. Runoff-only degrade, record carried.
       - `except DiskHardLimitError as exc:` → `log.error("nwp.disk_hard_limit", ...)`
         then return `_NwpFetchOutcome(cycle_time=cycle_time, fallback_used=False,
         nwp_unavailable=False, disk_check_detail=_DiskCheckDetail(...,
         severity=CRITICAL, abort_requested=True))`. This replaces the old bare `None`
         return for the disk-hard case: the outcome is non-`None` **only** so it can
         carry the detail; the `abort_requested=True` flag drives the same RED abort
         the old `None` produced (see flow emit below). All other adapter/extraction
         failures still return `None` and abort exactly as today.
    3. **Flow emit** (`run_forecast_cycle.py`, right after `nwp_future.result()` at
       `:1186`, before the `nwp_outcome is None` abort at `:1210`): if
       `nwp_outcome is not None and nwp_outcome.disk_check_detail is not None`, call
       `_append_pipeline_health_record(pipeline_health_store,
       check_type=PipelineCheckType.DISK_USAGE, checked_at=clock(),
       status=detail.severity, subject=detail.subject, detail={"path": detail.path,
       "free_gb": detail.free_gb, "threshold_gb": detail.threshold_gb},
       cycle_time=resolved_cycle_time)`. This is the **first real use** of
       `PipelineCheckType.DISK_USAGE` (`types/enums.py:156`).
    4. **Explicit hard-abort guard (BLOCKER fix — the sentinel is NON-`None`, so the
       existing `if nwp_outcome is None` check at `:1210` never catches it).** The
       hard-disk sentinel has `nwp_unavailable=False` and `disk_check_detail.abort_requested=True`.
       Without a dedicated guard it would fall through: `:1210` (`nwp_outcome is None`)
       is `False`; `:1231` `nwp_unavailable_runtime` is `False` (because
       `nwp_unavailable=False` on the sentinel); `:1262` `assert nwp_outcome is not None`
       passes; and `nwp_outcome.fallback_used`/`.cycle_time` are then read as if a
       normal NWP success — defeating fail-closed. **The implementation MUST insert a
       distinct, explicit abort guard immediately AFTER the disk-emit block and BEFORE
       the `nwp_unavailable_runtime` block (`:1231`) — do NOT rely on the
       `if nwp_outcome is None` branch (`:1210`), which cannot catch the non-`None`
       hard-disk sentinel:**

       ```python
       if (
           nwp_outcome is not None
           and nwp_outcome.disk_check_detail is not None
           and nwp_outcome.disk_check_detail.abort_requested
       ):
           log.error("forecast_cycle.nwp_disk_hard_limit_aborting")
           return ForecastCycleResult(
               cycle_time=resolved_cycle_time,
               health=ForecastCycleHealth.FAILED,
               stations_attempted=0,
               stations_succeeded=0,
               stations_failed=0,
               forecasts_stored=0,
               alerts_checked=False,
               duration_ms=round((time.perf_counter() - flow_t0) * 1000, 1),
               errors=("NWP disk hard limit — fail-closed",),
           )
       ```

       This mirrors the `:1212-1222` RED-abort body exactly but fires on the hard-disk
       sentinel. (Rejected alternative: setting `nwp_unavailable=True` on the hard
       sentinel to reuse the `:1231` runoff-only path — that DEGRADES rather than
       fail-closes, contradicting D2's hard-tier objective.)
    5. **Belt-and-suspenders log (store may be None).** `_append_pipeline_health_record`
       silently no-ops when `pipeline_health_store is None` (`:400-401`). The
       always-on channel is the `log.warning`/`log.error` the **task** emits in the
       `except DiskSoftLimitError`/`except DiskHardLimitError` clauses (2 above); the
       adapter ALSO logs at the raise site so the event is visible even in the
       adapter's own unit tests (which have no flow/store). The DB record is the
       operator-dashboard channel; the log is the always-on channel.

    Trade-off NOTE: this adds one optional field + a `_DiskCheckDetail` struct + two
    exception subclasses (all small, additive) rather than adding
    `pipeline_health_store` to the task signature (rejected to keep the task-boundary
    DI minimal) or moving the check into the flow (unreachable on the injected/test
    path — see the Location blocker above). The hard-disk path now returns a non-`None`
    sentinel outcome instead of `None`; because that sentinel is **non-`None`**, the
    generic `if nwp_outcome is None` abort at `:1210` does **not** catch it — the
    explicit `abort_requested` guard added in step 4 above is what produces the
    identical RED-abort. The generic `None`-return abort path (`:1210`) is unchanged
    for every non-disk failure.
  - **Soft (e.g. < ~1.5 GB free on the 4 GiB scratch / < ~8 GB on the persistent
    disk) → WARN + DEGRADE:** the adapter returns the **NWP-unavailable signal** so
    the cycle falls to runoff-only. **This is a NEW behaviour** (see corrected
    semantics above — today a fetch abort does NOT degrade). Concretely: have the
    adapter raise `DiskSoftLimitError(NoCycleAvailableError)` on the soft-disk path.
    Because it subclasses `NoCycleAvailableError`, `_fetch_nwp_task` still routes it to
    the runoff-only signal `_NwpFetchOutcome(nwp_unavailable=True)` (`:660-666` →
    `effective_runoff_only` at `:1241`); the new, more-specific `except
    DiskSoftLimitError` clause additionally attaches `disk_check_detail` so the flow can
    emit the `DISK_USAGE` record (see the wire-through bullet above). No
    `pipeline_health_store` param is added to the task. Feed stays alive on
    native/fallback models; issue surfaced via the flow's `DISK_USAGE` emit + the
    task/adapter log.
  - **Hard/critical (e.g. < ~0.5 GB scratch / < ~3 GB persistent disk) →
    FAIL-CLOSED:** the adapter raises `DiskHardLimitError(AdapterError)`. The task's new
    `except DiskHardLimitError` clause returns a sentinel
    `_NwpFetchOutcome(abort_requested=True, disk_check_detail=..., severity=CRITICAL)`
    (non-`None` only so the CRITICAL record survives); the flow emits the record and
    then takes the same RED-equivalent abort as the old `None` return (`:1210-1222`,
    `ForecastCycleHealth.FAILED`). Every OTHER adapter failure still returns `None`
    and aborts as today. Maximum visibility when the disk is critically full.
  - **`nwp_grid_archive_path is None` case (major):** on `config/overlays/mac-mini.toml`
    the archive path is unset, so `DeploymentConfig.nwp_grid_archive_base_path` is
    `None` (`deployment.py:144`). When the injected `nwp_grid_archive_path` is `None`,
    the adapter **skips the persistent-volume check** and logs a one-line
    `nwp.disk_check_archive_skipped` warning; the scratch-mount check always runs. No
    crash, no silent both-checks-skip.
  - **Second call-site: the recording tool (blocker/major fix — false-premise
    corrected).** `MeteoSwissNwpAdapter` is also constructed by
    `src/sapphire_flow/tools/record_fixtures.py:299-305` (fixture recording). **The
    earlier draft claimed this call-site already passes `cleanup_scratch_on_fetch=False`
    — that is FALSE (reviewer- and grep-confirmed):** it passes only
    `stac_base_url`/`stac_collection`/`scratch_path`/`http_client`/`max_fallback_steps`
    and inherits the ctor default `cleanup_scratch_on_fetch=True` (`meteoswiss_nwp.py:324`).
    The literal `cleanup_scratch_on_fetch=False` appears **nowhere** in `src/` or
    `tests/`. So there is no existing opt-out on this call-site — as-is, the disk
    tripwire (and D1 stale sweep) would fire on recording runs, and a near-full mini
    scratch could abort a recording with `DiskSoftLimitError`, the exact failure the
    opt-out is meant to prevent.
    The new ctor params (`disk_guard_enabled`, `disk_guard_*_gb`, `nwp_grid_archive_path`,
    the probe `Callable`) are **keyword-only with defaults** so this call-site (and the
    many test call-sites) keep compiling.
    **Chosen resolution (OWNER DECISION — opt out via the NEW `disk_guard_enabled`
    flag):** the pre-fetch disk check AND the D1 stale sweep are **gated on the new
    keyword-only ctor flag `disk_guard_enabled: bool = True`** (NOT on
    `cleanup_scratch_on_fetch`), AND this plan **adds a required one-line change** at
    `src/sapphire_flow/tools/record_fixtures.py:299`: pass `disk_guard_enabled=False`
    explicitly. The fixture/CI recording tool opts out of the operational disk
    thresholds — it needs its downloaded GRIBs preserved for the subsequent Zarr-store
    step and must not be aborted by mac-mini operational free-space limits. The
    operational `run_forecast_cycle.py:997-1007` call-site keeps `disk_guard_enabled=True`
    (default) so the tripwire is active in production. `cleanup_scratch_on_fetch` is
    untouched (stays `True` everywhere it is today).
    **Required code change:** `src/sapphire_flow/tools/record_fixtures.py:299` — add
    `disk_guard_enabled=False` to the `MeteoSwissNwpAdapter(...)` constructor call
    (listed in the D1/D2 file inventory). NOTE the correct path is
    `src/sapphire_flow/tools/record_fixtures.py`, not `scripts/record_fixtures.py`.
  - Absolute GB (not %) — predictable against the fixed ~2.8 GB working set.
    Thresholds live in config so they are tunable per deployment (schema below).
- **D3 — weekly HOST-level launchd cron for image/build-cache prune (NOT a Prefect
  flow).** A Docker prune needs **host Docker-daemon access**; running it from a
  Prefect flow would require mounting the Docker socket into the worker — a
  **security no-go** (container escape surface; violates the least-privilege model in
  `docs/standards/security.md`). So the weekly `docker image prune -a -f` +
  `docker builder prune -f` runs as a **host launchd periodic job on the mac-mini**
  (alongside `start-sapphire.sh`), documented in the mini runbook. **Not** on every
  boot, **not** in the upgrade runbook (owner chose weekly-cron only).
  - **`-a` is REQUIRED, not optional (BLOCKER fix).** The primary ~15 GB offender is
    old **tagged** `sapphire-flow:0.1.xxx` images (one per version bump — the live host
    pins `VERSION=x.y.z`, cf. `docker-compose.yml` `sapphire-flow:${VERSION}` at
    `:69,126,167,248`, and MEMORY's VERSION/.env convention). Plain
    `docker image prune -f` removes only **dangling (untagged)** images and would
    reclaim **nothing** from these tagged-but-unreferenced images — D3 would be a no-op
    for the cited root cause. `docker image prune -a -f` removes **all** images not
    referenced by a running container, including old tags. Consequence documented in
    the `prune-docker.sh` header: the currently-running `sapphire-flow:${VERSION}` and
    its base images stay (referenced by the live containers); every older
    `sapphire-flow:0.1.xxx` tag is removed. If a future need arises to protect specific
    third-party base images, add a `--filter 'label=...'` — not needed today since the
    live stack keeps its own referenced images.
  - **Naming / path (matches existing `scripts/launchd/` convention).** The existing
    plists are `ch.hydrosolutions.sapphire.plist` and
    `ch.hydrosolutions.sapphire-watchdog.plist`. Follow the same `ch.hydrosolutions.`
    prefix: label the new job **`ch.hydrosolutions.sapphire-docker-prune`**, file
    `scripts/launchd/ch.hydrosolutions.sapphire-docker-prune.plist`, invoking
    `scripts/launchd/prune-docker.sh`; register it in
    `scripts/launchd/install-launchd.sh` next to the other two.
  - **Cadence:** weekly via `StartCalendarInterval` (e.g. Sunday 04:00 local, off
    the operational cycle cadence).
  - **Size-guard (concrete starting heuristic, was a residual):** before pruning,
    read the reclaimable size via `docker system df --format '{{.Reclaimable}}'` and
    **skip the prune when reclaimable < 1 GB**. NOTE (major fix): the field is
    `{{.Reclaimable}}`, **not** `{{.ReclaimableSize}}` — the latter is not a valid Go
    template field on `docker system df` and silently yields empty output, which would
    parse as 0 GB and skip the prune every week. Output is human-readable like
    `19.75GB (33%)`, so the parser strips the ` (xx%)` suffix; alternatively use
    `docker system df --format '{{json .}}'` (per-row `{"Reclaimable":"19.75GB (33%)",
    ...}`) for structured parsing. Log the reclaimable figure and the skip/prune
    decision either way.
- **D4 — keep the 4 GiB scratch, no `max_files` mini cap.** The live incident showed
  a *clean* fetch stays well under 4 GiB (~400 MB and climbing when healthy) — the
  tmpfs is not too small; the clog was leftovers (fixed by D1). Do **not** add a
  `max_files` cap prematurely; only revisit if a clean fetch is later shown to exceed
  4 GiB.

### Implementation vision (feeds WF1 plan-review → WF2)

- **D1 (code, all inside `adapters/meteoswiss_nwp.py` `_fetch_grib_files`
  `:500-504`):** (a) KEEP the entry `rmtree(scratch_dir)` at `:502-503` unchanged
  (still gated on `cleanup_scratch_on_fetch`);
  (b) ADD, before `scratch_dir.mkdir` (`:504`), a sweep — **gated on the NEW
  `self._disk_guard_enabled` flag** (NOT `cleanup_scratch_on_fetch`) —
  removing every child **directory** of `self._scratch_path` that != `scratch_dir`.
  **Filter to `child.is_dir()` (major fix)** so a stray non-directory entry (file /
  symlink placed directly under `scratch_path`) is not silently deleted, and so
  `rmtree` never trips `NotADirectoryError` on such an entry — the sweep iterates the
  children and only `rmtree`s those that ARE directories, skipping any stray file
  outright:
  `for child in self._scratch_path.iterdir(): if child.is_dir() and child != scratch_dir: shutil.rmtree(child, ignore_errors=True)`
  (`ignore_errors=True` retained; low probability in production — the tmpfs is fresh on
  each worker restart — but the explicit `is_dir()` filter makes the sweep safe
  regardless, so a non-directory entry under the tmpfs cannot break it);
  (c) wrap the download body (the `while url ...` loop + STAC walk, `:535-644`) in
  `try/except Exception: shutil.rmtree(scratch_dir, ignore_errors=True); raise` so a
  failed cycle cleans its own dir on any raise (this intentionally also cleans on
  `AdapterError`/`BudgetExceededError` — see the D1 note) while the success return
  leaves files for `_parse_grib_files`. Do **not** touch `fetch_forecasts` for cleanup.
  Unit-tests: (1) seed a stale `scratch/<oldcycle>/` + confirm the sweep removes it on
  a fetch for a new cycle (adapter built with `disk_guard_enabled=True`, the default);
  (2) make the STAC walk raise mid-download, assert the active `scratch_dir` is gone
  afterward; (3) confirm the existing `test_cleans_scratch_on_entry`
  (`test_meteoswiss_nwp.py:975`) still passes unmodified; (4) success path leaves
  `scratch_dir` files intact for parse.
- **D2 (code + config):** add a `disk_free_gb(path)` helper (`shutil.disk_usage`) and
  a **pre-fetch check inside `MeteoSwissNwpAdapter.fetch_forecasts`
  (`meteoswiss_nwp.py:459`, top, before `_fetch_grib_files`), gated on
  `if self._disk_guard_enabled:`** (the NEW ctor flag, default `True`; the recording
  tool passes `False`) — the adapter always
  owns `self._scratch_path` and (threaded via ctor) `nwp_grid_archive_path`. On soft
  raise `DiskSoftLimitError(NoCycleAvailableError)` carrying
  `path/free_gb/threshold_gb/subject` (task maps it to
  `_NwpFetchOutcome(nwp_unavailable=True, disk_check_detail=...)` → runoff-only at
  `run_forecast_cycle.py:660-666,1231-1241`); on hard raise
  `DiskHardLimitError(AdapterError)` with the same attributes (task maps it to a
  sentinel `_NwpFetchOutcome(abort_requested=True, disk_check_detail=...)`; flow emits
  the CRITICAL record then aborts RED-equivalent at `:1210-1222`); archive-path `None`
  → skip that mount's check + log. The **flow** emits the `DISK_USAGE`
  `PipelineHealthRecord` via `_append_pipeline_health_record` (`:390`) from
  `nwp_outcome.disk_check_detail` right after `nwp_future.result()` (`:1186`) — NOT via
  the `_check_nwp_grid_staleness` synchronous pattern (that helper is flow-local; the
  disk check is adapter-deep and reaches the flow only through the outcome struct — see
  the DISK_USAGE wire-through bullet above). Constructor wiring at the production
  call-site `run_forecast_cycle.py:997-1007` reads the new config keys (below) +
  `config.nwp_grid_archive_base_path`. Inject the free-space probe (a
  `Callable[[Path], float]` defaulting to `disk_free_gb`) into the adapter so tests can
  force values. Tests: soft → raises `DiskSoftLimitError` (assert attributes) + task
  returns `nwp_unavailable=True` outcome with `disk_check_detail`; hard → raises
  `DiskHardLimitError` + task returns `abort_requested=True` outcome; flow emits a
  `DISK_USAGE` record with the right `status`/`subject`/`detail` for each; **flow-level
  hard-abort: given a non-`None` sentinel outcome with `abort_requested=True`, the flow
  returns `ForecastCycleHealth.FAILED, stations_attempted=0` (the new explicit guard) —
  NOT a normal-NWP-success run** (this is the regression test for the blocker); healthy
  → proceeds, no record; archive `None` → scratch-only check + skip log; recording-tool
  call-site (`disk_guard_enabled=False`) → disk check AND stale sweep skipped (no raise
  even when the probe reports below-hard free space).
- **D2 config schema (was unspecified).** Add four threshold fields to
  `_WeatherForecastAdapterConfig` (`run_forecast_cycle.py:114-123`) and the underlying
  loader `_load_weather_forecast_adapter_config` (`:194-289`), sourced from
  `[adapters.weather_forecast]` in the overlay TOML. The fields carry
  **dataclass-level defaults** (starter values; impl to tune) so existing callers and
  the `SAPPHIRE_CONFIG is None` early-return branch (`:198-207`) that construct
  `_WeatherForecastAdapterConfig` without the new keys keep compiling — same pattern as
  `max_files` (`None` default). **The `SAPPHIRE_CONFIG is None` early-return branch at
  `:197-207` needs NO call-site change (major fix): it builds
  `_WeatherForecastAdapterConfig(enabled=False, ...)` with kw_only fields, so it silently
  GAINS the four new `disk_guard_*_gb` fields via their dataclass (kw-only) defaults —
  the call-site keeps compiling untouched — and returns before any adapter is constructed
  on the no-`SAPPHIRE_CONFIG` (runoff-only) path. On the production path these four
  fields are then threaded from `_WeatherForecastAdapterConfig` into the
  `MeteoSwissNwpAdapter(...)` construction at `run_forecast_cycle.py:~997-1007`.**
  **Required loader change (major fix — was omitted): `_load_weather_forecast_adapter_config`
  (`:276-289`, where the `_WeatherForecastAdapterConfig(...)` is finally built) must add
  four new TOML key reads, one per threshold, using the SAME parse+validate pattern as
  `max_files` (`:239-245`) — read the value from the `[adapters.weather_forecast]`
  table, validate it (see below), else fall back to the shared default constant.** The
  loader reads each TOML value and **falls back to the dataclass default when the key is
  absent** (so a non-mac-mini overlay that omits them still works):
  - `disk_guard_scratch_soft_gb: float = 1.5`
  - `disk_guard_scratch_hard_gb: float = 0.5`
  - `disk_guard_archive_soft_gb: float = 8.0`
  - `disk_guard_archive_hard_gb: float = 3.0`

  **Single source of truth for the four defaults (minor fix).** The `MeteoSwissNwpAdapter`
  ctor also needs these four thresholds as keyword-only params with defaults (so the
  many test/recording call-sites keep compiling without threading config). To avoid a
  silent divergence — where a test that constructs the adapter directly (bypassing
  `_WeatherForecastAdapterConfig`) uses an adapter-ctor default that differs from the
  config default — define the four values ONCE as module-level named constants and
  reference them from BOTH the `_WeatherForecastAdapterConfig` dataclass defaults AND
  the adapter ctor defaults:
  - `_DEFAULT_DISK_GUARD_SCRATCH_SOFT_GB = 1.5`
  - `_DEFAULT_DISK_GUARD_SCRATCH_HARD_GB = 0.5`
  - `_DEFAULT_DISK_GUARD_ARCHIVE_SOFT_GB = 8.0`
  - `_DEFAULT_DISK_GUARD_ARCHIVE_HARD_GB = 3.0`

  (If the constants live in `adapters/meteoswiss_nwp.py` and `_WeatherForecastAdapterConfig`
  imports them, or vice-versa, either direction is fine as long as there is exactly one
  literal per value. A future tune then changes both the adapter ctor default and the
  config default atomically.)

  **Validation (minor fix — match the existing loader's per-field checks, e.g.
  `expected_delivery_offset_hours` `:181-190` and `max_files` `:239-245`).** For each
  `disk_guard_*_gb` key present in the TOML, raise `ConfigurationError` with a
  descriptive message when the value is a `bool` (TOML `true`/`false` parses to Python
  `bool`, which is `float`-coercible and would silently yield `1.0`/`0.0`), is not an
  `int`/`float`, or is `<= 0`. Add a cross-field check per mount that `hard_gb <
  soft_gb` (raise `ConfigurationError` otherwise) so an inverted-tier config fails
  loudly at load time rather than degrading unexpectedly. Reject `bool` explicitly via
  `isinstance(value, bool) or not isinstance(value, (int, float))`.

  Example `config/overlays/mac-mini.toml` addition under `[adapters.weather_forecast]`:
  `disk_guard_scratch_soft_gb = 1.5`, `disk_guard_scratch_hard_gb = 0.5`,
  `disk_guard_archive_soft_gb = 8.0`, `disk_guard_archive_hard_gb = 3.0`. Thread these
  (plus `config.nwp_grid_archive_base_path`) into the `MeteoSwissNwpAdapter` ctor at
  `:997-1007`.
- **D3 (ops) — file inventory:**
  - `scripts/launchd/prune-docker.sh` (new) — runs the reclaimable-size guard (skip if
    `docker system df --format '{{.Reclaimable}}'` < 1 GB) then `docker image prune -a -f`
    (the `-a` is required to reclaim old **tagged** `sapphire-flow:0.1.xxx` images — see
    the D3 `-a` note above) + `docker builder prune -f`.
  - `scripts/launchd/ch.hydrosolutions.sapphire-docker-prune.plist` (new) — label
    `ch.hydrosolutions.sapphire-docker-prune`, weekly `StartCalendarInterval`.
  - `scripts/launchd/install-launchd.sh` (**edit — minor fix, was omitted**) — add
    `"ch.hydrosolutions.sapphire-docker-prune.plist"` to the hard-coded `PLISTS=(...)`
    array at `:14-17` (next to `ch.hydrosolutions.sapphire.plist` and
    `ch.hydrosolutions.sapphire-watchdog.plist`), else the installer creates but never
    registers the new job.
  - mini runbook / `docs/standards/cicd.md` — document the weekly prune job.

  No app-code change; no Docker socket in any container.

## Non-goals

- The NWP-off overlay persistence + fallback floor — Plan 100.
- The water_level QC datum bug — Plan 101.
- The full Flow-4 pipeline-monitoring watchdog — this plan adds one disk tripwire on
  the existing `DISK_USAGE` metric; the broader watchdog stays in the Flow-4 plan.
- Postgres/backup-volume retention tuning (separate; `/data/raw` CAMELS-CH at 92% is
  a large reference dataset on its own disk, noted but out of scope).

## Verification (local dev stack is up)

- **D1:** simulate a failed fetch (leave a dummy `scratch/<oldcycle>/` dir), trigger
  a fetch for a new cycle, confirm the old dir is pruned and a failed fetch cleans
  its own dir (scratch returns to ~empty).
- **D2:** constrain free space (or lower the threshold) and confirm a loud
  `DISK_USAGE` event fires (a `PipelineHealthRecord` written to the store via the flow
  emit from `nwp_outcome.disk_check_detail`, PLUS the always-on adapter/task log) and
  the chosen fail-closed/warn behaviour holds *before* a doomed download — soft →
  runoff-only degrade, hard → RED abort.
- **D3:** run the weekly host launchd prune job and confirm stale images/build cache
  are reclaimed with no impact on running services.

## Process

Grill-me **COMPLETE** (2026-07-06); **plan-review pass applied (2026-07-08)**:
corrected all stale `file:line` citations (adapter cleanup lives in
`_fetch_grib_files` not `fetch_forecasts`; `run_forecast_cycle.py` NWP handling at
`:669/:680-685/:1210-1222/:1231-1241` not `:347-360`; `PipelineCheckType.DISK_USAGE`
at `:156`), relocated the D2 disk check into the adapter (only place with a
call-path-independent scratch path + testable), pinned the D2 config schema
(4 `disk_guard_*_gb` fields on `_WeatherForecastAdapterConfig`), handled
`nwp_grid_archive_base_path is None`, clarified disk-full aborts (does NOT degrade
today — D2 soft-degrade is new behaviour), matched the D3 plist to the
`ch.hydrosolutions.*` convention with a `<1 GB reclaimable` size-guard, and noted the
`pipeline_health_store is None` silent-drop. **Second plan-review pass applied
(2026-07-08)** resolving the reviewers' blockers/majors: (1) the DISK_USAGE emit was
architecturally **unreachable** (the adapter raises; the task discards disk context;
the flow only sees `_NwpFetchOutcome | None`) — fixed by adding
`DiskSoftLimitError(NoCycleAvailableError)`/`DiskHardLimitError(AdapterError)` carrying
`path/free_gb/threshold_gb/subject`, a `disk_check_detail: _DiskCheckDetail | None`
field on `_NwpFetchOutcome` (with `severity` + `abort_requested`), a dedicated
`except` clause in `_fetch_nwp_task` that populates it, and a flow emit after
`nwp_future.result()` — the false "`_check_nwp_grid_staleness` pattern" analogy struck;
(2) D3 `docker image prune -f` → **`-a -f`** (dangling-only prune reclaimed nothing
from the ~15 GB of old TAGGED `sapphire-flow:0.1.xxx` images); (3) `docker system df
--format '{{.ReclaimableSize}}'` → **`{{.Reclaimable}}`** (invalid field → empty →
skip every week); (4) `disk_guard_*_gb` fields carry **dataclass-level defaults** +
`bool`/`>0`/`hard<soft` validation; (5) the `record_fixtures.py:299` second call-site
is inventoried and gated out of the disk guard (opt-out flag chosen — superseded by the
fourth pass, see below); (6) the D1 stale sweep is likewise gated out via the same flag;
(7) the D1 `except Exception` intentionally-catches-`AdapterError` invariant is documented. D1 (safe via `concurrency_limit=1`);
D2 tiered soft-degrade / hard-fail-closed on absolute free-GB; D3 weekly host launchd
prune (not a Prefect flow — no Docker socket in the worker); D4 keep 4 GiB, no
`max_files`. Next: confirming plan-review → READY → implement. Implementation is a
code + config + ops change (`exceptions.py` two `Disk*LimitError` subclasses;
`adapters/meteoswiss_nwp.py` disk check + D1 cleanup; `flows/run_forecast_cycle.py`
`_DiskCheckDetail` + `_NwpFetchOutcome.disk_check_detail` + `_fetch_nwp_task` disk
except-clauses + flow `_append_pipeline_health_record` emit + **explicit
`abort_requested` RED-abort guard before `:1210`**; `_WeatherForecastAdapterConfig`
threshold keys + validation + loader parse + shared default constants + mac-mini
overlay; `tools/record_fixtures.py:299` gains `disk_guard_enabled=False` (the NEW opt-out
flag — see the fourth pass); a
`scripts/launchd/` prune job + `.plist` + `install-launchd.sh` `PLISTS` array edit,
docs) → **hold-at-PR** with a version bump.

**Third plan-review pass applied (2026-07-08)** resolving reviewer blockers/majors:
(1) **BLOCKER** — the hard-disk sentinel (`nwp_unavailable=False`,
`abort_requested=True`) is non-`None`, so the existing `if nwp_outcome is None` check
at `:1210` never aborts it; added an **explicit `abort_requested` guard** (returns
`ForecastCycleHealth.FAILED, stations_attempted=0`) inserted between the disk-emit
block and `:1210`, else the sentinel falls through to the normal-NWP-success path at
`:1231/:1262`. (2) **BLOCKER/MAJOR** — the false premise that
`record_fixtures.py:299-305` passes `cleanup_scratch_on_fetch=False` (it does not;
grep-confirmed absent from all of `src/`/`tests/`); corrected to a **required one-line
change** at `src/sapphire_flow/tools/record_fixtures.py:299` (correct path, not
`scripts/record_fixtures.py`) so the recording tool actually opts out of the disk guard —
**the opt-out mechanism was finalised in the fourth pass below** (a NEW
`disk_guard_enabled=False` flag, NOT overloading `cleanup_scratch_on_fetch`).
(3) **MAJOR** — `_load_weather_forecast_adapter_config` (`:276-289`)
must add four TOML key reads (max_files pattern at `:239-245`); the `:197-207`
early-return branch needs no change (defaulted kw_only fields). (4) **MAJOR** — the
adapter-ctor threshold defaults MUST equal the config defaults; defined as **shared
module-level constants** referenced by both. (5) **MAJOR** — the D1 stale sweep filters
to `child.is_dir()` so stray files/symlinks under `scratch_path` aren't deleted and
`rmtree` can't trip `NotADirectoryError`. (6) **MINOR** — `install-launchd.sh` `PLISTS`
array (`:14-17`) added to the D3 file inventory.

**Fourth plan-review pass applied (2026-07-08)** resolving reviewer blockers/majors and
an OWNER DECISION: (1) **BLOCKER/MAJOR ×3 — the `cleanup_scratch_on_fetch=False`
opt-out premise was false** (that flag/value exists nowhere in the codebase;
`record_fixtures.py:299` uses the default `True`). **OWNER DECIDED** to gate the disk
guard on a **NEW keyword-only adapter-ctor flag `disk_guard_enabled: bool = True`** —
BOTH the D1 stale-sweep gate and the D2 pre-fetch disk-check gate branch on
`if self._disk_guard_enabled:` (NOT on `cleanup_scratch_on_fetch`, whose semantics are
left untouched). `src/sapphire_flow/tools/record_fixtures.py:299` is a **required code
change**: it passes `disk_guard_enabled=False` so the fixture/CI tool opts out of the
operational disk thresholds; the file is added to the D1/D2 change inventory, and the
mis-cited `scripts/record_fixtures.py` path is corrected to
`src/sapphire_flow/tools/record_fixtures.py` throughout. (2) **BLOCKER** — the D2 hard
threshold's explicit fail-closed abort guard is pinned (see D2 wire-through step 4): the
non-`None` hard-disk sentinel is caught by a **distinct explicit guard**
(`nwp_outcome is not None and disk_check_detail is not None and abort_requested`)
inserted after the disk-emit block and before the `nwp_unavailable_runtime` block
(`:1231`), returning `ForecastCycleHealth.FAILED, stations_attempted=0` — NOT relying on
the `nwp_outcome is None` branch. (3) **MINOR** — the four threshold defaults are a
single source of truth via shared module constants referenced by both the config
dataclass and the adapter ctor. (4) **MAJOR** — the `SAPPHIRE_CONFIG is None`
early-return `_WeatherForecastAdapterConfig(enabled=False, ...)` (`:197-207`) gains the
four `disk_guard_*_gb` fields via kw-only defaults (call-site unchanged), threaded into
the `MeteoSwissNwpAdapter` construction at `:997-1007`. (5) **MAJOR** — the D1
stale-sweep skips non-directory entries (`child.is_dir()`, `ignore_errors=True`
retained) so a stray file under the `/tmp/sapphire_nwp` tmpfs cannot break it.
(6) **MINOR** — `install-launchd.sh` `PLISTS=(...)` array (`:14-17`) is a required D3
file edit.
