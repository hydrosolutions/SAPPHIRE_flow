# Plan 067 — MeteoSwiss STAC adapter investigation + configurability

**Status**: DRAFT
**Date**: 2026-04-21
**Depends on**: none at the code level. Informed by dress-rehearsal 2026-04-21 findings F4 and F5.
**Scope**: Two phases. **Phase 1 (investigation):** determine why `MeteoSwissNwpAdapter` reported "no cycle available within 3 fallback steps" during the 2026-04-21 dress rehearsal when MeteoSwiss is a reliable publisher — the "cycle late" signal is almost certainly a query-implementation bug on our side (wrong datetime filter semantics, missing CQL predicate, sort-order or pagination-cursor issue), not an external outage. **Phase 2 (configurability):** once the root cause is understood, make `_MAX_FALLBACK_STEPS` configurable via `DeploymentConfig` and decide whether the 100-page pagination cap should be raised, removed, or sidestepped by server-side filtering. Phase 2's shape is determined by Phase 1's findings — Phase 1 may make parts of Phase 2 unnecessary.

---

## Context

### Why now

- Forecast-cycle (A3 step 8) is the only A3 step still blocked after the 2026-04-21 rehearsal. Until it runs end-to-end against real MeteoSwiss data, v0 operational readiness cannot be signed off.
- Nepal production cutover in Oct 2026 depends on the equivalent adapter path working against ECMWF IFS (Plan 047, stub). Any query-semantics bugs in our STAC client likely recur there.
- Plan 063 landed the fetch-semantics redesign and four follow-up bug fixes. This is the next layer — understanding why the adapter misreports cycle availability.

### Observed behaviour (dress rehearsal 2026-04-21, finding F4)

- At 11:35 UTC, `run_forecast_cycle_flow(adapter=adapter)` aborted with `"No cycle available within 3 fallback steps from 2026-04-21T09:00:00+00:00"`.
- Direct `curl` probes of the STAC API returned items whose `forecast:reference_datetime` was `2026-04-20T12:00:00Z` — not today's cycles.
- However, that probe used `?datetime=2026-04-21T.../...` filter, which in STAC semantics filters on the item's valid-time (`properties.datetime`), not the forecast reference cycle. Items from the 2026-04-20T12:00 cycle with valid-time on 2026-04-21 *would* match such a filter.
- **This is the strongest indicator that our adapter's query is asking the wrong question** — filtering by item valid-time when it should be filtering by `forecast:reference_datetime`, or sorting by the wrong property, or paginating wrong.

### Observed behaviour (F5, when Phase 2's caps were manually bypassed)

- Monkey-patched `_MAX_FALLBACK_STEPS = 10`; the adapter reached `2026-04-20T12:00` cycle items, began downloading GRIB2 files (t_2m ctrl + perturbed), correctly applied the `tp + t_2m` allowlist for v0, and aborted with `"STAC pagination exceeded 100 pages"`.
- A single ICON-CH2-EPS cycle contains ~80 ensemble members × ~36 forecast steps × ~40 variables ≈ 115,200 items. With 100 per page, that's ~1,152 pages — far above the current 100-page cap.

### Principle

Investigate before configuring. The `_MAX_FALLBACK_STEPS=3` and 100-page cap both look like symptom-papering if the query itself is wrong. Phase 1 must conclude with a single-sentence root cause before Phase 2 commits to either raising caps or adding server-side filters.

### Non-goals

- **Not** expanding into NWP-late monitoring / operator alerting. That surface belongs in Flow 4 (pipeline monitoring) scoping, which Plan 039 (DEFERRED) already reserves. If Flow 4 picks up the signal later, fine; this plan does not deliver it.
- **Not** changing `nwp_max_fallback_age_hours` policy (default 12.0). That's a deployment-config decision orthogonal to the adapter's ability to *find* cycles.
- **Not** touching the ECMWF IFS adapter (Plan 047). Lessons from Phase 1 should be captured as design notes for when Plan 047 is promoted, but implementation is scoped to MeteoSwiss only.
- **Not** introducing a cycle cache or download memoization. Scope creep; separate plan if warranted.

---

## Architecture decisions (draft)

| # | Decision | Rationale |
|---|---|---|
| D1 | **Phase 1 investigation produces a written root-cause finding** before any code change in Phase 2. Finding lives in `docs/plans/067-*.md` (this file) under a new `## Phase 1 findings` section when T1 completes. | Forces clarity; avoids speculative fixes. Matches the pattern Plan 046 has used for each detour. |
| D2 | **`_MAX_FALLBACK_STEPS` → `DeploymentConfig.nwp_max_fallback_steps`** (default 3, operator-tunable). Delete the module-level constant; thread the config through `MeteoSwissNwpAdapter.__init__`. | Matches the convention already used by `nwp_max_wait_hours` and `nwp_max_fallback_age_hours`. Plan 063 D4 explicitly deferred configurability; this is "the future plan". |
| D3 | **Pagination cap** decision is deferred to Phase 2 once Phase 1 concludes. Options ranked by preference: (a) server-side CQL filter so only relevant items return (few pages); (b) remove the cap entirely (rely on the network timeout as the backstop); (c) raise to 2000 pages (matches worst-case ICON-CH2-EPS cycle size). | Phase 1 may show that we're over-fetching because of a missing CQL filter — in which case (a) is the fix and the cap is irrelevant. |
| D4 | **No changes to the fetched-item allowlist** (v0 = `tp + t_2m` per MEMORY). Phase 2 fixes make the allowlist faster-to-reach, not broader. | Scope discipline; allowlist extensions are per-model decisions, not adapter-infra changes. |
| D5 | **Integration test uses live MeteoSwiss** (`live_stac` marker per pyproject.toml line 80) to exercise a full fetch path against a real recent cycle. CI runs this on a schedule; developers opt in locally. | The adapter has been bitten by half a dozen live-only bugs this month; unit tests with fakes clearly aren't enough. |
| D6 | **Do not expand the investigation beyond MeteoSwiss.** Lessons apply to Plan 047 (ECMWF IFS adapter) but implementation is separate. | Avoids coupling two plans that should ship independently. |

---

## Task sketch

- **T1** — **Phase 1 investigation.** Probe the STAC endpoint with varying `datetime=` semantics, with/without `sortby=`, with/without CQL filter, and compare the adapter's current query path. Deliverable: a 2–3 paragraph `## Phase 1 findings` section in this plan stating the root cause. Required before any code change.
- **T2** — **Phase 1 fix.** Correct the query implementation per T1's finding. Likely candidates (narrow after T1): replace datetime filter with CQL `forecast:reference_datetime` predicate; fix sort order; fix pagination-cursor handling. Add a regression test covering the specific scenario.
- **T3** — **Phase 2 configurability.** Move `_MAX_FALLBACK_STEPS` into `DeploymentConfig` as `nwp_max_fallback_steps`. Delete the module constant. Unit test for default + override.
- **T4** — **Phase 2 pagination cap** per D3, shape decided by T1 outcome.
- **T5** — **Live-STAC integration test** (`live_stac` marker) that: fetches the latest available cycle via the adapter, confirms the fetch completes under the configured fallback steps, downloads the expected allowlisted variables, and returns a non-empty `BasinAverageForecast`. Fail fast if MeteoSwiss is genuinely down (flag test skip, don't fail hard).
- **T6** — **Plan 046 cross-reference update.** Remove step 8's "blocked per F4/F5" Rev 11 note and reinstate the normal procedure. Rev 12 (or similar) in Plan 046 references this plan's DONE commit.

---

## Files to modify / create (sketch)

- Modify: `src/sapphire_flow/adapters/meteoswiss_nwp.py` — query logic per T1+T2; remove `_MAX_FALLBACK_STEPS` module constant; accept `max_fallback_steps` ctor kwarg per T3.
- Modify: `src/sapphire_flow/config/deployment.py` — `nwp_max_fallback_steps: int = 3` field with validator `>= 0`.
- Modify: `config.toml` — document the new knob (keep default behaviour).
- Modify: `tests/unit/adapters/test_meteoswiss_nwp.py` — regression test for T2.
- New: `tests/integration/live/test_meteoswiss_nwp_live.py` — T5, marked `live_stac`.
- Modify: `docs/plans/046-mac-mini-staging-deployment.md` — Rev 12 reinstating step 8.

---

## Dependency graph (sketch)

```json
{
  "phases": [
    {"id": "investigate", "tasks": ["T1"], "parallel": false},
    {"id": "fix", "tasks": ["T2"], "parallel": false, "depends_on": ["investigate"]},
    {"id": "configurability", "tasks": ["T3", "T4"], "parallel": true, "depends_on": ["fix"]},
    {"id": "integration", "tasks": ["T5"], "parallel": false, "depends_on": ["configurability"]},
    {"id": "cross-ref", "tasks": ["T6"], "parallel": false, "depends_on": ["integration"]}
  ]
}
```

---

## Exit gates (sketch)

1. `## Phase 1 findings` section written in this plan, naming the root cause in a single sentence.
2. `uv run pytest tests/unit/adapters/test_meteoswiss_nwp.py` green, including the new regression test.
3. `uv run pyright --strict src/sapphire_flow/adapters/meteoswiss_nwp.py src/sapphire_flow/config/deployment.py` clean.
4. Against a live MeteoSwiss STAC endpoint during business hours, `live_stac`-marked integration test passes: fetches the latest cycle, downloads `tp + t_2m` control + perturbed files, returns non-empty `BasinAverageForecast`.
5. Plan 046 Rev 12 reinstates step 8 — no more "blocked per F4/F5" note in the A3 sequence.
6. Version bump applied.

---

## Risks (sketch)

| Risk | Mitigation |
|---|---|
| Phase 1 reveals MeteoSwiss was genuinely late during our testing window | Re-probe at a different time. If still reliably late, re-scope the plan to fallback-age policy rather than query implementation. |
| Server-side CQL not supported by MeteoSwiss STAC | Fall back to D3 option (b) or (c): remove / raise the page cap. |
| Raising the pagination cap surfaces a worker-memory issue (finding F6 redux) | Cap the concurrent fetch fan-out within the adapter; stream items rather than buffering all pages. |
| Phase 2 configurability (`nwp_max_fallback_steps` in config) is a breaking change if operators override | It's a new field with a safe default — existing deployments behave identically. |
| ECMWF IFS adapter (Plan 047) inherits the same bug | T1's finding becomes a design note pinned to Plan 047's stub for when it promotes. |

---

## Open questions (non-blocking DRAFT → READY)

1. Does MeteoSwiss STAC support CQL-2 filtering, or only the older `datetime=` parameter? Settles D3 option (a).
2. Should the integration test run against a *specific* recent cycle (reproducible) or the *latest* available (realistic)? (Recommendation: latest — reproducibility matters less than live-bug-catch.)
3. Does the `forecast:reference_datetime` property use a different field name in the actual STAC response vs our adapter's query? (Phase 1 answers.)
4. Is the 100-page cap itself load-bearing for any reason (e.g., avoiding runaway queries on a misconfigured endpoint), or is it a safety-net that has never fired in practice?
