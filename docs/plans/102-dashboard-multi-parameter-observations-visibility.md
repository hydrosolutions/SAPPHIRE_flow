# Plan 102 — dashboard: make water_level & water_temperature observations visible

**Status**: READY — grill-me COMPLETE + **plan-review (WF1) COMPLETE (2026-07-06, 3
rounds)**: layout = **multi-panel (stacked, per-parameter axis)**; **ship
independent of Plan 101** with a **QC-failed show/hide toggle**; **scope =
station-detail page only**. Plan-review found 2 blockers + 4 majors (all
implementation-precision, no design change) — resolutions folded into "Plan-review
fixes (round 2)" at the end. Next: **WF2 (vision-build)** to implement (hold-at-PR).
**Priority**: medium — operators report "we don't see any water level or
temperature data" on the dashboard, yet all three parameters are ingested and
served. It is a visibility/UX gap, not missing data.
**Phase**: v0b — review dashboard
**Parent**: Plan 096 (dashboard forecast graph); the multi-parameter experiment
(discharge + water_level [+ water_temperature])
**Related**:
- `src/sapphire_flow/api/templates/stations/detail.html:132-137` (the
  `#param-select` dropdown definition) and `:242-274` (the obs-chart fetch + plot
  block inside `loadCharts()` → `/observations.json?parameter=…`; groups by
  qc_status; already labels axis "Date (UTC)")
- `src/sapphire_flow/api/templates/stations/detail.html:216-223` (the `paramEl`
  null-guard: `if (!paramEl || !startEl || !endEl) { … return; }` — bails the WHOLE
  function, incl. baseline/forcing/hindcast fetches, if `#param-select` is absent)
- `src/sapphire_flow/api/routes/stations.py:388-434` (`observations.json` — returns
  ALL rows incl. `qc_statuses`, **no** qc filter, but **does** filter by
  `?parameter=`), `:194-233` (`parameters` = sorted `DISTINCT parameter` from
  observations → the dropdown options, already injected via `station["parameters"]`
  at `:233`)
- `src/sapphire_flow/api/routes/stations.py:487-525` (`baselines.json` — **fully
  parameter-aware**, filters `clim_baselines` by `?parameter=`; a station may have
  a water_level baseline but no discharge baseline)
- Plan 101 (water_level `qc_failed` datum bug — why water_level renders as red
  "failed" markers even when selected)
**Created**: 2026-07-06

---

## Problem (grounded on the live local stack, 2026-07-06)

The data is present and served — the dashboard just doesn't surface it:

- The `observations` table holds **discharge, water_level, water_temperature** for
  2009/2091. The local API returns real data for each, e.g. station 2009 over the
  last 7 days: **water_temperature = 350 pts ~9.1 °C, all `qc_passed`**;
  **water_level = 350 pts ~376, mostly `qc_failed`**.
- But the station-detail **obs chart shows ONE parameter at a time** via
  `#param-select`, and the options are `sorted(DISTINCT parameter)` → **discharge
  first**, so the default view is discharge and the other two are invisible unless
  the operator knows to change the dropdown. Perceived result: "no water
  level/temperature data."
- Compounding it, **water_level currently renders as red `qc_failed` markers**
  (the Plan 101 datum/threshold bug), so even when selected it looks broken/empty
  rather than like a valid series.

So two things hide the parameters: (1) single-parameter dropdown defaulting to
discharge (discoverability), and (2) water_level's all-failed QC display (Plan 101).

## Goal

Every ingested parameter for a station is **visible and legible** on the dashboard
without hunting — discharge, water_level, and water_temperature — each with an
appropriate axis/scale and clear QC state.

## DECIDED DESIGN (grill-me 2026-07-06)

- **D1 — multi-panel (stacked), one chart per parameter.** Replace the single obs
  chart for the station-detail page with **N stacked panels**, one per parameter in
  `station["parameters"]`, each with its **own y-axis + unit**. All parameters are
  always visible — no dropdown hunting. Each panel calls the existing
  `/api/v1/stations/{id}/observations.json?parameter=…` endpoint (unchanged
  contract). Rationale: the three parameters have incompatible scales/units (m³/s,
  m a.s.l., °C), so separate axes read far better than an overlay.
- **D2 — units via a small hardcoded display map (no registry exists).** There is
  **no** parameter→unit registry today (the obs y-axis currently shows the bare
  `param` string). Add a minimal display-unit map in the template/route:
  `discharge → "m³/s"`, `water_level → "m a.s.l."`, `water_temperature → "°C"`
  (unknown params fall back to the bare name). Each panel's y-axis title becomes
  `"<param> (<unit>)"`. **One source of truth:** a `PARAM_UNITS` dict in
  `stations.py`, passed as a `"param_units"` context key and emitted inline as
  `const UNIT_MAP = {{ param_units | tojson }};` so both the client-side Plotly axis
  titles and the server-side panel headings read the same dict (see Implementation
  vision → Unit map). A future canonical unit registry can replace it without
  touching the layout. water_level's unit is **m a.s.l.** (the absolute datum — see
  Plan 101).
- **D3 — ship INDEPENDENT of Plan 101 + a QC-failed toggle.** Do **not** block on
  Plan 101. Each panel keeps the existing group-by-`qc_status` rendering, plus a
  **single page-level "show QC-failed" checkbox** (default **on**, but failed points
  **de-emphasized** — smaller/greyer markers) so water_level is **visible and legible
  now** (not an alarming wall of red) and simply becomes clean once Plan 101 fixes the
  datum threshold. A short inline note links water_level's current failures to Plan 101.
  - **Toggle mechanism = `Plotly.restyle` (no re-fetch).** After each obs panel is
    plotted, record the trace index of its `qc_failed` trace **dynamically** — the
    trace-building loop at `detail.html:265-266` **skips any qc_status absent from the
    data** (`if (!groups[q]) continue;`), so the `qc_failed` index is **data-dependent,
    NOT a constant** from the fixed status order. A station/date-window with no `raw` or
    `qc_suspect` rows pushes `qc_failed` to index 0 or 1, not 3. The implementer MUST
    compute it from the built `traces` array, e.g.
    `failedIdx[param] = traces.findIndex(t => t.name === 'qc_failed')` (returns **-1**
    when the panel has no `qc_failed` group). The checkbox's `onchange` iterates every
    obs-panel div and calls `Plotly.restyle(divId, {visible: checked}, [failedIdx[p]])`
    — but **only when `failedIdx[p] >= 0`** (guard against -1/undefined; passing an
    out-of-range or undefined index to `Plotly.restyle` throws or mis-toggles trace 0).
    Zero HTTP round-trips, instant. **Do NOT re-fetch panels on toggle** (a re-fetch
    loop costs N×~350ms per click for an N-parameter station). One checkbox + one
    restyle loop over all panel div-IDs; **page-level, not per-panel** — per-panel
    checkboxes add UI clutter with no operator benefit (the toggle exists only to make
    water_level legible).
- **D4 — scope: station-detail page ONLY.** The observations-coverage page and the
  station list are **out of scope** for this plan (revisit separately if needed).

### Implementation vision (feeds WF1 plan-review → WF2)

This section pins the concrete JS entry-points so the implementer does not have to
re-design. The core problem: `loadCharts()` currently reads `paramEl.value` from
`#param-select` (`detail.html:216-225`) and **bails the entire function** at the
null-guard (`:220-223`) if that element is absent — so naively removing the obs
dropdown would silently kill the baseline, forcing, AND hindcast fetches too.

**RESOLUTION — approach (B): remove `#param-select`, split obs from forecast charts.**
We remove the shared obs dropdown and add a small **`#forecast-param-select`** scoped
only to the baseline + hindcast charts. Rationale for (B) over "repurpose the existing
dropdown": the obs section becomes fully multi-panel (every parameter always visible),
which is the whole point of D1 — keeping a single dropdown around the obs section
would re-introduce the discoverability gap.

- **Template (`stations/detail.html`) — obs section:**
  - **Delete** the `#param-select` `<div>` (`:131-138`).
  - **Replace** the single `#obs-chart` (`:155`) with a server-side loop over
    `station.parameters` that renders, **per parameter**, a heading (label uses the unit
    map — see below), a chart `<div id="obs-chart-{{ p }}">`, **and a per-panel error
    `<div id="obs-error-{{ p }}" style="color:red;...">`** (so N concurrent panel fetches
    don't stomp the shared `#chart-error` — see N-panel status/error note below).
    `station.parameters` is already in the template context (`:16`, `:129`).
  - **Add** one page-level `<input type="checkbox" id="qc-failed-toggle" checked>`
    "Show QC-failed points" next to the date pickers.
  - The date pickers and the `Reload` button (`:141-148`) keep their
    `onchange`/`onclick="loadCharts()"` wiring unchanged — `loadCharts()` remains the
    single top-level entry-point (see JS below).
  - **REQUIRED server→JS emission (blocker fix).** Today the `<script>` block emits
    **only** `const stationId = "{{ station.id }}";` (`detail.html:170`); the `station`
    Python dict is **never** serialised to JS. Both `loadObsCharts()` (iterates the
    parameter list) and the `forecastParam` default read the parameter list at runtime,
    so the list MUST be emitted as a JS array. **Add, immediately after
    `detail.html:170`:** `const STATION_PARAMETERS = {{ station.parameters | tojson }};`
    Every JS reference below uses `STATION_PARAMETERS` — **never** the bare
    `station.parameters` (which is a Jinja server-side var with no JS binding; using it
    in JS throws `ReferenceError: station is not defined`). The `#forecast-param-select`
    `<option>` elements are still populated server-side by the Jinja loop; the JS array
    is needed only for the `loadObsCharts()` iteration and the `forecastParam` default.
    (`{{ ... | tojson }}` under FastAPI's Jinja2 autoescape emits unicode-escaped JSON
    that JS parses cleanly.)
- **Template — baseline/hindcast section:** add a compact
  `<select id="forecast-param-select" onchange="loadForecastCharts()">` populated
  from `station.parameters`, **defaulting to the first parameter for which forecast
  data is relevant**. Do NOT hardcode the literal `"discharge"`:
  `baselines.json` is parameter-aware (`stations.py:487-525`) and a station may have
  a water_level baseline but no discharge baseline — a hardcoded discharge default
  would render an empty baseline chart while valid data exists. Default instead to
  `discharge` **only if present**, else the first entry (using the JS array
  `STATION_PARAMETERS` emitted above — **not** the Jinja `station.parameters`):
  `const forecastParam = STATION_PARAMETERS.includes('discharge') ? 'discharge' : STATION_PARAMETERS[0];`
  (`station.parameters` is `sorted(...)` at `stations.py:207`, so discharge wins
  alphabetically when present; otherwise the operator sees real data). Set the
  `<select>`'s initial `selected` option accordingly.
- **JS restructure (`loadCharts()` split into named entry-points):**
  - **`loadObsPanel(param, seq)`** — fetches
    `/observations.json?parameter=${param}&start=…&end=…` for ONE parameter, groups
    by qc_status (existing styles at `:258-263`), plots into `obs-chart-${param}`,
    y-axis = `"${param} (${UNIT_MAP[param] || param})"`, x-axis `"Date (UTC)"`. It
    records the `qc_failed` trace index **dynamically** —
    `failedIdx[param] = traces.findIndex(t => t.name === 'qc_failed')` (**-1** when the
    panel has no `qc_failed` group; see D3, the fixed status order is NOT a safe index)
    — then applies the current `#qc-failed-toggle` state via `Plotly.restyle` **only
    when `failedIdx[param] >= 0`** immediately after plot. Errors from this panel's
    fetch write to a **per-panel** error surface `#obs-error-${param}` (a `<div>`
    rendered by the Jinja panel loop, see Template above), **not** the shared
    `#chart-error` — see the N-panel status/error note below.
  - **`loadObsCharts()`** — reads the shared date range, **iterates
    `STATION_PARAMETERS`** (the emitted JS array — **not** the Jinja `station.parameters`)
    and calls `loadObsPanel(p, seq)` for each. **Per-panel stale guard:** the existing
    global `_loadSeq` counter (`:208-245`) guards only a single in-flight obs fetch;
    with N panels in flight a mid-load date change must invalidate the right responses.
    Give **each panel its own seq entry** (a `_panelSeq = {}` map keyed by param) or use
    an `AbortController` per panel — a single global counter is insufficient. **Bump ALL
    per-panel seq counters at the TOP of `loadObsCharts()`, before the fetch loop — not
    inside the loop.** Bumping in bulk up-front means a concurrent second invocation
    (rapid date change) atomically invalidates the entire in-flight batch; bumping
    per-iteration lets the second call advance a param's counter before that param's
    first fetch fires, so the stale-check compares against an already-superseded value
    and a stale response can win. Each `loadObsPanel` captures its `seq` and drops its
    response if `_panelSeq[param] !== seq`.
  - **N-panel status/error surfaces (race fix).** The shared `#chart-status` /
    `#chart-error` divs (`detail.html:152-153`) are written by every fetch callback
    (`:246, 276, 298, 325, 370`); with N obs panels now firing concurrently they would
    stomp each other (last completer wins the status; any error clobbers a prior one).
    Split the surfaces: (1) `loadObsCharts()` sets a **single** `#chart-status` summary
    (`"Loading N panel(s)…"`) once before dispatch, and each `loadObsPanel` writes its
    OWN errors to a **per-panel** `#obs-error-${param}` div (rendered by the Jinja panel
    loop) — never the shared `#chart-error`. (2) The shared `#chart-status` /
    `#chart-error` remain owned by `loadForecastCharts()` for baseline/forcing/hindcast
    (a single param, no concurrency among themselves beyond the pre-existing three
    fetches). This keeps every panel's error independently visible instead of one
    overwriting another.
  - **`loadForecastCharts()`** — reads `#forecast-param-select` (falling back to the
    computed `forecastParam` default if the element is somehow absent) and drives the
    baseline (`:279`) and hindcast (`:328`) fetches with that param. The forcing
    fetch (`:301`) takes no parameter and is called here unchanged.
  - **`loadCharts()`** (kept as the single top-level entry-point the date pickers /
    Reload call) simply invokes `loadObsCharts()` + `loadForecastCharts()`. The old
    `paramEl` null-guard (`:220-223`) is **removed** (there is no `#param-select`);
    each sub-function guards only the elements it actually reads.
  - **`#qc-failed-toggle.onchange`** — iterates every `obs-chart-${p}` div and, **only
    when `failedIdx[p] >= 0`** (guard: a panel with no `qc_failed` group has
    `failedIdx[p] === -1`; passing `-1`/`undefined` to `Plotly.restyle` throws or
    mis-toggles trace 0), calls
    `Plotly.restyle(divId, {visible: this.checked}, [failedIdx[p]])` using the stored
    per-panel `qc_failed` trace index. No re-fetch (see D3).
- **Route (`stations.py`):** **no endpoint contract change.** `parameters` is already
  computed (`:194-207`) and injected as `station["parameters"]` (`:233`), and
  `observations.json` already serves per-parameter data with `qc_statuses`. The
  **only** context addition is the unit map: add a module-level `PARAM_UNITS` dict to
  the route and pass it as a new `"param_units"` key in the `TemplateResponse`
  context (`:371-384`). **Do NOT** re-inject `parameters` — it is already available
  as `station.parameters` in the template (`:16`, `:129-134`); a redundant top-level
  key risks a stale shadow if `station["parameters"]` is later updated.
- **Unit map — single source of truth, emitted as inline JS (`{{ param_units | tojson }}`).**
  The unit label is needed **client-side** (Plotly sets `yaxis.title.text` in JS at
  `:273`), so the map must reach the JS. Emit it once as
  `const UNIT_MAP = {{ param_units | tojson }};` in the `<script>` block. Server-side
  panel headings (rendered in the Jinja loop) read the **same** `param_units` context
  var, so both server and client labels come from one dict in `stations.py`. Values:
  `discharge → "m³/s"`, `water_level → "m a.s.l."`, `water_temperature → "°C"`;
  unknown params fall back to the bare name (`UNIT_MAP[p] || p`).
- **Verification:** render 2009/2091 at http://localhost:8010 — three panels
  (discharge m³/s, water_level m a.s.l., water_temperature °C); water_temperature a
  clean ~9 °C series; water_level visible with failed points de-emphasized + the
  Plan-101 note; the QC-failed toggle hides/shows failed markers **without a network
  round-trip**; the date pickers + Reload re-fire all N obs panels; baseline +
  hindcast still render for the `#forecast-param-select` default.

### Test plan

Manual browser verification (above) is a smoke check, not the gate. Add automated
tests mirroring the existing dashboard-page pattern
(`tests/integration/api/test_dashboard_forecasts.py::TestForecastDetailPage`,
`:169-170` — seed DB, GET the HTML route, assert on rendered fragments):

- **`TestStationDetailPage`** (new integration test, mirror of `TestForecastDetailPage`):
  seed a station with multi-parameter observations (discharge + water_level +
  water_temperature), GET `/stations/{id}/`, and assert: (a) one obs-panel div per
  parameter is present (e.g. `id="obs-chart-water_level"`); (b) the unit labels are
  rendered — **assert on the server-side Jinja panel headings**, which emit UTF-8
  directly (e.g. the panel-heading HTML for `water_level` contains `"m a.s.l."`, and
  the `discharge` / `water_temperature` headings contain `"m³/s"` / `"°C"`). Do **NOT**
  assert the literal `"m³/s"` / `"°C"` against the emitted `UNIT_MAP` JSON block:
  `{{ ... | tojson }}` under Jinja2 autoescape unicode-escapes non-ASCII (`³` → `³`,
  `°` → `°`), so that block contains `"m³/s"` / `"°C"`, not the literal
  glyphs — a literal-glyph assert against the JSON path would fail. (c) the `#qc-failed-toggle`
  checkbox and `#forecast-param-select` are present; (d) `#param-select` is **absent**
  (regression guard against the old shared dropdown).
- **`TestStationObservationsJson`** (new unit test): the `.json` dashboard endpoint
  at `stations.py:388` (`/api/v1/stations/{id}/observations.json`) currently has
  **zero coverage** — `tests/unit/api/test_api_stations.py::TestListObservations`
  (`:190`) tests only the API variant `/api/v1/stations/{id}/observations`, not the
  `.json` route. Add a test that seeds observations across qc_statuses and asserts the
  `.json` payload shape (`timestamps` / `values` / `qc_statuses` arrays, aligned and
  ordered, filtered by `?parameter=`).

## Non-goals

- Fixing the water_level QC failures — that is Plan 101 (datum/threshold). This plan
  only ensures the series is *visible*.
- Changing ingestion or the `observations.json` contract (it already serves all
  parameters + qc_statuses).
- Redesigning the forecast/hindcast charts. They stay single-parameter, driven by a
  scoped `#forecast-param-select` (default `discharge`-if-present-else-first — see
  Implementation vision). Note: `baselines.json`/`hindcasts.json` are already
  parameter-aware (`stations.py:487-525`), so the selector honours whatever forecast
  data a station actually has rather than assuming discharge exists.

## Process

Grill-me **COMPLETE** (2026-07-06): D1 multi-panel, D2 hardcoded unit map, D3 ship
independent + QC-failed toggle, D4 station-detail only (see DECIDED DESIGN).
**plan-review round 1 RESOLVED** (2026-07-06) the residual `#param-select` question
and the follow-on JS/refactor/test gaps (see Implementation vision + Test plan):
approach (B) — remove `#param-select`, split `loadCharts()` into
`loadObsCharts()` (N panels, per-panel stale guard) + `loadForecastCharts()`
(scoped `#forecast-param-select`, default `discharge`-if-present-else-first);
QC toggle = page-level `Plotly.restyle` (no re-fetch); unit map = single
`PARAM_UNITS` dict emitted inline; tests = `TestStationDetailPage` +
`TestStationObservationsJson`. Next: confirm READY → implement. Implementation is a
template + route change (`stations/detail.html`, `stations.py` context only — no
endpoint contract change) plus the two tests above → **hold-at-PR** with a version
bump.

## Plan-review fixes (2026-07-06, round 2) — AUTHORITATIVE (supersede any conflicting detail above)

Plan-review (WF1, 3 rounds) found 2 blockers + 4 majors. Resolutions folded in:

- **B1 (blocker) — parameter-less stations must not crash.** The `<script>` block
  (`detail.html:169`) is OUTSIDE `{% if station.parameters %}`, so the load runs on
  every page; today the removed `paramEl` null-guard (`:220-223`) is the only thing
  stopping chart fetches when a station has no parameters. **Fix:** emit
  `const STATION_PARAMETERS = {{ station.parameters | tojson }};` **unconditionally**
  (`[]` when empty) and add, as the first line of the refactored `loadCharts()` (and
  defensively in `loadObsCharts()` + `loadForecastCharts()`):
  `if (!STATION_PARAMETERS || STATION_PARAMETERS.length === 0) return;`. This
  replaces the old `paramEl` null check.
- **B2 (blocker) — `TestStationObservationsJson` is an INTEGRATION test, not unit.**
  `/observations.json` → `get_reflected(conn)` runs `MetaData.reflect` against a live
  DB; the unit conftest's `_DummyConnection.execute()` no-op breaks reflection.
  **Fix:** place `TestStationObservationsJson` **and** `TestStationDetailPage` in
  `tests/integration/api/test_dashboard_forecasts.py` (reuse its `db_connection`
  fixture + `_client()` helper) so they also inherit its `autouse=True`
  `_reset_reflected` fixture (clears the `tables.py` `_reflected` singleton between
  tests). If a new file is used instead, extract `_reset_reflected` to
  `tests/integration/api/conftest.py` (M4 below).
- **M1 (major) — actually de-emphasize `qc_failed`, not just toggle it.** The
  `Plotly.restyle` toggle only flips `visible`; it does NOT restyle. So change the
  `styles['qc_failed']` entry (currently `detail.html:262` bright red size-6) to
  **`{mode: 'markers', marker: {color: '#BDBDBD', size: 3, opacity: 0.6}}`** in the
  obs panels. The toggle then hides/shows already-muted markers — meeting D3's "not
  an alarming wall of red".
- **M2 (major) — shared JS state at module scope.** Declare
  **`let failedIdx = {};`** and the obs stale-guard counter at the SAME scope as the
  existing `let _loadSeq = 0;` (`detail.html:208`), i.e. outside all functions, so
  both `loadObsPanel` (writer) and the `#qc-failed-toggle` `onchange` (reader) share
  them. Per M-minor below, use a **single `let _obsSeq = 0;` counter** (mirror of
  `_loadSeq`) rather than a per-param `_panelSeq` map — all N panels dispatch in one
  `loadObsCharts()` call, so one integer gives identical atomicity.
- **M3 (major) — `#forecast-param-select` default is set server-side.** In the Jinja
  loop, emit `selected` on `discharge` when present else `loop.first`
  (`{% if p == default_forecast_param %}selected{% endif %}`, with
  `default_forecast_param` computed in `stations.py`) — server-side correctness, no
  JS needed. (Alternative: no `selected` + `document.getElementById(
  'forecast-param-select').value = forecastParam;` before the initial `loadCharts()`.)
- **M4 (major) — `_reset_reflected` fixture** must cover the new integration tests
  (see B2): inherit it by living in `test_dashboard_forecasts.py`, or extract it to
  `tests/integration/api/conftest.py`.
- **Minors folded:** single `_obsSeq` counter (not a `_panelSeq` map); in the test
  plan, note `{{ ... | tojson }}` renders **JSON unicode escapes** (`m³/s`),
  so assert units on the **server-side Jinja panel headings**, not the JSON block;
  pin the toggle handler to **`{visible: this.checked}`** (not bare `checked`); the
  de-emphasized style values are pinned in M1.

**Status after round 2:** the residual blockers are implementation-precision, not
design forks — the DECIDED DESIGN (multi-panel / QC toggle / detail-only) is
unchanged. With B1/B2 + M1–M4 folded, Plan 102 is **READY** for WF2 (vision-build).
