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
  2. **ADD stale-cycle sweep** in `_fetch_grib_files`, **AFTER
     `scratch_dir.mkdir(parents=True, exist_ok=True)` at `:504`** (so
     `self._scratch_path` is guaranteed to exist before `iterdir()` — see the ordering
     note in the D1 impl vision; the just-created `scratch_dir` is spared by the
     `child != scratch_dir` filter): remove **every** child **directory** of
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
    inside `MeteoSwissNwpAdapter.fetch_forecasts`. **Insertion point (minor fix — pin
    it): AFTER `resolve_cycle(cycle_time)` returns at `:464-465`, BEFORE
    `_fetch_grib_files(resolved_cycle)` at `:479`.** Inserting it before `resolve_cycle`
    would fire the disk check even for cycles that would have raised
    `NoCycleAvailableError` (no published cycle) before any download is attempted —
    emitting a misleading DISK_USAGE record for what is really an NWP-unavailability
    event. Placing it after `resolve_cycle` but before the download keeps the log
    semantics clean and still pre-empts the doomed `_fetch_grib_files` download.
    Thread the four threshold values + an optional
    `nwp_grid_archive_path: Path | None` into the adapter constructor (`:997-1007`
    call-site) for the persistent-volume check. This makes the check testable via the
    adapter's normal fake-HTTP tests. `_fetch_nwp_task` DOES gain one keyword param —
    `pipeline_health_store: object | None = None` (`:624-634`) — so it can emit the
    DISK_USAGE record directly (see the wire-through bullet below); this is the only
    task-signature change and it is a single new line at the submit call (`:1166-1176`).
  - **DISK_USAGE health emit — wire-through mechanism (BLOCKER fix; SIMPLIFIED, 5th pass).**
    The earlier draft claimed "the flow emits the DISK_USAGE record following the
    `_check_nwp_grid_staleness` pattern" — that analogy was **FALSE and the emit was
    unreachable**: `_check_nwp_grid_staleness` (`:1244-1249`) is called directly by the
    flow and returns synchronously, whereas the disk check fires deep inside the
    adapter and signals **only via exception**. On the soft path the adapter raises
    `NoCycleAvailableError`, which `_fetch_nwp_task:660-667` converts to
    `_NwpFetchOutcome(nwp_unavailable=True)`; on the hard path `AdapterError` →
    `_fetch_nwp_task:668-670` returns `None`. In both cases the disk context (`path`,
    `free_gb`, `threshold_gb`) is dropped before the flow sees it.
    **Chosen resolution (5th pass — reviewer-corrected to the SIMPLEST option): emit
    the DISK_USAGE record from inside `_fetch_nwp_task` directly, by injecting
    `pipeline_health_store` into the task.** This is one line at the call-site and
    eliminates the entire threading apparatus the earlier pass proposed (`_DiskCheckDetail`
    dataclass, the `disk_check_detail` outcome field, and the non-`None`
    `abort_requested` sentinel + its explicit flow abort guard). Rationale the earlier
    draft used to reject this ("keep the task-boundary DI minimal") does **not** hold:
    `pipeline_health_store` is already injected into the flow at the top-level flow
    signature and is already passed to `_check_nwp_grid_staleness` (`:1246`) — adding it
    to `_fetch_nwp_task` (`:624-634`) is a single new keyword param plus one line at the
    submit call (`:1166-1176`).
    1. **Two disk-specific exception subclasses** (in `exceptions.py`, alongside the
       existing `AdapterError:29` / `NoCycleAvailableError:33` / `BudgetExceededError:37`):
       - `DiskSoftLimitError(NoCycleAvailableError)` — carries `path: str`,
         `free_gb: float`, `threshold_gb: float`, and `subject:
         Literal["scratch", "nwp_archive"]` attributes.
       - `DiskHardLimitError(AdapterError)` — same four attributes.
       **Except-clause ordering (BLOCKER — must be pinned exactly).** Because
       `DiskSoftLimitError` subclasses `NoCycleAvailableError`, Python evaluates the
       `try/except` clauses **top-to-bottom** and dispatches to the FIRST matching
       handler. Therefore the two new clauses `except DiskSoftLimitError` and
       `except DiskHardLimitError` MUST be the **first two handlers** in the
       `_fetch_nwp_task` try/except — inserted **BEFORE `except NoCycleAvailableError`
       at `:660`** (NOT merely before `except Exception` at `:668`). If they were
       placed anywhere AFTER `except NoCycleAvailableError` (`:660`), that existing
       clause would swallow every `DiskSoftLimitError` — no `DISK_USAGE` record would
       be emitted and the wrong `nwp.no_cycle_available` log would fire. Concretely,
       the final handler order is:
       1. `except DiskSoftLimitError as exc:` (NEW — emits WARNING record, degrades)
       2. `except DiskHardLimitError as exc:` (NEW — emits CRITICAL record, aborts)
       3. `except NoCycleAvailableError as exc:` (existing `:660`)
       4. `except Exception as exc:` (existing `:668`)
       The two new clauses read the disk metadata AND emit the record; the existing
       two are unchanged.
       **`__init__` signature (minor fix — the existing `exceptions.py` classes at
       `:1-59` are all bare subclasses with NO constructor, so the reader attribute
       access would `AttributeError` unless the ctor is specified).** Both classes take:
       `def __init__(self, message: str, *, path: str, free_gb: float, threshold_gb:
       float, subject: Literal["scratch", "nwp_archive"]) -> None:` — store the four
       attributes, then `super().__init__(message)`. State this explicitly in the file
       inventory so the implementer does not follow the bare-subclass pattern and drop
       the attributes.
       - **Import the two new exceptions in `run_forecast_cycle.py` (BLOCKER — else
         `NameError` at the new `except` clauses).** The file currently imports
         `NoCycleAvailableError` (and `ConfigurationError`, `StoreError`) from
         `sapphire_flow.exceptions` (`run_forecast_cycle.py:19-23`). Add
         `DiskSoftLimitError` and `DiskHardLimitError` to that same import block —
         the new `except DiskSoftLimitError` / `except DiskHardLimitError` clauses
         reference these names, so without the import both clauses raise `NameError`
         at task run. Pin this in the `run_forecast_cycle.py` file inventory.
    2. **Inject `pipeline_health_store` into `_fetch_nwp_task`** (`run_forecast_cycle.py:624-634`):
       add a new keyword param `pipeline_health_store: object | None = None` to the task
       signature, and pass `pipeline_health_store=pipeline_health_store` at the submit
       call-site (`:1166-1176`, where the flow already has it in scope). The task then
       emits the DISK_USAGE record itself in the two new `except` clauses:
       - `except DiskSoftLimitError as exc:` → `log.warning("nwp.disk_soft_limit", path=exc.path,
         free_gb=exc.free_gb, threshold_gb=exc.threshold_gb, subject=exc.subject)`, then
         `_append_pipeline_health_record(pipeline_health_store,
         check_type=PipelineCheckType.DISK_USAGE, checked_at=clock(),
         status=PipelineHealthStatus.WARNING, subject=exc.subject, detail={"path": exc.path,
         "free_gb": exc.free_gb, "threshold_gb": exc.threshold_gb}, cycle_time=cycle_time)`,
         then `return _NwpFetchOutcome(cycle_time=cycle_time, fallback_used=False,
         nwp_unavailable=True)`. Runoff-only degrade; record emitted from the task.
       - `except DiskHardLimitError as exc:` → `log.error("nwp.disk_hard_limit", ...)` with
         the same fields, then `_append_pipeline_health_record(..., status=PipelineHealthStatus.CRITICAL,
         ...)`, then `return None`. Returning `None` is exactly what the old bare
         `except Exception: return None` did, so the **existing** `if nwp_outcome is None`
         abort at `:1210-1222` fires **unchanged** — no sentinel, no explicit flow guard,
         no non-`None` fall-through hazard. This is the **first real use** of
         `PipelineCheckType.DISK_USAGE` (`types/enums.py:156`).
       `_append_pipeline_health_record` (`:390-426`) is module-level in
       `run_forecast_cycle.py`, so the task can call it directly; it already silently
       no-ops when `pipeline_health_store is None` (`:400-401`).
    3. **No flow emit block, no `_DiskCheckDetail`, no `disk_check_detail` field, no
       `abort_requested` sentinel, no explicit hard-abort guard.** The 4th-pass design
       added all of these to thread metadata to the flow; the 5th pass DROPS them all
       because the task now emits the record directly. The hard path returns `None` and
       reuses the existing `:1210` abort verbatim; the soft path returns the existing
       `_NwpFetchOutcome(nwp_unavailable=True)` (`:665-667`) verbatim → the existing
       `:1231-1241` runoff-only path. `_NwpFetchOutcome` (`run_forecast_cycle.py:126-145`)
       is **unchanged**.
    4. **Belt-and-suspenders log (store may be None).** `_append_pipeline_health_record`
       silently no-ops when `pipeline_health_store is None` (`:400-401`). The
       always-on channel is the `log.warning`/`log.error` the **task** emits in the
       `except DiskSoftLimitError`/`except DiskHardLimitError` clauses (2 above); the
       adapter ALSO logs at the raise site so the event is visible even in the
       adapter's own unit tests (which have no flow/store). The DB record is the
       operator-dashboard channel; the log is the always-on channel.

    Trade-off NOTE: this adds one keyword param to `_fetch_nwp_task` (the reason the
    4th-pass draft rejected — "keep task DI minimal" — did not hold: the flow already
    passes `pipeline_health_store` to `_check_nwp_grid_staleness` at `:1246`) plus the
    two exception subclasses (still needed either way). It REMOVES the `_DiskCheckDetail`
    struct, the `_NwpFetchOutcome.disk_check_detail` field, the non-`None` hard sentinel,
    and the flow-side emit + explicit `abort_requested` abort guard. Net: strictly less
    code and no non-`None` fall-through hazard, because the hard path returns `None`
    exactly as every other adapter failure does today.
  - **Soft (e.g. < ~1.5 GB free on the 4 GiB scratch / < ~8 GB on the persistent
    disk) → WARN + DEGRADE:** the adapter returns the **NWP-unavailable signal** so
    the cycle falls to runoff-only. **This is a NEW behaviour** (see corrected
    semantics above — today a fetch abort does NOT degrade). Concretely: have the
    adapter raise `DiskSoftLimitError(NoCycleAvailableError)` on the soft-disk path.
    The new, more-specific `except DiskSoftLimitError` clause in `_fetch_nwp_task`
    emits the `DISK_USAGE` WARNING record (via `_append_pipeline_health_record` — the
    task now has `pipeline_health_store` injected) and then returns
    `_NwpFetchOutcome(nwp_unavailable=True)` — exactly the runoff-only signal the
    existing `:665-667` branch produces → `effective_runoff_only` at `:1241`. Feed
    stays alive on native/fallback models; issue surfaced via the task's `DISK_USAGE`
    emit + the task/adapter log.
  - **Hard/critical (e.g. < ~0.5 GB scratch / < ~3 GB persistent disk) →
    FAIL-CLOSED:** the adapter raises `DiskHardLimitError(AdapterError)`. The task's new
    `except DiskHardLimitError` clause emits the `DISK_USAGE` CRITICAL record and then
    returns `None` — exactly what the existing `except Exception: return None`
    (`:668-670`) does, so the existing `if nwp_outcome is None` abort at `:1210-1222`
    (`ForecastCycleHealth.FAILED`) fires unchanged. No sentinel, no explicit flow
    guard. Every OTHER adapter failure still returns `None` and aborts as today.
    Maximum visibility when the disk is critically full.
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
    - **Stack-up safety (minor fix — `-a` protection is conditional on running
      containers).** The "currently-running image stays referenced" protection holds
      **only while the stack is up**. If the weekly cron fires during a maintenance
      window where the operator has run `docker compose down` before a version upgrade,
      `docker image prune -a -f` would remove **every** image including
      `sapphire-flow:${VERSION}` (`docker-compose.yml:69` pins `sapphire-flow:${VERSION}`).
      Per the VERSION/.env convention (MEMORY), a plain `docker compose up -d` (no
      `--build`) then reuses a **cached** image — which no longer exists, so `up` errors
      or forces an unexpected rebuild. Mitigations, to state in `prune-docker.sh` and the
      mini runbook: (a) add a stack-up guard — skip the prune entirely unless the
      stack containers are running; AND (b) document that operators should always use
      `docker compose up -d --build` (not `up -d` alone) after a version upgrade, so a
      pruned image is rebuilt rather than assumed cached.
      - **Guard command (MAJOR fix — the compose-based guard is a permanent no-op).**
        Do **NOT** use `docker compose ps --status running | grep -q sapphire`: launchd
        runs `prune-docker.sh` from the job's own cwd (`scripts/launchd/`), which has
        **no `docker-compose.yml`**, so a bare `docker compose ps` (no `-f`) finds no
        Compose project and the guard **always fails** → `docker image prune -a -f` is
        permanently skipped and D3 becomes a no-op. Use plain
        **`docker ps --format '{{.Names}}' | grep -q sapphire`** instead — plain
        `docker ps` needs no Compose project or cwd; the stack containers are named
        `sapphire_flow-*`, so the name match succeeds while the stack is up. **If the
        `docker ps` guard command itself errors** (Docker daemon unreachable, etc.),
        the script defaults to **SKIPPING the prune** (safe — never prune when the
        running state is unknown), NOT aborting. Because launchd runs with a minimal
        environment, `prune-docker.sh` MUST set an explicit absolute `PATH` and working
        directory (per the `scripts/launchd/start-sapphire.sh` convention, which invokes
        `docker compose` with absolute `-f` paths) so `docker` resolves at all.
  - **Naming / path (matches existing `scripts/launchd/` convention).** The existing
    plists are `ch.hydrosolutions.sapphire.plist` and
    `ch.hydrosolutions.sapphire-watchdog.plist`. Follow the same `ch.hydrosolutions.`
    prefix: label the new job **`ch.hydrosolutions.sapphire-docker-prune`**, file
    `scripts/launchd/ch.hydrosolutions.sapphire-docker-prune.plist`, invoking
    `scripts/launchd/prune-docker.sh`; register it in
    `scripts/launchd/install-launchd.sh` next to the other two.
  - **Cadence:** weekly via `StartCalendarInterval` (e.g. Sunday 04:00 local, off
    the operational cycle cadence).
  - **Size-guard (concrete starting heuristic, was a residual) — parse `{{json .}}`
    per Type row (major fix, revised).** `docker system df --format '{{.Reclaimable}}'`
    emits **four rows** — Images, Containers, Local Volumes, Build Cache — NOT one
    aggregate; and the Build Cache row is a bare figure (`20.43GB`) with **no `(xx%)`
    suffix**, so a naive `strip ' (xx%)'` parser breaks on it and a first-line-only read
    would silently ignore a multi-GB Build Cache. **Chosen resolution:** `prune-docker.sh`
    reads `docker system df --format '{{json .}}'` (one JSON object per row, each with
    `Type` and `Reclaimable`), selects the `"Images"` and `"Build Cache"` rows
    explicitly, and parses each `Reclaimable` (stripping any trailing ` (xx%)` and the
    `GB`/`MB` unit, normalising to GB). Gate `docker image prune -a -f` on the **Images**
    reclaimable and `docker builder prune -f` on the **Build Cache** reclaimable
    independently, each skipped when its figure is `< 1 GB`. Log the two figures and the
    per-command skip/prune decision.
    **Correction (minor fix — the `{{.ReclaimableSize}}` failure mode):** the earlier
    draft said `{{.ReclaimableSize}}` "silently yields empty output" — that is WRONG.
    Verified on this host: `docker system df --format '{{.ReclaimableSize}}'` exits
    **non-zero (code 1)** with a Go template-parsing error. Under a `prune-docker.sh`
    using `set -euo pipefail`, that invalid field would ABORT the script on every weekly
    run (a persistent no-op that logs an error), NOT silently skip. `{{.Reclaimable}}`
    is the valid field; `{{json .}}` (above) is the parsing form this plan pins.
- **D4 — keep the 4 GiB scratch, no `max_files` mini cap.** The live incident showed
  a *clean* fetch stays well under 4 GiB (~400 MB and climbing when healthy) — the
  tmpfs is not too small; the clog was leftovers (fixed by D1). Do **not** add a
  `max_files` cap prematurely; only revisit if a clean fetch is later shown to exceed
  4 GiB.

### Implementation vision (feeds WF1 plan-review → WF2)

- **D1 (code, all inside `adapters/meteoswiss_nwp.py` `_fetch_grib_files`
  `:500-504`):** (a) KEEP the entry `rmtree(scratch_dir)` at `:502-503` unchanged
  (still gated on `cleanup_scratch_on_fetch`);
  (b) ADD the sweep — **gated on the NEW `self._disk_guard_enabled` flag** (NOT
  `cleanup_scratch_on_fetch`) — removing every child **directory** of
  `self._scratch_path` that != `scratch_dir`. **Ordering (minor fix — guard the
  `iterdir()` FileNotFoundError): run the sweep AFTER
  `scratch_dir.mkdir(parents=True, exist_ok=True)` at `:504`.** `mkdir(parents=True)`
  unconditionally creates `self._scratch_path` (the parent of `scratch_dir`), so a
  later `self._scratch_path.iterdir()` cannot raise `FileNotFoundError` — which
  `shutil.rmtree(..., ignore_errors=True)` would NOT suppress because that flag applies
  only to the `rmtree` call, not to `iterdir()`. This matters when `scratch_path` is a
  non-Docker host path that has not been pre-created (in production the tmpfs is fresh
  on each worker restart, but the reorder makes the sweep robust regardless).
  `scratch_dir` itself was just created by that `mkdir`, so the `child != scratch_dir`
  filter correctly spares it. **Filter to `child.is_dir()` (major fix)** so a stray
  non-directory entry (file / symlink placed directly under `scratch_path`) is not
  silently deleted, and so `rmtree` never trips `NotADirectoryError` on such an entry —
  the sweep iterates the children and only `rmtree`s those that ARE directories,
  skipping any stray file outright:
  `for child in self._scratch_path.iterdir(): if child.is_dir() and child != scratch_dir: shutil.rmtree(child, ignore_errors=True)`
  (`ignore_errors=True` retained; the explicit `is_dir()` filter plus the
  after-`mkdir` ordering make the sweep safe against both stray non-directory entries
  and a missing `scratch_path`);
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
  tool passes `False`) — inserted AFTER `resolve_cycle` (`:464-465`) and BEFORE
  `_fetch_grib_files` (`:479`). The adapter always
  owns `self._scratch_path` and (threaded via ctor) `nwp_grid_archive_path`. On soft
  raise `DiskSoftLimitError(NoCycleAvailableError)` carrying
  `path/free_gb/threshold_gb/subject`; the task's new `except DiskSoftLimitError`
  clause emits the WARNING `DISK_USAGE` record (via `_append_pipeline_health_record`,
  now callable because `pipeline_health_store` is injected into the task) then returns
  `_NwpFetchOutcome(nwp_unavailable=True)` → runoff-only at
  `run_forecast_cycle.py:665-667,1231-1241`. On hard raise
  `DiskHardLimitError(AdapterError)` with the same attributes; the task's new
  `except DiskHardLimitError` clause emits the CRITICAL `DISK_USAGE` record then returns
  `None` — the existing `if nwp_outcome is None` abort at `:1210-1222` fires unchanged
  (no sentinel, no explicit flow guard). Archive-path `None` → skip that mount's check +
  log. The `DISK_USAGE` `PipelineHealthRecord` is emitted **from inside the task**,
  NOT from the flow and NOT via the `_check_nwp_grid_staleness` synchronous pattern (see
  the simplified DISK_USAGE wire-through bullet above; `_NwpFetchOutcome` is
  **unchanged** — no `disk_check_detail` field). `_fetch_nwp_task` gains one keyword
  param `pipeline_health_store` (`:624-634`), passed at the submit call
  (`:1166-1176`). Constructor wiring at the production
  call-site `run_forecast_cycle.py:997-1007` reads the new config keys (below) +
  `config.nwp_grid_archive_base_path`. Inject the free-space probe (a
  `Callable[[Path], float]` defaulting to `disk_free_gb`) into the adapter so tests can
  force values. Tests: soft → raises `DiskSoftLimitError` (assert attributes) + task
  returns `nwp_unavailable=True` outcome + task emits a WARNING `DISK_USAGE` record to a
  fake store; hard → raises `DiskHardLimitError` + task returns `None` + emits a CRITICAL
  `DISK_USAGE` record; the emitted record carries the right `status`/`subject`/`detail`
  for each; **flow-level hard-abort: given a `None` outcome (the hard path), the flow
  returns `ForecastCycleHealth.FAILED, stations_attempted=0` via the existing `:1210`
  abort** (unchanged — no new guard needed); healthy → proceeds, no record; archive
  `None` → scratch-only check + skip log; recording-tool call-site
  (`disk_guard_enabled=False`) → disk check AND stale sweep skipped (no raise even when
  the probe reports below-hard free space).
  **`_make_adapter` test helper (minor fix).** The shared adapter-builder helper
  `_make_adapter` in `tests/unit/adapters/test_meteoswiss_nwp.py:226-235` (used by ~20
  existing tests) must default `disk_guard_enabled=False`, so those pre-existing tests
  are NOT silently subjected to the new D1 stale sweep / D2 pre-fetch disk check. The
  new disk-guard tests then pass `disk_guard_enabled=True` explicitly (and the
  stale-sweep test likewise, per the D1 unit-test note) to exercise the guarded
  behaviour.
  **Test file placement (minor fix).** The **adapter-level** tests (soft/hard raise
  `Disk*LimitError` with the right attributes, healthy → no raise, archive-`None` skip,
  recording-tool opt-out) live in `tests/unit/adapters/test_meteoswiss_nwp.py`
  (alongside `_make_adapter`). The **task-level** tests (`_fetch_nwp_task` returns
  `nwp_unavailable=True` on soft / `None` on hard; the task emits a WARNING / CRITICAL
  `DISK_USAGE` `PipelineHealthRecord` to a fake store) live in
  `tests/unit/flows/test_run_forecast_cycle.py`, mirroring how the adapter-level tests
  are named against the adapter module.
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
  **`nwp_grid_archive_path` ownership (major fix — do NOT add it to
  `_WeatherForecastAdapterConfig`).** The persistent-volume archive path is
  `nwp_grid_archive_base_path`, which lives on `DeploymentConfig`
  (`src/sapphire_flow/config/deployment.py:144`, `str | None = None`), NOT on
  `_WeatherForecastAdapterConfig`. The four `disk_guard_*_gb` threshold fields are the
  ONLY new fields added to `_WeatherForecastAdapterConfig`. At the adapter construction
  site (`:997-1007`) the archive path is passed to the adapter's new
  `nwp_grid_archive_path` ctor param directly from `config.nwp_grid_archive_base_path`
  (`config` being the in-scope `DeploymentConfig`) — unchanged sourcing, no new config
  field. On the `SAPPHIRE_CONFIG is None` early-return branch (`:197-207`) there is no
  `DeploymentConfig` at all (the function returns before any adapter is constructed), so
  `nwp_grid_archive_base_path` is effectively `None` and the adapter's persistent-volume
  archive disk-check is simply skipped on that path (the scratch-mount check still runs
  when an adapter is later built). The implementer MUST source `nwp_grid_archive_path`
  from `config.nwp_grid_archive_base_path`, NOT from `_WeatherForecastAdapterConfig`.**
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
  - `scripts/launchd/prune-docker.sh` (new) — reads `docker system df --format
    '{{json .}}'`, parses the **Images** and **Build Cache** `Reclaimable` figures
    (per the revised size-guard), then runs `docker image prune -a -f` (the `-a` is
    required to reclaim old **tagged** `sapphire-flow:0.1.xxx` images — see the D3 `-a`
    note above) when Images-reclaimable ≥ 1 GB and `docker builder prune -f` when
    Build-Cache-reclaimable ≥ 1 GB. **Stack-up guard (major fix): `docker image prune
    -a` is only safe while the stack is UP**, guarded by plain
    `docker ps --format '{{.Names}}' | grep -q sapphire` (NOT `docker compose ps`,
    which is a permanent no-op from the launchd cwd — see the `-a` safety note below);
    a guard-command error defaults to SKIP; the script sets an explicit absolute
    `PATH`/working dir per the `start-sapphire.sh` convention.
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
  `DISK_USAGE` event fires (a `PipelineHealthRecord` written to the store by the
  `_fetch_nwp_task` disk `except` clause via `_append_pipeline_health_record`, PLUS the
  always-on adapter/task log) and the chosen fail-closed/warn behaviour holds *before*
  a doomed download — soft → runoff-only degrade (`nwp_unavailable=True`), hard → RED
  abort via the existing `nwp_outcome is None` branch (`:1210`).
- **D3:** run the weekly host launchd prune job with the stack UP and confirm stale
  images/build cache are reclaimed with no impact on running services; confirm the
  stack-up guard skips the prune when the stack is DOWN.

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
`path/free_gb/threshold_gb/subject`, **and (SUPERSEDED by the fifth pass) a
`disk_check_detail: _DiskCheckDetail | None`
field on `_NwpFetchOutcome` (with `severity` + `abort_requested`), a dedicated
`except` clause in `_fetch_nwp_task` that populates it, and a flow emit after
`nwp_future.result()`** — the false "`_check_nwp_grid_staleness` pattern" analogy struck
(the fifth pass keeps the two exception subclasses but drops the field/struct/flow-emit,
emitting directly from the task instead);
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
code + config + ops change (`exceptions.py` two `Disk*LimitError` subclasses **with an
explicit `__init__(message, *, path, free_gb, threshold_gb, subject)`** carrying the
four attributes; `adapters/meteoswiss_nwp.py` disk check + D1 cleanup;
`flows/run_forecast_cycle.py` **imports `DiskSoftLimitError`/`DiskHardLimitError` from
`sapphire_flow.exceptions` (added to the existing `:19-23` import block alongside
`NoCycleAvailableError` — else the new clauses `NameError`)**, and `_fetch_nwp_task`
gains a `pipeline_health_store` param + two disk `except`-clauses **placed as the FIRST
TWO handlers, BEFORE `except NoCycleAvailableError` at `:660`** (NOT merely before
`except Exception` at `:668` — since `DiskSoftLimitError` subclasses
`NoCycleAvailableError`, any placement after `:660` would be swallowed) that emit
`DISK_USAGE` via `_append_pipeline_health_record`
and return `nwp_unavailable=True` (soft) / `None` (hard, reusing the existing `:1210`
abort) — **no `_DiskCheckDetail`, no `_NwpFetchOutcome.disk_check_detail` field, no
sentinel, no new flow abort guard** (5th pass); `_WeatherForecastAdapterConfig`
threshold keys + validation + loader parse + shared default constants + mac-mini
overlay; `tools/record_fixtures.py:299` gains `disk_guard_enabled=False` (the NEW opt-out
flag — see the fourth pass); a
`scripts/launchd/` prune job + `.plist` + `install-launchd.sh` `PLISTS` array edit,
docs) → **hold-at-PR** with a version bump.

**Third plan-review pass applied (2026-07-08)** resolving reviewer blockers/majors
(**NOTE: items (1)–(2) below are SUPERSEDED by the fifth pass — the sentinel + flow
abort guard + threading apparatus were all removed when the DISK_USAGE emit moved into
`_fetch_nwp_task`; retained here as a decision-history record only**):
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
`src/sapphire_flow/tools/record_fixtures.py` throughout. (2) **BLOCKER (SUPERSEDED by
the fifth pass — the hard path now returns `None` and reuses the existing `:1210`
abort; no sentinel, no explicit guard)** — the D2 hard
threshold's explicit fail-closed abort guard was pinned (former D2 wire-through step 4): the
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

**Fifth plan-review pass applied (2026-07-08)** resolving reviewer blockers/majors —
mostly a SIMPLIFICATION of the 4th-pass wire-through: (1) **MAJOR + MAJOR (ambiguous
`abort_requested` location)** — the 4th-pass D2 wire-through was unnecessarily complex.
`pipeline_health_store` is already in flow scope and already passed to
`_check_nwp_grid_staleness` (`:1246`), so **inject it into `_fetch_nwp_task`**
(`:624-634`, one line at the submit call `:1166-1176`) and emit the `DISK_USAGE` record
**from the task's two disk `except` clauses**. This ELIMINATES the `_DiskCheckDetail`
dataclass, the `_NwpFetchOutcome.disk_check_detail` field, the non-`None` hard sentinel,
the explicit flow `abort_requested` abort guard, AND the flow-side emit block. Soft →
emit WARNING + `return _NwpFetchOutcome(nwp_unavailable=True)` (existing runoff-only
path); hard → emit CRITICAL + `return None` (existing `:1210` abort, unchanged). This
also moots the "`_NwpFetchOutcome(abort_requested=True, ...)` vs `_DiskCheckDetail`"
field-location ambiguity — `abort_requested` no longer exists anywhere. (2) **MINOR** —
the two `Disk*LimitError` subclasses get an explicit
`__init__(message, *, path, free_gb, threshold_gb, subject)` (the existing
`exceptions.py` classes are bare subclasses, so the attribute reads would `AttributeError`
without it). (3) **MINOR** — D1 stale sweep runs AFTER
`scratch_dir.mkdir(parents=True, exist_ok=True)` (`:504`) so `self._scratch_path`
exists before `iterdir()` (`ignore_errors=True` does NOT cover an `iterdir`
`FileNotFoundError`). (4) **MINOR** — D3 size-guard parses `docker system df --format
'{{json .}}'` per-`Type` row (4 rows; Build Cache lacks the `(xx%)` suffix), gating
`image prune -a` on Images-reclaimable and `builder prune` on Build-Cache-reclaimable
independently; corrected the `{{.ReclaimableSize}}` failure mode (exits code 1 /
aborts under `set -euo pipefail`, does NOT silently skip). (5) **MINOR** — D3 `-a`
protection is conditional on the stack being UP; added a stack-up guard (originally
`docker compose ps` — **corrected to plain `docker ps` in the sixth pass**, see below,
because the compose form is a permanent no-op from the launchd cwd) + a runbook note to
use `up -d --build` after a version upgrade
(`docker-compose.yml:69` pins `sapphire-flow:${VERSION}`). (6) **MINOR** — pinned the D2
disk-check insertion point to AFTER `resolve_cycle` (`:464-465`) / BEFORE
`_fetch_grib_files` (`:479`) so an NWP-unavailable (no published cycle) run does not
emit a misleading DISK_USAGE record.

**Sixth plan-review pass applied (2026-07-08)** resolving reviewer blockers/majors — all
precision corrections, no design change: (1) **BLOCKER** — pinned the `_fetch_nwp_task`
except-clause ordering UNAMBIGUOUSLY: because `DiskSoftLimitError` subclasses
`NoCycleAvailableError`, the two new `except DiskSoftLimitError` / `except
DiskHardLimitError` clauses MUST be the FIRST TWO handlers, inserted BEFORE
`except NoCycleAvailableError` at `:660` (NOT merely before `except Exception` at `:668`);
the earlier "ABOVE those generic branches" wording was ambiguous and any placement after
`:660` would silently swallow the disk error. Pinned in the `run_forecast_cycle.py` file
inventory too. (2) **BLOCKER** — `run_forecast_cycle.py` must import
`DiskSoftLimitError`/`DiskHardLimitError` from `sapphire_flow.exceptions` (added to the
existing `:19-23` import block alongside `NoCycleAvailableError`), else the new `except`
clauses raise `NameError`. (3) **MAJOR** — clarified `nwp_grid_archive_base_path`
ownership: it lives on `DeploymentConfig` (`config/deployment.py:144`), NOT on
`_WeatherForecastAdapterConfig`; the adapter's `nwp_grid_archive_path` param is sourced
directly from `config.nwp_grid_archive_base_path` at `:997-1007`, and on the
`SAPPHIRE_CONFIG is None` early-return (`:197-207`) there is no `DeploymentConfig` so it
is effectively `None` and the persistent-volume check is simply skipped. (4) **MAJOR** —
fixed the D3 stack-up guard so it is not a permanent no-op: replaced
`docker compose ps --status running | grep -q sapphire` (which finds no Compose project
from the launchd `scripts/launchd/` cwd → always skips the prune) with plain
`docker ps --format '{{.Names}}' | grep -q sapphire` (containers are named
`sapphire_flow-*`); a guard-command error defaults to SKIP (safe); `prune-docker.sh` sets
an explicit absolute `PATH`/working dir per the `start-sapphire.sh` convention.
(5) **MINOR** — `_make_adapter` (`tests/unit/adapters/test_meteoswiss_nwp.py:226-235`)
must default `disk_guard_enabled=False` so the ~20 existing callers are not subjected to
the new D1 sweep / D2 check; disk-guard tests pass `disk_guard_enabled=True` explicitly.
(6) **MINOR** — named the task-level test file: the `_fetch_nwp_task`
`nwp_unavailable=True` / WARNING-`DISK_USAGE` tests go in
`tests/unit/flows/test_run_forecast_cycle.py` (adapter-level raise/attribute tests stay
in `tests/unit/adapters/test_meteoswiss_nwp.py`).
