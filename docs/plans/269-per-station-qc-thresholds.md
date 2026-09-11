---
status: DRAFT
created: 2026-09-11
revised: 2026-09-11
plan: 269
title: Per-station QC thresholds declared in onboarding configuration
scope: Deliver per-station observation QC threshold overrides through configuration — a validated TOML surface, one pure resolution boundary, threading into the scheduled observation-ingest flow, and an edit-time validation command. NOT the onboarding QC path (descoped, see § What this deliberately does not do), NOT a database table, store or migration (the spec defers that to v1), NOT the DHM threshold values themselves (Plan 268 D14), NOT forecast QC overrides, NOT rule selection (Plan 264), NOT an API surface, NOT a new rule kind.
blocks: [268]
related: [264, 268, 012]
reviews:
  - "codex 2026-09-11 r1 — NOT READY, 5 blockers; killed the write path and the migration"
  - "claude 2026-09-11 r1 — NOT READY, 3 blockers; same central defect found independently"
  - "codex 2026-09-11 r2 — NOT READY, 3 blockers; found a third entrypoint and the 264 signature break"
  - "claude 2026-09-11 r2 — NOT READY, 3 blockers; measured the staging blast radius and the overlay trap"
  - "codex 2026-09-11 r3 — NOT READY, 1 blocker; the strict-ordering bootstrap contradiction"
  - "claude 2026-09-11 r3 — NOT READY, 2 blockers; 268's ceilings inert, and the suite cannot prove wiring"
  - "codex 2026-09-11 r4 — NOT READY, 1 blocker + 7 majors; Step 2.5 writes observations; the spike knob is undocumented"
  - "claude 2026-09-11 r4 — NOT READY, 3 blockers + 9 majors; onboarding QC skips 268's stations entirely"
open_decisions: []
source: 2026-09-11 — the owner's 2026-09-10 decision on Plan 268 D14 (per-station QC ceilings live in station onboarding configuration, updatable later) cannot be delivered on the current code. Descoped to one code path on 2026-09-11 after eight independent reviews, because the onboarding path generated most of the defects and delivers nothing to the case the plan exists for.
---

# Plan 269 — per-station QC thresholds declared in onboarding configuration

## Status

**DRAFT — NOT READY. Revision 5, 2026-09-11; UNREVIEWED.**

**Eight independent reviews across four rounds, every one NOT READY.** Blocker counts per gate:
5/3, 3/3, 1/2, 1/3. That is not convergence, and the reason is diagnosable: **every round found a
defect created by the previous round's own fix**, and each of those repairs was to the coupling
between two QC call paths with different station gates, different write orders and different
judgement predicates.

**Owner decision, 2026-09-11: cut the plan to the scheduled ingest path alone.** Round 4 supplied
the deciding evidence — the onboarding half **delivers nothing to Plan 268**, the case this plan
exists for (see below). Removing it deletes the union registry, the resolution window, the
write-order guarantee, the strict/lenient split by call site and the cross-path equality task —
the origin of nearly every blocker since round 2 — rather than repairing them a fourth time.

### What round 4 established

- **🔴 Neither QC path applies Plan 268's ceilings today, and the onboarding path never will.**
  Onboarding's QC step skips any station whose forecast target is unset
  (`services/onboarding.py:774-778`), and Plan 268 leaves `forecast_targets` **unset on all six
  DHM gauges by design**, asserting in its own task that the step takes the skip branch for every
  one (`268:796`, `:819-821`). Wiring that path was therefore pure cost. The ingest path judges
  only `operational` stations (`flows/ingest_observations.py:601-609`) and 268 keeps the six at
  `onboarding` — so **268 must promote its stations and set their targets before any ceiling can
  bite on any path.** That is 268's decision, recorded in its D14, not this plan's to make.
- **The strict window was still wrong, for the third round running.** Revision 4 placed it after
  "Step 2/2.5's station writes" — but Step 2.5 writes observations
  (`services/calculated_station_onboarding.py:366`). Descoping removes the guarantee entirely
  rather than moving it again.
- **`DEGRADED` is not a health status.** `PipelineHealthStatus` has exactly `OK`, `WARNING`,
  `CRITICAL` (`types/enums.py:187-190`). Revision 4 invented a fourth.
- **The watchdog would not have consumed the new record.** It probes explicitly named check types;
  adding an enum member does not extend it. Revision 4's claim that the watchdog reads this
  channel was false. Stated correctly below.
- **Production uses a spike knob the spec does not document.** `config.toml:271` and `:327` set
  `max_delta`, and `services/qc.py:161` checks it **before** `tolerance` — while
  `docs/spec/types-and-protocols.md:583-590` lists only `tolerance`. A validator built from the
  spec would reject the effective setting and accept an inert one. **The spec is wrong**, and T6
  fixes it; this is a repository defect independent of this plan.
- **The sweep of Plan 268 was incomplete again** — `268:704-705` still assigned in-memory
  construction to its T7, two lines from the corrected text. Third incomplete sweep; T6 now
  sweeps by meaning and names the specific line.

## Problem

The owner settled where per-station QC thresholds live: **configuration, updatable later**
(Plan 268 D14). The spec says the same and has all along —
`docs/spec/types-and-protocols.md` § StationQcOverride: *"Loaded from station onboarding TOML;
v1 migrates to DB (dashboard-editable)."* None of it is built.

Measured at `8e5fab86`:

- **`StationQcOverride` is a bare dataclass** (`types/domain.py:171-176`). The type is documented,
  including the `overrides` parameter on the `QualityChecker` Protocol
  (`protocols/stores.py:1033`); the **supply** is absent — no configuration key, no parser, no
  resolution step, no loader anywhere in `src/`.
- **The scheduled ingest flow hard-codes an empty list** — `flows/ingest_observations.py:308`,
  inside `_run_qc_task`. There is no path by which a declared threshold reaches the checker.
- **The consumption end is already built and correct.** `services/_qc_helpers.merge_thresholds`
  merges keyed on `(station_id, rule_id, parameter, time_step)`, skipping `None` values, with no
  `break`. **Only the supply end is missing.**

### Two dead tiers beside this one, recorded as a warning

`forecast_qc_overrides` (`db/metadata.py:1336-1353`, migration `0012`, 2026-03-24) is a real table
backing a dataclass structurally identical to `StationQcOverride`. **Nothing reads or writes it**;
all six forecast call sites hard-code `qc_overrides=[]`
(`flows/run_forecast_cycle.py:2837, 2965, 3149, 3221, 3295, 3503`). Archived Plan 012 specified
the loader and shipped only the schema.

`station_thresholds` (alert danger levels — a different concern) is read from five live sites
through `StationStore.fetch_thresholds` (`protocols/stores.py:623`) while its writer
`store_thresholds` (`:626`) is **called from nowhere in `src/`**.

Both are the same failure: schema and type shipped, supply never built. **This plan is not done
when the parser lands** — T3 is what makes it real.

## What this deliberately does not do

**The onboarding QC path keeps `overrides=[]`** (`services/onboarding.py:799`). Onboarding's QC
step therefore continues to judge backfilled history against base thresholds only. This is a
deliberate, owner-approved descope, and the reasoning is worth keeping because it is not obvious:

- It **delivers nothing to Plan 268**, whose six gauges that step skips entirely for want of a
  forecast target (`services/onboarding.py:774-778`, `268:796`).
- It was the **source of most blockers across rounds 2, 3 and 4** — the batch-versus-registry
  question, the union, the write-order window, and Step 2.5's hidden observation writes are all
  properties of that path alone.
- The two paths have **genuinely different judgement predicates**, so "one policy" was never
  available: ingest gates on `station_status`/`gauging_status`, onboarding on forecast targets.

**The cost, stated plainly**: a station's backfilled history is QC'd on base thresholds, so a
historical series can be judged by a ceiling its operator has since corrected. Re-running QC over
history is not in this plan. If that becomes load-bearing, it is a follow-on that should be
designed against the onboarding path's own gate, not bolted onto this one.

**With nothing declared, QC output is unchanged.** `merge_thresholds` over an empty list returns
`dict(base_thresholds)` unchanged, and every existing deployment declares nothing. T5 asserts it
at flow level.

**The checker signature does not change.** `Stage1QualityChecker.check` already takes `overrides`
and the Protocol already declares it. The positional callers in `scripts/dhm_precip/` are
untouched.

## Design sketch

Four pieces, no persistence, one code path:

1. **A configuration surface.** `[[onboarding.station_qc_thresholds]]` blocks parsed the way
   `[[onboarding.calculated]]` already is, into a frozen `StationQcThresholdSpec` naming a station
   by **code** plus **network** (configuration cannot know a `StationId`), a `rule_id`, a
   `parameter`, a `time_step` and threshold values.

   **Base-`config.toml` only, enforced rather than documented.** `_deep_merge` replaces arrays
   wholesale (`config/_overlay.py:44-56`), so an overlay declaring one block would delete every
   base ceiling before the resolver could see them. Rejection is implementable: `load_merged_toml`
   parses each overlay into `overlay_data` at `:22` **before** merging at `:23`.

   **The `None`-means-inherit sentinel is unreachable from TOML** — `thresholds` is
   `dict[str, float | None]` where `None` means inherit (`types/domain.py:176`), and TOML has no
   null, so **omitting the key is the only encoding**.

2. **One pure resolution boundary.**
   `resolve_station_qc_overrides(specs, stations, rule_set, judged_station_ids, on_unresolvable)
   -> Resolution`.
   It resolves each spec's `(code, network)` to a `StationId`, checks the named rule exists for
   that parameter and cadence, validates the **merged** thresholds against the base rule, and
   either raises or drops-and-reports per `on_unresolvable`.

   **`judged_station_ids` is a parameter, not an inference.** Round 4 found the previous
   signature could not compute what its own result promised: "will this run judge that station"
   is the caller's predicate, and the two callers' predicates genuinely differ. The resolver never
   derives eligibility from `StationConfig`.

   **`on_unresolvable` is an `Enum`** (CLAUDE.md § Enums over booleans — "strict mode"/"lenient
   mode" prose would be satisfied by a `bool`). **`Resolution` is a frozen dataclass** carrying
   the resolved overrides, rejected specs with reasons, specs resolving outside
   `judged_station_ids`, and fan-out records. Both live in `types/`, not `services/`, per
   CLAUDE.md's placement of domain enums and frozen value types.

3. **One loader.** `config/onboarding.py` already parses `[[onboarding.calculated]]` into
   `OnboardingConfig`; the threshold specs join it there. Three modules carry their own private
   `_load_qc_rules()` (`flows/ingest_observations.py:73`, `flows/onboard.py:59`,
   `scripts/onboard.py:70`) — this plan adds no fourth. Note `load_onboarding_config()` returns
   **`None`** when a config has no `[onboarding]` section (`config/onboarding.py:153-155`), a
   distinct branch from an unset `SAPPHIRE_CONFIG` (which raises at `:146`); both must yield zero
   overrides on the ingest path rather than an exception.

4. **Threading at the one live call site**, plus an edit-time validation command so a correction
   is checked when it is made rather than on the next scheduled run.

**Why configuration rather than a table.** The spec stages it that way; it removes the migration,
the production store registration and the database grant; and removal is correct by construction —
a deleted block is gone on the next run, whereas upsert-only persistence would leave a stale
ceiling silently in force. The honest cost: a correction is a config edit, not a row update, and
there is **no record of when a ceiling changed** beyond git. `QcFlag.detail` embeds merged
thresholds at most flag sites (`services/qc.py:66, :85, :171, :189, :216`) so a failed observation
usually records the ceiling in force — **but not for frozen-sensor**, whose detail carries only
`tolerance` (`:142-145`); an overridden `min_consecutive` or `exclude_at_or_below` is recorded
nowhere but git. When v1 adds the table it follows **`station_thresholds`** — natural-key
`PrimaryKeyConstraint` plus `created_at`/`updated_at` (`db/metadata.py:321-333`) — and **not**
`forecast_qc_overrides`, which has neither.

## Locked design

### D1 — Does forecast QC get the same surface? **CLOSED: no.**
Identical shape, identical gap, and `forecast_qc_overrides` even has the table. No second-network
pressure on forecast QC (Plan 264 D1's reasoning); widening into the forecast cycle for a problem
nobody has is a bad trade. **Dropping** the dead table is equally out of scope — a deletion needs
its own rollback rehearsal. T6 records it as dead.

### D2 — How this composes with Plan 264. **CLOSED.**
**Plan 264 selects *which rule* applies; this plan adjusts *that rule's thresholds*.** An override
cannot suppress a rule. Three consequences:

- **An override has no `rule_version`** and `merge_thresholds` has no `break`, so an override
  whose key matches more than one resolved rule **applies to all of them**.
  `scripts/dhm_precip/qc_ruleset.py:67-93` ships two `frozen_sensor` rules differing only by
  version **and key set** — one carries `exclude_at_or_below`, the other deliberately does not —
  so an override supplying that key would install an exclusion floor on the rule that exists to
  have none. T2 **reports the fan-out**; it is not fatal, because fan-out is legitimate.
- **This plan is not independent of Plan 264.** 264 makes `rules_for` network-aware and makes a
  station→network mapping required on `check`. **If 264 lands first**, T2 passes each spec's
  network to `rules_for`. **If this plan lands first**, 264 T1/T2 must update T2's call site — a
  required edit to 264, recorded there.
- **An override follows a network rule that replaces a generic one.** 264 selects the replacement
  within the same `(rule_id, parameter, time_step)` group, and `merge_thresholds` matches exactly
  those fields plus `station_id`, so the override lands on the new rule and is validated against
  its changed base thresholds. Revision 3 claimed such an override becomes unresolvable; it does
  not.

### D3 — What happens to a threshold that cannot be applied. **CLOSED: report, never halt.**
With one code path the previous split by call site is unnecessary. On the scheduled ingest flow:

- **A spec that cannot be resolved** — unknown station code, unknown `rule_id`, no matching rule,
  invalid merged thresholds — is **dropped and reported**; every other station is judged normally.
  The flow never halts on a resolvable-spec failure, because the trigger is often **database
  state, not a config edit**: decommissioning, renaming or re-coding a station would otherwise
  stop observation ingest fleet-wide with nobody watching, which `docs/workflow.md` § Preserve
  Existing Logic forbids introducing.
- **A spec that resolves but will not be applied** — the station is outside
  `judged_station_ids` — is reported the same way. This is not an edge case: **Plan 268's six
  gauges are exactly this class** until 268 promotes them. Declared-but-inert is the same
  fail-open this plan exists to kill, so it is surfaced, not assumed.
- **Configuration that will not parse still raises.** T1's rejections happen at parse time, before
  T2 sees a spec, and the loader parses the whole `[onboarding]` section. Leniency governs
  **resolvable-spec failures only** — T3 tests both classes so the distinction is pinned.
- **Reported where, precisely.** A `PipelineHealthRecord` at **`PipelineHealthStatus.WARNING`**
  (`types/enums.py:187-190` has only `OK`/`WARNING`/`CRITICAL`) under a new `PipelineCheckType`
  member, plus a log line and a count on `IngestResult`. **Stated honestly: the health API reads
  this** — `api/routes/health.py` resolves check types dynamically — **but `ops/watchdog.py` does
  not probe it**, because the watchdog queries explicitly named check types and a new enum member
  does not extend it. A watchdog probe is a named follow-on, not a claim this plan may make.
- **The edit-time command (T4) is the strict consumer**, so an operator correcting a ceiling finds
  out immediately rather than on the next run.

### D4 — An override attached to a rule that resolves to nothing. **CLOSED by T2.**
Round 1 deferred this to Plan 264 T3; both reviews found that false — 264 raises only when a
non-empty rule set resolves nothing for a series, so an override with a mismatched cadence stays
inert whenever any *other* rule resolves. T2 closes it directly, and D3 decides what follows.

### D5 — An API surface. **CLOSED: not in this plan.**
No stored row to serve; the question arrives with the v1 migration. Round 1's justification was
withdrawn: it rested on a rating-table basis that Plan 268's own round 2 **rejected** in favour of
a published flood-envelope curve from catchment area, characterised there as *"not a leak"*. If
D14 settles on a table-derived ceiling an API surface widens exposure; if it settles on the
flood-envelope basis, scope discipline is the only reason. D14 is open, so v1 must revisit this.

## Tasks

### T1 — The configuration surface

**Outcome**: a per-station QC threshold is declarable in configuration and is validated as far as
a parser without a registry can validate it.
**In**: `src/sapphire_flow/config/onboarding.py` (the spec type, its pydantic boundary model, and
its place on `OnboardingConfig`); **`src/sapphire_flow/config/_overlay.py`** (the overlay
rejection, implementable at `:22` where each overlay is parsed before the merge at `:23`);
`docs/spec/config-reference.toml`; unit tests.
**Out**: no station, rule or parameter *existence* checking and no merged-threshold checking —
both need the registry and the rule set, and are T2's. No threshold *values*: this ships the
surface empty and Plan 268 D14 owns what goes in it. No change to any existing `[onboarding.*]`
block, and **no change to array-merge behaviour for any other key** — the rejection is scoped to
this one key, leaving the eight other `load_merged_toml` callers untouched.
**Verification**: `uv run pytest tests/unit/config/test_onboarding_qc_thresholds.py` asserting a
valid block parses to the expected frozen spec, and that each of these is rejected naming the
offending key —
- a non-numeric or non-finite threshold value; an unknown field; a non-positive `time_step`; a
  missing station code or network;
- **a threshold key not belonging to the named rule**, against the **corrected** key list (T6
  fixes the spec): `range_check` → `value_min`/`value_max`; `rate_of_change` → `max_rate`;
  `frozen_sensor` → `tolerance`/`min_consecutive`/`exclude_at_or_below`; `spike` → **`max_delta`
  or `tolerance`**; `gross_outlier` → `k_sigma`. **`max_delta` is the knob production actually
  uses** (`config.toml:271`, `:327`) and `services/qc.py:161` checks it before `tolerance`;
  revision 4's allowlist, taken from the spec, would have rejected it and accepted an inert
  `tolerance` override. Where a rule has two modes, the override's keys must match the mode the
  base rule selects;
- **a value well-typed but nonsensical for its rule** — a negative `max_rate` (every comparison
  then exceeds it, `services/qc.py:79`), a non-positive or fractional `min_consecutive` (silently
  truncated by `int()`, `:98`), a negative spike threshold;
- **two blocks sharing `(network, station code, rule_id, parameter, time_step)`** — duplicates
  are rejected at parse, because `merge_thresholds` applies every match in order and the last
  would silently win;
- **an overlay declaring this key is rejected before the merge**, with a diagnostic naming the
  base file. Verified that no existing overlay carries it (`config/overlays/staging-5-stations.toml`,
  `mac-mini.toml`), so nothing in the repository breaks;
- **omitting a key is the only way to express "inherit"**;
- a config with no such block, **and a config with no `[onboarding]` section** (making
  `load_onboarding_config()` return `None`, `config/onboarding.py:153-155`), each yield an empty
  list rather than raising.
**Pre-change**: N/A — new capability; nothing to disprove.

### T2 — The resolution boundary

**Outcome**: declared specs become `StationQcOverride` values valid by construction, from one pure
function, with unapplicable specs classified rather than guessed at.
**In**: a named new module `src/sapphire_flow/services/station_qc_overrides.py` holding the
function; the `on_unresolvable` **`Enum`** and the frozen **`Resolution`** result in
`src/sapphire_flow/types/` (CLAUDE.md places domain enums and frozen value types there, not in
`services/`); unit tests. Naming the module matters: three other tasks import the symbol.
**Out**: no I/O — registry, rule set and `judged_station_ids` are parameters, which is what keeps
it testable without a database. **The function never infers eligibility from `StationConfig`**;
each caller supplies its own judged set. No threading into any flow (T3). No change to
`StationQcOverride`.
**Verification**: `uv run pytest` on the new module asserting —
- a station code resolves by `(code, network)`, proven by a fixture where two stations share a
  code across different networks — well-defined because `uq_stations_network_code`
  (`db/metadata.py:300`) makes the pair globally unique;
- an unknown station code, an unknown `rule_id`, or a `(rule_id, parameter, time_step)` triple
  matching no rule **raises in strict mode and is dropped-and-reported in lenient mode**, with the
  offending spec named in both, and **multiple failures reported together** rather than only the
  first — T4 prints every rejection, which a fail-fast resolver cannot supply;
- **merged-threshold validation against the base rule** — a partial `range_check` override
  supplying only `value_min` above the rule's `value_max` is rejected, which only this layer can
  see;
- **a spec resolving to a station outside `judged_station_ids` is classified as such** — neither
  a rejection nor a silent success;
- **an override matching two rules differing only by `rule_version` is applied to both and the
  fan-out reported** (D2), against an **inline two-version fixture**, citing
  `scripts/dhm_precip/qc_ruleset.py:67-93` as the motivating case rather than importing it, since
  that path is outside the pyright gate;
- **the 264 composition**: where a network-specific rule replaces a generic one within the same
  key group, the override follows the replacement and is validated against its new base
  thresholds (D2).
**Pre-change**: N/A — new pure function; its behavioural consequence is proven in T3.

### T3 — Thread into the scheduled ingest flow

**Outcome**: the operational ingest path judges a station against its declared ceiling, reports
what it could not apply, and never halts on a resolvable-spec failure.
**In**: `src/sapphire_flow/flows/ingest_observations.py` — specs from `config/onboarding.py`'s
loader (no fourth private `_load_*`), resolved **once** at flow level in **lenient** mode against
the `all_stations` the flow already holds (`:586-592`; three kind-filtered calls whose union is
the whole table, since `StationKind` has exactly three members — do not add a fourth query), with
`judged_station_ids` taken from the flow's own `eligible` set (`:601-609`); a new keyword
parameter on `_run_qc_task` beside `qc_rules` replacing the `overrides=[]` at `:308`; the drop and
inert counts on `IngestResult`; and the health record. **`src/sapphire_flow/types/enums.py`** (the
new `PipelineCheckType` member) and **`docs/spec/types-and-protocols.md`**'s enum mirror are in
scope. Adding a member is **additive-safe and needs no migration** — `pipeline_health.check_type`
is `sa.Text` with no CHECK constraint (`db/metadata.py:1881`) and `api/routes/health.py` resolves
types dynamically; state that, or an implementer will reasonably ask.
**Out**: the checker signature is unchanged. Forecast call sites keep `qc_overrides=[]` (D1).
`scripts/dhm_precip/` untouched. **The onboarding QC path keeps `overrides=[]`** (descoped — see
§ What this deliberately does not do). No watchdog probe — named as a follow-on in D3, not built
here. **`_run_qc_task`'s new parameter carries a default**, because the tests call
`_run_qc_task.fn(` directly (the production caller at `:692-700` passes only the first four
positionally).
**Verification**: `uv run pytest tests/unit/flows/test_ingest_observations.py` asserting —
- **in one run**, a station with a declared ceiling is judged against it while a station without
  one is judged against the base threshold — the discriminating case, since an implementation
  applying overrides to every station passes a single-station test;
- **a spec naming a nonexistent station does NOT abort the run**, and the drop is reported as a
  `WARNING` `PipelineHealthRecord` under the new check type, plus the log line and the count;
- **a spec that resolves to a station outside `eligible` is reported the same way** — Plan 268's
  six gauges are exactly this class;
- **the health record and the counts survive both early returns.** `IngestResult` is built at
  `:613`, `:660` and `:773`, and the first two return **before** the QC loop; the existing
  `_append_fetch_health_record` precedent fires at `:645`, after the first of them. Resolution and
  its health record therefore happen **immediately after the registry is in hand and before the
  no-eligible early return at `:611`**, or a drop on such a run is reported nowhere;
- **resolution happens once per flow run** — asserted by spying on `resolve_station_qc_overrides`
  itself, **not** the loader: an implementation that loads once and re-resolves inside the
  per-`(station, parameter)` loop (`:690`) passes a loader-call count;
- **a malformed or duplicate declaration still raises on this path** — leniency governs
  resolvable-spec failures, not parse failures (D3);
- **an unset `SAPPHIRE_CONFIG`, and a config with no `[onboarding]` section, each yield zero
  overrides without raising** — `_load_qc_rules` falls back safely (`:79-81`) while
  `load_onboarding_config()` raises (`:146`) or returns `None` (`:153-155`); without this the
  asymmetry takes the flow down on a path the rule-set loader tolerates.
`uv run pytest` passes whole.
**Pre-change**: a RED test proving a **declared, correctly-resolved** override has no effect on
the ingest flow's QC result today, because the call site passes `[]`. It fails on the flagged
status of a specific observation — not on a missing symbol.

### T4 — Edit-time configuration validation

**Outcome**: an operator correcting a ceiling learns immediately whether it resolves and whether
it will actually apply, instead of finding out on the next scheduled run or not at all.
**In**: a `--validate-config` branch in `scripts/onboard.py`, inserted **after the configuration
load (`:196-209`) and engine creation, and before `_run_migrations` (`:234-238`)**; it loads the
specs, fetches the live registry, resolves in **strict** mode, prints every rejection and every
declared-but-inert spec, and exits non-zero only on a rejection; unit tests.
**Out**: **strictly read-only.** It must not download (`:223`), must not run migrations (`:237`),
must not onboard (`:339`) and must not write. No change to any flow, to QC, or to T2's function —
this is a second caller, not new logic. No new CLI module.
**Verification**: `uv run pytest tests/unit/scripts/` asserting —
- a config naming an unknown station **exits non-zero** and names that spec, with **every**
  rejection printed when several are wrong, not just the first;
- **a ceiling for a station that exists but is not currently judged exits zero and is reported as
  inert** — the Plan 268 case: legitimate but not yet effective, and an operator must be able to
  tell that apart from a typo;
- **a ceiling for a station not yet onboarded at all exits non-zero** and says so — this command
  is defined against the registry as it stands, and that limitation is stated rather than
  discovered;
- a clean config **exits zero and prints no rejection or inert line** — asserted on captured
  stdout, not on an impression;
- **no migration, download, onboarding call or store write occurs** on any of the above, asserted
  directly. `scripts/onboard.py:234-238` runs `alembic upgrade head` unconditionally today, so a
  validation branch placed carelessly migrates a database as the side effect of a check.
**Pre-change**: N/A — new capability.

### T5 — Zero-configuration equivalence

**Outcome**: proof that a deployment declaring no thresholds is unaffected.
**In**: `tests/unit/flows/test_ingest_observations.py` — **flow level, deliberately**.
**Out**: not a new QC behaviour; a regression bar only.
**Verification**: a fixed observation series run through the ingest flow with **no threshold
blocks declared** yields flags, statuses and `detail` text identical to the pre-T3 result. The
rule set is the one **loaded from `config.toml`** — what actually runs — not
`_default_swiss_qc_rules()`, which `load_qc_rules` never returns while a `[qc_rules]` section
exists. Thresholds are not a returned field; they appear only inside `QcFlag.detail`, so the
comparison is over `detail` text.
**Exit gate**: the baseline is captured and committed **before T3 lands**, as a test phase 4 must
keep green — the oracle cannot be generated after the change it exists to detect.

### T6 — Documentation, the spec corrections, and the sibling sweep

**Outcome**: the surface is documented, two spec defects are fixed, the dead tiers are recorded as
dead, and no plan still points at scope this one has dropped.
**In**: `docs/spec/types-and-protocols.md` — § StationQcOverride now describes a surface that
exists and carries D2's composition rule; **§ "Threshold keys by rule" gains `max_delta`**, which
production has used since before this plan and which the spec has never listed; and the
`PipelineCheckType` mirror gains the new member. Plus `docs/touchpoint-maps.md`; `docs/v0-scope.md`
(which lists `forecast_qc_overrides` without noting nothing reads it); and the two sibling plans,
`docs/plans/264-qc-rules-select-on-network.md` and `docs/plans/268-dhm-barkhk-runoff-delivery.md`.
**Out**: no code change. `docs/spec/config-reference.toml` belongs to T1.
**Verification**: bounded inspection —
- the D2 composition rule is stated **once**; Plan 264's retracted "station beats network beats
  generic" phrasing is gone;
- **`max_delta` is documented**, with a note that the checker prefers it over `tolerance`;
- **the persisted DB tier is recorded as UNOWNED, deferred to v1**, in the spec and the touchpoint
  map — not only inside this plan, where a future reader will not look;
- **`services/onboarding.py` is recorded as knowingly un-threaded**, with the reason, so the next
  reader does not mistake it for an oversight — and Plan 268's dependency on **station promotion
  and forecast targets** is stated in 268, since no ceiling can apply to its six gauges until it
  makes that call;
- **the sibling sweep is by meaning, not by string.** Two sweeps have now missed live text:
  `268:704-705` still assigned in-memory construction to T7 after the ownership sentence above it
  was corrected, and an earlier one left an "open question" reopening what it had just closed. A
  search for old wording would have caught neither;
- `forecast_qc_overrides` is recorded as schema-only with its six hard-coded call sites;
  `StationStore.store_thresholds` as having no caller;
- the configuration shape's costs are stated: overlay wholesale-replacement, no record of *when* a
  ceiling changed, and the frozen-sensor detail gap.
**Pre-change**: N/A — documentation.

```json
{
  "phases": [
    { "id": "phase-1", "tasks": ["T1"] },
    { "id": "phase-2", "tasks": ["T2"], "depends_on": ["phase-1"] },
    { "id": "phase-3", "tasks": ["T5"], "depends_on": ["phase-2"] },
    { "id": "phase-4", "tasks": ["T3", "T4"], "depends_on": ["phase-3"], "parallel": true },
    { "id": "phase-5", "tasks": ["T6"], "depends_on": ["phase-4"] }
  ]
}
```

T5 sits **before** T3: its baseline is pre-change evidence and cannot be captured after the change
it exists to detect. T3 and T4 are genuinely independent — both consume T2, neither consumes the
other, and unlike revision 4 no task's exit gate now depends on a sibling declared parallel to it.

**Cross-plan sequencing.**

- **Plan 264 and this plan are coupled in both directions** (D2). If 264 lands first, T2 passes
  each spec's network to `rules_for`. If this plan lands first, **264 T1/T2 must update T2's call
  site** — a required edit to 264, recorded there.
- **Plan 264 T4's golden fixture must be captured before any DHM rule reaches `config.toml`.**
  This plan changes no QC output with nothing declared (T5), so it does not threaten that fixture.
- **Plan 268 must decide station promotion and forecast targets before any ceiling can bite.** Its
  six gauges are `station_status = onboarding` with `forecast_targets` unset, so they are judged
  by neither QC path. This plan reports that as inert rather than silently doing nothing, but the
  lifecycle decision is 268's. Its T7 must also declare the six ceilings as configuration and
  resolve them through T2 rather than constructing them in memory — `268:704-705` still says
  otherwise, and T6 sweeps it.

## Explicitly out of scope

- **The onboarding QC path.** Descoped 2026-09-11 with reasons stated above; it keeps
  `overrides=[]`.
- **A database table, store, migration, Protocol store method, production store registration or
  database grant.** Deferred to v1 with the spec — and **currently unowned**; T6 records that.
- **A watchdog probe for the new check type.** Named as a follow-on in D3 rather than claimed.
- **Re-running QC over historical observations** after a ceiling is corrected.
- **The DHM threshold values.** Plan 268 D14 owns them, including the basis (a published
  flood-envelope curve from catchment area is its current recommendation) and the coefficient,
  which its own text says the hydrologist sets.
- **Rule selection.** Plan 264 owns the network dimension, the fail-closed policy and the
  flag-version correction.
- **Reviving or dropping `forecast_qc_overrides`** (D1) — neither; document it.
- **An API or dashboard surface** (D5) — arrives with the v1 migration.
- **Fixing overlay composition** so an overlay can extend rather than replace an array. Plan 264
  D4 owns that; T1 rejects this one key rather than redesigning the merge.
- **A writer for `station_thresholds`.**
- **Any change to `Stage1QualityChecker.check` or the `QualityChecker` Protocol.** Already correct.
