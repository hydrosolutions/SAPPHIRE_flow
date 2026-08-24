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

**DRAFT — no review blockers open. Five owner forks remain (O1, O3, O4, O5, O6); each has a
recommendation and a working default, so none of them blocks further *review*, but all of them
block **READY** — they are the human's to answer.** The one blocker, **O7**, was resolved by the owner on
2026-08-21: verification (T4) is **cut**, and the raw BAFU export **proceeds**, recorded as an
Export extension in Plan 111.

### Review history — and an important caveat about what it is worth

Three `plan`-workflow runs, seven rounds. All findings verified against the repo or the live host
before folding; four of them corrected *this plan's own* measured facts (F1, F6, F8, D2).

| Run | Rounds | Codex verdicts | Outcome |
|---|---|---|---|
| 1 | 3 | **1 of 3** | 2 blockers + 12 majors, all folded (the one Codex round found the most) |
| 2 | 2 | 0 of 2 | 3 majors + 2 minors, all folded — every one cut scope |
| 3 | 2 | 0 of 2 | 2 majors + 2 minors, all folded (D19, D20 + two trims) |
| **direct** | — | **1 (manual `codex exec`)** | **8 errors + 9 inconsistencies + 1 blocking gap, all folded** |

**✅ The missing independent review has now been done — outside the workflow.** A direct
`codex exec --sandbox read-only … < /dev/null` pass on 2026-08-21, scoped by the owner to *errors
and consistency only, no engineering expansion*, returned a full verdict in ~8 minutes. It found
**8 factual/citation errors, 9 internal inconsistencies and 1 blocking gap** — the gap being that
the plan had **no authoritative v1 document shape**, so T1 had no input. All are folded, and the
shape is now § The v1 document shape. It also CONFIRMED ~18 substantive claims, including the F3
polygon orientation and the F4 envelope mapping against the checked-in fixture. **This satisfies the
`CLAUDE.md` independent-review floor; the workflow rounds do not, on their own.**

**⚠️ Across the 7 workflow rounds the independent Codex reviewer produced exactly ONE verdict.** Every other
round was Claude lenses only. `.claude/workflows/{plan,implement}.js` invoked `codex exec` without
`< /dev/null`, which makes the CLI block on stdin; both are now fixed — **but that fix is unproven
and has never run**, because `Workflow({name: …})` resolves from a session-start registration
snapshot, so run 3 executed the unfixed script (verified: the persisted `plan-wf_*.js` carries no
redirect). Run 3's failure was a different mode anyway — the relay agent stalled with no progress
for 3 min × 6 attempts during a window of platform model timeouts. To test the fix, invoke by
`scriptPath` (reads from disk), not by name.

**So: this plan is unusually well-grounded, and it has NOT had the independent repo-grounded review
`CLAUDE.md` makes a floor.** Both halves of that sentence are true and neither cancels the other.
Each run also ESCALATED as "stalled" — in runs 1 and 3 that verdict is an artefact of comparing a
Codex-less round against a Codex-ful one, not evidence of thrash.

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
5. No caching layer, no pagination, no rate limiting, **no conditional-GET machinery**
   (`ETag`/`If-None-Match`/`304`/`Last-Modified`) and **no server-side content hash** — a consumer
   that wants dedup hashes the body it already holds (D10). See T11 if that ever needs revisiting.
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
`docs/plans/071-weather-history-meteoswiss-reanalysis.md:349`). `cli/bafu_observation_audit.py` is the existing archive-reading CLI and
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
still calls `build_snapshot()` live (D1) and still emits `snapshot_id` (D10, T5) — it simply has no archive to read, so the BAFU section comes back through
the D13 guard as `status.bafu_forecast = "missing"`, reason `"archive not mounted"`. The complete
three-source snapshot is then produced only by the CLI (T6), run inside a container that already
has the mount (`docker-compose.yml:131` mounts `bafu_forecast_archive` on `prefect-worker`), and
handed to the map as a file. **Trade-off, stated rather than hidden:** under D2-alt the map loses
live BAFU forecasts over HTTP and gets them only from a manually exported file. (A pre-build-to-a-
shared-volume variant was **rejected**: a scheduled job (rule 4), a third producer (breaks D1), and
a staleness class the contract would have to model.)*

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
valid times**. **Two outcomes, distinguished — D13 depends on the split.** If the halves still reconstruct
(mismatch is in the *values*, e.g. non-monotonic), the run is emitted with `available: true` plus an
explicit `quality_flags` entry, never silently reshaped. If the geometry is **unreconstructable**
(the halves' `valid_time` sequences do not correspond, so there is no defensible p25/p75 series at
all), the run is not emitted: that station's `bafu_forecast` is `available: false`, reason
`"parse_error"`, and the source status degrades per D13. Same for the monotonicity check
(`minimum <= p25 <= median <= p75 <= maximum`): retain source values, add a flag.

**D7 — Station metadata: real or `null`, never approximated.** `code`, `name`, `location`
(EPSG:4326), `basin_area_km2`, `active` come from the DB. `river` and `display_name` come from an
archived BAFU inventory snapshot if one exists, else **`null`**. T9 (separable) adds inventory
archiving to the collector so they stop being null within one hour of deploy.

**D8 — Scoping reuses `ensure_station_in_scope`; an out-of-scope or unknown `station_code` is
`404`; a stationless principal gets an empty `200`. This route introduces no new status code.**
The repo-wide Plan 147 R2 mechanism is `api/security.py:230-233`, whose docstring reads
"404 (not 403) on an out-of-scope station — R2: do not reveal existence of stations outside the
caller's scope", and the station-path/detail routes use it (`api_forecasts.py:95`,
`api_stations.py:185,242,269`). *(Not universal: `api_alerts.py:86` calls
`principal.station_in_scope` directly and returns an empty `200` — a query-filter route, not a
station-path route. This plan follows the station-path pattern.)* This route does the same for
**explicitly requested** codes that
are unknown or out of scope: `404 Station not found`.

**⚠️ `ensure_station_in_scope` alone is NOT sufficient, and an earlier draft of D8 was wrong to
imply it was.** `Principal.station_in_scope` returns `True` for an admin **before** it tests for
`None` (`api/security.py:134-142`: `if self.is_admin: return True` precedes
`if station_id is None: return False`). So `ensure_station_in_scope(admin_principal, None)` passes
silently, and an admin token requesting a typo'd `station_code` would resolve to `None`, clear the
scope check, and reach assembly with no station. **T5 must therefore use the same two-step the
sibling `get_station` already uses** (`api_stations.py:185-198`): scope-check, then a *separate*
existence check that runs regardless of `principal.is_admin` and raises `404` when the code did not
resolve. Scope and existence are different questions; only a consumer principal accidentally
conflates them. Tested by AC26.

**`station_code` is repeatable, so the multi-code policy is stated rather than improvised: any
invalid or out-of-scope code among several requested `404`s the whole request** — no partial
result, no per-code error object. That is what `ensure_station_in_scope` already does when called
in a loop (`api/security.py:230-234` raises on the first bad id), it is consistent with the
single-code rule, and it costs no extra code. Tested by AC25.

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
routes return `400` from `_parse_enum` (`api/routes/api_stations.py:133-141`); this route is new, the
request specifies 422, and 422 is what FastAPI produces without custom code. Documented as an
intentional local difference.

**D10 — `snapshot_id` is a timestamp label, not a content hash, and there is no caching story at
all.** `snapshot_id = "fls1-" + generated_at` in compact form (e.g. `fls1-20260821T104500Z`): one
line to produce, promising nothing about content. *(Revised 2026-08-21, third review round —
scope cut.)* The earlier draft made it `sha256` over a canonical serialization of the body that had
to **exclude `snapshot_id` and `generated_at` from itself** — a self-referential construction and
its own bug source — purely so a re-sync over unchanged data would yield an identical id and the map
could skip re-caching. That is caching apparatus, the very thing proportionality rule 5 puts out of
scope, built for a benefit the integration request never asked for and that the single LAN consumer
gets for free: it already holds the response bytes, so `sha256(body)` on its side is one line, needs
no canonicalisation rule here, no exclusion list, and imposes no byte-identity obligation on the
server (the old AC16, now cut). **No `ETag`, no `If-None-Match`, no `304`, no `Last-Modified`**
either. **Trade-off, stated:** a repeat poll over unchanged data still transfers the full
~300–600 KB body (D11) and the server offers no dedup signal of its own; the consumer dedups on its
own hash. If that bandwidth is ever measured to matter, revisit conditional GET then — T11 is a
placeholder, not a spec.

**D19 — an unavailable model carries a reason and nothing else.** An earlier draft had the entry
also report a "last known successful issue time". That field cannot be populated, and the reason is
structural rather than incidental: D14 fetches via `fetch_latest_forecast()`
(`store/forecast_store.py:124-139`), which returns the most recent forecast a model ever produced
for a station **regardless of age** and yields `None` only when the model has produced *none at
all*. There are therefore exactly two unavailable causes, and neither wants the field: for
`"no_forecast"` no successful issue time exists at all; for `"unsupported_representation"` (D5) one
does exist, but the entry is unavailable because the forecast cannot be *summarised*, and reporting
a "last successful" time for a forecast we are actively refusing to render would be misleading.
Either way the entry carries `reason` and nothing else, keeping one shape for both causes. Implementing the field would
mean either a second historical query per unavailable model (new query surface D14 explicitly rules
out) or a column that is always `null` (dead schema, rule 6). It was a leftover from the staleness
concept D16 removed. **Cut.**

**D20 — `build_snapshot()` takes an injected `clock`.** `CLAUDE.md` § Testability Requirements is
marked **CRITICAL** — *"Never use `datetime.now()` … directly in business logic. Always inject
dependencies"* — and this is a live convention, not a dead letter: **62 call sites** in this repo
already take `clock: Callable[[], UtcDatetime]` (`flows/run_hindcast.py:48`,
`preprocessing/converters.py:43`, and so on). The requirement bites harder here than usual because
D10 derives `snapshot_id` **from `generated_at`**, so a bare `datetime.now(UTC)` inside
`build_snapshot()` would make both the timestamp *and* the identifier untestable in one stroke.
`build_snapshot(clock=...)` is therefore part of T3's signature; T5 and T6 pass the real clock in.
**Note this is a NEW pattern at the route boundary, not a copy of a sibling:** no existing route
injects a clock — `api_stations.py:270`, `health.py:41` and `security.py:204` all call
`datetime.now(UTC)` directly. The 62 call sites are in `flows/`, `adapters/` and `preprocessing/`.
The rule still binds (the assembly service is business logic, and `snapshot_id` derives from the
timestamp), but T5 should expect to introduce the wiring rather than copy it. T1's `generated_at` field types as `UtcDatetime` to
match. Tested by AC27 — a frozen clock yields a byte-identical document across two builds.

**D18 — `include_verification` is dropped entirely.** *(Rule 6, applied to this plan's own
leftovers.)* The request specified the flag, and it earned its place while T4 existed: it gated an
expensive computation. With T4 cut (O7.1) `verification` is a static four-field sentinel (status, window, method version, limitations — D3) that
costs nothing to emit, so the flag would only toggle whether a free constant object appears in the
JSON — a distinction the single consumer has no reason to exercise, with no acceptance criterion
covering it, propagating a branch into three places (`build_snapshot()`, a query parameter, a CLI
parameter). Carrying it "so the plumbing is ready when T4 returns" is exactly the foresight
proportionality rule 6 forbids. `verification` is therefore **always present and always
`insufficient_data`**. If a future plan restores real verification, it adds the parameter then.
*(Deviation 12.)*

**D11 — No global gzip.** F9: none exists. Adding `GZipMiddleware` changes every route's response
encoding, which is out of scope ("do not redesign unrelated APIs"). The snapshot for two stations
with 168 h of observations is ~300–600 KB uncompressed — negligible on a LAN. T10 (separable)
offers it if the owner wants it.

**D12 — Determinism is a tested property.** Stations sorted by `code`; points by `valid_time`
ascending; models by `(is_primary desc, model_key asc)`; daily rows by `day_start`; JSON key order
fixed by Pydantic field order; no set iteration anywhere. Two builds from identical data must
produce the same ordering and the same `is_primary` (AC16, AC24). That is a **parsed-document**
comparison, not a byte comparison — with D10's content hash cut, nothing in the contract promises
byte identity, and ordering is what the consumer actually renders.

`is_primary` = the **ACTIVE** assignment (D17b) with the lowest `model_assignments.priority` that
produced a forecast (lowest wins; `nwp_regression` is 10), **ties broken by `model_key` ascending** —
the same rule already used for list order. The tiebreak is not decoration: `priority` has **no
uniqueness constraint and a `server_default` of `0`** (`db/metadata.py:1016`), so any assignment
created without an explicit priority collides at `0` with every other such assignment on the same
station — the exact shape AC11 and F8 anticipate (two active models per MVP station). Without a
stated tiebreak two builds over identical data could pick different winners on unordered DB read
order, contradicting the determinism invariant above (AC16, AC24). Tested by AC24.

**D13 — Partial is the default failure mode.** Each of the three sources is assembled inside its
own guard. A source failure sets `status.<source> = error|missing` with a short non-secret
message and leaves that section unavailable. **But containment has a hard limit that must be stated
rather than discovered:** observations and SAPPHIRE forecasts share one request-scoped SQL
transaction (`api/deps.py`). Catching a failed statement does **not** repair an aborted PostgreSQL
transaction — every later query in that request fails too. Therefore: **`SQLAlchemyError` and
connection failures are whole-snapshot failures and must escape to `500`**; only non-poisoning
failures (archive file missing or unparseable, **unreconstructable** trace geometry per D6, domain
validation) degrade to a partial snapshot. A *reconstructable* mismatch is not a failure at all — it
is an available run carrying a `quality_flags` entry (D6), and it does not touch source status. No savepoints — that is machinery this MVP does not need (proportionality
rule 5). `500` also covers config missing.

**D14 — Latest-forecast fetching reuses the existing store method; no query-count contract.**
`ForecastStore.fetch_latest_forecast()` already exists (`store/forecast_store.py:124-139`) and
returns the latest forecast per `(station_id, model_id, parameter)`. T2b calls it in a small loop
over the assigned `(station, model)` pairs. At the scope this plan is capped to — **two stations**,
a handful of models — that is a handful of trivial indexed queries on a once-per-request LAN
endpoint, functionally indistinguishable from a windowed batch query, and it adds **no** new store
code. (A "two queries regardless of station or model count" draft, asserted by counting queries in
a test, was **rejected**: a performance guarantee for a station count this plan says will never
exist (rule 6), and it would fail a correct implementation needing one more query — e.g. joining
`model_assignments` for the D12 `is_primary` order.) **Trade-off, stated:** query count now grows linearly with `(station, model)`
pairs. If that ever matters it will matter at Nepal/v1 scale, and batching belongs to whichever
plan introduces those stations.

**D17 — The eligible set is narrowed before scoping, and assignments are filtered to ACTIVE.**
Two lookups in this plan default wider than the MVP claims, and both must be constrained explicitly:
(a) `fetch_all_stations()` filters only on `kind` (`store/station_store.py`), so an unfiltered
request under an **admin** token would sweep every station in every status — the eligible set is
therefore **`network='bafu'` AND `station_kind='river'` AND `station_status='operational'`**,
applied *before* principal scoping. This matches the forecast cycle's **kind and status**
restrictions (`run_forecast_cycle.py:2074-2078` fetches `kind=RIVER` then filters
`StationStatus.OPERATIONAL`) and **adds** the BAFU-network restriction, which the cycle does not
apply. A bare
`station_code` is resolved against the canonical `bafu` network, because
`fetch_station_by_code(code, network)` requires both and `(network, code)` is the uniqueness
constraint. **The eligibility filter must run on that path too, not only on the list-all sweep** —
`fetch_station_by_code` filters on `code` and `network` alone (`store/station_store.py:79-91`), so
an explicitly named `bafu` lake or `onboarding` station would otherwise resolve, pass scope, and be
assembled despite being out of scope for the whole feature. Re-check `station_kind == river` and
`station_status == operational` on the resolved row and treat a mismatch as **unknown → `404`**
(D8), so an ineligible station is indistinguishable from a typo. Tested by AC19. (b) `fetch_model_assignments()` returns every status, so an **inactive** assignment
could otherwise supply a stale forecast and even win `is_primary` on priority — filter to
`ModelAssignmentStatus.ACTIVE` first. Both get a regression test (AC19, AC20).

**D15 — The JSON Schema is generated from the Pydantic models and committed**, with a test
asserting the committed file equals `model_json_schema()`. The schema cannot drift from the code.

**D16 — `status.overall` semantics — exhaustive.** Each of the three sources carries a per-source
status in `ok | error | missing`. `status.overall` is the **first** matching rule, so every
combination maps to exactly one value:

1. all three sources `ok` → `ok`;
2. else no source is `ok` (every source is `error` or `missing`) → `unavailable`;
3. else → `partial`.

Rule 2 is the case an earlier draft left undefined (e.g. one `error` and two `missing`, or a
same-day outage across all three sources); it is tested by AC13.

*(Revised 2026-08-21, third review round — scope cut. A fourth per-source value `stale` and its own
overall rule were **removed**. Their thresholds were never given a number anywhere in this plan and
no acceptance criterion exercised the ok/stale boundary or the stale→overall transition: a state
introduced for exhaustiveness rather than for an observed failure. **The snapshot never declares
itself unusable because it is old** — each source already exports its own `issued_at`/`produced_at`
and the document carries `generated_at` (F8), so the map judges age from data it already holds,
without SAPPHIRE encoding and testing a threshold nobody has specified. If a concrete staleness
requirement arrives, it lands then, with real numbers and a real AC. **Not taken** from the same
finding: collapsing `error` and `missing` into a single `unavailable` + reason. Both are concretely
specified in D13, AC22 turns on the distinction (a missing archive file degrades, a poisoned
transaction `500`s), and merging them removes information the consumer's reason string would only
have to re-encode.)*

---

## The v1 document shape (authoritative)

**This section exists because the plan previously did not contain one, and T1 therefore had no
input** (found by the independent Codex review, 2026-08-21: *"the actual v1 JSON contract is
absent"*). The integration request proposed a shape; the decisions above then modified it in a dozen
places. What follows is that shape **as reconciled with every D-number** — the single artifact T1
turns into Pydantic models. Where a decision changed the request, the decision wins and is cited
inline. Field-level nullability rules live with the decisions, not repeated here.

```json
{
  "schema_version": "forecast-lab-snapshot/v1",
  "snapshot_id": "fls1-20260821T104500Z",
  "generated_at": "2026-08-21T10:45:00Z",
  "data_cutoff_at": "2026-08-21T10:40:00Z",
  "status": {
    "overall": "ok",
    "observations":       { "status": "ok", "latest_available_at": "2026-08-21T10:40:00Z", "message": null },
    "bafu_forecasts":     { "status": "ok", "latest_available_at": "2026-08-21T03:00:00Z", "message": null },
    "sapphire_forecasts": { "status": "ok", "latest_available_at": "2026-08-21T06:00:06Z", "message": null }
  },
  "comparison_semantics": {
    "variable": "discharge",
    "unit": "m3/s",
    "display_run_rule": "latest available run from each source",
    "daily_aggregation": "UTC mean over [day_start, next_day_start)",
    "bafu_daily_completeness_minimum": 22,
    "observation_daily_completeness_minimum": 130,
    "sapphire_quantile_method": "linear"
  },
  "stations": [
    {
      "station": {
        "code": "2009", "network": "bafu",
        "name": "Porte_du_Scex", "display_name": null, "river": null,
        "location": { "longitude": 6.89, "latitude": 46.35, "crs": "EPSG:4326" },
        "basin_area_km2": 5239.4, "active": true
      },
      "availability": { "observations": true, "bafu_forecast": true, "sapphire_forecast": true },
      "observations": {
        "variable": "discharge", "unit": "m3/s", "native_step_seconds": 600,
        "window_start": "2026-08-14T10:40:00Z", "window_end": "2026-08-21T10:40:00Z",
        "latest_available_at": "2026-08-21T10:40:00Z",
        "points": [ { "valid_time": "2026-08-21T10:40:00Z", "value": 293.67, "qc_status": "qc_passed" } ]
      },
      "bafu_forecast": {
        "available": true, "source": "bafu", "source_product": "hydrodaten_plot",
        "station_code": "2009", "variable": "discharge", "unit": "m3/s",
        "run_id": "2009_q_forecast_20260821T030000Z",
        "issued_at": "2026-08-21T03:00:00Z",
        "inventory_produced_at": "2026-08-21T05:58:09Z",
        "licence_status": "unresolved",
        "native_step_seconds": 3600,
        "horizon_start": "2026-08-21T03:00:00Z", "horizon_end": "2026-08-26T00:00:00Z",
        "point_count": 118,
        "quality_flags": [],
        "points": [ { "valid_time": "2026-08-21T03:00:00Z",
                      "minimum": 315.1, "p25": 315.1, "median": 315.1, "p75": 315.1, "maximum": 315.1 } ]
      },
      "sapphire_forecasts": [
        {
          "available": true, "source": "sapphire",
          "forecast_id": "6a8f4412-d3fa-4d2e-928b-523707eb6067",
          "model": { "key": "nwp_regression", "display_name": "Nwp Regression",
                     "artifact_id": "8b37b548-32a4-4c20-871c-22bf97797fd3",
                     "artifact_sha256": "<sha256>", "code_or_image_version": null,
                     "is_primary": true },
          "variable": "discharge", "unit": "m3/s",
          "issued_at": "2026-08-21T06:00:06Z",
          "observation_staleness_hours": 0.2766,
          "native_step_seconds": 86400, "ensemble_size": 21,
          "horizon_start": "2026-08-22T00:00:00Z", "horizon_end": "2026-08-26T00:00:00Z",
          "points": [ { "valid_time": "2026-08-22T00:00:00Z",
                        "minimum": 287.03, "p25": 287.36, "median": 287.85, "p75": 288.16, "maximum": 288.96 } ]
        },
        { "available": false, "model": { "key": "nwp_rainfall_runoff" }, "reason": "no_forecast" }
      ],
      "aligned_daily_comparison": [
        {
          "day_start": "2026-08-22T00:00:00Z", "day_end": "2026-08-23T00:00:00Z",
          "observation": { "value": null, "sample_count": 0, "complete": false },
          "bafu": { "minimum": 265.0, "p25": 272.0, "median": 280.0, "p75": 291.0,
                    "maximum": 305.0, "hour_count": 24, "complete": true },
          "sapphire": { "nwp_regression": { "minimum": 287.03, "p25": 287.36, "median": 287.85,
                                            "p75": 288.16, "maximum": 288.96, "complete": true } }
        }
      ],
      "verification": {
        "status": "insufficient_data",
        "window_start": null, "window_end": null,
        "method_version": "forecast-comparison/v1",
        "limitations": [
          "Verification is not computed in v1 (Plan 111 gate G1 — no BAFU-derived benchmark before licence clarity).",
          "Only two operational SAPPHIRE stations.",
          "Short comparison period."
        ]
      }
    }
  ]
}
```

**Deltas from the integration request, each owned by a decision** — an implementer reading the
request alone would get these wrong:

| Request said | v1 says | Owner |
|---|---|---|
| `fetched_at` | `inventory_produced_at`, no fetch clock exists | F8, Dev 11 |
| percentile first half → `p75` | first half → **`p25`** | F3 |
| `nwp_cycle_reason: "too_recent"` | **no `provenance` block at all** — `nwp_cycle_reason` is not persisted (F5) and the remaining two fields are not worth a nested object for one consumer (rule 6) | F5 |
| `code_or_image_version` | `source_commit` when present, else `null` (0 rows today) | F6 |
| `verification.models[]`, `bafu_pairing_rule` | absent — the sentinel carries no metrics and no pairing rule, because none is computed | D3, O7.1 |
| `include_verification` param | dropped | D18 |
| `station.river` / `display_name` | `null` until T9 | D7, F7 |
| `sapphire` as row-per-model | keyed object by `model_key` | D4 |
| ETag / `Last-Modified` / `304` | none | D10 |

**The example above is illustrative, not the fixture.** T1 produces the committed fixture from real
data; the `bafu` daily-aggregate numbers here are plausible placeholders (no server-side daily
aggregation exists yet to have measured them), and every other value is drawn from the audit's real
records. AC1 validates the *committed fixture* against the generated schema — not this block.

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
  *"the collector may archive; the ***scorer*** and the paper stay gated"* (`111:566-569`).
  **T4 computes a benchmark. This is a direct conflict, not a judgement call.**
- The quarantine's stated property is that *"a single `rm -rf` of that directory discards the whole
  archive"* (`111:17-20`). Exported snapshots are copies that a `rm` of the volume no longer
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

### Phase 0 — deployment wiring (no dependencies; runs first)

**T7 — Compose wiring.** `docker-compose.yml` (`api`: `SAPPHIRE_CONFIG`, `bafu_forecast_archive`
`:ro`) **and `docker-compose.macmini.yml` (`api`: `SAPPHIRE_CONFIG_OVERLAY` plus the read-only
overlay-file mount)**. An earlier draft claimed the mini overlay needed no edit because its `api`
override is `ports` only — true for the *volume*, wrong for the *path*: the archive path is declared
only in `config/overlays/mac-mini.toml`, so without the overlay the gate resolves to `None` and the
route reports the archive missing with the volume correctly mounted (D2).
**Under O1 = no (D2-alt), T7 shrinks rather than disappearing:** the `api` archive mount is *not*
added, and assertion (a) below drops its mount clause — but `SAPPHIRE_CONFIG` and
`SAPPHIRE_CONFIG_OVERLAY` on `api` are still required, because the route must resolve the archive
path far enough to report `"archive not mounted"` deliberately rather than crash. Assertion (b) is
unchanged. The dependency graph is unchanged either way.
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
*path* resolves. Without a second assertion the suite can go green while the endpoint returns
"archive not mounted" forever. But that assertion **cannot** be written the obvious way: feeding the
rendered container environment straight into `load_config()` from a host-run pytest raises
`FileNotFoundError` (`config/_overlay.py:36-38` — `/app/config.toml` does not exist on the host),
which fails identically whether or not the compose wiring is right, so it would confirm nothing.
No existing test under `tests/unit/deploy/` demonstrates a pattern for this, so T7 spells it out as
**two separate assertions**:

- **(a) Wiring — string comparison over the rendered JSON, no config loading.** On the base file
  merged with `docker-compose.macmini.yml`, the `api` service's environment has
  `SAPPHIRE_CONFIG == "/app/config.toml"` and
  `SAPPHIRE_CONFIG_OVERLAY == "/app/config/overlays/mac-mini.toml"`, and both files are mounted
  read-only at exactly those targets. **False on today's repo** (F1, F2) — this is the red half.
- **(b) Path derivation — `load_config()` against the REAL repo-relative files**, with the overlay
  env var pointed at the repo copy rather than the container path, asserting that
  `bafu_forecast_archive_path` resolves to the archive mount point. This is sound because the
  overlay declares that path as a **literal container-absolute string**
  (`config/overlays/mac-mini.toml:8`) while base `config.toml` has no `[adapters.bafu_forecast]`
  section at all — so the value a host-run test derives is the value the container derives, with no
  `${SAPPHIRE_*}` interpolation in play (`config/deployment.py:388-403`; `config.toml` contains no
  `${…}` references, verified). Verified 2026-08-21 that it resolves today and yields `None` when the
  overlay is dropped. *(The exact `monkeypatch` calls and literal path values belong in the test,
  not here — same policy F3 applies to itself: values rot in a plan, assertions do not.)*

(b) is therefore **green on today's repo** and is a guard, not a red-first assertion: (a) catches
the compose wiring being wrong or later removed, (b) catches the overlay's path declaration being
removed or renamed. Neither alone closes the gap. Plus a documented redeploy procedure;
**verification on the mini is a separate, owner-scheduled step.**

### Phase 1 — contract

**T1 — Snapshot models, generated JSON Schema, example fixture.**
*Scope (in):* Pydantic v2 boundary models for the document shape given in **§ The v1 document
shape (authoritative)** — that section is T1's input; do not re-derive the shape from the
integration request, which differs in nine places listed there. Models live in
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
**`issued_at` (BAFU's annotation) and `inventory_produced_at` (BAFU's `meta.produced_at`) stay
distinct; there is NO `fetched_at` — the archive holds no fetch clock (F8, Deviation 11).**
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
samples), the timestamp-derived `snapshot_id` (D10 — **no content hash, no canonical
serialization**), deterministic ordering (D12), unavailable-model entries carrying **a reason
only** (D19); the always-present `verification` sentinel (D3); and an **injected `clock`** (D20).
**There is no `include_verification` parameter** — see D18.
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
`observation_hours` (default 168, max 720) — **no `include_verification`** (D18); scoping via
`ensure_station_in_scope` for explicitly requested codes (`404`) and scope-filtering for the
no-code case (`200` + empty `stations`, D8) — and **any** invalid code among several requested
`404`s the whole request (D8); `snapshot_id` as emitted by `build_snapshot()` (D10) — unchanged
under D2-alt; `application/json`.
*Scope (out):* CORS changes, gzip, rate limiting, conditional-GET headers, any server-side hashing
of the response body (D10, T11), any change to an existing route.
*Exit:* `uv run pytest tests/unit/api/test_forecast_lab_route.py`.

**T6 — CLI export.** `cli/export_forecast_lab.py`, mirroring `cli/bafu_observation_audit.py` (F10).
*Scope (in):* `--output`, plus the same two query parameters (D18); atomic write (temp → validate against the
generated schema → `os.replace`); non-zero exit on total failure; zero exit on a partial snapshot.
*Scope (out):* a new console-script entry in `pyproject.toml` (F10 — module invocation is the
convention).
*Exit:* `uv run pytest tests/unit/cli/test_export_forecast_lab.py` — asserts no partial file survives a
mid-write failure.

### Phase 5 — documentation

**T8 — Documentation.** `docs/spec/forecast-lab-snapshot.md` (timestamp and aggregation semantics,
quantile method, trace normalization **with the F3 correction called out**,
offline/caching guidance (including: the server emits no content hash — a consumer that wants dedup
hashes the body it fetched, D10), the `curl` example using `$SAPPHIRE_API_TOKEN` and a
`<company-lan-host>` placeholder); OpenAPI descriptions on the route; a `docs/touchpoint-maps.md`
entry; a `docs/conventions.md` line if a new convention lands.
*Scope (out):* any doc restructuring beyond these.
*Exit:* `uv run pytest tests/unit/docs/` if such a gate exists, else link-and-example review.

### Separable extras

**T9 — Archive the BAFU inventory GeoJSON** *(see O3)*: one file per collector run alongside the
plot payloads; unlocks `river` and `display_name`; requires reprojection EPSG:2056 → 4326 for any
coordinate use, though the DB coordinate remains canonical.
**T10 — `GZipMiddleware`** *(see O4)*: two lines, affects all routes.
**T11 — Conditional GET** *(cut from v1 by D10)*: `ETag`/`If-None-Match`/`Last-Modified`. Only
worth building if repeat-poll bandwidth is measured to matter. Design it then, against the
requirements that exist then.

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
19. Only `operational` `bafu` `river` stations are eligible — enforced on **both** paths: the
    list-all sweep and an explicitly requested `station_code`. An `onboarding` station, a `lake`
    station and a same-code station in another network are each excluded, and an explicitly
    requested ineligible code returns `404`, indistinguishable from a typo (D17a).
26. An **admin** token requesting an unknown `station_code` gets `404`, not a crash and not a
    silent drop — the existence check runs independently of `principal.is_admin` (D8).
27. `build_snapshot()` accepts an injected `clock`, and under a **frozen** clock two builds over
    identical data are **byte-identical** — including `generated_at` and the `snapshot_id` derived
    from it. No `datetime.now()` appears in `services/forecast_lab/` (D20, `CLAUDE.md` §
    Testability Requirements).
28. An unavailable-model entry carries a `reason` and no last-successful-issue-time field (D19).
20. An `inactive` model assignment is never exported and can never win `is_primary`, even at a
    lower priority number than the active one (D17b).
21. A forecast with `representation == "quantiles"` yields an explicit
    `"unsupported_representation"` unavailable entry, never relabelled outer quantiles (D5).
22. A poisoned SQL transaction surfaces as `500`, not as a partial snapshot; a missing archive file
    surfaces as a partial snapshot, not `500` (D13).
12. `nwp_cycle_source == "fallback"` (the persisted column, F5) produces no error, no warning and
    no degraded status.
13. A missing source yields `200` + `status.overall == "partial"` + an explicit reason; all three
    sources failing yields `200` + `status.overall == "unavailable"` (D16 rule 2).
14. Daily completeness gates (BAFU ≥ 22 h, observations ≥ 130) are applied and tested at the
    boundary.
15. `build_snapshot()` queries no `hindcast_forecasts`, `hindcast_values` or `skill_scores` table
    at all, and `verification.status` is always `"insufficient_data"` (D3, O7.1) — so the 2,752
    known-bad future-dated hindcast rows cannot reach the export by any path.
16. Two builds over identical data produce identical **ordering** — of stations, forecast points,
    models and daily rows — and the same `is_primary`, asserted on the parsed documents (D12).
    *(Not byte identity: D10's content hash is cut, so nothing depends on field-order stability,
    and two builds legitimately differ in `generated_at` and the `snapshot_id` derived from it.)*
17. The CLI writes atomically and leaves no partial file on failure.
24. Two ACTIVE assignments on one station at **equal** `priority` (e.g. both at the `0`
    `server_default`, `db/metadata.py:1016`) yield a deterministic `is_primary` — the lower
    `model_key` — identically across repeated builds and across shuffled DB row order (D12).
25. A request mixing a valid and an invalid/out-of-scope `station_code`
    (`?station_code=<valid>&station_code=<invalid>`) returns `404` for the whole request, not a
    partial `200` (D8).

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
| 12 | `include_verification` query parameter | **dropped** | D18 — with T4 cut it would toggle only whether a free static sentinel appears; one consumer, no AC, three branches. Rule 6. |
| 11 | `bafu_forecast.fetched_at` | `inventory_produced_at`; **no `fetched_at` field** | **F8** — the archive stores BAFU's `meta.produced_at`, not our fetch clock; `run_at` is logged and never persisted. Exporting it as `fetched_at` would be a fabricated provenance claim. |

---

## Dependency graph

```json
{
  "phases": [
    { "id": "phase-0", "tasks": ["T7"], "parallel": false },
    { "id": "phase-1", "tasks": ["T1"], "parallel": false },
    { "id": "phase-2", "tasks": ["T2a", "T2b"], "parallel": true, "depends_on": ["phase-1"] },
    { "id": "phase-3", "tasks": ["T3"], "parallel": false, "depends_on": ["phase-2"] },
    { "id": "phase-4", "tasks": ["T5", "T6"], "parallel": true, "depends_on": ["phase-3"] },
    { "id": "phase-5", "tasks": ["T8"], "parallel": false, "depends_on": ["phase-4"] },
    { "id": "phase-opt", "tasks": ["T9", "T10"], "parallel": true, "depends_on": ["phase-5"] }
  ]
}
```

**T4 is gone** (O7.1) — **eight core task nodes** remain (T7, T1, T2a, T2b, T3, T5, T6, T8) plus two separable extras.
T11 is **cut from v1** by D10 and is deliberately **absent from the graph** — it is a named
placeholder for a future plan, not a schedulable task here. T3 owns the
`insufficient_data` sentinel, so nothing orphans. `phase-opt` is separable in full (O3, O4).

**T7 has no dependencies and runs first.** It edits two compose files and its exit gate reads
rendered compose JSON plus `load_config()` against real files — it never reads
`services/forecast_lab/*`, the route, or the CLI. An earlier draft serialised it behind phase 4,
forcing independent infra work to wait on code it does not touch. Running it first also front-loads
the one task whose failure would invalidate the deployment story (F1/F2), so a wiring problem
surfaces before six tasks of assembly work depend on it.

## Review

Non-trivial and external-facing: this is a new API contract on the auth surface. Per
`docs/workflow.md` § Multi-Model Review it needs the Claude design perspective plus a real
repo-grounded Codex pass every round, run through the `plan` workflow, converging with no blockers
and no majors before the owner sets READY.
