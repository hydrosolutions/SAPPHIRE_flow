---
status: DRAFT
created: 2026-09-11
revised: 2026-09-26
plan: 269
title: Per-station QC thresholds declared in onboarding configuration
scope: Deliver per-station observation QC threshold overrides and an explicit pending-network declaration through configuration — a validated TOML surface, one pure resolution boundary, threading into the scheduled observation-ingest flow, and an edit-time validation command. NOT the onboarding QC path (descoped, see § What this deliberately does not do), NOT a database table, store or migration (the spec defers that to v1), NOT the DHM threshold values themselves (Plan 268 D14), NOT forecast QC overrides, NOT rule selection (Plan 264), NOT an API surface, NOT a new rule kind.
blocks: [268]
blocked_by: []
related: [264, 268, 012, 315]
open_gaps: ["onboarding keeps overrides=[] — the follow-on this plan names is UNOWNED; trigger and reasoning recorded in-plan 2026-09-23"]
reviews:
  - "codex 2026-09-11 r1 — NOT READY, 5 blockers; killed the write path and the migration"
  - "claude 2026-09-11 r1 — NOT READY, 3 blockers; same central defect found independently"
  - "codex 2026-09-11 r2 — NOT READY, 3 blockers; found a third entrypoint and the 264 signature break"
  - "claude 2026-09-11 r2 — NOT READY, 3 blockers; measured the staging blast radius and the overlay trap"
  - "codex 2026-09-11 r3 — NOT READY, 1 blocker; the strict-ordering bootstrap contradiction"
  - "claude 2026-09-11 r3 — NOT READY, 2 blockers; 268's ceilings inert, and the suite cannot prove wiring"
  - "codex 2026-09-11 r4 — NOT READY, 1 blocker + 7 majors; Step 2.5 writes observations; the spike knob is undocumented"
  - "claude 2026-09-11 r4 — NOT READY, 3 blockers + 9 majors; onboarding QC skips 268's stations entirely"
  - "codex 2026-09-11 r5 — NOT READY, 1 blocker + 3 majors; the datum-skipped inert class; my 268 lifecycle edit was false"
  - "claude 2026-09-11 r5 — NOT READY, 3 blockers + 5 majors; strict mode unsatisfiable; the water-level datum frame"
  - "claude 2026-09-25 r6 — NEEDS_CHANGES; Plan 264 D4 dependency, validator rejection safety, pending-state alerting, and stale post-272 claims; folded, exact-state re-review pending"
open_decisions: []
source: 2026-09-11 — the owner's 2026-09-10 decision on Plan 268 D14 (per-station QC ceilings live in station onboarding configuration, updatable later) cannot be delivered on the current code. Descoped to one code path on 2026-09-11 after eight independent reviews, because the onboarding path generated most of the defects and delivers nothing to the case the plan exists for.
---

# Plan 269 — per-station QC thresholds declared in onboarding configuration

## Status

**DRAFT — unblocked.** Plan 264 and its D4 policy are implemented and merged in PR #315
(`dbbea4d9`, 2026-09-26). Network-specific rules live beside generic rules in the base
configuration; station-specific thresholds remain this plan's `[[onboarding.station_qc_thresholds]]`
surface. Plan 269 still needs its own final review before it can be marked READY.

Plan 272 no longer blocks this work (owner clarification, 2026-09-25).

**Measured post-272 scheduled-ingest limitation (2026-09-25).** `_run_qc_task` uses a two-hour
lookback and one-hour lookahead, extending only around newly fetched timestamps. A single new
daily reading therefore has no previous daily sample in the QC group
(`src/sapphire_flow/flows/ingest_observations.py:422-449`). `infer_time_step` returns `None` for fewer than two distinct
timestamps (`src/sapphire_flow/services/qc.py:41-63`), so daily rules and their overrides do not resolve for
that group; it remains `QC_UNCHECKED`. Plan 269 T3 remains useful for sufficiently frequent
observations, including per-station water-level limits. For Plan 268's daily discharge data, T7's
full-series import QC is the path that can apply the D14 ceilings. This limitation does not
reinstate a Plan 272 blocker.

## Problem

The owner settled where per-station QC thresholds live: **configuration, updatable later**
(Plan 268 D14). The spec says the same and has all along —
`docs/spec/types-and-protocols.md` § StationQcOverride: *"Loaded from station onboarding TOML;
v1 migrates to DB (dashboard-editable)."* None of it is built.

Measured against the implementation merged to `main` on 2026-09-26:

- **`StationQcOverride` is a bare dataclass** (`types/domain.py:198-203`). The type is documented,
  including the `overrides` parameter on the `QualityChecker` Protocol
  (`protocols/stores.py:1055-1065`); the **supply** is absent — no configuration key, no parser, no
  resolution step, no loader anywhere in `src/`.
- **The scheduled ingest flow hard-codes an empty list** — `flows/ingest_observations.py:477-483`,
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

**The cost, stated plainly, and it does not stop at history.** A station's backfilled history is
QC'd on base thresholds, so a historical series can be judged by a ceiling its operator has since
corrected. Re-running QC over history is not in this plan. **The second half reaches the wired
path**: onboarding computes climatological baselines from `QC_PASSED` observations only
(`services/onboarding.py:848-861`), so every value a per-station ceiling would have passed is
excluded from the baseline — and the ingest path's `gross_outlier` rule, which **will** receive
the override under T3 (`services/qc.py:281-295`), is then judged against a baseline censored by the
un-overridden ceiling. Round 5 found this; it is a real limitation, not a rounding error.
(⚠️ **Narrowed 2026-09-23**: true of *Step 5*, not of the whole run. Step 5 fetches
`qc_status=RAW` only (`services/onboarding.py:775-782`), but Step 3's upsert **resets
`qc_status` to `RAW`, clearing `qc_flags` and `qc_rule_version`, whenever a value or the
rating-curve provenance changed** (`store/observation_store.py:100-138`), so a restated row
re-enters Step 5 and IS re-judged. Measured while reviewing Plan 315.) If that becomes
load-bearing, it is a follow-on that should be designed against the onboarding path's own gate,
not bolted onto this one.

---

### 🔴 OPEN AND UNOWNED — the follow-on named just above has no plan and no owner

**Recorded 2026-09-23 so it surfaces at its trigger instead of in a conversation.** ⛔ *Plan 315
was briefly recorded as closing this. It does NOT: 315 fixes the zero-rule fail-open on this same
path and leaves `overrides=[]` exactly as it is (315 `scope`). Different defect, same gate.*

**What is still missing:** `services/onboarding.py:801` passes `overrides=[]`, so backfilled
history is judged on base thresholds, and the censored-baseline loop above follows from it.

**Why it is NOT folded into Plan 315, decided 2026-09-23 with the owner:** it looks like a
one-argument change — `_merge_thresholds` exists (`services/qc.py:26-38`), it is consulted at
`:341`, and the call site is three lines above the code Plan 315 rewrites. It is not. This section
already records that the onboarding path *"was the source of most blockers across rounds 2, 3 and
4 — the batch-versus-registry question, the union, the write-order window, and Step 2.5's hidden
observation writes"*, and that **the two paths have genuinely different judgement predicates**, so
threading overrides here is a design question about which stations qualify, not a wiring change.
Folding it into 315 — already five review rounds and sixteen findings for one narrow defect —
would rebuild the plan shape that failed here across eight rounds.

**The trigger, so this is not "someday":** draft it when a station whose operator has CORRECTED a
threshold needs its backfilled history judged on the corrected value — in practice, a Nepal
gauge onboarded after Plan 268 D14 sets real per-station ceilings. ⚠️ **And not before Plan 269
itself lands**, because the resolution boundary it would thread does not exist yet.

**Sequencing note:** Plan 315 runs last and rewrites Step 5. Whoever picks this up should check
whether 315 has landed first — the two touch the same twenty lines, and doing them in either order
is fine, but doing them in parallel is not.

**With nothing declared, QC output is unchanged.** `merge_thresholds` over an empty list returns
`dict(base_thresholds)` unchanged, and every existing deployment declares nothing. T5 asserts it
at flow level.

**This plan does not change the checker signature.** Plan 264 made the keyword-only
`station_networks` mapping required by `Stage1QualityChecker.check` and `resolve_selection`.
Scheduled ingest already builds and passes that mapping. T2 must select and validate rules using
each spec's network; T3 must preserve the mapping when it passes resolved overrides to
`_run_qc_task`.

## Design sketch

Four pieces, no persistence, one code path:

1. **A configuration surface.** `[[onboarding.station_qc_thresholds]]` blocks parsed the way
   `[[onboarding.calculated]]` already is, into a frozen `StationQcThresholdSpec` naming a station
   by **code** plus **network** (configuration cannot know a `StationId`), a `rule_id`, a
   `parameter`, a `time_step` and threshold values. Also parse `onboarding.qc_pending_networks` as a unique list
   of case-sensitive network tokens. T3 emits pending INFO for missing stations only when the
   network is explicitly in this list and the target tenant has no station on that network.

   **Base-`config.toml` only, enforced rather than documented.** `_deep_merge` replaces arrays
   wholesale (`config/_overlay.py:49-62`), so an overlay declaring one block would delete every
   base ceiling before the resolver could see them. Rejection is implementable: `load_merged_toml`
   parses each overlay into `overlay_data` at `:22` before merging at `:28`; Plan 264 already
   rejects a `qc_rules` overlay at that boundary.

   **The `None`-means-inherit sentinel is unreachable from TOML** — `thresholds` is
   `dict[str, float | None]` where `None` means inherit (`types/domain.py:203`), and TOML has no
   null, so **omitting the key is the only encoding**.

   **🔴 A water-level threshold is expressed in the datum-shifted frame, not metres above sea
   level.** Observations are shifted by the station's datum before the checker sees them
   (`services/qc_datum.py:44-55`, applied at `flows/ingest_observations.py:471-475`), so the base
   thresholds are gauge-relative — the daily water-level range was `-5.0 … 30.0`, widened to `-10 … 9000` on 2026-09-23. A ceiling written in
   m a.s.l. passes every validation listed in T1 and T2 and is wrong by the station's datum.
   T1 documents the frame; nothing can detect the error automatically.

2. **One pure resolution boundary.**
   `resolve_station_qc_overrides(specs, stations, rule_set, is_applicable) -> Resolution`.
   `is_applicable(station, rule_id, parameter)` is supplied by the caller. It resolves each spec's `(code, network)` to a `StationId`, checks the named rule exists for
   that parameter and cadence, validates the **merged** thresholds against the base rule, and
   classifies every spec. **It never raises on a resolvable-spec failure** — it always returns a
   `Resolution`, and each caller decides what to do with it.

   That is a deliberate reversal. Revision 5 gave it a strict/lenient `Enum`, and round 5 found
   strict unsatisfiable (a raising resolver returns no result, so the one strict caller could not
   print the classification its own gate required) **and unconsumed** after the descope — a dead
   tier of exactly the kind § "Two dead tiers beside this one" warns about. **Classification
   belongs to the resolver; policy belongs to callers.**

   **`is_applicable(station, rule_id, parameter) -> bool` is supplied by the caller.** A per-station
   predicate is not enough because the datum skip depends on the rule and parameter. `obs_skipped_rules` drops `{range_check, gross_outlier}` for a datum-less water-level station
   (`services/qc_datum.py:29-32`), and the checker skips the rule before merging — so a ceiling on a station inside the judged set can still be inert. The
   resolver never derives applicability from `StationConfig`.

   **`Resolution` is a frozen dataclass** carrying resolved overrides, rejected specs with typed
   reason kinds, specs that resolved but are not applicable, and fan-out records. Retain every
   applicable reason per spec; validation must not stop at the first failure. It and any
   supporting types live in `src/sapphire_flow/types/`, following the repository's existing
   placement of domain value types. (Revision 5 attributed that to CLAUDE.md; CLAUDE.md says
   nothing about directories, and the invented citation is withdrawn.)

3. **One loader.** `config/onboarding.py` already parses `[[onboarding.calculated]]` into
   `OnboardingConfig`; the threshold specs join it there. Three modules carry their own private
   `_load_qc_rules()` (`flows/ingest_observations.py:84`, `flows/onboard.py:59`,
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
thresholds at most flag sites (`services/qc.py:137, :157, :247, :265, :291`) so a failed observation
usually records the ceiling in force — **but not for frozen-sensor**, whose detail carries only
`tolerance` (`:216-219`); an overridden `min_consecutive` or `exclude_at_or_below` is recorded
nowhere but git. When v1 adds the table it follows **`station_thresholds`** — natural-key
`PrimaryKeyConstraint` plus `created_at`/`updated_at` (`db/metadata.py:321-333`) — and **not**
`forecast_qc_overrides`, which has neither.

## Locked design

### D1 — Does forecast QC get the same surface? **CLOSED: no.**
Identical shape, identical gap, and `forecast_qc_overrides` even has the table. No second-network
pressure on forecast QC (Plan 264 D1's reasoning); widening into the forecast cycle for a problem
nobody has is a bad trade. **Dropping** the dead table is equally out of scope — a deletion needs
its own rollback rehearsal. T6 records it as dead.

### D2 — How this composes with Plan 264. **CLOSED: shared-base network rules, then station overrides (owner, 2026-09-25).**
**Plan 264 selects *which rule* applies; this plan adjusts *that rule's thresholds*.** An override
cannot suppress a rule. Three consequences:

- **An override has no `rule_version`** and `merge_thresholds` has no `break`, so an override
  whose key matches more than one resolved rule **applies to all of them**.
  `scripts/dhm_precip/qc_ruleset.py:67-93` ships two `frozen_sensor` rules differing only by
  version **and key set** — one carries `exclude_at_or_below`, the other deliberately does not —
  so an override supplying that key would install an exclusion floor on the rule that exists to
  have none. T2 **reports the fan-out**; it is not fatal, because fan-out is legitimate.
- **Plan 264 is implemented in PR #315**; its selected-rule behavior and required network mapping
  are specified above. T2 resolves the station by `(network, code)` and validates the named rule
  through `rules_for(..., network=spec.network)`.
- **An override follows a network rule that replaces a generic one.** 264 selects the replacement
  within the same `(rule_id, parameter, time_step)` group, and `merge_thresholds` matches exactly
  those fields plus `station_id`, so the override lands on the new rule and is validated against
  its changed base thresholds. Revision 3 claimed such an override becomes unresolvable; it does
  not.

### D3 — What happens to a threshold that cannot be applied. **CLOSED: classify, report, never halt.**

On scheduled ingest, classify each declaration separately:

- A missing station is pending INFO only when the network is listed in
  `onboarding.qc_pending_networks` and the target tenant has no registered stations on that
  network. If the network has any registered station, an unknown code is a WARNING
  rejection, so a typo cannot disappear into a pending state.
- A matched station with `station_status = onboarding` is pending INFO. This covers Plan 268's
  six gauges and creates no warning health record.
- A matched station outside the judged set for another reason, or a rule skipped for its
  parameter (for example `range_check` on datum-less water level), is not-applicable and emits a
  WARNING health record.
- Malformed TOML still raises during parsing. Resolvable-spec rejections do not halt ingest;
  T3 reports them and processes unaffected stations. T4 remains strict by default and exits
  non-zero while a fatal rejection remains. Its opt-in waives only typed `STATION_NOT_FOUND`, never
  any rule, parameter, cadence, mode or merged-threshold reason.

Pending states are recorded in structured INFO logs and `IngestResult` counts. Rejections and
not-applicable declarations use a `PipelineHealthRecord` with `PipelineHealthStatus.WARNING`;
the health API reads this check type, but `ops/watchdog.py` does not probe it. A watchdog probe is
a follow-on, not part of this plan. `is_applicable(station, rule_id, parameter)` is supplied by
the caller because rule skips depend on parameter as well as station and rule.

The resolver never raises on resolvable-spec failures; it returns every applicable typed reason
for each rejected declaration. Callers choose policy. This preserves ingest for unrelated
stations while allowing the validator and Plan 268 T7 to fail closed.

### D4 — An override attached to a rule that resolves to nothing. **CLOSED by T2.**
Plan 264 T3 does not add a raise. Scheduled ingest records a zero-rule group as `QC_UNCHECKED`
under Plan 272; this plan independently classifies an override that cannot resolve or apply,
even when other rules resolve for the series. T2 reports that override outcome, and D3 defines
the caller policy.

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
**In**: `src/sapphire_flow/config/onboarding.py` (the spec type, its pydantic boundary model,
`qc_pending_networks`, and its place on `OnboardingConfig`); **`src/sapphire_flow/config/_overlay.py`** (the overlay
rejection, implementable at `:22` where each overlay is parsed before the merge at `:23`);
`docs/spec/config-reference.toml`; unit tests.
**Out**: no station, rule or parameter *existence* checking and no merged-threshold checking —
both need the registry and the rule set, and are T2's. No threshold *values*: this ships the
surface empty and Plan 268 D14 owns what goes in it. No change to any existing `[onboarding.*]`
block, and **no change to array-merge behaviour for any other key** — the rejection is scoped to
this one key, leaving the eight other `load_merged_toml` callers untouched.
**Verification**: `uv run pytest tests/unit/config/test_onboarding_qc_thresholds.py` asserting a
valid block parses to the expected frozen spec; `qc_pending_networks` accepts unique network
tokens, rejects non-string/duplicate/empty tokens; and each of these is rejected naming the
offending key —
- a non-numeric or non-finite threshold value; an unknown field; a non-positive `time_step`; a
  missing station code or network;
- **a threshold key not belonging to the named rule**, against the **corrected** key list (T6
  fixes the spec): `range_check` → `value_min`/`value_max`; `rate_of_change` → `max_rate`;
  `frozen_sensor` → `tolerance`/`min_consecutive`/`exclude_at_or_below`; `spike` → **`max_delta`
  or `tolerance`**; `gross_outlier` → `k_sigma`. **`max_delta` is the knob production actually
  uses** (`config.toml:273`, `:329`) and `services/qc.py:235-250` checks it before `tolerance`;
  revision 4's allowlist, taken from the spec, would have rejected it and accepted an inert
  `tolerance` override. **Mode *compatibility* with the base rule is T2's, not T1's** — a parser
  cannot know which mode a rule selects, and revision 5 asked T1 for a check its own `Out`
  excludes;
- **a value well-typed but nonsensical for its rule** — a negative `max_rate` (every comparison
  then exceeds it, `services/qc.py:150-151`), a non-positive or fractional `min_consecutive` (silently
  truncated by `int()`, `:168-169`), a negative spike threshold;
- **two blocks sharing `(network, station code, rule_id, parameter, time_step)`** — duplicates
  are rejected at parse, because `merge_thresholds` applies every match in order and the last
  would silently win;
- **an overlay declaring this key is rejected before the merge**, with a diagnostic naming the
  base file. Verified that no existing overlay carries it
  (`config/overlays/staging-5-stations.toml`, `mac-mini.toml`), so nothing in the repository
  breaks. **Stated because it is not obvious**: the rejection sits in `_overlay.py`, which nine
  other modules call across eleven sites (including `config/qc_rules.py`,
  `config/deployment.py` and `flows/run_forecast_cycle.py:353,619,649`), so an overlay carrying
  this key fails **every** config load, the forecast cycle included — not just onboarding. That
  is the intended consequence of "configuration that will not parse still raises", but revision 5
  wrongly described the other callers as untouched;
- **the frame is documented for water-level thresholds** — expressed in the datum-shifted frame
  the base rule uses, not m a.s.l. (Design sketch §1). No validation can catch a frame error, so
  the reference file must state it;
- **omitting a key is the only way to express "inherit"**;
- a config with no such block, **and a config with no `[onboarding]` section** (making
  `load_onboarding_config()` return `None`, `config/onboarding.py:153-155`), each yield an empty
  list rather than raising.
**Pre-change**: N/A — new capability; nothing to disprove.

### T2 — The resolution boundary

**Outcome**: declared specs become `StationQcOverride` values valid by construction, from one pure
function, with unapplicable specs classified rather than guessed at.
**In**: a named new module `src/sapphire_flow/services/station_qc_overrides.py` holding the
function, and the frozen **`Resolution`** result in `src/sapphire_flow/types/`, following the
repository's placement of domain value types; unit tests. Naming the module matters: two other
tasks import the symbol. **No strictness enum** — see D3.
**Out**: no I/O — registry, rule set and `is_applicable` are parameters, which is what keeps it
testable without a database. **The function never infers applicability from `StationConfig`**;
the caller supplies the predicate. It **never raises** on a resolvable-spec failure. No threading
into any flow (T3). No change to `StationQcOverride`. Each rejection is a frozen
`StationQcRejection` carrying its spec and a tuple of `StationQcRejectionKind` values:
`STATION_NOT_FOUND`, `RULE_NOT_FOUND`, `PARAMETER_MISMATCH`, `CADENCE_MISMATCH`,
`MODE_MISMATCH`, and `MERGED_INVALID`. Retain every applicable reason per spec. T4 may waive
only `STATION_NOT_FOUND` under its explicit network opt-in.
**Verification**: `uv run pytest` on the new module asserting —
- a station code resolves by `(code, network)`, proven by a fixture where two stations share a
  code across different networks — well-defined because `uq_stations_network_code`
  (`db/metadata.py:300`) makes the pair globally unique;
- an unknown station code, an unknown `rule_id`, a parameter mismatch, a cadence mismatch, a mode
  mismatch or invalid merged threshold lands in `Resolution.rejected` with its typed reason kind;
  retain every applicable reason per spec. Validate rule/network/parameter/cadence and merged
  thresholds even if the station lookup fails, so an unregistered station cannot hide a malformed
  rule declaration;
- **an override whose keys select a different mode than the base rule's is rejected** — a
  `max_delta` override against a `tolerance`-mode spike rule silently switches `_apply_spike`
  from proportional to absolute (`services/qc.py:235-250` branches on the **merged** dict), and only
  this layer can see the base rule. Test both directions;
- **merged-threshold validation against the base rule** — a partial `range_check` override
  supplying only `value_min` above the rule's `value_max` is rejected, which only this layer can
  see;
- **both inert classes are classified** — a spec for a station the run will not judge, **and** a
  spec whose rule is skipped for that station (a `range_check` ceiling on a water-level station
  with no datum). Neither is a rejection and neither is a silent success;
- **an override matching two rules differing only by `rule_version` is applied to both and the
  fan-out reported** (D2), against an **inline two-version fixture**, citing
  `scripts/dhm_precip/qc_ruleset.py:67-93` as the motivating case rather than importing it, since
  that path is outside the pyright gate;
- **the 264 composition**: where a network-specific rule replaces a generic one within the same
  key group, the override follows the replacement and is validated against its new base
  thresholds (D2).
**Pre-change**: N/A — new pure function; its behavioural consequence is proven in T3.

### T3 — Thread into the scheduled ingest flow

**Outcome**: the operational ingest path applies declared thresholds where cadence and station
applicability permit, reports pending and rejected declarations distinctly, and never halts on a
resolvable-spec failure.
**In**: `src/sapphire_flow/flows/ingest_observations.py` — load specs from
`config/onboarding.py`, resolve once at flow level against the registry already held, and pass the
resolved overrides to `_run_qc_task`. Extract the existing judged-station predicate into a named
pure helper in `services/` so T4 imports the same predicate. Applicability receives station,
rule ID and parameter and combines the judged predicate with `obs_skipped_rules(parameter, datum)`.
Add pending, rejected and not-applicable counts to `IngestResult`; structured INFO logs for
pending states; and a warning health record for rejected/not-applicable states. Update
`src/sapphire_flow/types/enums.py` and its mirror in `docs/spec/types-and-protocols.md`. The
`_run_qc_task` override parameter has a default because tests call its Prefect `.fn` directly.
**Out**: no change to the checker or Protocol signature established by Plan 264, forecast call
sites, `scripts/dhm_precip/`, or onboarding QC. Preserve and pass through the existing
`station_networks` mapping. No watchdog probe.
**Verification**: `uv run pytest tests/unit/flows/test_ingest_observations.py` and the full suite,
asserting:

- in one run, a station with a declared ceiling uses it while one without a declaration uses the
  base threshold;
- when the target tenant has no stations on a network explicitly named in
  `onboarding.qc_pending_networks`, station-not-found specs are reported as pending INFO, survive early returns in `IngestResult`, and create no warning health
  record; an empty network absent from that list remains a WARNING rejection;
- when a network has registered stations but a declaration names an unknown code, it is a WARNING
  rejection (so a typo cannot be hidden by a partly onboarded network); assert its warning health
  record and rejection count also survive an early return;
- a matching station with `station_status = onboarding` is pending INFO; this is Plan 268's six
  gauges;
- a matching station outside the judged set for another reason, or a rule skipped for its
  parameter (such as datum-less water-level `range_check`), is WARNING/not-applicable;
- resolution and diagnostics occur before no-eligible early returns and resolution runs once per
  flow;
- malformed/duplicate declarations still raise at parsing, while resolvable rejections do not
  stop other stations;
- unset `SAPPHIRE_CONFIG` and a config without `[onboarding]` continue to produce zero overrides
  as currently specified.

**Pre-change**: a RED test proves a correctly resolved declaration currently has no effect because
the call site supplies an empty override list.

### T4 — Edit-time configuration validation

**Outcome**: an operator can validate a declaration against the registry and rule set without
writing data, while an explicitly unonboarded network can be prepared safely.
**In**: a `--validate-config` branch in `scripts/onboard.py`, returned before download, migration,
onboarding or any write. It loads the explicit config, fetches the registry read-only, resolves
through T2, prints every typed rejection and every not-applicable spec, and exits non-zero iff a
fatal rejection remains. Add a repeatable `--allow-unonboarded-network NETWORK` option. Network
tokens are case-sensitive and must appear in the declared specs; an option naming no declared
network is an error. It waives `STATION_NOT_FOUND` only if the target tenant has zero registered
stations on that network. If any station on the network exists, an unknown code is fatal. A found
station in `onboarding` is reported pending INFO by T3, not waived by this option. T4 imports T3's
judged-predicate helper; it must not reimplement it.
**Out**: no download, migration, onboarding, store write, QC run, or change to resolver policy.
**Verification**: `uv run pytest tests/unit/scripts/` asserting:

- unknown station is fatal by default and all reasons are printed;
- an existing but `onboarding` station is reported pending INFO without being treated as an
  unknown code;
- `--allow-unonboarded-network dhm` allows missing DHM stations only when the target tenant has
  no DHM stations; with any DHM station registered, a different DHM code remains fatal;
- the option can be repeated, is case-sensitive, and rejects an undeclared network;
- with no DHM stations, a DHM spec containing an invalid rule still exits non-zero and reports
  both `STATION_NOT_FOUND` and `RULE_NOT_FOUND`; the same independent checks cover invalid
  parameter, cadence and merged thresholds;
- a registered station with invalid rule/parameter/cadence or merged thresholds is fatal;
- a clean config exits zero with no rejection or inert output;
- no migration, download, onboarding call or store write occurs for any validator path.

**Pre-change**: N/A — new command.

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
**Pre-change**: N/A — this task *is* the pre-change evidence. Its baseline is captured and
committed **before T3 lands**, as a test phase 4 must keep green; the oracle cannot be generated
after the change it exists to detect.

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
  reader does not mistake it for an oversight — and Plan 268 T7 applies its full-series imported observations independently of station
  promotion, while the scheduled path reports the configured pending network and onboarding
  stations as INFO and cannot infer daily cadence from one new daily reading;
- **the sibling sweep is by meaning:** update Plan 268 T7 to use the validated configuration and
  resolver boundary after the interface is merged, then verify persisted QC outcomes for the
  delivery-tagged cohort and preserve unrelated rows;
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
    { "id": "phase-3", "tasks": ["T5"], "depends_on": ["phase-1"] },
    { "id": "phase-4", "tasks": ["T3", "T4"], "depends_on": ["phase-2", "phase-3"], "parallel": true },
    { "id": "phase-5", "tasks": ["T6"], "depends_on": ["phase-4"] }
  ]
}
```

T5 sits **before** T3: its baseline is pre-change evidence and cannot be captured after the change
it exists to detect. It consumes nothing from T2 — round 5 noted the graph asserted a dependency
the text contradicts — so it is gated only on being captured before phase 4. T3 and T4 consume T2 and T5; they are independent of each other.

**Cross-plan sequencing.**

- **Plan 264 landed in PR #315.** `QcRuleSet.rules_for(..., *, network=...)` selects a matching
  network-specific rule in preference to a generic rule of the same `(rule_id, parameter,
  time_step)`; generic rules remain available where that network has no specific rule. Scheduled
  ingest supplies the station→network mapping to both `check` and `resolve_selection`. T2's rule
  lookup and validation must use the spec's network and the selected rule set.
- **Plan 264's Swiss selector-equivalence fixture is already committed** at
  `tests/fixtures/qc/swiss_daily_discharge_v1.json`. It protects network selection. Plan 269 T5
  remains a separate pre-T3 flow baseline proving that declaring no station overrides leaves
  current QC output unchanged.
- **🔑 How Plan 268 gets its ceilings, stated plainly, because the descope makes it look
  otherwise.** The `blocks: [268]` edge is **still real, through T1 and T2 only**. T3 and T4
  deliver nothing to 268: its six gauges are `station_status = onboarding`, so the ingest gate
  never reaches them. **268 T7 runs its own QC pass from its own import CLI**
  (`src/sapphire_flow/cli/import_dhm_delivery.py`, which does not exist yet) and 268 explicitly
  claims ownership of wiring it. What this plan supplies is the declaration surface (T1) and the
  resolver (T2); T7 supplies its own judged set and calls them. A reader could otherwise conclude
  the descope hollowed the edge out — it did not.
  T7 runs its own full-series QC pass after import, independent of forecast targets and
  operational promotion. The scheduled path reports configured empty networks and onboarding
  stations as pending INFO; one new daily reading still has no inferred cadence in the measured
  QC context window.

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
- **Rule selection.** Plan 264 owns the network dimension and per-flag configured rule versions.
  Scheduled zero-rule rows follow Plan 272's `QC_UNCHECKED` contract; this plan does not change
  that status policy.
- **Reviving or dropping `forecast_qc_overrides`** (D1) — neither; document it.
- **An API or dashboard surface** (D5) — arrives with the v1 migration.
- **Changing overlay array-merge semantics.** Plan 264 D4 keeps network QC rules in the shared
  base config; T1 rejects this plan's threshold key in overlays so a partial array cannot erase
  the base declarations.
- **A writer for `station_thresholds`.**
- **Any further change to `Stage1QualityChecker.check` or the `QualityChecker` Protocol.** Plan 264
  delivered the network mapping; this plan consumes that interface without changing it.
