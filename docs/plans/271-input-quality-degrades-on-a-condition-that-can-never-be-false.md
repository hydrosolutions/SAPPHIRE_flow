---
status: DRAFT
revised: 2026-09-11
created: 2026-09-11
plan: 271
reviews:
  - "claude 2026-09-11 r1 design/proportionality — NOT READY, 2 blockers + 4 majors + 3 minors; all verified, all folded"
  - "codex 2026-09-11 r1 citation verification — NOT READY, 2 blockers + 2 majors + 2 minors; all verified, all folded"
  - "gpt-6-astra 2026-09-11 expert consultation on D1 — recommends complying with the FI mapping; folded as the recommendation, not as the decision"
title: Three documents disagree about what a state-free model's warm-up source is, and the running code picks the one that degrades every forecast
scope: Resolve the three-way conflict over what `warm_up_source` means for a model that holds no state, make the classifier honour the resolution, and correct whichever documents lose. Covers the `warm_up` category ONLY. NOT implementing warm-up state persistence (already built, see §What already exists), NOT a change to the ForecastInterface signature, NOT the `observation`/`NWP`/`forcing` categories, NOT Plan 270's forcing-gap detection.
depends_on: []
blocks: []
related: [270, 023, 262, 253]
source: 2026-09-11 — measured on the mac mini in Plan 261 T1's first post-deploy cycle (12:00Z, 514 forecasts). Plan 270 measured the same saturation independently the same day and explicitly scoped it out as unowned (`270:139-141`). Host numbers are from that host on that day and are NOT verifiable from the repository; re-measure before quoting.
---

# Plan 271 — three sources disagree, and the code picks the worst one

> ⚠️ **Plan numbers 270 and 271 are not PR numbers.** PR #270 is Plan 261's merge (`75cf80cf`).

## Status

DRAFT, with **all four decisions CLOSED by the owner on 2026-09-11** and tasks written. The
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

## 🔴 The conflict — three sources, three answers

What should `warm_up_source` be for a model that holds no state?

| source | answer | where |
|---|---|---|
| **Our architecture document** | **`NULL`** — "NULL for ML models" | `docs/architecture-context.md:1845` |
| **The ForecastInterface contract** (co-designed with hydrosolutions) | **`FRESH`** — "a state-free FI model … always runs `WarmUpSource.FRESH` — already legal SAP3 behaviour for stateless models" | `ForecastInterface/docs/model_interface.md:82` |
| **The running code** | **`COLD_START`** | `services/operational_inputs.py:96-113` → `services/input_quality.py:120-127` |

The code's answer degrades every forecast. The other two do not.

### Why they disagree — they are answering different questions

This is not three parties contradicting each other on one question. It is three answers to
three questions, and only one of them was ever asked deliberately:

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
  **Plan 023 Step 2** — the WMO-compliance plan — which predates both other statements.

### 🔑 The proposed resolution (RECOMMENDED, NOT TAKEN — this is D1)

`NULL` and `FRESH` make the **same operational claim**: this forecast carries no warm-up
deficit. They differ only on whether the axis is "not applicable" or "applicable and nominal".
So the conflict is resolvable without declaring anyone wrong. The proposal:

> **`FRESH` = the axis applies to this model and there is no deficit** — including every
> state-free model, FI-routed or native.
> **`NULL` = there is no single model whose warm-up this could describe** — which today means
> combined products only (`services/forecast_combination.py:535` already does exactly this).
> **`COLD_START` = a model that CAN hold state was expected to have one and does not.**

Adopt FI's `FRESH`, and **amend `architecture-context.md:1845` in the same change** so the
repository carries one answer rather than two. Three reasons:

1. FI is the cross-organisation contract and `CLAUDE.md:70-77` makes compliance mandatory.
   This is the "our side violates the FI → fix our side" path, not the "file an FI issue" path.
2. `NULL` is already taken on the sibling field: `input_quality = None` means *unknown/legacy
   row* (`architecture-context.md:112`). Overloading `NULL` to also mean "deliberately not
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

- **Warm-up state persistence is fully built and wired.** `protocols/stores.py:562`,
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
512 carrying a structural artefact.

**There is exactly one real consumer, and it is broken today.** The API's `degraded_only=true`
filter (`api/routes/api_stations.py:274`, `store/forecast_store.py:243`) returns 100% of rows.
⚠️ **No dashboard indicator exists** (`architecture-context.md:112`) — an earlier revision of
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
| Tests | 17 `COLD_START` references across 5 files incl. `tests/integration/test_e2e_pipeline.py` | ⚠️ The change is NOT a one-liner. These encode the current rule. |

## The decisions — ALL FOUR CLOSED by the owner, 2026-09-11

**D1 — ✅ CLOSED: a state-free model records `FRESH`.** The owner adopted the FI mapping. The
resolution proposed above is therefore the resolution: `FRESH` = the axis applies and there is
no deficit; `NULL` = no single model to describe (combined products only, already the case at
`services/forecast_combination.py:535`); `COLD_START` = a model that CAN hold state was expected
to have one and does not. **`docs/architecture-context.md:1845` loses and must be corrected in
the same change** — that amendment is T3, and it is the deliberate part of this decision.
⚖️ Not an FI divergence, so nothing goes upstream.

**D2 — ✅ CLOSED: each model declares whether it keeps state.** Not adapter identity (three
NATIVE models — `linear_regression_daily.py:52`, `climatology_fallback.py:38`,
`persistence_fallback.py:36` — never touch the FI adapter and are equally state-free, so route
derivation would leave the saturation in place). Not an empty state table or a single `None`
return (a stateful model that LOST its state looks identical on its first cycle). An explicit
capability, detected by `isinstance`, exactly as SAP3 already detects `RetrainableModel`
(`forecast_interface/interface/protocol.py:55-58`).

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

**D4 — ✅ CLOSED: this ships BEFORE the deep-learning pilot.** The pilot does not depend on it,
but whoever watches that pilot would otherwise be reading a health signal stuck on for every
forecast and could not see the pilot misbehaving. ⚠️ Sequencing only — this plan does not block
the pilot's own preparatory work, which is owned by another session and must not be disturbed.

## Tasks

Now writable: D1 and D2 determine the shape and both are closed.

### T1 — a model declares whether it keeps state between runs

**Outcome.** A capability protocol exists SAP3-side, and every model is classifiable as
state-keeping or state-free without inspecting how it was constructed or what is in the store.

**In:** `src/sapphire_flow/protocols/`, `src/sapphire_flow/types/`, and the five model classes.
**Out:** any change to the FI package; any change to `predict` signatures.

⚠️ **Why a SAP3-side protocol and not an FI one.** FI reserves an additive `StatefulModel`
sub-protocol for FI models and has not shipped it — FI is state-free in v0 and no FI model is
stateful today. Our native models need classifying NOW. Define ours SAP3-side; when FI's lands,
it is additive and the two compose. This is not a workaround of the FI contract: it classifies
models FI does not cover.

**Verification:** each of the five deployed models reports state-free; a purpose-built
state-keeping fake reports state-keeping; the classification never consults the state store.

### T2 — the classifier honours the declaration

**Outcome.** A state-free model produces `FRESH` and NO `warm_up` flag. A state-keeping model
with no stored state still produces `COLD_START` and still degrades.

**In:** `services/operational_inputs.py:96-113`, `services/input_quality.py:120-127`, and the
**17 `COLD_START` references across 5 test files** (`test_input_quality.py`,
`test_operational_inputs.py`, `test_run_station_forecast.py`, `test_run_group_forecast.py`,
`tests/integration/test_e2e_pipeline.py`) — these encode the current rule and must be updated
deliberately, not mechanically. Depends on T1.

**Pre-change:** every forecast in the retained window is `cold_start`; `degraded_only=true`
returns 100% of rows.

**Verification:** a locking test per branch — state-free ⇒ no flag; state-keeping-and-missing ⇒
degraded. ⚠️ Scope the second to the **deterministic route**: stateful models are refused on the
ensemble fan-out as `UNSUPPORTED_STATEFUL_ENSEMBLE` (`services/run_station_forecast.py:434`,
`:536`) and ensemble-first is locked, so the ensemble route cannot exercise it.

### T3 — correct the architecture document (D1's amendment)

**Outcome.** Exactly one answer to "what is a state-free model's warm-up source" survives in
`docs/`.

**In:** `docs/architecture-context.md:1845` (the `NULL for ML models` comment) and `:1846`, plus
the warm-up prose at `:108-112` which frames warm-up as conceptual-model-only. Record that the
value changed and why, with the FI mapping cited as the authority.
**Out:** `docs/standards/wmo.md` — its row scopes evidence to persistence and API serialisation
only (`:188`) and never rested on this rule. Do not touch it.

**Verification:** a grep for the old convention returns only the changelog note recording its
withdrawal.

### T4 — record the historical period (D3)

**Outcome.** The interval during which `cold_start` meant "state-free model" is discoverable
FROM THE DATA, not only from this plan — so a later reader computing a degraded-rate trend can
see the discontinuity rather than mistake it for a quality change.

**In:** the changelog here plus wherever the deployment records version-scoped behaviour
changes. Measure first whether the affected rows include combined products, and state the
interval and the first version carrying the new meaning.
**Out:** re-stamping any historical row. D3 forbids it.

### T5 — verify on staging

**Outcome.** One real cycle demonstrates the signal discriminates again.

**In.** After T2 deploys: confirm the `warm_up` category produces no flag for state-free models,
that any forecast with genuinely stale observations still carries its `observation` flag, and
that `degraded_only=true` returns a strict subset rather than everything. Depends on T2, T3.

## Dependency graph

```json
{
  "plan": 271,
  "nodes": [
    {"id": "T1", "phase": 1, "depends_on": []},
    {"id": "T3", "phase": 1, "depends_on": [], "note": "doc-only; parallel with T1"},
    {"id": "T4", "phase": 1, "depends_on": [], "note": "measurement + record; parallel"},
    {"id": "T2", "phase": 2, "depends_on": ["T1"]},
    {"id": "T5", "phase": 3, "depends_on": ["T2", "T3"], "note": "needs a deployed cycle"}
  ]
}
```

## Exit gates

1. In one staging cycle the `warm_up` category produces **no flag** for state-free models, while
   any forecast with stale observations still carries its `observation` flag. ⚠️ Stated on the
   `warm_up` category's flag presence, NOT the aggregate level — Plan 270 and Plan 239's forcing
   flags move the same aggregate, so it cannot isolate this change.
2. A locking test proves a state-keeping model missing its state is STILL `COLD_START` and still
   degrades, on the deterministic route.
3. `degraded_only=true` returns a strict subset of forecasts, not all of them.
4. Exactly one answer to "what is a state-free model's warm-up source" survives in `docs/`.
5. No change to the `observation`, `NWP` or `forcing` categories. FI unchanged, with the reason
   recorded so it does not read as an omission.
6. No historical row re-stamped (D3), and the affected interval recorded where a data consumer
   will find it.

## Related, explicitly NOT in scope

- **Plan 270** — forcing-gap detection. Measured the same saturation and correctly scoped it out
  as unowned (`270:139-141`). 271 is the owner it was waiting for; neither changes the other's
  subject.
- **Plan 023** — introduced the rule (commit `77937c1b`, 2026-04-13). Read its rationale before
  overriding it. ⚠️ An earlier revision of this plan misattributed the rule to Plan 239, which
  added the *forcing* flags to an already-existing gate.
- **Plan 262** — the deep-learning pilot. D4 sequences this plan ahead of it. `cmal_small` is
  pure ML on a 30-day lookback and needs no persisted state (`262:84`, `262:91`), so it is one
  more model T1 classifies as state-free. It does not need this plan to run.
- **Building warm-up state persistence.** Already built and wired (`protocols/stores.py:562`,
  `services/operational_inputs.py:96-113`, five `store_state` sites in
  `flows/run_forecast_cycle.py`). What is genuinely open — and unowned — is whether any model
  will ever produce state, given FI is state-free by design and stateful models are refused on
  the ensemble route. Not started here.

## Changelog

- **2026-09-11 (initial)** — drafted from the first post-deploy cycle of Plan 261 T1.
- **2026-09-11 (r1 folds)** — two independent reviews (Claude design, Codex verification) both
  NOT READY, converging on the same blocker: FI already specifies the mapping. An expert
  consultation then surfaced a THIRD conflicting source, `architecture-context.md:1845`. Subject
  changed from "an open design space" to "a three-way conflict and its resolution".
- **2026-09-11 (owner decisions)** — D1–D4 all closed. D3 was decided AGAINST the unanimous
  recommendation of all three advisors; recorded as the owner's call with its consequence stated.
  Tasks written, since D1 and D2 now determine their shape.
