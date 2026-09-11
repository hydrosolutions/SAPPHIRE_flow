---
status: DRAFT
created: 2026-09-11
revised: 2026-09-11
plan: 269
title: Per-station QC thresholds declared in onboarding configuration
scope: Deliver per-station observation QC threshold overrides through station onboarding configuration — a validated TOML surface, a pure resolution boundary that turns declared specs into domain overrides, and threading into the two live observation-QC call sites — so a per-station ceiling can be declared at onboarding and corrected by editing configuration. NOT a database table, store or migration (the spec defers that to v1), NOT the DHM threshold values themselves (Plan 268 D14), NOT forecast QC overrides, NOT rule selection (Plan 264), NOT an API surface, NOT a new rule kind.
blocks: [268]
related: [264, 268, 012]
reviews:
  - "codex 2026-09-11 — NOT READY, 5 blockers + 5 majors + 2 minors; killed the write path and the migration"
  - "claude 2026-09-11 — NOT READY, 3 blockers + 8 majors + 5 minors; same central defect found independently"
open_decisions: []
source: 2026-09-11 — the owner's 2026-09-10 decision on Plan 268 D14 (per-station QC ceilings live in station onboarding configuration, updatable later) cannot be delivered on the current code. Plan 264 explicitly excludes the persisted-row tier and Plan 268 T7 would have built six thresholds in memory that nobody could change without editing code. Rewritten 2026-09-11 after two independent reviews: the database tier is dropped to match `docs/spec/types-and-protocols.md`, which stages this surface as onboarding TOML now and a DB migration at v1.
---

# Plan 269 — per-station QC thresholds declared in onboarding configuration

## Status

**DRAFT — NOT READY. Materially rewritten 2026-09-11; the rewrite is UNREVIEWED.**

Two independent reviews ran against the first revision (Codex: 5 blockers, 5 majors, 2 minors;
Claude: 3 blockers, 8 majors, 5 minors) and both returned NOT READY. Both found the same central
defect independently: **no task in the plan ever wrote a row**, so the capability the plan
existed to deliver was unbuilt. The owner then settled the scope question the reviews exposed —
**configuration now, database at v1** — and this revision is built on that answer, not patched
onto the old shape. It needs a fresh complete round before READY.

### What the reviews changed

- **The database tier is gone.** `docs/spec/types-and-protocols.md` § StationQcOverride already
  states the intended staging: *"Loaded from station onboarding TOML; v1 migrates to DB
  (dashboard-editable)."* The first revision built the table immediately, which is where most of
  the blocker weight sat — the migration, the production store registration in
  `flows/_db.py`, and the per-table write grant in `docker/bootstrap-roles.sql` that
  `docs/conventions.md` requires and that no task owned. Dropping to configuration removes all
  four rather than fixing them.
- **The first revision mirrored the wrong table.** It proposed a column-for-column copy of
  `forecast_qc_overrides`, which is the one comparable table in the repository with **no primary
  key and no `created_at`/`updated_at`**, while `clim_baselines` and `station_thresholds` both
  carry a natural-key `PrimaryKeyConstraint` plus timestamps. Moot now, but recorded so the v1
  migration does not repeat it: **`station_thresholds` is the schema model, not
  `forecast_qc_overrides`.**
- **The stale-ceiling defect dissolves.** A review found that upsert-only persistence leaves a
  deleted or re-keyed threshold silently in force — and for an impossibility ceiling on flood
  peaks, a stale low ceiling condemns the genuine monsoon record. Configuration is re-read every
  run and is the single source of truth, so removing a block removes the ceiling on the next
  run. This is a real advantage of the chosen shape, not merely a smaller scope.
- **Validation moved to the layer that can perform it.** The first revision put "reject an
  unknown station or rule" in `config/onboarding.py`, which is a pure TOML→dataclass parse with
  no station registry and no rule set. T2 now owns that check at the boundary where both exist.
- **Line references are re-derived at `75cf80cf`.** Both reviews found drift: PR #270 (Plan 261)
  merged mid-review and shifted `run_forecast_cycle.py` by 23 lines and `protocols/stores.py` by
  16. It touched nothing this plan depends on — verified by diffing the QC and override seam
  across the merge — but this revision prefers symbol names to line numbers wherever a symbol
  is unambiguous, because the drift will happen again.

## Problem

The owner settled where per-station QC thresholds live: **station onboarding configuration,
updatable later** (Plan 268 D14, 2026-09-10). The authoritative spec says the same thing and has
said it all along. None of it is built.

Measured at `75cf80cf`:

- **`StationQcOverride` is a bare dataclass.** `types/domain.py:171-176` — five fields
  (`station_id`, `rule_id`, `parameter`, `time_step`, `thresholds`), frozen. The **type** is
  documented (`docs/spec/types-and-protocols.md` § StationQcOverride, and the `overrides`
  parameter on the `QualityChecker` Protocol); the **supply** is absent. There is no
  configuration key, no parser, no resolution step and no loader anywhere in `src/`.
- **Both live observation-QC callers hard-code an empty list.** `flows/ingest_observations.py`
  inside `_run_qc_task`, and `services/onboarding.py` inside `_run_onboarding`, each pass
  `overrides=[]`. There is no code path by which a declared threshold can reach the checker.
- **The consumption end is already built and correct.** `services/_qc_helpers.merge_thresholds`
  merges an override onto a rule's base thresholds keyed on
  `(station_id, rule_id, parameter, time_step)`, skipping `None` values so a partial override
  leaves the rest of the rule intact. `services/qc.py` calls it for every rule.
  **Only the supply end is missing.**

So a per-station ceiling today can only be constructed in memory by whichever process wants one
— which is what Plan 268 T7 was scoped to do, and what the owner's decision rules out. An
in-memory threshold cannot be onboarded and cannot be corrected without editing and redeploying
code.

### Two dead tiers beside this one, recorded as a warning

`forecast_qc_overrides` (`db/metadata.py:1336-1353`, migration `0012`, 2026-03-24) is a real
table with the columns a persisted version of this would need, backing
`StationForecastQcOverride` — a dataclass structurally identical to `StationQcOverride`.
**Nothing reads or writes it.** All six forecast call sites in `flows/run_forecast_cycle.py`
hard-code `qc_overrides=[]` (`:2837, :2965, :3149, :3221, :3295, :3503`). Archived Plan 012
specified the loader — "batch pre-fetched from `forecast_qc_overrides` table at flow start" —
and shipped only the schema.

`station_thresholds` (alert danger levels — a different concern; Plan 268 D14 says so
explicitly) is *read* from five live call sites through `StationStore.fetch_thresholds`
(`protocols/stores.py:623`), while its writer `store_thresholds` (`:626`) is **called from
nowhere in `src/`**.

Both are the same failure: schema and type shipped, supply never built, and the capability
structurally impossible ever since. **This plan is not done when the parser lands** — T3 and T4
are the tasks that make it real, and they are the reason the graph is shaped the way it is.

## What this does not change

**With no threshold blocks declared, QC output is unchanged.** `merge_thresholds` over an empty
list returns `dict(base_thresholds)` unchanged, and every existing deployment declares no blocks.
T5 asserts this **at the flow level**, not by comparing a service call to itself — a review
correctly found that the first revision's equivalence test would have passed against any
implementation, including one that never loaded overrides and one that applied them to every
station. The merge's purity is evidence about the merge, not about the threading.

**The checker signature does not change.** `Stage1QualityChecker.check` already takes
`overrides` as its third parameter and the `QualityChecker` Protocol
(`protocols/stores.py:1028`) already declares it. This plan changes what callers *pass*. The
positional callers in `scripts/dhm_precip/` (`qc_mask.py`, reached from
`build_dudh_koshi_handover.py`) are therefore untouched — but see T3/T4 `Out`, because the
*enclosing* functions do change signature and that is where the real call-site risk lives.

## Design sketch

Four pieces, no persistence:

1. **A configuration surface.** `[[onboarding.station_qc_thresholds]]` blocks parsed the way
   `[[onboarding.calculated]]` already is (`config/onboarding.py`): a pydantic boundary model
   validating the raw block, converted to a frozen `StationQcThresholdSpec`. A spec names a
   station by **code** plus **network** (not a `StationId`, which configuration cannot know),
   a `rule_id`, a `parameter`, a `time_step` and the threshold values.
2. **A pure resolution boundary.** `resolve_station_qc_overrides(specs, stations, rule_set)
   -> list[StationQcOverride]` resolves each spec's station code to a `StationId`, checks the
   named rule exists in the resolved rule set for that parameter and cadence, and raises on any
   failure. This is the parse-don't-validate step: everything downstream receives overrides that
   are valid by construction. It is pure — station registry and rule set are passed in — so it
   is testable without a database.
3. **A loader mirroring the rule-set loader.** `_load_qc_rules()` already reads the rule set
   from config at flow level in `flows/ingest_observations.py`; a sibling loader reads the
   threshold specs the same way, from the same file, with the same fallback behaviour.
4. **Threading at the two live call sites.** The resolved overrides replace `overrides=[]`.
   Both enclosing functions gain a **keyword parameter with a default**, so no existing caller
   breaks.

Reusing `StationQcOverride` unchanged is deliberate: it is rule-keyed, which is what
`merge_thresholds` already indexes on. The dataclass is right; only its supply was missing.

**Why configuration rather than a table, stated once.** The spec stages it that way; it removes
the migration, the production store registration and the database grant from this plan entirely;
and it makes removal correct by construction. The cost is honest and worth writing down: there
is **no audit trail** of when a ceiling changed — configuration history lives in git, not in the
database — and a correction requires re-running onboarding or the ingest flow rather than
editing a row. When the dashboard needs editable thresholds, the v1 migration follows
`station_thresholds` (natural-key primary key plus `created_at`/`updated_at`), and this plan's
resolution boundary is the seam it plugs into.

## Locked design

### D1 — Does forecast QC get the same surface? **CLOSED: no.**
`ForecastQcRuleSet` and `StationForecastQcOverride` have the identical shape and the identical
gap, and `forecast_qc_overrides` even has the table. Leave it. There is no second-network
pressure on forecast QC (the reasoning Plan 264 D1 applied), and widening this plan into the
forecast cycle for a problem nobody has is a bad trade. **Dropping** the dead table is equally
out of scope — a deletion needs its own rollback rehearsal. T6 records it as dead instead.

### D2 — How this composes with Plan 264's network dimension. **CLOSED, and restated.**
The first revision said "station beats network beats generic", which a review correctly called a
category error: an override cannot suppress a rule. The two mechanisms are **orthogonal stages**,
not a lattice:

> **Plan 264 selects *which rule* applies. This plan adjusts *that rule's thresholds*.**

Two consequences that must be stated because neither is obvious:

- **An override has no `rule_version`** (`types/domain.py:171-176`), and `merge_thresholds` has
  no `break`. So an override whose four-part key matches **more than one resolved rule applies
  to all of them.** Plan 264 T1 deliberately keeps `rule_version` in its uniqueness key so that
  two rules differing only by version stay constructible — `scripts/dhm_precip/qc_ruleset.py`
  ships exactly that pair and `tests/unit/scripts/test_dhm_precip_ruleset.py` asserts both are
  returned. T2 tests an override against that rule set.
- **Before Plan 264 lands**, "the resolved rule set" is the flat parameter/cadence lookup; after
  it, it is network-resolved. T2's validation is written against whatever `rules_for` returns,
  so it is correct in both worlds and needs no change when 264 lands.

### D3 — A configured threshold naming an unknown station, rule or parameter. **CLOSED: fail hard.**
A typo yields a threshold that silently never merges, and the resulting QC looks entirely
plausible while running the ungoverned base threshold — the fail-open-by-omission failure Plan
264 D3 and T3 both designed out. The check is **not** in the config parser, which has no
registry and no rule set; it is in T2's resolution boundary, which has both. One bad block
refuses the whole run: that is the intended cost, and it is loud rather than silent.

### D4 — An override attached to a rule that resolves to nothing. **CLOSED by T2, not by deferral.**
The first revision deferred this to Plan 264 T3, and both reviews found that reasoning false:
264 raises only when a **non-empty rule set resolves nothing for a series**, so an override with
a mismatched cadence stays inert whenever any *other* rule resolves. T2's resolution check
closes it here and independently — a spec whose `(rule_id, parameter, time_step)` matches no
rule in the resolved set raises at load, before any observation is judged. This plan therefore
does **not** depend on Plan 264.

### D5 — An API surface for reading or editing thresholds. **CLOSED: not in this plan.**
There is no stored row to serve, so the question is v1's, arriving with the migration. The first
revision justified this with Plan 268 D14's restricted-value constraint; a review correctly
found that reason rests on a basis 268's own round 2 **rejected**. D14's recommendation is a
published flood-envelope curve from catchment area, characterised there as *"not a leak"*, with
the rating-table envelope explicitly *"not the basis"*. **Stated correctly: if D14 settles on a
table-derived ceiling, an API surface widens exposure and the constraint applies; if it settles
on the flood-envelope basis, there is no restricted value and scope discipline is the only
reason.** D14 is still open, so v1 must revisit this rather than inherit a decision.

## Tasks

### T1 — The configuration surface

**Outcome**: a per-station QC threshold can be declared in onboarding configuration and is
validated as far as a parser without a registry can validate it.
**In**: `src/sapphire_flow/config/onboarding.py`; `docs/spec/config-reference.toml`; unit tests.
**Out**: no station, rule or parameter *existence* checking — that needs the registry and the
rule set, and is T2's (a review found the first revision put it here, where it cannot work). No
threshold *values* for any station: this task ships the surface empty, and Plan 268 D14 owns what
goes in it. No change to any existing `[onboarding.*]` block.
**Verification**: `uv run pytest tests/unit/config/test_onboarding_qc_thresholds.py` asserting a
valid block parses to the expected frozen spec; and that each of these is rejected with a message
naming the offending key — a non-numeric or non-finite threshold value, an unknown field, a
non-positive `time_step`, a missing station code or network, and **a threshold key that does not
belong to the named rule**. That last case matters and the first revision missed it:
`docs/spec/types-and-protocols.md` § "Threshold keys by rule" is authoritative
(`range_check` → `value_min`/`value_max`; `rate_of_change` → `max_rate`; `frozen_sensor` →
`tolerance`/`min_consecutive`/`exclude_at_or_below`; `spike` → `tolerance`;
`gross_outlier` → `k_sigma`), and `merge_thresholds` copies **any** key it is given, so
`value_maximum` would be carried all the way to the checker and silently ignored. A config with
no such block yields an empty list and is unchanged behaviour.
**Pre-change**: N/A — new capability; nothing to disprove. (The first revision proposed proving
"a block is parsed by nothing", which reduces to a missing-key assertion and is not
discriminating evidence.)

### T2 — Resolution and validation at the boundary

**Outcome**: declared specs become `StationQcOverride` values that are valid by construction, or
the run refuses to start.
**In**: a new pure function in `src/sapphire_flow/services/` taking the parsed specs, the
station registry and the resolved `QcRuleSet`, returning `list[StationQcOverride]`; unit tests.
**Out**: no I/O — the registry and rule set are parameters, not fetched (this is what keeps the
function testable without a database and is why it is not in a store). No threading into any
flow (T3/T4). No change to `StationQcOverride`.
**Verification**: `uv run pytest` on the new test module asserting that a spec naming an unknown
station code, an unknown `rule_id`, or a `(rule_id, parameter, time_step)` triple matching no
rule in the resolved set **raises**, with the offending spec named (D3, D4); that a station code
is resolved to the right `StationId` where two stations share a code across **different
networks**, which is why a spec carries a network; and that **an override whose key matches two
rules differing only by `rule_version` is returned once and applies to both** — asserted against
the two-version rule set in `scripts/dhm_precip/qc_ruleset.py`, per D2.
**Pre-change**: N/A — new pure function. Its behavioural consequence is proven in T3.

### T3 — Thread into the observation-ingest flow

**Outcome**: the operational ingest path judges a station against its declared ceiling.
**In**: `src/sapphire_flow/flows/ingest_observations.py` — a threshold-spec loader beside
`_load_qc_rules`, resolution once at flow level using the stations the flow **already fetches**
via `station_store.fetch_all_stations()`, a new keyword parameter on `_run_qc_task` beside
`qc_rules`, and the `overrides=[]` it passes to the checker; unit tests.
**Out**: the checker signature is unchanged — this task edits arguments, not the checker's
parameters. The forecast call sites keep `qc_overrides=[]` (D1). `scripts/dhm_precip/` is not
touched. **`_run_qc_task`'s new parameter must carry a default**, because its caller passes the
first four arguments positionally; and no new store is introduced — the station registry is
already in hand at flow level, which is what makes the config-only shape work without one.
**Verification**: `uv run pytest tests/unit/flows/test_ingest_observations.py` asserting that,
**in one run**, a station with a declared ceiling is judged against it while a station without
one is judged against the rule's base threshold — the discriminating case, since an
implementation that loaded specs but applied them to every station passes a single-station test.
Resolution happens **once per flow run**, not per station-parameter group: asserted with a call
counter on the loader, because `_run_qc_task` is invoked per `(station_id, parameter)` pair and
a fetch placed inside it would satisfy every other assertion here. `uv run pytest` passes whole.
**Pre-change**: a RED test proving that today a **declared, correctly-resolved** override has no
effect on the ingest flow's QC result, because the call site passes `[]` regardless. This fails
on the assertion — the flagged/unflagged status of a specific observation — not on a missing
symbol, which is what makes it evidence of the defect rather than of the absence of the feature.

### T4 — Thread into the onboarding service

**Outcome**: onboarding judges a station's backfilled history against its declared ceiling, on
the same overrides the ingest flow uses.
**In**: `src/sapphire_flow/services/onboarding.py` — the `overrides=[]` inside `_run_onboarding`,
resolved from `station_by_id`, which the function already builds; and the parameter threading
from `onboard_from_camelsch` and `flows/onboard.py`; unit tests.
**Out**: no change to the checker or to any rule. **The new parameter on `_run_onboarding` must
be keyword-only with a default**: a review enumerated **eight existing call sites** across
`tests/unit/services/` and `tests/integration/`, and a required positional parameter breaks
every one. This is the same class of defect Plan 264's call-site audit got wrong twice — assume
the audit is incomplete and let `uv run pytest` prove it.
**Verification**: an onboarding-service test proving a declared override changes the QC outcome
for a backfilled series — the first revision verified only the ingest path and would have passed
with this caller still hard-coded to `[]`. `uv run pytest` passes whole.
**Pre-change**: a RED test proving that today a declared override has no effect during
onboarding, failing on the QC outcome rather than on a signature.

### T5 — Zero-configuration equivalence

**Outcome**: proof that a deployment declaring no thresholds is unaffected.
**In**: `tests/unit/flows/test_ingest_observations.py` — **flow level**, deliberately.
**Out**: not a new QC behaviour; a regression bar only.
**Verification**: a fixed observation series run through the ingest flow with **no threshold
blocks declared** yields flags, statuses and `detail` text identical to the pre-T3 result. The
rule set is the one **loaded from `config.toml`** — what actually runs — not
`_default_swiss_qc_rules()`, which `load_qc_rules` never returns while a `[qc_rules]` section
exists. Thresholds are not a returned field; they appear only inside `QcFlag.detail`, so the
comparison is over `detail` text.
**Pre-change**: the expected flags are captured **before T3 lands** and committed as the
baseline. This is not the first revision's symmetric self-comparison, which a review found would
pass against any implementation: the oracle must predate the change it exists to detect.

### T6 — Documentation

**Outcome**: the surface is documented, its v1 successor is specified, and the dead tiers beside
it are recorded as dead rather than left to be mistaken for working code.
**In**: `docs/spec/types-and-protocols.md` (§ StationQcOverride already promises this surface —
it now describes one that exists, and states the D2 composition rule); `docs/spec/config-reference.toml`;
`docs/touchpoint-maps.md`; `docs/v0-scope.md`, which lists `forecast_qc_overrides` without noting
nothing reads it.
**Out**: no code change.
**Verification**: bounded inspection — the D2 composition rule (264 selects the rule, this plan
adjusts its thresholds; an override applies to every resolved rule sharing its key) is stated
once and in one place; `forecast_qc_overrides` is recorded as schema-only with its six
hard-coded call sites; `StationStore.store_thresholds` is recorded as having no caller; the v1
migration is specified as following `station_thresholds` (natural-key primary key plus
timestamps) and **not** `forecast_qc_overrides`; and the absence of an audit trail under the
configuration shape is stated as a known, accepted cost.
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

T5 sits **before** T3/T4, not after: its baseline is pre-change evidence and cannot be captured
after the change it exists to detect. T3 and T4 are parallel but neither is optional — a review
found the first revision verified only one of the two live paths, and the other would have
stayed hard-coded to an empty list while every stated gate passed.

**Cross-plan sequencing.**

- **This plan does not depend on Plan 264** (D4). T2 validates an override against whatever
  `rules_for` returns, which is correct before and after the network dimension lands.
- **Plan 264 T4's golden fixture must still be captured before any DHM rule reaches
  `config.toml`.** This plan does not change QC output with nothing declared (T5), so it does not
  threaten that fixture — but nor is it a reason to skip capturing it.
- **Plan 268 T7 must be revised before it is READY, and this plan does not do it.** Both reviews
  found the dependency edge vacuous: 268 T7's `In` still requires "six in-process
  `StationQcOverride` objects", its gate proves the merge by calling `merge_thresholds` directly
  (which passes identically for in-memory objects), and its phase commentary names only Plan 264.
  268 T7 also runs QC from `src/sapphire_flow/cli/import_dhm_delivery.py`, **a third call site
  that does not exist yet** and which this plan's T3/T4 do not wire. **Required of 268 before its
  T7 is READY**: declare the six ceilings as configuration blocks, resolve them through T2, and
  state that the import CLI may not construct overrides in memory. D14 still owns the values.

## Explicitly out of scope

- **A database table, store, migration, Protocol store method, production store registration or
  database grant.** Deferred to v1 with the spec. When it comes, the schema model is
  `station_thresholds`, not `forecast_qc_overrides`.
- **The DHM threshold values.** Plan 268 D14 owns them, including the basis (a published
  flood-envelope curve from catchment area is the current recommendation) and the coefficient,
  which its own text says the hydrologist sets.
- **Rule selection.** Plan 264 owns the network dimension, the fail-closed policy and the
  flag-version correction.
- **Reviving or dropping `forecast_qc_overrides`** (D1) — neither; document it.
- **An API or dashboard surface** (D5) — arrives with the v1 migration.
- **A writer for `station_thresholds`.** A different concern with its own missing writer;
  recorded in T6, not fixed here.
- **Any change to `Stage1QualityChecker.check` or the `QualityChecker` Protocol.** Already correct.
