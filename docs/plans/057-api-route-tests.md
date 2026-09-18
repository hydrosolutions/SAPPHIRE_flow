# Plan 057 — API route-module tests (stub)

**Status**: DRAFT (stub)
**Date**: 2026-04-18
**Depends on**: none — orthogonal to other active plans.
Relates to Plan 055 D4 (route tests deferred here from the hygiene plan).

---

## Scope (sketch)

Add test coverage for the 5 HTML route modules under `src/sapphire_flow/api/routes/`
(`forecasts.py`, `dashboard.py`, `models.py`, `stations.py`, `tables.py`) plus
`health.py` (see open question 1 below).

### First step before design: classify `health.py`

`docs/v0-scope.md` §J marks `GET /api/v1/health` as done in Plan 041 (JSON
endpoint), but the file is named `health.py` rather than `api_health.py` (the
naming convention used for other tested JSON routes: `api_alerts.py`,
`api_forecasts.py`, `api_stations.py`). The answer to this question determines
whether `health.py` belongs in this plan at all:

- **(a)** It is the Plan 041 JSON endpoint with a naming inconsistency — in
  which case it is a Plan 041 test gap, not an HTML-route item, and Plan 057
  should not own it.
- **(b)** It is a separate HTML page (e.g. a dashboard status view) — in which
  case it belongs here alongside the other HTML route modules.

Resolve this before exiting DRAFT.

### Open questions (non-blocking; to be resolved when this plan exits DRAFT)

1. **`health.py` classification** — JSON endpoint (Plan 041 test gap) or HTML
   route (Plan 057 scope)? See classification step above.
2. **HTML assertion style** — structural (BeautifulSoup-walk of specific
   `data-*` attributes or element hierarchy) vs snapshot (Syrupy /
   approvaltests). Structural tests survive template refactors that preserve
   semantics; snapshots detect unintended visual regressions but need
   frequent updates. Pick one style and apply consistently across all 5
   (or 6) route modules.
3. **Test client setup** — direct `TestClient` (from `starlette.testclient`)
   vs any `pytest-fastapi` idiom already used elsewhere in the suite. Check
   `tests/unit/api/` and `tests/integration/api/` for existing precedent
   before choosing.
4. **Scope of JSON API routes** — should Plan 057 also cover the JSON API
   routes (`api_alerts.py`, `api_forecasts.py`, `api_stations.py`) that
   already have tests, to document consistency and gaps? Recommendation:
   scope Plan 057 to HTML-only routes; JSON routes are a separate concern.
5. **Fake vs real services** — HTML routes typically call service-layer
   functions. Should tests inject fake stores (matching the unit-test
   pattern elsewhere) or use a lightweight in-memory DB (matching the
   integration-test pattern)?

---

## Out of scope (for this plan)

- `services/forecast_combination.py` — already covered by
  `tests/unit/services/test_forecast_combination.py` (456 lines); no action
  needed.
- JSON API routes (`api_alerts.py`, `api_forecasts.py`, `api_stations.py`)
  unless the scope decision above changes.
- Coverage threshold enforcement — CLAUDE.md explicitly warns against
  coverage chasing.

---

## Task list

Not yet drafted. This is a stub pending resolution of the open questions
above. A future revision will expand the scope sketch into a concrete task
list once `health.py` is classified and the assertion / test-client style
is decided.
