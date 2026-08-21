---
status: DRAFT
created: 2026-08-21
plan: 198
title: Forecast Lab snapshot — a versioned, read-only JSON export for SAPPHIRE-flow-map
scope: One versioned snapshot contract (forecast-lab-snapshot/v1), one assembly service, one authenticated REST route, one CLI export, a generated JSON Schema, a fixture, tests and docs. No new external access, no new DB tables, no migration, no frontend.
depends_on: []
blocks: []
source: SAPPHIRE-flow-map integration request 2026-08-21; grounded in the Flow Map integration audit of the same date
---

# Plan 198 — Forecast Lab snapshot export

## Status

**DRAFT — blocker cleared, awaiting a final review round.** The `plan` workflow ran 3 rounds on
2026-08-21 and escalated on one blocker, **O7**, which the owner resolved the same day: verification
(T4) is **cut**, and the raw BAFU export **proceeds**, recorded as an Export extension in Plan 111.
Five owner forks remain open (O1, O3, O4, O5, O6) and none of them blocks — each has a stated
recommendation and a working default.

Review provenance, because it changes how much the rounds are worth: **the independent Codex pass
failed in rounds 1 and 2** (`codexFailedRounds: 2`), so those rounds were Claude-lens only and
"converged" 5 → 2 majors without the repo-grounded reviewer that `CLAUDE.md` makes a floor. Round 3
got the first real Codex pass and raised 2 blockers + 12 majors, which tripped the stall detector by
comparing a Codex-less round to a Codex-ful one. **That is a false stall**, and the round-3 findings
were the valuable ones: every one this session verified against the repo or the live host held up,
and four of them corrected *this plan's own* measured facts (F1, F6, F8, D2). Those are folded.
The three T4-internal findings died with T4. **Next step: one more `plan` round with a raised
`codexTimeoutMs`, so the whole loop gets the independent reviewer that only round 3 had.**

## Goal

Give the separate **SAPPHIRE-flow-map** project one self-contained, versioned JSON document
containing everything it needs to render a research comparison of BAFU observations, archived
BAFU forecasts and SAPPHIRE forecasts at the two operational stations — served over the company
LAN, cacheable offline, and produced by exactly one code path whether it comes from the REST
route or the CLI.

**Non-goals** (explicit, and none of them are deferred work this plan owns): public Internet
access, a tunnel, public DNS, TLS, hosted replica, frontend work, alerts, thresholds,
site-specific forecasts, river-network processing, direct DB access for the map, and any
commercial publication of BAFU forecast data.

## ⛔ Proportionality is a binding constraint on this plan AND on its review

> **Owner directive, 2026-08-21: this is an MVP. Do not over-engineer it.**
> The directive is recorded here, in the plan doc, because per-run scope passed as workflow
> args is silently discarded: the workflow has **no generic per-run scope argument** — it reads only
> `planPath`, `repo`, `maxRounds` and `codexTimeoutMs` (`.claude/workflows/plan.js`). Every reviewer
> and every revision round is bound by what follows.

This plan adds **one read path over data that already exists**. It must not become a data
platform. Specifically:

1. No new database table, no migration, no schema change.
2. No ingestion of BAFU forecasts into Postgres — the quarantine stands (Plan 111).
3. No change to any existing route's response shape.
4. No new monitoring, no new scheduled job, no new probe harness, no new script file.
5. No caching layer, no pagination, no rate limiting, no ETag beyond the single content hash
   already specified in D10.
6. No abstraction introduced for a second consumer, a second deployment, a second variable, or a
   second station set. There are **two stations and one consumer**. Generality that no caller
   exercises is over-engineering, not foresight.

### Rules binding every reviewer

- **"No findings" is a complete and welcome review.** Do not manufacture findings to justify
  the pass. A round that returns nothing is a successful round.
- **A finding must name a CONCRETE FAILURE** — an input, a state, and the wrong output or crash
  that results. "Consider also supporting X", "this may not scale", "future-proof by…" and
  "add a section describing…" are **not findings** and must be returned as minors at most, or
  omitted.
- **Adding length is a cost.** Prefer deleting to adding. A revision that grows the plan without
  removing a concrete failure has made the plan worse.
- **Do not reinstate cut scope.** **T4 is CUT, not deferred** — reinstating it would breach Plan
  111's scorer gate (O7.1). T9 and T10 are marked **separable** deliberately, and the
  six owner forks in § Owner decisions required are for the human, not for reviewers to
  pre-resolve by expanding the plan.
- **Do not propose new apparatus.** Per `CLAUDE.md` § Ad-hoc Analyses, a one-off check is a
  heredoc, not a committed script.
- **The measured findings in § Verified facts are settled.** F1–F10 were measured on the live
  deployment on 2026-08-21. Challenge them only with contrary evidence from the repo or the
  host — not with reasoning about what the code "should" do.

---

## Verified facts this plan is built on

Measured on the mac mini and in the repo on 2026-08-21. Anything not listed here must be
re-verified by the implementer rather than assumed.

### F1 — The API container cannot currently read the BAFU forecast archive

`docker-compose.yml:308-310` — the `api` service mounts only `model_artifacts:/data/artifacts:ro`
and `./config.toml`. The archive volumes are mounted on `prefect-worker`
(`docker-compose.yml:131-132`). `prefect-worker-ingest` mounts the **observation** archive only
(`:192`) — the forecast archive is on `prefect-worker` alone. **The REST route cannot
serve BAFU forecasts without a compose change.** See D2.

### F2 — The API does not set `SAPPHIRE_CONFIG` and never loads `DeploymentConfig`

`SAPPHIRE_CONFIG: /app/config.toml` is set on three services (`docker-compose.yml:94,155,214`),
**not** on `api`. Nothing under `src/sapphire_flow/api/` calls `load_config`. The quarantine gate
lives in `config/deployment.py:450` (`bafu_forecast_archive_path`, blank-normalised to `None`).

### F3 — ⚠️ The requested percentile normalization is INVERTED

The request states *"`25.-75. Percentile`, first half → `p75`; second half → `p25`"*. **This is
backwards.** Measured 2026-08-21 on three independent runs across three stations
(`2009` and `2091` @ `20260821T030000Z`, `2011` @ `20260820T210000Z`): in every run the **forward**
half is the lower series and the mirrored (backward) half the upper, with the `Median` trace between
them. The correct mapping is therefore **first half → `p25`, second (backward) half → `p75`**.
Implementing the request as written would silently swap the uncertainty band on every chart.
*The raw per-run numbers are deliberately not reproduced here: they belong with the assertion they
back (T2a's regression-fixture docstring), where they document a live test rather than rotting in a
plan as the archive rolls over.*

**Independently corroborated inside the repo, and the likely origin of the error.** The checked-in
reference payload `tests/fixtures/reference/bafu_q_forecast_2135.json` — a different station, and a
different polygon length (233, so `half = 116`) — shows the same forward-is-lower orientation. Yet
the domain comment at `types/bafu_forecast.py:116` states the **opposite** ("forward upper edge then
backward lower edge"). That comment is wrong, and it is the most plausible source of the inverted
mapping in the integration request. **T2a must correct it** — otherwise the next reader re-derives
the same bug from the same sentence. It also confirms D6: `118` is not a constant and must never be
hardcoded.

Polygon geometry, verified: length **237** = 118 forward + 118 backward + **1 closing vertex**.
`half = (n - 1) // 2`. Indices `half-1` and `half` share the same `valid_time` (the horizon turn).
Index `n-1` duplicates the first vertex and must be dropped. The backward half is in **descending**
`valid_time` and must be reversed.

### F4 — The min/max trace mapping in the request IS correct

`Min. / Max.` (with periods) is the **upper** envelope → `maximum`; `Min / Max` is the **lower**
→ `minimum`. Verified: the two envelopes coincide at lead 0 and separate monotonically toward the
horizon, the periodless name tracking the lower series throughout. They differ **only by punctuation** and must never be deduplicated on a
normalised name. Parser: `adapters/bafu_forecast.py:302` (`fetch_variant_forecast`),
`types/bafu_forecast.py:113-126` (`BafuForecastRow.point_index`, which exists for exactly this).

### F5 — `nwp_cycle_reason` is not persisted

`forecasts` (verified `\d`) has `nwp_cycle_source` (`primary | fallback | runoff_only`) and
`nwp_cycle_reference_time`, but **no `fallback_reason` column**. The reason (`too_recent`) exists
only in worker logs; Plan 196 explicitly **cut** the task that would persist it. The requested
`provenance.nwp_cycle_reason` must therefore be `null`. See § Deviations.

### F6 — Code/image version is not recorded per forecast

`model_artifacts` carries `sha256_hash`, training window and status, but no code version, and the
running image tag is a property of *now*, not of the forecast — substituting it would be a
provenance lie. **However `model_artifact_provenance.source_commit` exists**
(`db/metadata.py:991`, `store/model_artifact_provenance.py:41`) and is the honest source when an
artifact was imported. On the mini that table currently holds **0 rows** (verified 2026-08-21), so
the field is `null` in practice today — but the plan must read `source_commit` and fall back to
`null`, not hardcode `null`. Artifact SHA stays a separate field.

### F7 — River name and display name are not in SAPPHIRE

`stations` has `code`, `name` (`Porte_du_Scex`, underscored), `location` (EPSG:4326) — and no
river. `basins.area_km2` is populated for both MVP stations. River and label live in the BAFU
inventory GeoJSON (`properties.hydro_body_name`, `properties.label`, CRS **EPSG:2056**), which the
collector fetches hourly and **discards** (`flows/collect_bafu_forecasts.py:539` — the inventory
is never archived). See D7.

### F8 — Source shapes

- SAPPHIRE: daily (`time_step_seconds=86400`), 5 steps, `representation='members'`,
  21 members (`nwp_regression`) or 50 (`linear_regression_daily`); `parameter='discharge'`,
  `units='m³/s'`. Leads are **not** round for 06/12/18Z runs — D12 orders points by
  `valid_time`, so no lead schedule is assumed anywhere.
- BAFU: hourly, 118 points ≈ 117 h. `issued_at` from the `Forecast as of` layout annotation;
  `produced_at` is **not** our fetch time — it is BAFU's own `meta.produced_at` off the inventory
  GeoJSON (`adapters/bafu_forecast.py:215`, threaded through
  `flows/collect_bafu_forecasts.py:549`). Our true fetch clock is `run_at`, which is **only logged
  and never persisted** (`collect_bafu_forecasts.py:531-536`). Filenames: `{key}_{variant}_{issued_at:%Y%m%dT%H%M%SZ}.parquet`.
- Observations: 10-minute grid; filter `source='measured' AND qc_status='qc_passed'`;
  partial index `ix_observations_station_timestamp_qc_passed` exists for exactly this.
- `station_thresholds` and `alerts` are **empty (0 rows)** — no threshold metrics are possible.

### F9 — No gzip middleware exists

`api/__init__.py:51-54` adds `CORSMiddleware` only, and only when `SAPPHIRE_CORS_ORIGINS` is
non-empty. There is no `GZipMiddleware`. See D11.

### F10 — CLI convention is module-invocation, not a console script

`pyproject.toml:59-60` declares exactly one script (`check`). Every operational CLI is invoked as
`python -m sapphire_flow.cli.<module>` (`docs/conventions.md:76`,
`docs/plans/071-...:349`). `cli/bafu_observation_audit.py` is the existing archive-reading CLI and
is the pattern to mirror.

---

## Design decisions

**D1 — One assembly function, two surfaces.** `services/forecast_lab/snapshot.py::build_snapshot()`
is the single source of truth. The route and the CLI both call it per invocation and both serialise
the same Pydantic model. No second implementation, no drift, no third producer. *(Follows the
existing `services/skill/` package shape.)* **This invariant holds under both branches of owner
decision O1** — see D2 and D2-alt, neither of which introduces a pre-built document.

**D2 — The API reads the archive through a read-only volume mount.** Add to the `api` service:
`bafu_forecast_archive:/data/bafu_forecasts:ro` and `SAPPHIRE_CONFIG: /app/config.toml`. **The mini
overlay DOES need an edit** — the archive *path* is declared only in
`config/overlays/mac-mini.toml:7`; base `config.toml` has no `[adapters.bafu_forecast]` section at
all (verified). `load_config()` merges overlays solely from `SAPPHIRE_CONFIG_OVERLAY`
(`config/_overlay.py:27`), which the `api` service does not set, so without the overlay the
quarantine gate resolves `bafu_forecast_archive_path = None` and the route reports the archive
missing **even with the volume correctly mounted**. T7 must therefore add, to the mini `api`
service, both `SAPPHIRE_CONFIG_OVERLAY: /app/config/overlays/mac-mini.toml` and the read-only
overlay-file mount, mirroring `prefect-worker` (`docker-compose.macmini.yml:23-29`). This
**mirrors the pattern already in the file**
(`model_artifacts:/data/artifacts:ro`). The quarantine is preserved and arguably strengthened: the
mount is `:ro`, nothing writes BAFU values to the DB, and no `ModelId` is minted.
*Fallback if O1 = no (D2-alt): **nothing is pre-built and nothing is served from a file.** The route
still calls `build_snapshot()` live (D1) and still computes `snapshot_id`/`ETag` over its own
response body (D10, T5) — it simply has no archive to read, so the BAFU section comes back through
the D13 guard as `status.bafu_forecast = "missing"`, reason `"archive not mounted"`. The complete
three-source snapshot is then produced only by the CLI (T6), run inside a container that already
has the mount (`docker-compose.yml:131` mounts `bafu_forecast_archive` on `prefect-worker`), and
handed to the map as a file. **Trade-off, stated rather than hidden:** under D2-alt the map loses
live BAFU forecasts over HTTP and gets them only from a manually exported file. An earlier variant
of this fallback had a worker pre-build the whole document to a shared volume; it is **rejected**,
because it would add a scheduled job (barred by proportionality rule 4), a third producer (breaking
D1) and a staleness class the contract would have to model.*

**D3 — v1 computes no verification at all; the section is a declared sentinel.** *(Resolved by
owner decision O7.1, 2026-08-21 — T4 is CUT.)* `verification` is always
`{"status": "insufficient_data", ...}` with the window, method version and limitations populated,
and no metrics. This is not a stub awaiting code — it is the **honest** answer under Plan 111,
which bars computing a BAFU-derived benchmark before Gate G1. It also makes the contaminated-data
requirement structural rather than defensive: `build_snapshot()` **never queries
`hindcast_forecasts`, `hindcast_values` or `skill_scores` at all**, so the 2,752 known-bad
future-dated hindcast rows cannot reach the export by any path. A single test asserts those three
tables are untouched.

**D4 — `aligned_daily_comparison.sapphire` is a keyed object, not a row per model.** One row per
UTC day, with `sapphire` keyed by `model_key`. Row-per-model would duplicate the `observation` and
`bafu` values on every row and invite the consumer to double-count them. Documented in the schema.

**D5 — Quantile method is linear interpolation between order statistics**, matching
`polars.quantile_cont` / `numpy.quantile(method="linear")`. Surfaced in the payload as
`comparison_semantics.sapphire_quantile_method: "linear"` so a consumer never has to guess.
Summaries: `minimum` = min member, `p25`/`median`/`p75` = interpolated quantiles, `maximum` = max
member. **These are summaries of stored ensemble members and must be labelled as such — BAFU's
five traces are *not* members** (F4) and the schema descriptions must say so on both sides.
**Guard, not a feature:** `forecast_values` enforces a member/quantile XOR and `ForecastInterface`
can emit `EnsembleRepresentation.QUANTILES` (`types/ensemble.py`, `db/metadata.py`). All 232 stored
forecasts on the mini are `members` (verified), so quantile support is not built — but a
`representation != members` forecast must return an explicit unavailable entry with reason
`"unsupported_representation"`, never outer quantiles relabelled as `minimum`/`maximum`.

**D6 — BAFU normalization is derived and self-checked, never hardcoded.** Split at
`half = (n-1)//2`, drop the closing vertex, reverse the backward half, then **assert the two half
series carry identical ascending `valid_time` sequences and that they match the `Median` trace's
valid times**. Any mismatch → the run is emitted with `available: true` plus an explicit
`quality_flags` entry, not silently reshaped. Same for the monotonicity check
(`minimum <= p25 <= median <= p75 <= maximum`): retain source values, add a flag.

**D7 — Station metadata: real or `null`, never approximated.** `code`, `name`, `location`
(EPSG:4326), `basin_area_km2`, `active` come from the DB. `river` and `display_name` come from an
archived BAFU inventory snapshot if one exists, else **`null`**. T9 (separable) adds inventory
archiving to the collector so they stop being null within one hour of deploy.

**D8 — Scoping reuses `ensure_station_in_scope`; an out-of-scope or unknown `station_code` is
`404`; a stationless principal gets an empty `200`. This route introduces no new status code.**
The repo-wide Plan 147 R2 mechanism is `api/security.py:230-233`, whose docstring reads
"404 (not 403) on an out-of-scope station — R2: do not reveal existence of stations outside the
caller's scope", and every station-scoped route uses it (`api_forecasts.py:95`,
`api_stations.py:185,242,269`). This route does the same for **explicitly requested** codes that
are unknown or out of scope: `404 Station not found`.

The other case — a non-admin principal with an empty `station_ids` that supplies **no**
`station_code` — is handled the way the one directly analogous sibling handles it. `list_stations`
(`api/routes/api_stations.py:145-171`) takes no station id, filters to scope
(`:163-164`, `if not principal.is_admin: all_stations = [s for s in all_stations if
principal.station_in_scope(s.id)]`, and `security.py:134-142` makes a stationless consumer match
nothing) and returns `200` with `total=0, items=[]`. So does this route: it builds the snapshot over
the (empty) scoped station set and returns `200` with `stations: []` and
`status.overall == "unavailable"` (D16 rule 2). That reveals nothing about which stations exist,
adds no HTTP branch and no new test, and stays inside D13's rule that non-200 is reserved for "the
snapshot could not be generated at all" — a caller with zero grants is the "no data" case D13/D16
already have a vocabulary for.

*(D8 was revised twice during review; the text above is the settled answer. There is no
`requested_but_unavailable` list — with `404` on any out-of-scope code, nothing would populate it.)*

**D9 — `422` for invalid query parameters,** via FastAPI-native `Query(..., ge=, le=)`. Sibling
routes return `400` from `_parse_enum` (`api/routes/api_stations.py:127`); this route is new, the
request specifies 422, and 422 is what FastAPI produces without custom code. Documented as an
intentional local difference.

**D10 — `snapshot_id` and `ETag` are content-derived and identical.**
`snapshot_id = "fls1-" + sha256(canonical_body_without(snapshot_id, generated_at))[:16]`;
`ETag` is the same digest, emitted as a **weak** validator (`W/"..."`). Weak is the correct and
honest choice: the digest deliberately excludes `generated_at`, which *is* returned in the body, so
two 304-equivalent responses are not byte-identical — a strong validator would be a lie
(RFC 9110 §8.8.1). `Last-Modified` is `data_cutoff_at`, which is **nullable**: with zero stations or
all sources missing there is no cutoff, and the header is then omitted rather than invented.
`If-None-Match` returns `304`. Consequence, and the reason for the design: **a re-sync that yields identical data produces
an identical `snapshot_id`, so the map can skip re-caching.** That is a real benefit for a LAN-only,
cache-first consumer.

**D11 — No global gzip.** F9: none exists. Adding `GZipMiddleware` changes every route's response
encoding, which is out of scope ("do not redesign unrelated APIs"). The snapshot for two stations
with 168 h of observations is ~300–600 KB uncompressed — negligible on a LAN. T10 (separable)
offers it if the owner wants it.

**D12 — Determinism is a tested property.** Stations sorted by `code`; points by `valid_time`
ascending; models by `(is_primary desc, model_key asc)`; daily rows by `day_start`; JSON key order
fixed by Pydantic field order; no set iteration anywhere. `is_primary` = the assigned model with
the lowest `model_assignments.priority` that produced a forecast (lowest wins; `nwp_regression` is
10). Two builds from identical data must be byte-identical apart from `generated_at`.

**D13 — Partial is the default failure mode.** Each of the three sources is assembled inside its
own guard. A source failure sets `status.<source> = error|missing|stale` with a short non-secret
message and leaves that section unavailable. **But containment has a hard limit that must be stated
rather than discovered:** observations and SAPPHIRE forecasts share one request-scoped SQL
transaction (`api/deps.py`). Catching a failed statement does **not** repair an aborted PostgreSQL
transaction — every later query in that request fails too. Therefore: **`SQLAlchemyError` and
connection failures are whole-snapshot failures and must escape to `500`**; only non-poisoning
failures (archive file missing or unparseable, trace-geometry mismatch, domain validation) degrade
to a partial snapshot. No savepoints — that is machinery this MVP does not need (proportionality
rule 5). `500` also covers config missing.

**D14 — Latest-forecast fetching reuses the existing store method; no query-count contract.**
`ForecastStore.fetch_latest_forecast()` already exists (`store/forecast_store.py:123-138`) and
returns the latest forecast per `(station_id, model_id, parameter)`. T2b calls it in a small loop
over the assigned `(station, model)` pairs. At the scope this plan is capped to — **two stations**,
a handful of models — that is a handful of trivial indexed queries on a once-per-request LAN
endpoint, functionally indistinguishable from a windowed batch query, and it adds **no** new store
code. An earlier draft mandated "two queries regardless of station or model count" and asserted it
by counting queries in a test; that is a performance guarantee for a station count this plan says
will never exist (proportionality rule 6), and it would have failed a future, correct
implementation that needed one more query (e.g. joining `model_assignments` for the D12
`is_primary` order). **Trade-off, stated:** query count now grows linearly with `(station, model)`
pairs. If that ever matters it will matter at Nepal/v1 scale, and the batch query
(`store/historical_forcing_store.py:90-113` is the `row_number()` pattern to copy) belongs to
whichever plan introduces those stations.

**D17 — The eligible set is narrowed before scoping, and assignments are filtered to ACTIVE.**
Two lookups in this plan default wider than the MVP claims, and both must be constrained explicitly:
(a) `fetch_all_stations()` filters only on `kind` (`store/station_store.py`), so an unfiltered
request under an **admin** token would sweep every station in every status — the eligible set is
therefore **`network='bafu'` AND `station_kind='river'` AND `station_status='operational'`**,
applied *before* principal scoping, matching what the forecast cycle already does. A bare
`station_code` is resolved against the canonical `bafu` network, because
`fetch_station_by_code(code, network)` requires both and `(network, code)` is the uniqueness
constraint. (b) `fetch_model_assignments()` returns every status, so an **inactive** assignment
could otherwise supply a stale forecast and even win `is_primary` on priority — filter to
`ModelAssignmentStatus.ACTIVE` first. Both get a regression test (AC19, AC20).

**D15 — The JSON Schema is generated from the Pydantic models and committed**, with a test
asserting the committed file equals `model_json_schema()`. The schema cannot drift from the code.

**D16 — `status.overall` semantics — exhaustive.** Each of the three sources carries a per-source
status in `ok | stale | error | missing`. `status.overall` is the **first** matching rule, so every
combination maps to exactly one value:

1. all three sources `ok` → `ok`;
2. else no source is `ok` or `stale` (every source is `error` or `missing`) → `unavailable`;
3. else no source is `ok` and at least one is `stale` → `stale`;
4. else → `partial`.

Rule 2 is the case the earlier draft left undefined (e.g. one `error`, one `missing`, one `stale`,
or a same-day outage across all three sources). Staleness thresholds are constants in the module, documented in the schema, and deliberately generous —
**the snapshot never declares itself unusable because it is old; the map decides how to label age.**

---

## Owner decisions required (blocking READY)

**O1 — Widen the archive mount to the API container?** D2 mounts `bafu_forecast_archive` read-only
into `api`. This is the only way the REST route can serve BAFU data live. It slightly widens the
Plan 111 quarantine surface (from workers to the API), though read-only and still never into the
DB. *Recommendation: yes.* Fallback: D2-alt — the route still builds live but reports
`bafu_forecast: missing`, and only the CLI (run in a container that already has the mount) produces
the complete three-source snapshot.

**O2 — ✅ RESOLVED 2026-08-21: ship `insufficient_data`.** Superseded by O7.1 — T4 is cut, so the
question is moot. See D3.

**O3 — Archive the BAFU inventory (T9)?** Unlocks `river` and `display_name` and closes a gap the
audit already identified. **Correction to my earlier "~15 lines" estimate:** it is more than a
collector change. The adapter's boundary model drops geometry and `hydro_body_name` before the
collector ever sees the inventory (`adapters/bafu_forecast.py:126,280`), and
`BafuStationInventory` carries only parsed stations, source time and a skip count
(`types/bafu_forecast.py:96`) — so T9 also needs an adapter/domain change to surface the raw
payload, plus atomic archival and reader tests. *Recommendation: still yes, but price it honestly;
it is separable, and cutting it leaves those two fields `null`, which the contract already permits.*

**O7 — ✅ RESOLVED 2026-08-21 by the owner. Both halves answered; the blocker is cleared.**

> **O7.1 — verification: CUT.** T4 is removed from v1. Plan 111's scorer gate stands untouched.
> **O7.2 — raw BAFU export: PROCEED.** Owner: *"proceed with that. we use the data."* Recorded
> where the gate lives, as an **Export extension** in `docs/plans/111-...md` § Non-goals
> (commit on `main`, 2026-08-21), not only here.

The finding that forced the decision, retained because it is why the plan is shaped this way. It
was raised by the independent Codex review and **verified against the plan text**. Plan 111 is
`status: READY`, and therefore authoritative:

- *"The scoring half … stays **BLOCKED on external gate G1**: no benchmark can be **computed** or
  published until the BAFU request … returns"* (`111:3-6`), and among its non-goals:
  *"the collector may archive; the ***scorer*** and the paper stay gated"* (`111:563`).
  **T4 computes a benchmark. This is a direct conflict, not a judgement call.**
- The quarantine's stated property is that *"a single `rm -rf` of that directory discards the whole
  archive"* (`111:12-16`). Exported snapshots are copies that a `rm` of the volume no longer
  reaches — a weakening of the discard guarantee the owner accepted, whatever the payload says.
  `licence_status: "unresolved"` (O6) documents the constraint; it does not lift it.

**Consequence the implementer must carry forward.** The archive's discard property — *"a single
`rm -rf` of that directory discards the whole archive"* — no longer holds on its own, because
exported snapshots are copies outside it and the map deliberately caches the last good one. Plan 111
now records that a full discard takes three steps (volume, exported files, map cache). **T8 must
document this in the Forecast Lab spec**, so the operational consequence is not buried in a plan
nobody re-reads.

**O4 — Add `GZipMiddleware` (T10)?** *Recommendation: no for this MVP (D11).*

**O5 — Confirm the F3 correction.** The delivered contract will have `p25`/`p75` the opposite way
round from the request. This is measured, not inferred, and the plan will not proceed on the
request's mapping. Owner acknowledgement wanted because it changes the agreed contract.

**O6 — Licence posture in the payload.** The audit established Plan 111 Gate G1 is unsent and BAFU
publication rights are unresolved. *Recommendation: the snapshot carries a
`bafu_forecast.licence_status: "unresolved"` field and the schema documents that this export is
research-only and not for commercial publication.* This costs nothing and makes the constraint
travel with the data.

---

## Phases and tasks

### Phase 1 — contract

**T1 — Snapshot models, generated JSON Schema, example fixture.**
*Scope (in):* Pydantic v2 boundary models for the whole document in
`api/forecast_lab_schemas.py`; `docs/spec/forecast-lab-snapshot-v1.schema.json` generated from
them; a sanitized two-station example at
`tests/fixtures/forecast_lab/forecast_lab_snapshot_example.json`.
*Scope (out):* any assembly logic, any DB or filesystem access.
*Exit:* `uv run pytest tests/unit/api/test_forecast_lab_schema.py` — the committed schema equals
`model_json_schema()` (D15) and the example validates against it.

### Phase 2 — source readers (parallel)

**T2a — BAFU archive reader.** `services/forecast_lab/bafu_archive.py`.
*Scope (in):* newest `q_forecast` run per station code within a window; the derived,
self-checked trace normalization of D6/F3/F4; `issued_at` vs `fetched_at` kept distinct;
monotonicity + geometry quality flags; a pure function taking a base `Path`.
**Also corrects the wrong orientation comment at `types/bafu_forecast.py:116`** (F3) — a
comment-only edit, and the one place this plan touches an existing file's prose.
*Scope (out):* `p_forecast` (lakes — excluded from a discharge comparison), any write, any DB.
*Exit:* `uv run pytest tests/unit/services/forecast_lab/test_bafu_archive.py` — including a
fixture that **fails under the inverted (first-half → `p75`) mapping and passes only under the
corrected (first-half → `p25`) mapping** (F3). The fixture's docstring carries the three measured
runs behind F3, so the evidence sits with the assertion. A directionally insensitive fixture (one
that passes under both mappings) does not satisfy this exit criterion.

**T2b — Database readers.** `services/forecast_lab/db_sources.py`.
*Scope (in):* station metadata + `basins.area_km2`; observations filtered
`measured`/`qc_passed`/`discharge` over the window; latest forecast per assigned model in the
way of D14 (a loop over the existing `fetch_latest_forecast()`), with artifact provenance.
*Scope (out):* new tables, migrations, changes to existing stores' signatures, any new batch/windowed
query, any query-count assertion (D14).
*Exit:* `uv run pytest tests/unit/services/forecast_lab/test_db_sources.py`.

### Phase 3 — assembly

**T3 — `build_snapshot()`.** `services/forecast_lab/snapshot.py`.
*Scope (in):* orchestration, per-source guards (D13), status block (D16), ensemble summaries (D5),
`aligned_daily_comparison` with the daily completeness gates (BAFU ≥ 22 h, observations ≥ 130
samples), `snapshot_id` (D10), deterministic ordering (D12), unavailable-model entries with a
reason and last-known successful issue time; **ownership of the `include_verification` flag** — it
is a parameter of `build_snapshot()` itself, and with T4 cut (O7.1) it selects only whether the
`verification` key is present at all. When present it is always the `insufficient_data` sentinel
(D3). The flag stays on `build_snapshot()` rather than on the surfaces: with it owned by T5 and T6,
each would have had to splice verification into the document before hashing it — two producers
doing the merge, exactly the drift D1 exists to prevent.
*Scope (out):* HTTP, files, and any computed metric (barred — see D3/O7.1).
*Exit:* `uv run pytest tests/unit/services/forecast_lab/test_snapshot.py`.

**T4 — CUT (owner decision O7.1, 2026-08-21).** No `verification.py`, no metrics, no pairing
logic, no historical BAFU run scanning. T3 emits the `insufficient_data` sentinel (D3). **Do not
reinstate this task** — it is barred by Plan 111's scorer gate, not merely deprioritised. A future
plan may pick it up once Gate G1 returns publication rights; the pairing rule, the daily
aggregation and the UTC-mean convention are already specified in `comparison_semantics` and in the
audit, so nothing is lost by deleting the task here.

### Phase 4 — surfaces (parallel, after Phase 3)

**T5 — REST route.** `api/routes/forecast_lab.py`, registered under `require_principal`.
*Scope (in):* `GET /api/v1/forecast-lab/snapshot`; `station_code` (repeatable),
`observation_hours` (default 168, max 720), `include_verification` (default true); scoping via
`ensure_station_in_scope` for explicitly requested codes (`404`) and scope-filtering for the
no-code case (`200` + empty `stations`, D8); `ETag`/`Last-Modified`/`304` computed live over the response body (D10) — unchanged under
D2-alt; `application/json`.
*Scope (out):* CORS changes, gzip, rate limiting, any change to an existing route.
*Exit:* `uv run pytest tests/unit/api/test_forecast_lab_route.py`.

**T6 — CLI export.** `cli/export_forecast_lab.py`, mirroring `cli/bafu_observation_audit.py` (F10).
*Scope (in):* `--output`, plus the same three parameters; atomic write (temp → validate against the
generated schema → `os.replace`); non-zero exit on total failure; zero exit on a partial snapshot.
*Scope (out):* a new console-script entry in `pyproject.toml` (F10 — module invocation is the
convention).
*Exit:* `uv run pytest tests/unit/cli/test_export_forecast_lab.py` — asserts no partial file survives a
mid-write failure.

### Phase 5 — deployment and documentation

**T7 — Compose wiring.** `docker-compose.yml` (`api`: `SAPPHIRE_CONFIG`, `bafu_forecast_archive`
`:ro`) **and `docker-compose.macmini.yml` (`api`: `SAPPHIRE_CONFIG_OVERLAY` plus the read-only
overlay-file mount)**. An earlier draft claimed the mini overlay needed no edit because its `api`
override is `ports` only — true for the *volume*, wrong for the *path*: the archive path is declared
only in `config/overlays/mac-mini.toml`, so without the overlay the gate resolves to `None` and the
route reports the archive missing with the volume correctly mounted (D2).
*Scope (out):* ports, TLS, `SAPPHIRE_DOMAIN`, CORS, any other service.
*Exit:* `uv run pytest tests/unit/deploy/test_compose_forecast_lab_api_mount.py` — a **committed**
regression test, not an inline heredoc, so the plan's own `uv run pytest` gate covers it and a later
PR cannot silently drop the mount. It mirrors the existing sibling
`tests/unit/deploy/test_compose_ingest_bafu_observation_mount.py`, which asserts a worker's archive
mount against the **rendered** configuration (`docker compose -f … config --format json`, skipping
when the `docker` CLI is absent) — the same mechanism, one service over. It asserts, on the base
file merged with `docker-compose.macmini.yml`, that the `api` service (a) mounts
`bafu_forecast_archive` at `/data/bafu_forecasts` read-only, (b) has
`SAPPHIRE_CONFIG=/app/config.toml`, and (c) has `SAPPHIRE_CONFIG_OVERLAY` plus the overlay mount.
All three are **false on today's repo** (F1, F2) and true only once this task's edit lands.

**The mount test is necessary but not sufficient** — it proves the file is reachable, not that the
path resolves. T7 therefore also loads `DeploymentConfig` under the rendered `api` environment and
asserts `bafu_forecast_archive_path == Path("/data/bafu_forecasts")`. Without that second
assertion the suite can go green while the endpoint returns "archive not mounted" forever. Plus a
documented redeploy procedure; **verification on the mini is a separate, owner-scheduled step.**

**T8 — Documentation.** `docs/spec/forecast-lab-snapshot.md` (timestamp and aggregation semantics,
quantile method, trace normalization **with the F3 correction called out**, staleness thresholds,
offline/caching guidance, the `curl` example using `$SAPPHIRE_API_TOKEN` and a
`<company-lan-host>` placeholder); OpenAPI descriptions on the route; a `docs/touchpoint-maps.md`
entry; a `docs/conventions.md` line if a new convention lands.
*Scope (out):* any doc restructuring beyond these.
*Exit:* `uv run pytest tests/unit/docs/` if such a gate exists, else link-and-example review.

### Separable extras

**T9 — Archive the BAFU inventory GeoJSON** *(see O3)*: one file per collector run alongside the
plot payloads; unlocks `river` and `display_name`; requires reprojection EPSG:2056 → 4326 for any
coordinate use, though the DB coordinate remains canonical.
**T10 — `GZipMiddleware`** *(see O4)*: two lines, affects all routes.

---

## Acceptance criteria

Every one of these is a test, not a review item.

1. The committed example validates against the committed JSON Schema, and the schema equals
   `model_json_schema()` (no drift).
2. Both MVP stations are returned in **one** request.
3. Station scoping is enforced: an explicitly requested out-of-scope or unknown `station_code`
   returns `404` via `ensure_station_in_scope`; a stationless non-admin principal that requests no
   code returns `200` with `stations: []` and `status.overall == "unavailable"`, matching
   `list_stations` (D8, D16 rule 2).
4. Every timestamp is RFC 3339 UTC with a `Z` suffix — asserted by regex over the whole document.
5. Every numeric leaf is a JSON number or `null` — no `NaN`, no `Infinity`, no numeric strings.
6. Coordinates are EPSG:4326 and `crs` says so.
7. Observations are `measured` + `qc_passed` + `discharge` only.
8. BAFU traces normalize correctly despite punctuation (F4) **and the p25/p75 halves are the
   verified way round (F3)** — with a fixture that fails under the inverted mapping.
9. `issued_at` is BAFU's annotated issue time and is never conflated with the archive's
   `produced_at`, which is BAFU's inventory-generation stamp and is exported under a name that says
   so (F8) — no field claims to be our fetch time, because the archive does not hold one.
10. SAPPHIRE ensemble summaries match `numpy.quantile(..., method="linear")` on a known member set.
11. Multiple SAPPHIRE models stay distinguishable and are never merged.
19. Only `operational` `bafu` `river` stations are eligible, applied before principal scoping; an
    `onboarding` station and a same-code station in another network are both excluded (D17a).
20. An `inactive` model assignment is never exported and can never win `is_primary`, even at a
    lower priority number than the active one (D17b).
21. A forecast with `representation == "quantiles"` yields an explicit
    `"unsupported_representation"` unavailable entry, never relabelled outer quantiles (D5).
22. A poisoned SQL transaction surfaces as `500`, not as a partial snapshot; a missing archive file
    surfaces as a partial snapshot, not `500` (D13).
23. `ETag` is weak (`W/"..."`), a repeat `If-None-Match` request returns `304`, and
    `Last-Modified` is omitted when `data_cutoff_at` is null (D10).
12. `nwp_cycle_source == "fallback"` (the persisted column, F5) produces no error, no warning and
    no degraded status.
13. A missing source yields `200` + `status.overall == "partial"` + an explicit reason; all three
    sources failing yields `200` + `status.overall == "unavailable"` (D16 rule 2).
14. Daily completeness gates (BAFU ≥ 22 h, observations ≥ 130) are applied and tested at the
    boundary.
15. `build_snapshot()` queries no `hindcast_forecasts`, `hindcast_values` or `skill_scores` table
    at all, and `verification.status` is always `"insufficient_data"` (D3, O7.1) — so the 2,752
    known-bad future-dated hindcast rows cannot reach the export by any path.
16. Two builds over identical data are byte-identical except `generated_at`.
17. The CLI writes atomically and leaves no partial file on failure.

**Gates:** `uv run pytest`, `uv run ruff format --check`, `uv run ruff check`, `uv run pyright`
(ratchet). Hold at PR — no push, no merge. *(Every Exit path above is under `tests/unit/…` because
that is the repo's actual layout — `tests/` holds `unit/`, `integration/`, `deployment/`, `fakes/`,
`fixtures/` only; `tests/unit/{api,cli,services,deploy,docs}` all exist today.)*

---

## Deviations from the requested contract

| # | Requested | Delivered | Why |
|---|---|---|---|
| 1 | `25.-75. Percentile` first half → `p75` | first half → **`p25`** | **F3** — measured on 3 live runs / 3 stations and corroborated by the checked-in fixture for a 4th. The request is inverted, and the wrong comment at `types/bafu_forecast.py:116` is its likely source (T2a fixes it). |
| 2 | `provenance.nwp_cycle_reason: "too_recent"` | `null` | **F5** — not persisted; Plan 196 cut the task that would persist it. |
| 3 | `model.code_or_image_version` | `model_artifact_provenance.source_commit` when present, else `null` | **F6** — the column exists but holds 0 rows on the mini today, so `null` in practice. Never the running image tag: that is a property of *now*, not of the forecast. |
| 4 | `station.river`, `display_name` | `null` until T9 lands | **F7** — not in SAPPHIRE; the request forbids substituting approximations. |
| 5 | gzip "if already available" | not added | **F9** — not available; adding it changes every route (D11). |
| 6 | `sapphire-flow export-forecast-lab` | `python -m sapphire_flow.cli.export_forecast_lab` | **F10** — repo convention. |
| 7 | — | **added** `comparison_semantics.sapphire_quantile_method` | D5 — the request asked for the method to be documented; putting it in the payload is cheaper and cannot drift. |
| 8 | — | **added** `bafu_forecast.licence_status` | O6 — the unresolved Plan 111 G1 constraint should travel with the data. |
| 9 | `404`/`403` split | `404` for an out-of-scope/unknown requested `station_code`; **no `403` at all** — a stationless principal gets `200` + empty `stations` | D8 — `404` is the actual Plan 147 R2 helper (`api/security.py:230-233`); the empty-scope case follows `list_stations` (`api_stations.py:163-164`), the only analogous sibling, which narrows to an empty `200`. The API's only `403` is `require_admin`. |
| 10 | `peak_magnitude_error` | `null` | No event definition exists and thresholds are empty (F8). Honest null over a fabricated number. |
| 11 | `bafu_forecast.fetched_at` | `inventory_produced_at`; **no `fetched_at` field** | **F8** — the archive stores BAFU's `meta.produced_at`, not our fetch clock; `run_at` is logged and never persisted. Exporting it as `fetched_at` would be a fabricated provenance claim. |

---

## Dependency graph

```json
{
  "phases": [
    { "id": "phase-1", "tasks": ["T1"], "parallel": false },
    { "id": "phase-2", "tasks": ["T2a", "T2b"], "parallel": true, "depends_on": ["phase-1"] },
    { "id": "phase-3", "tasks": ["T3"], "parallel": false, "depends_on": ["phase-2"] },
    { "id": "phase-4", "tasks": ["T5", "T6"], "parallel": true, "depends_on": ["phase-3"] },
    { "id": "phase-5", "tasks": ["T7", "T8"], "parallel": true, "depends_on": ["phase-4"] },
    { "id": "phase-opt", "tasks": ["T9", "T10"], "parallel": true, "depends_on": ["phase-5"] }
  ]
}
```

**T4 is gone** (O7.1) — six tasks plus two separable extras remain. T3 owns the
`insufficient_data` sentinel, so nothing orphans. `phase-opt` is separable in full (O3, O4).

## Review

Non-trivial and external-facing: this is a new API contract on the auth surface. Per
`docs/workflow.md` § Multi-Model Review it needs the Claude design perspective plus a real
repo-grounded Codex pass every round, run through the `plan` workflow, converging with no blockers
and no majors before the owner sets READY.
