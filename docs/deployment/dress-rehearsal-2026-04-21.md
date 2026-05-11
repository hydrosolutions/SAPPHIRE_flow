# Dress-rehearsal report — 2026-04-21

**Plan**: 046 Stream A (Mac Mini Staging Deployment — dress rehearsal on MacBook Pro).
**Run window**: ~08:33–11:46 UTC.
**HEAD at start**: `c205d20` (Plan 046 Rev 10, v0.1.378).
**Config**: `main` branch with `docker compose -f docker-compose.yml -f docker-compose.dev.yml -f docker-compose.staging.yml up -d` — overlay selects 5-station A1 subset per Plan 065.
**Operator**: Beatrice (IT specialist); orchestration driven via CLI (no Prefect UI).
**Host**: MacBook Pro, Apple Silicon, Docker Desktop 16 GiB RAM allocation.

---

## Summary

**Partial pass.** Every step that could be verified on this run executed its core logic. The two gaps are external (MeteoSwiss cycle lateness) and a latent adapter pagination cap; neither is caused by Plan 065 and neither is specific to the dress rehearsal.

- 7 of 9 A3 steps completed (1, 2, 3, 5, 6, 7, 9).
- Step 4 (`train-models`) was intentionally skipped after inspection — retrain flow's data-window only covers post-onboarding observations; initial training already succeeded inside `onboard-stations`. See finding F3.
- Step 8 (`forecast-cycle`) blocked by two independent issues: (a) MeteoSwiss STAC has published no ICON-CH2-EPS cycle since 2026-04-20T12:00Z — ~23h late at the time of the run, exceeding `nwp_max_fallback_age_hours=12`; (b) when the fallback cap was manually bypassed, the adapter hit a hardcoded STAC-pagination cap of 100 pages.

**The Plan 065 deliverable is proven out for the first seven A3 steps.** A3 ran from `main` without a branch; the 5-station overlay was selected through the compose file; the config loader picked it up inside the container (verified both via Python smoke test pre-A3 and via the live flow behaviour during onboard-stations).

---

## A3 step log

| Step | Deployment | Wall-clock | Result | Notes |
|---|---|---|---|---|
| 1a | `onboard-model` (`linear_regression_daily`) | ~2 s trigger + ~8 s run | ✅ | register-only path, empty scope |
| 1b | `onboard-model` (`climatology_fallback`) | ~8 s | ✅ | |
| 1c | `onboard-model` (`persistence_fallback`) | ~8 s | ✅ | |
| 2 | `onboard-stations` | **~38 min** (08:55:21 → 11:33:20) | ✅ (4/5 operational) | Full CAMELS-CH bootstrap + per-station initial training + hindcast + skill gate. Murten (lake) correctly skipped by M.2 compatibility check. |
| 3 | `ingest-observations` (1 cycle) | ~30 s | ✅ (4/5 polled) | Operational stations only — matches gating from step 2. New rows per operational station per parameter. |
| 4 | `train-models` | — | ⏭️ skipped | See F3 |
| 5 | `run-hindcast` ×4 | ~19 min total (parallel on worker) | ✅ | Required JSON-string quoting on UUID params (see F2). First attempt failed on raw strings. |
| 6 | `compute-skills` ×4 | ~5 min total (parallel) + 1 retry | 🟡 → ✅ | One run SIGKILL'd (OOM under parallel load); transient — retry green. |
| 7 | `compute-combined-skills` | ~6 min | ✅ | Trigger requires (`station_id`, `parameter`, `strategy`); v0a no-op path. |
| 8 | `forecast-cycle` (direct-invoke) | ~2 min before abort | 🚫 blocked | See F4, F5 |
| 9 | API spot checks | <1 s each | ✅ | Stations list returns 5 (Murten onboarding, 4 operational); forecasts empty (expected — no step 8 forecast); alerts empty. |

---

## What broke, what we fixed, what we deferred

### F1. Stale volumes from prior A3 attempt confused the first onboard-model triple

**Symptom**: First onboard-model runs (before `down -v`) had 2/3 FAIL — `linear_regression_daily` with "Not enough training rows: need at least 8 (lookback=7 + 1), got 3" and `persistence_fallback` with "Required dependencies are None: ['forcing_source']".

**Root cause**: Prior A3 attempts on the `staging-5-stations` branch left 5 stations and 14,687 obs/station/param in the DB. Onboard-model then tried to run the full training + hindcast path instead of the "empty-scope register-only path" Rev 8 documented as working.

**Fix**: `docker compose down -v && up -d` cleared volumes. Fresh-DB retry: all 3 runs COMPLETED in ~30 s. Register-only path only triggers when no pre-existing stations exist.

**Follow-up**: No immediate code change. If we later want onboard-model to be idempotent against pre-existing stations, that's a separate design choice. For A3 purposes the `down -v` prerequisite should be documented in Plan 046 as the canonical starting state (not just an optional reset).

### F2. Prefect CLI requires JSON-string quoting on UUID parameters

**Symptom**: First `run-hindcast` batch of 4 FAILED with parameter-validation errors — `artifact_id: invalid length found 0` (empty string) and `station_id: expected UUID, found " " at 37` (trailing space).

**Root cause**: Plan 046 §A3 step 5 says "Pass UUIDs as `str`" but does not say how. `prefect deployment run -p station_id=$uuid` passes the value unquoted, which the CLI tries to JSON-parse. Raw UUIDs aren't valid JSON tokens. Wrapping with shell-level double quotes doesn't survive docker-exec argv parsing cleanly.

**Fix**: Use JSON-string form: `-p 'station_id="a7ac3be7-..."'`. All four hindcast runs COMPLETED on retry.

**Follow-up (Rev 11 candidate)**: Plan 046 §A3 steps 5 and 6 should document the JSON-string quoting explicitly. Current guidance is misleading.

### F3. `train-models` retrain flow data-window mismatch

**Symptom**: Step 4 FAILED with "Not enough training rows: need at least 8 (lookback=7 + 1), got 1" — even though the DB carries 130K historical observations.

**Root cause**: `train-models` (retrain path) evidently assembles its training data only from observations ingested *after* initial onboarding — at A3 time, that's a single LINDAS poll (3 rows per station per parameter, 1 timestamp). The full CAMELS-CH historical data already used by the initial training inside `onboard-stations` is not included in the retrain assembly.

**Decision**: Skipped step 4 for this run — initial training already produced 12 active artifacts (4 per model), covering all downstream steps. No redundancy gained by running a retrain with 1 row of new data.

**Follow-up (detour plan)**: Clarify intended semantics of `train-models` data window. Options: (a) retrain uses only post-last-artifact observations — then A3 step 4 belongs after multiple ingest cycles, not at step 4; (b) retrain uses full historical + recent — then the assembly needs a fix. This needs a design call before it lands as a plan.

### F4. MeteoSwiss ICON-CH2-EPS cycle publication ~23h late (external)

**Symptom**: Step 8 aborted with "No cycle available within 3 fallback steps from 2026-04-21T09:00:00+00:00".

**Root cause**: MeteoSwiss STAC endpoint's latest cycle at the time of the run was `2026-04-20T12:00:00Z` — roughly 23.5 hours prior. Every cycle slot on 2026-04-21 (00, 03, 06, 09 UTC) was unpublished. This is an external outage or publication lag outside our system.

**Correct behaviour**: The pipeline aborted cleanly with `forecast_cycle.nwp_fetch_failed_aborting`. 23.5h exceeds `nwp_max_fallback_age_hours=12` — per the NWP-lateness policy this cycle would be skipped even if the adapter reached it.

**Follow-up**: None required for our codebase. Separately, `pipeline_health` should record this as a late-source event; when Flow 4 (pipeline monitoring) is scoped, it should alert on prolonged NWP source absence. No code change from this finding alone — the policy worked as designed.

### F5. Adapter `_MAX_FALLBACK_STEPS` hardcoded + STAC pagination cap

**Symptom**: To exercise the end-to-end forecast path despite F4, I monkey-patched `_MAX_FALLBACK_STEPS` from 3 to 10 in a one-off Python session. The flow then reached the 2026-04-20T12:00 cycle, started downloading GRIB2 files, applied the `tp + t_2m` allowlist correctly, and failed with `STAC pagination exceeded 100 pages` before finishing fetch.

**Root cause**:
- `src/sapphire_flow/adapters/meteoswiss_nwp.py:36`: `_MAX_FALLBACK_STEPS: int = 3  # hard-coded per Plan 063 D4; non-configurable in v0`. Even if an operator needed to reach a cycle >9h old (e.g., during extended outages), no config path.
- The adapter has a 100-page pagination cap. ICON-CH2-EPS cycles carry tens of thousands of STAC items (80+ members × ~36 steps × ~40 variables); the 100×100 = 10k cap is too small to enumerate a full cycle.

**Follow-up (detour plan candidate)**: NWP adapter configurability pass — move `_MAX_FALLBACK_STEPS` into `DeploymentConfig`, raise or remove the pagination cap, or server-side filter via STAC CQL to reduce total items walked. Should be scoped as its own plan.

### F6. Compute-skills transient OOM under parallel load

**Symptom**: One of four parallel `compute-skills` runs SIGKILL'd (exit -9 = OOM). Retry with no code change COMPLETED cleanly.

**Root cause**: Docker Desktop's 16 GiB allocation hit a transient spike when four skill-compute processes ran in parallel. Each loads full historical hindcast + observations for its station.

**Follow-up**: Not reproducible deterministically; watch for recurrence on scale-up (A4/169 stations). If it recurs there, look at either chunking per-station skill-compute or lowering worker concurrency.

### F7. Lake stations are inert in current operational pipeline

**Observation (not a bug)**: Murten (station 2004, lake) was onboarded as a row but never marked `station_operational` because all three registered models target `discharge` — Murten only has `water_level`, so M.2 compatibility correctly skipped artifact creation. Without an active artifact, ingest-observations (step 3) filters Murten out.

**Implication**: Plan 046 §A1's claim that the 5-station subset "validates multi-parameter pipeline (discharge + water_level)" is overreaching — water_level-only stations cannot be validated through the discharge-targeting model lineup. Either (a) ship a water_level-target model, or (b) soften §A1's claim and accept that the multi-parameter angle is deferred to v0b.

**Follow-up**: Plan-level decision, not code. Recommend softening §A1 text in Plan 046 Rev 11 and spawning a separate water_level-target model plan later if needed.

---

## Resource baseline (live, captured immediately post-run)

| Container | RAM (live) | CPU (live) |
|---|---|---|
| `postgres` | 2.26 GiB | 18.7 % |
| `prefect-worker` | 2.40 GiB | 0.30 % |
| `prefect-server` | 217 MiB | 3.62 % |
| `api` | 125 MiB | 0.09 % |
| `caddy` | 22 MiB | 0.00 % |
| **Total** | **~5.0 GiB** | |

**Docker Desktop allocation**: 16 GiB RAM (headroom ~11 GiB).

**Database size after run**: 2,827 MiB (2.76 GiB). Dominated by `hindcast_values` (~19 M rows across 4 stations × 3 models × 45 years of daily hindcasts) and `observations` (130 k historical + a handful real-time).

**Peak RSS during run**: not captured continuously. The compute-skills OOM (F6) suggests ≥ 4 GiB peak for the worker process during parallel skill computation. If A4 hits similar OOM scenarios at 169 stations, worth instrumenting `docker stats --no-stream` on a poll loop during the next run.

---

## Timing baseline

**5-station set (this run):**
- `onboard-model` register-only path (3 models): ~30 s total
- `onboard-stations` full path (includes per-station initial training + hindcast + skill gate for 3 models × 4 valid stations): **38 min**
- `ingest-observations` (1 LINDAS cycle, 4 stations): ~30 s
- `run-hindcast` (4 parallel, per station, linear_regression_daily): ~19 min total (bounded by the slowest parallel run)
- `compute-skills` (4 parallel + 1 retry): ~5 min total
- `compute-combined-skills` (1 station, v0a no-op path): ~6 min
- `forecast-cycle` full path: **not achieved** — blocked by F4/F5. A `stations_attempted=0` abort takes ~4 s.

**169-station projection (A4, not yet run)**: `onboard-stations` scales roughly linearly in station count for the historical-hindcast phase. 38 min × (169/4) ≈ 26 hours → **this is the hard signal that the onboard-stations historical-hindcast path is not sized for v0's 169-station target.** Either the initial training should skip hindcast and only register artifacts (letting step 5 handle hindcast per station), or the historical hindcast needs to become a batch-parallel operation. Worth flagging as a v0-readiness concern before A4 is attempted.

---

## Mac-specific gotchas

- **`VERSION` env var is required** for every `docker compose` invocation (the base compose file references `sapphire-flow:${VERSION}` with no default). `docker compose down` without it errors with "required variable VERSION is missing a value" — trips up anyone switching sessions. Suggestion: add `${VERSION:-latest}` default to `docker-compose.yml` or commit a `.env` at repo root with `VERSION=...`. Small hygiene patch.
- **Stale pre-Plan-065 containers were left running from a prior session**. The `down` step before `up` is mandatory when the image tag changes; otherwise compose continues to use the old running containers. The image build step succeeded but the running containers were not replaced until an explicit `down`.
- **`docker compose exec` does not inherit runtime env built by the entrypoint.** The worker's `DATABASE_URL` is computed by `docker/entrypoint.sh` at startup (from `/run/secrets/db_password` + `DATABASE_URL_TEMPLATE`) but is only set inside the worker's *main* process. `docker compose exec python -c '...'` gets a fresh env that lacks `DATABASE_URL`, and the flow fails on `os.environ["DATABASE_URL"]`. Worked around by constructing `DATABASE_URL` inline in the exec shell. Worth documenting in the runbook for step 8's direct-invoke path.
- **Apple Silicon image tag `sapphire-flow:0.1.378` built cleanly** with `libgeos-c1v5`, `libexpat1`, and Plan 056's zarr/numcodecs upgrades — no arm64 wheel issues this pass.
- No Docker Desktop filesystem-permission issues on bind-mounts (Plan 060's CAMELS-CH bind-mount + Plan 065's overlay bind-mount both resolved inside the container, verified via `docker run --rm ls` smoke).

---

## Recommended follow-ups

Ordered by priority for the next A3 attempt:

1. **(P1, external)** Wait for MeteoSwiss cycle publication to recover, then retry step 8 with default config — no code change, just verify the normal fast path works.
2. **(P1, doc)** Plan 046 Rev 11 should incorporate finding F2 (JSON-string quoting for UUID deployment params) into §A3 steps 5 and 6; add the `DATABASE_URL` construction boilerplate to step 8's direct-invoke template.
3. **(P1, design call)** Decide F3: is `train-models` intentionally recent-only (then reorder A3 or remove step 4 for first-run) or should it assemble historical data too?
4. **(P2, detour plan)** F5: NWP adapter configurability — make `_MAX_FALLBACK_STEPS` configurable, raise/remove the 100-page pagination cap, consider STAC CQL server-side filtering.
5. **(P2, scale concern)** The onboard-stations wall-clock scales to ~26 h at 169 stations. Block A4 until we decide whether the initial historical-hindcast phase should be (a) skipped in onboard-stations and made a later step, or (b) batch-parallelised.
6. **(P3, plan text)** Plan 046 §A1 should soften the "validates multi-parameter pipeline (discharge + water_level)" claim per F7; Murten/lake is effectively an ingestion-only test today, and even that is blocked by operational-gating.
7. **(P3, hygiene)** `docker-compose.yml` `VERSION` default or repo-root `.env` — tiny change, big UX win.
8. **(P4, watch)** F6 compute-skills OOM — monitor on the A4 run; instrument `docker stats` polling during next rehearsal.

---

## Conclusion

The A3 dress rehearsal validated the Plan 065 overlay path end-to-end through step 7 (seven of nine steps). The remaining gaps (steps 4, 8) surface latent issues — one design question (train-models data window), one external outage (MeteoSwiss), two adapter configurability limits (fallback steps, pagination). None are regressions from Plan 065 or Plan 046 Rev 10.

**Signal for v0 readiness**: the 26-hour extrapolation for `onboard-stations` at 169 stations is the strongest operational blocker surfaced today. Worth resolving before A4 is attempted.

---

## 2026-04-23 re-run — step 8 forecast-cycle: PASS

**Trigger**: v0.1.412, the culmination of 10 commits today addressing
live-path gaps revealed by the first real forecast-cycle attempt since
Plan 045 landed. Rebuild sequence: rebuild prefect-worker image → `up
-d` → direct-invoke `run_forecast_cycle_flow(adapter=...)` against live
MeteoSwiss STAC.

**Result**: **PASS**.
`stations_attempted=4  stations_succeeded=4  stations_failed=0
forecasts_stored=4  alerts_checked=False  errors=()`. Wall-clock
~30 min total (1831890 ms); NWP fetch of ~2.9 GB dominates the
critical path.

**Behaviour notes**:
- `linear_regression_daily` failed predict for every station with
  "Insufficient lookback: need 7 rows, got 2" — lookback gap from
  the earlier 2026-04-21 rehearsal's thin observation ingest. The
  per-station fallback to `climatology_fallback` worked as designed
  and produced ensemble forecasts (ensemble_size=7,
  lead_time_hours=120.0). This is expected v0 behaviour, not a
  regression.
- `alerts_checked=False` — no alert thresholds configured in the
  current dress-rehearsal DB. Expected.
- Archive step logged a permission warning on `/data/nwp_grids/icon_ch2_eps`
  (Errno 13). Non-fatal; follow-up item.

**Bugs fixed in the 2026-04-23 live-path excavation**:
1. v0.1.405 — probe pagination (T2a's single-page check missed
   published cycles due to MeteoSwiss ref_dt-ascending ordering).
2. v0.1.406 — Dockerfile `libeccodes0` added; cfgrib couldn't load.
3. v0.1.407 — `dask[array]` added as runtime dep for
   `xr.open_mfdataset`.
4. v0.1.408-410 — three failed `open_mfdataset` kwarg-combo fixes
   that cascaded through xarray validation errors.
5. v0.1.409 — forecast-cycle no longer aborts when no station
   requests the NWP source (v0 models consume zero NWP features).
6. v0.1.411 — rewrote `_parse_grib_files` as explicit per-file loop
   (xr.open_mfdataset is the wrong tool for MeteoSwiss's one-message-
   per-file shape).
7. v0.1.412 — committed real ICON-CH2-EPS fixtures + integration
   test; drove the parse code against real data in 5 iterations,
   fixing scalar-vs-vector `number` coord handling, `t2m` vs `t_2m`
   shortName/data-var naming, scalar time-coord broadcast conflicts
   on `expand_dims`.

**Cross-plan impact**:
- Plan 046 A3 is now GREEN end-to-end. Stream C (Mac Mini glue) is
  ready to execute.
- Plan 067 Phase 2's T2a probe rewrite was necessary but not
  sufficient; the full fix required probe pagination (v0.1.405) on
  top. Plan 067's Appendix for Plan 047 should incorporate "paginate
  the availability probe" as a first-class requirement.
- The new `tests/unit/adapters/test_meteoswiss_nwp_real.py` with
  committed ICON-CH2-EPS fixtures (~12 MB) now gates any future
  parse-code regressions against real library semantics — closes
  the class of mock-gap bug this session exposed.

**Follow-ups (non-blocking)**:
- `/data/nwp_grids/icon_ch2_eps` volume permission (container-side
  perms or docker-compose mount).
- Consider bumping `libeccodes0` from Debian's 2.41.0 to 2.42.0+
  (cfgrib prefers it; currently a UserWarning only).
- Consider a `forecast_cycle.py` pre-check that skips NWP fetch
  entirely when no station requests the NWP source (currently we
  fetch 2.9 GB and discard most of it).

Plan 046 A4 (169-station scale-up) is still deferred to the
post-v0-deploy Plan-068 workstream per the v0-launch-roadmap D1
decision.
