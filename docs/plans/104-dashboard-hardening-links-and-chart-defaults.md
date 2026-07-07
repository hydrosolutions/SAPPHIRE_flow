# Plan 104 — dashboard hardening: links, chart defaults, and skill-chart consistency

**Status**: READY — drafted from the dashboard-investigator pass on 2026-07-06;
converged through six adversarial plan-review rounds (2026-07-06/07). Confirmed READY
by the user on 2026-07-07.
**Priority**: medium — these are review-dashboard correctness and operability
fixes not covered by Plans 100/101/102. They do not block the forecast pipeline,
but they make the dashboard misleading or less useful during operations.
**Phase**: v0b — review dashboard hardening
**Parent**: local dashboard investigation, 2026-07-06
**Related**:
- Plan 100 — forecast-feed resilience. Forecast-feed outage/staleness is out of
  scope here.
- Plan 101 — water_level QC datum/range failures. QC correctness is out of scope
  here.
- Plan 102 — station-detail multi-parameter observation visibility. This plan must
  avoid duplicating that work and should run after Plan 102 if both touch
  `stations/detail.html`.
- `src/sapphire_flow/api/templates/base.html:35-41` and
  `src/sapphire_flow/api/templates/dashboard.html:94-98` (Prefect links)
- `src/sapphire_flow/api/__init__.py:14-25` and `docker-compose.yml:169-172`
  (`PREFECT_UI_URL` becomes a browser-visible link)
- `src/sapphire_flow/api/routes/stations.py:235-252` and
  `src/sapphire_flow/api/templates/stations/detail.html:39-47` (basin context is
  displayed but not linked)
- `src/sapphire_flow/api/routes/models.py:82-126` and `:175-190` (model detail
  and skill-chart endpoint can select different active artifacts)
- `src/sapphire_flow/api/templates/models/detail.html:114-119` (skill chart fetch
  omits the artifact id shown in the table)
- `src/sapphire_flow/api/templates/stations/detail.html:191-201`, `:300-328`
  (all station charts share the last-30-days date default even when forcing and
  hindcasts are historical)
**Created**: 2026-07-06

---

## Problem

The dashboard investigation found no internal 404/500 links and no missing chart
endpoints for existing data. It did find four NEW dashboard issues that are not
covered by Plans 100/101/102:

1. **Prefect UI link is dead from the host browser.** The rendered link is
   `http://prefect-server:4200`, which is a Docker-internal hostname. The dev
   overlay exposes Prefect on host port `4200`, so the browser-visible URL should
   be `http://localhost:4200` for local development.
2. **Station basin data is displayed but not linkable.** Both stations have
   `basin_id` rows, but the station-detail basin card renders plain text only.
   The requested station→basin cross-link does not exist.
3. **Model skill chart can disagree with the table below it — because a model has
   multiple simultaneously-active artifacts (one per station).** The model-detail
   route orders artifacts newest-first and displays the *first* active artifact's
   table; the `/api/v1/models/{model_id}/skill-chart.json` endpoint resolves an
   active artifact with unordered `LIMIT 1` — both filter by `model_id` only. The
   deeper cause is structural: `model_artifacts` is unique per
   `(station_id, model_id)`, not per `model_id` (`db/metadata.py:493-513`), so a
   STATION-scope model assigned to N stations has N active artifacts at once. On the
   2-station dev stack `climatology_fallback` has two: the "175 vs 170 rows"
   mismatch is two different *stations'* concurrently-active artifacts, not a
   stale-vs-fresh race. Task 2A must therefore both reconcile chart↔table AND avoid
   silently hiding the other station's data.
4. **Forcing and hindcast charts default to empty date windows.** The station page
   defaults all charts to the last 30 days. Observations have current data, but
   `historical_forcing` ends at 2020-12-31 and `hindcast_forecasts` ends at
   2022-12-21, so those charts appear empty by default despite substantial data
   existing.

## Goal

Make dashboard links resolvable from the user's browser, make station basin
relationships navigable, make model skill charts represent the same artifact as
their detail tables, and prevent historical station charts from looking empty
just because the shared default date window is current-time oriented.

## Non-goals

- Fixing forecast-feed blackout or fallback behavior — Plan 100.
- Fixing `water_level` QC failures or threshold configuration — Plan 101.
- Redesigning multi-parameter observation visibility — Plan 102.
- Adding authentication, permissions, or a production-grade dashboard IA.
- Changing database schemas (no migrations).
- Changing any existing endpoint contract (the former Task 2C range-cap relaxation was
  cut in round 5 — Task 2B now shows a static empty-state message instead of re-querying).

## Evidence from the investigation

- All internal links discovered from dashboard, station, forecast, model, coverage,
  and table pages returned HTTP 200.
- Prefect link failed with DNS resolution for `prefect-server`; `docker-compose.dev.yml`
  exposes host port `4200`.
- Basin rows exist for stations 2009/2091:
  `stations.basin_id -> basins.id`, but the station page emits no `<a>` for the
  basin card.
- Forecast detail charts are meaningful: latest NWP member forecasts returned
  105 values each (21 members × 5 lead times); climatology quantile forecasts
  returned 35 values each (7 quantiles × 5 lead times).
- Station default observation endpoints are meaningful:
  discharge and water_temperature return current qc_passed points; water_level
  returns current points with mostly `qc_failed` status, as expected from Plan 101.
- Station default forcing/hindcast endpoints return zero points, while historical
  windows return real data: forcing `2020-12-01..2020-12-15` returns 15
  precipitation + 15 temperature rows per station; hindcasts for the same window
  return 75 discharge hindcast steps per station.
- Five non-virtual models have skill scores and embedded diagrams; virtual models
  `_bma`, `_pooled`, `_consensus` have no active artifacts and correctly show no
  skill chart.

## Implementation Plan

### Phase 1 — Link correctness

#### Task 1A — Browser-safe Prefect UI URL

**Scope**: Make dashboard links point to a browser-resolvable Prefect UI URL in
*every environment we currently operate*. The root defect is a single wrong value at
its source: the base compose hardcodes `PREFECT_UI_URL: http://prefect-server:4200`
(`docker-compose.yml:172`), a Docker-internal hostname that **no browser in any
environment can resolve** — dev, Mac-mini, or future prod. This is not an
environment-specific value: `docs/standards/security.md:485-491` already fixes Prefect
as internal-only, reached in *every* currently-operated deployment exclusively via SSH
tunnel, so the browser always talks to `localhost:4200`. There is therefore no
per-environment reason for the URL to vary today.

**Fix the value at its source, not per-overlay.** Change `docker-compose.yml:172`
directly from `PREFECT_UI_URL: http://prefect-server:4200` to
`PREFECT_UI_URL: http://localhost:4200`. This already matches the existing Python
fallback in `src/sapphire_flow/api/__init__.py:16`
(`os.environ.get("PREFECT_UI_URL", "http://localhost:4200")`), so the base value
becomes redundant-but-consistent rather than contradictory. **Keep the explicit
compose line** (rather than deleting it to rely solely on the code default): compose
is the operational config surface an operator reads to see the browser-facing URL, and
a future overlay that needs a different hostname overrides it there. The one-time cost
is a value duplicated with the code default; the benefit is discoverability and a
single obvious override point.

**Guardrail — do NOT touch `PREFECT_API_URL` (line 171).** The line directly above the
edit target is `PREFECT_API_URL: http://prefect-server:4200/api`, a *different* variable
that MUST keep resolving the Docker-internal `prefect-server` hostname — it is read at
container runtime by `routes/health.py:15` for the in-container Prefect health probe and
by Prefect's own client. Only `PREFECT_UI_URL` (line 172, browser-facing) changes; the
two visually-adjacent, similarly-named vars are an easy one-line mix-up in a diff. Do **not** scatter a
duplicate `PREFECT_UI_URL` override into `docker-compose.dev.yml` and
`docker-compose.macmini.yml` — that reproduces exactly the failure class that caused
this bug (Plan 053 set a Docker-internal default at base level with a dev-only
fallback, and the macmini overlay silently never got an override, leaving the link
dead there for a long stretch). A single source value means a future third overlay
inherits the correct URL for free.

**Only the per-overlay port-publish lines differ**, because only the *port binding*
is environment-specific:

1. **Local dev** (`docker-compose.dev.yml`): today publishes BOTH a bare `4200:4200`
   (Prefect, line 16) AND a bare `8010:8000` (API, line 20), which Docker binds on **all**
   host interfaces (`0.0.0.0`) — so a developer running the dev stack on a LAN exposes the
   **unauthenticated** Prefect UI/API AND the FastAPI to the network, contradicting the
   same internal-only policy (`security.md:485-491`) that governs the Mac-mini overlay.
   **Loopback-bind BOTH dev services:** `prefect-server: ports: ["127.0.0.1:4200:4200"]`
   and `api: ports: ["127.0.0.1:8010:8000"]` (dev keeps its offset host port `8010` — only
   the bind address changes). The local developer's browser still reaches `localhost:4200`
   and `localhost:8010` directly (loopback), and the LAN holes close. This makes the
   loopback binding a uniform, policy-consistent rule across every overlay and both services
   rather than a Mac-mini-only special case. (`PREFECT_UI_URL` is still inherited from the
   corrected base — no per-overlay override.)
2. **Mac-mini staging** (`docker-compose.macmini.yml`) — the team's actual
   currently-operated deployment. This overlay today publishes neither host port
   `4200` nor anything but `api: "8000:8000"` (`docker-compose.macmini.yml:36-38`).
   `prefect-server` also has no `ports:` key in the base compose
   (`docker-compose.yml:37-66`), so the SSH-tunnel path documented for operators today
   (`docs/deployment/mac-mini-staging.md:279,293`, `LocalForward 4200 localhost:4200` /
   `ssh -L 4200:localhost:4200`) is **currently non-functional** — nothing on the
   Mac-mini *host's* `localhost:4200` is listening for the tunnel to forward to.

   **The port publish is loopback-only, not LAN, and it is dictated by existing policy
   — not an open trade-off for the implementer.** `docs/standards/security.md:485-491`
   (§Network policy) lists "Prefect server: 4200" under *"Internal only (Docker
   network, not exposed to host)"* and states *"Prefect UI (port 4200) is accessible
   only via SSH tunnel: `ssh -L 4200:localhost:4200 user@vm`."* `Caddyfile:14-16`
   independently enforces the same decision. Exposing 4200 on the Mac-mini LAN would
   invert that already-decided policy, so it is explicitly rejected here.

   The Mac-mini overlay fix is therefore a **single port-publish line** (no
   `PREFECT_UI_URL` override — it inherits the corrected base value):
   - Publish the port **bound to loopback only** in `docker-compose.macmini.yml`:
     `prefect-server: ports: ["127.0.0.1:4200:4200"]` (never a bare `4200:4200` or a
     LAN IP — loopback binding keeps it off the LAN and satisfies the "not exposed to
     host [network]" intent while still giving the SSH tunnel a local endpoint to
     forward to). This makes the tunnel actually work. A tunnelled operator whose
     `LocalForward 4200 localhost:4200` maps the browser's `localhost:4200` onto the
     Mac-mini's loopback-published port then reaches the inherited
     `PREFECT_UI_URL=http://localhost:4200` correctly.
   - Document the (now-actually-working) SSH-tunnel procedure in Task 3B.

   **Also loopback-bind the Mac-mini API (port 8000) — the same exposure, same fix.**
   `docker-compose.macmini.yml:36-38` publishes the **unauthenticated** FastAPI as a bare
   `8000:8000`, which Docker binds on all host interfaces — LAN-exposing the API even
   though `security.md:485-491` lists FastAPI 8000 as internal-only. This is the identical
   class of hole as the Prefect one and is fixed the same way: change the `api` service to
   `ports: ["127.0.0.1:8000:8000"]`. The host watchdog still probes `localhost:8000`
   (loopback is served), and operator dashboard access continues via the existing
   tunnel/Caddy path — the LAN hole closes. **This is a security change to a live-operated
   host and expands Plan 104 beyond the Prefect-link fix, so flag it for the IT/security
   teammate's sign-off before the code PR merges** (the analogous Prefect change was
   already policy-mandated; this one re-binds a currently-LAN-reachable service, so it
   warrants an explicit OK). **Decision: the re-bind ships in THIS PR, with IT/security
   sign-off as a merge gate** — the security fix stays atomic with the rest of Task 1A
   rather than being split out. Before sign-off, IT confirms no direct LAN client depends
   on the Mac-mini `:8000` (only the loopback host watchdog + the tunnel/Caddy path should);
   if a LAN consumer is found, that is surfaced for a decision rather than silently
   breaking it. The PR does not merge until that confirmation lands.

For any *future* deployment target that genuinely needs a different browser-facing
URL (e.g. a production reverse-proxy hostname), it must still override
`PREFECT_UI_URL` explicitly in that overlay — but no such environment exists today,
so no override is written now.

**Out of scope**: Moving Prefect behind Caddy, adding auth, exposing Prefect on any
LAN/public interface, or otherwise changing the internal-only Prefect networking
policy in security.md §Network policy.

**Verification**:

```bash
uv run pytest tests/integration/api/test_dashboard*.py
```

Manual smoke:

```bash
curl -sS http://127.0.0.1:8010/ | rg 'http://localhost:4200'
curl -sS http://127.0.0.1:8010/models/ | rg 'http://localhost:4200'
```

#### Task 1B — Station→basin cross-link

**Scope**: Add a minimal read-only basin detail page and link station-detail basin
cards to it. Include basin metadata and the stations belonging to that basin, with
station links back to `/stations/{station_id}/`.

**Out of scope**: Full basin management, basin maps, geometry rendering, or basin
editing.

**Implementation notes**:
- Add a dashboard route such as `/basins/{basin_id}/` in the existing dashboard
  route layer or a small new `basins.py` router.
- **If a new `basins.py` router is created, register it** via
  `app.include_router(basins_router)` in `src/sapphire_flow/api/__init__.py` alongside
  the existing six routers (`health`, `dashboard`, `tables`, `stations`, `forecasts`,
  `models` — `api/__init__.py:51-56`). Without this the route 404s in the running app
  even though the router module and templates are correct; the Task 1B manual smoke
  curl (below) is the end-to-end catch for this.
- **Select an explicit column list — never `sa.select(basins_table)`.** The `basins`
  table has a PostGIS `geometry` (MULTIPOLYGON) column plus `band_geometries` and
  `attributes` JSONB blobs (`db/metadata.py:42-65`), and `get_reflected()` registers
  geoalchemy2 (`routes/tables.py:33`), so a naive full-table select pulls back a raw
  `WKBElement` for `geometry` that Jinja renders as an unreadable hex-WKB string — the
  exact failure `routes/tables.py:60-69` (`_build_select`/`ST_AsText`) and the existing
  hand-listed basin query (`stations.py:242-246`) already avoid. Select only:
  `id, code, name, area_km2, regional_basin, network, created_at`. Explicitly exclude
  `geometry`, `band_geometries`, and `attributes` (this page renders no map/geometry —
  see Out of scope). If a geometry preview is ever wanted, use `ST_AsText`/`_build_select`,
  not the raw column.
- **The basin's station list has the SAME geometry trap — apply the explicit-column
  rule there too.** `stations` also has a PostGIS `location` (POINT) column
  (`db/metadata.py`), plus JSONB/ARRAY columns (`forecast_targets`,
  `measured_parameters`), so a naive `sa.select(stations_table)` for the basin's members
  renders raw WKB / leaks irrelevant columns exactly like the basins case. Select only
  the scalar columns needed to list + link stations: `id, code, name, network`
  (add `altitude_masl` if useful). Explicitly exclude `location` and the JSONB/ARRAY
  columns; if a coordinate is ever wanted, project it with `ST_X`/`ST_Y`, not the raw
  `location`.
- Include `basin_id` in the station-detail basin context.
- Render the basin name/code as a link in `stations/detail.html`.

**Verification**:

```bash
uv run pytest tests/integration/api/test_dashboard*.py
```

Manual smoke:

```bash
curl -sS -i http://127.0.0.1:8010/basins/bdf942fe-6754-412a-8d7f-62c6bd90148c/
curl -sS http://127.0.0.1:8010/stations/d2667187-66b9-4c4a-9d1d-21a658120ed2/ | rg '/basins/'
```

### Phase 2 — Chart correctness

#### Task 2A — Reconcile the model skill chart with a per-station active artifact

**Root cause is structural, not an ordering accident.** The original framing (an
"unordered `LIMIT 1`" tie-break bug) is incomplete. Verified against the schema and
the live dev DB:

- The uniqueness guarantee on `model_artifacts` is per **(station_id, model_id)** or
  **(group_id, model_id)** — not per `model_id` alone
  (`db/metadata.py:493-513`, `ix_model_artifacts_station_model_active` /
  `ix_model_artifacts_group_model_active`, both `postgresql_where status='active'`).
  So a STATION-scope model assigned to N stations legitimately has **N
  simultaneously-active artifacts at once**, one per station.
- `climatology_fallback` is the last-resort fallback for every station
  (`config.toml:59-65` lists it at priority 100), so on the current 2-station dev
  stack it has exactly 2 active artifacts. Problem #3's cited "175 vs 170 rows"
  is precisely this shape: two *different stations'* concurrently-active
  artifacts, not a stale-vs-fresh race.
- Both queries filter by `model_id` only, with no station/group scoping:
  `model_detail` picks the first `status=='active'` artifact from a
  `created_at DESC` list (`routes/models.py:112-117`); `model_skill_chart_json`
  resolves the active artifact with an unordered `.limit(1)`
  (`routes/models.py:175-190`).

**Consequence for the naive fix.** Making both endpoints pick "newest active by
`created_at`" would make chart and table agree with *each other*, but that single
pick is an arbitrary choice between multiple legitimately-active, different-station
artifacts. It would silently drop every other station's current skill data from the
page, flip to a different station's data whenever that station is retrained, and
give no UI signal of which station is shown — converting a *visible* inconsistency
into an *invisible* data-loss-on-display. That is not acceptable as the whole fix.

**Scope**: Ensure the model-detail skill chart and skill-score table always use the
same active artifact AND that no active artifact's skill data is silently hidden for
a multi-station STATION-scope (or multi-group GROUP-scope) model. Concretely:

1. **Make the model-detail page scope-explicit and key the selector by
   `station_id`/`group_id`, NOT by raw `artifact_id`.** In `model_detail`, gather
   *all* active artifacts for the model (one per station/group), not just the first.
   Render a small selector labelled by station code (STATION-scope) or group name
   (GROUP-scope) whose option **value is the `station_id` or `group_id`** — never the
   opaque `artifact_id`. Default to a deterministic choice (the artifact newest by
   `created_at`, tie-broken by artifact id for stability; the selector value is that
   artifact's `station_id`/`group_id`). Display the selected station/group next to the
   chart and table so an operator is never misled into reading one station's skill as
   the model's overall performance.

   **Why key by scope id, not artifact id (parse, don't validate — CLAUDE.md).**
   Threading a globally-unique `artifact_id` through the public query param makes any
   `model_id` + `artifact_id` combination *representable*, including cross-model ones,
   which would force a "verify this artifact_id belongs to model_id" ownership check
   bolted onto every entry point (and every future one). Keying by `station_id` /
   `group_id` instead makes a cross-model reference **structurally unrepresentable**:
   the server resolves the active artifact with a compound
   `WHERE model_id = :model_id AND (station_id = :sid OR group_id = :gid) AND status =
   'active'` against `model_artifacts` (`db/metadata.py:493-513`), so there is no
   `station_id`/`group_id` a client can supply that resolves to a *different* model's
   row. The entire manual ownership-check requirement disappears rather than needing
   must-not-forget validation on two (and potentially more) endpoints.
2. **Pin chart, table, and diagrams to the same resolved artifact.** The skill-score
   table, the skill diagrams, and the chart fetch all resolve from the same selected
   `station_id`/`group_id` → active artifact. The template fetch passes
   `station_id={{ selected_station_id }}` (or `group_id={{ selected_group_id }}`) to
   `/api/v1/models/{model_id}/skill-chart.json?station_id=...`.
3. **Resolve the JSON endpoint deterministically and model-scoped.** When the scope
   param is supplied, `model_skill_chart_json` resolves the active artifact via the
   same compound `WHERE model_id AND (station_id OR group_id) AND status='active'`,
   returning 404 when no active artifact matches for this model+scope (a foreign or
   unassigned scope id simply yields no row — cross-model data cannot leak). When the
   scope param is omitted, fall back to the deterministic newest-first-by-`created_at`
   (tie-broken by id) active artifact for the model instead of the current unordered
   `.limit(1)` — but the template always passes an explicit scope id, so the default is
   only a fallback.

   > Design note: this replaces the earlier draft's `?artifact_id=` param + explicit
   > "validate artifact_id belongs to model_id, 404 on mismatch" ownership check
   > (previously scope item 4). That check is no longer a separate requirement because
   > the compound model-scoped lookup makes cross-model resolution impossible by
   > construction, not by a remembered guard.

**Decision (resolved in review, 2026-07-06): drop-down selector keyed by
scope id.** The page renders a `<select>` of all active artifacts (labelled by
station code / group name, **option value = `station_id`/`group_id`**) and switches
the chart, skill-score table, and skill diagrams together to the chosen scope's active
artifact, defaulting to the deterministic newest-by-`created_at` pick. Rationale for
choosing the selector over the lighter "indicator + note" minimum: the note variant
still shows only one station's skill while merely *acknowledging* the others exist,
which the reviewer flagged as converting visible inconsistency into
silent-on-display data loss — the selector actually lets an operator read each
station's skill. Rationale for choosing a selector over "display all active
artifacts at once" (a single chart/table overlaying every station): a
STATION-scope model can be assigned to many stations (v0 targets ~1000), so an
all-at-once view produces an unreadable N-series chart and an N×-rows table and does
not scale; the drop-down keeps chart/table legible (one artifact shown at a time)
regardless of station count.

**Known limit of the chosen fix (accepted for this pass).** A plain `<select>` still
lists one option per active artifact, so a fleet-wide fallback model like
`climatology_fallback` (priority 100, assigned to every station — `config.toml:59-65`)
would eventually render a dropdown with up to ~1000 options at v0's target scale. This
is acceptable for v0b: the dev/staging stack has 2 stations today, and a long-but-scrollable
`<select>` degrades gracefully (unlike the all-at-once chart, which becomes unreadable).
If/when a model's active-artifact count grows large in practice, a lightweight follow-up
(searchable/typeahead `<select>`, or grouping) is the mitigation — explicitly deferred,
not designed here. An at-a-glance cross-station view would instead require skill
*aggregation*, which is out of scope (below).

**Out of scope**: Redesigning skill diagrams, changing skill-score computation, or
aggregating multiple station artifacts into a single combined model-level chart
(cross-station skill aggregation is a separate design question).

**Implementation notes**:
- In `model_detail`, compute the list of active artifacts (with station/group
  labels and their `station_id`/`group_id`) and a `selected_station_id` /
  `selected_group_id` (whichever matches the model's scope); pass both in context.
- **Wire the selector to the server-rendered parts of the page.** `skill_scores`
  and `skill_diagrams` are rendered server-side in Jinja (`models/detail.html:68-99`),
  not fetched client-side like the chart — so a `<select>` alone changes nothing for
  the table/diagrams. `model_detail` MUST accept an optional `station_id` (STATION
  scope) / `group_id` (GROUP scope) query param typed `uuid.UUID | None = Query(default=None)`
  (NOT `Query("")` — an empty-string default collides with UUID typing and would make an
  *omitted* param a `""` that fails artifact resolution / virtual-model empty states), and
  resolve the selected active artifact via the compound `WHERE model_id AND (station_id OR
  group_id) AND status='active'`; when the param is `None` (omitted), use the deterministic
  default. The
  selector is a GET-triggering control that reloads the page:
  `<select onchange="location.href='?station_id='+this.value">` (or `group_id`). This
  keeps chart, table, and diagrams re-rendered from one resolved artifact on every
  switch — no partial AJAX rework of the server-rendered sections.
- **No separate ownership check needed** (removed vs. the earlier draft). Because
  `model_detail` resolves the artifact by `model_id` + supplied `station_id`/`group_id`
  through the compound WHERE, a foreign or unassigned scope id resolves to no row →
  404. There is no representable scope-id value that renders a *different* model's
  table/diagrams under the correct-looking `model_id`, so the cross-model leak is
  closed by construction, not by a remembered guard. `model_detail` currently takes no
  query param (`routes/models.py:60-64`); the new param is model-scoped by the lookup.
- **Define scope-param validation explicitly (both entry points).** The compound
  `WHERE` alone leaves ambiguous cases the DB does not constrain — `models.artifact_scope`
  (`db/metadata.py:433`, CheckConstraint `IN ('station','group','virtual')`) is NOT
  enforced to agree with which of `station_id`/`group_id` a caller passes, and
  `model_artifacts` only enforces station/group XOR *per artifact row*
  (`ck_model_artifacts_scope_xor`, `db/metadata.py:489`), not consistency with the model
  row. So specify: (a) at most **one** of `station_id`/`group_id` may be supplied —
  reject **both-supplied** with 400; (b) the supplied param must match the model's
  `artifact_scope` (`station_id` only when `artifact_scope='station'`, `group_id` only
  when `'group'`) — reject a **wrong-scope** param with 400; (c) a `virtual` model has no
  active artifacts and takes no scope param (renders the existing no-skill-chart empty
  state); (d) a well-formed-but-unassigned/foreign scope id resolves to no row → 404 (the
  by-construction leak closure above). Add tests for the 400 (both-supplied, wrong-scope)
  and 404 (foreign/unassigned) cases on both `model_detail` and `model_skill_chart_json`.
- **Parse the scope params as UUIDs at the boundary (don't hand raw strings to SQL).**
  `station_id`/`group_id` are compared against reflected PostgreSQL `UUID` columns
  (`db/metadata.py:453`); a malformed value like `station_id=not-a-uuid` passed straight
  into the query becomes a **DB error / 500**, not a clean client error. Type the params
  as `uuid.UUID | None = Query(default=None)` so FastAPI returns **422** on a malformed
  value automatically and treats an *omitted* param as `None` (parse-don't-validate,
  CLAUDE.md). Add tests for BOTH a malformed param (clean 422, not 500) AND an omitted
  param (falls back to the deterministic default / virtual empty state, not a `""`-driven
  failure) on both `model_detail` and `model_skill_chart_json`.
- Re-key **all three** artifact-scoped queries in `model_detail` to the same resolved
  artifact, not just the skill-score table: `skill_scores` (`routes/models.py:119-131`),
  the skill **chart** fetch (via the template's `station_id=`/`group_id=` query param),
  AND `skill_diagrams` (`routes/models.py:134-146`), which today filters on the
  first-active `active_artifact_id` (`routes/models.py:113-117`). All three must
  reflect the selected scope's artifact or the embedded diagrams will silently show a
  different station's/group's data than the table.
- **Pin ONE display contract for freshness + computation_version — same artifact is not
  enough.** Even for a single artifact the three views currently disagree: the table
  query (`routes/models.py:119-131`) selects the artifact's skill rows with **no**
  `freshness` filter, so it returns both `current` and `stale` rows (`skill_scores` has a
  `freshness` CHECK `IN ('current','stale')`, `db/metadata.py:901-903`) and multiple
  `computation_version`s (`db/metadata.py:969`); the chart endpoint already filters
  `freshness == 'current'` (`routes/models.py:206`); the diagrams query
  (`routes/models.py:134-146`) filters neither, so it returns **all**
  `computation_version`s (`skill_diagrams.computation_version`, `db/metadata.py:933`).
  After a recompute/staling, the table/diagrams can show rows the chart does not. Fix the
  contract so all three show the SAME set: the skill-score **table** must also filter
  `freshness == 'current'` and the **latest** `computation_version`; the **chart**
  endpoint (`model_skill_chart_json`, `routes/models.py:195-206`) today filters ONLY
  `freshness == 'current'` — it must ALSO filter the **latest** `computation_version`,
  because `skill_scores` can legally hold two `current` rows at different
  `computation_version`s for one artifact (so a current-only chart still shows both while
  the latest-only table shows one); the **diagrams** must filter the latest
  `computation_version` (diagrams have no freshness column — latest version is the
  "current" analogue). Apply the identical "latest `computation_version`" resolution
  (e.g. a correlated subquery / window over the same scope) in all three so they cannot
  diverge. Regression test: seed one artifact with (i) a `stale` and a `current` skill row
  AND (ii) **two `current` rows at different `computation_version`s** AND two
  `computation_version`s of a diagram; assert table, chart, and diagrams all render the
  single current/latest set — the stale row and the older current-version row appear in
  none of them.
- Resolve station/group labels for the selector by joining `station_id` →
  station code (or `group_id` → group name); do not surface raw UUIDs alone.
- **Fix the adjacent broken artifact-table columns while here.** The artifacts table on
  this same page renders `a.training_start` / `a.training_end`
  (`models/detail.html:37`), but those columns do not exist — the real columns are
  `training_period_start` / `training_period_end` (`db/metadata.py:475-476`), so the
  training-window cell renders **blank today**. Task 2A already edits this template/route;
  correct the two field references (a one-line template fix). Assert the training window
  renders non-empty for a seeded artifact.
  - **Same class of bug: `model.time_step`.** `models/detail.html:10` and
    `models/list.html:22` render `model.time_step` / `m.time_step`, but `time_step` is
    **not** a column on `models` (`db/metadata.py:427`) — it exists only on
    `model_artifacts` (so `a.time_step` at `detail.html:61` is fine). The model-level
    Time-Step cells render blank today. Fix by removing the model-level time-step from
    those two templates (or deriving it from the artifacts/assignments if a value is
    genuinely wanted). Assert the model list/detail render no blank time-step cell.
- **Fix the adjacent "Station Assignments" block for GROUP-scope models.** The
  assignments section on this same page queries only `model_assignments`
  (`routes/models.py:96-110`, rendered at `models/detail.html:49-66`) and never
  `group_model_assignments` (`db/metadata.py:543-561`), so a GROUP-scope model shows an
  empty/absent assignments block regardless of how many `station_groups` it is assigned
  to — inconsistent with Task 2A now making the page GROUP-aware for the skill selector.
  For a GROUP-scope model, also query `group_model_assignments` (joined to
  `station_groups` for the group name) and render those assignments (group name +
  priority), mirroring the existing STATION assignments table. Reuse the model's scope
  (STATION vs GROUP) to decide which assignment source to show. Add a regression
  assertion that a GROUP-scope model's detail page lists its group assignments (not an
  empty block).
- In `model_skill_chart_json`, replace the unordered `.limit(1)`
  (`routes/models.py:187`) with the compound model-scoped resolve on the supplied
  `station_id`/`group_id` (`WHERE model_id AND (station_id OR group_id) AND
  status='active'`), returning 404 when no active artifact matches; when the scope
  param is omitted, fall back to a deterministic newest-first-by-`created_at`
  (tie-broken by id) active artifact for the model.
- **Remove the pre-existing `artifact_id` query param from `model_skill_chart_json`.**
  This is not hypothetical: the endpoint *already ships* a client-supplied
  `artifact_id: str = Query("")` (`routes/models.py:165`) used verbatim in
  `WHERE skill_scores.model_artifact_id == artifact_id` (`routes/models.py:206`) with
  **no** check that the artifact belongs to `model_id` — so today
  `/api/v1/models/<model_A>/skill-chart.json?artifact_id=<artifact-of-model_B>` already
  serves model B's rows mislabeled as model A's (a live, untested cross-model leak; no
  test references this endpoint at all). The "leak closed by construction" claim above
  is only true once this param is gone. Since `station_id`/`group_id` are now the
  sanctioned scope keys, **delete the `artifact_id` param entirely** rather than leaving
  it as a second, unguarded resolution path alongside the new scoped one. (If a future
  caller genuinely needs artifact-level addressing, it must carry the same compound
  `WHERE model_id AND model_artifact_id == artifact_id` ownership join — but no such
  caller exists today, so remove it now.)
- **Cross-model-leak regression test:** with the `artifact_id` param removed from the
  endpoint signature, FastAPI silently *ignores* an unknown `?artifact_id=` (it does not
  400 by default), so do NOT assert a rejection. Assert instead that
  `skill-chart.json?artifact_id=<an artifact of a DIFFERENT model>` can **no longer
  select that other model's rows** — it resolves via the scope path (or the deterministic
  default) for the path's `model_id`, never the foreign artifact — and that
  `skill-chart.json?station_id=<a station assigned to a DIFFERENT model>` 404s. Together
  these prove the pre-existing leak is closed regardless of the ignored param.
- **STATION-scope regression test** (must encode the multi-station scenario, since
  the partial unique index `ix_model_artifacts_station_model_active`
  (`db/metadata.py:493-513`) forbids two active rows for the same station+model — a
  single-station test *cannot* reproduce this): seed two active artifacts for one model
  on two different stations with different skill-row counts. Assert (a) the HTML fetch
  URL and the JSON endpoint target the *same* artifact for the selected station,
  (b) the rendered skill **diagrams** correspond to the selected station's artifact
  (not the first-active one) when two active artifacts exist, (c) the page surfaces the
  existence of BOTH active artifacts (a selector option per active artifact, labelled
  by station code, not raw UUID) so the second station's data is not silently dropped,
  and (d) requesting the page with `?station_id=<second station's id>` re-renders the
  skill-score table, the skill diagrams, AND the chart-fetch URL all pinned to that
  second station's artifact — i.e. switching moves all three together, not just the
  chart.
- **GROUP-scope regression test** (the identical structural bug applies to GROUP-scope
  models: `group_model_assignments` (`db/metadata.py:543-561`) has no uniqueness
  constraint on `model_id` alone, so one GROUP-scope model can be assigned to multiple
  `station_groups`, and the partial unique index
  `ix_model_artifacts_group_model_active` (`db/metadata.py:504-513`) permits one active
  artifact per `(group_id, model_id)` — i.e. N simultaneously-active artifacts for one
  model, the same shape as the STATION case): mirror the STATION test but seed two
  active artifacts for one GROUP-scope model on two different `station_groups` with
  different skill-row counts. Assert the selector lists **both** group options
  (labelled by group name, not raw UUID — exercising the `group_id` → group-name label
  resolution path), and that requesting the page with `?group_id=<second group's id>`
  re-pins table, diagrams, AND chart-fetch URL to the selected group's artifact.
- **Model-scoped-resolve regression test** (replaces the earlier cross-model
  ownership-check test): request `model_detail` with
  `?station_id=<a station NOT assigned to this model>` (i.e. a scope with no active
  artifact for this `model_id`) and assert the HTML route **404s** rather than
  rendering another model's / no artifact's data; request
  `skill-chart.json?station_id=<same unassigned/foreign station>` and assert it too
  **404s**. This proves the compound `WHERE model_id AND (station_id OR group_id) AND
  status='active'` closes the cross-model leak on **both** entry points by
  construction.

**Verification**:

```bash
uv run pytest tests/integration/api/test_dashboard*.py tests/unit/api/
```

Manual smoke:

```bash
curl -sS 'http://127.0.0.1:8010/models/climatology_fallback/' | rg 'skill-chart.json\\?station_id='
curl -sS 'http://127.0.0.1:8010/api/v1/models/climatology_fallback/skill-chart.json?station_id=d2667187-66b9-4c4a-9d1d-21a658120ed2' | jq '.series | length'
```

#### Task 2B — Historical-aware station chart defaults

**Constraint that reshapes this task.** The station page has a *single* shared pair
of date inputs `#start-date`/`#end-date` (`stations/detail.html:140-145`). They feed
one `loadCharts()` which computes one `start`/`end` pair
(`stations/detail.html:217-235`) and reuses it for the observations fetch (`:242`),
the forcing fetch (`:300-301`), and the hindcasts fetch (`:328`). Two inputs cannot
simultaneously equal "today − 30 days" (observations) and a 2020-era historical
window (forcing/hindcasts) — it is one value, not two. Plan 102 does **not** relax
this: it introduces no second date-range widget, so `loadForecastCharts()`
(`docs/plans/102-...:203-206`) continues to close over the same shared `start`/`end`
that `loadObsCharts()` reads (`docs/plans/102-...:177`). So the original
"keep obs at 30 days but default forcing/hindcast to their historical window" is not
achievable by setting these two inputs — it either needs a second, independent
date-range widget (a template/UI change the plan never scoped, plus reconciling with
the single Reload button) or a different approach.

**Chosen approach — a static empty-state MESSAGE; no re-query, no cap change, no DB
change.** Keep ONE shared date range. When a forcing or hindcast chart has no data
points for the selected range, render a per-chart empty-state message that names the
available historical span, e.g. *"No forcing data in the selected range. Historical data
available: 1981-01-01 – 2020-12-31. Adjust the date range to view it."* The operator uses
the existing `#start-date`/`#end-date` inputs (which already work) to navigate there.
There is **no** automatic window swap, **no** "show full history" re-query button, and
therefore **no** widening of any endpoint range, **no** index migration, and **no**
endpoint/DB change of any kind.

**Why this is cut down from the earlier "show full history" re-query (and Task 2C).**
Adversarial review showed the re-query approach quietly dragged in relaxing the endpoint's
25-year cap, an Alembic index migration, a forcing version/source dedup policy
(`forcing.json` bypasses `PgHistoricalForcingStore`'s latest-version collapse), and
40-year observation serialization inside `hindcasts.json` — a large, risky feature riding
under a cosmetic goal ("charts shouldn't look mysteriously empty"). A message that states
*what data exists and how to reach it* delivers essentially all the operator value at a
fraction of the cost and risk. If operators later ask to *browse* arbitrary historical
windows, that becomes its own scoped plan with the endpoint/index/versioning work done
properly.

> Note (rejected alternatives, all heavier for the same cosmetic value): (1) a second
> independent date-range widget; (2) a silent auto-fallback that swaps the window on empty;
> (3) the explicit "show full history" re-query control (the version this plan carried
> through round 4, **cut in round 5** after review showed its cost — cap relaxation + index
> migration + forcing-version policy + observation serialization — was disproportionate to
> a cosmetic fix).

**Scope**: Stop the station-detail forcing and hindcast charts from *looking dead-empty*
when the shared date default (current-time) misses their historical data, by rendering a
per-chart empty-state message that states the available historical span and points the
operator at the existing date inputs. No re-query, no new/changed endpoint, no DB change.

**Out of scope**: Any re-query or automatic window change; a second/independent date-range
widget; relaxing the endpoint range cap or adding indexes (both cut along with the former
Task 2C); the multi-parameter observation layout from Plan 102; new data endpoints; and
changing any existing endpoint contract.

**Implementation notes**:
- If Plan 102 is implemented first, add the empty-state message inside whichever post-102
  function owns the forcing/hindcast fetches (`loadForecastCharts()`), leaving
  `loadObsCharts()` and the shared date inputs untouched.
- **Server-side context = a bounded aggregate, NOT a data re-query. Use the RIGHT column
  per table:**
  - **Forcing span:** `SELECT min(valid_time), max(valid_time) FROM historical_forcing
    WHERE station_id = :sid` — `historical_forcing` has `valid_time` (`db/metadata.py:373`).
    One span per station is fine for the forcing chart (it plots all parameters in one
    combined fetch — `forcing.json` has no `parameter=` filter).
  - **Hindcast span:** `SELECT min(hindcast_step), max(hindcast_step) FROM
    hindcast_forecasts WHERE station_id = :sid AND parameter = :param` — `hindcast_forecasts`
    has **`hindcast_step`, NOT `valid_time`** (`db/metadata.py:729`; `valid_time` lives on
    `hindcast_values`). And it MUST be **keyed by `parameter`**: `hindcasts.json` filters by
    parameter (`routes/stations.py:528,563`), so a station-level span would wrongly tell a
    user viewing `temperature` that historical hindcasts exist when only `discharge` has
    them. Provide a per-parameter hindcast span map; the client picks the span for the
    currently-selected parameter.
  - **Cost — describe accurately, do NOT claim index-optimal.** These are bounded per-station
    (hindcast: per-station-per-parameter) aggregates — one station's rows, computed once at
    page render, and crucially **not** a widened data fetch to the client (that is what was
    cut). But they do NOT ride the existing indexes as a direct min/max: the forcing index is
    `(station_id, source, valid_time)` and the hindcast index leads
    `(station_id, model_id, hindcast_step, …)`, so without constraining `source`/`model_id`
    Postgres scans the station's rows across intervening key values rather than doing an
    index-endpoint min/max. Accept that station-bounded scan as cheap enough for a single
    page render; do not assert it is index-cheap. (If page-render latency ever proves a
    problem, a covering index is a deliberate FUTURE follow-up — explicitly not now, to keep
    this pass migration-free.) Unit-test the aggregate (correct min/max on the right column;
    per-parameter hindcast span incl. "rows for another parameter only → no span for the
    selected parameter"; absent → no span).
- **Define the "empty for this range" predicate precisely against the ACTUAL response
  shapes** — a naive check silently fails here:
  - Forcing is empty iff the total data-point count is zero —
    `sum(series[*].timestamps.length) === 0` — NOT `Object.keys(data.series).length === 0`
    (the object can be present with empty arrays).
  - Hindcast is empty iff there are zero hindcast data points (count `hindcast_steps` /
    hindcast values) — NOT Plotly `traces.length`, because the JS adds an **observed** trace
    first (`stations/detail.html:331`), so `traces.length > 0` even when the range has
    observations but no hindcast values. Exclude the observed trace from the emptiness test.
- **Render the message via the EXISTING per-chart Plotly-title empty-state pattern — do NOT
  add new sibling containers.** The code already renders no-data as each chart's own Plotly
  title: `plot("forcing-chart", [], {title: {text: "No forcing data for this range"}})`
  (`stations/detail.html:317`) and the hindcast equivalent (`:361`). Enhance those existing
  title texts to include the historical span (below). This pattern is inherently **per-chart**
  (forcing and hindcast titles are independent — no shared `#chart-error` clobber, since the
  no-data path never touches that div) and **auto-clears** on the next `plot()` with data, so
  there is no separate empty-state element lifecycle to manage. (This supersedes the earlier
  sibling-container idea, which would have needed manual clear-on-load/clear-on-data handling
  and could leave stale text when the user navigates to a populated range.)
- The message distinguishes: (i) no historical data exists at all for this chart/parameter
  (span absent → "No data available"), vs. (ii) data exists outside the selected range (span
  present → "No data in selected range. Historical data available: `<min>` – `<max>`. Adjust
  the date range to view it.").
- **Add the `_loadSeq` stale-guard to the forcing and hindcast callbacks** (this is required
  even though there is no re-query). Today only the observations callback checks
  `if (_loadSeq !== seq) return;` (`stations/detail.html:245`); the forcing (`:302-320`) and
  hindcast (`:329-364`) `.then()` callbacks write their charts with **no** stale check. Since
  the date inputs fire `loadCharts()` on both `onchange` and `oninput`
  (`stations/detail.html:140-145`), a user changing the range quickly can have an *older,
  empty-range* response land after a newer populated one and overwrite the chart with the
  "No data in selected range" title. Capture `const seq = ++_loadSeq` per load and bail on
  `_loadSeq !== seq` before ANY `plot()` / title write in both callbacks. (The earlier claim
  that `_loadSeq` was "untouched" was wrong — the message write is subject to the same race
  as any chart write in these already-unguarded callbacks.)

**Verification**:

```bash
uv run pytest tests/integration/api/test_dashboard*.py
```

Manual smoke:

```bash
curl -sS 'http://127.0.0.1:8010/api/v1/stations/d2667187-66b9-4c4a-9d1d-21a658120ed2/forcing.json?start=2020-12-01T00:00:00&end=2020-12-15T23:59:59' | jq '.series | keys'
curl -sS 'http://127.0.0.1:8010/api/v1/stations/d2667187-66b9-4c4a-9d1d-21a658120ed2/hindcasts.json?parameter=discharge&start=2020-12-01T00:00:00&end=2020-12-15T23:59:59' | jq '.hindcast_steps | length'
```

### Phase 3 — Regression coverage and documentation

#### Task 3A — Cross-link and empty-state regression tests

**Scope**: Add focused dashboard tests that prevent recurrence of this investigation's
NEW findings:
- Prefect links render as browser-safe URLs (`http://localhost:4200`) — from the
  single corrected base `PREFECT_UI_URL` (`docker-compose.yml:172`), which every overlay
  *inherits* (no per-overlay override exists). Assert the merged/effective `api` service
  env resolves `PREFECT_UI_URL=http://localhost:4200` under both the dev and Mac-mini
  overlays; do NOT test for an overlay-level `PREFECT_UI_URL` key, which Task 1A
  deliberately does not add.
- **Compose-binding guard (loopback invariant) — cover the EFFECTIVE surface, not just
  overlay files.** Mirror the existing precedent `tests/unit/test_compose_schedule_default.py`
  (which `yaml.safe_load`s a compose file and asserts a config value cannot silently
  regress). But per-overlay-file `yaml.safe_load` alone is insufficient: if a future edit
  adds `ports: ["4200:4200"]` to the **base** `docker-compose.yml`, the overlay-file tests
  still pass while the merged/effective compose LAN-exposes Prefect. So the guard must:
  (a) assert the **base** `docker-compose.yml` `prefect-server` has **no `ports` key** at
  all (it relies on the overlays to publish, loopback-bound); (b) assert the
  effective/merged ports for each overlay — ideally by shelling
  `docker compose -f docker-compose.yml -f <overlay> config` and parsing the resolved
  `ports`, or if that's unavailable in CI, by asserting the overlay files publish the
  **overlay-specific expected bindings** (below), with the base carrying no port publish.
  The expected loopback bindings are **NOT uniform across overlays** — the host port
  differs by overlay (dev offsets to avoid local conflicts):
  - **dev** (`docker-compose.dev.yml`): Prefect `127.0.0.1:4200:4200`, API
    `127.0.0.1:8010:8000` (dev deliberately offsets the host API port to `8010`;
    Task 1A's dev smoke curls `127.0.0.1:8010`). Loopback-bound per the dev-API decision.
  - **Mac-mini** (`docker-compose.macmini.yml`): Prefect `127.0.0.1:4200:4200`, API
    `127.0.0.1:8000:8000`.
  The guard must FAIL on a bare `4200:4200` / `8000:8000` / `8010:8000` (all-interfaces) or
  any non-loopback IP in base OR either overlay, so no future edit can quietly LAN-expose
  Prefect or the API. Do NOT assert a single `8000` API port for both overlays (dev is
  `8010`). Distinct from the rendered-link test above, which checks HTML, not bindings.
- Station basin card links to a real basin page.
- **STATION scope**: model skill chart fetch includes the selected `station_id`, AND
  for a model with two active artifacts on two different stations the page surfaces
  both (no silent drop) and the embedded skill diagrams track the selected station's
  artifact — see Task 2A's STATION-scope regression test.
- **GROUP scope**: for a GROUP-scope model with two active artifacts on two different
  `station_groups`, the selector lists both group options (labelled by group name, not
  raw UUID) and switching via `?group_id=` re-pins table, diagrams, and chart to the
  selected group's artifact — see Task 2A's GROUP-scope regression test. This exercises
  the same multi-active-artifact shape the STATION test covers but through
  `ix_model_artifacts_group_model_active` (`db/metadata.py:504-513`).
- Both `model_detail` (HTML route) AND `skill-chart.json` (JSON endpoint) return
  404 for a `?station_id=`/`?group_id=` that has no active artifact for this
  `model_id` (foreign/unassigned scope), proving the compound model-scoped resolve
  closes cross-model leakage on **both** entry points — see Task 2A's
  model-scoped-resolve regression test.
- Virtual models with no artifacts remain valid empty pages, not errors.
- Station forcing/hindcast empty-state (Task 2B): unit-test the **available-historical-span
  aggregate** — `min(valid_time)/max(valid_time)` over `historical_forcing` per station, and
  `min(hindcast_step)/max(hindcast_step)` over `hindcast_forecasts` per station **and
  parameter** (NOT `valid_time`, which is not a column on `hindcast_forecasts`). Assert:
  correct min/max when rows exist; an absent span (→ "No data available") when none exist;
  and the parameter-scoping case — a station with hindcast rows for `discharge` only returns
  **no** span for a `temperature` chart. The DOM glue (emptiness predicate on a fetch result,
  enhanced Plotly-title message, `_loadSeq` guard) is a documented manual smoke, not
  automated (no JS test runner in the repo; browser automation out of scope). No re-query,
  control, or endpoint-range assertions — those were cut with the former Task 2C.

**Out of scope**: Full browser automation or Plotly rendering tests.

**Verification**:

```bash
uv run pytest tests/integration/api/test_dashboard*.py tests/unit/api/
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run pyright src/
```

#### Task 3B — Dashboard ops notes

**Scope**: Update affected dashboard/operator documentation with the resolved
browser-facing Prefect URL behavior and the intended meaning of empty historical
charts. Must explicitly cover the **Mac-mini overlay** (`docker-compose.macmini.yml`):
document the Task 1A resolution — the loopback-bound `127.0.0.1:4200:4200` publish +
the inherited base `PREFECT_UI_URL=http://localhost:4200` (no overlay override),
reached through the operator's `LocalForward 4200 localhost:4200` SSH tunnel — so
operators on the live-operated host have a concrete, working procedure. Reconcile
`docs/deployment/mac-mini-staging.md:263-295` (which already documents the tunnel but
against a port that was not published) so its tunnel instructions now match a port
that actually listens on the host loopback. Keep the internal-only policy statement
consistent with `security.md:485-491` — the tunnel remains the *only* access path;
no LAN exposure is added.

**Update `security.md` itself.** `security.md:485-491` currently states Prefect
(4200) is *"Internal only (Docker network, not exposed to host)"*, which becomes
literally false once Task 1A publishes `127.0.0.1:4200:4200` — the port now listens
on the host loopback. Edit that bullet so the authoritative security standard matches
the shipped compose change: e.g. *"loopback-only on the operated host
(`127.0.0.1:4200`), reached via SSH `LocalForward`; never LAN-exposed."* Do not leave
`security.md` reading as contradicted by the deployed configuration.

**Also reconcile the FastAPI (8000) bullet in the SAME section.** `security.md:485-491`
lists FastAPI 8000 as internal-only, but the Mac-mini overlay published it bare
`8000:8000` (LAN-exposed) — a pre-existing contradiction this plan must not leave standing
while editing this very section. Once Task 1A loopback-binds the API to
`127.0.0.1:8000:8000` (pending IT sign-off), update the 8000 bullet to match: loopback-only
on the operated host, reached via the SSH tunnel / Caddy; never LAN-exposed. If the IT
sign-off defers the API re-bind, then instead state the 8000 status *honestly* (currently
LAN-published, flagged for follow-up) rather than asserting internal-only — the standard
must describe reality either way.

**Out of scope**: Rewriting the full API docs or broader deployment docs.

**Verification**:

```bash
uv run pytest tests/integration/api/test_dashboard*.py
```

## Dependency Graph

```json
{
  "phases": [
    {
      "id": "phase-1-links",
      "tasks": ["1A", "1B"],
      "parallel": true
    },
    {
      "id": "phase-2-chart-correctness",
      "tasks": ["2A", "2B"],
      "parallel": true,
      "depends_on": ["phase-1-links"],
      "notes": "2A (model skill selector) and 2B (station empty-state message) are independent — different routes/templates, no shared code. The former Task 2C (endpoint range-cap relaxation + index migration) was cut in round 5."
    },
    {
      "id": "phase-3-tests-docs",
      "tasks": ["3A", "3B"],
      "parallel": false,
      "depends_on": ["phase-2-chart-correctness"]
    }
  ]
}
```

## Self-review

- Scope excludes known Plan 100/101/102 issues.
- Each task has explicit scope, out-of-scope, and verification commands.
- The only station-detail overlap is Task 2B; it explicitly waits for or builds on
  Plan 102 to avoid competing rewrites of the same JS. Task 2B works with the single
  shared date-input pair (`stations/detail.html:140-145`) via a static per-chart
  empty-state message (naming the available historical span) — no re-query, no window
  swap, no second date-range widget, no endpoint/DB change.
- Task 1A fixes the browser-visible Prefect URL **at its source** — a single
  `docker-compose.yml:172` change from `http://prefect-server:4200` to
  `http://localhost:4200` (already matching the `api/__init__.py:16` fallback) — rather
  than scattering duplicate per-overlay `PREFECT_UI_URL` overrides, which would
  reproduce the failure class that caused this bug. Only the environment-specific
  *port-publish* line differs: the Mac-mini overlay adds a **loopback-bound**
  `127.0.0.1:4200:4200` publish (never LAN), keeping it consistent with the
  already-decided internal-only/SSH-tunnel-only policy in `security.md:485-491` and
  `Caddyfile:14-16` — making the existing (but currently non-functional) tunnel path
  actually work rather than opening a new network surface.
- Task 2A keys the artifact selector by `station_id`/`group_id`, not by raw
  `artifact_id`, so cross-model artifact references are structurally unrepresentable
  (compound `WHERE model_id AND (station_id OR group_id) AND status='active'`) — the
  earlier "validate artifact_id belongs to model_id" ownership check on two endpoints
  is removed as unnecessary (parse, don't validate — CLAUDE.md). It treats the
  skill-chart mismatch as the structural multi-active-artifact issue it is (per-scope
  artifacts, `db/metadata.py:493-513`), covers BOTH the STATION
  (`ix_model_artifacts_station_model_active`) and GROUP
  (`ix_model_artifacts_group_model_active`, `:504-513`) multi-active shapes with
  dedicated regression tests, and asserts no scope's active skill data is silently
  dropped.
- No database schema change and no migration is required (the former Task 2C index
  migration was cut with the re-query it supported). Task 2B's only server-side query is a
  bounded per-station (hindcast: per-station-per-parameter) `min/max` aggregate — one
  station's rows at page render, not a widened data fetch to the client. It is NOT claimed to
  be index-optimal (the existing indexes lead with `source`/`model_id`, so an unconstrained
  min/max scans the station's rows); a covering index is a deliberate future follow-up if
  page-render latency ever warrants it, kept out of this pass to stay migration-free.
- One deliberate scope expansion beyond the four original findings, forced by code-grounded
  security review: Task 1A **loopback-binds every LAN-exposed host port** it touches — dev
  Prefect + dev API (`127.0.0.1:8010:8000`) and the Mac-mini API (`127.0.0.1:8000:8000`) —
  closing pre-existing LAN exposures the plan would otherwise leave contradicting
  `security.md`. (The earlier Task 2C endpoint/index expansion was **cut in round 5**: its
  cost — cap relaxation, index migration, forcing-version policy, observation serialization —
  was disproportionate to Task 2B's cosmetic goal, so Task 2B now shows a static empty-state
  message instead of re-querying.)
- **Sign-off dependency:** the Mac-mini API (8000) re-bind changes network exposure on a
  live-operated host. Decision: it **ships in this PR with IT/security sign-off as a merge
  gate** (IT first confirms no direct LAN client depends on `:8000`), not split out.
- Otherwise the fixes are read-only/user-facing and do not change forecasting, ingestion,
  QC, or model computations; the only runtime-behavior changes are the loopback port
  re-binds (dev + Mac-mini).
