# Plan 096 — dashboard: forecast time-series graph (absolute x-axis + obs overlay)

**Status**: READY (plan-review workflow 2026-07-03 resolved every design decision
against the code — endpoint shape, `issued_at` as the obs-window anchor, Plotly
reuse; no residual human-decision forks. The automated verdict read
"exhausted/not-converged", but the residuals were all *implementation-precision*
majors, not design forks. Two that matter (already covered in Process): the
per-series **init dicts must add `"valid_times": []`** at `forecasts.py:155/:165`
before the `valid_times` append (else `KeyError`), and the Phase-3 obs-fetch lives
inside the existing `{% if values %}` guard (`detail.html:20`) so it only fires
when `forecast_values` rows exist. Implementation watch-outs: use a
`def _override(): yield db_connection` wrapper for the `get_connection` override,
seed fixtures with `lead_time_hours >= 1`, and treat the template smoke-test as a
render check only — endpoint correctness is covered by the response assertions.)
**Priority**: medium — operator-requested; makes the NWP-on forecasts legible at
a glance (the whole point of collecting them).
**Phase**: v0b — review dashboard (Flow 3 adjacent)
**Parent**: the developer dashboard (`api/routes/`, `api/templates/`)
**Related**: `api/routes/forecasts.py`
(`forecast_detail` at `forecasts.py:68`, `forecast_data_json` at `forecasts.py:117`,
route param is `{forecast_id}`), `api/templates/forecasts/detail.html`
(existing ensemble chart, lines 20-91), `api/routes/stations.py`
(`station_observations_json` at `stations.py:388`),
`api/templates/stations/detail.html` (obs overlay fetch pattern),
`api/templates/base.html` (Plotly + htmx already loaded),
`db/metadata.py` (`forecast_values.valid_time` at `metadata.py:676`,
`forecasts.units` at `metadata.py:658`)
**Created**: 2026-07-03

---

## Request

The forecast detail page (`/forecasts/{forecast_id}/`) already has an ensemble
Plotly chart, but its x-axis is **relative lead time (hours)**, not absolute
calendar time, and there is **no observed-discharge context**. The operator wants
forecasts to read as a proper **time-series graph on an absolute x-axis**, with
recent observations flowing into the forecast.

## What already exists (corrected — the chart is already built)

- **Plotly is already loaded** (`base.html:9`, `plotly-2.35.2.min.js`) and htmx
  (`base.html:8`). No new frontend/build step needed.
- **The forecast detail page ALREADY renders an ensemble Plotly chart.**
  `templates/forecasts/detail.html:20-91` contains `<div id="ensemble-chart">`
  (line 22) and a `<script>` block (lines 49-88) that fetches
  `/api/v1/forecasts/{{ forecast.id }}/data.json`, then calls `Plotly.newPlot`
  with either a spaghetti trace per member (lines 54-66) or one line per quantile
  (lines 67-80). **This plan modifies that existing block — it does NOT add a new
  chart div or a second script block.** (Reviewer finding: the earlier "add a
  Plotly chart block" framing was wrong; the block exists.)
- **The remaining gaps in that existing chart are:**
  1. x-axis is `series.lead_times` (integer hours) — see `detail.html:59` and
     `:73`, with `xaxis: {title: "Lead time (hours)"}` at `:83`. We want an
     **absolute `valid_time` (UTC) date axis**.
  2. **No observation overlay** — the last observed discharge is not drawn, so
     the forecast has no anchor.
  3. **No units** on the y-axis title — it currently uses the raw parameter name
     (`detail.html:84`).
- **The data endpoint exists**: `forecast_data_json` (`forecasts.py:117`) returns
  values grouped by `member` (spaghetti) or `quantile`, each with `lead_times`
  and `values` arrays. It does **not** currently emit `valid_time`, `units`, or
  `issued_at`, even though all three are available in the queried rows / forecast
  metadata row (see "Endpoint shape" below).
- **The observations endpoint exists** and is directly reusable:
  `station_observations_json` (`stations.py:388`) accepts `parameter` +
  ISO `start`/`end` (both **mandatory** — `stations.py:391-393`) and returns
  `{timestamps, values, qc_statuses}` with absolute ISO timestamps
  (`stations.py:430`). The station detail page already calls this endpoint; the
  fetch pattern there is the one to mirror for the overlay.

## Goal

The **existing** forecast-detail ensemble chart is upgraded to a true
time-series graph: **x = absolute `valid_time` (UTC)**, **y = forecast value with
its `units`**, ensemble spread + central line, **with recent observed discharge
overlaid up to issue time** so the forecast reads in context.

## Resolved design decisions (previously "open questions")

1. **Ensemble rendering — KEEP AS-IS.** The existing chart already branches on
   `data.representation`: spaghetti for `members` (`detail.html:54-66`),
   one dashed line per quantile with a solid median for `quantiles`
   (`detail.html:67-80`). This plan does **not** change the rendering mode; it
   only changes the x-axis and adds an obs trace. (Quantile-band shading is a
   possible follow-on, out of scope here.)

2. **Placement — forecast detail page only for this plan.**
   Scope is `/forecasts/{forecast_id}/` (the page that already has the chart).
   The station-page overlay (drawing the *latest* forecast onto the station's
   obs time series) is **deferred to a follow-on plan** because the station
   detail route (`stations.py:165-385`) passes **no forecast context** to its
   template — it would need either a new `latest_forecast_id`/`issued_at`/
   `parameter` context variable or a two-step JS fetch (GET
   `/api/v1/stations/{id}/forecasts` → then `data.json`). Not worth coupling into
   this plan. (Reviewer finding acknowledged.)

3. **Observation overlay — RESOLVED (no open question).**
   The overlay fetches
   `/api/v1/stations/{{ forecast.station_id }}/observations.json?parameter={{ forecast.parameter }}&start=<issued_at − N days>&end=<issued_at>`.
   - `forecast.station_id` and `forecast.parameter` are in the detail template
     context (`detail.html:9`, `:11`).
   - `start`/`end` are computed client-side from the **authoritative `issued_at`**
     now emitted at the top level of `data.json` (see Endpoint shape / design
     decision 6). The obs window is exactly `[issued_at − N days, issued_at)`.
   - Window `N` defaults to **7 days** (config as a JS constant; tune in grill-me).
   - **Join key = `parameter` string — VERIFIED, gate closed.** Both
     `observations.parameter` (`stations.py:417`) and `forecasts.parameter`
     (`metadata.py:657`) are free-text columns, and the v0 ingest + write paths
     use the identical bare literal `"discharge"`:
     `camelsch_adapter.py:63` (`parameter="discharge"`),
     `hydro_scraper.py:47` (BAFU `discharge` → `"discharge"`),
     `linear_regression_daily.py:182` (`parameter="discharge"`), and the type
     system pins `ForecastParameter = Literal["discharge", "water_level"]`
     (`domain.py:215`). The strings match on both sides, so the join is confirmed
     for discharge — this is **no longer a DRAFT→READY gate**. Adding a
     non-discharge parameter (e.g. `water_level`) is a follow-on concern: it would
     need the same match re-checked, and if the strings ever diverge a mapping
     layer is required (flag, do not silently overlay mismatched series).

4. **Units — RESOLVED.** Emit `units` from the forecast row (already fetched at
   `forecasts.py:129-134`; column at `metadata.py:658`) in `data.json`, use it as
   the y-axis title. The observations endpoint returns **no units**
   (`stations.py:428-433`); the overlay therefore assumes the obs series shares
   the forecast's units for the same station+parameter. Add a JS guard: if the
   forecast `units` string is missing/empty, fall back to the parameter name and
   log a `console.warn` rather than mislabeling. (Reviewer finding: units are
   absent from both endpoints — documented here.)

5. **Multi-model — out of scope.** Show only the produced forecast for this
   `forecast_id`. Multi-model overlay stays a follow-on tied to the skill-
   comparison view.

6. **Endpoint shape — RESOLVED: extend `data.json` with per-series `valid_times`
   + top-level `units` + top-level `issued_at`.**
   - `valid_time` is **already in the queried rows**: `forecast_data_json` selects
     `sa.select(fv)` (`forecasts.py:139`), and `forecast_values.valid_time` exists
     (`metadata.py:676`). It is simply not appended to the per-series dict today
     (`forecasts.py:156`, `:166` append only `lead_time_hours` and `value`). Add a
     `valid_times` array (ISO 8601 via `.isoformat()`) alongside `lead_times`. Note
     these are the **per-series** absolute x-values for the traces — they are NOT
     the obs-window anchor (see next bullet and design decision 3-anchor below).
   - `units` and `issued_at` both come from the forecast metadata row already
     fetched at `forecasts.py:129-134`; add **both** to the top-level JSON
     response: `"units": forecast.get("units")` and
     `"issued_at": forecast["issued_at"].isoformat()`
     (`forecasts.units` at `metadata.py:658` is `nullable=False`; `issued_at` at
     `metadata.py:617` is a tz-aware `DateTime`, so `.isoformat()` yields a
     JS-`Date`-parseable string).
   - **Why `issued_at` at the top level is the obs-window anchor (NOT
     `valid_times[0]`)**: the store computes
     `lead = int((vt.timestamp() − issued_at.timestamp()) // 3600)`
     (`forecast_store.py:250`), i.e. `valid_time = issued_at + lead_hours`. For the
     v0 daily linear-regression model the first step is `issued_at + 24h`, and for
     hourly ICON-CH2-EPS it is `issued_at + 1h` — a lead of 0 only if a model emits
     a step at exactly `issued_at`, which the current model does **not**. So
     `valid_times[0]` is 1–24 h *after* issue time, and using it as the obs-window
     `end` would leak post-issue observations into the "historical" trace and
     misrepresent the anchor. `issued_at` is exact, requires no arithmetic, and
     correctly bounds the window to `[issued_at − N days, issued_at)` regardless of
     the model's time step. (Reviewer blocker acknowledged and resolved here.)
   - **Why emit `issued_at` rather than read `{{ forecast.issued_at }}` from the
     template** (a reviewer-proposed alternative): the template renders
     `issued_at` as a raw SQLAlchemy row value (`detail.html:12`,
     `{{ forecast.issued_at }}`) with **no `.isoformat()`**, so its string form is
     not guaranteed to be JS-`Date`-parseable. Emitting it from `data.json` gives
     the JS an authoritative ISO value and keeps the endpoint self-describing for
     the deferred station-overlay use (design decision 2), which would consume
     `data.json` without a template context. Net backend change is ~4 lines
     (`valid_times` in both branches + top-level `units` + top-level `issued_at`).
   - The overlay still needs a **second fetch** to `observations.json` (it is a
     different resource/URL — `stations.py:388`); "one fetch" is not achievable
     without a new bundled endpoint, which is not worth it for the forecast-page
     case. The chart JS computes the obs `start`/`end` window from
     `data.issued_at` (see Phase 3) minus `N` days.

## Known pre-existing bugs (note, do not depend on)

- `detail.html:16` renders `{{ forecast.horizon_hours }}`, but the `forecasts`
  table has **no `horizon_hours` column** (`metadata.py:601-666`) — Jinja renders
  it as empty. **Do NOT reference `forecast.horizon_hours` in the chart JS.**
  Derive the horizon from `max(lead_times)` in the `data.json` payload if needed.
  Fixing the stray template field is out of scope (clean up independently).

## Non-goals

- A production/polished UI or a JS build pipeline (stays server-rendered Jinja +
  Plotly).
- Quantile-band (shaded) rendering — spaghetti/line rendering stays as-is.
- The station-page forecast overlay (deferred, design decision 2).
- Forecast editing/adjustment (Flow 3 review workflow — separate, v2).

## Implementation phases

### Phase 1 — extend `data.json` (backend, ~4 lines)

`forecasts.py`, `forecast_data_json`:
- **First extend the per-series initialization dicts** at BOTH branch sites from
  `{"lead_times": [], "values": []}` to
  `{"lead_times": [], "valid_times": [], "values": []}` — the quantiles init at
  `forecasts.py:155` (`quantiles[q] = {...}`) and the members init at
  `forecasts.py:165` (`members[m] = {...}`). Skipping this is a **runtime
  `KeyError`** on the first row of each new series, because the append below writes
  to a key that must already exist. (Reviewer blocker: init sites omitted.)
- **Then** append `r["valid_time"].isoformat()` to the now-initialized
  `valid_times` list alongside the existing `lead_times`/`values` appends in
  **both** the quantiles branch (`forecasts.py:156-157`) and the members branch
  (`forecasts.py:166-167`).
- Add `"units": forecast.get("units")` to both returned JSON payloads
  (`forecasts.py:158`, `:168`). `forecast` row is already fetched at
  `forecasts.py:129-134`; `units` is `nullable=False` (`metadata.py:658`).
- Add `"issued_at": forecast["issued_at"].isoformat()` to both returned JSON
  payloads (same two return sites). `issued_at` is `nullable=False` and tz-aware
  (`metadata.py:617`), so `.isoformat()` yields a JS-`Date`-parseable string. This
  is the authoritative obs-window anchor consumed in Phase 3.
- Preserve the two empty-forecast early returns (`forecasts.py:126`, `:135`) —
  keep them returning `{"lead_times": [], "members": {}}` (unknown/empty
  `forecast_id` path). These early returns carry **no** `issued_at`. Note the
  null-guard on `data.issued_at` in Phase 3 is **defensive, not currently on the
  live template path**: the chart `<script>` block is wrapped in `{% if values %}`
  (`detail.html:20`), and `values` is populated by the same `forecast_detail`
  route (`forecasts.py:93-104`) that already 404s on an unknown `forecast_id`
  before the page renders. So from the running template `data.issued_at` is always
  present; the guard protects future `data.json` consumers (e.g. the deferred
  station-overlay, design decision 2) and any refactor that lifts the script out of
  `{% if values %}`. (Reviewer finding: the "early returns omit it" rationale is
  not reachable from the template today — clarified here.)

### Phase 2 — upgrade the existing chart script (template, no new div/block)

`templates/forecasts/detail.html:49-88`, inside the existing `<script>`:
- Change both trace builders to use `x: series.valid_times` instead of
  `series.lead_times` (`detail.html:59`, `:73`).
- Set the layout `xaxis` to `{type: "date", title: "Valid time (UTC)"}`
  (replacing `detail.html:83`).
- Set `yaxis.title` to `data.units || "{{ forecast.parameter }}"` (replacing the
  bare parameter at `detail.html:84`); `console.warn` if `data.units` is falsy.

### Phase 3 — observation overlay (template, second fetch)

**Template-guard dependency (read first).** The entire chart `<script>` block —
and therefore the obs fetch — lives inside the existing `{% if values %}` guard
(`detail.html:20`, closed at `:89`). `values` is the template context list
populated by `forecast_detail` (`forecasts.py:93-104`). Consequences:
- The obs fetch only fires when `forecast_values` rows exist for the forecast. A
  `forecasts` row with **zero** `forecast_values` rows renders no script at all
  (the block is suppressed), so there is nothing to overlay — the template guard,
  not the `data.issued_at` null-guard, is the real safety net for the no-values
  case. The `data.issued_at` null-guard is belt-and-suspenders for if the script is
  ever moved out of `{% if values %}`.
- Because the obs URL embeds server-side `{{ forecast.station_id }}` /
  `{{ forecast.parameter }}`, the fetch is well-formed whenever the script renders.

Still inside the same `detail.html` script, after the forecast traces are built.
**Critical structural requirement:** `Plotly.newPlot` must move **inside** the obs
fetch's `.then()` callback so it fires only after the obs response resolves — the
existing code ends the outer `.then(data => {...})` with `Plotly.newPlot` at
`detail.html:81`, and simply appending a second async `fetch(...)` there would race
(the chart would render before the obs arrive, silently dropping the overlay).
Restructure the outer callback to:

1. **Anchor the obs window on `data.issued_at`** (the top-level field added in
   Phase 1 — NOT `valid_times[0]`, which is 1–24 h after issue time; see design
   decision 6). Null-guard it first, because the empty-forecast early returns omit
   it:
   ```
   const anchor = data.issued_at ? Date.parse(data.issued_at) : NaN;
   ```
   If `anchor` is `NaN` (missing/unparseable), skip the obs fetch entirely and call
   `Plotly.newPlot` with the forecast traces only.
2. Otherwise compute the window:
   `end = new Date(anchor).toISOString();`
   `start = new Date(anchor − N*86400000).toISOString();` (`N = 7` days as a JS
   `const`).
3. Fetch
   `/api/v1/stations/{{ forecast.station_id | string }}/observations.json?parameter={{ forecast.parameter | urlencode }}&start=${start}&end=${end}`.
   Note `forecast.station_id | string`: the column is `UUID(as_uuid=True)`
   (`metadata.py:606`), so coerce to its canonical string form for the URL —
   consistent with the link href at `detail.html:9` — rather than relying on
   default `__str__` rendering. `station_observations_json` accepts `station_id`
   as a path-string (`stations.py:390`).
4. **Check `r.ok` before `r.json()`** (mirror the station-page pattern at
   `stations/detail.html:243`): on a non-OK response (e.g. HTTP 400 returns
   `{"detail": ...}`, not `{"timestamps": []}` — `stations.py:404-405`), do **not**
   attempt to read `obs.timestamps` — that would throw a `TypeError` swallowed by
   `.catch()`. Show the guard explicitly in the code (non-optional):
   ```
   fetch(url)
     .then(r => { if (!r.ok) return null; return r.json(); })
     .then(obs => {
       if (obs && obs.timestamps && obs.timestamps.length > 0) {
         traces.push({x: obs.timestamps, y: obs.values,
                      mode: "lines+markers", name: "Observed"});
       }
       Plotly.newPlot("ensemble-chart", traces, layout);
     })
     .catch(() => Plotly.newPlot("ensemble-chart", traces, layout));
   ```
5. On success: if `obs.timestamps.length === 0`, skip the obs trace; otherwise push
   `{x: obs.timestamps, y: obs.values, mode: "lines+markers", name: "Observed"}`
   into the `traces` array.
6. Call `Plotly.newPlot("ensemble-chart", traces, layout)` **once**, at the end of
   the obs `.then()` (and in the anchor-`NaN` / `!r.ok` / `.catch()` fallback
   branches) — so the forecast chart always renders even when the overlay is
   unavailable. Add a `.catch()` on the obs fetch that logs and renders
   forecast-only, so an obs failure never aborts the whole script.

## Tests

**Test tier — integration, NOT unit (reviewer blocker resolved).** The dashboard
route `forecast_data_json` calls `get_reflected(conn)` which runs a real
`sa.MetaData().reflect(bind=conn)` (`tables.py:30-36`). The existing unit harness
overrides `get_connection` with a no-op `_DummyConnection` (`conftest.py:29-31`,
`:60`) whose `execute` returns `None` and which has no DBAPI surface for
`MetaData.reflect()` — passing it to the reflected route either crashes or returns
empty payloads, making assertions vacuously pass. Additionally `tables._reflected`
is a **module-level singleton** (`tables.py:24`) that caches the first connection's
schema across tests. Therefore this test lives at the **integration tier**, which
already provides a real reflectable DB.

**Create the test directory first.** `tests/integration/api/` does **not** exist
today (existing integration subdirs are `adapters/`, `db/`, `live/`, `store/`, each
carrying an `__init__.py` — e.g. `tests/integration/adapters/__init__.py`). Add
`tests/integration/api/__init__.py` (empty) mirroring that pattern, then place the
new test at `tests/integration/api/test_dashboard_forecasts.py`. (Alternative: put
it flat at `tests/integration/test_dashboard_forecasts.py` to avoid the new
subdirectory — the subdir is preferred for symmetry with the source layout.)

`tests/integration/api/test_dashboard_forecasts.py` is a **new file — the dashboard
routes in `api/routes/forecasts.py` currently have zero test coverage;
`tests/unit/api/test_api_forecasts.py` covers only the RESTful `api_forecasts.py`
endpoint**. Reuse the existing integration harness in
`tests/integration/conftest.py`: the session-scoped `db_engine` fixture starts a
PostGIS testcontainer and runs Alembic migrations to `head`
(`conftest.py:15-43`); the per-test `db_connection` fixture yields a
transaction-rolled-back connection for isolation (`conftest.py:46-52`). Fixture
sketch:

- **Seed the FK prerequisites before the `forecasts` row.** `forecasts.model_id`
  is a NOT-NULL FK → `models.id`, so `sa.insert(forecasts)` fails with an FK
  violation unless a `models` row exists first. Follow the exact sequence in
  `tests/integration/store/test_forecast_summary.py:34-77`:
  (1) `_seed_station` (via `PgStationStore(conn).store_station(...)`,
  `test_forecast_summary.py:34-37`) to get a real `station_id`;
  (2) `_seed_model` — insert a `models` row with
  `id`/`display_name`/`artifact_scope`/`description`/`created_at`
  (`test_forecast_summary.py:40-50`);
  (3) either `_seed_artifact` (`test_forecast_summary.py:53-77`) or set
  `model_artifact_id=None` (nullable).
- **Then** seed one `forecasts` row (real UUID `id`, the seeded `station_id`,
  `model_id`, `issued_at` tz-aware, `parameter="discharge"`, `units="m³/s"`,
  `representation` set per case) plus aligned `forecast_values` rows via
  `db_connection.execute(sa.insert(...))` against the reflected/metadata tables —
  same insert path the app reads back. **Seed `forecast_values` with
  `lead_time_hours` starting at 1** (not 0), so that `valid_times[0]` is strictly
  after `issued_at` and assertion 3's anchor-regression guard is meaningful
  (`lead_time_hours=0` would make `valid_times[0] == issued_at`, silently defeating
  it). Seed **at least one** `forecast_values` row so `forecast_detail` passes a
  non-empty `values` context and the `{% if values %}` script block actually
  renders (see assertion 5 / Phase 3 template-guard note).
- Build a `TestClient(app)` and override `get_connection` with a **generator
  function that yields `db_connection`** — NOT `_DummyConnection`, and NOT a
  `lambda: db_connection` (a lambda *returns* the connection instead of yielding,
  skipping FastAPI's dependency teardown). Mirror
  `test_e2e_pipeline.py:699-701`:
  ```
  def _override_conn():
      yield db_connection
  app.dependency_overrides[get_connection] = _override_conn
  ```
  (`db_connection` is the already-open, transaction-rolled-back connection from the
  fixture, so the wrapper yields it directly rather than opening a new one.)
- Add an **autouse fixture that resets `sapphire_flow.api.routes.tables._reflected
  = None` before and after each test**, so the singleton reflects this test's live
  schema and never leaks a stale `MetaData` across tests. (This is required
  regardless of tier because of the module-level cache at `tables.py:24`.)

Assert:

1. `data.json` emits per-series `valid_times` (ISO 8601), top-level `units`, and
   top-level `issued_at` (ISO 8601) for both the **members** and **quantiles**
   branches.
2. `valid_times`, `lead_times`, `values` are equal-length and aligned per series.
3. `data.issued_at` equals the seeded `issued_at` (exact anchor — guards against
   the `valid_times[0]` regression: assert `issued_at` is strictly earlier than
   `valid_times[0]` so a future refactor cannot silently swap the anchor back).
   **This assertion only holds because the fixture seeds `lead_time_hours >= 1`**
   (see fixture sketch); with `lead_time_hours=0` the guard is a no-op.
4. Unknown `forecast_id` returns the empty payload
   (`{"lead_times": [], "members": {}}`), not a 500.
5. **Smoke test only:** `forecast_detail` (`/forecasts/{forecast_id}/`) rendered
   HTML contains the `ensemble-chart` div and the `data.json` fetch URL — i.e. the
   `{% if values %}` script block rendered (this requires the fixture to seed at
   least one `forecast_values` row; otherwise the block is absent and the substring
   match would trivially pass without testing anything). This is a **substring
   check on static HTML**, not a validation of JS behaviour: `data.units` /
   `data.issued_at` / `valid_times` are JS identifiers inside `<script>`, so a
   match cannot prove they are used correctly (or used at all). Correctness of the
   `valid_times`/`units`/`issued_at` *values* is covered by assertions 1-3 on the
   endpoint response.

The observation overlay is exercised via `station_observations_json`, already
covered by the stations test suite; the overlay itself is client-side JS (window
anchoring on `data.issued_at`, `r.ok` guard, forecast-only fallback) and is **not**
validated here beyond the assertion-5 smoke test (no headless-browser test in
scope).

## Process

DRAFT until grill-me confirms the obs window `N` (default 7 days) — the **only**
remaining prerequisite. The `parameter`-string join between `observations` and
`forecasts` for discharge is already **verified from code** (design decision 3:
both sides emit `"discharge"`), so it is no longer a blocking gate. Then → READY.
Scope is small and additive: ~6 backend lines (2 init-dict keys + `valid_times`
appends in both branches + top-level `units`/`issued_at`) + edits to one existing
template script block + one new integration test file (plus a new
`tests/integration/api/__init__.py`). No new endpoint, no new chart div, no second
script block.
