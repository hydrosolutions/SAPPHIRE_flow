---
status: DRAFT
created: 2026-07-21
plan: 136
title: BAFU LINDAS observation archive collector (all gauges, quarantined, sub-daily)
scope: A standalone quarantined collector that archives ALL BAFU gauges' real-time observations from LINDAS, decoupled from station onboarding. Swiss. Supersedes Plan 058.
depends_on: []
blocks: []
supersedes: [58]
---

# Plan 136 — BAFU LINDAS observation archive collector

## Status

**DRAFT.** Owner decision locked (2026-07-21, below). Went through a `/plan` adversarial round
(2026-07-21) that escalated with 1 blocker + 9 majors; this revision folds all of them, grounded in
a **live LINDAS probe** (§ Live LINDAS probe evidence) that settled the two empirical unknowns
(cadence, endpoint load) and caught a watchdog trap. For a confirming `/plan` round before READY.

## Context — collect now, because LINDAS has no history

LINDAS (`lindas.admin.ch/foen/hydro`) serves BAFU river-gauge **observations** in **real time only** —
it carries **no historical time series** (Plan 111 Phase 0, confirmed 2026-07-08; memory
`project_bafu_lindas_realtime_only`). Today we ingest only the **2 onboarded operational stations**
(Porte_du_Scex, Rheinfelden-Messstation) via `flows/ingest_observations.py` → `hydro_scraper`
(`stations_polled=2`, verified live 2026-07-21). The full BAFU network publishes on the
same graph but we don't collect it.

Because there is no back-catalogue, **the archive can only be built forward** — so starting a full-network
collector **now** maximizes the eventual sub-daily series length. This is the same rationale, and the
same shape, as the Plan 111 **forecast** collector (`collect-bafu-forecasts`), and it is that plan's
**natural complement**: BAFU forecasts + BAFU observations together are exactly what the Plan 111
benchmark scores (forecast-vs-own-observation). This plan collects the observation half; it does **not**
build the scorer (Plan 111 G3).

## Live LINDAS probe evidence (2026-07-21, grounds cadence + load + freshness)

A single courteous whole-graph probe (one `POST`, identifying `User-Agent`, `LIMIT 5000`) against
`lindas.admin.ch/query` over `<lindas.admin.ch/foen/hydro>`, filtered to the four dimension predicates,
returned:

- **Feasibility + load — settled.** `HTTP 200 in 0.12 s`, **348 KB**, **730 rows** for **233 gauges**
  (LIMIT nowhere near hit). A single whole-graph request returns the entire network's current state
  cheaply. Polling this **hourly** (below) is negligible load on the shared public endpoint — the earlier
  "whole-graph SPARQL courtesy/cost" major is resolved by measurement, not assumed away. Courtesy rules
  still apply: identifying `User-Agent`, poll no faster than the update cadence, single request per run.
- **Cadence — settled, and it corrects an earlier assumption.** Earlier drafts assumed ~10-min updates.
  The live `measurementTime` grid shows **228 of 235 gauges on the top of the hour** (max `15:00:00`), a
  handful off-grid (`:10/:20/:30/:35/:40`). BAFU/LINDAS effectively **refreshes hourly**. The collector
  polls **hourly** (cron `5 * * * *`, a 5-min offset to catch the fresh top-of-hour value). This makes the
  cadence *empirically known now* — so it is no longer an output of a later task, which **dissolves the
  T6↔T8 dependency circularity** the reviewer flagged.
- **Inventory.** 233 gauges (**199 river + 34 lake**); discharge 187, waterLevel 224, waterTemperature 84
  — consistent with Plan 029's 199+34 URI split, and far more than the 2 we ingest operationally.
- **Freshness trap the probe caught.** The oldest `measurementTime` is **`2025-05-28`** — a gauge dead
  >1 year still present in the graph. A naïve "all gauges fresh" watchdog check would fire forever.
  Freshness must be **network-level** (newest `measurementTime` recent, or ≥N% of gauges fresh), never
  per-gauge-minimum. Folded into T8/T9 below.

> The probe key/endpoint were used read-only for one request; nothing was persisted. The recorded facts
> above are what the plan is built on; the collector re-derives the inventory live at run time (T2).

## Supersedes Plan 058 (BLOCKER-1 resolution)

Plan 058 (`docs/plans/058-bafu-lindas-archive-collection.md`, still `DRAFT`, listed active at
`docs/plans/README.md:45`) proposes the **opposite** architecture: *"Use the Mac Mini v0 deployment as
the LINDAS archive … There is **no separate archive-collection pipeline**"* (`058:28-31`) — i.e. onboard
the full roster as real stations and let the `observations` **table** accumulate via Flow 2, then export a
promotion fixture. Plan 136 **rejects and replaces** that approach with a standalone quarantined collector
(Decision below). The two cannot both be active — 058's roster-widening (`058:T1`) would entangle ~170
archive-only gauges with the operational forecast set, exactly what this plan avoids.

**Action on adoption (P0, T7):** mark Plan 058 `SUPERSEDED by 136` in its header and in
`docs/plans/README.md:45`, and migrate the two 058 artefacts still worth keeping — the live-LINDAS
schema-drift watch (058 T5, already **shipped** as `tests/integration/live/test_lindas_live_schema.py` +
`.github/workflows/live-lindas-weekly.yml`; keep as-is, no change) and the reference-fixture *promotion*
idea (058 T3, still deferred) — into this plan's orbit (§ Relationship to Plan 058). This plan does **not**
delete 058's shipped watch; it inherits it.

> NOTE (scope boundary): editing `058-*.md` and `README.md` is a documentation action listed as task **T7**
> below; it is not code and does not bump the version (per "plans direct to main").

## Decision (owner, 2026-07-21): quarantined collector, onboard later

**Quarantined "collect-now, import-later" archive** — a standalone collector that writes all gauges'
observations to a quarantined store keyed by **BAFU gauge code + LINDAS kind (river/lake)** (not
`station_id`), **decoupled from station onboarding**. **Rejected: onboarding all BAFU gauges** as real
stations to route them through `ingest-observations` into the `observations` table (the Plan 058 approach)
— heavier, entangles archive-only gauges with the operational forecast set, and unnecessary to start the
clock. Specific gauges are onboarded/imported from the archive **when a real use** (training, benchmarking)
needs them.

**Why key on `(gauge_code, lindas_kind)` and not `gauge_code` alone (MAJOR resolution):** the same numeric
code can appear under both the `river/observation/{code}` and `lake/observation/{code}` URI paths, the two
paths expose **different** parameter sets (river: discharge + waterLevel + waterTemperature; lake:
waterLevel only — Plan 029 `docs/plans/archive/029-lindas-adapter-fix.md:18-23`), and the URI-path kind can
even **disagree with our TOML classification** — station **2004** is served **lake-only** despite the TOML
marking it river (`tests/integration/live/test_lindas_live_schema.py:21-25,68-75`). The archive therefore
records the LINDAS-observed kind as ground truth, independent of any onboarding classification.

## Objective

Start a forward-only sub-daily observation archive for the full BAFU gauge network from LINDAS, with the
smallest safe machinery, mirroring the deployed-and-proven `collect-bafu-forecasts` collector — so that a
long sub-daily series accrues from today, importable later without re-collection.

## Non-goals

- **Not** onboarding BAFU gauges as stations, and **not** writing observation **values** or any
  station-linked data to the DB (quarantine). *Precisely:* the only DB write on this path is the
  best-effort `pipeline_health` **heartbeat metadata** (no observation values, no `station_id`), written
  **after** the archive gate — exactly as `collect-bafu-forecasts` does (`collect_bafu_forecasts.py:158-208`).
  "Quarantine" means "no observation values / no station identity in the DB", not "zero DB rows".
- **Not** touching the operational `ingest-observations` path (the 2 operational stations stay as-is).
- **Not** constructing `RawObservation`/`StationId` on this path (see Decision T2 below — a new
  gauge-code-keyed row type is used instead).
- **Not** the benchmark/scorer — that is Plan 111 (G3); this is the observation-collection half only.
- **Not** backfill — LINDAS has no history; the archive is forward-only by nature.
- **Not** alerting/QC on these gauges — archive-only, evaluation-tier.

---

## Design commitments locked before `/plan` hardens the query (MAJOR resolution)

The reviewer flagged that the two building blocks this plan gestured at reusing are structurally
**incompatible** with a gauge-code-keyed, un-onboarded archive. Both are now closed off in the plan doc
itself:

### DC-1 — SELECT ?subject + a NEW subject-grouping parser (do NOT reuse `_parse_bindings` as-is)

`hydro_scraper._build_sparql_query` is **per-station** — it `BIND`s exactly one `?subject` and returns
`SELECT ?predicate ?object` for that one node (`hydro_scraper.py:173-192`), and `fetch_observations` loops
one station at a time (`hydro_scraper.py:58-112`). Its parser, `_parse_bindings`, **assumes a single
subject per call**: it accumulates *all* bindings into one flat `timestamp_str` + `param_values` pair
(`hydro_scraper.py:194-234`) and is only ever invoked per-station today (`hydro_scraper.py:97`). Fed the
flat triple-list a true whole-graph query returns (many subjects interleaved), it would **silently merge
every gauge's measurements into one row per poll** — a correctness bug, not a missing feature.

**Locked:** the fetch-all query MUST `SELECT ?subject ?predicate ?object` (project `?subject`, not `BIND` a
single one) over the whole `foen/hydro` graph, and the collector needs a **new subject-grouping parser**
(group triples by `?subject`, then apply per-subject logic analogous to but **distinct from**
`_parse_bindings`). `hydro_scraper.py`'s existing per-station parser is **NOT reused as-is** on this path.

### DC-2 — a new gauge-code-keyed row type; `RawObservation`/`StationId` are never constructed

The adapter path this plan draws its dimension mapping from only ever produces `RawObservation`, whose
`station_id: StationId` field is **mandatory** (`src/sapphire_flow/types/observation.py:21-29`) — there is
no way to construct one for a gauge that, by design, has no `StationId` (Non-goals). The **sibling forecast
collector already solved this** with a plain-string key: `BafuForecastRow.station_key: str`
(`src/sapphire_flow/types/bafu_forecast.py:46,64`), no DB identity.

**Locked:** this collector defines a new gauge-code-keyed row type — working name **`BafuObservationRow`**
with `gauge_code: str`, `lindas_kind: Literal["river", "lake"]`, `parameter: str`, `value: float`,
`measurement_time: UtcDatetime` — mirroring `BafuForecastRow`. `RawObservation` / `StationId` are **never**
constructed on this path. This closes the "or a collector-local query" ambiguity that previously left
`Scope §1` open to drifting back toward the incompatible type.

### DC-3 — per-subject river/lake discrimination from the URI segment

There is no per-subject `StationKind` available the way the per-station path has one today (the per-station
path is *told* the kind by its `StationConfig`, `hydro_scraper.py:176`). The whole-graph query has only the
subject URI. **Locked:** the subject-grouping parser discriminates river-only params
(discharge/waterTemperature) from lake-only params (waterLevel) by the `/river/observation/` vs
`/lake/observation/` segment already visible in the subject URI (the same segment
`_build_sparql_query` constructs at `hydro_scraper.py:177`), and stamps that kind onto `lindas_kind`. The
parser trusts the URI segment, not any external classification — consistent with the 2004 lake/river
disagreement noted above.

---

## Phase / task structure (BLOCKER-2 resolution)

Each task lists **scope**, **files**, and **verification**. The JSON dependency graph is at the end of this
section. Exit gates (§ Exit gates) apply to every code task.

### Phase A — types + query + parser (foundation)

#### T1 — `BafuObservationRow` row type (DC-2)

- **Scope:** new frozen dataclass module mirroring `types/bafu_forecast.py` (evaluation-only,
  quarantined, never DB / never `ModelId`). Fields per DC-2. No `StationId` import.
- **Files:** `src/sapphire_flow/types/bafu_observation.py` (new);
  `tests/unit/types/test_bafu_observation.py` (new — construction, kw-only, frozen, `lindas_kind`
  Literal round-trips).
- **Verification:** `uv run pytest tests/unit/types/test_bafu_observation.py`.

#### T2 — fetch-all SPARQL query + subject-grouping parser (DC-1, DC-3)

- **Scope:** a **collector-local** query builder + parser (NOT edits to `HydroScraperAdapter`'s
  per-station path, which the operational flow depends on). Query `SELECT ?subject ?predicate ?object`
  over `<https://lindas.admin.ch/foen/hydro>` filtered to the dimension predicates the adapter already
  maps (`discharge`, `waterLevel`, `waterTemperature`, `measurementTime` — `hydro_scraper.py:36-50`),
  with **no** per-station `BIND` and **no** onboarded-station list. Parser groups bindings by `?subject`,
  derives `(gauge_code, lindas_kind)` from the URI segment (DC-3), and yields `list[BafuObservationRow]`
  — one row per (subject, parameter). `/plan` confirms the exact query against the live graph (which
  gauges + parameters are exposed) but MUST keep the SELECT-?subject + subject-grouping shape locked
  here.
- **Files:** `src/sapphire_flow/adapters/bafu_observation.py` (new — query + parser + polite httpx client,
  mirroring `adapters/bafu_forecast.py`); `tests/unit/adapters/test_bafu_observation.py` (new) plus a
  recorded multi-gauge fixture.
- **Verification:** parser test over a faked whole-graph response containing **≥3 distinct subjects of
  both kinds** proves (a) rows are grouped per subject, not merged into one; (b) a lake subject yields
  waterLevel only; (c) a river subject yields discharge/waterLevel/waterTemperature; (d) `gauge_code` +
  `lindas_kind` come from the URI. `uv run pytest tests/unit/adapters/test_bafu_observation.py`.

### Phase B — collector flow + archive (depends on A)

#### T3 — collector flow (mirror `collect-bafu-forecasts`)

- **Scope:** a `@flow(name="collect-bafu-observations")` mirroring the forecast collector's safeguards
  (`flows/collect_bafu_forecasts.py`): **quarantine** — writes **only** under the configured archive path,
  **never** observation values / a `station_id` to the DB, **blank/unset path ⇒ no-op** (the exact
  `_configured_path is None or not str(_configured_path).strip()` gate at
  `collect_bafu_forecasts.py:485-494`); **restatement-safe dedup** (BLOCKER — see below); **atomic**
  temp+rename writes (`collect_bafu_forecasts.py:231-239`); **polite client** (identifying `User-Agent`,
  request cap/retry). A single whole-graph request per run — no per-station fan-out.
- **Restatement-safe dedup (BLOCKER resolution).** BAFU can **restate** a value at an unchanged
  `measurement_time` (a correction) — the repo already handles this class for operational observations by
  updating on value change (`stores/observation_store.py:97`). Dedup on
  `(gauge_code, lindas_kind, parameter, measurement_time)` **alone would silently drop the correction** —
  data loss. **Locked design:** the archive is **append-only immutable snapshots** (T4), so the identity
  key detects a *re-fetch of unchanged data* (skip), while a *changed value at the same identity* is a
  **new snapshot, never a delete-and-replace**. Concretely: each cycle carries an injected `fetched_at`
  (dependency-injected clock, per CLAUDE.md — never `datetime.now()` in the flow body), and dedup is at the
  **snapshot level** — skip the write **iff** this cycle's parsed content is byte-identical to the
  immediately-previous archived cycle (content hash). A restatement changes content ⇒ a new snapshot file ⇒
  preserved. The "current value per identity" is reconstructed at **import** time as the row with the
  greatest `fetched_at` — the quarantine never has to decide correctness at collection time.
- **Files:** `src/sapphire_flow/flows/collect_bafu_observations.py` (new);
  `tests/unit/flows/test_collect_bafu_observations.py` (new).
- **Verification:** quarantine no-op test (blank `archive_base_path` ⇒ zero writes, no DB touch); dedup
  test (second run over a **byte-identical** snapshot archives zero new files); **restatement test** (a
  second run where one gauge's `value` changed at the same `measurement_time` archives a **new** snapshot
  and does **not** overwrite/drop the first — the corrected and original values both survive, ordered by
  `fetched_at`); multi-gauge archive test (one run archives many distinct gauges). `fetched_at` is
  injected, so the test is deterministic. `uv run pytest tests/unit/flows/test_collect_bafu_observations.py`.

#### T4 — quarantined parquet archive store

- **Scope:** a parquet archive mirroring `bafu_forecast_archive` (`/data/bafu_forecasts`) — a new
  `bafu_observation_archive` volume at `/data/bafu_observations`, forward-only permanent retention, not the
  `observations` table.
  - **Layout — LOCKED to per-cycle snapshots (MAJOR resolution).** One poll ⇒ one immutable parquet
    snapshot of the whole-graph result (all gauges/params in that cycle), named/partitioned by
    `fetched_at` (e.g. `.../{YYYY}/{MM}/{DD}/obs-{fetched_at:%Y%m%dT%H%M%SZ}.parquet`). Per-cycle (not
    per-gauge) is what makes the restatement design (T3) trivially correct — a snapshot is a point-in-time
    fact that is never rewritten — and it makes the completion-marker unambiguous: **the parquet file's
    atomic temp+rename existence IS the marker** (same rationale as `collect_bafu_forecasts.py:272-283`);
    a half-written cycle never appears. Row schema = `BafuObservationRow` fields (DC-2) **plus a
    `fetched_at: UtcDatetime` provenance column** (the snapshot's cycle time), so import can reconstruct
    latest-value-per-identity by `max(fetched_at)`. Storage: ~348 KB/cycle × hourly ≈ ~8 MB/day ≈ ~3 GB/yr
    — acceptable for permanent staging retention; consecutive-identical snapshots are deduped away (T3).
  - **Raw-payload archival (MAJOR resolution).** The forecast collector archives the **raw** upstream
    payload alongside the parsed rows as forward-only safety
    (`collect_bafu_forecasts.py` raw-snapshot path). Mirror it: persist the raw SPARQL-results JSON of each
    archived cycle (same atomic write, alongside the parsed parquet, under the same `fetched_at` key) so a
    future parser change can re-derive rows without re-collection. Skipped only when the cycle itself is
    deduped (byte-identical) — the raw is redundant then.
- **Files:** helper functions inside `flows/collect_bafu_observations.py` (T3); covered by T3 tests.
- **Verification:** included in T3 (snapshot layout + `fetched_at` column + raw-JSON companion + atomic
  marker asserted there).

### Phase C — config, deploy wiring, watchdog (each depends on the phase it touches)

#### T5 — `DeploymentConfig.bafu_observation_archive_path` loader (MAJOR resolution)

- **Scope:** add `bafu_observation_archive_path: Path | None = None` to `DeploymentConfig`
  (mirroring `bafu_forecast_archive_path`, `config/deployment.py:158-162`); in `load_config` parse
  `[adapters.bafu_observation].archive_base_path`, **normalize blank/whitespace → None** (the identical
  guard at `config/deployment.py:403-418`), and set it before `DeploymentConfig.model_validate`
  (`config/deployment.py:428`). Add the config-reference doc entry.
- **Files:** `src/sapphire_flow/config/deployment.py`; `tests/unit/config/test_deployment.py` (two new
  tests mirroring `:267-283` — defaults-to-None, parsed-from-adapters-section, plus a blank-string→None
  test); config-reference docs (`docs/standards/cicd.md` config table or the deployment-config reference,
  wherever `bafu_forecast_archive_path` is already documented).
- **Verification:** `uv run pytest tests/unit/config/test_deployment.py`.

#### T6 — deploy wiring (register_deployments + docker-compose + overlay) (MAJOR resolution)

- **Scope:**
  1. `register_deployments._build_specs()`: add a `collect-bafu-observations` `DeploymentSpec`
     (`flow_module="sapphire_flow.flows.collect_bafu_observations"`, `concurrency_limit=1`, default
     `WORK_POOL`, `cron=os.environ.get("SCHEDULE_COLLECT_BAFU_OBSERVATIONS", "5 * * * *")` — mirroring
     the forecast spec at `register_deployments.py:46,108-114`). The default is **`5 * * * *` (hourly at
     :05)**: the live probe (§ Live LINDAS probe evidence) established that LINDAS refreshes **hourly** on
     the top of the hour, so hourly-at-:05 catches each fresh value with minimal lag and negligible load.
     The cadence is a **known constant here**, not a value produced by T8 — this is what removes the former
     T6↔T8 circularity.
  2. **docker-compose `init` service env (the finding's core point):** add
     `SCHEDULE_COLLECT_BAFU_OBSERVATIONS: ${SCHEDULE_COLLECT_BAFU_OBSERVATIONS:-5 * * * *}` to the
     `init` service `environment:` block (`docker-compose.yml:269-275`) — without this the new env var
     never reaches `register_deployments` (which runs inside `init`, `docker-compose.yml:266`) and the
     schedule silently falls to the code default.
  3. `bafu_observation_archive` named volume (`docker-compose.yml:311`, mirroring
     `bafu_forecast_archive`) mounted `:/data/bafu_observations:rw` on the worker service that runs the
     collector (mirroring `docker-compose.yml:122`).
  4. `config/overlays/mac-mini.toml`: `[adapters.bafu_observation]\narchive_base_path =
     "/data/bafu_observations"` as the enable switch (unset ⇒ no-op), mirroring `mac-mini.toml:7-8`.
- **Files:** `src/sapphire_flow/cli/register_deployments.py`; `docker-compose.yml`;
  `config/overlays/mac-mini.toml`; `tests/unit/cli/test_register_deployments.py`; **doc-sync** — add the
  `bafu_observation_archive` volume + the `collect-bafu-observations` deployment to the authoritative
  Compose topology / named-volume table in `docs/standards/cicd.md` (wherever `bafu_forecast_archive` /
  `collect-bafu-forecasts` are already listed), per CLAUDE.md "every code change updates affected docs".
- **Verification:** update `test_register_deployments.py` — bump the spec count (`:97` currently asserts
  `len == 11` → 12), add `collect-bafu-observations` to `DEPLOYMENT_NAMES`, and add cadence-default +
  env-override tests mirroring `:101-111`. `docker compose config` parses (verifies the init env + volume
  wiring). `uv run pytest tests/unit/cli/test_register_deployments.py`.

#### T7 — supersede Plan 058 (docs only)

- **Scope:** header of `docs/plans/058-*.md` → `SUPERSEDED by 136`; `docs/plans/README.md:45` entry →
  `SUPERSEDED by 136`; add a one-line pointer from 058 to this plan. No code, no version bump (plan-doc
  change).
- **Files:** `docs/plans/058-bafu-lindas-archive-collection.md`; `docs/plans/README.md`.
- **Verification:** grep shows no remaining "active" claim for 058.

### Phase D — heartbeat + watchdog (depends on B + C)

#### T8 — Flow-4 heartbeat (cadence already fixed by the live probe)

Cadence is **not** an open probe — the live evidence fixed it at **hourly** (T6, cron `5 * * * *`). The
collector may still **log** the observed inclusion count + newest `measurementTime` per run as an
operational fact, but nothing downstream waits on it. T8 is the heartbeat + its freshness semantics.

- **Scope:**
  1. **Heartbeat:** one best-effort `PipelineHealthRecord` per run, `check_type=` a **new**
     `PipelineCheckType.BAFU_OBSERVATION_FRESHNESS` enum member (mirroring `BAFU_FORECAST_FRESHNESS`,
     `src/sapphire_flow/types/enums.py:151-163`), written via the same best-effort/never-fatal pattern as
     `collect_bafu_forecasts._append_bafu_health_record` (`collect_bafu_forecasts.py:158-208`).
  2. **Freshness semantics — network-level, not per-gauge (MAJOR + probe-trap resolution).** The health
     record's freshness signal is the **newest `measurement_time` across the network** (equivalently
     ≥N% of gauges fresh), **never** the per-gauge minimum: the probe found a gauge stuck at
     `2025-05-28` (dead >1 yr, still in the graph), so a per-gauge-min or all-gauges-fresh rule would be
     permanently CRITICAL. Normal = ~233 gauges / ~730 rows and a newest `measurement_time` within the
     last hour.
  3. **Empty / error whole-graph response (MAJOR resolution).** A whole-graph query returning **zero
     rows** (or an HTTP/parse error) is an **outage or schema drift**, not a quiet no-op: the heartbeat
     records a **failed** run (`is_stale`/failure status) rather than a healthy one, so the watchdog (T9)
     escalates. The existing weekly live schema-drift test (`test_lindas_live_schema.py`) guards the
     *structure* independently; T8 guards the *runtime emptiness* at collection time.
- **Files:** `src/sapphire_flow/types/enums.py` (new enum member) **and its authoritative doc**
  (`docs/spec/types-and-protocols.md` `PipelineCheckType` listing — the enum must not drift from the spec,
  per CLAUDE.md "every code change updates affected docs");
  `src/sapphire_flow/flows/collect_bafu_observations.py` (heartbeat call, T3); T3 tests extended.
- **Verification:** a successful run writes a `BAFU_OBSERVATION_FRESHNESS` record whose freshness reflects
  the **newest** gauge (a fixture with one stale + many fresh gauges is **healthy**); an **empty**
  whole-graph response writes a **failed** record; a health-store outage does not fail the run.
  `uv run pytest tests/unit/flows/test_collect_bafu_observations.py`.

#### T9 — generalize the host watchdog, then add observation freshness (MAJOR resolution)

The host watchdog is **hardcoded to `bafu_forecast_freshness`** — the probe URL constant
(`ops/watchdog.py:54-56`), URL derivation (`:59-65`), stale threshold `BAFU_STALE_THRESHOLD` (`:68-71`),
state field `consecutive_bafu_failures` (`:81-84`), and the whole ~57-line BAFU block in `run_once`
(`:427-484`) all name only the forecast collector, and a new heartbeat does **not** automatically produce
alerts. Copy-pasting that block for a near-identical second check would ~double the duplication; with a
**second** instance now concrete, the cheap-and-right move is to **generalize once, then add a row**.

- **Scope:**
  1. **Refactor (behaviour-preserving):** extract the forecast BAFU block into a **table-driven
     freshness-check loop** over a small spec list — each entry a
     `(check_name, health_detail_url, stale_threshold, state_field)` — iterated in `run_once`, each
     iteration reusing the existing generic `should_alert_health` hysteresis policy (`:264-276`) and the
     shared alert formatter. The forecast check becomes the **first row** of this table; its existing
     tests (thresholds/hysteresis/messages) must stay green unchanged — that green is the proof the
     refactor preserved behaviour (lock it Plan-105-style: the refactor lands with the *old* forecast
     tests untouched and passing).
  2. **Add the observation row:** a second table entry for `bafu_observation_freshness` with
     `state_field=consecutive_bafu_obs_failures` (add to `WatchdogState`, backward-compatible load
     defaulting the absent key to 0, exactly as `:99` does for the forecast field), its health-detail URL,
     and a `BAFU_OBS_STALE_THRESHOLD` sized to the **hourly** feed — stale after **~3 h** (three missed
     hourly cycles), *not* the ~1 h a 10-min feed would use (the probe corrected the cadence). Add the
     `--bafu-obs-health-detail-url` CLI arg + `WatchdogConfig` field (mirror `:344-346,519-526`).
- **Files:** `src/sapphire_flow/ops/watchdog.py`; `tests/unit/ops/test_watchdog.py` (existing forecast
  tests unchanged as the refactor lock; new coverage for the observation row: first-failure alert,
  every-6th, recovery, stale-vs-degraded, backward-compatible state load).
- **Verification:** `uv run pytest tests/unit/ops/test_watchdog.py` (old + new both green).

### Dependency graph

```json
{
  "phase-a-foundation": {
    "tasks": ["T1", "T2"],
    "sequential": false,
    "note": "T2 imports the T1 row type; run T1 first if serialized, else T2 stubs the type.",
    "depends_on": []
  },
  "phase-b-collector": {
    "tasks": ["T3", "T4"],
    "note": "T4 helpers live inside the T3 flow module; single build unit.",
    "depends_on": ["phase-a-foundation"]
  },
  "phase-c-config-deploy": {
    "tasks": ["T5", "T6", "T7"],
    "parallel": "T5/T6/T7 are independent files (config vs deploy-wiring vs plan-docs)",
    "depends_on": ["phase-b-collector for T6 (registers the flow); T5/T7 depend on nothing but land together"]
  },
  "phase-d-heartbeat-watchdog": {
    "tasks": ["T8", "T9"],
    "sequential": true,
    "note": "T9 (watchdog) probes the heartbeat T8 emits. NO T6 back-dependency: the cadence is fixed at hourly by the live probe, so T6's cron default is a known constant, not a T8 output — the former circularity is gone.",
    "depends_on": ["phase-b-collector", "phase-c-config-deploy"]
  }
}
```

---

## Live-inventory note (MINOR resolution)

Earlier drafts asserted "ALL ~170" gauges. The **live probe (2026-07-21) confirms 233 gauges — 199 river +
34 lake** — matching Plan 029's URI-split count (`docs/plans/archive/029-lindas-adapter-fix.md:20-23`) and
distinct from `config.toml`'s 169 CAMELS-CH-matched `[onboarding].basin_ids` (a different population). This
plan still does **not** hard-code the count: the included set is whatever the **all-gauge whole-graph
query** (T2) returns live, and the run logs the inclusion count each cycle (T8) — the 233 figure is the
current fact, not a coded assumption. The T2 parser test uses a fixture containing **the same numeric code
under both `/river/observation/` and `/lake/observation/`** (the 2004-class collision) to prove the
`(gauge_code, lindas_kind)` key keeps them distinct (MINOR test-coverage resolution).

## Relationship to Plans 111 and 058

- **Plan 111** (BAFU forecast benchmarking) collects **forecasts** (`collect-bafu-forecasts`, merged) and
  is gated on a licence for **publishing** the comparison. This plan collects the **observations** the
  benchmark scores against — the two collectors together make the benchmark *possible*; publishing stays
  under Plan 111 Gate G1. No licence is needed to *collect* public LINDAS data (same basis as the forecast
  collector).
- **Plan 058** is superseded (§ Supersedes Plan 058). Its one **shipped** artefact — the weekly
  live-LINDAS schema-drift watch (`tests/integration/live/test_lindas_live_schema.py` +
  `.github/workflows/live-lindas-weekly.yml`) — is kept as-is and now also guards *this* collector's
  parser (same endpoint, same dimension names). Its deferred reference-fixture *promotion* idea (058 T3)
  remains deferred and out of scope here.

## Tests (summary — detailed per task above)

- Parser (T2): a faked whole-graph response with ≥3 distinct subjects of both kinds — **including the same
  numeric code under both `/river/` and `/lake/`** — yields **grouped** per-subject rows (not one merged
  row), correct params per kind, `gauge_code`/`lindas_kind` from the URI, and the collision kept distinct.
- Dedup (T3): a second run over a **byte-identical** snapshot archives zero new files.
- **Restatement (T3, BLOCKER):** a re-run where one gauge's `value` changed at the same `measurement_time`
  archives a **new** snapshot and preserves the original — both survive, ordered by injected `fetched_at`.
- Quarantine (T3): blank `archive_base_path` ⇒ no-op (no writes, no DB touch); a set path writes only
  under it; the only DB write is the heartbeat (no observation values / no `station_id`).
- Raw archival (T4): each archived cycle persists the raw SPARQL-results JSON alongside the parsed parquet.
- Config (T5): `bafu_observation_archive_path` defaults to None, parses from
  `[adapters.bafu_observation]`, blank→None.
- Deploy (T6): `_build_specs()` includes `collect-bafu-observations` (count 11→12) with cron default
  `5 * * * *`; env override works; `docker compose config` parses.
- Heartbeat (T8): a successful run writes a `BAFU_OBSERVATION_FRESHNESS` record whose freshness reflects
  the **newest** gauge (one-stale-many-fresh fixture is healthy); an **empty** whole-graph response writes
  a **failed** record; a health-store outage does not fail the run.
- Watchdog (T9): the **existing forecast tests stay green unchanged** (refactor-behaviour lock); new
  observation-row tests cover stale (~3 h)/degraded/recovery alerts + backward-compatible state load.
- No operational-path change: `ingest-observations` / the 2 operational stations are untouched.

## Exit gates

```bash
uv run ruff format --check src/ tests/ && uv run ruff check src/ tests/
uv run pyright src/
uv run pytest
```

## Verification

- Unit tests above.
- Post-deploy (mac-mini, Swiss staging, when convenient): one run archives many distinct gauges (both
  kinds); a second run dedups; the `BAFU_OBSERVATION_FRESHNESS` heartbeat lands and the extended watchdog
  probes it; the operational feeds (forecast collector + `ingest-observations`) are unaffected.

## References

- Plan 111 / 111b (the BAFU **forecast** collector — the pattern this mirrors; the benchmark this feeds).
- Plan 058 (superseded — the operational-DB approach this replaces).
- `adapters/hydro_scraper.py` (per-station `_build_sparql_query` `:173-192`, `subject_uri` `:177`,
  single-subject `_parse_bindings` `:194-234`, dimensions `:36-50`, graph `:24`).
- `types/bafu_forecast.py` (`BafuForecastRow.station_key: str` `:46,64` — the plain-string-key pattern
  DC-2 mirrors).
- `types/observation.py` (`RawObservation.station_id: StationId` mandatory `:21-29` — why DC-2 avoids it).
- `flows/collect_bafu_forecasts.py` (quarantine `:485-494` / atomic write `:231-239` / dedup marker
  `:272-283` / heartbeat `:158-208`).
- `config/deployment.py` (`bafu_forecast_archive_path` field `:162`, blank→None loader `:403-418`).
- `cli/register_deployments.py` (`cron_bafu_forecast` `:46`, forecast spec `:108-114`).
- `docker-compose.yml` (init env `:269-275`, worker volume mount `:122`, volume def `:311`).
- `ops/watchdog.py` (forecast-only BAFU block `:427-484`, thresholds `:68-71`, state field `:81-84`).
- `types/enums.py` (`PipelineCheckType`, `BAFU_FORECAST_FRESHNESS` `:151-163`).
- `docs/plans/archive/029-lindas-adapter-fix.md` (river/lake URI split, counts `:18-23`).
- `tests/integration/live/test_lindas_live_schema.py` (2004 lake-vs-river disagreement `:21-25,68-75`).
- memory `project_bafu_lindas_realtime_only` (no history → forward-only), `project_plan111_bafu_collector`.
