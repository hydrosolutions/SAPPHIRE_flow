# SAPPHIRE Flow — Plan Index

Maintained by hand — update whenever a plan's status changes, a new plan is added,
or a plan is implemented (move it to [archive/](archive/)). Do not auto-generate.

- ⚠️ **273** — [Nepal illustrative backend export](273-nepal-flow-map-demo-handoff.md) — `status: COMPLETE` **but NOT MERGED** — `feat/nepal-demo-export` is still unmerged into `main` (verified 2026-09-24), so it stays OUT of `archive/`. ⛔ *A plan reading COMPLETE for work that is not on `main` is the same hazard as one reading READY after merge, in the other direction.* Original note: reviewed v2 multi-cycle backend on `feat/nepal-demo-export`, 6311 regression tests passed (51 skipped, 15 deselected); ready for frontend import (not merged).
- **340** — [Immutable forecast evidence capture](340-forecast-evidence-capture.md) — `PARTIAL`; T1 capture, T2 backup/restore tooling and T3 handoff merged in PRs #306, #311 and #312. DHM protected-target configuration and Nepal-sized backup/restore proof remain open; CHWRR publication stays disabled.
- **341** — [CHWRR forecast review and publication API](341-chwrr-forecast-publication-api.md) — `DRAFT`; attributed per-forecast publication and withdrawal, published-only consumer API. Plan 340's software dependency has landed; its DHM target proof and the real CHWRR identity-provider details are activation inputs.
- **342** — [Alert evaluations and CHWRR warning decisions](342-chwrr-alert-evaluation-and-decisions.md) — `DRAFT`; high/low-flow evaluation ledger and human warning publish/dismiss history. Depends on 340 and 341.
- **343** — [Operational post-event verification](343-operational-post-event-verification.md) — `DRAFT`; preliminary scoring, revisions, cases and corrective actions. Depends on 340, 341 and 342.
- **344** — [Six-year evidence archive and replay](344-chwrr-evidence-archive-and-replay.md) — `DRAFT`; cold archive and diagnostic replay. Depends on 340–343; not required for first guarded CHWRR testing.
- **401** — [A reviewer access token for the review dashboards](401-reviewer-access-token-role.md) — `DRAFT, HIGH RISK`; third HTTP token role — GET-only, tenant-scoped like a consumer, plus REVIEW routes — one per dashboard (BAFU/Swiss, Nepal). Amends Plan 147 G4's role list only; tokens stay GET-only; publishing is a named person (341); the DHM gauges get their own tenant (confirms Plan 268 D11) and the Nepal dashboard's token binds to it only (owner, 2026-09-26); a dashboard token uses tenant mode only when every station in its tenant belongs to its client, otherwise an explicit station list. Review corrections folded; current text NOT yet re-reviewed. Blocks 402.
- **402** — [The flow map reads the /api/v1 interface — QC rule sets, station skill, forecast QC flags, committed contract](402-flow-map-reads-the-api.md) — `DRAFT, HIGH RISK`; **depends on 401**. Review corrections folded; current text NOT yet re-reviewed; high-risk ⇒ one extra owner-commissioned review before READY. Decisions: map reads the API (snapshot stays v2), forecast rules served too, per-dashboard reviewer token, forecast QC flags visible to every role. Follow-on: Plan 404 (QC-rejected member/group forecasts are dropped today, so the map can never show them). Open: D9 — QC what-if dry run, recommended as a follow-on.
- **404** — [Keep the member and group forecasts that QC rejects](404-store-qc-rejected-member-forecasts.md) — `DRAFT, HIGH RISK`; rejected forecasts go to a **separate record** (new table + one REVIEW route), never the `forecasts` table, so fallback, alerting, combination, re-runs (327/328), model state and the freshness heartbeat are untouched; where Plan 341's gate is active, reviewer tokens see the rejecting rule but not the values, admins and granted hydrologists the full record (owner, 2026-09-26). Depends on 401 and 402. All decisions closed; review corrections folded, NOT yet re-reviewed.

## Status convention (added 2026-08-28 after a stale-status audit)

YAML `status:` frontmatter is the only machine-readable status source for active
plans in `docs/plans/`. The canonical active statuses are `DRAFT`, `READY`,
`BLOCKED`, `DEFERRED`, `PARTIAL`, `SUPERSEDED`, and `COMPLETE`. Only `READY` is
implementable. A missing active YAML status is reported as `NONE`, never inferred
from body prose. Do not use `IN_PROGRESS` or `DONE` as active-plan statuses;
execution progress belongs to the branch, run, or PR.

When a plan reaches `COMPLETE`, `git mv` it into [archive/](archive/) **and** grep
for references first — a plan path is cited from other plans and, in at least one
case, from a workflow comment; archiving Plan 174 once broke a test that read the
doc from disk. `ARCHIVED` is a location, not an active status. Historical files
already in `archive/` keep their legacy `**Status**:` labels, EXCEPT where a legacy label
contradicts the archived outcome: the 2026-09-18 stale-status audit rewrote eight such labels that
still read `READY` (097, 103, 107, 111b, 221, 223, 235, 253) because an archived plan reading READY
is authoritative twice over and reads to an agent as a live order.

**Why this is written down.** An audit on 2026-08-28 scanned for stale statuses and found the
scan itself could not work: statuses were recorded in three different ways — frontmatter
`status:`, a legacy `**Status**:` line (27 plans), and 11 plans with no status marker at all.
A scan keyed on frontmatter silently skipped the other 38, which is how Plan 064 sat reading
`READY` while ~90% shipped. Active plans now require YAML frontmatter; legacy body markers
remain historical diagnostics for archived plans only.

**Context:** v0 is complete (the mac-mini runs NWP-on operational runoff
forecasting). We are marching to **v1 = Nepal DHM deployment** (ECMWF IFS via the
recap Data Gateway, DHM gauges, ERA5-Land, multi-tenant east/west). Category tags:
**A** = v0 operational hardening / reliability (land before any v1 prod deploy) ·
**B** = v1 Nepal feature · **C** = dev-experience / dashboard / deferrable.

## Writing new plans

Plan tasks are the single completion ledger. Every non-trivial task states an
observable **Outcome**, bounded **In / Out**, exact **Verification**, and—when
behavior changes—the **Pre-change** failure that the same evidence exposes. Use
`N/A` only for documentation, mechanical, or integration/gate tasks and say why.
Do not add a separate acceptance-map table or persistent run-state file.


## Nepal observation integration

- **301** — [DHM precipitation observation adapter](301-dhm-precipitation-adapter.md) —
  `DRAFT` — extends Plan 300 to weather-station rainfall using captured BIPAD
  examples. Rainfall-window and timestamp semantics remain owner-confirmed open
  questions; resolve these before canonical precipitation writes or READY.

- **300** — [DHM water-level observation adapter](archive/300-dhm-observation-adapter.md) —
  `COMPLETE` — offline implementation and 308 focused checks passed; archived.
  Independent patch reviews completed with five distinct low-severity findings
  recorded in PR #278; no
  blocking implementation defect was found. The full default suite passed after
  merging current main: 6,408 passed, 53 skipped, 15 deselected (2026-09-16).
  Owner-relayed BIPAD confirmation establishes the request/response
  contract for direct DHM adapter development. Captured examples support offline
  parsing, history/pagination and Flow 2 integration; real DHM connection settings
  remain deployment inputs. First adapter slice of Plan 106 D5-2; generic unit
  conversion and rating curves remain separate. The owner approved implementation
  on 2026-09-16 after independent review and incorporation of the accepted findings.

## Archived by the 2026-08-28 stale-status audit

Each had `status: READY` while its work was already present on `main`. Archived on **code-artifact
evidence** (plan-named tests and source in the tree), **not** on a task-by-task re-verification of
exit criteria — Plan 212 owns that deeper screening.

- **090** — NWP incomplete-cycle selection — header already said DONE (P1, PR #49, `ab54d24e` on main); P2 optional, must be re-scoped.
- **140** — ICON-CH2-EPS STAC pagination fix — body already said "COMPLETE — shipped as `3264a45`"; confirmed on main.
- **082** — recap Gateway operational readiness (19 plan-named test files, 10 source files).
- **117** — basin/static artifact architecture.
- **129** — continuous precipitation knit (RhiresD → RprelimD → NWP).
- **130** — temperature reanalysis live-tail.
- **145** — future-snow (JSNOW) forcing wiring — present in the recap adapter, the reanalysis ingest and the forecast cycle.
- **161** — DATABASE_URL credential parsing.

**Held back deliberately, with reasons:**
- **138** (BAFU precip+temp+runoff regression) — its own body says "**T1 is PARTIAL, not done**". Archiving it would hide outstanding work.
- **035** (rating-curve provenance) — contradictory: the header says implementation begins at v1, yet `tests/unit/services/test_rating_conversion.py` and a `0035` migration downgrade test already exist. The 2026-09-18 audit recorded frontmatter `PARTIAL` from that same evidence, which makes the file machine-readable but does NOT resolve the contradiction — whether the remaining scope is v1 work or already shipped still needs an owner decision before it is archived.
- **162** (robust database backup) — the work looks shipped, but `tests/unit/ops/test_restore_rehearsal.py:10` cites its path in a docstring. Moving it dangles that reference, and editing a test file is a code change belonging in a PR.

## Recently merged (v1 operational hardening — implemented via WF2, independently reviewed)

- **243 — ARCHIVED 2026-09-07, same day it was drafted, approved and merged (#262).** The Gateway
  sends NO units, for any variable, on any endpoint (measured). Every variable now records its
  assumed source unit and the named authority for it, beside the converter the code already reads,
  plus the radiation trap (`ssr`/`str` are accumulated J/m², not W/m²) for whoever adds them next.
  **It makes a unit error attributable, not detectable** — detection needs the pass-through requested
  upstream, or a basin with snow in it. HRU 12300 has none.

- **201 / 206 / 219 — ARCHIVED 2026-09-07.** 201 (sequential unit-suite isolation, #220) and 206
  (`cicd.md` drift, #222) had been held back only because `integration-nightly.yml:144` cited 201's
  path; that citation now points into `archive/`, so both moved. 219 (snow channel for the 12300
  feed, #255/#256) is merged, deployed and verified live — 723 snow records on the first scheduled
  run, units and aggregation confirmed against the snow modeller's CF metadata. Its two open
  findings — HRU 12300 is blind to a snow-unit error, and the Gateway strips CF metadata — carry
  forward to Plan 243, not to the archive.

- **184 / 193 / 205 / 209** — **DHM precipitation research arc (M-A6 → M-A9) — MERGED (#211, #212,
  #213, #215, #218), ARCHIVED.** Gauge vs ERA5-Land, temporal characterisation, elevation and regime
  structure, and the Phase-2 recommendation. Each plan independently reviewed **before** implementation
  (7, 7 and 12 findings folded on 193/205/209 respectively); 184 was implemented first and paid twelve
  retrospective amendments, which is what established the review-first order. **Outcome:** the sample
  supports characterisation well and correction poorly — a precipitation–elevation lapse rate is not
  obtainable from it (needs OD-10), forcing correction transfers only below ~2,000 m, and every route
  to the snow-dominated high basins is closed by the data. Recommendation at
  `docs/design/dhm-precipitation-phase2-recommendation.md`. **Remaining on the track:** M-I1 (QC rules),
  M-I3 (WMO catch-efficiency inventory), M-A5b (IMERG, rewritten around Early).

- **101** — water_level QC datum fix — **MERGED (#66), ARCHIVED** — per-station datum,
  subtract-before-QC across all four QC call sites; the mechanism DHM's mixed
  cm/m/m-a.s.l. units need. 4 design gates + implementation review (regression locks
  verified).
- **100** — Forecast-feed resilience — **MERGED (#65 base + #67 floor-gate fix),
  ARCHIVED** — persist NWP-on across restarts + always-on climatology floor + fatal
  NWP-off gate + new-onboarding floor gate (6a, the incident-class fix) +
  staleness/health. Implemented via WF2, independently reviewed (the review caught
  the 6a gap in #65; #67 closed it, re-verified).
- **105** — Operational disk hygiene & NWP scratch cleanup — **MERGED (#68),
  ARCHIVED** — scratch self-clean on failure + pre-fetch disk tripwire + weekly
  image prune. First Wave-0 lead; conventional build + adversarial review (round-2
  caught 3 blockers a green suite missed).
- **038** — Store write atomicity — **MERGED (#71), ARCHIVED** — injectable-
  transaction DI replaces AUTOCOMMIT two-phase inserts; resilient reads + orphan
  cleanup. Wave-0.
- **040** — Hindcast deduplication constraint — **MERGED (#75), ARCHIVED** — 6-col
  UNIQUE + ON CONFLICT DO UPDATE full-replace upsert (idempotent hindcast writes)
  + migration 0029 dedup. Wave-0; 2 adversarial Codex rounds converged. **All 3
  Wave-0 correctness bugs (105 + 038 + 040) now merged.**

## Active — operational hardening (A) — the gate to any v1 prod deploy

- **253** — Quality signals dropped at the store boundary — `READY, implemented (hold-at-PR)` —
  three defects of one class, all measured on the mini (0.1.833) 2026-09-02/03: the
  per-forecast **input-quality assessment is computed and never persisted** (no DB
  column, no API field, reads back as `FULL` for every forecast — Plan 023's
  unfinished half, and the WMO-1072/QMF-H commitment `wmo.md:171` records as
  *Addressed in v0*); the **`_pooled` combination forecast is stored unchecked**
  (`forecast_combination.py:404` hard-codes `qc_status=RAW` — all 30 pooled rows
  since combination was enabled 2026-08-27); and **no gauged feed synthesises a
  `MISSING` row for a timestamp that never arrived** (0 rows live; two producers exist —
  calculated-station derivation, reached from ingest but dormant with no calculated
  stations, and an offline research script — but neither covers sensor silence).
  Carries an **owner decision**, since taken, to build that producer and a fourth task re-verifying `wmo.md` § 5, which asserted
  two of these as closed. Plan 023 was archived by a pure file-move commit while
  still `status: READY`, which is how the drift stayed invisible. **Decision taken
  2026-09-03: build the producer — DHM's API will deliver unmarked gaps, which was
  the stated flip condition. It is Plan 250, not a phase here; what stays in 253 is
  making `wmo.md` truthful in the interval.**
- **250** — Explicit gap markers for feeds that deliver unmarked absences — `DRAFT,
  STUB — not scoped` — gives operational ingest the ability to write a `MISSING` row
  for an expected-but-absent observation. `QcStatus.MISSING` is defined and enforced
  twice (domain invariant + DB check constraint); 0 rows live, the two existing
  producers both cover unusable *components*, not sensor silence, and nothing records
  an expected reporting schedule per station and parameter to make "expected"
  meaningful. `pipeline_health` does NOT close this — its records are collector- and
  run-level, not per-station freshness. Driven by DHM, whose DMS flags a value `Erroneous`
  and then withholds it from the API, so an absence arrives with no marker.
  ⛔ A `MISSING` row records *that* a value was expected and absent, never *why* — it
  does not recover DHM's flag; that needs an API change, tracked in the DHM data-format
  questionnaire § 5. Scoping must settle cadence metadata, the materialisation bound,
  retention on null rows, and whether this overlaps `pipeline_health`.
- **251** — The Forecast Lab should show a rejected combination — `DRAFT, unreviewed` —
  follow-on from 253's OD-1a. A combined forecast that fails QC is stored by 253 and
  deliberately hidden from the Forecast Lab, because surfacing it means adding two
  fields to a **published, strict snapshot format** (`additionalProperties: false`,
  eight permitted fields) and therefore a v2→v3 transition across the eight files that
  stamp or check `forecast-lab-snapshot/v2`. Split out so a versioned external-format
  change does not ride on a persistence fix. Three open owner decisions: whether v3
  replaces or coexists with v2, whether the new fields are required, and what
  `available` should mean for a rejected forecast. Depends on 253 landing first.
- **267** — Our own aggregation stamps the start of a period; every source whose convention we have
  established stamps the end — `DRAFT` — every ingested source we have checked is period-ending while
  our own `group_by_dynamic` labels bucket starts by an unexamined library default. The one-hour shift
  is PROVEN by execution but LATENT: all stored forcing is already daily, so the hourly→daily path
  never runs today. Nepal is hourly. Rides Plan 254 T6's cutover. Depends on 252 and 258.

  ⚠️ *(This entry previously carried Plan 261's description — the reanalysis tail — and falsely
  declared a dependency on 239. Corrected 2026-09-10. **Renumbered 262 → 267 on 2026-09-11**: number 262 was
  published twice; the `cmal_small` pilot landed first and keeps it.)*

- **263** — Two sources share a step and not a phase, and no operation consumes the second one —
  `DRAFT` — Plan 252 OD-15 makes Swiss daily temperature an off-grid input, and under the settled
  rules nothing legal consumes it: resampling would split a daily value, shifting would move a
  timestamp, refusing discards temperature. A sketch written into 252 was found underspecified in five
  ways the same hour and demoted to OQ-7; this plan settles them. **Blocks 254 T6.**

- **261** — The reanalysis tail is never filled, though the values are already here — `DRAFT`
  — measured while validating Plan 239 T1b: past forcing runs permanently ~2.5 days
  short of the issue time, and the FORECAST values that would cover that span are
  already stored. Concatenates them onto past forcing **in memory**, in the two
  operational assemblers only. **Nothing is stored** — not a new source, not a change
  to what is ingested, not a new column, not interior gap-filling. Renumbered off a
  three-way 258 collision on 2026-09-09. Depends on 239.
- **258** — A value is a point or an interval — `DRAFT` — **split out of 252 on
  2026-09-08**, after an independent review returned eight blockers of which three
  belonged to this one concern and none to grids. Owns CF `cell_methods` as the
  temporal-support vocabulary, the period-ending convention (which binds interval
  data only — the blanket form was wrong for every instantaneous channel), and the
  verification of a declaration against the source. ⛔ Four open decisions first,
  the blocking one being **at what cardinality** support is recorded: the same
  canonical parameter has different support per product — MeteoSwiss `TabsD` is a
  daily mean while ECMWF `2t` is instantaneous, and both map to `temperature`, so
  one value per parameter cannot express both. Its verification task is blocked
  upstream: the Gateway strips CF attributes, and Plan 243's in-flight ask covers
  `units` only.
- **252** — A time grid is a step AND a phase — `DRAFT, reviewed twice` — **conventions
  and types only** after a review returned 26 findings (20 blockers) and forced a
  split. Nepal Time is UTC+05:45, so hourly NPT and hourly UTC grids never share a
  timestamp; converting to UTC relabels instants without moving them onto a grid and
  makes the offset invisible. ⚠️ **CF `cell_methods` and period-ending left this plan
  on 2026-09-08 for Plan 258** — it is now grids only. Declares `TimeGrid(step, phase)`, a per-deployment
  boundary that no deployment may default into, and **PROPOSES the supersession of Plan
  228 D4** by owner disposition — proposed, not done: 228 is READY and its rule is what
  the code implements until 252 T8 actually lands. Nepal is provisionally 18:00Z — reached both by rounding civil midnight
  and, independently, by SnowMapper's UTC+6 solar day. ⛔ **Correction (2026-09-08):** the
  claim that a different DHM boundary would put us "at odds with SnowMapper" was
  withdrawn inside Plan 252 itself — we receive SnowMapper in UTC and convert, so the
  UTC+6 coincidence corroborates 18:00Z and constrains nothing. Whatever DHM names is
  what we adopt. Four distinct boundary values are now tabulated in 252; do not quote
  one without checking which.
- **254** — Phase-aware execution — `DRAFT` — the behavioural half.
  The resampler has **TWELVE** invocation sites (re-measured 2026-09-10; this entry said seven, and
  before that three — ⛔ never cite a call-site count from prose), and `floor_to_time_step` /
  `aligned_lookback_bounds` are separately phase-zero, so changing the bucketing alone
  would lose Plan 228 D4's exactly-N-complete-buckets guarantee. Plan 253's
  input-quality channel cannot carry resampling provenance (hindcasts were excluded
  from it). Artifacts record no training grid, so a phase-mismatched artifact cannot
  fail closed. Carries the Swiss retrain and cutover, which no plan currently owns —
  226 is anchoring-only and 235 points at 228's recompute. Four open decisions first,
  including whether phase belongs to the FI contract (which would need an upstream
  issue, not a SAP3 workaround).
- **255** — Eligibility exclusions become typed data — `DRAFT` — **small, self-contained,
  and the thing that unblocks 256.** `eligible_meteoswiss_configs` logs why it refuses
  a station a MeteoSwiss binding and then throws the reason away, so no caller can act
  on it: that is why the Plan 115b2 §2C hold (`eligible - backfilled`) cannot fire for
  an excluded station — being more broken puts it outside the guard. Adds a
  partitioning function beside the existing one rather than changing a signature four
  production call sites (two of them operator scripts), seven tests and the spec depend
  on. Split out of the original 255 after three Codex rounds.
- **256** — Onboarding must not promote what it did not build — `DRAFT`,
  **high-risk** — withholds a river/lake station excluded for one of four geometry
  reasons from assignment, training and the promotion write, and audits the status
  transitions onboarding writes via the existing `STATION_STATUS_CHANGE`. 🔑 **Scope
  narrowed twice on evidence.** `station_status` is overloaded — it gates forecasting
  AND ingest (`ingest_observations.py:604` polls only `operational`), so no status
  means "keep collecting, don't forecast" and demoting Branson would stop its
  observation record permanently; owner chose to leave it operational, so this plan
  prevents the NEXT wrong promotion rather than repairing the existing one. And the
  audit cannot capture the direct SQL write that caused the staging incident — that
  would need a database trigger, deliberately not attempted. Depends on 255; blocks 260.
- **260** — Recover the three stations whose history QC never processed — `BLOCKED`
  on 256 T3 — 2041, 2116 and 2615 hold 14 610 / 14 610 / 9 497 pre-2026 rows still in
  `raw`; the fleet partition is exact (all 143 QC'd stations have baselines, none of
  the 5 un-QC'd do). Deliberately carries no tasks until the cause of the QC skip is
  known — `workflow.md:129` forbids a READY plan that defers its own inputs — but
  records the verification it will have to meet.
- **259** — Onboarding reports what it actually did — `DRAFT` — one outcome per
  station a run was asked to handle, and a delivered report naming every station
  complete / degraded / withheld / failed. Split from the original 255 so its
  unresolved destination question stopped blocking 256. 🪤 Carries a trap worth
  reading: the worker root is `read_only: true` and only four `/data/*` paths are
  writable, so the obvious `/data/reports` destination would fail in production while
  every `tmp_path` test passed. Also keys outcomes on REQUESTED not resolved stations —
  a failed *update* leaves the station in `station_map` (`onboarding.py:504` before
  `:510`), so a naive implementation reports it as a success. Depends on 255.
- **257** — Combined-forecast coverage is unmonitored — `DRAFT` — `_pooled` writes
  stopped on staging at 2026-09-04 06:26Z and were found by hand four days later.
  Nothing reported it: `forecast_freshness` read `ok` throughout — correctly, since it
  answers "did the cycle store ANY forecast" and ~1336/day kept arriving from the other
  five `model_id`s while a product 34 stations depend on produced nothing. The gap is
  already written down: `_emit_forecast_freshness_record` says "No partial-coverage
  state is tracked here (that is the explicitly out-of-scope per-product coverage
  ledger)" — Plan 116 named it and deferred it. One `PipelineCheckType`, one emitter,
  counts in `detail`, tests; T2 (carry the drop reason) is the half to cut first. Two
  traps it must face: there are **five** `_emit_forecast_freshness_record` call sites,
  not one (normal completion plus four abort/fatal paths, so a dark cycle never
  silences its own heartbeat), hence a `cycle_completed` flag with status floored at
  `warning` on aborts — otherwise an NWP abort reads as a combination failure; and the
  check is **born red and stays red until Plan 226**, so D2/D3 record it without paging
  yet. Does not touch the combiner: Plan 222's absence is correct behaviour and 226
  (**absorbed into 254 T8 on 2026-09-08 — Plan 226 is SUPERSEDED**) is what refills the
  product. Scope note: 222 D7 priced this at 2
  stations; onboarding on 09-04 multiplied it to 34 in the same window the guard landed.
- **163** — Watchdog dead-man's switch + HTTP hardening — `READY, implemented
  (hold-at-PR)` — the mac-mini watchdog went silent ~03:54 2026-08-16 with no
  alert (the exact silence-looks-like-health shape of the 29-July 14-day outage).
  Adds an off-box dead-man's-switch heartbeat (Healthchecks.io) POSTed after every
  tick that COMPLETES AND PERSISTS its state — an unhealthy stack still pings
  (Slack is the *detected-failure* channel, the dead-man is the
  *watchdog-died-before-it-could-report* channel), but a tick that raises before
  persistence correctly emits no heartbeat, never placed in a `finally` (which
  would falsely mark a crashed tick healthy). Also hardens all four outbound HTTP
  call sites (health probe, BAFU-detail probe, Slack POST, dead-man POST) against
  `httpx.InvalidURL` (verified NOT a subclass of `httpx.HTTPError`), `OSError`
  and `UnicodeError` — a malformed hand-pasted URL could otherwise kill a tick at
  exactly the moment it tries to report an outage. Routes all four Slack call
  sites through a safety helper so an unexpected delivery exception can no longer
  lose Plan 162 Phase A's `backup_notification_pending` transition.
- **160** — BAFU forecast adapter schema-drift resilience — `READY, implemented
  (hold-at-PR)` — fixes the live BAFU forecast collector outage (dead since
  2026-08-12 ~16:00 UTC, caught by the Plan 158 Slack alert): BAFU added the icon
  value `river_missing`, which `BafuIcon`'s flat `Literal["river","lake","missing"]`
  rejected, and whole-batch validation aborted the inventory for all 54 stations on
  that ONE bad value. The icon is now modelled compositionally (water-body `kind` ×
  `BafuGaugeDataStatus`, D1), so `lake_missing` is supported before it has ever been
  seen (D6, forward-looking lock). Routing follows KIND only — `_missing` does NOT
  suppress the fetch (D2/D8: probed directly, BAFU still publishes a full forecast
  for a station whose live gauge is down). `fetch_station_inventory` now validates
  per FEATURE (D3, the class fix): a bad feature is skipped and recorded, the rest
  of the batch is still returned; an unrecognised icon SKIPS its station rather than
  falling through to a river-shaped default fetch (D4, fail-safe not fail-open). A
  skipped station is a WARNING + a queryable `pipeline_health` WARNING status (D5).
  Auditing the other Literal-typed external vocabularies (`BafuMetric`,
  `BafuForecastVariant`, `LindasKind`, `BafuObservationParameter`) is a named
  follow-on (D9), not in scope here.
- **154** — Recap IFS fetch containment — `READY, implemented (hold-at-PR)` — a
  station-scoped `RecapDataUnavailableError` (one HRU's control fetch missing) no longer
  discards every other HRU's already-accumulated rows or escalates into the flow's
  cycle-wide runoff-only degradation. Per-HRU exception containment with an
  all-or-nothing HRU commit (an HRU is the Gateway call unit — station-level
  partiality is unrepresentable at this boundary until Plan 151's per-track path).
  Per-HRU divergence is treated as an ANOMALY (owner-confirmed, publication is
  global): healthy HRUs are still served, and `_fetch_nwp_task` reconciles
  requested-vs-returned stations, alarming a CRITICAL `pipeline_health` record +
  DEGRADED cycle health rather than silently darkening the whole deployment. No
  adapter return-type/Protocol change. Independent of the forecast-cycle redesign;
  Plan 151 D7 needs this same containment for its own per-track path (154 first
  shrinks 151's T4). **Fixer round (2026-08-12, post-implementation review):** folded
  in a mixed-empty/populated-control-variable `AdapterError` guard (D2's "complete
  variable set" invariant covers this shape too, not just the raising case), a
  `run_forecast_cycle_flow` end-to-end test proving the divergence wiring (was
  previously only unit-tested in isolation), and an accurate total-loss re-raise
  message that preserves the original Gateway diagnostic as `__cause__`. See the
  plan doc's "Fixer round" section.
- **103** — Writable `PREFECT_HOME` under the read-only container — `COMPLETE` (#125; archived 2026-09-11) — set
  `PREFECT_HOME=/tmp/prefect` on the 3 client services (worker, worker-ingest, init). **Supersedes 062 and
  141.** Trivial/env-only. The flow-run-**log-persistence** half was **split to
  Plan 142** (2026-07-23) — it needed a load-bearing deployment-entrypoint change.
- **142** — Persist Prefect flow-run logs — `DRAFT` — carved out of 103; module-path deployment entrypoints
  (dot, ⚠️ no colon) + guarded `flows/__init__.py` hook + `APILogHandler` on a `sapphire_flow`-scoped logger.
  Load-bearing; depends on 103; needs its own /plan → /implement.
- **141** — Prefect writable home under read-only container — `SUPERSEDED by 103` — a redundant re-draft of
  103's D1 (`PREFECT_HOME=/tmp/prefect`); folded into 103 (owner 2026-07-22).
- **097** — Short-lookback observability — `COMPLETE` (#76; archived 2026-09-11) — warn
  when the delivered lookback is shorter than requested.
- **048** — restic encrypted backup + monthly restore rehearsal — `DRAFT (stub)` —
  **HARD prod prerequisite.** Depends on 046.
- **046** — Mac Mini staging deployment + edge-case suite — `PARTIAL` (was shown here as
  `IN_PROGRESS`, which the vocabulary above forbids).
- **058** — BAFU LINDAS archive via operational collection — `SUPERSEDED by 136` (archived).
- **136 / 175 / 176 / 186 / 189** — **BAFU LINDAS observation archiving — COMPLETE, ARCHIVED
  (2026-08-21).** The whole family is merged and running on the mac-mini in image `0.1.775`:
  **136** the quarantined all-gauge archive collector (#121, `ea33394`), **175** LINDAS rate-limit
  resilience (#172, `9c96792`), **176** the 10-minute grid (#181, `afbf7e2`), **186** whole-graph
  operational ingest resolving 175's deferred D5 (#188, `4ae7cf8`), **189** audit edge + poll bound
  (#193, `d1f0837`). Live evidence 2026-08-21: **233 gauges × 495 rows per 10-minute slot, 700
  snapshots / 81 MB**, and the on-demand T8 audit measures completeness at **95.8 %** (322/336)
  post-176 against **16.3 %** (94/576) on the old hourly cadence. Remaining gaps are BAFU publish
  stalls upstream, not collector faults. The archive stays quarantined — no gauge is onboarded, and
  nothing here reaches the `observations` table. See
  [archive/136-bafu-lindas-observation-archive-collector.md](archive/136-bafu-lindas-observation-archive-collector.md).
- **091** — Mac-mini NWP-on data-collection runbook — `DRAFT` — depends on 046.
- **094** — Cap onboarding/hindcast window to actual data range — `DRAFT`.
- **083** — Human-readable `station_code` in structured logs — `DRAFT`.
- **075** — Mac Mini Stream C: glue + one-command bootstrap — `READY`.
- **084** — Dev-machine deployment validation (2-station runoff-only) — `READY`
  (validated 2026-06-28; reusable harness not fully built).
- **064** — Supply-chain hardening — `PARTIAL` (~90% shipped; remaining scope = the e2e tier).
- **069** — Pyright backlog cleanup: ratchet + drain — `PARTIAL` (Phase 1 ratchet
  shipped; the `flows/` drain remains).
- **062** — Prefect state persistence (`PREFECT_HOME` ↔ volume) — `SUPERSEDED by 103` (reconciled
  2026-07-22; also carried a stale SQLite-server premise — prefect-server is Postgres-backed).

## Active — v1 Nepal feature (B)

- ✅ **Plan-number collision RESOLVED, 2026-09-24.** `323` and `324` briefly named two plans each;
  the other session renumbered its series to **340–344**, so `323`/`324`/`325` are unambiguous.
  🔑 *Kept as a note because plans are cited by NUMBER and the collision window produced commits and
  a PR body — a citation written during it may still mean the other plan.*
- ✅ **272 is COMPLETE and ARCHIVED** (2026-09-24) —
  [archive/272-qc-rules-unreachable-on-inferred-cadence.md](archive/272-qc-rules-unreachable-on-inferred-cadence.md).
  Behaviour shipped via 316/317/318, records repaired by 324, and its last three items closed on
  evidence rather than carried: the DHM mask byte-identical test was **superseded** (the mirrored
  implementation it guarded was deleted in favour of importing the production selector, and the
  removed fallback could not have altered an hourly series anyway), the bounded inference fetch was
  **superseded** by 323 (and **revived by Plan 400** on 2026-09-26, on a new argument), and the severity-ranking gap is **unreachable** (a `QcFlag` cannot carry
  `QC_UNCHECKED`, because that status means no rule ran and so no flag exists).
  ⛔ *Plan 314's sentinel question is 314's, not 272's — a plan does not stay open because a
  different plan has an open decision.*
- **Nepal DHM observation/QC family (264 / 268 / 269 / 301 / 303 / 304 / 315–318)** — read these plans
  together. Plans 316, 317 and 318 are **COMPLETE and ARCHIVED** (#301, #303, #299);
  Plan 264 is **COMPLETE** (#315); Plans 268/269/301/303/304/315 remain DRAFT.
  - **268** — DHM Barkhk delivery: parse, verify and import six Koshi/Narayani gauges —
    `DRAFT`, `depends_on: [264, 269]`. Five review rounds folded (2× Codex, 2× Claude, 1 set
    review). Fifteen of sixteen decisions closed; **D14 reopened** — the DHM daily QC
    threshold calibration, which needs a hydrologist for the values and must avoid three
    traps: deriving a threshold from the record it judges (circular), reusing the rating
    tables (not independent evidence), and publishing a restricted tabulated value unless
    passed through a deliberately lossy transform. The delivery itself is **unpublishable**;
    never check an excerpt into the repo.
  - **264** — QC rules select on network, not only parameter and cadence — **COMPLETE**,
    merged in PR #315 (`dbbea4d9`, 2026-09-26); `blocks: [268, 269]`. Adds network-specific
    selection with generic fallback, threads the station-network mapping through the checker,
    selection reporter and offline DHM mask, records configured rule versions on flags, and
    enforces that QC rules live in the shared base configuration. D4 is closed: hydromet rules
    extend that shared list. The Swiss selector-equivalence fixture is committed.
  - **269** — Per-station QC thresholds declared in onboarding configuration — `DRAFT`,
    unblocked by PR #315; `blocks: [268]`. Delivers the missing configuration and resolution
    path for station-specific observation thresholds, plus safe pending-network handling and an
    edit-time validator. The onboarding QC path, persistence and API remain out of scope; the DB
    tier is deferred to v1 and currently unowned. The latest round-6 review findings are folded;
    exact-state re-review is pending before READY.
  - **303** — DHM subdaily precipitation and temperature QC rules — `DRAFT`,
    `depends_on: [264, 272]`, blocked on Plan 301 T1's source interval/cadence contract. Adds
    only the DHM-network rule rows Plan 272 D4 requires; Plan 301 must not enable rainfall before
    these rows are selected and tested.
  - **304** — Daily QC neighbor context — `DRAFT`, `depends_on: [264, 272, 316, 317, 318]`.
    Restores daily discharge/water-level `rate_of_change` and `spike` inputs without widening the
    rows judged; short-window scheduled ingest and the onboarding QC follow-on must wait for it.

- **106** — v1 (Nepal DHM) critical-path roadmap — `DRAFT` (was `READY (locked)` until the
  2026-09-18 audit; waves 0-3 contain completed work, so it must not be implemented as a plan) — **the
  sequencing plan. Read this first for v1 planning.** Locks the wave order (0 stabilize →
  1 forcing → 2 obs/rating → 3 auth/deploy → 4 DHM go-live → 5 v1.x), classifies every
  remaining piece designable-now vs blocked-on-external-knowledge, and lists the
  collaborator questions (DHM/HSOL/gateway dev). v1.0 is **headless** (Flow 3/dashboard/
  bulletin/Bikram Sambat → v1.x). Reviewed via 2× WF1 plan-review + 2× Codex independent
  review (all fixes applied); the gateway-dispatch fix + multi-year backfill window are
  owned in Plan 082 Tasks 2C/3B.
- **080** — FI wheel distribution — `DEFERRED` (low-pri) — publish `forecastinterface`
  as a versioned wheel, migrate off the git-pin, drop the temporary CI wheel-guard
  (Plan 079). **Blocked externally** on FI hitting the private index. Packaging
  prerequisite for a Nepal handover.
- **081** — recap-dg-client forcing adapter — `DRAFT` — the Nepal forcing foundation
  (IFS/ERA5-Land time-series from the gateway). **Offline-completable** against fakes.
- **082** — recap Gateway operational + training readiness — `READY` — Flow-1
  forecast dispatch, cycle fallback, source-aware watchdog, coverage manifest, §5a
  polygon store/resolver, secret plumbing, runbook. **Implemented + Codex-reviewed to
  convergence (3 rounds), open in PR #91** (hold-at-PR; CI blocked on the
  `RECAP_DG_CLIENT_TOKEN` secret). Depends on 081/115a. Flow-6 reanalysis wiring +
  the training-gate/snow wiring are **carved out to Plan 121**.
- **121** — Recap Gateway: Flow-6 reanalysis + deferred integration follow-ons —
  `DRAFT (stub)` — carved out of 082: the Flow-6 `_ReanalysisAdapter` Protocol fork
  (115b1 mismatch), coverage training-gate wiring, snow-forecast Flow-1 wiring, and
  the `RECAP_DG_CLIENT_TOKEN` CI-secret follow-up. Needs the `plan` workflow before READY.
- **192** — Gateway forcing for 12300 on the mac-mini — `COMPLETE`, archived (2026-09-10) — Stage A passed
  2026-08-20; **Stage B has run as a standing daily feed for 21 days (18 clean)**: 51-member IFS to +14.75 d,
  snow since 09-07 (Plan 219), units settled by Plan 243, dead-man live and proven. Owner questions O1–O4 were
  answered by what was built (light shape, host-triggered, 14:00 UTC, no external HTTP). Corrected a real
  identity error (station code is `123`, `12300` is the Gateway HRU, polygon `g_123` — the loader derives
  `g_<station_code>`). ⛔ **Validated ACQUISITION, never the pipeline** — the nepal DB holds 0 forecasts,
  0 artifacts, 0 observations, 0 assignments; forecasting 12300 is Plan 139. 🔴 Surfaced an unowned
  prerequisite: nothing ingests ERA5-Land or JSNOW **reanalysis**, so `historical_forcing` is 0 and training
  has no history.
- **143** — DHM v1 station, basin and Gateway onboarding — `DRAFT` — consumes Plan 268's six station rows, binds the registered `nepal6_20260923` polygons, imports supported forcing and conditionally prepares approved, datum-compatible water-level targets; ends at model-onboarding readiness, not operational activation. Depends on Plans 120, 268, 304 and 315.
- **144** — Multi-track probabilistic forecasting — `SUPERSEDED by docs/design/forecast-cycle-redesign.md`
  (2026-07-23). The multi-track/ensemble orchestration is folded into the forecast-cycle redesign (its D1–D6
  decisions carry over). Six /plan stalls proved it needs a forecast-cycle re-architecture, not incremental patches.
- **145** — Future-snow forecast forcing wiring — `DRAFT` — carved from 139 W7, then **SPLIT** (2026-07-23). The
  FUTURE channel: `fetch_snow_forecast` (zero callers → broadcast no-op) scoped + wired into the cycle → store →
  broadcast WITH snow-scoped degradation, + the aggregation fix (`swe`/`snow_depth` MEAN, `snowmelt` **SUM**). No
  blocker; unblocks 144. Needs independent Claude and Codex reviews before READY.
- **146** — Antecedent (past) snow reanalysis channel — `DRAFT` — the SPLIT-off load-bearing half: a supported
  `ForcingSource` for `recap_snow_reanalysis` + a **dedicated recap-reanalysis ingest flow/schedule** (the
  blocker — no production caller today) + read-side hybrid snow tier so stored snow reaches `past_dynamic` in
  training/hindcast/live. Depends on 082 + 145. Blocks 139/144 snow-lookback. Needs `/plan`.
- **148** — Forecast-cycle redesign **Phase 1**: `ModelRunContext` + per-assignment `prior_state` —
  `COMPLETE (archived, PR #139)` — first behaviour-preserving slice of `docs/design/forecast-cycle-redesign.md`:
  split warm-up state loading to per-`(station_id, model_id)` + the assignment-keyed run unit. `/plan`-reviewed (0
  blockers, majors folded). Fixes a latent shared-state bug; foundation for later phases. Merged to `main` at
  `fa14b9a`. See [archive/148-forecast-redesign-phase1-modelruncontext.md](archive/148-forecast-redesign-phase1-modelruncontext.md).
- **149** — Reconcile the forecast-cycle redesign with the repo architecture + standards — `SUPERSEDED / ABSORBED`
  (2026-07-24). The alignment findings + 3 real contract gaps were folded DIRECTLY into `forecast-cycle-redesign.md`
  (§ Formal contracts/layering), `architecture-context.md` (Flow 1 + combination rule), and
  `types-and-protocols.md` (widened Protocol + types) — a /plan loop over-expanded the meta-plan, so the
  reconciliation was done by direct fold + an alignment re-review instead. Do not implement from 149.
- **150** — Forecast-cycle redesign **Phase 2**: per-assignment outcome SHAPE — **COMPLETE, ARCHIVED (#141, 2026-08-10)** — the
  outcome-SHAPE sub-slice of build-sequence item 2 (option A): `_run_single_model` /
  `run_all_station_forecasts` / `MultiModelForecastResult.failed_models` migrated from `StationForecastResult | str`
  to a discriminated `AssignmentSuccess | AssignmentFailure` (assignment-level `AssignmentFailureCause`), plus a
  loop-level backstop closing a latent fallback-invariant gap (an unanticipated exception in a lower-priority
  assignment no longer darkens a station whose higher-priority assignment already succeeded). Does NOT complete
  build-item 2 alone — the FI typed `ModelFailure`-signal preservation (Phase 2-FI follow-on) and the runner's
  `ModelRunContext`-consumption seam (Phase 3) are explicit named follow-ons. See [archive/150-forecast-redesign-phase2-assignment-outcome.md](archive/150-forecast-redesign-phase2-assignment-outcome.md).
- **151** — Forecast-cycle redesign **Phase 3**: `ForcingTrackKey` projection + per-track resolution +
  per-assignment assembly — `MERGED (T1–T8a: PRs #182/#192/#196), T8b implemented (hold-at-PR)` — the one atomic
  phase the redesign refuses to split: per-requirement track projection + dedup (T1–T3), the
  `CandidateAwareForecastSource` contract + `recap_gateway` migration (T4), per-track walk-back resolution with
  exact-member-set completeness (T5), per-assignment assembly (T6), the runner consumption seam (T7), and the
  dormant flow-level policy carrier / retrying task / freshness / preflight / D30 helpers (T8a) are all on `main`.
  **T8b** (this run) wires the `isinstance` dispatch, re-scopes Phase A, and adds the flow-level goldens that are
  now the only protection for the live control-only route — migrates `recap_gateway`-served, non-group stations
  only (D6/D12/D30); MeteoSwiss and group CONTROL stay on the legacy superset path. See
  [151-forecast-redesign-phase3-track-resolution-assembly.md](archive/151-forecast-redesign-phase3-track-resolution-assembly.md).
- **124** — Station active-assignment consistency — `COMPLETE` (#95), archived. It was
  scope-locked and cleared to implement directly on 2026-07-18, and then was: NARROW — INACTIVE station assignments stop forecasting + leave the
  alert-priority index (match the group path); the fallback-priority-drift health check stays
  **all-status** (Plan 100 untouched). Fix = a separate active-filtered view for forecasting/alerts,
  raw dict kept for drift. (`plan` workflow escalated 3× by over-scoping a tiny fix — implementing
  directly with a red-first test instead.) Store stays all-status (real callers); no group-side bug.
- **125** — Inactive assignments fully inert — `DRAFT (stub)` — follow-up to 124: also make INACTIVE
  invisible to the fallback-priority-drift detector, which **requires an owner-ratified supersession
  of Plan 100 C1c**. Coherence/cleanup; not deployment-critical. Depends on 124.
- **127** — fc-first minimal unblock — **MERGED (#97 → `d317af0`, 2026-07-19)** — the
  deployment-critical forcing path is COMPLETE (082 + 124 + 127). Tolerant `pf` fetch + `SINGLE`-model
  bare columns keyed on `ensemble_mode` + a mixed-model fail-fast guard. Critical Codex review caught
  a ratchet-masked type bug + a mixed-model regression (both fixed, round-2 APPROVE). Sandro's live
  control-only models now forecast end-to-end.
- **123** — Model-driven forcing membership (CONTROL_ONLY + NONE) — `DRAFT (DEFERRED)` — the full
  flow-level membership design (skip `pf` entirely for control-only + real `NONE` skip +
  staleness/provenance). Genuinely multi-part; **ESCALATED 2×**. **No longer the blocker** (127
  unblocks the deployment); this is the efficiency/completeness follow-up, revisit after 127.
- **126** — Requirement-aware ensemble cycle resolution — `SUPERSEDED by docs/design/forecast-cycle-redesign.md`
  (2026-07-23). Cycle-resolution can't bolt onto the single-cycle-per-batch v0 flow; folded into the redesign
  (components 2/3: per-requirement resolved-cycle map + candidate-local accumulation).
- **047** — Nepal v1 data sources umbrella (IFS, DHM, ERA5-Land) — `DRAFT (stub)` —
  depends on 081/082.
- **117** — Basin/static artifact architecture alignment — `READY` — documents the
  **adjacent** basin/static extraction artifact boundary: SAP3 consumes a validated
  package and does not integrate the extractor's code. Covers the GeoPackage
  terminology + naming rules (`g_<station_code>`), single-kind Gateway HRUs, and the
  confirmed static-Parquet shape. Unblocks the
  **basin/static architecture cleanup only** — 047 separately needs its
  **re-scope per Plan 106** before it advances.
- **120** — Basin/static importer + §5a persistence + versioned basin state — **COMPLETE, ARCHIVED
  (all 4 slices merged: #124 foundation / #126 loader / #128 write-side / #129 entrypoint+docs,
  2026-07-23).** Build-complete: a basin/static package imports end-to-end via
  `import_basin_package_from_directory` / `python -m sapphire_flow.cli.import_basin_package`; Plan 143
  is tasked with using the `import_loaded_basin_package` core for the Nepal package. Remaining gate is OPERATIONAL only (run
  the importer against a real accepted package before 082's resolver returns non-`None` in production —
  the "Production-gate note"). See [archive/120-basin-static-importer.md](archive/120-basin-static-importer.md).
- **147** — Auth / RBAC / audit + tenant write-isolation foundation (v1.0 headless) — **COMPLETE, ARCHIVED
  (all 5 slices merged: #130 tenant model / #131 audit-log substrate / #132 access-token auth+enforcement /
  #134 least-privilege DB roles / #140 tenant write-isolation, 2026-08-10).** Config-declared `WritePrincipal`
  (never target-derived, never a read-token) enforced pre-write on every flow/CLI write path, with success-path
  mutation+audit atomicity; hardened through 3 independent Codex rounds. Unblocks Flow-0 Nepal onboarding. See
  [archive/147-auth-rbac-tenant-isolation.md](archive/147-auth-rbac-tenant-isolation.md).
- **035** — Rating-curve provenance for skill integrity — `PARTIAL` — v1 DHM hQ.
- **017** — Manual vs automatic station support — `DRAFT` — v1, DHM mixed networks.
- **015** — Calculated station support (component-derived) — **MERGED (#109 storage+trigger,
  #112 Flow 2 step-2.5 derivation, #113 TOML onboarding), 2026-07-21.** Move to archive/ once
  confirmed. Ungauged half split to 016.
- **016** — Ungauged station support — `DRAFT` — split out of 015. **Reframed 2026-07-21:**
  not fully blocked — a **SAP3 scaffolding slice is buildable now** (Step-8 gate refactor,
  zero-row past_targets plumbing, gauging_status branching, donor-CV skill framework). *Live*
  ungauged forecasting still needs an FI operational model (modelling team; mountain
  snow+glacier+bands — paradigm under discussion) + basin geometry (117/120). The floor is
  deferrable + downstream of the model choice; basin user-upload+security is optional.
- **194** — The backup target must be the device it claims to be — `COMPLETE`, archived — shipped in
  PR #200 (`357386b`) with the marker-file follow-up in #201. `/plan` escalated and over-expanded on
  this one (Codex failed 3 of 4 rounds); it was reconstructed and reviewed by hand. Carried forward:
  the device predicate now exists in **three** independent copies (`bootstrap-mac-mini.sh`,
  `start-sapphire.sh`, `watchdog.py`) — verified identical 2026-08-21, but nothing keeps them so.
- **195** — A launchd agent that cannot run must not look healthy — `COMPLETE`, archived — shipped in
  PR #216 (`6af4aa0`, `v0.1.817`). The watchdog now probes `launchctl list` (never `print`, whose
  output Apple explicitly disclaims) for the installer-managed labels, latched per label, with
  probe-unreadability latched separately so "the monitor stopped monitoring" cannot present as "no
  agents failing". Four Codex rounds; two blockers were errors introduced *while fixing* earlier
  findings, and two properties were asserted-but-unlocked until mutation testing exposed them.
  ⚠️ **Not yet deployed to the mini.**
- **208** — Backups must leave the box — `DRAFT` — the off-box sink Plan 162 D4 named but never
  created (162's `blocks:` is empty). Owner 2026-08-28: no backup drive for the mini ever; separation
  arrives with the **AWS** deployment, sink is **S3**. Two findings make it more than a port: 162
  deferred this on the reasoning that "an encrypted artifact is safe wherever it lands", but
  **encryption never shipped** (D5 was Phase B; the mini's dumps are plaintext, verified) — so it is a
  prerequisite, not a companion. And **Plan 194's device predicate does not port**: meaningless on S3,
  and on EBS it passes trivially while the volume still shares an AZ with the database — a green light
  for separation that does not exist. Four decisions open.
- **102** — Dashboard multi-parameter observation visibility — `PARTIAL` (per-parameter
  selector shipped; the ratified multi-panel layout did not).
- **104** — Dashboard hardening (links, chart defaults, skill-chart) — `PARTIAL` (issue 1
  of 4 fixed; issue 2 open, 3-4 unassessed).
- **099** — Dashboard display timezone — **P1 shipped** (UTC axis labels, #59); **P2
  pending** (UTC↔Europe/Zurich toggle).
- **090** — NWP incomplete-cycle selection + horizon-coverage — **P1 shipped**
  (age-delay guard, #49); **P2 pending** (terminal-valid-time refetch).
- **113** — Align forecast schedule with NWP cycle delivery — `DRAFT` (low-pri) —
  the forecast cron sits on the NWP cycle boundaries → every run uses a 6h-stale
  `fallback` cycle and the **00:00 slot silently drops to obs-only** (1 clean daily
  bucket short). Chosen direction = offset the schedule (opt B); documented, not urgent.
  Diagnosed 2026-07-13.
- **049** — Cloudflare public URL + Entra SSO for staging — `DRAFT` — depends on 046.
- **108** — Swiss market standards posture — `DRAFT` (low-priority v1+) —
  nFADP/DSG, OGC, INTERLIS, and SVGW W12 decision gates for future Swiss partner
  readiness. Docs-first; no change to the v1.0 Nepal critical path.
- **266** — CAP 1.2-conformant hydrological alert publication — `DRAFT`
  (low-priority v1+) — preserve internal alerts as decision-support inputs, then add an
  opt-in CAP Message Producer, immutable Alert/Update/Cancel history, explicit authority
  and warning-area policy, and an authenticated restricted CAP source/RSS feed. `Actual`
  is observation-only until forecast review/publication provenance exists. Needs the
  ordinary Claude + Codex plan reviews and an additional operational-warning/security
  review before READY.
- **111** — Benchmarking against BAFU's operational forecasts — collector **MERGED
  (#72)**; scorer/publication **BLOCKED on external gate G1** (low-priority). Route-C
  hourly collector archives hydrodaten Plotly-JSON forecasts (54 stations, quantiles
  not members, ~5-day horizon) to a quarantined parquet store; evaluation-only,
  forward-only. Dev collection validated 2026-07-10. G3 scorer + any published
  comparison stay gated on the (unsent) BAFU licence request.
- **111b** — Mac-mini deployment runbook for the collector — `COMPLETE` (runbook; #73,
  archived 2026-09-11) —
  deploy wiring in PR #73; hourly schedule + quarantined volume + overlay switch.
  See [111b-bafu-collector-macmini-deployment.md](archive/111b-bafu-collector-macmini-deployment.md).
- **071** — v0b weather-history: MeteoSwiss daily reanalysis adapter — `DRAFT`.
- **072** — v0b weather-history: hybrid forcing resolver — `DRAFT`.
- **066** — Configurable retrain data-window — `DRAFT`.
- **068** — `onboard-stations` parallelization + async backfill — `DRAFT` — depends
  on 038 + 040.
- **057** — API route-module tests — `DRAFT (stub)`.

## Active — developer workflow (C)

- **325** — [A compliance row proves our code does something, not that WMO asks for it](325-compliance-rows-must-cite-the-wmo-clause.md)
  — `DRAFT`, `open_decisions: [D1, D2, D3]`. Raised by the owner on seeing a
  compliance row's CODE citations go stale: the row's OTHER half was never
  precise. 🔴 **All eleven compliance rows cite a publication; none cites a
  clause, section or page** — four rest on `WMO-168 Vol I`, a few hundred pages.
  ⭐ The standing evidence rule (253 T4c) disciplines the code half — "a named,
  runnable test" — and is **silent on whether the requirement was ever located**,
  so a row can pass it completely while nobody has opened the publication.
  ⛔ Not a "standards are vague" problem: § 2 of the same document already names
  chapter-level locators. ⚖️ Owner 2026-09-24: **T1 approved to ship now** (the
  honest marker + extending the standing rule to BOTH halves); **T2/T3 are LOW
  PRIORITY** backlog and must not displace the v1 critical path; and the target
  is publication number + URL + **CHAPTER**, ⛔ not clause or page.

- **324** — [Plan 272's last four items](archive/324-plan-272-last-four-items.md) — **`COMPLETE`, ARCHIVED** (merged #305, 2026-09-24)
  (orchestrator 2026-09-24, after three review rounds — the middle one rejected a
  fold that acknowledged findings in an appendix while the task text still said
  the old thing). From a full item-by-item audit of 272 against `main`:
  its BEHAVIOUR is complete and deployed (316/317/318 close triage §A and §B),
  but four items it marked "DO NOW" or "UNCONDITIONALLY" are unshipped and
  **owned by no follow-on**. 🔴 The headline one is a WMO compliance row
  (`standards/wmo.md:187`) whose three code citations now land on a docstring
  terminator, a parameter name and a keyword argument — the exact failure class
  272's own T4 invoked Plan 023 about. ⛔ TRAP: there are TWO `_RULE_VERSION`
  constants and only the observation one is in scope. D1 asks how the stale-text
  sweep is bounded, because two independent counts of the same claim already
  disagree.

- 🔴 **COLLISION, 2026-09-25 — Plans 328 and 341 are independently inventing the same mechanism.**
  328 adds a `SUPERSEDED` forecast status; **341 adds `WITHDRAWN`, described in the same words** —
  *"removes it from current consumer reads"*. Same enum, same DB CHECK, same unfiltered readers
  (`fetch_latest_forecast`, `fetch_forecasts_for_cycle`). ⛔ **341 does not know the unique index's
  predicate is dead**, so a withdrawn forecast would still occupy the slot and no replacement could
  be published at that key. ⇒ **The predicate should exclude any not-current state, not one named
  value.** ⚠️ Not urgent — 341 is `DRAFT — not implementable` — but the two sides should talk
  before either builds the status change. Neither plan's frontmatter mentions the other.

- **405** — [Four warm-start requirements that never shipped, and two boundaries nothing tests](405-warm-start-gaps-and-untested-boundaries.md)
  — `DRAFT`, **no open decisions** (D1 closed on (b), 2026-09-26), **`depends_on: [399]`**. Drafted from two independent post-merge
  reviews of **399's shipped code** — the first able to check that plan against reality rather than
  itself. ⭐ **No new capability, with one declared exception** (T1's refusal-before-training ordering, which 399 never required): every other item is something 399 asserts and the code does not do.
  🔴 **A retrain-of-a-retrain CRASHES** — the inherited NULL-path reason is dropped and the record
  type raises **after the artifact is stored**, so a saved model loses its provenance (demonstrated by
  execution). 🔴 **The changed-template refusal does not exist** (`installed_config_sha256` is
  accepted and never compared; the resolver has no test at all). 🔴 **The donor config path is never
  recorded, for any donor, ever.** 🔴 **Neither retrain boundary is tested** — gutting the adapter's
  `retrain` body leaves **723 tests passing** *(that SELECTION, not the 6043-test suite)*. ⚖️ **D1 closed on (b)**: the donor's params PATH stays
  NULL with a reason true of its class, noting its configuration is reachable via `base_artifact_id` —
  ⛔ *no migration, no new column.* *(The question was what "inspect the donor's params" means now that
  a retrained donor demonstrably HAS a recorded config.)* ⛔ 399's staging run stays with 399.

- **399** — [SAP3 never calls the warm-start retrain both sides already implement](399-wire-warm-start-retrain.md)
  — ⚖️ **MERGED 2026-09-26 (#314, 0.1.994, migration 0060)** but **`PARTIALLY_IMPLEMENTED`**. ⛔ *An
  earlier version of this entry said "T1/T2/T4 complete, ONE thing remains" — **false**; a post-merge
  review against the shipped code found four requirements that never shipped and THREE verification
  bullets never written.* 🔴 **EIGHT items remain** — see the plan's Status table; **Plan 405** carries
  the SEVEN code/test ones (four gaps + three bullets), the staging run stays here. ⛔ *An earlier
  version said "seven remain, six carried" — the pre-fold arithmetic, from counting two bullets.* 🔴 *A retrain-of-a-retrain currently CRASHES after
  storing the artifact.* ⚠️ **NOT deployed** — the mini runs 0.1.986. `cmal_small` was trained on ERA5-Land
  and is served MeteoSwiss forcing; the owner chose to fine-tune on Swiss forcing
  rather than onboard ERA5-Land.
  ⭐ **Asks for NO new capability.** FI already defines `RetrainableModel.retrain()`,
  aquacast already implements it with an identical signature, and
  `assemble_group_training_data` already reads the same reanalysis binding the
  operational path uses. ⚖️ *Was: "SAP3 is the only side that does not participate — no call site anywhere, and the
  passthrough is missing at all four of our layers." **Fixed by #314**; kept as the problem
  statement, not a current fact.*
  ⚖️ *Was: "the empty config mapping is hardcoded at SEVEN sites, so there is no model-config
  channel at all" and "group training is broken today — the flow attaches no station-code
  resolver, which is why no group artifact has ever been produced here". **Both fixed by #314**;
  kept as the problem statement. ⛔ Neither is a current fact.* (D3 closed: config supplied as a
  run parameter; the channel is T2's, the RECORD is T4's.)
  ⚠️ ⚖️ D2 (closed: REFUSE) deliberately diverges from FI's suggested fall-back to
  `train`. 🔴 A bare structural `isinstance` would silently defeat that refusal — the
  adapter has no `__getattr__`, so support must be read off the INNER model.
  **Related:** `docs/fi-issues/004` (neither item blocks this).

- **329** — [The Forecast Lab snapshot cannot see a group-assigned model](329-the-snapshot-cannot-see-a-group-model.md)
  — `READY`, **no open decisions** (both closed by the owner 2026-09-25; 4 review rounds). Raised by the Flow Map session:
  `cmal_small` forecasts do not appear in the document the map consumes.
  ⭐ **The cause is ours, and it is the ENUMERATION, not the retrieval.**
  `fetch_active_model_assignments` asks only `station_store.fetch_model_assignments`,
  and `ForecastLabStores` carries no group store — so the snapshot
  **structurally cannot** see a group assignment. Once the model appears in
  the list, the existing per-station lookup finds its forecast unchanged.
  🔑 Small by construction: `fetch_groups_for_station` already exists, and
  `ModelAssignment` vs `GroupModelAssignment` differ by ONE field.
  ⚖️ **D1 closed: the entry does NOT say it is group-scoped** — the map does not care, so no
  `scope` field and no change to `forecast-lab-snapshot/v2`. ⛔ *An earlier version of this entry
  called that "the map's schema" — **wrong owner**: the export contract is OURS, generated from our
  own models, and it forbids unknown fields.*
  ⚖️ **D2 closed: a group model MAY be a station's headline forecast.** ⛔ *An earlier version said
  "today ours is not, but only because its priority is 50" — **false**. `is_primary` goes to the
  first RENDERABLE entry, so a priority-50 group model becomes primary wherever the 10/12/20/30
  models have no renderable forecast. That happens today, on real stations.*

- **328** — [Replacing a forecast — supersession, and the readers that would still serve the old one](328-replacing-a-forecast-supersession.md)
  — **`READY`** (final review: no findings), `depends_on: [327]`, no open decisions. Split out of 327 on
  2026-09-25 because resume and supersession were entangled: a review found 327
  scheduled its resume task before the supersession task that its own
  verification required. ⚖️ Owner: *"replace, keep the old marked"*, and make the
  dead predicate work rather than delete it. 🔴 **The finding that makes this more
  than a schema change: `fetch_latest_forecast()` and `fetch_forecasts_for_cycle()`
  have NO status filter**, and the Forecast Lab uses both — so after a
  supersession they would serve the OLD row. ⛔ A plan scoped to
  "status-filtering consumers" would have missed exactly the two readers that
  matter. ⚠️ Evidence cannot be discarded (migration 0057 rejects
  UPDATE/DELETE/TRUNCATE), so superseded forecasts are permanent — a retention
  question, named not answered.

- **327** — [A forecast cycle that died partway cannot be resumed](327-a-forecast-cycle-cannot-be-re-run.md) — **`READY`**
  — `DRAFT`, `open_decisions: [D1, D2, D3]`. Demonstrated 2026-09-25, not
  predicted: a second run for the same pinned issue time fails on
  `uq_forecasts_station_model_issued_param` and the whole flow goes to Failed.
  ⭐ Nothing is corrupted — 653 rows before and after — and that protective half
  must survive. But a cycle that dies halfway cannot be re-run.
  🔴 **The escape hatch the schema appears to offer is DEAD**: the index is
  partial, `WHERE status <> 'superseded'`, and `ForecastStatus` has no such
  member (RAW/REVIEWED/PUBLISHED) — the only `SUPERSEDED` belongs to
  `ModelArtifactStatus`. The predicate excludes an impossible value, so the
  index is effectively full, and a reader of `metadata.py` would conclude
  otherwise. ⚠️ `touchpoint-maps.md:757` documented this and said *"confirm this
  is intended"*; nobody did. 🔑 D1 separates three things called "retry" —
  resume a half-done cycle, re-issue a corrected forecast, and silent overwrite,
  which must be forbidden once anything is published.

- **326** — [Nothing records the hours at which a model may be issued](326-honour-the-declared-issue-hours.md)
  — `DRAFT`, `open_decisions: [D1, D2, D3]`, **`blocks: [262]`**. Plan 311's
  closure needs an ENFORCED midnight restriction and there is nowhere to put one.
  ⛔ **This plan's first draft claimed the model already declares its issue hours
  and we ignore them — an independent review proved that FALSE and it is
  corrected in the text.** aquacast's `issue_hours` is a training-template field,
  *"only meaningful for HOURLY regimes"*, and a non-hourly model **must** set
  `(0,)` or aquacast raises ⇒ a mandatory constant, not a declaration.
  🔴 Measured: **FI cannot express issue hours** (pinned `ad19597`), selection
  cannot see the cycle time, and the assignment record has no field for it.
  ⭐ D1 asks whether this is a MODEL property (⇒ an FI issue and an upstream
  round-trip) or a DEPLOYMENT decision (⇒ ours, and not a workaround — *we* chose
  to restrict the pilot; the model never asked). ⚠️ An hour match alone does not
  fix it, and flooring the issue time is not small — it touches forecast
  uniqueness, lead times, group state and rating curves.

- **323** — [Five Swiss stations report hourly and select no QC rule at all](323-hourly-stations-select-no-qc-rule.md)
  — **`READY`** (orchestrator, 2026-09-26, owner-confirmed), no open decisions, `blocks: [400, 403]`;
  READY from both reviewers in rounds 8-11. ⚠️ T1 needs staging, which is off-network for now. Found from a live Slack warning on
  2026-09-24: five BAFU gauges deliver HOURLY, the rule set declares only 600 s and 86400 s,
  and selection matches by exact equality — so they resolve ZERO rules. Not new: their
  earlier rows were fail-open passed (~1,277 readings never checked). Thresholds come from
  the gauges' own measured hourly behaviour but **loose** (owner 2026-09-26, per the
  loose-first QC posture): range bounds and `k_sigma` copied from 600 s; change limits the
  larger of 2 × P99.9 and the 600 s value, each statistic in its rule's own form (water level
  datum-relative, discharge spike relative); CAMELS daily imports excluded (D1). ⚠️ The rows
  apply to every hourly series, not only these five. Hourly gets each parameter's 600 s rule shape **less `frozen_sensor`**, which has
  **no plan yet**; water temperature gets no `spike` (D2). 🔴 **It does not silence the
  watchdog:** ~5% of hourly checks still infer no cadence, or one no rule declares, from a single missing reading —
  the owner accepted that leftover (D3); 400 closes it. ⛔ Plan 264 does NOT own
  nearest-rule matching. 🔴 **D4/T4 (owner 2026-09-26): a reading passes only if some
  selected check could actually judge it** — else `QC_UNCHECKED`, reason `no_check_could_run`,
  in a separate health record the watchdog does **not** alarm on (D5), counted by distinct
  reading ids, not records. Wide reach: **no Swiss
  river station has a water-level datum**, so water level runs only neighbour rules fleet-wide;
  datums are Plan 403. Hourly rows and the guard deploy together (T5). ⚠️ 313 and 315 do not yet
  carry the reverse notes.

- **400** — [Work out a series' reporting interval from its recent readings, not from the two-hour check window](400-infer-cadence-from-recent-readings.md)
  — `DRAFT`, redesigned 2026-09-26; **READY from both reviewers in rounds 6, 7 and 8**
  (round 8 CLEAN; only its § Status and § Review record changed since); `depends_on: [323]`; number granted
  by the owner 2026-09-25. Infers each group's cadence from a **separate, configurable
  look-back** (`[qc_rules.cadence_inference]`: last 50 distinct readings within 30 days by
  default, SQL `LIMIT`) while the rules keep running on the 2 h window — the owner's
  option B, after both reviews showed a 24 h check window changes what the rules compare.
  Replayed on staging: **100%** correct on hourly and 10-minute series (the 2 h window
  gives 95.3% hourly). Revives Plan 272's parked bounded-lookback design on a new argument.
  ⚠️ Exposure it states: a repaired check runs `rate_of_change`/`spike` across one missing
  reading (Plan 313's arithmetic) — within 2 h ordinarily, **not bounded on the DHM catch-up
  path**. 🔴 Relies on 323's new guard that a reading passes only if some selected check could
  actually judge it (`no_check_could_run`). ⚠️ Cannot end the leftover for datum-less water
  level — that is Plan 403. ⚠️ Uses the network-aware `check` signature merged in PR #315.

- **403** — [Give Swiss river stations their surveyed gauge-zero datum](403-swiss-river-gauge-zero-datums.md)
  — `DRAFT`, `depends_on: [323]`; **READY from both reviewers in rounds 10 and 11** (round 11
  on its current text; two LOW precision points from round 11 left for the implementer: whether an EXISTING river's
  unsupported unit fails re-onboarding (likely yes), and that a unit entry without a datum entry is
  ignored);
  number granted by the owner 2026-09-26. BAFU delivers water level in
  m a.s.l., and the water-level range check is relative to the gauge zero; Plan 101 skips the
  datum-dependent rules "until the datum is set" — and for **rivers it never was**: CAMELS
  onboarding forces `None` for every river, so water level on the Swiss river stations (most
  of the ~142 delivering it) is checked only by neighbour-comparing rules (the root of Plan 323 D4's unjudged leftover).
  Sources each Pegelnullpunkt from the **hydrological yearbook** (owner), **validates it
  against the station's own readings** (a wrong datum fails every reading), stores it in the
  existing `[onboarding.water_level_datums_masl]` table, applies it to rivers, and sets it on
  existing rows through a datum-only command — validated at apply time, tenant-checked and
  audited like onboarding's writes (not a re-onboarding). A stage-relative LINDAS series gets
  datum 0 / `m`. ⚠️ River water-level
  baselines are still never computed, so `gross_outlier` stays inert there.

- **319** — [Shard the unit suite across parallel CI jobs](319-shard-the-unit-suite-across-ci-jobs.md)
  — `READY`, both owner decisions closed 2026-09-24 (four shards; the shards'
  coverage stitched back into one number; ⛔ no threshold introduced — none
  exists today). Follows PR #300, which fixed test-worker ISOLATION and so made
  CI reliable without making it faster: CI already ran `-n auto`, and the gap is
  the runner (2–4 cores against 12 locally). Measured: 11m18s serial / 2m49s
  `-n auto` / 3m09s with coverage, against 11m53s–13m22s for the same CI job.
  ⚠️ Two things a reader should carry from it: **N shards pay the geospatial apt
  setup N times** and take N times the exposure to a step whose own comment
  records eight stalls in one day — stated as a risk to measure, not a predicted
  failure rate; and **a test in no shard runs nowhere and nothing fails**, which
  caught this plan's own first draft (a directory split that silently dropped the
  44 tests sitting directly in `tests/unit/`). The split is therefore proved by
  collected NODE ID, not by directory. Three review rounds; the evidence was
  wrong twice — once from counting `def test_` (parametrisation diverges), once
  from running the count in the WRONG WORKTREE.
- **231 / 232 / 233** — `SUPERSEDED` by 242 after PR #247 dogfood showed their
  manifests, fingerprints, evidence schemas, and confirmation mechanics made the
  workflows slower and less reliable.
- **242** — Plain workflow prompts — `COMPLETE` — removed the automated plan and
  implementation engines; kept three small prompts and owner-run review passes.
- **241** — Adopt the declared horizon semantics — `READY` — the FI adapter
  dropped a model's `AT_MOST`/`min_future_steps` declaration, so the resolver
  could never see it; T4 then persists a forecast's cadence, which T2/T3 make
  reachable. PR #258.
- **248** — Tighten `forecasts.time_step_seconds` to `NOT NULL` — `DRAFT`,
  **REWRITTEN 2026-09-10** after three Codex rounds. Now TWO tasks split on
  whether the artifact SHIPS. **T1** clears the 7 793 legacy NULL rows on the
  disposable mac-mini sandbox — two statements, children first, no backup and no
  manifest, because the data is throwaway and the cycle refills at ~1 336/day.
  **T2** is the shipped change and gets full rigor: a migration chained from the
  current head (0055) to `SET NOT NULL`, retiring the reader's whole
  nullable-column branch, deleting BOTH legacy tests whose helper writes NULL,
  a DB-backed migration test, the head pin, pyright, and an independent review.
  ⛔ **T1's shortcut is SANDBOX-ONLY.** The careful backfill and the deletion
  runbook that three review rounds produced are preserved in the plan's
  § *operational variant* — that is what a hydromet deployment does instead, and
  running T1 against one would destroy a real record. Measured 2026-09-10:
  11 083 rows, 7 793 NULL (cohort unchanged across three censuses), 3 290
  stamped. ⛔ Awaiting owner READY.
- **265** — Discard the 69 non-uniform `_pooled` forecasts — `SUPERSEDED` by
  248's rewrite, same day it was created. It held a deletion runbook for
  precious data; on a disposable sandbox that machinery is not warranted. Its
  requirements travelled into 248 § *operational variant*; read it for those, not
  for instructions.

## Deferred

- **039** — Sensor/Model failure visibility — `DEFERRED` → Flow 4 (pipeline
  monitoring).
- **042** — API Key Auth + Client SDK — `PARTIAL` — auth/RBAC/audit and tenant isolation
  shipped via archived Plan 147; the client-SDK half stays deferred to post-v0.

## v1 gaps — work with NO plan yet (draft before the waves that need them)

> **All now sequenced + classified in Plan 106** (wave, owner, designable-now vs blocked).
> This list is the raw inventory; Plan 106 is the ordered plan. Gap #4 (ERA5-Land) is
> subsumed by 081/082; the training-forcing backfill window is owned in 082 Task 3B.

These are named in `architecture-context.md` / `v0-scope.md` but have no dedicated plan:

1. **Multi-tenant / deployment isolation** (east HSOL / west DHM) — blocks the
   multi-tenant wave.
2. **DHM observation adapter** — now tracked by **Plan 300 (DRAFT)**, based on
   owner-confirmed request/response examples; distinct from the gateway *forcing*
   adapter 081. Generic unit conversion remains gap #3 below.
3. **water_level unit normalization** — cm / m-above-ground → canonical metres at the
   adapter boundary (Plan 101 only *guards* the metres assumption).
4. **ERA5-Land reanalysis adapter** (`WeatherReanalysisSource` for Nepal) — folded
   verbally into 081/047, no dedicated build plan.
5. **Flow 0 Nepal deployment onboarding** — the six-station basin/Gateway and
   model-onboarding readiness path is tracked in Plan 143; the basin/static
   artifact boundary is tracked in Plan 117.
6. **Rating-curve h→Q ingestion + reprocessing** (Flow 12 Branch A) — 035 covers
   provenance only.
7. ~~**Auth / RBAC / audit** for the multi-tenant handover~~ — **no longer a gap**:
   shipped by archived Plan 147 (`status: COMPLETE`, 2026-08-10), which folded in
   the deferred Plan 042. Only 042's client-SDK half remains, deferred to post-v0
   rather than unplanned.
8. **Flow 4 pipeline monitoring** full build (v0 is basic-only; 039 folds in).
9. **Bikram Sambat calendar + bulletin generation** (Nepal official reporting).

> **NOT needed for v1: elevation-band / gridded NWP extraction.** Nepal forcing
> arrives as **basin/band time-series directly from the Data Gateway API** — SAP3
> does not extract from grids for Nepal. (The ICON-mesh extraction, Plan 087, is
> Swiss/v0-only.)

## Archived

See [archive/](archive/) for completed and archived plans (124 entries).

## Superseded / stranded branches (recorded 2026-08-17)

**`docs/plan-158-session-independence` — SUPERSEDED, do not build from it.** Its plan docs live only on that
branch and were never on `main`; it is now **82+ commits behind**. Everything operationally load-bearing in it
has been rebuilt on current `main` instead, because the branch had diverged too far to merge:

| Plan 158 task | Delivered by |
|---|---|
| T1 — dead-man ping | **Plan 163** (merged, PR #162) |
| T1b — forecast-production freshness | **Plan 116** (merged, PR #167) — *small version; the `(station, model, parameter)` coverage ledger was deliberately excluded after it drew 5 blockers* |
| T2/T3/T5 — watchdog in the system domain | **Plan 164** (READY — console runbook + fresh-host installer guard) |

**Still unique to that branch, if anyone wants it:** the Docker endpoint contract (`scripts/launchd/docker-endpoint.sh`,
`SAPPHIRE_DOCKER_BIN`/`SAPPHIRE_DOCKER_HOST`), the `bootstrap-mac-mini.sh` service-account and teardown fixes,
and the excluded coverage ledger. Extract deliberately; do not merge the branch.

**⚠️ Plan-number collision:** that branch also contains a `159-headless-container-runtime-migration.md`, while
`main`'s **159** is `aquacast shim (in-repo optional extra) + forecast-cycle worker image` — a different plan by
another session. Renumber the headless-runtime plan if it is ever revived.
