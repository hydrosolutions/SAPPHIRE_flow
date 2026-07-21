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

**DRAFT — design settled, awaiting owner READY.** Owner decision locked (2026-07-21, below). Went through
**three** `/plan` adversarial rounds (2026-07-21), grounded in a **live LINDAS probe** (§ Live LINDAS probe
evidence) that settled the two empirical unknowns (cadence = hourly; endpoint load negligible) and caught a
dead-gauge watchdog trap.

**Why the loop stopped here (not a fourth round).** Each automated round **re-inflated** the dedup mechanism
(round 1: canonical-content hash + `ORDER BY` pin + cross-partition sidecar pointer; round 3: a two-branch
Prefect-`scheduled_start_time`/clock scheme) and the *next* round's reviewer correctly flagged its own
predecessor's gold-plating but the loop **stalled** rather than self-simplifying. Per the repo's own
"independent review beats the automated loop" lesson, the core design is applied by **human judgment** to the
simplest correct form; the residual findings each round were mechanical (graph ordering, doc anchors, wording)
or were the loop's own additions. Final locked design:
- **T3 dedup — path-existence on `cycle_at` = the injected clock truncated to the top of the hour** (mirror
  `_already_archived`). A same-hour retry resolves to the same `cycle_at` and dedups *in production*; a new
  hour writes a new snapshot; restatements are preserved by construction (a correction lands in a later
  hour's snapshot). **Rejected** the round-3 Prefect-`scheduled_start_time` branch: it adds a `prefect.runtime`
  dependency in business logic and, for a real-time-only source, either defeats dedup (ad-hoc rerun gets
  `now`) or mislabels provenance (stamps current data with a stale slot). No content hash, no `ORDER BY`, no
  sidecar.
- **T4 — raw JSON kept permanently as plain `.json`** (byte-for-byte like `_write_raw_payload`,
  `collect_bafu_forecasts.py:285-294`); no gzip, no retention knob.
- **T1 — no "Literal raises at runtime" test** (dataclasses don't enforce `Literal`; the parser boundary is
  the contract).
- **T8 — explicit catch-record-then-reraise** so a total HTTP/parse failure writes its `CRITICAL` heartbeat
  instead of vanishing. Status values are the real `OK`/`WARNING`/`CRITICAL`.
- **T9 — freshness alerts use `_format_bafu_stale_alert`/`_degraded_alert`** (not the HTTP-probe
  `_format_health_alert`); additive copy, table-driven generalization deferred.
- **Dependency graph rebuilt** into the repo's `phases: [...]` shape with **T5 in the foundation phase before
  T3** (T3 reads the config path T5 adds) — the round-3 blocker.
- Minors: T5/T6 doc-sync corrected to not rely on nonexistent anchors (spec + cicd table lack even the
  shipped forecast entries — back-fill both); parser rejects unmapped predicates loudly (consumes
  `measurementTime`); "2 stations" is an observed runtime fact; T2 keeps a safety `LIMIT`.

The remaining `/plan` "majors" were the loop re-inflating its own dedup; the design is now proportionate and
internally consistent. **Owner owns the READY flip.**

## Context — collect now, because LINDAS has no history

LINDAS (`lindas.admin.ch/foen/hydro`) serves BAFU river-gauge **observations** in **real time only** —
it carries **no historical time series** (Plan 111 Phase 0, confirmed 2026-07-08; memory
`project_bafu_lindas_realtime_only`). The **live mac-mini deployment reported `stations_polled=2`
on 2026-07-21** (Porte_du_Scex, Rheinfelden-Messstation) via `src/sapphire_flow/flows/ingest_observations.py:401` →
`hydro_scraper` — an observed runtime fact, not a repo-inferred count (`config.toml`'s onboarding list is a
different, larger population; § Live-inventory note). The full BAFU network publishes on the same graph but
we don't collect it.

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
  The live `measurementTime` grid shows the **vast majority of the 233 gauges on the top of the hour**
  (max `15:00:00`), a handful off-grid (`:10/:20/:30/:35/:40`). BAFU/LINDAS effectively **refreshes hourly**. The collector
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
(`src/sapphire_flow/types/bafu_forecast.py:46`), no DB identity.

**Locked:** this collector defines a new gauge-code-keyed row type — working name **`BafuObservationRow`**
with `gauge_code: str`, `lindas_kind: Literal["river", "lake"]`,
`parameter: BafuObservationParameter`, `value: float`, `measurement_time: UtcDatetime` — mirroring
`BafuForecastRow`. `RawObservation` / `StationId` are **never** constructed on this path. This closes the
"or a collector-local query" ambiguity that previously left `Scope §1` open to drifting back toward the
incompatible type.

**Parameter is a `Literal`, not a raw `str` (MAJOR resolution).** The LINDAS parameter set is fixed and
small, and the adapter already maps the three camelCase LINDAS predicates to three snake_case domain names
via `_PARAM_MAP` (`hydro_scraper.py:46-50`: `waterLevel→water_level`, `waterTemperature→water_temperature`,
`discharge→discharge`). Per CLAUDE.md "Literal over raw strings — always, when the set of valid values is
fixed and known", the row type declares
`BafuObservationParameter = Literal["discharge", "water_level", "water_temperature"]` (T1), and the parser
(T2) emits only those three values. `measurementTime` is a **recognized non-parameter predicate** (it
supplies the row's timestamp, not a parameter row) and is consumed, not rejected. Any *other* predicate the
parser cannot classify to a `BafuObservationParameter` is a **schema-drift signal** the T2 parser must
**reject/raise loudly** — never silently drop, never stringify through as an unknown parameter.

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
  quarantined, never DB / never `ModelId`). Fields per DC-2, including the module-level
  `BafuObservationParameter = Literal["discharge", "water_level", "water_temperature"]` alias used for the
  `parameter` field. No `StationId` import.
- **Files:** `src/sapphire_flow/types/bafu_observation.py` (new);
  `tests/unit/types/test_bafu_observation.py` (new — construction, kw-only, frozen, `lindas_kind` +
  `parameter` round-trips). The `Literal` aliases are for **static** analysis only (pyright, `src/`) —
  dataclasses do not enforce `Literal` at runtime and `BafuForecastRow` adds no runtime check either, so
  **no** pytest asserts an "invalid Literal raises": the real contract is that the **T2 parser only ever
  emits the three known parameters** (parse-don't-validate boundary), which the parser tests cover.
- **Verification:** `uv run pytest tests/unit/types/test_bafu_observation.py`.

#### T2 — fetch-all SPARQL query + subject-grouping parser (DC-1, DC-3)

- **Scope:** a **collector-local** query builder + parser (NOT edits to `HydroScraperAdapter`'s
  per-station path, which the operational flow depends on). Query `SELECT ?subject ?predicate ?object`
  over `<https://lindas.admin.ch/foen/hydro>` filtered to the dimension predicates the adapter already
  maps (`discharge`, `waterLevel`, `waterTemperature`, `measurementTime` — `hydro_scraper.py:36-50`),
  with **no** per-station `BIND` and **no** onboarded-station list. Query carries a generous safety
  **`LIMIT`** (e.g. 10000 — well above the ~730 rows the live probe returned) as a bounded-request courtesy
  guard; no `ORDER BY` is needed (the simplified path-existence dedup, § T3, does not hash row content).
  **Truncation guard (MINOR resolution):** the parser MUST detect `len(bindings) >= LIMIT` and surface it —
  hitting the cap means the whole-graph fetch is silently missing part of the network (gauge-network growth,
  or more triples per subject than expected). It is raised as a schema-drift/coverage signal so T8 can flip
  the heartbeat off `OK` (see T8 §3); a truncated fetch must never masquerade as a complete run.
  Parser groups bindings by `?subject`, derives
  `(gauge_code, lindas_kind)` from the URI segment (DC-3), maps each dimension predicate to a
  `BafuObservationParameter` via the adapter's `_PARAM_MAP` (`hydro_scraper.py:46-50`) — consuming
  `measurementTime` as the row timestamp and **rejecting/raising** on any *other* unmapped predicate
  (schema-drift signal, DC-2), never silently dropping or stringifying it through — and
  yields `list[BafuObservationRow]` — one row per (subject, parameter). `/plan` confirms the exact query
  against the live graph (which gauges + parameters are exposed) but MUST keep the SELECT-?subject +
  subject-grouping shape locked here.
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
- **Restatement-safe dedup (BLOCKER resolution) — path-existence on a *production-stable cycle identity*.**
  BAFU can **restate** a value at an unchanged `measurement_time` (a correction). Dedup on
  `(gauge_code, lindas_kind, parameter, measurement_time)` — a per-observation key — **would silently drop
  the correction** (data loss). The forecast collector avoids this class not with a content hash but with
  **trivial path-existence dedup**: `_already_archived` just checks whether a deterministically-named file
  already exists (`collect_bafu_forecasts.py:272-283,395,415`), because each unit of work has a **natural
  stable identity supplied by the source** — the forecast's own `issued_at`, a *content-derived* value that
  is byte-identical across any number of separate fetches of the same still-current issuance
  (`register_deployments.py:44-45`: "re-fetches are cheap no-ops"; `BafuForecastRow.issued_at`,
  `types/bafu_forecast.py:49`).
  - **The correction the reviewer caught (why an earlier draft was wrong).** An earlier revision named the
    snapshot by `fetched_at` = the collector's own wall-clock read inside the flow body. That value is
    **not** stable across genuinely separate invocations of the same slot (a manual re-trigger after a
    crash, a misfired duplicate cron tick) — truncated to seconds in the filename they would essentially
    never collide, so path-existence on a `fetched_at` name would dedup only in tests that re-inject the
    identical literal timestamp; in production it would silently write a second near-duplicate snapshot for
    the same real-world hour. The fix is to name the snapshot by a **stable per-slot identity**, not a
    wall-clock read.
  - **Locked fix — snapshot identity = `cycle_at` = the injected clock truncated to the top of the hour.**
    The archive is **append-only per-cycle snapshots** (T4), one file per hourly slot, named by
    `cycle_at = clock().replace(minute=0, second=0, microsecond=0)`. The clock stays dependency-injected
    (per CLAUDE.md — never bare `datetime.now()` in the flow body). Because the cadence is a **known hourly
    constant** (T6 cron `5 * * * *`), truncating to the hour makes every invocation belonging to the same
    hourly slot resolve to the **same** `cycle_at`. **A deliberately simpler identity than the round-1
    draft's** two-branch scheme (Prefect `flow_run.scheduled_start_time` preferred, clock fallback): the
    round-2 reviewer showed that scheme both introduces a new `prefect.runtime` dependency in business logic
    *and* misbehaves for a real-time-only source — an ad-hoc "re-run the missed hour" gets `scheduled_start_time = now`
    (defeating dedup) or, if it did inherit the old slot, would stamp *currently-fetched* data with a
    *stale* slot label (wrong provenance for the `max(cycle_at)` reconstruction). Truncating the **actual
    fetch-time clock** to the hour avoids both: a late recovery fetch lands, correctly, in the hour it was
    actually collected. No `prefect.runtime` import, no branch.
  - **Dedup = path-existence on that `cycle_at`-named snapshot** (mirror `_already_archived`): a genuine
    retry / duplicate tick *within the same hour* resolves to the same `cycle_at`, finds the file present,
    and **skips** — in production, not merely under a re-injected test literal; a new hour gets a new name
    and writes. `concurrency_limit=1` (T6 deployment spec) already precludes two overlapping runs, so the
    realistic duplicate is an in-slot retry, which this now catches.
  - **Restatements are preserved by construction, with no extra machinery:** a corrected value simply
    appears in a *later* cycle's snapshot (a distinct `cycle_at`), which is never deduped against an
    earlier one. There is **no content hash, no `ORDER BY` pin, and no cross-partition sidecar pointer** —
    the round-2 reviewer correctly flagged those as machinery guarding a saving of at most ~one file per
    day-boundary, in a case whose own storage math says snapshot-content is almost never identical between
    hourly cycles anyway. The "current value per identity" is reconstructed at **import** time as the row
    with the greatest `cycle_at` — the quarantine never decides correctness at collection time.
  - **Residual trade-off (noted, not hidden):** a within-hour restatement that arrives *after* this slot's
    snapshot is already written is skipped until the next hourly slot picks it up. That is an accepted
    consequence of hourly per-slot snapshots (BAFU refreshes hourly anyway — § probe evidence); it is not a
    silent duplicate and no cross-hour correction is lost.
- **Files:** `src/sapphire_flow/flows/collect_bafu_observations.py` (new);
  `tests/unit/flows/test_collect_bafu_observations.py` (new).
- **Verification:** quarantine no-op test (blank `archive_base_path` ⇒ zero writes, no DB touch); dedup
  test — **the production-default path, not just a re-injected literal**: two runs whose injected clocks fall
  in the **same hour but at different minutes/seconds** (e.g. `10:05:03` then a retry at `10:07:41`) resolve
  to the **same** `cycle_at` (truncate-to-hour) and the second writes **zero** new files; a run in the *next*
  hour writes a new snapshot; **restatement test** (a later-hour cycle — distinct `cycle_at` — where one
  gauge's `value` changed at the same `measurement_time` archives a **new** snapshot and does **not**
  overwrite/drop the earlier one; both values survive, ordered by `cycle_at`); multi-gauge archive test
  (one run archives many distinct gauges). The clock is injected and `cycle_at` is truncated to the hour, so
  tests are deterministic. `uv run pytest tests/unit/flows/test_collect_bafu_observations.py`.

#### T4 — quarantined parquet archive store

- **Scope:** a parquet archive mirroring `bafu_forecast_archive` (`/data/bafu_forecasts`) — a new
  `bafu_observation_archive` volume at `/data/bafu_observations`, forward-only permanent retention, not the
  `observations` table.
  - **Layout — LOCKED to per-cycle snapshots (MAJOR resolution).** One poll ⇒ one immutable parquet
    snapshot of the whole-graph result (all gauges/params in that cycle), named/partitioned by
    `cycle_at` (e.g. `.../{YYYY}/{MM}/{DD}/obs-{cycle_at:%Y%m%dT%H%M%SZ}.parquet`, where `cycle_at` is the
    scheduled slot time per T3). Per-cycle (not per-gauge) is what makes the restatement design (T3)
    trivially correct — a snapshot is a point-in-time fact that is never rewritten — and it makes the
    completion-marker unambiguous: **the parquet file's atomic temp+rename existence IS the marker** (same
    rationale as `collect_bafu_forecasts.py:272-283`); a half-written cycle never appears. Row schema =
    `BafuObservationRow` fields (DC-2) **plus a `cycle_at: UtcDatetime` provenance column** (the snapshot's
    top-of-hour cycle time, T3), so import can reconstruct latest-value-per-identity by `max(cycle_at)`.
  - **Storage estimate — two artifacts per cycle, both permanent, both uncompressed.** Roughly: a **parsed
    parquet snapshot** (~730 rows of `BafuObservationRow` + `cycle_at`, compressed columnar — order
    ~50–100 KB/cycle, to be measured on the first live run) and a **plain raw SPARQL-results JSON companion**
    (the ~348 KB uncompressed response). At hourly cadence (~8,760 cycles/yr) the raw JSON alone is ~3 GB/yr
    and the total (raw + parquet) is on the order of **~3–4 GB/yr** permanent — a volume this plan already
    deems **acceptable for staging**. This matches the forecast collector's posture (raw kept forever, as
    plain JSON); if storage ever becomes a concern, a retention/prune (and/or compression) step is a trivial
    later follow-up, not something to design speculatively now.
  - **Raw-payload archival (MAJOR resolution).** The forecast collector archives the **raw** upstream
    payload alongside the parsed rows as forward-only safety — `_write_raw_payload` writes **plain,
    uncompressed** JSON via `tmp.write_text(json.dumps(payload))` (`collect_bafu_forecasts.py:285-294`), and
    its tests assert a `.json` file read back with `read_text()`/`json.loads`
    (`tests/unit/flows/test_collect_bafu_forecasts.py:201-207`). **Mirror it exactly:** persist the raw
    SPARQL-results JSON of each archived cycle as a plain `.json` file (same atomic temp+rename, alongside
    the parsed parquet, under the same `cycle_at` key), retained permanently like the parquet, so a future
    parser change can re-derive rows without re-collection. **No gzip and no retention knob** are introduced
    — the earlier gzip ask contradicted "mirror it exactly" (grep confirms `gzip` appears nowhere in
    `src/sapphire_flow/`) and bought a saving the storage math above shows is not needed to clear the bar
    this plan sets. Raw is skipped only when the whole cycle is a path-existence dedup (an in-slot retry).
- **Files:** helper functions inside `flows/collect_bafu_observations.py` (T3); covered by T3 tests.
- **Verification:** included in T3 (snapshot layout + `cycle_at` column + plain `.json` raw companion +
  atomic marker asserted there).

### Phase C — config, deploy wiring, watchdog (each depends on the phase it touches)

#### T5 — `DeploymentConfig.bafu_observation_archive_path` loader (MAJOR resolution)

- **Scope:** add `bafu_observation_archive_path: Path | None = None` to `DeploymentConfig`
  (mirroring `bafu_forecast_archive_path`, `config/deployment.py:158-162`); in `load_config` parse
  `[adapters.bafu_observation].archive_base_path`, **normalize blank/whitespace → None** (the identical
  guard at `config/deployment.py:403-418`), and set it before `DeploymentConfig.model_validate`
  (`config/deployment.py:428`). Add the config-reference doc entry.
- **Files:** `src/sapphire_flow/config/deployment.py`; `tests/unit/config/test_deployment.py` (two new
  tests mirroring `:267-283` — defaults-to-None, parsed-from-adapters-section, plus a blank-string→None
  test); config-reference docs across three surfaces: (a) `docs/spec/config-reference.toml` — add the
  `[adapters.bafu_observation]` section (the actual adapter-config reference file); (b) `docs/standards/cicd.md`
  config table; (c) the `## DeploymentConfig` section of `docs/spec/types-and-protocols.md`. **Doc caveat
  (minor resolution):** the spec's `DeploymentConfig` listing currently does **not** carry
  `bafu_forecast_archive_path` either — the code (`config/deployment.py:158-162`) has drifted ahead of the
  spec. T5 therefore adds the new `bafu_observation_archive_path` field **and** back-fills the already-shipped
  `bafu_forecast_archive_path`, and corrects any stale "adapter archive paths are not part of
  `DeploymentConfig`" note so the spec matches the dataclass, per CLAUDE.md "every code change updates
  affected docs". Do not assume the anchors already exist.
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
     `init` service `environment:` block (`docker-compose.yml:269-275`, which today lists only the four
     schedules `INGEST_OBSERVATIONS`/`FORECAST_CYCLE`/`BACKUP_DATABASE`/`INGEST_WEATHER_HISTORY`) — without
     this the new env var never reaches `register_deployments` (which runs inside `init`,
     `docker-compose.yml:266`) and the schedule silently falls to the code default.
     **Note (accuracy):** `SCHEDULE_COLLECT_BAFU_FORECASTS` is itself **absent** from that block today
     (verified — it appears nowhere in `docker-compose.yml`), so the forecast collector currently relies on
     its code default too. This env line is therefore **net-new wiring**, not a mirror of existing compose
     config; adding the forecast equivalent alongside it is a reasonable in-scope fix but not required by
     this plan.
  3. `bafu_observation_archive` named volume (`docker-compose.yml:311`, mirroring
     `bafu_forecast_archive`) mounted `:/data/bafu_observations:rw` on the worker service that runs the
     collector (mirroring `docker-compose.yml:122`).
  4. `config/overlays/mac-mini.toml`: `[adapters.bafu_observation]\narchive_base_path =
     "/data/bafu_observations"` as the enable switch (unset ⇒ no-op), mirroring `mac-mini.toml:7-8`.
- **Files:** `src/sapphire_flow/cli/register_deployments.py`; `docker-compose.yml`;
  `config/overlays/mac-mini.toml`; `tests/unit/cli/test_register_deployments.py`; **doc-sync** — add the
  `bafu_observation_archive` volume + the `collect-bafu-observations` deployment to the authoritative
  named-volume table in `docs/standards/cicd.md` (`### Named volumes`, which today ends at `caddy_config` and
  lists **neither** BAFU archive). **Minor resolution:** the shipped `bafu_forecast_archive` volume /
  `collect-bafu-forecasts` deployment are **also missing** from that table — do not rely on a nonexistent
  anchor; add **both** the already-shipped forecast rows and the new observation rows, per CLAUDE.md "every
  code change updates affected docs".
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
  3. **Status semantics — exact `PipelineHealthStatus` values (MAJOR resolution).** `PipelineHealthStatus`
     has **exactly** `OK` / `WARNING` / `CRITICAL` (`src/sapphire_flow/types/enums.py:145-148`; DB check
     constraint `status IN ('ok','warning','critical')`, `src/sapphire_flow/db/metadata.py:1355-1360`) —
     **there is no `failed` value**; the earlier "failed run" wording was wrong. Locked mapping for the
     `BafuObservationRecord.status`:
     - **`OK`** — a normal run: ~233 gauges / ~730 rows and a newest `measurement_time` within the last
       ~hour. `detail` carries `row_count`, `gauge_count`, `newest_measurement_time`.
     - **`CRITICAL`** — an **empty whole-graph response** (zero rows), an **HTTP error**, a **parse /
       schema-drift error** (a predicate outside the fixed `BafuObservationParameter` set, DC-2), or a
       **truncated fetch** (`len(bindings) >= LIMIT`, T2 — the archive would be silently missing part of the
       network every cycle from that point on). These are outages, not quiet no-ops; `detail` carries
       `error_type`, `row_count`, `newest_measurement_time`. The watchdog (T9) escalates on this.
     - **`WARNING`** — reserved for a future fresh-fraction-below-threshold degradation signal; **not
       implemented in this pass** — the first cut emits only `OK`/`CRITICAL`. (Noted so a later addition
       needs no enum/schema change.)

     The existing weekly live schema-drift test (`test_lindas_live_schema.py`) guards the *structure*
     independently; T8 guards the *runtime emptiness/error* at collection time.
  4. **Control flow for the CRITICAL paths (MAJOR resolution).** The forecast collector appends its
     heartbeat **only after** a successful collection (`collect_bafu_forecasts.py:547`), so a **total**
     HTTP or parse failure there writes **no** heartbeat at all — which would leave this collector's
     outage invisible to Flow 4. This plan therefore **explicitly wraps** the fetch+parse in `try/except`:
     on an HTTP error, an empty (zero-row) response, or a parse/schema-drift error, the flow writes the
     **CRITICAL** heartbeat **first**, then re-raises so the Prefect run is marked failed. The heartbeat
     write itself stays best-effort/never-fatal (a health-store outage never masks the original error).
- **Files:** `src/sapphire_flow/types/enums.py` (new enum member) **and its authoritative docs** —
  **both** `docs/spec/types-and-protocols.md` `PipelineCheckType` listing **and** the
  `pipeline_health.check_type / PipelineCheckType` enum-value row in `docs/conventions.md:412` (which
  enumerates every `PipelineCheckType` value and must gain `bafu_observation_freshness`) — the enum must
  not drift from either doc, per CLAUDE.md "every code change updates affected docs";
  `src/sapphire_flow/flows/collect_bafu_observations.py` (heartbeat call, T3); T3 tests extended.
- **Verification:** a successful run writes a `BAFU_OBSERVATION_FRESHNESS` record with status **`OK`** whose
  freshness reflects the **newest** gauge (a fixture with one stale + many fresh gauges is **`OK`**); an
  **empty** whole-graph response writes a status **`CRITICAL`** record with `row_count=0` /
  `newest_measurement_time=None` **and re-raises**; an **HTTP-error** fetch writes a **`CRITICAL`** record
  (`error_type` set) **before re-raising** (proving the catch-record-reraise path, not a skipped
  heartbeat); a **truncated fetch** (a fixture with `len(bindings) >= LIMIT`) writes a **`CRITICAL`** record;
  a health-store outage does not fail the run.
  `uv run pytest tests/unit/flows/test_collect_bafu_observations.py`.

#### T9 — add observation freshness to the host watchdog (smallest safe diff — MAJOR resolution)

The host watchdog is **hardcoded to `bafu_forecast_freshness`** — the probe URL constant
(`ops/watchdog.py:54-56`), URL derivation (`:59-65`), stale threshold `BAFU_STALE_THRESHOLD` (`:68-71`),
state field `consecutive_bafu_failures` (`:81-84`), and the ~57-line BAFU block in `run_once` (`:427-484`)
all name only the forecast collector, and a new heartbeat does **not** automatically produce alerts.

**Scope decision (reviewer MAJOR).** An earlier draft folded a **table-driven generalization** of the
already-shipped forecast block into this plan. That is a bigger, riskier diff against **live host-monitoring
code** than this quarantined archive-only collector needs, and it is a pure internal-quality (DRY) concern,
not something the collector's correctness or safety depends on. **De-scoped:** T9 here adds the observation
check as a **second, independently-parameterized copy** of the existing pattern — the shipped forecast
block's *structure is left untouched* (zero risk to the green forecast check). The table-driven
generalization is filed as a **separate follow-up cleanup** (§ Follow-up), justified on DRY grounds and
reviewed on its own — not gated behind landing this collector.

- **Scope (this plan):**
  1. **Second freshness check, additive only:** add an observation-freshness block in `run_once` alongside
     (not replacing) the forecast block, **reusing the existing generic `should_alert_health` hysteresis
     policy** (`ops/watchdog.py:264-276`, as the forecast block already does). **Formatter (citation fix):**
     the freshness alerts use the **BAFU-freshness** formatters `_format_bafu_stale_alert` (`:309`) /
     `_format_bafu_degraded_alert` (`:322`) — **not** `_format_health_alert` (`:279`), which formats an
     HTTP-liveness `HealthProbeResult` (`http_status`) and is the wrong shape for a freshness alert. Add
     observation-specific variants (or parameterize the two BAFU formatters by check-name/threshold). New
     state field `consecutive_bafu_obs_failures` on `WatchdogState` (backward-compatible load defaulting the
     absent key to 0, exactly as `:99` does for the forecast field). New `BAFU_OBS_STALE_THRESHOLD` sized to
     the **hourly** feed — stale after **~3 h** (three missed hourly cycles), *not* the ~1 h a 10-min feed
     would use (the probe corrected the cadence). Add the `--bafu-obs-health-detail-url` CLI arg +
     `WatchdogConfig` field (mirror `:344-346,519-526`).
  2. **No refactor of the forecast block.** The existing forecast tests
     (thresholds/hysteresis/messages) stay green **unchanged** — not as a refactor lock, but because the
     forecast code is not touched at all.
- **Files:** `src/sapphire_flow/ops/watchdog.py` (additive block + new state field + new CLI arg);
  `tests/unit/ops/test_watchdog.py` (forecast tests unchanged; new coverage for the observation check:
  first-failure alert, every-6th, recovery, stale-vs-degraded, backward-compatible state load).
- **Verification:** `uv run pytest tests/unit/ops/test_watchdog.py` (old + new both green).

> **Follow-up (out of scope — separate plan/task):** generalize the now-two near-identical BAFU freshness
> blocks into one table-driven loop over `(check_name, health_detail_url, stale_threshold, state_field)`
> specs. Justified purely on DRY grounds, reviewed on its own diff, and **not** a dependency of this
> collector. (Alternative considered and rejected for now: deferring watchdog wiring entirely until a real
> archive consumer exists — rejected because a silently-dark collector is exactly the failure mode Plan 100
> warns about; a cheap additive freshness alert is worth having from day one even though nothing downstream
> yet consumes the archive.)

### Dependency graph

Execution order is driven by this graph, **not** the `Phase A–D` narrative headers above (which group the
task *descriptions* for readability). In particular **T5 executes in the foundation phase**, before T3 —
because T3's quarantine gate reads `config.bafu_observation_archive_path`, which T5 adds (BLOCKER-4
resolution: the earlier graph scheduled the collector before the config field it depends on).

```json
{
  "phases": [
    {
      "id": "foundation",
      "tasks": ["T1", "T5", "T7"],
      "parallel": true,
      "depends_on": [],
      "note": "Mutually independent: T1 (row type), T5 (DeploymentConfig field — no upstream dep, MUST precede T3), T7 (058 supersede docs)."
    },
    {
      "id": "parser",
      "tasks": ["T2"],
      "parallel": false,
      "depends_on": ["foundation"],
      "note": "T2 imports the T1 row type and maps predicates to BafuObservationParameter."
    },
    {
      "id": "collector",
      "tasks": ["T3", "T4"],
      "parallel": false,
      "depends_on": ["parser"],
      "note": "T3 needs BOTH the T2 parser AND the T5 config field (both land in earlier phases). T4 helpers live inside the T3 module — single build unit."
    },
    {
      "id": "deploy",
      "tasks": ["T6"],
      "parallel": false,
      "depends_on": ["collector"],
      "note": "T6 registers the T3 flow and wires the T5 overlay/volume. Cron default is the known hourly constant (5 * * * *) — no back-dependency on any later task."
    },
    {
      "id": "heartbeat",
      "tasks": ["T8"],
      "parallel": false,
      "depends_on": ["collector"],
      "note": "T8 adds the best-effort heartbeat call into the T3 flow."
    },
    {
      "id": "watchdog",
      "tasks": ["T9"],
      "parallel": false,
      "depends_on": ["heartbeat"],
      "note": "T9 probes the heartbeat T8 emits (additive copy of the forecast block)."
    }
  ]
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
  `.github/workflows/live-lindas-weekly.yml`) — is kept as-is. **Scope caveat (MAJOR resolution):** that
  shipped test imports `HydroScraperAdapter` and exercises the **per-station** `fetch_observations` path
  (`test_lindas_live_schema.py:37,166`), so it guards the shared **endpoint + dimension-predicate** drift
  that this collector *also* depends on — but it does **not** exercise the new whole-graph
  `SELECT ?subject` adapter/parser. A live whole-graph smoke test for `BafuObservationAdapter` is a small,
  **optional** add (noted here, not required for this plan); the per-cycle CRITICAL heartbeat (T8) is the
  primary runtime guard against whole-graph drift. Plan 058's deferred reference-fixture *promotion* idea
  (058 T3) remains deferred and out of scope here.

## Tests (summary — detailed per task above)

- Parser (T2): a faked whole-graph response with ≥3 distinct subjects of both kinds — **including the same
  numeric code under both `/river/` and `/lake/`** — yields **grouped** per-subject rows (not one merged
  row), correct params per kind, `gauge_code`/`lindas_kind` from the URI, and the collision kept distinct.
- Dedup (T3, BLOCKER): the **production-default** path — two runs whose injected clocks land in the **same
  hour at different minutes/seconds** resolve to the same `cycle_at` (truncate-to-hour) and the second finds
  the snapshot present (path-existence, mirror `_already_archived`) and writes zero new files; a next-hour
  run writes a new snapshot.
- **Restatement (T3, BLOCKER):** a later-hour cycle (distinct `cycle_at`) where one gauge's `value` changed
  at the same `measurement_time` archives a **new** snapshot and preserves the original — both survive,
  ordered by `cycle_at`.
- Quarantine (T3): blank `archive_base_path` ⇒ no-op (no writes, no DB touch); a set path writes only
  under it; the only DB write is the heartbeat (no observation values / no `station_id`).
- Raw archival (T4): each archived cycle persists the raw SPARQL-results JSON as a **plain `.json`** file
  (read back via `read_text()`/`json.loads`, mirroring `test_collect_bafu_forecasts.py:201-207`) alongside
  the parsed parquet (both retained permanently; no gzip, no retention knob).
- Config (T5): `bafu_observation_archive_path` defaults to None, parses from
  `[adapters.bafu_observation]`, blank→None.
- Deploy (T6): `_build_specs()` includes `collect-bafu-observations` (count 11→12) with cron default
  `5 * * * *`; env override works; `docker compose config` parses.
- Heartbeat (T8): a successful run writes a status-**`OK`** `BAFU_OBSERVATION_FRESHNESS` record whose
  freshness reflects the **newest** gauge (one-stale-many-fresh fixture is `OK`); an **empty** whole-graph
  response and an **HTTP-error** fetch each write a status-**`CRITICAL`** record and **re-raise** (proving
  catch-record-reraise, not a skipped heartbeat); a health-store outage does not fail the run.
- Watchdog (T9): the **existing forecast tests stay green unchanged** (the forecast block is not touched —
  the observation check is purely additive); new observation-check tests cover stale (~3 h)/degraded/recovery
  alerts + backward-compatible state load.
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

> **Note (removed References appendix):** an earlier revision closed with a standing `## References`
> file:line bibliography that merely re-listed the citations already carried inline under DC-1/DC-2/DC-3 and
> each task's Scope/Files bullets. It added no decision content and its line numbers would rot the moment the
> cited files are touched (the reviewer already caught two stale entries there). Per the MINOR finding it is
> dropped; the in-body citations at each design commitment are the grounding a reviewer needs.
