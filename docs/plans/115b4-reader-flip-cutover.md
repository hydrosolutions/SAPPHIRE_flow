---
status: READY
created: 2026-07-15
plan: 115b4
parent: 115b
title: Reader flip + cutover — hybrid default, priority chain, camels-ch retirement, loudness & guards
scope: The RISKY behaviour change. Flips what data models are fed. Gated on 115b3's validation passing.
depends_on: [115b3]
blocks: [115c]
---

# Plan 115b4 — Reader flip + cutover

> **Design source: [Plan 115b](115b-weather-flow6-reachability.md)** — read §5 (parameter-drop fix +
> priority chain + distribution-shift gate + flip), §6 loudness, §7 health-by-effect, §10 converter
> guards, §11 dashboard, and the phase-5 deployment-choreography subsection. Carries **phases 5 and 6**.

## Status

**READY** (2026-07-16). Three independent Codex plan-review rounds → READY-TO-IMPLEMENT (round 1: 3
blockers — the non-executable single-deploy choreography, the false `downgrade()`-restores-binding rollback
claim, and an underspecified distribution-shift gate — the first two ESCALATED to and decided by the owner
[two-step release + standard backup-restore rollback]; round 2: 1 blocker [deploy gate conflated the two
releases] + 2 majors [phase-6 label / 5A returned-rows rule], all folded; round 3: no blockers, converged
on two text-consistency leftovers now fixed). Fourth and final chunk (115b1 → 115b2 → 115b3 → **115b4**).
**This is the high-risk landing** — it changes what data reaches models, so it is isolated as TWO sequenced
releases: a staging problem reverts without dragging back the schema (115b1) or the backfilled data (115b2).
Implementation authorised; hold at PR.

**⚠️ Gated on 115b3.** Do not flip until the validation gate's result is recorded and any flag
dispositioned.

**Fixer-round note (2026-07-17):** an independent Codex review of the implementation found
that `0033_retire_camels_ch_weather_binding.py` (§5E, Release B) had been committed
alongside Release A (5A–5D + phase-6) in the SAME commit — reproducing the exact
single-deploy-choreography hazard round 1 rejected. **5E has been split out to
[Plan 115b5](115b5-camels-ch-retire-migration.md)**, on a separate branch, not part of
this plan's `main` diff. `tests/unit/db/test_alembic_head_release_a.py` mechanically
enforces that `alembic/versions/` on `main` carries no camels-ch retire migration until
115b5's merge gate (Release-A staging confirmation) passes.

## Scope

### Phase 5 — the reader

- **5A — hybrid parameter-drop fix (BEFORE the flip).** `hybrid_reanalysis.py:78-84` silently `continue`s
  (drops the row) for any parameter with no configured chain; `StoreBackedReanalysisSource` (today's
  default) passes any parameter through. Flipping as-is is a **silent data-loss regression**. Rule
  (decided from the ROWS ACTUALLY RETURNED for that parameter/logical key, NOT a static source map —
  owner, round-1 MAJOR): **zero** sources returned rows → the parameter is genuinely absent, behave as
  today (no row); **exactly one** source returned rows → that source wins (do not raise); **two or more**
  distinct sources returned rows for a parameter with **no configured priority chain** → raise
  `ConfigurationError` (a nondeterministic winner is the bug). The decision is made per resolved
  parameter/key from the fetched rows, so it cannot drift from a stale config table.
- **5B — the priority chain, no CAMELS tier.** `precipitation: RHIRESD → RPRELIMD`; `temperature: TABSD`;
  `temperature_min: TMIND`; `temperature_max: TMAXD`; `relative_sunshine_duration: SRELD`. Plan 072's
  `… → CAMELS_CH` chains are retired.
- **5C — distribution-shift gate.** The flip changes where a feature's value comes from; a model fitted on
  CAMELS-sourced features that suddenly reads MeteoSwiss-sourced features is fed a different distribution
  (Plan 072 §175). The same path serves training, hindcast AND live forecast past-dynamic inputs. Before
  the flip: enumerate **active** artifacts + their past/future requirements; retrain on the new series, or
  hold the flip for affected stations. *(Repo review suggests today's models are probably unaffected —
  native/fallback declare no past/future dynamic features; the FI NWP model needs only future
  precip/temp — but that is an inference; the live artifact/assignment tables settle it. NEEDS-LIVE-DB.)*
- **5D — flip the reanalysis default to `hybrid`** (`config/deployment.py:111-113`) — the last step of **Release A**. Only after 5A.
  `tests/unit/config/test_deployment_reanalysis_source.py:24-39` locks the `single` default and updates deliberately. Verify
  CAMELS-only stations still resolve (the chain falls back correctly) — a test, not an assumption.
- **5E — retire the camels-ch weather binding, as a SEPARATE SECOND RELEASE (owner decision 2026-07-16,
  round-1 blockers 1+2).** `single` (default until 5D) reads `cfg.nwp_source` directly
  (`store_backed_reanalysis.py:35`); retiring the binding while `single` is still default leaves a station
  with **no readable reanalysis source**. The original "flip first, then migrate, in one deploy" is
  **NOT executable** — the repo runs `alembic upgrade head` in the `init` container **before** workers start
  (`docker-compose.yml:247-253`, `cicd.md:111-119`), so a retire migration in `head` would fire before there
  is any running system to confirm hybrid is serving.

  **Resolution — TWO SEQUENCED RELEASES on the standard deploy path (no bespoke alembic targets):**
  - **Release A = 5A–5D + phase-6** (param-drop fix, priority chain, distribution-shift gate, **flip
    default to `hybrid`**, plus the loudness/guards of phase-6). **No retire migration.** Deploy; **confirm
    the hybrid reader is serving** past-dynamic
    features on staging (the deploy gate below).
  - **Release B = 5E** (the `camels-ch` binding-retirement migration), shipped **only after** Release A is
    confirmed serving. Deploy on the standard path.

  This is reflected in the phase graph (phase-5 splits into 5A–5D then 5E; **5E `depends_on` the Release-A
  deploy-gate**, not merely on 5D as a code task). *(If cleaner, 5E may become its own tiny plan 115b5 — but
  it stays gated on Release A serving either way.)*

  **Rollback = the repo's standard path (backup restore + previous image, `cicd.md:137-139`)** — NOT a
  schema `downgrade()`. Do not claim a special reversible downgrade. The retire migration is destructive of
  the `camels-ch` binding rows, but that is safe to roll back by restore because the binding shape is
  deterministic (`nwp_source=forcing[0].source`, `extraction_type=POINT`, `status=ACTIVE`, `role=REANALYSIS`,
  `onboarding.py:365-371`; PK `(station_id, nwp_source)`, `db/metadata.py:164-193`). **The CAMELS forcing
  ROWS are NOT deleted** — they stay as the 115b3 validation reference + audit trail; only the *weather
  binding* is retired. (CAMELS remains the runoff/discharge + static-attribute + basin-polygon source.)

### Phase 6 — loudness + guards

- **6A — `WEATHER_HISTORY_INGEST` check type.** `ingest_weather_history_flow` has no
  `pipeline_health_store` param (`ingest_weather_history.py:255-262`) and `PipelineCheckType`
  (`types/enums.py:151-164`) has no weather-history value — build both + thread the store. **Note:**
  `pipeline_health.check_type` has **no** DB check constraint today (only `status` is constrained,
  `db/metadata.py:1088-1108`, `0001_v0_schema.py:748-762`), so there is nothing to "extend" — either add a
  NEW full check constraint enumerating all `PipelineCheckType` values, or add none (match the current
  no-constraint state). Do not claim a constraint that isn't there.
- **6B — health measured by EFFECT, never `rows_stored`.** `rows_stored` is `len(records)` after
  `on_conflict_do_nothing` (`ingest_weather_history.py:230`, `historical_forcing_store.py:52`), so a
  pure-duplicate re-fetch looks healthy. UNHEALTHY when `stations_targeted == 0` (config fault) and when a
  run inserts nothing over a full window — asserted via **actual DB rowcount** or a **non-advancing
  `MAX(valid_time)` per source**. Two distinct failures distinguished: "nobody bound" vs "bound but silent."
- **6C — converter guards, ALL THREE (round-1 major).** `point_forecast_to_records`,
  `elevation_band_to_records`, **and** `basin_avg_to_records` all write `WeatherForecastRecord.nwp_source`
  (`converters.py:21,50,79`) — not just the two an earlier draft named. Centralize a single
  **reanalysis-tag reject helper** and call it from all three, so a reanalysis row can never be written into
  the forecast table (Plan 071 §243; the code has no such check). Tests for each converter.
- **6D — dashboard forcing endpoint = HYBRID-RESOLVED** (decided). `api/routes/stations.py:452-490` today
  reads `historical_forcing` and ignores `source`, merging provenance streams. Route it through
  `select_reanalysis_source(mode="hybrid")` so it serves exactly what a forecast used, with the **winning
  `source` tag per point** (so an operator can spot a stuck/preliminary tail). **API wiring:** the route today
  depends only on a raw SQL connection and selects `valid_time,parameter,value` grouped by parameter, ignoring
  `source` (`stations.py:452-499`); rewire it to use `get_stores` → `station_store.fetch_reanalysis_bindings`
  → `select_reanalysis_source(mode="hybrid")`.

## Tests

- **The double-dark regression:** with the MeteoSwiss binding present and `hybrid` default, rows written
  under product tags are readable **end to end** by the default consumer. *Must fail against today's wiring.*
- **Priority, not supersession (§3):** for a `(station, valid_time, parameter)` covered by BOTH precip
  sources, a **direct source-keyed fetch returns BOTH rows**, while the **hybrid reader returns only the
  `RhiresD` winner**. *Two assertions.*
- **Parameter drop (5A), all three returned-row cases:** for a parameter with **no configured chain** —
  **zero** sources returned rows → behaves as today (absent, no raise); **exactly one** source returned rows
  → that single source **wins**, no raise; **two+** sources returned rows → **raises** `ConfigurationError`
  (nondeterministic winner). *Decided from returned rows, not a static map.*
- **CAMELS-only station survives the flip (5D)** — a station whose binding still literally reads
  `nwp_source="camels-ch"` (a pre-flip artifact) continues to serve its **backfilled MeteoSwiss
  past-dynamic features unchanged** through the hybrid chain (hybrid resolves by `station_id` + `role`,
  never by the binding's `nwp_source` literal) — not merely "returns empty without raising", which would
  also pass if the whole hybrid chain were dark.
- **Flow 6 health (6B):** `stations_targeted == 0` → UNHEALTHY; bound-but-no-inserts over a full window →
  UNHEALTHY (via a **before/after `MAX(valid_time)` comparison** per targeted source — captured BEFORE the
  fetch/store step and compared AFTER — not merely "does a row exist post-run", which is trivially true once
  the rolling window has ever been populated by a prior run), NOT via `rows_stored`.
- **Two-release ordering (5E):** the retire-camels migration (Release B) cannot leave a station unreadable —
  a deploy-gate that Release A's hybrid default is confirmed **serving past-dynamic features** BEFORE
  Release B ships. Split out to [Plan 115b5](115b5-camels-ch-retire-migration.md) on a separate branch
  (fixer round, 2026-07-17) — `main` never carries the retire migration until 115b5's merge gate passes.
  (Unit-testable, on `main`: `tests/unit/db/test_alembic_head_release_a.py` asserts the retire migration is
  **absent from `main`'s Alembic head**; a station on the hybrid reader serves rows.)
- **No `camels-ch` weather binding remains** after Release B ([Plan 115b5](115b5-camels-ch-retire-migration.md));
  this plan (Release A) leaves the binding in place. CAMELS forcing rows are untouched and still readable by
  a direct source-keyed fetch.
- **Converter guards (6C)** reject a reanalysis tag.

## Dependency graph

```json
{
  "phases": [
    {
      "id": "phase-5",
      "name": "Reader: param-drop fix -> chain -> distribution-shift gate -> flip; camels-ch retirement choreographed with the flip",
      "tasks": ["5A-hybrid-parameter-drop-raise", "5B-chain-rhiresd-then-rprelimd-no-camels-tier", "5C-distribution-shift-gate", "5D-flip-default-to-hybrid"],
      "parallel": false,
      "note": "Release A. STRICT ORDER 5A->5D. 5E is NOT here — it is a separate SECOND release (phase-5b) gated on Release-A confirmed serving; rollback is standard backup-restore, not schema downgrade.",
      "depends_on": ["plan-115b3"]
    },
    {
      "id": "phase-5b",
      "name": "Release B (SEPARATE deploy) — retire the camels-ch weather binding, only after Release A is confirmed serving hybrid",
      "tasks": ["5E-retire-camels-weather-binding-migration"],
      "parallel": false,
      "note": "Second release on the standard deploy path. depends_on the WHOLE of Release A (phase-5 = 5A-5D AND phase-6 = loudness/guards) being deployed and the Release-A staging deploy-gate confirmed serving — NOT just the 5D code. Rollback = backup-restore + previous image (cicd.md), not schema downgrade.",
      "depends_on": ["phase-5", "phase-6"]
    },
    {
      "id": "phase-6",
      "name": "Loudness + guards — ships WITHIN Release A (alongside 5A-5D, before the Release-A deploy gate)",
      "tasks": ["6A-weather-history-ingest-check-type", "6B-health-by-effect", "6C-converter-guards", "6D-dashboard-hybrid-resolved"],
      "parallel": true,
      "depends_on": ["phase-5"]
    }
  ]
}
```

## Exit gates

```bash
uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/
uv run pyright src/
uv run pytest
```

**Deploy gates (staging, do not skip) — TWO gates, one per release (round-2 blocker: do not conflate them):**
- **Release A gate (after 5A–5D + phase-6, NO retire migration):** `ingest-weather-history` reports a
  **non-zero** effect (advancing `MAX(valid_time)` per source), a station serves past-dynamic features via
  the `RHIRESD → RPRELIMD`/`TABSD`/… chain, and a forecast cycle completes on the new series. The
  `camels-ch` weather binding is **still present** at this gate (it is retired only in Release B). Confirm
  the retire migration is **absent from Release A's `head`** — mechanically enforced by
  `tests/unit/db/test_alembic_head_release_a.py` on `main` (fails if a camels-ch retire migration file
  exists in `alembic/versions/`, or if revision `0032` is no longer the true leaf), not merely documented.
  **A green flow is not evidence.**
- **Release B gate (after 5E ships, only once Release A is confirmed serving):** the `camels-ch` weather
  binding is **gone**, its forcing ROWS remain readable by a direct source-keyed fetch, and a forecast
  cycle still completes on the hybrid chain.

**Doc sync:** `docs/v0-scope.md §A12` + `docs/architecture-context.md:140,574` (CAMELS is now a validation
reference, not the training-forcing source; record the self-derived provenance
`RhiresD`/`TabsD`/`TminD`/`TmaxD`/`SrelD`, our polygons); `docs/standards/cicd.md` (the flip + retirement
choreography as a deploy step).

## Provenance

Extracted from Plan 115b (phases 5–6), 2026-07-15. The risky landing, deliberately isolated.
