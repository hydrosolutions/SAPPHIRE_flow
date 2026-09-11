---
status: DRAFT
revised: 2026-09-11
created: 2026-09-11
plan: 271
reviews:
  - "claude 2026-09-11 r1 design/proportionality — NOT READY, 2 blockers + 4 majors + 3 minors; all verified, all folded"
  - "codex 2026-09-11 r1 citation verification — NOT READY, 2 blockers + 2 majors + 2 minors; all verified, all folded"
  - "claude 2026-09-11 r2 task-implementability — NOT READY, 3 blockers + 6 majors + 5 minors; all verified, all folded"
  - "codex 2026-09-11 r2 citation verification — NOT READY, 0 blockers + 3 majors + 3 minors; all verified, all folded"
  - "claude 2026-09-11 r3 coherence/implementability — NOT READY, 3 blockers + 7 majors + 4 minors; all verified, all folded"
  - "codex 2026-09-11 r3 citation verification — NOT READY, 0 blockers + 1 major + 3 minors; all verified, all folded"
  - "gpt-6-astra 2026-09-11 expert consultation on D1 — recommends complying with the FI mapping; folded as the recommendation, not as the decision"
title: Four documents disagree about what a state-free model's warm-up source is, and the running code picks the one that degrades every forecast
scope: Resolve the four-way conflict over what `warm_up_source` means for a model that holds no state, make the classifier honour the resolution, and correct whichever documents lose. Covers the `warm_up` category ONLY. NOT implementing warm-up state persistence (already built, see §What already exists), NOT a change to the ForecastInterface signature, NOT the `observation`/`NWP`/`forcing` categories, NOT Plan 270's forcing-gap detection.
depends_on: []
blocks: []
related: [270, 023, 262, 253]
source: 2026-09-11 — measured on the mac mini in Plan 261 T1's first post-deploy cycle (12:00Z, 514 forecasts). Plan 270 measured the same saturation independently the same day and explicitly scoped it out as unowned (`270:139-141`). Host numbers are from that host on that day and are NOT verifiable from the repository; re-measure before quoting.
---

# Plan 271 — four sources disagree, and the code picks the worst one

> ⚠️ **Plan numbers 270 and 271 are not PR numbers.** PR #270 is Plan 261's merge (`75cf80cf`).

## Status

DRAFT. **All six decisions (D1–D6) are CLOSED by the owner as of 2026-09-11**, and the tasks
are written. Nothing in this plan now waits on a decision. The
subject was a three-way conflict between our architecture document, the co-designed
ForecastInterface contract, and the running code; D1 resolves it. ⚠️ This revision is material
and UNREVIEWED — it needs one fresh independent round before it can be considered. Only the
owner sets READY.

## What was measured

Mac mini, 2026-09-11, the first cycle after Plan 261 T1 deployed (`75cf80cf` / `0.1.901`,
confirmed present in the *running* worker, not merely in the checkout).

| fact | value |
|---|---|
| Forecasts in the 12:00:01Z cycle | **514** |
| `input_quality = degraded` | **514 (100%)** |
| Carrying a `warm_up` cold-start flag | **514** — re-measured; an earlier revision said 512 |
| Also carrying an `observation` staleness flag | 2 |
| `warm_up_source = cold_start` | **514** |
| Rows in `model_states` | **0** |
| Distinct `warm_up_source` in the retained window (2026-09-04 →) | **`cold_start`, and nothing else** |

Not a Plan 261 regression, and not new: every forecast in the retained window, ~1,335/day.

## 🔴 The conflict — FOUR sources, three answers

What should `warm_up_source` be for a model that holds no state? ⚠️ **Round 2 found a FOURTH
source** — the type/Protocol spec, which `CLAUDE.md` calls *authoritative for implementation*. TWO documents say `NULL`, and T3 must correct both or exit gate 4 fails.

| source | answer | where |
|---|---|---|
| **Our type/Protocol spec** (`CLAUDE.md` calls it *authoritative for implementation*) | **`NULL`** — `# NULL for ML models` | `docs/spec/types-and-protocols.md:1788` |
| **Our architecture document** | **`NULL`** — "NULL for ML models" | `docs/architecture-context.md:1845` |
| **The ForecastInterface contract** (co-designed with hydrosolutions) | **`FRESH`** — "a state-free FI model … always runs `WarmUpSource.FRESH` — already legal SAP3 behaviour for stateless models" | `ForecastInterface/docs/model_interface.md:82` |
| **The running code** | **`COLD_START`** | `services/operational_inputs.py:96-113` → `services/input_quality.py:120-127` |

The code's answer degrades every forecast. The other three do not.

### Why they disagree — they are answering different questions

This is not four parties contradicting each other on one question. It is three answers to
three questions — two documents give the same answer — and only one was ever asked
deliberately:

- **`NULL`** was written when warm-up was conceived as a *conceptual-model* concept.
  `architecture-context.md:108-112` describes warm-up entirely in terms of conceptual models
  deriving soil moisture, snow and groundwater, with ML models explicitly excluded ("ML models
  do not produce state; this step is a no-op for them"). `NULL` there means **the axis does
  not apply to this model**.
- **`FRESH`** answers a different question: given SAP3's *existing* three-member vocabulary,
  which member does a state-free model map onto? FI's answer is the member meaning **no
  deficit** — and it explicitly calls that "already legal SAP3 behaviour".
- **`COLD_START`** answers no question at all. It is the unconsidered default: `load_warm_up_state`
  returns `COLD_START` whenever the store is empty, and the store is empty for every model that
  does not produce state. The rule dates to commit `77937c1b` (2026-04-13), implementing
  **Plan 023 Step 2** — the WMO-compliance plan. ⚠️ **Corrected round 2:** an earlier revision
  said this predates the other statements. It does not — `NULL for ML models` dates to `f87d6de1`
  (2026-03-11) and came FIRST; the code's answer came LAST, which if anything strengthens the
  case that it was never a considered choice. Plan 023 also explicitly excluded the pipeline
  wiring (`archive/023-degraded-forecast-input-quality.md:738-740`); the empty-store ⇒
  `COLD_START` path arrived separately in `7c642642`.

### 🔑 The resolution — TAKEN by the owner as D1

`NULL` and `FRESH` make the **same operational claim**: this forecast carries no warm-up
deficit. They differ only on whether the axis is "not applicable" or "applicable and nominal".
So the conflict is resolvable without declaring anyone wrong. The proposal:

> **`FRESH` = the axis applies to this model and there is no deficit** — including every
> state-free model, FI-routed or native.
> **`NULL` = there is no single model whose warm-up this could describe** — which today means
> combined products only (`services/forecast_combination.py:535` already does exactly this).
> **`COLD_START` = a model that CAN hold state was expected to have one and does not.**

Adopt FI's `FRESH`, and **amend BOTH documents that say `NULL` in the same change** —
`docs/spec/types-and-protocols.md:1788` and `docs/architecture-context.md:1845` — so the
repository carries one answer instead of three. Three reasons:

1. FI is the cross-organisation contract and `CLAUDE.md:54-69` makes compliance mandatory.
   This is the "our side violates the FI → fix our side" path, not the "file an FI issue" path.
2. `NULL` is already taken on the sibling field: `input_quality = None` means *unknown/legacy
   row* (`architecture-context.md:110`). Overloading `NULL` to also mean "deliberately not
   applicable" destroys the distinction between "we did not assess this" and "we assessed it
   and there is nothing to report".
3. Project type rules prefer an explicit enum member over an overloaded `None`.

⚠️ **The amendment is the deliberate part.** Correcting a trusted internal document to match an
external contract must be a recorded decision, not a quiet edit. If the owner prefers `NULL`,
that is a divergence from FI and must go upstream as an FI issue per `CLAUDE.md`, not be taken
silently.

⛔ **This is NOT an FI gap.** FI's state-free design is deliberate and documented, and FI has
already specified the SAP3 mapping. Filing an FI issue here would be the wrong one of
`CLAUDE.md`'s two paths.

## What already exists, so nobody re-scopes it

- **Warm-up state persistence is fully built and wired.** `protocols/stores.py:563`,
  `services/operational_inputs.py:96-113`, and five `store_state` call sites in
  `flows/run_forecast_cycle.py` (`:2906`, `:2939`, `:3194`, `:3273`, `:3561`). The only missing
  piece is a model that returns non-`None` state. Do not plan a build here.
- **The "not applicable" representation exists**: `WarmUpSource | None`, with
  `input_quality.py:120` suppressing the whole block on `None`.
- **`WarmUpSource` has exactly three members** today — `FRESH`, `SNAPSHOT`, `COLD_START`
  (`types/enums.py:23-26`).

## Why this is worth fixing

**A flag that is always on is not a degraded signal, it is no signal.** The two forecasts in
that cycle with a REAL problem — 85.4 h stale observations — are indistinguishable from the
512 carrying ONLY a structural artefact (all 514 carry the cold-start flag; 512 carry nothing
else).

**There is exactly one real consumer, and it is broken today.** The API's `degraded_only=true`
filter (`api/routes/api_stations.py:274`, `store/forecast_store.py:243`) returns 100% of rows.
⚠️ **No dashboard indicator exists** (`architecture-context.md:110`) — an earlier revision of
this plan claimed dashboard harm that does not exist.

**It does NOT threaten the WMO claim.** `docs/standards/wmo.md:188` scopes its evidence to
"persistence and authenticated API serialisation ONLY" — it never rested on the cold-start
rule. Plan 023 owns that rule and should be cited when it changes.

## Consumers — who reads this field

| consumer | reads | effect of the change |
|---|---|---|
| `api/routes/api_stations.py:274` + `store/forecast_store.py:243` | `degraded_only` filter | Stops returning 100% of rows. **This is the point.** |
| `services/forecast_combination.py:348-375` | contributor `input_quality` flags, inherited onto combined products | Combined products stop inheriting a spurious `warm_up` flag; their own `warm_up_source` stays `None` (`:535`) |
| `docs/standards/wmo.md:188` | compliance evidence row | Unaffected — scope is persistence + serialisation |
| Dashboard | — | **No indicator exists.** No effect. |
| Tests | **23** references across 5 files — 17 uppercase `COLD_START` + 6 lowercase `cold_start` — PLUS a golden fixture | ⚠️ The change is NOT a one-liner. These encode the current rule. |

## The decisions — ALL SIX CLOSED by the owner, 2026-09-11

**D1 — ✅ CLOSED: a state-free model records `FRESH`.** The owner adopted the FI mapping. The
resolution proposed above is therefore the resolution: `FRESH` = the axis applies and there is
no deficit; `NULL` = no single model to describe (combined products only, already the case at
`services/forecast_combination.py:535`); `COLD_START` = a model that CAN hold state was expected
to have one and does not. **BOTH documents saying `NULL` lose and must be corrected in the same
change** — `docs/spec/types-and-protocols.md:1788` and `docs/architecture-context.md:1845`
(⚠️ round 3: an earlier revision named only the architecture document here, while the conflict
table, T3 and gate 4 all named two — the fold had been applied everywhere except the decision
itself) — that amendment is T3, and it is the deliberate part of this decision.
⚖️ Not an FI divergence, so nothing goes upstream.

**D2 — ✅ CLOSED: each model declares whether it keeps state.** Not adapter identity (three
NATIVE models — `linear_regression_daily.py:52`, `climatology_fallback.py:38`,
`persistence_fallback.py:36` — never touch the FI adapter and are equally state-free, so route
derivation would leave the saturation in place). Not an empty state table or a single `None`
return (a stateful model that LOST its state looks identical on its first cycle). An explicit
capability, following the optional-capability pattern FI documents
(`forecast_interface/interface/protocol.py:55-58`).
⚠️ **Corrected round 2:** an earlier revision said "exactly as SAP3 already detects
`RetrainableModel`". SAP3 does NOT — `RetrainableModel` appears nowhere in `src/`, and
`docs/requirements/03-forecast-interface-adherence.md:51` records that `isinstance` routing as
**designed, unbuilt**. The decision stands; the precedent it cited did not exist.

**D3 — ✅ CLOSED, AGAINST the recommendation: leave the history alone and record the period.**
All three advisors recommended selective reclassification; the owner chose the conservative
option. No historical row is re-stamped. T4 records which interval carried the old meaning and
from which version the new one applies.
⚠️ **Accepted consequence, stated plainly:** any metric spanning the cutover compares two
different meanings of the same stamp. Anyone computing a degraded-rate trend across it will get
a discontinuity that is an artefact, not a change in forecast quality. T4 must make that
discoverable from the data, not only from this plan.
📎 The outstanding "do those 514 include combined products?" measurement no longer blocks a
decision; it is now only needed to state the period's extent accurately in T4.

**D5 — ✅ CLOSED 2026-09-11: silence is legal, an UNINTERPRETABLE declaration is fatal.**
A model that declares nothing is treated as **state-free**. A model that declares something the
system cannot interpret **raises** and stops the load. This follows the working precedent in the
same trio T1 piggybacks on — `declared_static_naming` (`services/caravan_statics.py:161-186`)
defaults on absence and raises only on a malformed declaration.
⚖️ The owner rejected BOTH extremes, and the reasons are worth keeping: a mandatory declaration
follows the `model_tier` path where `discover_models` **re-raises** `ConfigurationError`
(`services/model_registry.py:122-124`), so one forgotten model would darken the ENTIRE registry
and stop forecasting outright; pure defaulting would silently misclassify a genuinely stateful
model and lose the alarm this plan exists to preserve. Silence-legal/gibberish-fatal keeps the
alarm for malformed declarations without the catastrophic failure mode.

**D6 — ✅ CLOSED 2026-09-11: a model that never passes through `discover_models` is state-free.**
Test fakes are handed directly to `_run_single_model` and never reach the registry, so neither
the propagation nor the D5 check ever runs for them. They are treated as state-free, consistent
with D5. ⭐ **This closes the T2 ambiguity round 3 raised**: the golden fixture
(`tests/fixtures/plan151_t8b_canonical_snapshot.json`) is **REGENERATED**, not treated as an
error. Its model `fake_station_model` declares nothing and is therefore state-free.

*(Superseded framing, kept for one revision: D5 was previously open and stated to block T1 and
T2. Both are now unblocked.)*
- *Mandatory* follows the `model_tier` pattern, where `discover_models` **re-raises**
  `ConfigurationError` (`services/model_registry.py:122-124`) rather than skipping — so one
  undeclared class darkens the ENTIRE model registry.
- *Defaulted* already has a working precedent in the same trio T1 piggybacks on:
  `declared_static_naming` (`services/caravan_statics.py:161-186`) defaults on absence and raises
  only on a malformed declaration. ⭐ Round 3 surfaced this; the owner should see it before deciding.
- 🔴 **It gates T2, not only T1** (round 3): the golden fixture's model is `fake_station_model`
  (`tests/fixtures/plan151_t8b_canonical_snapshot.json:5`), a test fake injected directly into
  `_run_single_model` that NEVER passes through `discover_models`, so neither the propagation nor
  any mandatory check runs for it. Whether T2 reads such a model as state-free (fixture regenerated)
  or as undeclared-and-therefore-an-error IS D5. **T2 must specify the behaviour for a model that
  never went through `discover_models`.**

**D4 — ✅ CLOSED: this ships BEFORE the deep-learning pilot.** The pilot does not depend on it,
but whoever watches that pilot would otherwise be reading a health signal stuck on for every
forecast and could not see the pilot misbehaving. ⚠️ Sequencing only — this plan does not block
the pilot's own preparatory work, which is owned by another session and must not be disturbed.
⚠️ **This sequencing is OWNER-HELD and deliberately NOT encoded in the graph.** `blocks:` stays
empty and Plan 262 stays in `related:`, because 271 does not block the pilot technically — it
is a judgement about what should land first. Recorded here so it is not mistaken for an
unenforced dependency (round 2 finding).

## Tasks

D1 and D2 are closed, so these are writable. ⚠️ **Round 2 rewrote T1 and T2**: the mechanism as
first specified would not have worked.

### T1 — a model declares whether it keeps state between runs

**Outcome.** Every model is classifiable as state-keeping or state-free without inspecting the
state store, and the classification SURVIVES FI adaptation.

🔴 **The trap that sank the first draft — the adapter forwards NOTHING.** `discover_models`
wraps every FI model (`services/model_registry.py:107`, `adapt_if_fi`) before anything
downstream sees it, so `isinstance(adapted_model, …)` inspects the ADAPTER, not the model. This
is a known, already-solved problem here: `_assert_model_classification_declared`
(`services/model_registry.py:61-88`) exists precisely to "read classification declarations off
the RAW model and copy them onto the ADAPTED model", and does so today for `model_tier`,
`alert_eligibility` and `static_naming` — added as a Plan 155 D16 blocker for this exact reason.
**The declaration must be ATTRIBUTE-shaped and propagated there**, alongside those three. A
capability protocol alone would silently misclassify every FI-routed model — including
`cmal_pool_pt` today and `cmal_small` next, the very model D4 cites as the reason to ship.

🔴 **And it must not key on the state signature.** `prior_state: bytes | None = None` is already
on the base `StationForecastModel.predict` (`protocols/forecast_model.py:36`) and on all three
native models, and `isinstance` against a `runtime_checkable` Protocol is STRUCTURAL — so a
protocol keyed on that signature matches EVERY model and classifies nothing. The declaration
must be a member no current model has.

🔴 **Round 3: T1 copied only HALF the pattern.** `_declared_model_tier` and
`_declared_alert_eligibility` (`services/model_registry.py:34-50`) are **config-first**: they
consult `MODEL_TIERS` / `ALERT_ELIGIBILITIES` (`types/ids.py:35-50`) and only then fall back to
the class attribute, raising if neither supplies a value. Only `static_naming` is attribute-only.
That config route is not decoration — **it is how a model whose class we do not own gets
classified**, which is the entire premise of the partner/FI integration. An earlier revision
specified attribute-only propagation, under which an FI model we cannot edit would be
UNCLASSIFIABLE. T1 must either provide the same config-dict route, or state explicitly that it
does not and why that is safe for models we do not own.

**Shape.** A two-valued domain state, so `CLAUDE.md` forbids a `bool` — an enum (e.g.
`WarmUpStatePolicy.KEEPS_STATE` / `.STATE_FREE`) declared as a class attribute, with the
config-dict route above where a model's class is not ours to edit.
⭐ **D5 settles the absence rule:** a class declaring nothing is state-free; a class declaring
something uninterpretable raises. Mirror `declared_static_naming`
(`services/caravan_statics.py:161-186`), NOT `_declared_model_tier`, which raises on absence.
⚠️ Choose a name that does NOT collide with FI's reserved `StatefulModel`; two
`runtime_checkable` protocols with overlapping semantics and the same name is a trap for the
next reader.

**In:** `src/sapphire_flow/types/enums.py`, `src/sapphire_flow/protocols/forecast_model.py`,
`services/model_registry.py:61-88` (propagation), and **all SEVEN registered classes**
(`pyproject.toml:174-184`): `LinearRegressionDaily`, `ClimatologyFallbackModel`,
`PersistenceFallbackModel`, `NwpRegression`, `NwpRainfallRunoff`,
`SeasonalPrecipRunoffRegression`, and the optional `CmalPoolPT`.
⚠️ An earlier revision said "five". Five is the count of models OBSERVED ISSUING on the host;
seven is the count REGISTERED in the repository. T1's scope is the seven.
📎 **Documentation is IN scope.** `CLAUDE.md` requires every code change to update affected
docs, and the new enum plus the new protocol member belong in `docs/spec/types-and-protocols.md`,
where `WarmUpSource` already lives (`:100`) and `StationForecastModel.predict` is specified.
⚠️ **That is the same file T3 edits**, so T1 and T3 must NOT run concurrently — the graph now
sequences T3 after T1.
**Out:** the FI package; any `predict` signature change.

⚖️ **Why SAP3-side and not upstream — judged SOUND by independent review.** FI reserves
`StatefulModel` for FI models, has not shipped it, and explicitly delegates detection to SAP3.
Three of our models never touch FI at all and still need classifying. This classifies models the
FI contract does not cover; it is not the forbidden patch-around.
**Composition rule, stated now rather than left open:** while FI ships no state extension, an
FI-routed model is state-free by contract; when FI's extension lands, an FI model satisfying it
is state-keeping and the SAP3 attribute defers to it.

**Verification:** all seven classes report a policy (⚠️ `CmalPoolPT` resolves only with the
`aquacast` extra, which is NOT installed by default — that check needs a skip condition); a purpose-built state-keeping fake reports
state-keeping; **an FI-ADAPTED model still reports its raw model's policy after `discover_models`**
(the propagation test — this is the one that would have caught the original defect); the
classification never reads the state store.

### T2 — the classifier honours the declaration

**Outcome.** A state-free model produces `FRESH` and NO `warm_up` flag. A state-keeping model
with no stored state still produces `COLD_START` and still degrades.

📌 **D6: a model handed straight to `_run_single_model` (test fakes) never reaches the registry
and is state-free.** The golden fixture is therefore REGENERATED, not an error.

🔴 **The companion field, named for the first time in round 3.** `warm_up_state_age_hours` is set
to `None` on the `COLD_START` branch and to a real age on `FRESH`
(`services/operational_inputs.py:110-121`). After T2 the database therefore carries TWO meanings
of `FRESH`: `(fresh, age NULL)` = a state-free model, `(fresh, age 3.2)` = a genuine recent
snapshot. They are distinguishable ONLY by the age field. T2 must state that a state-free model
sets the age to `None`, and T3 must give the replacement text for
`docs/architecture-context.md:1846`, whose current `(NULL when fresh or ML)` is already wrong for
the snapshot case.

🔴 **The signature must change, and that widens the edit surface.** `load_warm_up_state`
(`services/operational_inputs.py:96-101`) receives a `ModelId`, never a model object, so T1's
classification cannot be applied inside it as-is. Changing it touches **both** call sites:
`services/run_station_forecast.py:378` (station route) and
`services/operational_inputs.py:1132` (the GROUP/`OperationalInputMetadata` route). ⚠️ The GROUP
route was ABSENT from the first draft's scope even though `services/run_group_forecast.py:315`
and `:359` stamp `warm_up_source` from exactly that metadata.

**In:** `services/operational_inputs.py:96-113` and `:1132`,
`services/run_station_forecast.py:378`, `services/run_group_forecast.py:315,359`, and the test
surface below. Depends on T1.
📎 `services/input_quality.py:120` is **read-only — confirm no change is needed**: it already
reads `if warm_up_source is not None and warm_up_source != WarmUpSource.FRESH`, so `FRESH`
already suppresses the block. Listing it as an edit invites an unnecessary change to a function
three other categories share.

**Test surface — measured, and larger than the first draft claimed:**
**23 references across 5 files**, re-measured in round 3 after the two reviewers disagreed:
**17 uppercase `COLD_START`** (input-quality 3, operational-inputs 2, station 6, group 4, e2e 2)
**+ 6 lowercase `cold_start`** (input-quality 2, operational-inputs 2, station 2) — the lowercase
form matters because it is the persisted DB value. ⚠️ Both are literal grep counts; an earlier
revision claimed 23 was "an impact inventory, not a grep count", which overclaimed. PLUS a
**golden fixture**: `tests/fixtures/plan151_t8b_canonical_snapshot.json:11`
hard-codes `"warm_up_source": "cold_start"` with its flag and detail text, and is loaded and
compared by `tests/unit/flows/test_run_forecast_cycle.py:8837`. **T2 fails that test unless the
fixture is regenerated.**

**Pre-change:** every forecast in the retained window is `cold_start`; `degraded_only=true`
returns 100% of rows.

**Verification:** a locking test per branch — state-free ⇒ no flag; state-keeping-and-missing ⇒
`COLD_START` and degraded. ⚠️ **Scope the second to the DETERMINISTIC route, and cite the right
guard.** `services/run_station_forecast.py:536` (`reject_stateful_ensemble_states`, the
OUTPUT-side guard) is what refuses a stateful model on the ensemble fan-out. `:434`
(`reject_prior_state_for_fanout`, `services/ensemble_fanout.py:56-57`) raises only when
`prior_state is not None` — which is NOT the branch under test, so a test built from `:434`
would pass without proving anything. An earlier revision cited `:434`.

### T3 — correct BOTH losing documents (D1's amendment)

**Outcome.** Exactly one answer to "what is a state-free model's warm-up source" survives in
`docs/`.

**In:** `docs/spec/types-and-protocols.md:1788` ⚠️ **(found in round 2 — the first draft missed it,
and `CLAUDE.md` calls this spec *authoritative for implementation*)**;
`docs/architecture-context.md:1845`, `:1846` (give it replacement text — see T2), and the
warm-up prose at `:108`, `:109` and `:112` framing warm-up as conceptual-model-only.
⛔ **NOT `:110`** — that is the input-quality bullet whose `None = unknown/legacy row` statement
this plan's own reason 2 depends on KEEPING, and `:111` is an unrelated per-track bullet. An
earlier revision gave the range `:108-112`, which would have swept both. Record that the value changed and why, citing the FI mapping
as the authority.
📎 Also disposition — do not necessarily change — the other surfaces that carry this field:
`types/forecast.py:59`, `store/forecast_store.py:77,432,448`, `api/routes/api_forecasts.py:83`,
`api/schemas.py:123`, `db/metadata.py:1134`, `alembic/versions/0001_v0_schema.py:477`,
`docs/conventions.md:415`, `docs/spec/database-schema.md:284,856`, `docs/touchpoint-maps.md:394`.
Most need no edit; the plan must say so rather than leave them unexamined.
**Out:** `docs/standards/wmo.md` — its row scopes evidence to persistence and API serialisation
only (`:188`) and never rested on this rule.
📎 Gate 5's "FI unchanged, and why" needs a home that outlives this plan: record it in
`docs/requirements/03-forecast-interface-adherence.md`.

**Verification:** a grep for the old convention returns only changelog notes recording its
withdrawal — across BOTH documents.

### T4 — record the historical period (D3)

**Outcome.** The interval during which `cold_start` meant "state-free model" is discoverable
FROM THE DATA, so a later reader charting a degraded-rate trend sees the discontinuity rather
than mistaking it for a change in forecast quality.

**In:** the changelog here plus wherever the deployment records version-scoped behaviour changes.
Measure first whether the affected rows include combined products, then state the interval and
the first version carrying the new meaning.
⚠️ **Depends on T2** — "the first version carrying the new meaning" does not exist until T2 is
built and the version bumped. An earlier revision put T4 in phase 1 with no dependencies, which
was unbuildable.
**Out:** re-stamping any historical row. D3 forbids it.

### T5 — verify on staging

**Outcome.** One real cycle demonstrates the signal discriminates again.

**In.** After T2 deploys: confirm the `warm_up` category produces no flag for state-free models,
that any forecast with genuinely stale observations still carries its `observation` flag, and
that `degraded_only=true` returns a non-total subset (see gate 3 on why non-empty cannot be
required unconditionally). Depends on T2, T3.

## Dependency graph

```json
{
  "plan": 271,
  "nodes": [
    {"id": "T1", "phase": 1, "depends_on": [], "note": "declaration + propagation through the FI adapter"},
    {"id": "T3", "phase": 2, "depends_on": ["T1"], "note": "round 3: T1 also edits types-and-protocols.md, so T3 must NOT run concurrently"},
    {"id": "T2", "phase": 2, "depends_on": ["T1"]},
    {"id": "T4", "phase": 3, "depends_on": ["T2"], "note": "needs the shipped version number (round 2 fix)"},
    {"id": "T5", "phase": 3, "depends_on": ["T2", "T3"], "note": "needs a deployed cycle"}
  ]
}
```

## Exit gates

1. In one staging cycle the `warm_up` category produces **no flag** for state-free models, while
   any forecast with stale observations still carries its `observation` flag. ⚠️ Stated on the
   category's flag PRESENCE, not the aggregate level — Plan 270 and Plan 239's forcing flags move
   the same aggregate. ⚠️ **If the cycle contains no stale-observation forecast, this gate is NOT
   met by default** (the measured cycle had 2 of 514); fall back to T2's unit test.
2. A locking test proves a state-keeping model missing its state is STILL `COLD_START` and still
   degrades, **on the deterministic route** (`run_station_forecast.py:501`). ⚠️ Build it on the
   forecast's own provenance and quality, NOT on either fan-out guard: `reject_prior_state_for_fanout`
   (called `:425`) raises only when `prior_state` is NOT None, and `reject_stateful_ensemble_states`
   (called `:527`) only when a model RETURNS per-member states — both are inert for this branch, so
   a test anchored on either passes without proving anything. Round 2 corrected this citation once
   and it was still wrong; round 3 corrected it again.
3. `degraded_only=true` returns a **non-total** subset. ⚠️ "A strict subset" alone is satisfied by
   the empty set, which is also what a broken filter returns — but non-EMPTY cannot be required
   unconditionally either: after T2 the only degraded rows in the measured cycle were the 2 of 514
   with stale observations (0.4%), so a clean cycle legitimately returns none. If the verification
   cycle contains no degraded forecast from any surviving source (`observation`, `NWP`, or Plan 239
   forcing), this gate falls back to T2's unit test, exactly as gate 1 does.
4. Exactly one answer to "what is a state-free model's warm-up source" survives in `docs/` —
   checked across BOTH `types-and-protocols.md` and `architecture-context.md`.
5. No change to the `observation`, `NWP` or `forcing` categories. FI unchanged, with the reason
   recorded in `docs/requirements/03-forecast-interface-adherence.md` (not only here).
6. No historical row re-stamped (D3), and the affected interval recorded where a data consumer
   will find it.
7. An FI-adapted model reports its raw model's state policy after `discover_models` — the
   propagation that the original T1 would have got wrong.

## Related, explicitly NOT in scope

- **Plan 270** — forcing-gap detection. Measured the same saturation and scoped it out as
  unowned (`270:139-141`). 271 is the owner it was waiting for.
- **Plan 023** — introduced the rule (commit `77937c1b`). Read its rationale before overriding
  it. ⚠️ An earlier revision misattributed the rule to Plan 239, which added the *forcing* flags
  to an already-existing gate.
- **Plan 262** — the deep-learning pilot. `cmal_small` is pure ML on a 30-day lookback and needs
  no persisted state (`262:84`, `262:91`), so it is one more model T1 classifies as state-free.
- **Building warm-up state persistence.** Already built and wired (`protocols/stores.py:563`,
  `services/operational_inputs.py:96-113`, five `store_state` sites in
  `flows/run_forecast_cycle.py`). What is genuinely open — and unowned — is whether any model
  will ever produce state. Not started here.

## Changelog

- **2026-09-11 (initial)** — drafted from the first post-deploy cycle of Plan 261 T1.
- **2026-09-11 (r1 folds)** — two independent reviews, both NOT READY, converging on the same
  blocker: FI already specifies the mapping. An expert consultation surfaced a third conflicting
  source. Subject changed from "an open design space" to "a conflict and its resolution".
- **2026-09-11 (owner decisions)** — D1–D4 closed. D3 decided AGAINST the unanimous advice of
  three advisors; recorded as the owner's call with its consequence stated. Tasks written.
- **2026-09-11 (r2 folds)** — two further independent reviews: 3 blockers, 9 majors, 8 minors
  between them, all verified before folding. ⭐ **The mechanism was wrong twice over**: an
  `isinstance` capability would have inspected the FI ADAPTER rather than the model (the adapter
  forwards nothing, which `model_registry.py` already exists to work around), and keying it on
  the state signature would have matched EVERY model because `prior_state` is already on the base
  protocol. A **fourth** conflicting source was found, ranked above the one already named. T2's
  edit surface gained a signature change, two call sites and the entire GROUP route; its test
  surface gained 6 references and a golden fixture. T4 was unbuildable in phase 1. Three
  citations were wrong, including one that would have produced a locking test passing for the
  wrong reason. The count of models was "five" (observed) where it should be seven (registered).
  ⚖️ The FI-adherence judgement in T1 was independently assessed as SOUND.
- **2026-09-11 (r3 folds)** — two further reviews: 3 blockers, 8 majors, 7 minors between them.
  ⭐ The round-2 fold had been applied at the conflict table, T3 and gate 4 but **NOT at D1
  itself**, which still named one document — the exact fix-the-primary-site failure this project
  has on record. ⭐ **`warm_up_state_age_hours` was named for the first time in any round**: after
  T2 the database carries two meanings of `FRESH`, separable only by that field. ⭐ T1 had copied
  only HALF the classification pattern — `model_tier`/`alert_eligibility` are config-FIRST, and
  that route is how a model whose class we do not own gets classified, so attribute-only
  propagation would leave partner models unclassifiable. T1 also listed no documentation despite
  editing a spec file T3 edits, while the graph ran them concurrently; T3 now depends on T1.
  D5 was found to gate T2 as well as T1. The ensemble-guard citation was wrong for the THIRD
  round running (`:434` → `:536` → actually `:425`/`:527`, and both are inert for the branch under
  test). Gate 3 could legitimately block on an empty set. The "ranks ABOVE" claim was false.
  ⚖️ **The count dispute settled by direct measurement:** the reviewers disagreed 17 vs 23; both
  were right — 17 uppercase `COLD_START` + 6 lowercase `cold_start`, and the lowercase form is the
  persisted DB value, so 23 is the impact inventory.
- **2026-09-11 (owner closes D5 + D6)** — silence is legal and an uninterpretable declaration is
  fatal, following `declared_static_naming` rather than `_declared_model_tier`; and a model that
  never passes through `discover_models` is state-free, which regenerates the golden fixture
  rather than erroring on it. Both extremes were rejected deliberately. No decision is now open.
- *(historical)* **D5 as first surfaced by round 2:** is the declaration
  MANDATORY on every model, or does it default to state-free? If mandatory on the `model_tier`
  pattern, `discover_models` **re-raises** `ConfigurationError`
  (`services/model_registry.py:122-124`) rather than skipping — so one undeclared class would
  darken the ENTIRE model registry. Defaulting is safer but lets a genuinely stateful model be
  silently misclassified. This must be settled before T1 is built.
