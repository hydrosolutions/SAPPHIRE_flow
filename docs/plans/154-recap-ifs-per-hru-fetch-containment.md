---
status: READY
created: 2026-08-11
plan: 154
title: Recap IFS fetch containment — one station's missing data must not degrade the whole cycle
scope: Contain RecapDataUnavailableError inside RecapGatewayForecastAdapter.fetch_forecasts so a data gap affecting one HRU no longer discards every other station's already-accumulated rows and no longer escalates a station-scoped gap into the flow's cycle-wide runoff-only degradation. Containment and commit are both per HRU (the Gateway call unit): an HRU is committed only with its complete variable set, and station-level partiality within an HRU is unrepresentable at this boundary (one call per HRU/variable; every polygon column required). Per-HRU divergence is an ANOMALY (owner-confirmed): healthy HRUs are still served, and the fault is raised as a CRITICAL pipeline_health record naming the affected stations, with DEGRADED cycle health, per the logging standard. Preserves today's behaviour when no HRU commits. No adapter return-type change, no Protocol change. The single-HRU cycle probe is retained unchanged (publication is global, so one HRU is representative); requirement-driven probe/fetch variable selection is a named follow-on.
depends_on: []
blocks: []
supersedes: []
---

# Plan 154 — Recap IFS fetch containment

## Status
**READY** (owner-ratified 2026-08-12). A standalone operational-reliability fix (category **A**), independent of the
forecast-cycle redesign. No open questions: D10 answered by the owner, the requirement-driven variable selection it
raised split out as a follow-on, and the full adversarial review's blocker + two majors folded in.
Numbered 154 because 152 is taken (aquacast) and 153 is informally reserved for multi-resolution domain-type support.

**Provenance.** Two manual Codex passes plus a `/plan` review shaped this document; the `/plan` run escalated by
over-expansion (179 → 418 lines) rather than converging, so this is a **reconstruction** carrying only the
independently re-verified findings. The expanded version is preserved outside the repo for audit. The reviewers
**disagreed with each other** on commit granularity (HRU vs station); that was settled by reading the adapter boundary
directly rather than by deferring to either — see D2, which records the reasoning and the condition under which the
answer flips.

## Problem
`RecapGatewayForecastAdapter.fetch_forecasts` loops over HRUs and, for each variable, fetches the deterministic
control member **outside any containment block**:

- The HRU loop is `for hru_name, refs_by_polygon in by_hru.items()`
  (`src/sapphire_flow/adapters/recap_gateway.py:825`).
- The **control (`fc`)** call — `_accumulate_member(..., ifs_type="fc", ...)` — sits directly in that loop body with
  **no `try`** (`:830-841`).
- Only the **perturbed (`pf`)** loop is contained: `except RecapDataUnavailableError:` → log
  `recap.pf_unavailable_control_only` → `break` (`:842-871`). Added deliberately (Plan 127 Fix 1) because ECMWF
  disseminates `fc` before `pf`, so during that window every `pf` member is absent.
- `_accumulate_member` reaches the Gateway through `_guarded_fetch(self._client.ecmwf.ifs_forecast, ...)`
  (`:908-911`), which raises `RecapDataUnavailableError` when the Gateway reports `source_data_missing`.

**Consequence.** A control-fetch gap for one HRU propagates out of the *entire* `for hru_name` loop. Rows already
accumulated for HRUs processed earlier are discarded with the stack frame, and later HRUs are never attempted.

**Blast radius is the whole cycle.** At flow level `_fetch_nwp_task` catches `RecapDataUnavailableError`
(`src/sapphire_flow/flows/run_forecast_cycle.py:1101-1116`) and returns
`_NwpFetchOutcome(..., nwp_unavailable=True)` with a WARNING pipeline-health record naming `source_data_missing`. That
handler is **correct** for a genuinely cycle-wide gap — it degrades to runoff-only rather than aborting — but here it
fires on a **station-scoped** cause, so **every station in the deployment loses NWP forcing for that cycle**,
including stations whose own data was complete.

**Root cause:** the adapter **loses the scope of the failure**, raising the same exception for "this HRU's data is
missing" and "this cycle is not published at all". The flow can only see the exception type, so it correctly applies
the cycle-wide remedy to both.

**Reachability.** Latent on the current single-HRU deployment; live for multi-HRU Nepal.

**Note on framing (owner, 2026-08-12).** Per-HRU divergence is **not** an expected operating condition — publication
is global, so divergence means the HRU or the Gateway is broken (D10). Containment therefore exists to **diagnose the
fault accurately and keep healthy basins served**, not to normalise a routine gap. Today's behaviour fails both
tests: it discards healthy HRUs' data *and* reports the cause as cycle-wide non-publication.

## Design

- **D1 — Contain the exception per HRU (the fetch unit).** Wrap each HRU's fetch in
  `except RecapDataUnavailableError`, record that HRU as **discarded**, and continue to the next HRU. A discarded HRU
  contributes no rows for any of its stations — the Gateway call is per `(HRU, variable, member)`, so a raised call
  yields nothing for that HRU regardless of which station needed it. Only `RecapDataUnavailableError` is contained.
- **D2 — Stage per HRU and commit all-or-nothing per HRU — and this IS the correct granularity.**
  *(Two reviewers disagreed here; resolved by reading the boundary directly.)* One reviewer argued commit must be
  per **station**, because an HRU groups several stations (`_group_by_hru` →
  `dict[GatewayHruName, dict[GatewayPolygonName, GatewayPolygonRef]]`, `:651-653`) and discarding the HRU would drop a
  healthy sibling. That is true of HRU *grouping* but **not producible at this boundary**:
  - A control fetch is issued **once per `(HRU, variable, member)`** (`:826-841`), so a raised call yields rows for
    **no** station in that HRU — never a subset.
  - `_iter_long_rows` receives the HRU's **entire** `refs_by_polygon` and raises `AdapterError` if **any** resolved
    polygon lacks a column (`:511-525`), so a successful call yields rows for **every** station in the HRU.
  - Therefore "station A complete, station B incomplete, same HRU, same variable" is **unrepresentable**. Per-station
    coverage tracking would be additional state guarding an unreachable case, and HRU-local staging discards a failed
    call unit without needing any station→HRU reverse lookup.
  - **Invariant (state it in the code):** *within one HRU, all stations share the same fetch outcome per variable.*
  - **Forward constraint — this changes under Plan 151.** Plan 151 D7 deliberately reclassifies a missing polygon
    column from a batch-wide `AdapterError` into **per-station** unavailability on its new per-track path. Once that
    lands, station-level partiality **becomes** representable and commit granularity must move to per-station **there**.
    This plan's HRU granularity is correct for the legacy path it fixes, and must not be copied into the per-track
    path unexamined.
  - **Invariant: a committed HRU has its complete variable set.** IFS fetches **two** variables per HRU
    (`RECAP_VARIABLES`, `:75-90`) and `_accumulate_member` writes rows straight into the shared accumulator as each
    call succeeds (`:912-921`), so precipitation-succeeds-then-temperature-raises must discard the HRU's staged
    precipitation rows rather than ship **incomplete forcing** — strictly worse than today's cycle-wide degradation.
  - **Provenance commits on the same boundary.** `cycle_source_run` is shared mutable state threaded through
    `_accumulate_member`'s `prior` argument (`:823`) and set by the first successful call, so a discarded HRU must not
    determine the returned results' `cycle_time`.
- **D3 — Re-raise only on TOTAL loss; distinguish DISCARDED from legitimately EMPTY.** *(Reviewer blocker-fix.)*
  Three outcomes, not two:
  - **At least one HRU committed → return its stations.** Stations absent from the mapping are already how this
    return shape says "no data for this station" (`:874-878`); the downstream per-station path degrades them
    individually.
  - **No HRU committed AND at least one HRU discarded → re-raise `RecapDataUnavailableError`,** so the flow's
    existing cycle-wide handler fires unchanged. This preserves today's behaviour for the total-failure case.
  - **No HRU committed and NO HRU discarded** (every response well-formed but empty) → return `{}`, exactly as
    today. Without this split, an empty-but-successful HRU could mask a real total failure: the flow would treat `{}`
    as success and never set `nwp_unavailable`.
- **D4 — Divergence is an ANOMALY: serve the healthy HRUs, alarm LOUDLY.** *(Owner-answered 2026-08-12, D10.)*
  The owner confirms the NWP fetch is **global**: all HRUs should resolve to the same cycle, and a forecast present
  for one HRU but absent for another **in one deployment indicates a fault in that HRU or in the Data Gateway** —
  not a routine timing gap. This changes what containment is *for*: not to absorb an expected condition, but to
  **diagnose it accurately** instead of misattributing it to a cycle-wide non-publication, and to keep healthy basins
  served while the fault is raised.
  - **Behaviour on divergence (owner-ratified):** the healthy HRUs' stations get their forecasts; the affected HRU's
    stations fall back locally; a **CRITICAL** `pipeline_health` record names the affected **stations** and
    cycle; cycle health is **DEGRADED**; the application log carries the matching ERROR. Withholding a valid forecast for a healthy basin does not repair the broken HRU —
    the alarm is what makes the failure loud, not the withholding.
  - **Severity is deliberately higher than a routine warning.** `docs/standards/logging.md:428` requires a queryable
    `pipeline_health` record for resilience events affecting operator trust, explicitly including **dark station
    forecasts**; because divergence signals a fault rather than a timing gap, the health record is
    **CRITICAL** — not the WARNING used for a genuine cycle-wide `source_data_missing`. (`PipelineHealthStatus` is
    `OK | WARNING | CRITICAL`, `types/enums.py:143-146`; there is no ERROR status. ERROR is the *application-log*
    level, a different destination per `docs/standards/logging.md`.) Log-only would both violate the standard and let
    the cycle report `HEALTHY` while an NWP-fed model was silently suppressed.
  - **No adapter contract change is needed.** The flow already knows which stations it requested, so `_fetch_nwp_task`
    reconciles requested station ids against the returned mapping's keys, appends the `NWP_DELIVERY` record naming the
    missing **stations**, and threads an internal partial flag through `_NwpFetchOutcome` into
    `_forecast_cycle_health`.
  - **Stations, not HRUs — the flow cannot see HRUs** (reviewer major-fix). The returned mapping is station-keyed
    (`:873-878`), `StationWeatherSource` carries no HRU (`types/station.py:103-109`), and HRU identity lives only in
    the adapter-private `GatewayPolygonRef` (`:120`, `:811`). Naming stations needs no new channel and is what an
    operator acts on anyway; the adapter's own log event still names the HRU (D4 last bullet), so the two together
    give the full picture without widening the adapter contract (D8).
  - **Known conflation, accepted:** a station missing from the mapping because the resolver skipped it is
    indistinguishable at this seam from one missing through containment. Both are "requested but unforecast", which
    is worth an operator record either way; distinguishing them would require the metadata channel D8 excludes.
  - **Reconciliation fires ONLY on a non-empty PROPER SUBSET** (reviewer blocker-fix). An empty mapping is *not*
    divergence: `{}` is today's legitimate no-op-NWP success (D3), and reconciling it would mark every station missing
    and alarm on a cycle that behaves exactly as it does on `main`. Divergence handling requires
    `0 < len(returned) < len(requested)`; `len(returned) == 0` keeps its existing semantics untouched.
  - The adapter additionally logs per contained HRU naming the HRU, variable and `ifs_type`, mirroring the existing
    `recap.pf_unavailable_control_only` precedent (`:862-868`). Event: `recap.hru_unavailable_contained`.
- **D5 — Auth and configuration errors stay fatal and uncontained.** `RecapAuthError` and `RecapConfigurationError`
  keep propagating; the flow hard-aborts on auth (`run_forecast_cycle.py:1086-1100`) because an expired or
  misconfigured key needs operator action, not a per-station skip. Containment applies **only** to the temporal
  `source_data_missing` signal.
- **D6 — The missing-polygon-column raise is NOT touched.** `_iter_long_rows` raises a batch-wide `AdapterError` when a
  resolved polygon lacks a response column (`:511-525`), deliberately, so a corrupt response cannot **silently** drop a
  station. Different cause (malformed response, not absent data). Re-classifying it belongs to Plan 151, and only on
  its new per-track path.
- **D7 — Relationship to the snow channel: analogous, deliberately stricter.** *(Reviewer minor-fix — the draft
  overstated this as "making IFS consistent with snow".)* `fetch_snow_forecast` also contains per `(HRU, variable)`
  and records gaps per HRU (`:953`, `:972-1009`), but it **ships whatever rows accumulated** and reports the gaps in a
  returned `unavailable` map. IFS deliberately does **not** ship partial-variable forcing (D2), because snow is an
  optional enrichment channel whereas precipitation and temperature are the model's required forcing. Snow is a
  **precedent for containment**, not for partial shipping.
- **D8 — Return type, Protocol, and flow contract are otherwise unchanged.** No new types, no `WeatherForecastSource`
  change, no per-station outcome model — those belong to Plan 151 Phase 3. This is the smallest change that stops one
  station's gap darkening the deployment.
- **D9 — The single-HRU cycle PROBE stays as-is. RESOLVED by D10, not deferred.**
  `_resolve_effective_cycle` probes **one** HRU — `resolved[0].hru_name` (`:784`) — via a real `ifs_forecast` request
  and raises `RecapDataUnavailableError` before the accumulation loop exists (`:791-796`) when no candidate falls
  within `max_cycle_age_hours`. The owner confirms (D10) that publication is global and per-HRU gaps should not
  occur, so **one HRU's answer is representative and probe fallback across HRUs is not needed**. Two consequences are
  recorded rather than fixed:
  - If the *probe* HRU is itself the faulty one, the probe reports "no cycle published" and the whole cycle degrades —
    the correct outcome under D10 (something is broken) but with a **misleading diagnostic**, since the record says
    cycle-wide non-publication rather than "probe HRU faulty". Acceptable for now; revisit if it is ever observed.
  - **T1's tests must stub the probe explicitly** so they exercise the accumulation loop rather than passing
    accidentally.

## Phases

- **T1 — Per-HRU containment with all-or-nothing HRU commit (D1–D6) + docs.**
  - **In scope:** `src/sapphire_flow/adapters/recap_gateway.py` — per-HRU exception containment, an HRU-local staging
    buffer merged into the shared accumulator only when that HRU has its complete variable set, provenance committed
    on the same boundary, the three-way committed/discarded/empty outcome, and the contained-gap **application-log**
    event (a log line, distinct from the CRITICAL `pipeline_health` record D4 requires — the two destinations
    serve different audiences per `docs/standards/logging.md`).
    `src/sapphire_flow/flows/run_forecast_cycle.py` — requested-vs-returned station reconciliation **restricted to a
    non-empty proper subset**, the `NWP_DELIVERY` **CRITICAL** `pipeline_health` record naming the affected
    **stations**, and the partial flag into `_forecast_cycle_health` → DEGRADED (D4). Add the event to `docs/standards/logging.md` and
    the entry to `docs/plans/README.md`.
  - **Red-first acceptance tests** (extend `tests/unit/adapters/test_recap_gateway.py`; each stubs the cycle probe so
    D9 cannot mask the behaviour under test):
    1. **Two HRUs, A discarded, B complete** → returns B's stations, not A's; no raise. **Fails today.**
    2. **Order independence** — same scenario with A and B swapped. **Fails today in both orders**; this variant
       specifically locks preservation when the failure lands *after* successful accumulation.
    3. **The multi-station-per-HRU invariant holds** — for two stations sharing one HRU, a discarded HRU yields
       **neither**, and a successful HRU yields **both** (never a subset), locking D2's "all stations in an HRU share
       the fetch outcome" reasoning. Paired with an unchanged-behaviour assertion that a **missing polygon column
       still raises batch-wide `AdapterError`** naming the affected station (D6), which is what makes station-level
       partiality unrepresentable here — the existing `TestMissingPolygonColumnBatch` contract must not regress.
    4. **Partial forcing is never returned** — an HRU whose precipitation succeeds but whose temperature control
       fails contributes nothing; with that HRU alone the call re-raises.
    5. **Total unavailability unchanged** — every HRU discarded → `RecapDataUnavailableError` still propagates, so
       `_fetch_nwp_task` still returns `nwp_unavailable=True` and the cycle still degrades to runoff-only.
    6. **Well-formed empty is not a failure** — all HRUs return empty, none discarded → returns `{}` with no raise,
       exactly as today; distinct from case 5.
    7. **The decisive mixed case** (reviewer major-fix) — **one empty-success HRU PLUS one discarded HRU, with zero
       row-producing commits** → `RecapDataUnavailableError` **is raised**. Tests 5 and 6 both pass against an
       implementation that returns `{}` whenever any HRU merely *succeeded*; only this case pins D3's actual rule
       ("no HRU **committed** AND at least one discarded → raise"), which is the empty-masks-total-loss bug itself.
    8. **Single-HRU deployment unchanged** — today's production shape yields the same returned result / raised
       exception and the same flow outcome. (Not byte-identical *logging*: D4 adds new observable output by design.)
    9. **Auth is not contained** — `RecapAuthError` propagates immediately; no partial result.
    10. **`pf` containment unchanged** — the "fc present, pf absent" window still yields control + accumulated pf
       members; no regression to Plan 127 Fix 1.
    11. **Provenance** — a discarded HRU cannot determine the returned results' `cycle_time`.
    12. **Flow-level divergence** (`tests/unit/flows/test_run_forecast_cycle.py`): with one station returned and one
        missing, the complete station **is persisted and forecast**, the missing station falls back locally, a
        queryable `NWP_DELIVERY` **CRITICAL** `pipeline_health` record names the affected **station**, and cycle health
        is **DEGRADED** (D4) — locking "serve healthy, alarm loudly". **Plus the boundary case:** an **empty** mapping
        records **no** divergence record and does **not** alarm, proving reconciliation is restricted to a non-empty
        proper subset.
    13. **A contained gap logs** `recap.hru_unavailable_contained` with HRU name, variable **and `ifs_type`** (D4).
  - **Test soundness:** tests 1–2 must be shown failing against unmodified `fetch_forecasts`; test 4 against a naïve "any rows → return" implementation. State which baseline
    each was proven against.
  - **Exit gate:** `uv run pytest tests/unit/adapters/test_recap_gateway.py tests/unit/flows/test_run_forecast_cycle.py -q`
    + full `uv run pytest -q` + `uv run pyright` + `uv run ruff check`.

## Phase dependency graph
```json
{
  "phases": [
    { "id": "T1", "tasks": ["T1"], "parallel": false }
  ]
}
```

## Non-goals
- **Probe FALLBACK across HRUs** — not needed: publication is global, so one HRU's answer is representative (D9/D10).
  The probe is unchanged.
- **Requirement-driven probe/fetch variable selection** — deferred to its own plan (see Follow-ons).
- **Any per-station outcome modelling** (`StationTrackOutcome`, `CandidateFetchResult`) — Plan 151.
- **The missing-polygon-column batch-wide raise** (D6) — unchanged.
- **The snow channel** — already contained (D7).
- **MeteoSwiss** — different adapter, different failure model.

## Resolved questions
- **D10-probe-representativeness — RESOLVED (owner-answered 2026-08-12).** The question was whether the single-HRU
  cycle probe's answer holds for every other HRU. Answers, which now govern D4, D9 and the follow-on below:
  1. **Publication is global.** A new NWP fetch covers all HRUs; optimally every HRU uses the **same** cycle. A
     forecast present for one HRU and absent for another **within one deployment indicates a problem with that HRU or
     with the Data Gateway** — an anomaly, not a timing gap — and **must fail loudly** (see D4 for what "loudly"
     means operationally: healthy basins are still served, the fault is raised as a CRITICAL health record plus an ERROR log).
  2. **Per-HRU gaps are not expected.** No routine case where one HRU legitimately lacks a cycle others have.
  3. **Required variables are hard requirements.** If an operational model subscribes to `tp`, `tp` must be present or
     it is a **hard failure**. If no operational model subscribes to `tp`, the probe must check a different variable —
     hence the requirement-driven variable selection recorded as a follow-on below.
  **Consequences:** probe fallback across HRUs is **not** needed (D9 resolved, probe unchanged); divergence handling
  is an alarm path rather than a routine-degradation path (D4); and answer 3 becomes a **follow-on plan** rather than
  part of this fix (below). One residual is recorded in D9: a faulty *probe* HRU produces a correct outcome with a
  misleading diagnostic.
## Follow-ons
- **Requirement-driven IFS variable selection** *(from the owner's D10 answer 3; removed from this plan's scope after
  review showed it is materially larger than a "small generalisation").* Two hardcoded assumptions that `tp` is always
  required: the probe variable `_PROBE_VARIABLE = "tp"` (`:376`), and `_ifs_variables()`, which returns **every**
  IFS-capable variable unconditionally (`:673-674`) so `fetch_forecasts` always fetches `tp` **and** `2t`. Both are
  correct today only because the shipped FI models require precipitation (`models/nwp_regression.py:223`, shared by
  all three entry points at `pyproject.toml:141`), which maps to `tp` (`RECAP_VARIABLES`, `:75-90`).
  **The owner's rule to implement:** if an operational model subscribes to `tp`, `tp` must be present or it is a hard
  failure; if no operational model subscribes to `tp`, the probe must check a different variable.
  **Why it is not in Plan 154** — the constraints a proper design must resolve:
  - **No data path exists today.** `fetch_forecasts(station_configs, cycle_time)` (`protocols/adapters.py:24`) gets no
    model or required-variable information, and `StationWeatherSource` carries none (`types/station.py:103-109`).
    Threading requirements in means a new capability seam — the precedent is `SnowForecastSource.fetch_snow_forecast`,
    which takes `required_snow: Mapping[StationId, frozenset[str]]` (`protocols/adapters.py:60`) — **not** a change to
    the base `WeatherForecastSource`, which Plan 154 explicitly does not touch (D8).
  - **Group requirements are discovered too late.** Group runs and active group assignments resolve in Phase B2
    (`flows/run_forecast_cycle.py:2316`, `services/run_group_forecast.py:218`), after the NWP fetch. Scoping the fetch
    to station-assignment requirements alone could **starve a future-dynamic `GroupForecastModel`** that today's
    unconditional fetch serves. The analogous snow path has the same gap and deferred it (`_compute_required_snow`
    excludes group requirements — see Plan 151 D25), so deferring here is consistent, not novel.
  - It also needs an explicit canonical-feature → Recap-variable mapping and a backward-compatible story for existing
    direct adapter callers.
  **Not urgent:** no live defect — the required set is `{tp, 2t}`, exactly what is fetched now. It becomes real when a
  deployment's models stop requiring precipitation.

## Cross-plan notes
- **Relationship to Plan 151.** Plan 151 D7 requires exactly this containment for its per-track path and currently
  carries it inside its own T4. Landing 154 first shrinks 151's T4 and de-risks it; landing 151 first would leave 154
  still needed on the legacy path, which persists until Phase 4. They do not conflict. Recommended order: **154
  first** — small, independently valuable, testable today.
