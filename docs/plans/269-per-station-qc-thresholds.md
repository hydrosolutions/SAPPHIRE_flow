---
status: DRAFT
created: 2026-09-11
revised: 2026-09-11
plan: 269
title: Per-station QC thresholds declared in onboarding configuration
scope: Deliver per-station observation QC threshold overrides through station onboarding configuration — a validated TOML surface, one pure resolution boundary shared by every caller, and threading into all three live observation-QC entrypoints — so a per-station ceiling can be declared at onboarding and corrected by editing configuration. NOT a database table, store or migration (the spec defers that to v1), NOT the DHM threshold values themselves (Plan 268 D14), NOT forecast QC overrides, NOT rule selection (Plan 264), NOT an API surface, NOT a new rule kind.
blocks: [268]
related: [264, 268, 012]
reviews:
  - "codex 2026-09-11 r1 — NOT READY, 5 blockers + 5 majors + 2 minors; killed the write path and the migration"
  - "claude 2026-09-11 r1 — NOT READY, 3 blockers + 8 majors + 5 minors; same central defect found independently"
  - "codex 2026-09-11 r2 — NOT READY, 3 blockers + 6 majors + 1 minor; found a third entrypoint and the 264 signature break"
  - "claude 2026-09-11 r2 — NOT READY, 3 blockers + 7 majors + 6 minors; measured the staging blast radius and the overlay trap"
  - "codex 2026-09-11 r3 — NOT READY, 1 blocker + 3 majors + 2 minors; the strict-ordering bootstrap contradiction"
  - "claude 2026-09-11 r3 — NOT READY, 2 blockers + 5 majors + 7 minors; found 268's six ceilings would be inert, and that the suite cannot prove entrypoint wiring"
open_decisions: []
source: 2026-09-11 — the owner's 2026-09-10 decision on Plan 268 D14 (per-station QC ceilings live in station onboarding configuration, updatable later) cannot be delivered on the current code. Plan 264 explicitly excludes the persisted-row tier and Plan 268 T7 would have built six thresholds in memory that nobody could change without editing code. Rewritten on configuration (round 1) and on one shared resolution contract (round 2).
---

# Plan 269 — per-station QC thresholds declared in onboarding configuration

## Status

**DRAFT — NOT READY. Revision 3, 2026-09-11; UNREVIEWED.**

Four independent reviews across two rounds, all NOT READY. Round 1 (Codex 5 blockers, Claude 3)
found that **no task ever wrote a row** — the capability was unbuilt — and the owner then settled
the scope question it exposed: **configuration now, database at v1**, matching
`docs/spec/types-and-protocols.md` § StationQcOverride. Round 2 (Codex 3 blockers, Claude 3)
reviewed that rewrite and found it resolved thresholds **against the wrong station registry**,
missed a **third production entrypoint**, and asserted an independence from Plan 264 that the
code contradicts.

**Owner decision, 2026-09-11 — fail-hard is split by call site.** Round 2 found that a single
validation policy cannot serve both paths: during onboarding an operator is present and a bad
entry should refuse the run, but on the scheduled ingest flow the trigger is **database state,
not a config edit** — decommission, rename or re-code a station and the next run would halt
observation ingest for every station with nobody watching. D3 now splits accordingly.

### What round 3 changed

Six independent reviews across three rounds. Round 3 found **one defect created by round 2's own
fix** and one that reaches into Plan 268's primary case.

- **The strict-ordering guarantee was unsatisfiable.** D3 required resolution *before anything is
  written* **and** against the stored registry — but first-time onboarding is what **creates**
  those stations (`services/onboarding.py:413-538` Step 2). Resolve before Step 2 and a ceiling
  for a newly onboarded station is rejected as unknown, which is the plan's own titular case and
  Plan 268's six gauges. Resolve after, and "before anything is written" is false. Both round-3
  reviews found this independently. D3 now names the only workable window and states the
  guarantee honestly.
- **🔴 Plan 268's six ceilings would be declared-but-inert.** `flows/ingest_observations.py:601-609`
  judges only stations whose `station_status` is `operational`; Plan 268 T2b/T7 keep all six DHM
  gauges at `onboarding` by design (`268:799`). Their ceilings would resolve cleanly, never
  apply, and report nothing — the exact fail-open failure this plan exists to eliminate, landing
  on its primary use case. **Owner decision 2026-09-11: report it.** T3 now surfaces that class.
- **"Let `uv run pytest` prove it" was worthless, and the repo proves it.** `scripts/onboard.py`
  already omits `calculated_specs`, `water_level_datums_masl` and `water_level_units` from its
  `onboard_from_camelsch` call (`:339-364`) while `flows/onboard.py` loads and passes all three
  (`:231-233`) — and the suite is green with that live today. A forgotten keyword behaves
  identically: green suite, silent `[]`, fail-open QC. T4 now pins each wrapper with a forwarding
  assertion against the stubs that already exist.
- **Degradation was reported on a channel with no reader.** The repo reports flow degradation
  through `PipelineHealthRecord`, consumed by `api/routes/health.py` and the watchdog, and
  `pipeline_health_store` is **already** a parameter of `ingest_observations_flow`
  (`flows/ingest_observations.py:527`). Revision 3 invented a structlog ERROR instead.
- **Onboarding is one-shot, so strictness landed where it cannot help.** After setup the only
  path that re-reads a corrected ceiling is the scheduled ingest — the lenient one. **Owner
  decision 2026-09-11: add an edit-time check** (T6) so a correction is validated when it is
  made, not on the next run.
- **"Base-only" was declared but unenforced**; revision 3 tested that overlays replace the array
  rather than preventing it. T1 now rejects the key in overlays before the merge.

### What round 2 changed

- **One registry contract, named once.** The previous revision resolved onboarding's overrides
  against `station_by_id`, which `services/onboarding.py:418` builds **empty** and populates only
  from the batch being onboarded (`:505`, `:523`, `:577`). With fail-hard, a ceiling for any
  station outside that batch refused the run — and `config/overlays/staging-5-stations.toml`
  narrows staging to five basins against a base of 169, so declaring Plan 268's six DHM ceilings
  would have **refused onboarding on the deployment that exists today.** Both round-2 reviews
  found this independently. T2 now fixes one source — `station_store.fetch_all_stations()`,
  unfiltered — for every caller. `station_store` is already a `_run_onboarding` parameter.
- **A third production entrypoint.** `scripts/onboard.py:339` calls `onboard_from_camelsch`
  directly and documents itself as a production entrypoint (`:284`). Under the previous
  revision's keyword default it would have silently received no overrides. Round 1 flagged
  understated wiring; the fix addressed the instances named and did not re-audit, so the same
  defect survived in a new place. T4 now names all of them.
- **Independence from Plan 264 was false.** D2 claimed T2 "needs no change when 264 lands".
  Plan 264 changes `rules_for` to resolve most-specific-wins on network and makes a
  station→network mapping a **required** parameter of `Stage1QualityChecker.check`, so T2's
  lookup breaks. D2 now states the ordering constraint in both directions.
- **The overlay trap.** `config/_overlay.py:50-56` merges dict-on-dict and **replaces everything
  else**, arrays included. An overlay adding one threshold block silently deletes every
  base-declared ceiling. Plan 268 T7 routes explicitly around this for rules; the previous
  revision built a second array-shaped surface into the same hole without noticing.
- **Stale cross-plan text, including this plan's own.** The previous revision described Plan 268
  as still requiring "six in-process objects" — a description that **the same commit made false**
  by fixing 268. Corrected, and T6 now owns sweeping the sibling plans rather than only this one.
- **The audit-trail cost was overstated.** `services/qc.py` embeds the **merged** thresholds in
  `QcFlag.detail` at every flag site, so a failed observation does record the ceiling in force at
  judgement time. Only passing observations record nothing. Stated correctly below.

## Problem

The owner settled where per-station QC thresholds live: **station onboarding configuration,
updatable later** (Plan 268 D14, 2026-09-10). The authoritative spec says the same and has all
along. None of it is built.

Measured at `a6e8b6c3` (re-verified after PR #270 and Plan 270 landed; neither touched `src/`):

- **`StationQcOverride` is a bare dataclass** (`types/domain.py:171-176`). The **type** is
  documented, including the `overrides` parameter on the `QualityChecker` Protocol
  (`protocols/stores.py:1028`); the **supply** is absent. No configuration key, no parser, no
  resolution step, no loader anywhere in `src/`.
- **Every live observation-QC caller hard-codes an empty list** — `flows/ingest_observations.py`
  inside `_run_qc_task`, and `services/onboarding.py` inside `_run_onboarding`.
- **The consumption end is already built and correct.** `services/_qc_helpers.merge_thresholds`
  merges keyed on `(station_id, rule_id, parameter, time_step)`, skipping `None` values.
  **Only the supply end is missing.**

### Two dead tiers beside this one, recorded as a warning

`forecast_qc_overrides` (`db/metadata.py:1336-1353`, migration `0012`, 2026-03-24) is a real
table backing a dataclass structurally identical to `StationQcOverride`. **Nothing reads or
writes it**; all six forecast call sites in `flows/run_forecast_cycle.py` hard-code
`qc_overrides=[]` (`:2837, :2965, :3149, :3221, :3295, :3503`). Archived Plan 012 specified the
loader and shipped only the schema.

`station_thresholds` (alert danger levels — a different concern) is *read* from five live sites
through `StationStore.fetch_thresholds` (`protocols/stores.py:623`) while its writer
`store_thresholds` (`:626`) is **called from nowhere in `src/`**.

Both are the same failure: schema and type shipped, supply never built. **This plan is not done
when the parser lands** — T3 and T4 make it real, and that is why the graph is shaped as it is.

## What this does not change

**With no threshold blocks declared, QC output is unchanged.** `merge_thresholds` over an empty
list returns `dict(base_thresholds)` unchanged, and every existing deployment declares none. T5
asserts this **at flow level on both QC paths** — not by comparing a service call to itself,
which round 1 found would pass against any implementation.

**The checker signature does not change.** `Stage1QualityChecker.check` already takes
`overrides`, and the Protocol already declares it. This plan changes what callers *pass*. The
positional callers in `scripts/dhm_precip/` are untouched. The *enclosing* functions do change
signature — see T3/T4 `Out`, which is where the real call-site risk lives, and which round 2
found understated once already.

## Design sketch

Four pieces, no persistence:

1. **A configuration surface.** `[[onboarding.station_qc_thresholds]]` blocks parsed the way
   `[[onboarding.calculated]]` already is, into a frozen `StationQcThresholdSpec`. A spec names a
   station by **code** plus **network** (configuration cannot know a `StationId`), a `rule_id`,
   a `parameter`, a `time_step` and threshold values.
   **Base-`config.toml` only, and enforced rather than documented.** `config/_overlay.py` merges
   before parsing and replaces arrays wholesale (`:48-56`), so an overlay declaring one block
   would delete every base ceiling before the resolver ever sees them — and neither strict
   resolution nor lenient reporting can detect an absence. T1 therefore **rejects this key in an
   overlay**, with a diagnostic pointing at the base file. Revision 3 only *tested that* overlays
   replace, which documents the wound instead of closing it. This needs no general
   array-composition redesign (that is Plan 264 D4's question).

   **The `None`-means-inherit sentinel is unreachable from TOML.** `StationQcOverride.thresholds`
   is `dict[str, float | None]` where `None` means inherit (`types/domain.py:176`), but TOML
   cannot express a null — **omitting the key is the only encoding**, and T1 says so.
2. **One pure resolution boundary**, shared by every caller:
   `resolve_station_qc_overrides(specs, stations, rule_set, on_unresolvable) -> Resolution`.
   It resolves each spec's `(code, network)` to a `StationId`, checks the named rule exists in
   the resolved set for that parameter and cadence, validates the **merged** thresholds against
   the base rule, and either raises or drops-and-reports per `on_unresolvable` (D3). Pure —
   registry and rule set are passed in — so it is testable without a database.

   **`on_unresolvable` is an `Enum`, not a flag.** CLAUDE.md § Enums over booleans is a hard rule
   and "strict mode / lenient mode" prose would be satisfied by `strict: bool`. **`Resolution` is
   a frozen dataclass** carrying the resolved overrides, the rejected specs with their reasons,
   the specs that resolved to a station this run will not judge (§ D3), and the fan-out records
   (§ D2). Naming the shape here is not gold-plating: T3 must put the counts on `IngestResult`
   and the health channel, so an unspecified return blocks its own verification.

   **The station set is the full registry, not the judged subset.** The ingest flow already
   holds it: `flows/ingest_observations.py:586-592` makes three **kind-filtered** calls whose
   union is the whole table (`StationKind` has exactly three members), so T3 reuses
   `all_stations` and does **not** add a fourth query. Onboarding resolves against
   `station_store.fetch_all_stations()` plus the batch it has just written (§ D3).
3. **One loader, not a private sibling per flow.** `config/onboarding.py` already parses
   `[[onboarding.calculated]]` into `OnboardingConfig` and is read by `flows/onboard.py`; the
   threshold specs join it there. Both flow modules obtain specs from that one function.
   **Three** modules currently carry their own private `_load_qc_rules()` —
   `flows/ingest_observations.py:73`, `flows/onboard.py:59` and `scripts/onboard.py:70`, the last
   of which is also an entrypoint T4 must thread. This plan does not add a fourth private loader.
   Note `load_onboarding_config()` returns **`None`** when a config carries no `[onboarding]`
   section (`config/onboarding.py:150-152`) — a distinct branch from an unset `SAPPHIRE_CONFIG`,
   and both must yield zero overrides rather than an exception on the ingest path.
4. **Threading at all three live entrypoints**, replacing `overrides=[]`.

Reusing `StationQcOverride` unchanged is deliberate: it is rule-keyed, which is what
`merge_thresholds` indexes on. The dataclass is right; only its supply was missing.

**Why configuration rather than a table.** The spec stages it that way; it removes the migration,
the production store registration and the database grant entirely; and it makes removal correct
by construction — a deleted block is gone on the next run, whereas upsert-only persistence would
leave a stale ceiling silently in force. The honest cost: **a correction is a config edit plus a
re-run, not a row update**, and there is no record of *when* a ceiling changed beyond git —
though note `QcFlag.detail` does embed the merged thresholds at every flag site
(`services/qc.py:66, :85, :142, :171, :189, :216`), so every **failed** observation records the
ceiling in force when it was judged. Only passing observations record nothing. When v1 adds the
table it follows **`station_thresholds`** — natural-key `PrimaryKeyConstraint` plus
`created_at`/`updated_at` (`db/metadata.py:321-333`) — and **not** `forecast_qc_overrides`, which
has neither. (`clim_baselines` has a natural-key PK and `created_at` but no `updated_at`; it is
not the model.)

## Locked design

### D1 — Does forecast QC get the same surface? **CLOSED: no.**
Identical shape, identical gap, and `forecast_qc_overrides` even has the table. Leave it: no
second-network pressure on forecast QC (Plan 264 D1's reasoning), and widening into the forecast
cycle for a problem nobody has is a bad trade. **Dropping** the dead table is equally out of
scope — a deletion needs its own rollback rehearsal. T6 records it as dead.

### D2 — How this composes with Plan 264. **CLOSED, restated twice.**
Not a lattice. **Plan 264 selects *which rule* applies; this plan adjusts *that rule's
thresholds*.** An override cannot suppress a rule. Three consequences, none obvious:

- **An override has no `rule_version`** (`types/domain.py:171-176`) and `merge_thresholds` has no
  `break`, so an override whose four-part key matches **more than one resolved rule applies to
  all of them.** This is not hypothetical: `scripts/dhm_precip/qc_ruleset.py:67-93` ships two
  `frozen_sensor` rules differing only by version **and by key set** — one carries
  `exclude_at_or_below`, the other deliberately does not. An override supplying that key would
  install an exclusion floor on the rule that exists precisely to have none. T2 therefore
  **reports the fan-out** when an override matches more than one rule, so an operator sees it.
- **This plan is NOT independent of Plan 264, and the previous revision was wrong to say so.**
  264 makes `rules_for` network-aware and makes a station→network mapping a **required**
  parameter of `check`. **If 264 lands first**, T2 passes each spec's `network` to `rules_for`.
  **If this plan lands first**, 264 T1/T2 must update T2's call site — recorded in Cross-plan
  sequencing as a required edit to 264, not an assumption.
- **Post-264, an override does NOT become unresolvable when a network rule replaces a generic
  one — and revision 3 was wrong to say so.** Plan 264 selects the replacement *within the same
  `(rule_id, parameter, time_step)` group*, and `merge_thresholds` matches exactly those fields
  plus `station_id`, so the override **follows the replacement onto the new rule** and is then
  validated against that rule's changed base thresholds. T2 tests this composition directly.
- **The fan-out report's destination.** It rides `Resolution` and is surfaced the same way as a
  dropped spec (D3) — not fatal in either mode, because a fan-out is legitimate. Today it cannot
  fire: `config.toml` carries no duplicate `(rule_id, parameter, time_step)` tuple. Plan 268 T7
  adds rows to that array and Plan 264's most-specific-wins should keep it that way, which is
  why this is a report rather than a gate.

### D3 — An entry naming an unknown station, rule or parameter. **CLOSED: split by call site (owner, 2026-09-11).**
A single policy cannot serve both paths.

- **Onboarding — fail hard, in the one window where that is possible.** Revision 3 said
  "before anything is written", which **cannot be satisfied**: first-time onboarding *creates*
  the stations a ceiling names (`services/onboarding.py:413-538` Step 2), so resolving earlier
  rejects the plan's own titular case. Resolution runs in the window **after Step 2/2.5's station
  writes and before Step 3's observation writes** (`services/onboarding.py:541-599`), against the
  stored registry **plus the batch just written**. Stated honestly: basins, station rows and audit
  rows are already committed at that point — `scripts/onboard.py:369` already warns that partial
  data may be committed — and `onboard_from_camelsch` loads observations before calling the
  service at all (`:1356` before `:1361`), so the guarantee is scoped to **the service's own
  observation writes and QC**, not to the whole process. A bad entry stops the run before any
  observation is stored or judged; it does not unwind station creation.
- **Scheduled ingest — degrade and report.** The trigger here is **database state, not a config
  edit**: decommissioning, renaming or re-coding a station would otherwise halt observation
  ingest for every station with nobody watching, which `docs/workflow.md` § Preserve Existing
  Logic forbids introducing. An unresolvable spec is **dropped and reported**; every other
  station is judged normally.

  **Reported on the channel that has a reader.** Not a structlog ERROR, which revision 3 chose
  and which nothing consumes: the repo reports flow degradation through `PipelineHealthRecord`,
  written by `_append_fetch_health_record` (`flows/ingest_observations.py:208-250`), consumed by
  `api/routes/health.py` and the watchdog, and `pipeline_health_store` is **already** a parameter
  of `ingest_observations_flow` (`:527`) and production-bootstrapped (`:545`). A drop is a
  DEGRADED record under a dedicated check type, plus the log line and the flow-result count.

- **🔴 A ceiling that resolves but will never be applied is reported the same way** (owner,
  2026-09-11). The ingest flow judges only `eligible` stations — `station_status == "operational"`
  plus a gauged/weather gate (`flows/ingest_observations.py:601-609`) — so a ceiling on a station
  outside that set resolves cleanly, never applies, and would otherwise be silent. **This is not
  hypothetical: Plan 268 keeps all six DHM gauges at `onboarding` by design (`268:799`)**, so
  every one of its ceilings is in this class until promotion. Declared-but-inert is the same
  fail-open this plan exists to kill, so it is surfaced, not assumed.

  The accepted cost, stated plainly: a dropped or inert ceiling means that station runs on its
  ungoverned base threshold until someone acts on the health record.

- **Configuration that will not parse still raises everywhere, and the previous revision's
  "never halts on configuration" was too broad.** T1's rejections — a malformed value, an unknown
  field, a duplicate key, a threshold key that does not belong to its rule — happen **at parse
  time, before T2 sees a spec**, and the shared loader parses the whole `[onboarding]` section
  including unrelated calculated-station blocks. Lenient mode governs **resolvable-spec
  failures** (unknown station, unknown rule, no matching rule, invalid merged thresholds), not
  parse failures. T3 tests both classes so the distinction is pinned rather than assumed.

The check is **not** in the config parser, which has no registry and no rule set; it is in T2,
which has both.

### D4 — An override attached to a rule that resolves to nothing. **CLOSED by T2.**
Round 1 deferred this to Plan 264 T3; both reviews found that false — 264 raises only when a
**non-empty rule set resolves nothing for a series**, so an override with a mismatched cadence
stays inert whenever any *other* rule resolves. T2 closes it directly: a spec whose
`(rule_id, parameter, time_step)` matches no rule in the resolved set is unresolvable, and D3
decides what happens next.

### D5 — An API surface. **CLOSED: not in this plan.**
No stored row to serve; the question arrives with the v1 migration. Round 1's justification was
withdrawn — it rested on a rating-table basis that Plan 268's own round 2 **rejected** in favour
of a published flood-envelope curve from catchment area, characterised there as *"not a leak"*.
**Correctly stated: if D14 settles on a table-derived ceiling, an API surface widens exposure and
the constraint applies; if it settles on the flood-envelope basis, scope discipline is the only
reason.** D14 is open, so v1 must revisit this rather than inherit it.

## Tasks

### T1 — The configuration surface

**Outcome**: a per-station QC threshold is declarable in onboarding configuration and is
validated as far as a parser without a registry can validate it.
**In**: `src/sapphire_flow/config/onboarding.py` (the spec type and its pydantic boundary model,
joining `OnboardingConfig` beside `calculated`); `docs/spec/config-reference.toml`; unit tests.
**Out**: no station, rule or parameter *existence* checking and no merged-threshold checking —
both need the registry and the rule set, and are T2's. No threshold *values* for any station:
this ships the surface empty and Plan 268 D14 owns what goes in it. No change to any existing
`[onboarding.*]` block.
**Verification**: `uv run pytest tests/unit/config/test_onboarding_qc_thresholds.py` asserting a
valid block parses to the expected frozen spec, and that each of these is rejected naming the
offending key —
- a non-numeric or non-finite threshold value; an unknown field; a non-positive `time_step`; a
  missing station code or network;
- **a threshold key not belonging to the named rule.** `docs/spec/types-and-protocols.md:583-590`
  is authoritative (`range_check` → `value_min`/`value_max`; `rate_of_change` → `max_rate`;
  `frozen_sensor` → `tolerance`/`min_consecutive`/`exclude_at_or_below`; `spike` → `tolerance`;
  `gross_outlier` → `k_sigma`) and `merge_thresholds` copies **any** key it is given, so
  `value_maximum` would reach the checker and be silently ignored;
- **a value that is well-typed but nonsensical for its rule** — a negative `max_rate` (every
  comparison then exceeds it, `services/qc.py:79`), a non-positive or fractional
  `min_consecutive` (silently truncated by `int()`, `:97`), a negative `spike` tolerance (`:179`);
- **two blocks sharing `(network, station code, rule_id, parameter, time_step)`** — duplicates
  are rejected at parse, because `merge_thresholds` applies every match in order and the last
  would silently win.

Also assert that **an overlay declaring this key is rejected before the merge**, with a
diagnostic naming the base file — not merely that overlays replace arrays, which was revision 3's
weaker bar and leaves the silent-deletion path open (`config/_overlay.py:48-56` merges before
parsing, so a base ceiling is gone before the resolver could notice); that **omitting a threshold
key is the only way to express "inherit"**, since TOML cannot encode the `None` sentinel
`StationQcOverride.thresholds` allows; and that a config with no such block — **and a config with
no `[onboarding]` section at all**, which makes `load_onboarding_config()` return `None`
(`config/onboarding.py:150-152`) — each yield an empty list rather than raising.
**Pre-change**: N/A — new capability; nothing to disprove.

### T2 — The shared resolution boundary

**Outcome**: declared specs become `StationQcOverride` values valid by construction, from one
function that every caller uses, with unresolvable specs handled per D3.
**In**: a new pure function in `src/sapphire_flow/services/` taking the parsed specs, the station
registry, the resolved `QcRuleSet` and an unresolvable-handling **`Enum`** (not a `bool` —
CLAUDE.md § Enums over booleans); returning a **frozen `Resolution`** carrying the resolved
overrides, rejected specs with reasons, specs resolving to stations this run will not judge, and
fan-out records; unit tests.
**Out**: no I/O — registry and rule set are parameters, which is what keeps it testable without a
database and why it is not a store. No threading into any flow (T3/T4). No change to
`StationQcOverride`.
**Verification**: `uv run pytest` on the new module asserting —
- **the registry contract**: the function takes the **full, unfiltered** station set and a
  station code is resolved by `(code, network)`, proven by a fixture where two stations share a
  code across different networks;
- an unknown station code, an unknown `rule_id`, or a `(rule_id, parameter, time_step)` triple
  matching no rule **raises in strict mode and is dropped-and-reported in lenient mode** (D3),
  with the offending spec named in both;
- **merged-threshold validation against the base rule** — a partial `range_check` override
  supplying only `value_min` above the rule's `value_max` is rejected, which only this layer can
  see because it needs the base rule;
- **an override matching two rules that differ only by `rule_version` is applied to both and the
  fan-out is reported** (D2) — asserted against an **inline two-version fixture**, citing
  `scripts/dhm_precip/qc_ruleset.py:67-93` as the motivating real case rather than importing it,
  since that path sits outside the pyright gate;
- **the 264 composition**: where a network-specific rule replaces a generic one within the same
  `(rule_id, parameter, time_step)` group, the override **follows the replacement** and is
  validated against the new rule's base thresholds (D2). Revision 3 claimed such an override
  becomes unresolvable; it does not, and a test is the only thing that keeps that straight.
**Pre-change**: N/A — new pure function; its behavioural consequence is proven in T3/T4.

### T3 — Thread into the observation-ingest flow (lenient)

**Outcome**: the operational ingest path judges a station against its declared ceiling, and never
halts on configuration.
**In**: `src/sapphire_flow/flows/ingest_observations.py` — specs obtained from
`config/onboarding.py`'s loader (not a fourth private `_load_*`), resolved **once** at flow level
in **lenient** mode against the `all_stations` the flow **already holds** (`:586-592`; do not add
a fourth query), a new keyword parameter on `_run_qc_task` beside `qc_rules` replacing the
`overrides=[]` it passes, the drop/inert counts on `IngestResult` (`:52-65`), and the health
record. **`IngestResult` is constructed at three sites, two of which return before the QC loop
(`:618-628` no eligible stations, `:657-668` no new data)** — those must carry the counts too, or
a drop on such a run is reported nowhere; unit tests.
**Out**: the checker signature is unchanged. Forecast call sites keep `qc_overrides=[]` (D1).
`scripts/dhm_precip/` untouched. **`_run_qc_task`'s new parameter carries a default** — not
because the production caller needs it (`:692-700` passes only the first four positionally and
everything from `qc_rules` on by keyword) but because the tests call `_run_qc_task.fn(` directly.
No new store: the flow already fetches the registry.
**Verification**: `uv run pytest tests/unit/flows/test_ingest_observations.py` asserting —
- **in one run**, a station with a declared ceiling is judged against it while a station without
  one is judged against the rule's base threshold — the discriminating case, since an
  implementation that applied overrides to every station passes a single-station test;
- **a spec naming a nonexistent station does NOT abort the run** — every other station is still
  judged, and the drop is reported as a **DEGRADED `PipelineHealthRecord`** under a dedicated
  check type, alongside the log line and the flow-result count (D3). The health record is the
  load-bearing half: nothing consumes structlog ERROR, while `api/routes/health.py` and the
  watchdog consume health records. This is the case that halts the fleet if it is got wrong;
- **a spec that resolves to a station absent from `eligible`** (`:601-609`) — not operational, or
  an ungauged river station — **is reported the same way rather than silently doing nothing.**
  Plan 268's six DHM gauges are exactly this class (`268:799` keeps them at `onboarding`), so
  this is the plan's own primary case, not an edge;
- **a malformed or duplicate declaration still raises on this path** — lenient mode governs
  resolvable-spec failures, not parse failures (D3), and without this the two classes are
  indistinguishable in test;
- **resolution happens once per flow run** — asserted by spying on
  `resolve_station_qc_overrides` itself, **not** on the loader. An implementation that loads once
  and re-resolves inside the per-`(station, parameter)` loop (`:690`) passes a loader-call count;
- **an unset `SAPPHIRE_CONFIG` yields the default rules and zero overrides, and does not raise.**
  `_load_qc_rules` falls back safely (`:76`) while `load_onboarding_config()` raises when the
  variable is absent (`config/onboarding.py:143`); without this assertion the asymmetry takes the
  ingest flow down on a path the rule-set loader tolerates.

`uv run pytest` passes whole.
**Pre-change**: a RED test proving a **declared, correctly-resolved** override has no effect on
the ingest flow's QC result today, because the call site passes `[]`. It fails on the flagged
status of a specific observation — not on a missing symbol, which is what makes it evidence of
the defect rather than of the feature's absence.

### T4 — Thread into the onboarding path (strict), at every entrypoint

**Outcome**: onboarding judges backfilled history against declared ceilings, resolves them from
the same registry the ingest flow uses, and refuses the run on a bad entry before writing.
**In**: `src/sapphire_flow/services/onboarding.py` — the `overrides=[]` inside `_run_onboarding`,
resolved in **strict** mode **in the window between Step 2/2.5's station writes and Step 3's
observation writes** (`:541-599`), against `station_store.fetch_all_stations()` **unioned with the
batch this run has just written** (`station_by_id`). Neither source alone works: the registry
alone rejects a station created by this very run (the titular case), and `station_by_id` alone
rejects every already-onboarded station — round 2 and round 3 found those two failures in turn.
The `station_store` parameter exists (`:342-360`) but **the call is new**, and its position is
what decides the semantics. Plus the parameter threading from `onboard_from_camelsch`,
**`src/sapphire_flow/flows/onboard.py`**, and **`scripts/onboard.py:339`**, a documented
production entrypoint (`:284`).
**Out**: no change to the checker or to any rule. **The new parameter on `_run_onboarding` is
keyword-only with a default**: there are **nine** direct call sites across `tests/unit/services/`
and `tests/integration/` plus the production caller at `:1361`, and a required positional breaks
every one. **🔴 `uv run pytest` does NOT prove the wrappers are wired, and revision 3 was wrong to
lean on it.** `scripts/onboard.py:339-364` already omits `calculated_specs`,
`water_level_datums_masl` and `water_level_units` from its call while `flows/onboard.py:231-233`
loads and passes all three — and the suite is green with that defect live today. A forgotten
keyword behaves identically. Each wrapper therefore gets its own forwarding assertion below.
**Verification**: an onboarding-service test proving a declared override changes the QC outcome
for a backfilled series (round 1 verified only the ingest path and would have passed with this
caller still hard-coded); **a spec naming a station outside the current onboarding batch but
present in the registry resolves successfully** — the staging case, where the overlay narrows to
five basins of 169 and the previous revision would have refused the run; a spec naming a station
absent from the registry **raises before any observation is fetched or stored**; and **both
paths resolve an identical override list from one fixture config**, asserted directly, since
T3 and T4 reaching different answers is the failure mode D3's split makes possible — **that
cross-path assertion moves to T7**, since it cannot run while T3 is unlanded and T3/T4 are
parallel;
- **a spec naming a station created by THIS onboarding batch resolves successfully in strict
  mode** — the titular case, and Plan 268's six gauges, which revision 3 would have refused;
- **each production wrapper is asserted to forward the specs** to `onboard_from_camelsch` —
  `flows/onboard.py` and `scripts/onboard.py` — using the stubs that already capture kwargs at
  `tests/unit/flows/test_onboard_flow.py:534` and `tests/unit/scripts/test_onboard_script.py:45`,
  covering the flow's explicit-`basin_ids` branch as well as its config-derived branch. Without
  these, every other bullet here passes while both wrappers drop the argument.
`uv run pytest` passes whole.
**Pre-change**: a RED test proving a declared override has no effect during onboarding today,
failing on the QC outcome rather than on a signature.

### T5 — Zero-configuration equivalence, on both paths

**Outcome**: proof that a deployment declaring no thresholds is unaffected, on **both** QC paths.
**In**: `tests/unit/flows/test_ingest_observations.py` and a matching baseline for
`_run_onboarding`'s QC step in `tests/unit/services/` — **flow and service level, deliberately**.
**Out**: not a new QC behaviour; a regression bar only.
**Verification**: a fixed observation series run through each path with **no threshold blocks
declared** yields flags, statuses and `detail` text identical to the pre-T3/T4 result. The rule
set is the one **loaded from `config.toml`** — what actually runs — not `_default_swiss_qc_rules()`,
which `load_qc_rules` never returns while a `[qc_rules]` section exists. Thresholds are not a
returned field; they appear only inside `QcFlag.detail`, so the comparison is over `detail` text.
Round 1 covered one path and round 2 found the second still unguarded.
**Exit gate**: the baselines are captured and committed **before T3/T4 land**, as tests that
phase 4 must keep green — the oracle cannot be generated after the change it exists to detect.

### T6 — Edit-time configuration validation

**Outcome**: an operator correcting a ceiling learns immediately whether it resolves, instead of
finding out on the next scheduled run — or not at all.
Added on the owner's 2026-09-11 decision, after round 3 found the strict path lands where it
cannot help: **onboarding is one-shot**, so after setup the only code that re-reads a corrected
ceiling is the lenient ingest flow. Strictness sat where typos are least likely and leniency where
corrections actually happen. There is no existing validator to lean on — `cli/check.py` is a ruff
wrapper, not a config check.
**In**: a `--validate-config` path on `scripts/onboard.py` (already an entrypoint T4 threads, and
already holding a loaded `OnboardingConfig` at `:198-204`) that loads the specs, fetches the live
registry, resolves in **strict** mode, prints every rejected and every declared-but-inert spec,
and exits non-zero if any spec fails; unit tests.
**Out**: no change to any flow, to QC, or to the resolution function itself — this is a second
caller of T2, not new logic. No new CLI module; no scheduling.
**Verification**: `uv run pytest tests/unit/scripts/` asserting a clean config exits zero and
prints nothing alarming; a config naming an unknown station exits non-zero naming that spec; and
**a config whose ceiling targets a non-operational station exits zero but reports it as inert** —
the Plan 268 case, which is legitimate-but-ineffective rather than wrong.
**Pre-change**: N/A — new capability; nothing to disprove.

### T7 — Cross-path equality

**Outcome**: the ingest flow and the onboarding service resolve the identical override list from
one configuration.
Moved out of T4 because it cannot be asserted while T3 is unlanded, and T3/T4 are parallel — a
task whose exit gate depends on a sibling declared parallel to it is an unsound ordering.
**In**: one test exercising both paths against a single fixture configuration.
**Out**: no production change; this is a gate, not a feature.
**Verification**: both paths, given the same config and the same station registry, produce the
same resolved overrides for the same station — the failure mode D3's split makes possible, since
the two now differ in policy and could drift in resolution too.
**Pre-change**: N/A — the assertion has no meaning before T3 and T4 land.

### T8 — Documentation, including the sibling plans

**Outcome**: the surface is documented, its v1 successor specified, the dead tiers recorded as
dead, and **no plan still points at scope this one has dropped**.
**In**: `docs/spec/types-and-protocols.md` (§ StationQcOverride now describes a surface that
exists, and carries the D2 composition rule); `docs/touchpoint-maps.md`; `docs/v0-scope.md`
(which lists `forecast_qc_overrides` without noting nothing reads it); **and the two sibling
plans** — `docs/plans/264-qc-rules-select-on-network.md` and
`docs/plans/268-dhm-barkhk-runoff-delivery.md`.
**Out**: no code change. `docs/spec/config-reference.toml` belongs to T1, not here.
**Verification**: bounded inspection —
- the D2 composition rule is stated **once**, and Plan 264's retracted "station beats network
  beats generic" phrasing is gone from `264`;
- **the persisted tier is recorded as UNOWNED and deferred to v1** in the spec and the touchpoint
  map. Both siblings had said "Plan 269 now owns it" for a database tier this plan out-of-scopes,
  which would have left the same orphaned gap that caused this plan to exist; **that sweep was
  performed during the planning folds**, so this task verifies no such pointer has returned
  rather than making it for the first time. (Check the siblings at HEAD, not from this text — a
  previous revision described sibling state that its own commit had already changed.)
  **Sweep by meaning, not by string**: round 3 found the first sweep corrected 268's ownership
  sentence while leaving an "open question — does that persistence work belong in this plan, in
  264, or its own?" three lines below, reopening what the correction had just closed. A search
  for the old wording would not have caught it;
- `forecast_qc_overrides` is recorded as schema-only with its six hard-coded call sites;
  `StationStore.store_thresholds` as having no caller;
- the v1 migration is specified as following `station_thresholds` (natural-key PK plus both
  timestamps) and **not** `forecast_qc_overrides`;
- the configuration shape's costs are stated: overlay wholesale-replacement, and *when* a ceiling
  changed being recorded only in git — with the `QcFlag.detail` nuance, so v1 does not re-derive it.
**Pre-change**: N/A — documentation.

```json
{
  "phases": [
    { "id": "phase-1", "tasks": ["T1"] },
    { "id": "phase-2", "tasks": ["T2"], "depends_on": ["phase-1"] },
    { "id": "phase-3", "tasks": ["T5"], "depends_on": ["phase-2"] },
    { "id": "phase-4", "tasks": ["T3", "T4"], "depends_on": ["phase-3"], "parallel": true },
    { "id": "phase-5", "tasks": ["T6", "T7"], "depends_on": ["phase-4"], "parallel": true },
    { "id": "phase-6", "tasks": ["T8"], "depends_on": ["phase-5"] }
  ]
}
```

T5 sits **before** T3/T4: its baselines are pre-change evidence and cannot be captured after the
change they exist to detect. T3 and T4 are parallel but neither is optional, and they differ in
policy (lenient vs strict) while sharing one resolution function — which is why **T7**, not T4,
asserts they resolve identically: that assertion needs both landed, so it cannot live inside a
task declared parallel to its counterpart. T6 needs T2 for the resolver and T4 for the wrapper
threading, so it sits with T7.

**Cross-plan sequencing.**

- **Plan 264 and this plan are coupled in both directions** (D2). If 264 lands first, T2 passes
  each spec's network to `rules_for`. **If this plan lands first, Plan 264 T1/T2 must update T2's
  call site** — that is a required edit to 264, not an assumption this plan may make.
- **Plan 264 T4's golden fixture must be captured before any DHM rule reaches `config.toml`.**
  This plan changes no QC output with nothing declared (T5), so it does not threaten that
  fixture — but nor is it a reason to skip capturing it.
- **Plan 268 T7 must be revised before it is READY, and this plan does not do it.** Its gate
  proves the merge by calling `merge_thresholds` directly, which passes identically for
  in-memory objects, so the dependency edge stays vacuous until its verification changes. 268 T7
  also runs QC from `src/sapphire_flow/cli/import_dhm_delivery.py`, **a third call site that
  does not exist yet** and which T3/T4 do not wire. **Required of 268 before its T7 is READY**:
  declare the six ceilings as configuration, resolve them through T2, and state that the import
  CLI may not construct overrides in memory. D14 still owns the values.

## Explicitly out of scope

- **A database table, store, migration, Protocol store method, production store registration or
  database grant.** Deferred to v1 with the spec — and **currently unowned**; T6 records that
  rather than leaving two siblings pointing here.
- **The DHM threshold values.** Plan 268 D14 owns them, including the basis (a published
  flood-envelope curve from catchment area is its current recommendation) and the coefficient,
  which its own text says the hydrologist sets.
- **Rule selection.** Plan 264 owns the network dimension, the fail-closed policy and the
  flag-version correction.
- **Reviving or dropping `forecast_qc_overrides`** (D1) — neither; document it.
- **An API or dashboard surface** (D5) — arrives with the v1 migration.
- **Fixing overlay composition** so an overlay can extend rather than replace an array. Plan 264
  D4 owns that question; T1 pins the current behaviour with a test.
- **A writer for `station_thresholds`.** A different concern with its own missing writer.
- **Any change to `Stage1QualityChecker.check` or the `QualityChecker` Protocol.** Already correct.
