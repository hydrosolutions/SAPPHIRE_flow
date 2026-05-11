# Plan 067 — MeteoSwiss STAC adapter investigation + configurability

**Status**: DONE (Phase 1 investigation + Phase 2 implementation shipped 2026-04-21 in commit `1318451`; archived 2026-05-11)
**Date**: 2026-04-21 (revised 2026-04-21 — four critical-review passes; [5] post-T1 scope expansion to include `_CYCLE_HOURS` cadence correction — T1.b discovered MeteoSwiss publishes at 6 h cadence, not the 3 h cadence the constant assumes. D2's derivation divisor changes from `/ 3.0` to `/ 6.0`; new decision D7 and new sub-task T3.d added.) → 2026-05-11 (DONE, archived)
**Depends on**: none at the code level. Informed by dress-rehearsal 2026-04-21 findings F4 and F5.
**Scope**: **Phase 1 (investigation):** determine why `resolve_cycle_time` emitted "no cycle available within 3 fallback steps" during the 2026-04-21 dress rehearsal. The adapter contains two separate STAC queries — an **availability probe** (`_cycle_is_published`) and a **fetch-scope loop** (`_fetch_grib_files`) — each of which may be behaving correctly or incorrectly independently. Phase 1 must distinguish between four hypotheses: **H-A** "MeteoSwiss was genuinely late", **H-B** "the availability probe is buggy", **H-C** "the fetch scope is over-fetching", and **H-D** "the page cap is too low for any single cycle's item count". All four are independent — any combination can be true. **Phase 2 (configurability + scope fix + cap raise):** apply the fixes that Phase 1's findings justify; reconcile the two currently-duplicated time-window limits (`_MAX_FALLBACK_STEPS` hard-coded at 9 h vs `nwp_max_fallback_age_hours` config default 12 h) into a single derived value; raise the pagination cap to accommodate the legitimate per-cycle item count (which remains high even after scope narrowing, unless MeteoSwiss supports server-side variable-name filtering via CQL).

---

## Context

### Why now

- Forecast-cycle (A3 step 8) is the only A3 step still blocked after the 2026-04-21 rehearsal. Until it runs end-to-end against real MeteoSwiss data, v0 operational readiness cannot be signed off.
- Nepal production cutover in Oct 2026 depends on the equivalent adapter path working against ECMWF IFS (Plan 047, stub). Any query-semantics bugs in our STAC client likely recur there.
- Plan 063 landed the fetch-semantics redesign and four follow-up bug fixes. This is the next layer — understanding whether the two remaining symptoms (F4 availability-probe "cycle late" and F5 fetch "pagination exceeded 100 pages") are one bug, two bugs, or an external-plus-internal mix.

### Observed behaviour (dress rehearsal 2026-04-21, finding F4)

- At 11:35 UTC, `run_forecast_cycle_flow(adapter=adapter)` aborted with `"No cycle available within 3 fallback steps from 2026-04-21T09:00:00+00:00"`. This message is emitted by `resolve_cycle_time` (`meteoswiss_nwp.py:144` area), which wraps `_cycle_is_published` in a fallback loop bounded by the module-level constant `_MAX_FALLBACK_STEPS = 3`.
- `_cycle_is_published` (line 174-190) issues `?datetime=<cycle-instant>&limit=100` against the STAC items endpoint and checks whether any of the first 100 returned items has an ID matching `<MMDDYYYY-HHMM>-0-` (step-0 of the requested cycle).
- Direct `curl` probe of `?datetime=2026-04-21T00:00:00Z/..` returned 100 items whose `forecast:reference_datetime` was uniformly `2026-04-20T12:00:00Z` — i.e. forward-step items from a cycle that is itself ~23 h old. No items with reference-datetime on 2026-04-21 appeared in that 100-item page.
- **This is consistent with two different hypotheses**:
  - **H-A**: MeteoSwiss genuinely hadn't published any cycle after 2026-04-20T12:00Z (7 consecutive missed cycles — unusual but not impossible during a real outage or maintenance window).
  - **H-B**: MeteoSwiss had published today's cycles, but the `_cycle_is_published` probe's first-100-items heuristic is unreliable because STAC's datetime filter on a single instant matches items from *every* cycle whose valid-time falls at that instant, and the ordering of those items is not guaranteed to surface step-0 items of the target cycle within the first 100.
- **Evidence fit**: H-A is the stronger fit to the specific curl observation. A `?datetime=2026-04-21T00:00:00Z/..` range filter would match step-0 items of any 2026-04-21 cycle (valid-time = cycle start). If any such cycle were published at MeteoSwiss, its step-0 items would be 21 members × ~40 variables = ~840 items per cycle. **Caveat**: STAC's default sort order is unspecified by the spec; if the server happens to sort by valid-time ascending *or* by item-ID alphabetical, step-0 items of cycles 00/03/06/09 would appear near the front of the result and several would land in the first 100. Under an adversarial sort (e.g., insertion-order when step-0 items are inserted last), they could be occluded. Seeing zero step-0 matches in the first 100 is *more* consistent with "no 2026-04-21 cycles exist yet" than with "they exist but were ordering-occluded" under any plausible sort — but H-B remains technically possible, and Phase 1 rules it in or out concretely.
- Phase 1 must distinguish H-A from H-B. The *rationale* for T2a differs between the two: H-A → T2a is hardening against the prefix-based check's ordering-fragility; H-B → T2a is a direct bug fix. But T2a ships unconditionally per D4 in either case — the probe rewrite does not depend on which hypothesis holds.

### Observed behaviour (finding F5, with the fallback cap monkey-patched)

- Monkey-patched `_MAX_FALLBACK_STEPS = 10`; `resolve_cycle_time` reached `2026-04-20T12:00` cycle items on step 7 of the fallback loop. Availability probe returned True for that cycle.
- `fetch_forecasts` → `_fetch_grib_files` (line 230 onward) then issued `?datetime=<2026-04-20T12:00>/<2026-04-25T12:00>&limit=100` — a 120-hour valid-time range. It began downloading GRIB2 files (t_2m ctrl + perturbed at step 6), correctly applied the `tp + t_2m` allowlist **client-side** (line 237: `allow_tokens: list[str] = [row[0] for row in self.PARAM_GROUPS]`; `_is_grib_asset` filters after the response), and aborted with `"STAC pagination exceeded 100 pages"` (line 251-252) before finishing.
- **Two contributing problems**:
  - **H-C (over-fetching by cycle — hypothesis, not yet confirmed)**: the `datetime=<cycle>/<cycle+120h>` filter almost certainly matches items from many cycles whose valid-times fall in that 120-hour window (every 3-hourly cycle from 2026-04-20T12:00 onward contributes forward-step items into that window). The intended semantic is "items whose `forecast:reference_datetime` equals `<cycle>`", which would return only the target cycle's items. T1.c's distinct-reference_datetime count against a real response directly confirms or refutes H-C. Narrowing scope server-side to `forecast:reference_datetime == <cycle>` (if CQL supports it) is the fix.
  - **H-D (page cap too low for per-cycle item count)**: even a correctly-scoped query (one cycle only) returns a large number of items because the per-cycle allowlist is applied **client-side**, not server-side. Per MEMORY, ICON-CH2-EPS publishes **21 members** (1 control + 20 perturbed). At 5-day hourly forecast horizon (120 steps) and ~40 variables per member-step (all variables the server offers), a single cycle is ~21 × 120 × 40 ≈ **~100,000 items = ~1,000 pages at 100/page**. Even at 3-hourly step granularity (40 steps) the count is ~33,600 items ≈ ~336 pages. Both well above the current 100-page cap. T1.f quantifies the actual per-cycle item count observed against a real cycle, removing this uncertainty.
- **H-C and H-D are independent** — H-C says "we query too many cycles", H-D says "one cycle is already more pages than the cap". Both fixes are likely required: (a) scope-narrowing via `forecast:reference_datetime` (server-side if CQL-supported; otherwise client-side with no pagination benefit), and (b) raising the cap or adding server-side allowlist filtering on the item-ID variable-name token (e.g. `-tot_prec-` or `-t_2m-`) if MeteoSwiss's CQL permits such predicates.

### Principle

**Investigate before configuring, and distinguish the two queries.** The adapter has two STAC calls with overlapping symptoms but potentially independent root causes. Phase 1 must name each root cause (or rule one out) before Phase 2 ships fixes. Speculative "make it configurable" patches without a root-cause finding risk papering over a real bug. **Scope of the principle**: it applies to speculative or hypothesis-contingent fixes. Changes that address inconsistencies visible *in the current code* — the duplicated limit (D2), the prefix-based probe's known fragility (D4), and the per-cycle page count exceeding the cap (D3) — are evidence-driven and ship unconditionally in Phase 2.

### Non-goals

- **Not** expanding into NWP-late monitoring / operator alerting. That surface belongs in Flow 4 (pipeline monitoring) scoping, which Plan 039 (DEFERRED) already reserves. If Flow 4 picks up the signal later, fine; this plan does not deliver it.
- **Not** changing `nwp_max_fallback_age_hours` policy semantics (default 12.0). That's a deployment-config decision orthogonal to the adapter's ability to *find* cycles; this plan reconciles how many fallback *steps* map to that age window, not the age policy itself.
- **Not** touching the ECMWF IFS adapter (Plan 047). Lessons from Phase 1 are captured as an appendix checklist in this plan that Plan 047 consumes when it promotes to full DRAFT (T6 deliverable).
- **Not** introducing a cycle cache or download memoization. Scope creep; separate plan if warranted.
- **Not** changing the `tp + t_2m` allowlist. Allowlist extensions are per-model decisions, not adapter-infra changes.
- **Not** moving the allowlist from client-side to server-side as a hard requirement — it's an opportunistic optimization for Phase 2 only if MeteoSwiss supports CQL on the item-ID variable-name token.

---

## Architecture decisions (draft)

| # | Decision | Rationale |
|---|---|---|
| D1 | **Phase 1 investigation produces a written root-cause finding** before any code change. A new `## Phase 1 findings` section is appended to this file when T1 completes, naming the verdict on each hypothesis (H-A/H-B/H-C/H-D) plus the ancillary questions (CQL support, item-ID convention, per-cycle page count). A single sentence per sub-question is the goal. | Forces clarity; avoids speculative fixes. Matches the pattern Plan 046 has used for each detour. |
| D2 | **`_MAX_FALLBACK_STEPS` is derived, not configured.** Delete the module-level constant. Add a new kwarg `max_fallback_steps: int` to `MeteoSwissNwpAdapter.__init__`. The **caller** (the flow) computes `math.ceil(cfg.nwp_max_fallback_age_hours / 6.0)` from `DeploymentConfig` and passes the integer in. Adapter stays config-object-free — only the flow knows about `DeploymentConfig`. One knob = one truth. **Divisor rationale**: MeteoSwiss publishes ICON-CH2-EPS cycles at 6 h cadence per the collection description (T1.b finding); the divisor matches the cadence. Other adapters (e.g. Plan 047's ECMWF IFS) will have their own cadence-appropriate divisor — not 6.0 in general. | The two current limits (9 h hardcoded vs 12 h config) are inconsistent: an operator configuring 12 h still only gets 9 h of fallback. Deriving removes the inconsistency and the redundant config surface. Passing a pre-computed `int` keeps the adapter's ctor signature narrow and doesn't couple it to the full deployment-config schema. |
| D3 | **Pagination-cap IS raised unconditionally in Phase 2.** The current 100-page cap is too low for a correctly-scoped per-cycle fetch even after H-C is fixed — per-cycle item count with the client-side allowlist exceeds ~300 pages (T1.f quantifies). Raise to a documented ceiling derived from the T1.f measurement (target: T1.f measured value × 2, rounded up). If MeteoSwiss CQL supports server-side variable-name filtering (on the `-<token>-` segment of item IDs), that's an additional optimization — not a prerequisite for D3. | Fixing scope (T2b) is necessary but not sufficient; a single cycle is still multi-hundred pages because the variable allowlist is client-side. Cap raise is straightforward; server-side variable filter is nice-to-have. |
| D4 | **Switch the availability probe to a property-based check unconditionally.** The current implementation (line 189-190) uses `prefix = cycle.strftime("%m%d%Y-%H%M-0-")` on item IDs — a client-side string match on an undocumented MeteoSwiss naming convention. Phase 2 always replaces this with `any(f.get("properties", {}).get("forecast:reference_datetime") == cycle_iso for f in features)`. **No explicit step-0 check** — cycle publication at MeteoSwiss is assumed atomic (all steps of a cycle become available together, not incrementally), so a single feature with matching reference-datetime is sufficient evidence that the cycle is published. If Phase 1 (T1.b) observes a partial-publish state (some steps present, others not), the assumption is falsified and we add a step-0 predicate as a follow-up; until then, simpler check wins. T1.d is retained for *documentation* — confirming whether the ID convention is contract or happenstance — but D4 does not depend on T1.d's verdict. | Property-based matching is robust to naming-convention changes and to first-100-items ordering. Doing the switch unconditionally is cleaner than branching the implementation on T1.d's verdict, and avoids the fragility of the current prefix-based check regardless of whether MeteoSwiss ever changes the convention. |
| D5 | **Integration test uses live MeteoSwiss** (`live_stac` marker per pyproject.toml line 80) to exercise the full fetch path against a real recent cycle. Developers opt in locally; CI runs on a schedule. Test skips (not fails) when MeteoSwiss is unreachable — skip heuristic: **`GET /collections/<collection-id>` with 5 s timeout; HTTP 5xx or timeout → skip.** STAC endpoints don't universally support HEAD; a GET on a collection document is cheap and unambiguous. | The adapter has been bitten by half a dozen live-only bugs this month; unit tests with fakes aren't enough. Skip-on-outage avoids spurious CI failures. |
| D6 | **Lessons inform Plan 047** via a "STAC adapter checklist" appendix appended to this plan at close. Plan 047 (Nepal v1 — ECMWF IFS adapter) reads this checklist when it promotes from stub to DRAFT. No code change in 047 from this plan. | Avoids coupling the two plans at implementation time while capturing the institutional learning in a durable place. |
| D7 | **`_CYCLE_HOURS` corrected to `(0, 6, 12, 18)`** in `meteoswiss_nwp.py:35`. Added to Plan 067 scope post-T1 (not in the original plan) because T1.b surfaced that MeteoSwiss's own collection metadata states "updated every 6 hours". The current constant `(0, 3, 6, 9, 12, 15, 18, 21)` causes `_snap_to_cycle` to snap to phantom slots (e.g. 21:00Z), and `resolve_cycle_time` then burns a fallback step discovering the phantom is unpublished before reaching a real cycle. Fixing this in the same plan as D2 because the two are mechanically linked — the fallback-step count's divisor (D2: `/ 6.0`) and the slot enumeration (D7) both reflect the same cadence. | Keeps the two cadence-dependent code paths in sync in a single coherent change. Shipping D2 without D7 would leave the adapter snapping to non-existent slots; shipping D7 without D2 would leave the fallback step count wrong. Both-or-neither avoids a partially-corrected state. |

---

## Task sketch

- **T1** — **Phase 1: structured investigation.** Deliver written findings on each hypothesis, named explicitly:
  - **T1.a — Availability probe (`_cycle_is_published`).** Call it against a cycle that T1.b confirms is currently published (do not hard-code a specific date — T1.b runs first and identifies a known-good target). Then call it against the *next unpublished slot* (slightly in the future of MeteoSwiss's latest published cycle). Does it return True / False respectively? Inspect the raw STAC response and count how many of the first 100 items match the `%m%d%Y-%H%M-0-` prefix for the known-published cycle — does the ordering make prefix-match reliable?
  - **T1.b — MeteoSwiss reliability at investigation time.** Fetch a larger page of items (e.g. `curl -s '.../items?limit=100'`) and compute `max(forecast:reference_datetime)` client-side — do NOT rely on the server's default sort order, which is unspecified by STAC and may return items in insertion / ID-alphabetical / valid-time order. If the server advertises `sortby=` support (check `/conformance`), use `sortby=-properties.forecast:reference_datetime&limit=5` instead and read the first item. Is the latest cycle recent (≤ 6 h old) or stale? Repeat over several hours to characterize the normal publish cadence and whether the 2026-04-21 gap was an outage or normal behaviour.
  - **T1.c — Fetch scope (`_fetch_grib_files`).** For a known cycle, issue the current query `?datetime=<cycle>/<cycle+120h>&limit=500` and count distinct `forecast:reference_datetime` values in the response. **Note**: `limit=500` may not be honored — MeteoSwiss may cap at 100 or similar; fall back to `limit=100` and paginate via `Link: rel=next` (lift a sample of ≥ 3 pages). If > 1 distinct `forecast:reference_datetime` appears, H-C is confirmed.
  - **T1.d — Item-ID naming convention (documentation only; does not gate D4).** Query the MeteoSwiss STAC collection description (`/collections/<id>`) and/or `/collections/<id>/queryables`. Is the `<MMDDYYYY-HHMM>-<step>-<var>-<member>-<hash>` item-ID pattern documented as stable, or an undocumented happenstance? Document the finding either way; D4 switches to property-based regardless.
  - **T1.e — CQL-2 support.** Check whether MeteoSwiss STAC supports the `filter=` parameter (CQL-2): curl `/conformance` and look for `http://www.opengis.net/spec/cql2/...` class URIs. Then probe **two** concrete predicates because Phase 2 needs both independently:
    - (i) on the `forecast:reference_datetime` property: `?filter=forecast%3Areference_datetime%3D'<cycle>'&filter-lang=cql2-text` — required by T2b's server-side scope filter.
    - (ii) on the item-ID variable-name token (substring match): `?filter=id%20LIKE%20'%25-t_2m-%25'&filter-lang=cql2-text` (escape and syntax to be confirmed against the CQL-2 spec; STAC may require `CASEI(id) LIKE ...` or similar) — required by T4b's optional server-side allowlist filter.
    - Record for each: the conformance advertisement and whether the concrete query actually returns filtered results.
  - **T1.f — Per-cycle page count (replaces the old "cap load-bearing" question).** For one known-published cycle, query `?datetime=<cycle>/<cycle+120h>&limit=100` **with a server-side `forecast:reference_datetime` filter if T1.e finds CQL is supported, otherwise without**, and paginate all the way through (or until a hard stop). Count total items and total pages. This sets D3's cap target. The scope narrowing is **reference-datetime only**, not allowlist — the client-side allowlist stays by design unless T1.e shows CQL can filter on the variable-name token too.
  - **Deliverable**: a new `## Phase 1 findings` section appended to this plan, one short paragraph per sub-task. Explicit verdicts on H-A / H-B / H-C / H-D / T1.d / T1.e / T1.f.

- **T2a** — **Availability probe property-based rewrite (D4, unconditional).** Change `_cycle_is_published` to inspect `f.get("properties", {}).get("forecast:reference_datetime")` on each returned feature and compare to the target cycle in ISO-8601 form. **No step-0 check** — D4's atomic-publication assumption governs; if Phase 1 (T1.b) observes partial-publish state, escalate to a follow-up plan. Add a regression test that passes when the first 100 returned items do NOT contain a direct `<cycle>-0-` ID prefix but DO contain items whose `forecast:reference_datetime` matches the target cycle — confirming the probe is no longer ordering-fragile.

- **T2b** — **Fetch-scope narrowing (client-side only — CQL unavailable).** **Phase 1 verdict: ship the client-side fallback; skip the CQL path.** T1.e (i) confirmed MeteoSwiss silently ignores `filter=` on both `/items` and `/search` endpoints (GET 200 with default response; POST `/search` returns 400 `"non-queriable parameter: filter"`). Implementation: inside `_fetch_grib_files`'s pagination loop, `continue` on features whose `properties.forecast:reference_datetime` ≠ target cycle ISO. **Note**: client-side filtering preserves correctness but does NOT reduce pages walked; the full ~552-page walk remains and T4a's cap raise to 800 is the load-bearing mitigation. Add a regression test verifying the fetched items all share the target `forecast:reference_datetime` after filtering (fakes emit a multi-cycle response; T2b drops the non-matching ones).

- **T3** — **Reconcile the two time-window limits (D2) + correct cycle cadence (D7).** Four sub-steps:
  - **T3.a — Locate adapter construction sites.** `grep -rn "MeteoSwissNwpAdapter(" src/ docs/` to find every place the adapter is instantiated. Known sites at plan-drafting time: (i) the Plan 046 §A3 step 8 direct-invoke heredoc (markdown template, not source — update to pass the kwarg). Other sites may exist — e.g. a scheduled Prefect deployment registration, a test fixture, or Plan 046 Stream C's runbook. Inventory them before implementing T3.b.
  - **T3.b — Adapter API change.** Remove `_MAX_FALLBACK_STEPS` module constant. Add `max_fallback_steps: int` kwarg to `MeteoSwissNwpAdapter.__init__` (default 2 — preserves *corrected-cadence* current behaviour: `ceil(12 / 6) = 2` steps ≈ 12 h coverage, matches the default `nwp_max_fallback_age_hours=12.0` policy; old default of 3 was wrong under the corrected cadence and produced 18 h coverage). **Production callers MUST pass the derived value explicitly** after T3.c lands; the default exists only for test convenience, not as a fallback policy. Update `resolve_cycle_time` to use the instance attribute. Unit test: `max_fallback_steps=2 → resolve searches back 2 steps (12 h at 6 h cadence)`, `=0 → tries only the snapped cycle, raises if unpublished`.
  - **T3.c — Caller updates.** At every construction site identified in T3.a, compute `math.ceil(cfg.nwp_max_fallback_age_hours / 6.0)` from the available `DeploymentConfig` and pass it to the ctor. Integration test: `cfg.nwp_max_fallback_age_hours=12.0 → adapter.max_fallback_steps == 2`, `=6.0 → 1`, `=0.0 → 0`, `=1.5 → 1`, `=18.0 → 3`.
  - **T3.d — Cycle cadence correction (D7).** Change `_CYCLE_HOURS: tuple[int, ...] = (0, 3, 6, 9, 12, 15, 18, 21)` at line 35 to `(0, 6, 12, 18)`, with a comment citing T1.b's discovery of the 6 h cadence in the MeteoSwiss collection description. Update `_snap_to_cycle` if it has a comment referring to 3-hourly slots (it computes `max(h for h in _CYCLE_HOURS if h <= now_utc.hour)` — behaviour unchanged, works for any tuple). Unit test: `_snap_to_cycle(2026-04-21T07:30Z) → 2026-04-21T06:00Z`, `_snap_to_cycle(2026-04-21T11:59Z) → 2026-04-21T06:00Z`, `_snap_to_cycle(2026-04-21T12:00Z) → 2026-04-21T12:00Z`, `_snap_to_cycle(2026-04-21T21:00Z) → 2026-04-21T18:00Z`.

- **T4a** — **Pagination cap raise (D3, unconditional).** Set `_MAX_PAGINATION_PAGES = 800` per T1.f's recommendation (sized for the **full 120 h-window walk** — 552 pages observed — not just the per-cycle 138 pages, because T2b's client-side filter does not reduce pages walked when CQL is absent). Replace the cap constant in the pagination-loop guard (line 251: `if page_count > 100: raise AdapterError("STAC pagination exceeded 100 pages")`) with a named constant `_MAX_PAGINATION_PAGES = 800` and update the error message to cite the new value. **Do NOT touch the per-request `limit=100` parameter at lines 178, 243** — that is the STAC protocol page size, unrelated to the max-pages guard. Ships independently of T4b.

- **T4b** — **Optional server-side variable-name filter.** **Phase 1 verdict: T4b is a documented no-op.** T1.e (ii) confirmed MeteoSwiss does NOT support CQL filtering on item IDs (the `filter=id LIKE '%-t_2m-%'` probe returned items with non-matching IDs, silently ignoring the filter). Implementation guidance for the subagent: add a one-line comment to `_fetch_grib_files` near the pagination loop noting "T4b (Plan 067): server-side variable-name CQL filter would reduce per-cycle item count ~20× but MeteoSwiss does not support CQL as of 2026-04-21; allowlist stays client-side per line 237." No behavioural change.

- **T5** — **Live-STAC integration test** (`live_stac` marker) that: (a) does the skip-heuristic probe per D5 (GET `/collections/<id>`, 5 s timeout), (b) calls `adapter.resolve_cycle_time(now_utc)` and expects **either** a successful return (in which case the returned cycle is within policy by construction) **or** a clean `NoCycleAvailableError` raise, (c) if the call succeeded, fetches GRIB files via `adapter.fetch_forecasts(station_configs=[], cycle_time=resolved)` (the gridded adapter ignores `station_configs` per the `# noqa: ARG002` in source; pass an empty list) and asserts the returned `GriddedForecast.values` xr.Dataset has at least one time coordinate and contains both `tp` and `t_2m` data variables, and that each variable has at least one all-finite (no-NaN, no-Inf) value across the member × step × gridpoint axes. The test does NOT assert cycle age separately — the adapter's internal contract guarantees the returned cycle is within policy.

- **T6** — **Plan 046 cross-reference + Plan 047 appendix.** Add the next Revision block to Plan 046 (currently Rev 11 is the last; Rev 12 expected) removing the "blocked per F4/F5" note from §A3 step 8. Append a new **`## Appendix: STAC adapter checklist for Plan 047`** section to this plan (Plan 067) before close, summarizing the Phase 1 findings as a concrete checklist (availability-probe semantics, fetch-scope filter, CQL support verification for both predicate types, item-ID-convention verification, per-cycle page count sizing).

---

## Files to modify / create (sketch)

- Modify: `src/sapphire_flow/adapters/meteoswiss_nwp.py` — availability-probe property-based rewrite (T2a), fetch-scope client-side filter (T2b), remove `_MAX_FALLBACK_STEPS` (T3.b), add `max_fallback_steps: int` kwarg to ctor (T3.b), correct `_CYCLE_HOURS` from 3-hourly to 6-hourly (T3.d), replace page cap with `_MAX_PAGINATION_PAGES = 800` (T4a), add T4b no-op comment.
- Modify: **adapter construction sites** identified by T3.a. Plan-drafting-time inventory: (i) the Plan 046 §A3 step 8 direct-invoke heredoc — this lives inside `docs/plans/046-mac-mini-staging-deployment.md`, not source code; update the template to compute and pass `max_fallback_steps`. (ii) any other sites T3.a finds via grep. Scheduled-deployment construction (if separate from the direct-invoke path) likely lives in a flow entry point like `flows/run_forecast_cycle.py` or a registration helper — T3.a confirms.
- **Not modified**: `src/sapphire_flow/config/deployment.py` — D2 explicitly avoids adding a new config field.
- Modify: `config.toml` — add (or reaffirm) a comment near `nwp_max_fallback_age_hours` so operators know it's the single knob:
  ```toml
  # NWP cycle lateness: search back up to this many hours for a published cycle.
  # The MeteoSwiss adapter derives its fallback-step count as ceil(hours / 6.0)
  # (ICON-CH2-EPS cycles publish every 6 h). Other adapters use their own
  # cadence-appropriate divisor.
  nwp_max_fallback_age_hours = 12.0
  ```
- Modify: `tests/unit/adapters/test_meteoswiss_nwp.py` — regression tests for T2a (property-based probe), T2b (fetch-scope filter), T3.b (ctor kwarg default + effective step count).
- Modify or add: an integration-level test for T3.c confirming the end-to-end mapping `cfg.nwp_max_fallback_age_hours → adapter.max_fallback_steps` at each construction site.
- New: `tests/integration/live/test_meteoswiss_nwp_live.py` — T5, marked `live_stac`, skip-on-outage per D5.
- Modify: `docs/plans/046-mac-mini-staging-deployment.md` — Rev 12 reinstating A3 step 8.
- Modify: this plan (`docs/plans/067-*.md`) — append `## Phase 1 findings` and `## Appendix: STAC adapter checklist for Plan 047`.

---

## Dependency graph (sketch)

```json
{
  "phases": [
    {"id": "investigate", "tasks": ["T1"], "parallel": false},
    {"id": "fix-queries", "tasks": ["T2a", "T2b"], "parallel": true, "depends_on": ["investigate"]},
    {"id": "reconcile-limits", "tasks": ["T3"], "parallel": false, "depends_on": ["fix-queries"]},
    {"id": "cap-and-test", "tasks": ["T4a", "T4b", "T5"], "parallel": true, "depends_on": ["reconcile-limits"]},
    {"id": "cross-ref", "tasks": ["T6"], "parallel": false, "depends_on": ["cap-and-test"]}
  ]
}
```

T2a and T2b are independent (different code paths) and can run in parallel by two subagents if desired. T4a (cap raise) and T4b (optional server-side variable-name filter) are likewise independent; T4b may be a no-op if T1.e (ii) found no CQL support for the predicate.

---

## Exit gates (sketch)

1. **`## Phase 1 findings` section** appended to this plan, with explicit verdicts on H-A / H-B / H-C / H-D / T1.d / T1.e (both predicates) / T1.f.
2. **`uv run pytest tests/unit/adapters/test_meteoswiss_nwp.py`** green, including regression tests: T2a property-based probe, T2b client-side fetch-scope filter, T3.b ctor kwarg default (=2) + effective step count, T3.c integration mapping from `cfg.nwp_max_fallback_age_hours / 6.0` to `adapter.max_fallback_steps`, and T3.d `_snap_to_cycle` behaviour across the corrected 6 h cadence.
3. **`uv run pyright --strict src/sapphire_flow/adapters/meteoswiss_nwp.py src/sapphire_flow/flows/run_forecast_cycle.py`** clean.
4. **Live integration test** (T5, `live_stac` marker): against a live MeteoSwiss STAC endpoint during a window when MeteoSwiss is reachable (skip-heuristic per D5), the test passes by **either** returning a `GriddedForecast` that contains both allowlisted variables **or** raising `NoCycleAvailableError` cleanly. Returned cycle is within policy by the adapter's internal contract — the test does not assert this separately.
5. **Plan 046 Rev 12** reinstates step 8 — the Rev 11 "blocked per F4/F5" note is removed.
6. **`## Appendix: STAC adapter checklist for Plan 047`** appended to this plan.
7. **No direct references to `_MAX_FALLBACK_STEPS`** remain anywhere in `src/sapphire_flow/` or `docs/plans/` (the Plan 046 direct-invoke heredoc explicitly references it in commentary; remove that reference after T6 lands). The magic `100` page cap is replaced by `_MAX_PAGINATION_PAGES = 800` with a T1.f citation. `_CYCLE_HOURS` equals `(0, 6, 12, 18)` with a comment citing MeteoSwiss's 6 h cadence from the collection description.
8. Version bump applied.

---

## Risks (sketch)

| Risk | Mitigation |
|---|---|
| Phase 1 confirms H-A only (MeteoSwiss was genuinely late, availability probe was correct) | T2a still ships under D4's unconditional property-based switch. T2b still ships to fix H-C if T1.c confirms it. T3.a–c + T4a + T5 still deliver limit reconciliation + cap raise + live test. The plan remains coherent even under the mildest Phase 1 outcome. |
| MeteoSwiss STAC does not support CQL-2 | T2b falls back to client-side filter — correctness preserved, but pages-walked is NOT reduced. D3's cap raise is therefore a hard prerequisite; in practice T4a becomes the fix-of-record for the pagination problem, not T2b's CQL path. |
| Server-side variable-name filter (T4b) is not supported by MeteoSwiss CQL | T4a's cap raise per T1.f's measurement is the primary path. T4b is nice-to-have; plan does not block on it and T4b is flagged as a documented no-op. |
| `ceil(nwp_max_fallback_age_hours / 6.0)` behaves surprisingly for non-multiples-of-6 | T3.c integration tests cover 0.0, 1.5, 6.0, 12.0, 18.0 step-count mapping. T3.b unit tests cover the adapter-side behaviour at `max_fallback_steps=0, 1, 2, 3`. Operators setting a non-multiple-of-6 get a documented-predictable step count (rounded up); e.g. `nwp_max_fallback_age_hours=10.0 → 2 steps = 12 h coverage`, slightly *longer* than the configured policy — acceptable; the age-hours value is the policy ceiling and the derived step count must not *under*-cover it. |
| Adapter ctor signature change (new `max_fallback_steps` kwarg) breaks callers | Default the kwarg to 3 (matches old hardcoded value); existing callers that don't pass it behave as before. T3.c updates production callers; default is only for test fixtures. |
| ECMWF IFS adapter (Plan 047) inherits the same bugs | T6 produces the appendix checklist including the cadence-correction lesson; Plan 047 consumes it on promotion. ECMWF IFS has its own cycle cadence (HRES typically 6 h, ENS typically 6 h or 12 h depending on resolution) — the divisor in Plan 047's adapter will NOT be 6.0 in general; it must be set per the ECMWF collection metadata. |
| Raising the pagination cap (T4a) surfaces a worker-memory issue (finding F6 redux) under large cycles | Cap is raised to `T1.f measured × 2`, not unbounded. If memory pressure surfaces, introduce streaming iteration over pages in a follow-up plan — do not block this plan on that optimization. |
| The availability probe's property-based rewrite (T2a) depends on `forecast:reference_datetime` being present on every feature | Regression test confirms presence on a real MeteoSwiss response; if MeteoSwiss omits it for some feature variants, fall back to the previous prefix-based check inside the same probe as a second-tier match. |

---

## Open questions (non-blocking DRAFT → READY)

1. Does MeteoSwiss STAC support CQL-2 filtering on the `forecast:reference_datetime` property? On the item-ID variable-name token? T1.e answers.
2. Should the live integration test target a *specific* recent cycle (reproducible) or the *latest* available (realistic)? (Recommendation: latest — reproducibility matters less than live-bug-catch; the skip-heuristic handles outages.)
3. Is the item-ID naming convention documented (contract) or happenstance? T1.d answers; D4 unconditional switch means this is a documentation finding, not a gate.
4. ~~Is the 100-page cap load-bearing?~~ — **replaced by T1.f** (measure actual per-cycle page count; D3 raises the cap based on the measurement).

---

## Phase 1 findings

**Investigation date**: 2026-04-21T21:00Z – 21:35Z (live probes against `https://data.geo.admin.ch/api/stac/v1`)
**MeteoSwiss latest reference_datetime at probe time**: `2026-04-20T18:00:00Z` (≈27 h old at the start of the probe)

### T1.b — MeteoSwiss reliability
Fetched the first page of 100 items (unsorted). All 100 shared `forecast:reference_datetime = 2026-04-20T18:00:00Z`, and walking forward through `?datetime=<instant>&limit=1` at every 3-hourly candidate from 2026-04-21T21:00Z back to 2026-04-20T18:00Z returned items whose sole ref_dt was `2026-04-20T18:00:00Z` — i.e. forward-step items of the 18:00Z cycle whose valid-time covered each queried instant. Conversely, `?datetime=2026-04-20T15:00:00Z` returned **zero** items, confirming no earlier cycle still has assets whose forecast horizon reaches that instant. The `/conformance` document advertises neither `sortby=` nor `cql2=` classes, so `sortby=-properties.forecast:reference_datetime` was tested and was silently ignored (items returned in the same default order). The collection description states *"updated every 6 hours, available for the last 24 hours"* — **cycles publish at 6 h cadence, not the 3 h cadence implied by `_CYCLE_HOURS = (0, 3, 6, 9, 12, 15, 18, 21)`**; only the last ≤4 cycles are retained. **Conclusion**: the latest cycle (18:00Z yesterday) was ≈27 h stale at probe time — a real gap but the 120-h-window paginate confirmed three NEWER cycles (21-00, 21-06, 21-12 Z) ARE published and simply not surfaced by the single-instant probe. So MeteoSwiss reliability at probe time is nominal; the "genuinely late" narrative of F4 is false.

### T1.d — Item-ID naming convention
`/collections/ch.meteoschweiz.ogd-forecasting-icon-ch2/queryables` returns HTTP 404 ("Not Found"). The collection document's `summaries` is empty, no `item_assets` extension is present, and the description does not mention the item-ID format. The pattern `<MMDDYYYY>-<HHMM>-<step>-<var>-<member>-<hash>` (e.g. `04202026-1800-0-alb_rad-ctrl-ut7i9un0`) is uniformly present on all sampled items but is **undocumented happenstance**, not a published contract. The IDs are stable across today's samples but MeteoSwiss has no obligation to preserve them. D4 mandates property-based matching regardless, so this finding is documentation-only.

### T1.e — CQL-2 support
- **Conformance advertises**: none. The only classes returned are `api.stacspec.org/v1.0.0/{core,collections,ogcapi-features,item-search}` and `ogcapi-features-1/1.0/conf/{core,oas30,geojson}`. No `http://www.opengis.net/spec/cql2/...` URIs.
- **Predicate (i) — `forecast:reference_datetime`**: **not supported**. Both `?filter-lang=cql2-text&filter=forecast:reference_datetime='2026-04-20T18:00:00Z'` and the quoted-identifier form `"forecast:reference_datetime"=...` returned HTTP 200 with the default first-page response. Probing with an impossible cycle (`='2099-01-01T00:00:00Z'`) still returned the normal 5 items — proof the filter is silently ignored, not honoured. The POST `/search` endpoint with `cql2-json` returns HTTP 400 `"non-queriable parameter: filter"`, confirming filter support is absent server-side on both endpoints.
- **Predicate (ii) — `id LIKE '%-token-%'`**: **not supported**. `?filter=id LIKE '%-t_2m-%'&filter-lang=cql2-text` returned HTTP 200 with items whose IDs contain `-alb_rad-`, `-alhfl_s-` etc. — i.e. filter silently ignored.

### T1.a — Availability probe
The current `_cycle_is_published` issues `?datetime=<cycle-instant>&limit=100` and checks for IDs starting with `<MMDDYYYY-HHMM>-0-`. Against the **known-published** cycle `2026-04-20T18:00:00Z` the query returned 100 items, all with prefix `04202026-1800-0-` and ref_dt `2026-04-20T18:00:00Z` → probe correctly returns `True`. Against the **newer, also-published** cycles `2026-04-21T12:00:00Z`, `2026-04-21T18:00:00Z`, and `2026-04-22T00:00:00Z`, the query returned 100 items ALL of which were forward-step items of the oldest 2026-04-20T18:00Z cycle (ref_dt uniformly 2026-04-20T18:00Z, not the target cycle). The prefix-match count was **0** in each case → probe incorrectly returns `False` for cycles that are in fact published. Paginating the `?datetime=2026-04-21T12:00:00Z` query 5 pages deep confirmed the 2026-04-21T12:00Z cycle's own step-0 items only surface starting at **page 4** (server returns items ordered roughly oldest-ref_dt-first, with 114 step-0 items per cycle per instant). The prefix-match heuristic is thus **fundamentally ordering-fragile**: any cycle whose step-0 items don't land in the first 100 is invisible. **H-B confirmed**: the probe is buggy.

### T1.c — Fetch scope
Issued the current fetch query `?datetime=2026-04-20T18:00:00Z/2026-04-25T18:00:00Z&limit=500`; the server capped response at 100 items and returned a `rel=next` link (limit=500 not honoured). Paginated 500 pages (50,000 items). **Four distinct `forecast:reference_datetime` values appeared**: 2026-04-20T18:00Z (13,794 items), 2026-04-21T00:00Z (13,110), 2026-04-21T06:00Z (12,426), 2026-04-21T12:00Z (10,670). The proportions match the geometric intuition: each cycle's 120 h horizon overlaps the query window by 120h, 114h, 108h, 102h respectively. **H-C confirmed**: the adapter's `cycle/cycle+120h` range matches items from every cycle whose forecast horizon intersects the window, not just the target cycle. Only 27.6 % of returned items belong to the target cycle; the remaining 72.4 % are wasteful over-fetch that the client-side `cycle_prefix` filter (line 264) discards.

### T1.f — Per-cycle page count
Continued pagination with client-side target-cycle counting: the 2026-04-20T18:00Z cycle had **13,794 items total**, with the last matching item appearing at **page 138** (no more target-cycle items appeared in pages 139–250). Because the server orders by ref_dt ascending, the target cycle's items are confined to the first 138 pages of a per-cycle-ref_dt-filtered walk.

- **Total items for one cycle (after reference_datetime narrowing)**: **13,794**
- **Total pages at 100/page**: **138**
- **Recommended `_MAX_PAGINATION_PAGES`**: **300** (138 × 2 = 276, rounded up to the nearest 100). Given that CQL is not supported, the adapter will continue to walk ALL pages in the 120h window (552 pages for the current 4-cycle overlap, potentially more if MeteoSwiss retention grows); the pagination cap must cover the *entire window walk*, not just a single cycle's contribution. Suggested operational cap: **800** pages (552 observed × 1.5 safety margin, rounded up). Decision between "per-cycle sizing" (300) and "full-window sizing" (800) depends on whether Phase 2's T2b filter is server-side (no → use 800) or client-side (also 800 since we still walk the full window). **Phase 2 should choose 800.**

### Hypothesis verdicts
- **H-A** (MeteoSwiss genuinely late on 2026-04-21): **refuted**. At 21:00Z on 2026-04-21, four cycles published within the last ≈27 h are present in STAC (2026-04-20T18Z, 2026-04-21T00Z, 06Z, 12Z). This is consistent with normal 6 h cadence; the 21:00Z "cycle" probed by the dress-rehearsal code at 11:35Z on 2026-04-21 was simply not a valid publication slot (6 h cadence = 00/06/12/18 only), and the nearest valid-and-published cycle at rehearsal time was 2026-04-21T06:00Z — which F4's 3-step fallback (9 h window) starting from 09:00Z should have reached but did not, because the availability probe itself was buggy (H-B).
- **H-B** (availability probe buggy): **confirmed**. The prefix-based check against the first 100 items misses step-0 items of newer cycles because the server sorts roughly by ref_dt ascending and the first 100 are occluded by forward-step items of the oldest retained cycle. All three newer-cycle probes returned False for cycles that are actually published.
- **H-C** (fetch over-fetching): **confirmed**. The `cycle/cycle+120h` range filter returned 4 distinct ref_dts (target plus 3 unwanted); only 27.6 % of returned items belong to the target cycle.
- **H-D** (page cap too low for single cycle): **confirmed**. Even one correctly-filtered cycle occupies 138 pages at 100/page — above the 100-page cap. The *full-window* walk (which is what the adapter currently does) is ~552 pages.

### Implications for Phase 2
- **T2a ships as designed (D4)**: property-based probe comparing `properties.forecast:reference_datetime == cycle_iso`. The first-page response for a target cycle contained 100 items all with matching ref_dt, so a single page check is sufficient; no multi-page walk needed for the probe. Dependency on the ID prefix is removed.
- **T2b's CQL path does NOT ship** — MeteoSwiss advertises no CQL conformance and silently ignores `filter=` on both items and `/search`. T2b ships only the **client-side `forecast:reference_datetime` filter** inside the pagination loop. Correctness is preserved; pages walked is NOT reduced (the server returns all 4 cycles' items across ≈552 pages).
- **T4a cap raise is unconditional and must be sized to the full-window walk, not per-cycle**: `_MAX_PAGINATION_PAGES = 800` (552 observed × 1.5 safety margin). Per-cycle-only sizing (300) would regress the moment MeteoSwiss retention extends beyond 24 h or any additional cycle lands in the 120 h window.
- **T4b is a documented no-op** — item-ID CQL is not supported. The allowlist stays client-side; T4a's cap raise is the primary mitigation.
- **Surprise for subsequent subagents**: cycles publish at **6 h cadence**, not the 3 h cadence the adapter's `_CYCLE_HOURS = (0, 3, 6, 9, 12, 15, 18, 21)` assumes. This is a **latent bug not in scope for Plan 067** — when snapped to an odd-multiple-of-3 hour (e.g. 21:00Z), `resolve_cycle_time` would try a non-existent cycle slot as its starting point and burn a fallback step discovering that fact. Recommendation: Phase 2 subagent should open a follow-up plan (or amend D2) to correct `_CYCLE_HOURS` to `(0, 6, 12, 18)`; ICON-CH2-EPS cycles at 00, 06, 12, 18 UTC per the published collection metadata. Leaving the constant incorrect does not break correctness (the fallback loop will still find a published cycle) but it doubles the expected step count and silently wastes an HTTP request per resolve.
- **Secondary surprise**: the server orders items roughly by `forecast:reference_datetime` ascending on both `?datetime=<instant>` and `?datetime=<range>` queries. This is what makes the current availability-probe bug as bad as it is (the oldest cycle's forward-step items consume the first 100 positions). Property-based rewrite (T2a) makes ordering irrelevant and solves the bug cleanly.

---

## Appendix: STAC adapter checklist for Plan 047

Plan 047 (Nepal v1 — ECMWF IFS adapter) will build a STAC-backed NWP adapter
similar to `MeteoSwissNwpAdapter`. Plan 067's Phase 1 investigation and
Phase 2 fixes surface a concrete checklist of STAC adapter landmines and
lessons. When Plan 047 promotes from stub to DRAFT, it should verify each
item below against the target ECMWF endpoint.

### 1. Cycle cadence — verify against the collection description, not MEMORY

MeteoSwiss ICON-CH2-EPS publishes every 6 h per its collection description
(not 3 h as the adapter originally assumed). `_CYCLE_HOURS` must match the
server's actual cadence. ECMWF IFS has its own cadence: HRES is typically
6 h (00/06/12/18 UTC); ENS is typically 6 h or 12 h depending on resolution.
**Always read the target endpoint's collection description and confirm the
cadence before hardcoding `_CYCLE_HOURS`.** The divisor in
`ceil(cfg.nwp_max_fallback_age_hours / CADENCE_HOURS)` must match.

### 2. Availability probe — use property-based matching, not ID prefixes

Do NOT rely on item-ID structural conventions — they are undocumented
happenstance, not contract (T1.d). Use
`properties.forecast:reference_datetime` or the equivalent published
property on the target collection. Verify via `/collections/<id>` and
`/collections/<id>/queryables` whether the property is documented.

### 3. STAC default sort is unspecified — never depend on it

MeteoSwiss sorts items roughly by `forecast:reference_datetime` ascending,
which makes availability probes that inspect only the first 100 items
ordering-fragile (H-B confirmed). Plan 047 must either (a) sortby= the
relevant property if the server advertises sortby conformance; (b) paginate
until the target item is found or the page budget is exhausted; or (c) use
property-based matching that tolerates any ordering (this plan's choice).

### 4. CQL support is not universal — verify per endpoint

MeteoSwiss advertises no CQL conformance and silently ignores `filter=` on
both `/items` and `/search` (T1.e). Check the target endpoint's
`/conformance` document for CQL classes
(`http://www.opengis.net/spec/cql2/...`) AND probe a concrete predicate
against known data — some endpoints advertise CQL but don't honour every
predicate. If CQL is available, server-side narrowing on
`forecast:reference_datetime` is the cheapest fix for over-fetching.

### 5. Fetch-scope filtering — server-side if possible, else client-side

If server-side reference_datetime narrowing is unavailable (like MeteoSwiss),
add a client-side filter inside the pagination loop that skips features
whose reference_datetime ≠ target. Correctness is preserved but pages
walked is NOT reduced — see item 6.

### 6. Pagination cap — size for the realistic full-window walk, not a single cycle

A `datetime=<cycle>/<cycle+horizon>` range filter matches items from every
cycle whose forecast horizon intersects the window. At MeteoSwiss's 24 h
retention, this means 4 cycles × ~14 k items = ~55 k items = ~552 pages
at 100/page. The cap must cover the full window walk unless server-side
narrowing is available. Plan 067 sized `_MAX_PAGINATION_PAGES = 800`
(552 × 1.5 safety). ECMWF's retention and per-cycle item count will
differ; T1.f-style measurement during Plan 047's own Phase 1 is required.

### 7. Allowlist filtering is client-side

Both MeteoSwiss and (likely) ECMWF return all variables for an ensemble
cycle; the adapter filters to the v0 allowlist (`tp`, `t_2m`) client-side
after the HTTP response. This will not change unless CQL predicate on
item-ID variable-name tokens is supported (MeteoSwiss: no — T4b is a
documented no-op).

### 8. Cycle resolution should be tolerant of late publishing, not brittle

`resolve_cycle_time` should accept a policy ceiling
(`nwp_max_fallback_age_hours`) and walk back in cycle-cadence-sized steps
until it finds a published cycle OR exhausts the budget and raises
`NoCycleAvailableError`. Plan 067 derives step count from the policy
(D2: `ceil(age_hours / CADENCE_HOURS)`) — single source of truth. Plan 047
should follow the same pattern.

### 9. Integration tests against the live endpoint are non-negotiable

Unit tests with faked STAC responses caught most of Plan 067's bugs during
implementation but missed the 6 h-cadence happenstance (T3.d), which only
surfaced during T1.b's live probe. Plan 047 should ship a `live_<source>`
pytest marker per `pyproject.toml` and a live integration test with the
standard skip-on-outage heuristic (GET collection URL, 5 s timeout,
HTTP 5xx or network error → skip).

### 10. Variable renaming — lesson from T5

MeteoSwiss's `_parse_grib_files` renames STAC/GRIB variable names to
adapter-internal canonical names (e.g. `tp` → `precipitation`,
`t_2m` → `temperature`; verify against the current code). Plan 047's live
test assertions should match the POST-rename names, not the raw names.
