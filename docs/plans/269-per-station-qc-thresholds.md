---
status: DRAFT
created: 2026-09-11
plan: 269
title: Per-station QC thresholds that can be onboarded and corrected
scope: Build the missing persistence tier for per-station observation QC threshold overrides — table, store, Protocol, fake, onboarding configuration surface, and a loader wired into the two live observation-QC call sites — so a per-station ceiling can be declared at onboarding and corrected later without a code change. NOT the DHM threshold values themselves (Plan 268 D14), NOT forecast QC overrides, NOT rule selection (Plan 264), NOT an API surface for reading or editing thresholds, NOT a new rule kind.
blocks: [268]
related: [264, 268, 012]
open_decisions: [D1, D2, D3, D5]
source: 2026-09-11 — the owner's 2026-09-10 decision on Plan 268 D14 (per-station QC ceilings live in station onboarding configuration, updatable later) cannot be delivered on the current code. Plan 264 explicitly excludes the persisted-row tier and Plan 268 T7 would have built six thresholds in memory that nobody could change without editing code. Neither plan owns the gap; this one does.
---

# Plan 269 — per-station QC thresholds that can be onboarded and corrected

## Status

**DRAFT — NOT READY. No independent review has run.** This plan adds a migration, a store, a
Protocol and a configuration surface, and it wires them into the live observation-ingest path,
so before the owner may set it READY it needs the ordinary independent Claude and Codex plan
reviews (`docs/workflow.md` § Multi-Model Review).

Split out of Plans 264 and 268 deliberately, because neither can absorb it. Plan 264 is about
rule *selection* and states the persisted-row tier as out of scope. Plan 268 has already
survived five review rounds; growing it by a migration, a store and a config surface would
force a fresh complete round on a plan whose remaining open decision (D14) is a hydrological
question, not a software one.

## Problem

The owner settled where per-station QC thresholds live: **station onboarding configuration,
updatable later** (Plan 268 D14, 2026-09-10). None of that exists.

Measured across `src/`, `alembic/`, `config.toml` and `docs/spec/`:

- **`StationQcOverride` is a bare dataclass.** `types/domain.py:170-176` — five fields
  (`station_id`, `rule_id`, `parameter`, `time_step`, `thresholds`), frozen, with no table,
  no store, no Protocol method and no configuration key anywhere in the repository.
- **Both live observation-QC callers hard-code an empty list.**
  `flows/ingest_observations.py:308` and `services/onboarding.py:799` each pass
  `overrides=[]`. There is no code path by which a per-station threshold can reach the checker.
- **The consumption end is already built and correct.** `services/_qc_helpers.merge_thresholds`
  merges an override onto a rule's base thresholds keyed on
  `(station_id, rule_id, parameter, time_step)`, skipping `None` values so a partial override
  leaves the rest of the rule intact. `services/qc.py:258` calls it for every rule.
  **Only the supply end is missing.**

So a per-station ceiling today can be constructed in memory by whichever process wants one and
passed straight to the checker — which is precisely what Plan 268 T7 was scoped to do, and what
the owner's decision rules out. An in-memory threshold cannot be onboarded, cannot be persisted,
and cannot be corrected without editing and redeploying code.

### The same tier exists one seam over, and it is dead

`forecast_qc_overrides` (`db/metadata.py:1336-1353`, migration `0012`) is a real table with
exactly the columns this plan needs — `station_id` FK, `rule_id`, `parameter`,
`time_step_seconds`, `thresholds` JSONB, and a natural-key unique constraint — backing
`StationForecastQcOverride` (`types/domain.py:271-276`), a dataclass structurally identical to
`StationQcOverride`.

**Nothing reads or writes it.** Archived Plan 012 specified the loader — "batch pre-fetched
from `forecast_qc_overrides` table at flow start" (`archive/012:339`) — and it was never built.
All six forecast call sites hard-code `qc_overrides=[]`
(`flows/run_forecast_cycle.py:2814, 2942, 3126, 3198, 3272, 3480`). The table has existed
unpopulated and unread since migration 0012.

This is worth stating plainly for two reasons. It gives this plan a **schema precedent to
mirror rather than invent** — column types, the `time_step_seconds` integer encoding, the
natural key. And it is the warning: a table plus a dataclass is not a tier. Plan 012 shipped
both and stopped before the store and the loader, and every forecast run since 2026-03-24 has
executed with per-station overrides structurally impossible. This plan is not done when the migration lands.

**A third variant shows the shape working end-to-end.** `station_thresholds` — alert danger
levels, a different concern (Plan 268 D14 says so explicitly) — is per-station, per-parameter,
carries a `source: ThresholdSource` discriminator, and is *read* from five live call sites
(`api/routes/api_stations.py:201`, `flows/compute_skills.py:206,475`,
`flows/run_forecast_cycle.py:2422`, `services/observation_alert_checker.py:41`) through
`StationStore.fetch_thresholds` (`protocols/stores.py:607`). Its writer
`store_thresholds` (`:610`) is **called from nowhere in `src/`**, which is why the deployment
carries zero threshold rows. The read wiring is the model to copy; the missing writer is the
second warning.

## What this does not change

**With no override rows configured, QC output is byte-identical by construction.**
`merge_thresholds` over an empty list returns `dict(base_thresholds)` unchanged. Every existing
deployment has zero rows on the day the migration lands, so the acceptance bar is not a hope
about careful implementation — it is a property of the merge that already ships. T5 asserts it
anyway.

**The checker signature does not change.** `Stage1QualityChecker.check` already takes
`overrides` as its third parameter and the `QualityChecker` Protocol already declares it
(`protocols/stores.py:1017`). This plan changes what callers *pass*, not what the checker
*accepts*. That is the material difference from Plan 264 T2, and it means the positional
callers in `scripts/dhm_precip/` (`qc_mask.py:203,208`, reached from
`build_dudh_koshi_handover.py:175`) — the ones round 2 of Plan 264 found breaking — are
untouched here. Keep it that way: if an implementation finds itself editing the signature, the
design has drifted.

## Design sketch

Four pieces, mirroring `forecast_qc_overrides` for schema and `station_thresholds` for wiring:

1. **Table `station_qc_overrides`** — a column-for-column mirror of `forecast_qc_overrides`,
   natural key `(station_id, rule_id, parameter, time_step_seconds)`, additive-only migration
   `0056`.
2. **`PgStationQcOverrideStore`** — `fetch_overrides(station_id)`, `store_overrides(list)` as
   an upsert on the natural key, `delete_overrides(station_id, parameter)`; a `Protocol` in
   `protocols/stores.py` and a fake in `tests/fakes/fake_stores.py`, following
   `PgClimBaselineStore` / `FakeClimBaselineStore` exactly.
3. **Configuration surface** — a `[[onboarding.station_qc_thresholds]]` TOML block parsed the
   way `[[onboarding.calculated]]` already is (`config/onboarding.py`): a pydantic boundary
   model validating the raw block, converted to a frozen spec dataclass, resolved to
   `StationQcOverride` rows at the onboarding boundary. **Idempotent upsert on the natural
   key** is what makes "updatable later" true: correcting a ceiling is editing the config and
   re-running onboarding, not a migration and not a code change.
4. **The loader** — both live call sites fetch the station's overrides and pass them instead of
   `[]`. `flows/ingest_observations.py` already fetches per-station baselines at `:297`, eleven lines
   above the `overrides=[]` at `:308`; the override fetch belongs beside it, same lifetime.

Reusing `StationQcOverride` unchanged is deliberate. It is rule-keyed, which is what
`merge_thresholds` already indexes on, and a parameter-keyed shape like `StationThreshold`
would need a translation layer at the merge to answer "which rule does this ceiling belong
to". The dataclass is right; only its supply was missing.

## Owner decisions

**D1 — Does this plan also revive the dead `forecast_qc_overrides` tier? NEW, open.**
The table has been unread and unpopulated since migration 0012 and its six call sites hard-code
`[]`. Building the observation tier beside it leaves two structurally identical mechanisms, one
live and one dead, which is how the next reader ends up wiring the wrong one.
*Recommendation: observation-only, and write the forecast table's dead status into the
touchpoint map rather than acting on it.* There is no second-network pressure on forecast QC
(the same reasoning Plan 264 D1 applied), reviving it doubles this plan's blast radius into the
forecast cycle for a problem nobody has, and **dropping** it would need its own rollback
rehearsal — the trap Plan 248 spent three review rounds in. Leave it, name it, revisit when a
forecast path actually needs it.

**D2 — Precedence between a per-station override and Plan 264's network dimension. NEW, open,
and it is a genuine seam.** After Plan 264 lands, a rule resolves by network
(most-specific-wins); this plan then merges a station's override onto whichever rule won. The
two mechanisms compose in one order only, and neither plan currently states it.
*Recommendation: station beats network beats generic, stated once in
`docs/spec/types-and-protocols.md` and asserted by a test that exercises all three tiers
together.* That is the order the code already implies — 264 resolves *which rule*, this plan
adjusts *that rule's thresholds* — but "implied by the call order" is not a contract, and the
two plans will be implemented by different passes.

**D3 — A configured override naming an unknown station, rule or parameter: fail or warn? NEW,
open.** *Recommendation: fail hard at the onboarding boundary.* A typo in a station code or a
`rule_id` yields an override that silently never merges, and the resulting QC looks entirely
plausible while running the ungoverned base threshold — the same fail-open-by-omission failure
Plan 264 D3 and T3 both designed out. This is the parse-don't-validate boundary; a bad row
should not reach the store.

**D4 — What does an override mean for a rule that resolves to nothing? CLOSED by deferral.**
Plan 264 T3 makes a non-empty rule set resolving nothing an error. An override for such a rule
is then unreachable by construction, so this plan needs no separate policy — but the ordering
matters: if this plan lands first, an unreachable override is silently inert. Recorded in the
cross-plan sequencing note rather than as an open decision.

**D5 — Does the threshold tier get an API surface? NEW, open.**
`station_thresholds` — the closest precedent, and the one whose wiring T4 copies — **is**
exposed through `api/routes/api_stations.py:201`. Mirroring its wiring without deciding this
means the next plan that wants to show thresholds in the dashboard has a half-built path and no
stated position.
*Recommendation: no API surface in this plan, and say so as a contract rather than an
omission.* Plan 268 D14's data-handling constraint is the reason: a ceiling derived from a DHM
rating table **is** a restricted tabulated value unless it has passed through a deliberately
lossy transform. Storing such values in configuration and in the database already widens who
can read them; serving them over the stations API widens it again, to a surface whose scoping
this plan has not reviewed. Decide it deliberately, later, with that constraint in front of you.

## Tasks

### T1 — Schema

**Outcome**: a `station_qc_overrides` table exists, mirroring `forecast_qc_overrides`, with an
additive-only migration that leaves every existing row and column untouched.
**In**: `alembic/versions/0056_station_qc_overrides.py`;
`src/sapphire_flow/db/metadata.py`; `docs/spec/database-schema.md`.
**Out**: no change to `forecast_qc_overrides` (D1); no change to `observations`; no data
backfill; no column added to `stations`.
**Verification**: `uv run alembic upgrade head` then `downgrade -1` then `upgrade head` on a
scratch database leaves the schema identical, asserted by comparing the reflected metadata
before and after; the natural-key unique constraint rejects a duplicate
`(station_id, rule_id, parameter, time_step_seconds)`; the `station_id` FK rejects an unknown
station. **The migration chains onto `0055`** — check the current head before authoring, since
`0055`'s own docstring records two rebases caused by exactly this.
**Pre-change**: N/A — new schema, nothing to disprove.

### T2 — Store, Protocol and fake

**Outcome**: per-station overrides can be written and read back through a Protocol, with a fake
that behaves identically for tests.
**In**: `src/sapphire_flow/store/station_qc_override_store.py`;
`src/sapphire_flow/protocols/stores.py`; `tests/fakes/fake_stores.py`; unit tests.
**Out**: no call site wiring (T4); no configuration parsing (T3); no change to the
`QualityChecker` Protocol at `protocols/stores.py:1011-1021` — **its signature is already
correct and must not change.**
**Verification**: `uv run pytest tests/unit/store/test_station_qc_override_store.py` asserting
round-trip fidelity including the `timedelta` ↔ `time_step_seconds` encoding; that
`store_overrides` **upserts** rather than duplicating, so re-running onboarding with a corrected
ceiling replaces the old value (this is the mechanism that makes D14's "updatable later" true,
so it is asserted, not assumed); and that the fake and the Postgres store satisfy the same test
body. `uv run pyright` passes.
**Pre-change**: a RED test proving that today no store can persist a `StationQcOverride` —
the Protocol has no such method.

### T3 — Onboarding configuration surface

**Outcome**: a per-station QC threshold can be declared in configuration and is validated at
the boundary.
**In**: `src/sapphire_flow/config/onboarding.py`; `docs/spec/config-reference.toml`;
`src/sapphire_flow/flows/onboard.py` (the spec reaches `onboard_stations_flow` the way
`calculated_specs` already does); unit tests.
**Out**: no threshold *values* for any station — this task ships the surface empty; Plan 268
D14 owns what goes in it. No change to any existing `[onboarding.*]` block.
**Verification**: `uv run pytest tests/unit/config/test_onboarding_qc_thresholds.py` asserting
a valid block parses to the expected frozen spec; a non-numeric threshold, an unknown field and
a negative `time_step` are each rejected with a message naming the offending key; **an override
naming an unknown station, `rule_id` or parameter raises at the boundary** (D3) rather than
being stored inert; and a config with no such block yields an empty list, unchanged behaviour.
**Pre-change**: a RED test proving that today a `[[onboarding.station_qc_thresholds]]` block is
parsed by nothing — it is accepted and silently discarded.

### T4 — Wire the loader into the two live call sites

**Outcome**: the observation quality checker receives a station's configured overrides instead
of an empty list, at both production call sites.
**In**: `src/sapphire_flow/flows/ingest_observations.py:308`;
`src/sapphire_flow/services/onboarding.py:799`.
**Out**: **the checker signature is unchanged** — this task edits arguments, not parameters.
The forecast call sites (`flows/run_forecast_cycle.py`, six sites) keep `qc_overrides=[]`
(D1). **`scripts/dhm_precip/qc_mask.py:203,208` and `build_dudh_koshi_handover.py:175` are not
touched** — they call `check` positionally and are outside the pyright gate; Plan 264 round 2
found them the hard way.
**Verification**: `uv run pytest tests/unit/flows/test_ingest_observations.py` asserting a
station with a configured ceiling is judged against that ceiling while a station without one is
judged against the rule's base threshold, **in the same run** — the discriminating case, since
an implementation that loaded overrides but applied them to every station would pass a
single-station test. The override is fetched once per station per run, not per observation.
`uv run pytest` passes whole.
**Pre-change**: a RED test proving that today a persisted override has no effect — the call
sites pass `[]` regardless of what the store contains.

### T5 — Zero-row equivalence

**Outcome**: proof that a deployment with no configured overrides produces identical QC output
before and after this plan.
**In**: `tests/unit/services/test_qc_zero_override_equivalence.py`.
**Out**: not a new QC behaviour; a regression bar only.
**Verification**: a fixed observation series run against the rule set **loaded from
`config.toml`** — what actually runs, not `_default_swiss_qc_rules()`, which `load_qc_rules`
never returns while a `[qc_rules]` section exists (`config/qc_rules.py:262-268`) — yields
identical flags, statuses and `detail` text with an empty override list and with the loader
wired. Thresholds are not a returned field; they appear only inside `QcFlag.detail`, so the
comparison is over `detail` text.
**Pre-change**: N/A — the assertion is symmetric and needs no baseline artifact, because the
"before" case (empty list) remains executable after the change. This is why this plan needs no
golden fixture and Plan 264 T4 does.

### T6 — Documentation

**Outcome**: the per-station threshold tier is documented, and the two dead tiers beside it are
recorded as dead rather than left for the next reader to mistake for working code.
**In**: `docs/spec/types-and-protocols.md`; `docs/spec/database-schema.md`;
`docs/touchpoint-maps.md`; `docs/v0-scope.md:310` (which lists `forecast_qc_overrides` without
noting nothing reads it).
**Out**: no code change.
**Verification**: bounded inspection — the station-beats-network-beats-generic precedence (D2)
is stated once and in one place; `forecast_qc_overrides` is recorded as schema-only with its
six hard-coded call sites named (D1); `StationStore.store_thresholds` is recorded as having no
caller; and the absence of an API surface is stated as a contract with its reason (D5).
**Pre-change**: N/A — documentation.

```json
{
  "phases": [
    { "id": "phase-1", "tasks": ["T1"] },
    { "id": "phase-2", "tasks": ["T2", "T3"], "depends_on": ["phase-1"], "parallel": true },
    { "id": "phase-3", "tasks": ["T4"], "depends_on": ["phase-2"] },
    { "id": "phase-4", "tasks": ["T5", "T6"], "depends_on": ["phase-3"], "parallel": true }
  ]
}
```

**Cross-plan sequencing.** This plan and Plan 264 both end at `Stage1QualityChecker`, and the
order matters in one direction only:

- **Plan 264 before this one is preferred.** 264 T3 makes a non-empty rule set resolving
  nothing an error; land it first and an override attached to an unreachable rule fails loudly.
  Land this plan first and such an override is silently inert until 264 arrives (D4).
- **Plan 264 T4's golden fixture must be captured before either plan changes QC output.** This
  plan does not change it at zero rows (see *What this does not change*), so it does not
  threaten the fixture — but nor may it be used as a reason to skip capturing it.
- **Plan 268 T7 changes shape once this lands.** Its six station ceilings stop being built in
  memory and become configured rows. 268 D14 still owns the *values*, including the lossy
  transform that keeps a table-derived ceiling from publishing a restricted tabulated value —
  and that constraint gets stronger here, not weaker, because a configured row is readable by
  anyone with the config file or the database.

## Explicitly out of scope

- **The DHM threshold values.** Plan 268 D14 owns them, including whether a rating-table-derived
  ceiling is rounded or area-scaled before it is written down. This plan ships the surface empty.
- **Rule selection.** Plan 264 owns the network dimension, the fail-closed policy and the
  flag-version correction.
- **Reviving `forecast_qc_overrides`** (D1) — and equally, *dropping* it. Neither; document it.
- **An API or dashboard surface for reading or editing thresholds** (D5).
- **A writer for `station_thresholds`.** It is a different concern (alert danger levels) with
  its own missing writer and its own blocked product; recorded in T6, not fixed here.
- **Any change to `Stage1QualityChecker.check`'s signature or to the `QualityChecker`
  Protocol.** Both are already correct.
