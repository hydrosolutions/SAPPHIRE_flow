---
status: DRAFT
created: 2026-08-27
plan: 204
title: Forecast Lab shows 3 of 6 forecasts once POOLED is on — quantile models and `_pooled` are both invisible
scope: Two additions to the export so it renders what the deployment now produces, shipped as `forecast-lab-snapshot/v2`. The v1-vs-v2 question T3 asked is ANSWERED — v2, because every boundary model forbids extra fields (the FORBID-EXTRA RULE below) so no addition is backward-compatible. No new tables, no change to the forecast cycle.
depends_on: [198]
blocks: []
source: Surfaced 2026-08-27 by the `forecast_combination_strategy = pooled` trial (PR #214)
---

# Plan 204 — the Forecast Lab cannot see half of what the cycle now produces

## Status

**DRAFT.** Not for implementation until the owner confirms.

Scope is three tasks (T1/T2/T3) and has not grown across the review rounds: no new abstraction, no
backfill, no performance work, no verification metrics. The body below states only the **current**
rule for each decision — the round-by-round trail that produced it is in Appendix A.

## ⛔ Proportionality

**Two gaps, both narrow.** Neither is a bug in what Plan 198 shipped — both are consequences of a
config change made after the contract was written. Do not reopen Plan 198's settled decisions
(F3's percentile orientation, the cut T4/T9b/T10/T11, `licence_status`, the deviation table).
Reviewers: "no findings" is a complete review.

### Reviewers: DO NOT OVER-ENGINEER THIS PLAN (owner instruction, 2026-08-27)

This is a **three-task change to an export that already works in production** — verified end-to-end
on real data (see "Plan 198 is now PROVEN" below). It is not an architecture round. The owner's
explicit instruction for this review is to **hold the scope**, and a review that grows the plan is a
worse review than one that finds nothing.

**In scope for findings:** the T1/T2/T3 contract shape is wrong or ambiguous; a stated fact is false;
an acceptance criterion does not actually lock its behaviour; a locking test would pass against the
buggy implementation; the change breaks an existing consumer.

**What a good review here actually does:** check that every cited `file:line` says what the plan
claims (a fix folded in one round has already turned out to be wrong in a later one), and check that
each locking test would genuinely fail against the buggy implementation it targets — a test that
passes both before and after locks nothing. A round reporting "it checks out" is complete and
valuable; do not manufacture findings to justify the pass.

**Explicitly OUT of scope — do not propose, and reject if proposed:**

- New abstractions, registries, strategy objects or plug-in points for "future representations".
  There are exactly two representations (`members`, `quantiles`) and a CHECK constraint enforcing it.
- Generalising the quantile mapping beyond the measured level set. Handle the levels this deployment
  stores; fall back to the D5 guard otherwise. That fallback IS the generality.
- Backfill, migration, recomputation, or any change to the forecast cycle, Plan 026 combination, or
  the models themselves. This plan reads what the cycle already wrote.
- Performance work. The export is 2 stations and 364 KB, built in under a second.
- **Reopening `v1` versus `v2`. ANSWERED: `v2`** (decision 1 below, and T3). Do not re-argue it in
  either direction, and do not propose a dual-serving v1 compatibility surface.
- Verification metrics, CRPSS, thresholds, or anything from the Flow Map integration audit. Plan 111
  G1 gates that and the licence letter is unsent.
- Expanding the `p05`/`p95` fork below — the owner has decided it (option (a)).

**If a reviewer believes a genuinely blocking problem sits outside these bounds, say so in one
sentence and stop there** — do not design the fix into this plan.

## Contract decisions

Five decisions fix the shape of this change; everything in T1/T2/T3 follows from them. Two facts are
named here **once** and referenced by name for the rest of the document rather than re-cited.

1. **`v2`, not `v1` — the FORBID-EXTRA RULE.** Every boundary model inherits `ForecastLabModel`,
   whose config is `{"extra": "forbid"}` (`api/forecast_lab_schemas.py:91-95`), so the generated
   schema carries `additionalProperties: false` on **every fixed-shape boundary object — 22 of the
   23 objects** in `docs/spec/forecast-lab-snapshot-v1.schema.json`. *That is the FORBID-EXTRA RULE;
   it is referred to by name below, not re-cited.* The one exception is intentional and not a hole:
   `AlignedDailyRowSchema.sapphire` is a *keyed map*, so its `additionalProperties` is the typed
   `$ref` to `AlignedDailySapphireEntrySchema` rather than `false`
   (`docs/spec/forecast-lab-snapshot-v1.schema.json:139-145`) — which is exactly why T1 can fold the
   combined forecast into that map under a sentinel key without a shape change (decision 5).
   A v1 validator therefore **rejects** any document carrying `combined_forecast` or the new
   per-entry `representation`. The additions are additive to the *builder*, not to the *contract*.
2. **A quantile entry is not an ensemble entry on the wire — the MEMBER-COUNT TRAP.** `ensemble_size`
   is fed from `ensemble.member_count` (`services/forecast_lab/snapshot.py:293`), which for
   `QUANTILES` returns the number of *levels* (`types/ensemble.py:30-37`) — so rendering a 0-member
   quantile forecast through today's shape would ship `ensemble_size: 7`. *That is the MEMBER-COUNT
   TRAP; it is referred to by name below, not re-cited.* v2 therefore splits the available entry into
   **two variants, nested-discriminated on `representation`**: a members variant with a **required**
   `ensemble_size` and no quantile field, and a quantiles variant with a **required**
   `quantile_level_count` and no ensemble field. A tag plus two nullable counts on one class would
   leave `representation="quantiles", ensemble_size=21` constructible, locked by nothing but a
   hand-written test someone must remember to extend — exactly what CLAUDE.md § Type Driven
   Development forbids ("invalid states should be unrepresentable"), and exactly the pattern this
   module already avoids everywhere else (`BafuForecastEntry`, `SapphireForecastEntry`,
   `forecast_lab_schemas.py:214-217`, `:263-266`). T2 carries the class layout; the same split
   applies to T1's combined block.
3. **The combined block is strategy-gated, not fetched unconditionally.**
   `ForecastStore.fetch_latest_forecast()` (`store/forecast_store.py:124-138`) returns the latest
   matching row with no age or current-mode constraint, so a deployment switched `pooled` →
   `primary` would keep exporting a stale `_pooled` row forever. `build_snapshot()`
   (`snapshot.py:488`) gets the strategy injected from config, through both callers.
4. **`combined_forecast` is always present and discriminated on `available`.** "Absent with a reason"
   is self-contradictory — an absent key carries no reason. It mirrors the existing `bafu_forecast`
   union (`forecast_lab_schemas.py:214-222`, field at `:336`).
5. **The combined forecast joins the daily comparison.** `aligned_daily_comparison[].sapphire` is a
   plain `dict[str, AlignedDailySapphireEntrySchema]` (`forecast_lab_schemas.py:290-305`) with no
   per-model metadata, so folding it in under the sentinel key costs nothing structurally — and
   leaving it out would exclude the deployment's best forecast from the one array the spec built for
   cross-source comparison (`docs/spec/forecast-lab-snapshot.md:74-82`).

**Trade-off accepted, recorded rather than hidden:** v2 is a *breaking* cutover, not a compatible
addition. It is affordable only because the single consumer token (`sapphire-flow-map`, minted
2026-08-27) has not yet integrated. If that stops being true before this ships, stop and re-plan the
rollout — do not add a compatibility shim inside this plan.

**One finding no review round produced, supplied by an external consumer** (the SAPPHIRE-flow-map
Codex agent, 2026-08-27): the committed fixture pairs real code `2091` with **Chancy's** name and
coordinates. It is fixed in T3. Every round reasoned about the fixture's *structure*; only someone
trying to *join on it* noticed.

## Plan 198 is now PROVEN against real data — this plan starts from a working export

Plan 198 closed with two caveats: *"the export has never run against the real database or the live
archive"* and *"T7's compose changes need the mac-mini redeploy to be verified in place."* **Both
are now closed, verified on the mini 2026-08-27T16:00Z (image `sapphire-flow:0.1.806`).**

| Check | Result |
|---|---|
| T7 compose deployed | `SAPPHIRE_CONFIG_OVERLAY=/app/config/overlays/mac-mini.toml` set on `api`; archive volume mounted **read-only** at `/data/bafu_forecasts` (O1) |
| Archive path **resolves** (not just mounted) | `load_config().bafu_forecast_archive_path` → `/data/bafu_forecasts`, `exists=True`, `['parsed','raw']` |
| CLI export, real DB + real archive | `overall_status=ok`, `station_count=2`, 364 KB, exit 0 |
| REST route, real consumer token | `HTTP 200`, identical station set and status roll-up |
| Committed JSON Schema | **validates** the real document (`forecast-lab-snapshot-v1.schema.json`) |
| Non-finite floats | **0** bare `NaN`/`Infinity` tokens in the emitted JSON (the post-build defect stays fixed on real data) |
| `bafu_forecast` | **`ok`, not `missing`** — the failure mode the memory predicted is gone; real runs `2009_q_forecast_20260827T130000Z` / `2091_q_forecast_20260827T090000Z` |
| Consumer token | `sapphire-flow-map`, role `consumer`, tenant-scoped, expires 2027-08-27 — already minted |

Two operational facts worth carrying, neither a defect:

- **`licence_status: "unresolved"`** on every BAFU forecast — correct and expected until the Plan 111
  G1 letter is sent. The map must not render it as an error.
- **The CLI cannot be run with a bare `docker exec`.** The image entrypoint composes `DATABASE_URL`
  from `DATABASE_URL_TEMPLATE` + `DB_PASSWORD_SECRET` (Plan 161); `docker exec` bypasses it and the
  export dies on `KeyError: 'DATABASE_URL'`. The working invocation is
  `docker exec <api> /entrypoint.sh python -m sapphire_flow.cli.export_forecast_lab --output …`.
  **T3 must add this line to `docs/spec/forecast-lab-snapshot.md`** — the CLI is an operator tool and
  its documented invocation currently fails in the only deployment that has one.

## What changed underneath the contract

PR #214 set `forecast_combination_strategy = "pooled"` on the mini. A cycle now writes **6 forecasts
per station** where it wrote 1. The Plan 198 export renders **3** of them.

Measured 2026-08-27T13:29Z, station 2009 (identical at 2091):

| forecast | representation | in snapshot? |
|---|---|---|
| `nwp_regression` | members (21) | ✅ |
| `nwp_rainfall_runoff` | members (21) | ✅ |
| `linear_regression_daily` | members (50) | ✅ |
| `climatology_fallback` | **quantiles (7)** | ❌ `unsupported_representation` |
| `persistence_fallback` | **quantiles (7)** | ❌ `unsupported_representation` |
| `_pooled` | members (92) | ❌ **absent entirely** |

**Re-measured 2026-08-27T16:0xZ against the live export — the table holds, with one clarification a
reviewer will otherwise trip on.** `sapphire_forecasts` contains **six entries**, which is not the
six rows above: it is the six *assigned* models. Three are `available: true`; two are
`unsupported_representation` (the quantile fallbacks); one is
`seasonal_precip_runoff_regression` → `no_forecast` (assigned, produced nothing — pre-existing, out
of scope here). `_pooled` is the seventh model and appears **nowhere**. So the consumer sees three
usable forecasts where the cycle produced six, and the one absence is silent — an
`unsupported_representation` entry at least tells the map *why*, whereas `_pooled` leaves no trace
at all. That asymmetry is why T1 and T2 are both worth doing, and why T1 is the more urgent.

## Gap 1 — quantile models render as unavailable

D5 built a deliberate guard: a `representation != members` forecast returns
`"unsupported_representation"` rather than relabelling outer quantiles as `minimum`/`maximum`. **The
guard is correct and is doing its job** — this plan does not weaken it.

But D5's stated premise was *"All 232 stored forecasts on the mini are `members` (verified), so
quantile support is not built."* That was true when written and **POOLED made it false the same
week.** Both fallback-tier models emit 7 quantile levels and 0 members.

**RESOLVED by the owner 2026-08-27 — SHOW THEM.** The fallback models are the *floor*, and a
research comparison UI wants the baseline everything else must beat. T2 is **in scope**. Map the
stored quantile levels onto the envelope **without inventing the tails**: `p25`/`median`/`p75` come
from real stored levels; `minimum`/`maximum` must be **`null`**, because a 7-level quantile forecast
does not contain the ensemble extremes and fabricating them is the exact error the guard exists to
prevent.

**The mapping is exact — measured on the mini 2026-08-27T16:0xZ, not assumed.** Every stored
quantile forecast carries exactly these seven levels, for both fallback models, at every
`valid_time` (`forecast_values.quantile`, 5 valid times x 7 levels x 2 stations):

```
0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95
```

Two consequences, both load-bearing for T2:

1. **`p25`, `median` and `p75` are present as EXACT stored levels.** The mapping is a lookup, not an
   interpolation — `sapphire_quantile_method: "linear"` describes how *member* forecasts are
   summarised and does not apply here. A quantile forecast must never be run through the
   order-statistic path (`_quantile_summary`, `snapshot.py:96-118`).
2. **`0.05`/`0.95` are NOT the extremes** — which is exactly why they cannot fill `minimum`/
   `maximum`. A 5th-percentile value rendered as "minimum" would read on the map as the lower bound
   of the forecast, understating the tail by construction. This is the concrete form of the error
   D5's guard was built to prevent, and the reason AC3's locking test matters.

**`sapphire_quantile_method` is a per-representation statement, not a document-wide one.** After T2 a single snapshot mixes entries whose `p25`/`median`/`p75` are
`numpy.quantile(..., method="linear")` order statistics over members with entries whose values are
exact stored levels — while `ComparisonSemanticsSchema.sapphire_quantile_method` is a single field
for the whole document (`forecast_lab_schemas.py:139-146`, `Literal["linear"]`). Leaving it
unqualified would re-introduce, one field over, exactly the "silently misleading" failure D5 guards
against. **Resolution (cheapest honest one): keep the field and its literal unchanged and narrow its
documented meaning** — it describes `representation: "members"` entries only. T3 writes that
sentence into `docs/spec/forecast-lab-snapshot.md:33-45` alongside how quantile entries are derived.
No per-entry method field is added: v2's `representation` discriminator (T2) already tells a
consumer which rule applies to which entry, so a second field would be redundant.

**Residual fork — RESOLVED by the owner 2026-08-27: option (a), envelope shape unchanged.** The
envelope has **no slot for the 0.05/0.10/0.90/0.95 levels**, so mapping to `p25`/`median`/`p75`
silently *discards four of the seven stored levels* — including the widest band the fallback models
publish. Options: (a) accept the loss, envelope unchanged, the map shows a narrower band for
fallbacks than the model actually emits; (b) extend the envelope with optional `p05`/`p95`.
**The owner chose (a) — accept the loss, ship the comparison the map asked for — explicitly "for
now, may change later".** So: do NOT add `p05`/`p95` in this plan, and reviewers must not reopen it.
Two obligations follow from the "may change later", and they are the whole of what (a) costs:

1. `comparison_semantics` must state that a quantile forecast's envelope is built from **stored
   levels** and that its extremes are **unavailable**, so a consumer never reads a `null` `minimum`
   as a data error or as a zero.
2. The discarded levels must be recorded as a **known, deliberate omission** in
   `docs/spec/forecast-lab-snapshot.md` — naming the four dropped levels — so that revisiting this
   is a documented one-line change and not a rediscovery. **Adding `p05`/`p95` later is NOT free of
   a version bump:** `QuantileEnvelopeSchema` (`forecast_lab_schemas.py:175-181`) inherits the
   FORBID-EXTRA RULE, so a later `p05`/`p95` is a `v3`. The spec must say so.

## Gap 2 — `_pooled` is structurally invisible

The snapshot iterates the station's **assigned models** (D17b, `fetch_active_model_assignments`,
`services/forecast_lab/db_sources.py:134-142` → `station_store.fetch_model_assignments()`).
`_pooled` (`types/ids.py:21`) is an `artifact_scope = 'virtual'` sentinel with **no assignment row**
— assignments are only written for onboarded models (`services/model_onboarding.py:976,984`) — so no
iteration ever reaches it, regardless of representation.

This is the forecast a comparison UI would most want: a 92-member combined ensemble over the three
skill-tier models, carrying the provenance no per-model entry has. It is also the deployment's best
forecast under the Plan 026 design.

**What `source_model_ids` does and does not mean.** The export **reproduces the
persisted DB provenance exactly** — nothing more. The stored `source_model_ids` is built from every
combinable result (`services/forecast_combination.py:237`), while `combine_ensembles_pooled` skips
non-`MEMBERS` ensembles *per parameter* (`forecast_combination.py:58-64`). On the measured live row
the two coincide, but the export must not be described as independently validating which model
actually contributed to which parameter.

**Shape (concrete, v2).** A sibling `combined_forecast` block on `StationEntrySchema`, **always
present and discriminated on `available`**, exactly mirroring the existing `bafu_forecast` union
(`forecast_lab_schemas.py:214-217`, field at `:336`). It is not a model entry: it has no artifact,
no `is_primary`, and carries `combination_strategy` + `source_model_ids` that no per-model entry has.

**The alternative, weighed on its merits.** The serious alternative to a sibling field is a **third
discriminated variant** of `SapphireForecastEntry` (a combined variant carrying
`combination_strategy`/`source_model_ids` and no `model`/`is_primary`), synthesised into the list
`_sapphire_entries()` already returns (`snapshot.py:230-301`). It adds **zero** fields to ordinary
entries, and every existing generic consumer of `sapphire_entries` — `_days_covered` (`:306`),
`_aligned_sapphire` (`:402`), `_aligned_daily_comparison` (`:425`) and the roll-up filter
(`:526-542`) — would pick it up for free. That is a genuine advantage: forgetting to wire one of
those four is a failure that has already happened once in this plan's drafting.

**Decision: keep it a sibling field, and pay the sync cost with structure instead of vigilance.**
Reasons, in order of weight:

1. `sapphire_forecasts[]` is contractually *"one entry per assigned model"*
   (`forecast_lab_schemas.py:220-222`, D12/D17b) and is keyed by `model.key` throughout the consumer
   contract. The combined forecast has **no assignment row at all** (that is Gap 2's whole cause),
   so putting it in the list makes the array's documented cardinality rule false and forces a new
   decision — whether the combined entry competes for `is_primary` — that the sibling shape simply
   does not have to make. AC8 stays a statement about assigned models.
2. A consumer iterating `sapphire_forecasts[]` to draw per-model traces would silently start drawing
   the combined ensemble as if it were a model — for the one consumer that exists
   (`sapphire-flow-map`, not yet integrated), a sibling field is an explicit opt-in.
3. The sync risk the alternative removes is removed here too, structurally, by the
   single-rendered-list requirement in T1 below: derived views must not each re-decide what a
   SAPPHIRE forecast is.

**Cost accepted and recorded:** the combined entry is *not* free-riding on the existing generic
iteration, so T1 must wire it into the shared list explicitly, once. If a future task adds a fifth
derived view and reads `sapphire_entries` directly instead of the shared list, the bug recurs —
which is why the shared list, not the wiring, is the required part.

```
combined_forecast (available: true, representation: "members")
  available: true                      # Literal[True], outer discriminator
  representation: "members"            # Literal["members"], inner discriminator
  ensemble_size: int                   # REQUIRED here, absent from the quantiles variant
  source: "sapphire"
  forecast_id: str
  model_key: str                       # the sentinel actually fetched: "_pooled" or "_bma"
  combination_strategy: str            # from the stored row (types/forecast.py:69)
  source_model_ids: list[str]          # from the stored row (types/forecast.py:70)
  variable: "discharge" / unit: "m3/s"
  issued_at, observation_staleness_hours, native_step_seconds
  horizon_start, horizon_end
  points: list[QuantileEnvelopeSchema]

combined_forecast (available: true, representation: "quantiles")
  # identical, except: quantile_level_count: int (REQUIRED) and NO ensemble_size field.
  # Not reachable today (Plan 026 combination emits members), but the type is the same
  # nested union as the per-model entry so the two cannot drift — see T2.

combined_forecast (available: false)
  available: false                     # Literal[False], outer discriminator
  reason: "strategy_primary" | "no_combined_forecast"
  #   strategy_primary     -> strategy is PRIMARY; no lookup performed
  #   no_combined_forecast -> POOLED/BMA discharge lookup returned None,
  #                           or strategy is CONSENSUS (unsupported, no lookup)
```

The nested-union mechanics are shared with T2 and verified against this repo's pydantic: an outer
`Field(discriminator="available")` union whose `True` arm is itself a
`Field(discriminator="representation")` union validates, rejects `ensemble_size` on a quantiles
entry, and generates a nested `oneOf`/`discriminator` JSON Schema that `jsonschema.validate` accepts
(the committed-schema tests use `jsonschema`, `tests/unit/api/test_forecast_lab_schema.py:38-42`,
and `discriminator` is not a JSON Schema keyword, so validation runs on the `oneOf`).

`qc_status` is deliberately **not** on this block — see "Not in scope".

## Tasks

Every command below runs from the repo root.

**T1 — Expose the combined forecast as a `combined_forecast` block.**

Add the schemas above to `api/forecast_lab_schemas.py` — a shared available base plus its
`members`/`quantiles` variants, the unavailable schema, and the `CombinedForecastEntry` nested
discriminated union (outer `available`, inner `representation`; same construction as T2's, so build
the two blocks to the same pattern) — and a `combined_forecast: CombinedForecastEntry` field to
`StationEntrySchema` (`:332-339`). In `services/forecast_lab/snapshot.py`:

- `build_snapshot()` (`:488`) takes a new keyword
  `combination_strategy: ModelCombinationStrategy = ModelCombinationStrategy.PRIMARY`
  (`types/enums.py:115-119`). **The `PRIMARY` default is deliberate:** it is
  exactly the pre-Plan-204 behaviour (no combined block), so the **17 existing `build_snapshot(...)`
  call sites** in `tests/unit/services/forecast_lab/test_snapshot.py` (`:180 … :912`) — none of which
  exercise combination — need no mechanical edit. A required keyword would have forced 17 churn-only
  diffs that lock nothing. The two real callers still inject the deployment-configured value
  explicitly, and the propagation exit test below is what proves they do; the default is never the
  path production takes. **The sentinel is never fetched unconditionally** — the whole lookup is
  gated on the strategy, because `ForecastStore.fetch_latest_forecast()`
  (`store/forecast_store.py:124-138`) has no age or current-mode constraint and would otherwise
  re-export a stale `_pooled` row forever after a `pooled` → `primary` switch. Dispatch is over the
  **whole** enum — write it as one exhaustive `match` on `ModelCombinationStrategy`, never as a
  `PRIMARY` special case with an "everything else fetches `_pooled`" tail — that shape passes a
  pooled-only test set while exporting a stale `_pooled` row under `BMA` and `CONSENSUS`:
  - `PRIMARY` → no fetch at all; `available: false`, `reason: "strategy_primary"`.
  - `POOLED` → fetch `POOLED_MODEL_ID`; `BMA` → fetch **`BMA_MODEL_ID`, not `POOLED_MODEL_ID`**
    (`types/ids.py:21-22`).
  - `CONSENSUS` → **no fetch at all** (an implementation note in the `match` arm — the *observable*
    requirement is only that nothing is exported; see the exit list) (the cycle raises
    `NotImplementedError` for it,
    `forecast_combination.py:206-207`, so no row can exist — and `_consensus`, `types/ids.py:23`, is
    never written); `available: false`, `reason: "no_combined_forecast"`.
  - fetch returned `None` → `available: false`, `reason: "no_combined_forecast"`.
    **What `no_combined_forecast` asserts.** It means **"no exportable combined *discharge* forecast
    is available under the current strategy"** — not "no stored sentinel row exists at all". Two
    distinct cases share the one reason, and T3 documents them separately:
    - `POOLED`/`BMA`: the lookup ran and returned `None`. The lookup is filtered to
      `parameter="discharge"` (`db_sources.py:145-152` → `_DISCHARGE_PARAMETER`,
      `db_sources.py:42`), while the cycle writes **one combined row per parameter**
      (`forecast_combination.py:240`, `for _param, ensemble in combined.items()`), so a sentinel
      row for some other parameter may well exist. The reason says nothing about those.
    - `CONSENSUS`: unsupported, and **no query is issued** — the reason is a static verdict, not a
      lookup result.
    Within the `POOLED`/`BMA` case the block follows the document's existing latest-run rule:
    `fetch_latest_forecast()` (`store/forecast_store.py:124-138`) orders all history by
    `issued_at desc` and takes the newest match, so once *any* combined discharge row exists it
    returns that row — a later cycle with fewer than two combinable results
    (`forecast_combination.py:202-203` returns `[]`) writes nothing and the block therefore keeps
    showing the previous run. That is the *same* "latest available run from each source" semantics
    every other block in this document already uses (`snapshot.py:581-589`,
    `display_run_rule="latest available run from each source"`), so it is consistent, not a defect.
    Current-cycle matching is explicitly **not** designed here — it would need a different query and
    is out of scope; T3 documents the latest-run semantics for this block instead.
- **ONE shared "rendered SAPPHIRE sources" list, not four bespoke wirings — a required
  implementation constraint, the structural half of the sibling-field decision above.** Today four
  derived views each re-derive "what counts as a rendered SAPPHIRE forecast" by filtering
  `sapphire_entries` themselves: `_days_covered` (`:306`, via `isinstance` at `:324`),
  `_aligned_sapphire` (`:402`), `_aligned_daily_comparison` (`:425`) and the roll-up filter in
  `build_snapshot` (`:526-542`, `sapphire_available_entries = [...]` feeding `sapphire_flags` and
  `sapphire_times`). If T1 wires the combined block into each of them separately, a fifth view added
  later forgets it.
  **Requirement:** build, per station, a single list of `(key, available_entry)` rendered sources —
  the available `sapphire_entries` plus, when present, the combined block under its sentinel key —
  and have **all four** views consume *that* list. The combined block keeps its own wire field; the
  list is internal to `snapshot.py`. No view may filter `sapphire_entries` for availability on its
  own after T1.
  **Type contract for the shared element (say it, do not leave it to the implementer).** The four
  views are currently typed against
  `list[SapphireForecastAvailableSchema | SapphireForecastUnavailableSchema]`
  (`snapshot.py:306-310`, `:425-430`) and `_aligned_sapphire` keys its result by `entry.model.key`
  (`:414`) — an attribute the combined variants deliberately do not have (they carry `model_key`).
  Define **one module-level `Protocol`** in `snapshot.py` (CLAUDE.md § Type Driven Development,
  "prefer `Protocol` over inheritance") holding only what these four views actually read —
  **`issued_at: datetime`** and `points: list[QuantileEnvelopeSchema]` — and change the four
  signatures to take `list[tuple[str, <that Protocol>]]`, the `str` being the render key
  (`model.key` for an assigned model, the sentinel for the combined block). `_aligned_sapphire` then
  keys off the tuple, not off `entry.model.key`, which is what lets a combined entry into the map at
  all. Both Pydantic families satisfy the Protocol structurally, so pyright strict (the pre-push
  gate) is satisfied without a shared base class and without widening any wire model.
  **The annotation must be `datetime`, NOT `UtcDatetime` (independent Codex pass, round 6 — this is
  the one defect five prior rounds missed).** The schemas carry `issued_at: Rfc3339Utc`, which is
  `Annotated[datetime, PlainSerializer(...)]` (`forecast_lab_schemas.py:36-38`, used at `:248`) —
  statically just `datetime`. `UtcDatetime` is a **distinct `NewType`** (`types/datetime.py:4`), and
  a Protocol's mutable attributes are **invariant**, so `datetime` does not satisfy `UtcDatetime`
  and pyright strict (`pyrightconfig.json:3`, `include: ["src"]`) would reject every implementer.
  Write `issued_at: datetime`. If a stronger guarantee is ever wanted, make it a read-only
  `@property` (covariant) rather than reintroducing the `NewType`.
- **What each derived consumer then shows** (they are the point of the block, not an afterthought):
  `_days_covered` includes the combined forecast's horizon; `_aligned_sapphire` and
  `_aligned_daily_comparison` fold it into the `sapphire` dict under the **sentinel key actually
  fetched** (`_pooled` / `_bma`) — free, because `AlignedDailySapphireEntrySchema` (`:290-296`)
  carries no per-model metadata; and `availability.sapphire_forecast` plus the D16a
  `sapphire_forecasts` source status / `latest_available_at` roll-up (`:451-476`, `:500-560`) count
  it as an available SAPPHIRE forecast. Under `PRIMARY` none of this can fire, so **T1 contributes
  no combined forecast to a `PRIMARY` roll-up; T2 may still alter it** (a quantile fallback that
  becomes renderable changes `availability.sapphire_forecast` and the status roll-up on a `PRIMARY`
  deployment too — this is the same narrowing AC2 already records, restated here so the two do not
  contradict each other).
- Callers inject the strategy from config (`config/deployment.py:148-150`): the REST route
  (a new `get_forecast_combination_strategy()` dependency defined alongside
  `get_bafu_forecast_archive_path()` at `api/routes/forecast_lab.py:74-79`, added as a `Depends()`
  parameter next to `archive_base_path` at `:135` and passed through the `build_snapshot(...)` call
  at `:142`) and the CLI
  (`cli/export_forecast_lab.py:139`, from the `load_config()` it already calls at `:177`).

*Exit — all eight below. Nothing in the current suite touches the new field, so a negative-only or
pooled-only subset would pass a build that never renders the block, or one that leaks a stale
`_pooled` row into `BMA`/`CONSENSUS`.*

- **Positive path (the finding this task exists for):** given a station with a stored `_pooled`
  forecast (`POOLED_MODEL_ID`, `combination_strategy="pooled"`, `source_model_ids` set) and
  `combination_strategy=POOLED`, `build_snapshot()` renders `combined_forecast.available is True`
  with exactly that `forecast_id`, `combination_strategy`, `source_model_ids` and a non-empty
  `points`. RED today: no code path calls
  `fetch_latest_forecast_for_model(..., POOLED_MODEL_ID)` (`db_sources.py:145-152`) and the schema
  has no such field.
- **Representation/count invariant on the combined block:** the positive test above must *also*
  assert `combined_forecast.representation == "members"`, that the block is an instance of the
  **members variant**, and `combined_forecast.ensemble_size == stored_ensemble.member_count` (92 on
  the measured live row). No `quantile_level_count is None` assertion: the members variant has no
  such field and the FORBID-EXTRA RULE makes emitting one a validation error, so the MEMBER-COUNT
  TRAP is unrepresentable here rather than merely tested for.
- **Daily-alignment path:** the same fixture puts a `_pooled` key into
  `stations[0].aligned_daily_comparison[*].sapphire`, and its horizon appears in the day set even
  when it extends past every per-model forecast.
- **Stale-row lock (decision 3):** the *same stored `_pooled` row*, with
  `combination_strategy=PRIMARY`, renders `available: false`, `reason: "strategy_primary"`, and the
  `_pooled` key is absent from every aligned row — the snapshot still builds, no error. A test that
  seeds *no* `_pooled` row would pass against the buggy build, so it must seed one.
- **Second absence path:** `POOLED` with no stored `_pooled` row → `available: false`,
  `reason: "no_combined_forecast"`.
- **`BMA` selects the `_bma` row, not `_pooled` — the dispatch lock.** Seed **both**
  a `_pooled` row and a `_bma` row (`types/ids.py:21-22`) for the same station with distinguishable
  `forecast_id`s, build with `combination_strategy=BMA`, and assert `combined_forecast.model_key ==
  "_bma"` and the `_bma` `forecast_id` — and that the aligned-daily `sapphire` dict carries a `_bma`
  key and **no `_pooled` key**. RED against the "`PRIMARY` special case, everything else fetches
  `POOLED_MODEL_ID`" build, which the pooled-only tests above cannot distinguish.
- **`CONSENSUS` exports nothing, even with combined rows stored.** Seed a stale `_pooled` row (and,
  for good measure, a `_bma` row), build with `combination_strategy=CONSENSUS`, and assert
  `available: false`, `reason: "no_combined_forecast"`, and that **no sentinel key** (`_pooled`,
  `_bma`, `_consensus`) appears in any `aligned_daily_comparison[].sapphire` dict — RED against a
  `PRIMARY`-special-case tail, which exports the stale `_pooled` row and fails all three.
  **Deliberately not tested: that no query is issued.** "No fetch" and "fetch then discard" are
  indistinguishable in the export's output, so asserting it would lock an implementation detail with
  no observable consequence (CLAUDE.md § Testing Philosophy golden rule 1) and would need spy
  infrastructure no fake in `tests/fakes/fake_stores.py` has (`FakeForecastStore`, `:250-274`,
  records nothing). **Trade-off recorded:** a build that fetches and discards under
  `CONSENSUS`/`PRIMARY` passes — one wasted DB round trip, otherwise correct.
- **Roll-up lock — combined-only positive scenario.** The three roll-ups
  (`availability.sapphire_forecast`, `status.sapphire_forecasts.status`,
  `status.sapphire_forecasts.latest_available_at`) today derive **exclusively** from
  `sapphire_entries` (`snapshot.py:522-543`; `_aggregate_source_status`, `:451-475`), so a build
  that renders the block and the daily alignment but leaves those three untouched passes every other
  test here. So: **one station with no renderable assigned forecast** (assignments producing nothing, or
  only the D5-guarded representation) **plus a stored `_pooled` row**, `combination_strategy=POOLED`
  → assert `stations[0].availability.sapphire_forecast is True`,
  `status.sapphire_forecasts.status == "ok"` (the single eligible station has the source), and
  `status.sapphire_forecasts.latest_available_at == combined_forecast.issued_at`. RED today on all
  three.
- **Propagation — BOTH caller tests must use a NON-`PRIMARY` strategy (independent Codex pass,
  round 6).** One route test (`api/routes/forecast_lab.py:142`) and one CLI test
  (`cli/export_forecast_lab.py:139`) proving the configured strategy actually reaches
  `build_snapshot()`. **The earlier design — `strategy_primary` in one case, a rendered block in the
  other — locks only half of what it claims:** `build_snapshot()` defaults to `PRIMARY`, so the
  caller handed the `PRIMARY` case passes *identically whether or not it forwards the argument*.
  That test is green against the exact bug it exists to catch. So patch the config to **`POOLED`**
  (or `BMA`) in **both** tests and assert a rendered combined block in each, which is RED against a
  caller that drops the argument. If a `strategy_primary` assertion is still wanted, add it as a
  **third** test — never as one of the two propagation locks.

*Verify:*
`uv run pytest tests/unit/services/forecast_lab/ tests/unit/api/test_forecast_lab_route.py tests/unit/cli/test_export_forecast_lab.py -q`

**T2 — Quantile envelope. IN SCOPE (owner confirmed 2026-08-27).**

In `_sapphire_entries()` (`snapshot.py:230-301`), a `QUANTILES` forecast whose stored levels are the
**recognised set** is rendered available; anything else keeps the D5 guard.

> **T2 ships inside the same v2 cutover as T1; there is no "T1 slips, ship T2 anyway" path.** T2
> adds `representation` and `quantile_level_count` to the available entry, and the committed
> `SapphireForecastAvailableSchema` is `additionalProperties: false` with neither property
> (`docs/spec/forecast-lab-snapshot-v1.schema.json:624-712`), so a T2-only document **fails**
> `test_example_validates_against_the_committed_schema`
> (`tests/unit/api/test_forecast_lab_schema.py:38-42`) and would break the real `sapphire-flow-map`
> consumer's validation. The one part of T2 that *is* v1-clean is not a separability claim:
> `QuantileEnvelopeSchema.minimum`/`.maximum` are already `anyOf: [number, null]`, so `null`
> extremes need no schema change.

- **The RECOGNISED-SET RULE — stated once, here; everything below and in T3 refers to it by this
  name rather than re-deriving it.** The recognised set is an EXACT match, not a superset —
  `{0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95}`, checked **per `valid_time`**, for every `valid_time`
  in the forecast. **Set equality alone is not enough: require exactly seven rows AND seven unique
  levels at each `valid_time`.** Nothing upstream rejects a duplicate — `forecast_values` has no uniqueness
  constraint on `(forecast_id, valid_time, quantile)` (`db/metadata.py:1161-1178`: only the XOR
  check constraint and a non-unique `(forecast_id, valid_time)` index at `:1181-1185`), and
  `ForecastEnsemble.from_quantiles()` validates only the *global* unique-level count and the outer
  levels (`types/ensemble.py:97-106`). So a timestep carrying all seven levels plus a second `0.25`
  row satisfies set equality while leaving two conflicting candidate values for `p25` — the lookup
  would silently pick one. Row-count-plus-uniqueness makes that forecast fall back to the D5 guard.
  The reason for exactness itself is the
  existing test, not the config: a looser "contains 0.25/0.50/0.75" reading would silently flip the
  9-level fixture (`tests/unit/services/forecast_lab/test_snapshot.py:346-390`, levels `0.02 … 0.98`)
  from `unsupported_representation` to available — an undetected AC5 regression. Per-`valid_time`
  because `ForecastEnsemble.from_quantiles()` validates the level set **globally**
  (`types/ensemble.py:97-106`), so a forecast can pass construction while one timestep is short a
  level.
  - **`min_operational_quantile_levels` does NOT support exactness — do not cite it as the reason.**
    That field
    (`config/deployment.py:152`) is used everywhere in the codebase as a *count floor* compared with
    `>=`/`<` — `observed < required` in model onboarding (`services/model_onboarding.py:784-793`) and
    `total < min_required` in the alert checker (`services/alert_checker.py:186-188`) — never as a
    value-set constraint. It says "at least 7 levels", which a 9-level model satisfies. So state the
    consequence plainly rather than imply otherwise: **a legitimately onboarded 9-level quantile
    model clears the deployment's operational floor and is still rendered
    `unsupported_representation` by this export.** That is the deliberate cost of the owner's option
    (a) scoping — handle the levels this deployment stores, fall back to the D5 guard otherwise — and
    T3 must record it in `docs/spec/forecast-lab-snapshot.md` next to the four dropped levels, so the
    next person meets it as a documented limit rather than a surprise.
- Mapping: exact lookup `0.25 → p25`, `0.50 → median`, `0.75 → p75`; `minimum` and `maximum`
  **`null`**. A quantile forecast is never routed through `_quantile_summary` (`snapshot.py:96-118`)
  or `_ensemble_points` (`:204-222`) — those read `member_id` and are the member path.
- **Wire shape — a nested discriminated union.** The MEMBER-COUNT TRAP is made **unrepresentable**
  rather than merely tested:
  - Keep `SapphireForecastAvailableSchema` (`forecast_lab_schemas.py:241-254`) as the **shared base**
    holding every field common to both representations (`available`, `source`, `forecast_id`,
    `model`, `variable`, `unit`, `issued_at`, `observation_staleness_hours`, `native_step_seconds`,
    `horizon_start`, `horizon_end`, `points`) — **minus `ensemble_size`**, which moves down.
  - Add `SapphireForecastMembersSchema(SapphireForecastAvailableSchema)` with
    `representation: Literal["members"]` and a **required** `ensemble_size: int`, and
    `SapphireForecastQuantilesSchema(SapphireForecastAvailableSchema)` with
    `representation: Literal["quantiles"]` and a **required** `quantile_level_count: int`. Neither
    carries the other's count field, and the FORBID-EXTRA RULE rejects it if a builder tries.
  - Union them `Annotated[Members | Quantiles, Field(discriminator="representation")]` and use that
    as the `True` arm of the existing `Annotated[..., Field(discriminator="available")]`
    `SapphireForecastEntry` (`:263-266`). Pydantic v2 supports this nesting; verified in this repo's
    environment (a `quantiles` payload carrying `ensemble_size` is rejected, and
    `ForecastLabSnapshot.model_json_schema()` emits a nested `oneOf` + `discriminator` that
    `jsonschema.validate` accepts).
  - **Why the base keeps its current name:** three `isinstance(..., SapphireForecastAvailableSchema)`
    call sites in `snapshot.py` (`:324`, `:402`, `:529`) and four in
    `tests/unit/services/forecast_lab/test_snapshot.py` (`:188`, `:242`, `:281`, `:610`) keep working
    unchanged, because both variants are subclasses. The base is never itself a union member, so it
    can never be constructed from the wire and never appears in the generated `$defs`.
  - The same split applies to the T1 combined block, for the same reason.
  - **Known cost, flagged not designed around:** the generated schema's outer
    `discriminator.mapping["True"]` is an inline subschema rather than a `$ref` string — fine for the
    committed-schema tests (`jsonschema` validates on `oneOf` and ignores `discriminator`), but an
    extension of the OpenAPI 3.1 discriminator convention. If a strict OpenAPI codegen consumer ever
    chokes on it, that is a follow-up; no fallback is designed here.
- **`is_primary` must consider quantile entries.** `primary_model_id` is currently
  the first fetched forecast whose `representation is MEMBERS` (`snapshot.py:243-249`). After T2 a
  cycle that falls through to a quantile fallback would render it available with `is_primary: false`
  and no entry primary at all. Change the rule to **the first entry this builder actually renders**
  — members, or a recognised quantile set — preserving the existing `(priority, model_id)` order
  from `fetch_active_model_assignments` (`db_sources.py:134-142`).
- **ONE shared predicate, not two prose-matched checks — a required implementation constraint.**
  The rule above is evaluated in *two* places: the
  primary pre-pass (`snapshot.py:243-249`) and the per-entry render decision inside the loop
  (`snapshot.py:230-301`, where T2 adds the per-`valid_time` exact-level-set check). If those two
  drift, the export breaks in a way no listed test catches. **Concrete failure:** model A (priority
  0) stores a quantile forecast over 5 `valid_time`s, one of which is missing `0.25`, so the loop
  correctly renders it `unsupported_representation`; model B (priority 1) stores a fully complete
  recognised set. A pre-pass that mirrors today's coarser shape — `representation is QUANTILES` plus
  a *global* unique-level-set match, ignoring per-`valid_time` construction detail — picks model A as
  `primary_model_id`. Model A is not in the rendered set at all, so **no entry carries
  `is_primary: true`** even though model B is available and should win it.
  **Requirement:** factor the recognition test into a single module-level predicate (e.g.
  `_is_renderable(forecast) -> bool` = "MEMBERS, or QUANTILES satisfying the RECOGNISED-SET RULE
  above", with the level set as one named module constant) and call **that same function** from both
  the pre-pass and the entry loop. No second, independently written check anywhere in `snapshot.py`.

*Exit:*

- **Positive mapping test with distinct values per level** (not merely "extremes are null"): stored
  `0.25`/`0.50`/`0.75` values of e.g. `1.0`/`2.0`/`3.0` must land as `p25=1.0, median=2.0, p75=3.0`
  — a build that emits nulls, or swaps `p25`/`p75`, fails.
- **Extremes lock:** the same forecast has `minimum is None` and `maximum is None`. RED against the
  plausible wrong build that fills them from `0.05`/`0.95`.
- **Extra-levels lock:** the existing 9-level fixture (`test_snapshot.py:346-390`) still yields
  `reason == "unsupported_representation"` — this is what pins the exact-match reading.
- **Per-`valid_time` completeness:** a two-timestep forecast where only the *second* timestep lacks
  `0.25` falls back to the D5 guard for the **whole** forecast, not a partial envelope on timestep
  one.
- **Duplicate-level regression:** a forecast whose every `valid_time` carries all
  seven recognised levels **plus a second `0.25` row with a different value** yields
  `reason == "unsupported_representation"` for the whole forecast. RED against a set-equality-only
  check, which would render an envelope whose `p25` depends on row order.
- **Wire shape:** the quantile entry is a `SapphireForecastQuantilesSchema` with
  `representation == "quantiles"` and `quantile_level_count == 7`; a member entry is a
  `SapphireForecastMembersSchema` with `representation == "members"` and `ensemble_size == 21`.
  Assert `not hasattr(quantile_entry, "ensemble_size")` **once**, as the lock on the split itself —
  the cross-field impossibility is otherwise carried by the types. Part of the v2 cutover; it cannot
  ship against the committed v1 schema.
- **Primary, single candidate:** a station whose only successful forecast is a quantile fallback
  renders that entry with `is_primary: true`.
- **Primary, mixed priority — the test that actually locks the rule.** One station, two assignments
  in `(priority, model_id)` order: **higher priority** = a stored *9-level* quantile forecast (the
  unrecognised set, so it must render `unsupported_representation`); **lower priority** = a
  recognised 7-level quantile forecast, rendered available. Assert the **lower** entry is the sole
  `is_primary: true` entry in `sapphire_forecasts[]` — RED against a "first non-`None` forecast"
  build, which the single-candidate test cannot distinguish.
- **Primary, per-`valid_time` divergence (the shared-predicate lock).** Same shape, but the
  higher-priority candidate is a *7-level* quantile forecast complete at every `valid_time` except
  one (so it renders `unsupported_representation` only under the per-`valid_time` rule), and the
  lower-priority candidate is complete. Assert the lower entry is the sole `is_primary: true` entry
  — RED against a pre-pass doing a coarser global level-set match, which passes the previous test.

*Verify:*
`uv run pytest tests/unit/services/forecast_lab/test_snapshot.py tests/unit/api/test_forecast_lab_schema.py -q`

**T3 — Version bump to `v2`, schema, fixture, spec.**

The answer to the version question is **bump** (decision 1). Concretely:

- `ForecastLabSnapshot.schema_version` (`forecast_lab_schemas.py:348`) →
  `Literal["forecast-lab-snapshot/v2"]`.
- `git mv docs/spec/forecast-lab-snapshot-v1.schema.json docs/spec/forecast-lab-snapshot-v2.schema.json`,
  regenerate it from `ForecastLabSnapshot.model_json_schema()`, and update `_SCHEMA_PATH`
  (`tests/unit/api/test_forecast_lab_schema.py:18`). **No v1 file is kept and no v1 surface is
  served** — the one consumer token has not integrated yet (see the trade-off note above).
- Regenerate `tests/fixtures/forecast_lab/forecast_lab_snapshot_example.json` so it carries an
  available `combined_forecast`, a quantile-representation entry, a `_pooled` key in
  `aligned_daily_comparison[].sapphire`, and `schema_version: "forecast-lab-snapshot/v2"`.
- **Fix the fixture's station 2091 identity — a live consumer already tripped on it (external
  review, SAPPHIRE-flow-map agent, 2026-08-27; this loop's three rounds all missed it).** The
  committed fixture pairs code `2091` with `"Chancy"` at `(5.99, 46.15)`, `basin_area_km2: null`.
  Real `2091` is **`Rheinfelden-Messstation`** at `(7.8, 47.56)`, `basin_area_km2: 34479.4`
  (measured against the live export the same day; `2009`/`Porte_du_Scex` is correct in both).
  Chancy is a real BAFU station under a *different* code, so this is not sanitisation — it is a real
  code wearing another station's identity, which is exactly the failure mode a consumer cannot
  detect by inspection. **Either** restore the true `2091` metadata **or**, if the sanitisation was
  deliberate, use an obviously non-production code (`9991`/`9992`) so no one can join on it by
  accident. Do **not** leave a real code with foreign metadata.
  *Exit:* a test asserting the fixture's station codes and names are mutually consistent, so this
  cannot silently regress on the next regeneration.
- Update the version strings in the route summary/description
  (`api/routes/forecast_lab.py:110`, `forecast-lab-snapshot/v1` → `/v2`) and the CLI argparse
  description (`cli/export_forecast_lab.py:155`).
- **Two consumer-visible `v1` identifiers a `forecast-lab-snapshot[/-]v1` grep does not match.**
  Both are emitted into the document body, so a `v2` document carrying them is self-contradictory:
  1. `_VERIFICATION_LIMITATIONS[0]` (`services/forecast_lab/snapshot.py:84-86`) reads *"Verification
     is not computed in v1 (Plan 111 gate G1 …)"*. **Make it version-neutral** — *"Verification is
     not computed in this release (Plan 111 gate G1 — no BAFU-derived benchmark before licence
     clarity)."* Version-neutral rather than `v2` on purpose: the sentence is about the Plan 111
     gate, not about the document version, so it should not need touching at every bump.
  2. `snapshot_id` is generated with an `fls1-` prefix
     (`services/forecast_lab/snapshot.py:581`). **Change it to `fls2-`** and update the two places
     that pin it: `tests/unit/services/forecast_lab/test_snapshot.py:755`
     (`assert snap1.snapshot_id == "fls1-20260821T104500Z"`) and
     `docs/spec/forecast-lab-snapshot.md:150` (`fls1-<generated_at, compact>`). The regenerated
     fixture (`tests/fixtures/forecast_lab/forecast_lab_snapshot_example.json:3`) picks it up
     automatically. The prefix is not declared version-independent: AC7 says "v2 end to end", and a
     3-line rename is cheaper than a documented exception.
- **Do NOT touch two unrelated `v1`s** — they are different version namespaces and must survive
  verbatim: the REST route prefix `/api/v1` (`api/routes/forecast_lab.py:1,55,138`) and
  **`VerificationSchema.method_version = "forecast-comparison/v1"`
  (`api/forecast_lab_schemas.py:313-318`, field at `:317`)**, which versions the *comparison method*,
  not the document. **`method_version` is on `VerificationSchema`, not on
  `ComparisonSemanticsSchema`** — the latter (`:139-146`) has only `variable`, `unit`,
  `display_run_rule`, `daily_aggregation`, `bafu_daily_completeness_minimum`,
  `observation_daily_completeness_minimum`, `sapphire_quantile_method`. `VerificationSchema` is a
  **per-station** block (`StationEntrySchema.verification`, `:339`), not a top-level one, so every
  assertion below is written against `snapshot.stations[0].verification.…`; `snapshot.verification`
  would raise `AttributeError` and lock nothing.
- `docs/spec/forecast-lab-snapshot.md`: the `combined_forecast` block and both `reason` values,
  **including that `no_combined_forecast` means "no exportable combined *discharge* forecast is
  available under the current strategy" — under `POOLED`/`BMA` the `parameter="discharge"` lookup
  returned nothing (a combined row for another parameter may still exist), under `CONSENSUS` the
  strategy is unsupported and no lookup is performed — and that the block follows the document's
  existing "latest available run" rule** (see T1); the **three roll-up
  semantics T1 changes** — a rendered combined forecast counts toward `availability.sapphire_forecast`,
  toward the `status.sapphire_forecasts` source status, and toward `latest_available_at`
  (`docs/spec/forecast-lab-snapshot.md:95-100` is the roll-up section that must say so); the
  **RECOGNISED-SET RULE** in consumer terms (defined once in T2 — do not re-derive its justification
  here or in the spec, just state the set, the per-`valid_time` scope, the exact lookup and the null
  `minimum`/`maximum`);
  `sapphire_quantile_method` narrowed to `representation: "members"` entries (`:33-45`); the four
  dropped levels (`0.05`/`0.10`/`0.90`/`0.95`) as a deliberate omission under option (a), **and that
  adding `p05`/`p95` later needs a `v3`, not an additive edit**; **that a quantile model with any
  other level set — including a 9-level model that clears
  `min_operational_quantile_levels` — still renders `unsupported_representation`** (see T2); the
  combined forecast's place in
  `aligned_daily_comparison` (`:74-82`); the **working CLI invocation**
  (`/entrypoint.sh python -m …`); and the `licence_status: "unresolved"` expectation.

*Exit:* the schema-drift test passes against the v2 filename; the regenerated fixture validates and
round-trips through `ForecastLabSnapshot.model_validate`; no `forecast-lab-snapshot/v1`,
`forecast-lab-snapshot-v1`, `fls1-` or `not computed in v1` string survives outside `docs/plans/`.
Plus **three exact assertions, because a grep is a hygiene check and not a contract** — a
`forecast-lab-snapshot[/-]v1` grep alone passes a document still emitting `fls1-…` and *"not computed
in v1"*:

- `snapshot.schema_version == "forecast-lab-snapshot/v2"`;
- `snapshot.snapshot_id.startswith("fls2-")` — replacing the literal at
  `tests/unit/services/forecast_lab/test_snapshot.py:755`;
- `"v1" not in snapshot.stations[0].verification.limitations[0]`, and
  `snapshot.stations[0].verification.method_version == "forecast-comparison/v1"` asserted
  **unchanged** in the same test, so the two `v1`s are not conflated by a later cleanup. Both live on
  the **per-station** `VerificationSchema` (`forecast_lab_schemas.py:313-318`, built by
  `_verification_sentinel()`, `snapshot.py:84-93`) — there is no `snapshot.verification`.

*Verify:*
`uv run pytest tests/unit/api/test_forecast_lab_schema.py tests/unit/api/test_forecast_lab_route.py -q && uv run ruff check src tests`

*Then, as a read-only drift check (expected: no output):*
`grep -rnE "forecast-lab-snapshot[/-]v1|fls1-|not computed in v1" src tests docs | grep -v "^docs/plans/"`

(The pattern deliberately does **not** match `/api/v1` or `forecast-comparison/v1`, both of which
must survive — see the T3 bullet above.)

## Acceptance criteria

1. On a `POOLED` deployment the snapshot renders **every** forecast the cycle produced — the three
   member models, both quantile fallbacks, and the combined forecast — and the combined forecast
   also appears in `aligned_daily_comparison[].sapphire` under its sentinel key.
2. **`combined_forecast` behaviour only** — AC2 does not claim a `primary` deployment is unchanged
   *overall*, because T2's quantile rendering changes it too: with
   `combination_strategy=PRIMARY`, `combined_forecast` is `available: false`,
   `reason: "strategy_primary"` **even when a stale `_pooled` row exists in the DB**, no sentinel
   key appears in any aligned row, and the build does not error.
3. A quantile forecast never yields a non-null `minimum`/`maximum`, and its `p25`/`median`/`p75` are
   the exact stored `0.25`/`0.50`/`0.75` values (T2 only).
4. `combination_strategy` and `source_model_ids` reproduce the persisted DB row **exactly** — the
   export makes no claim about which model contributed to which parameter.
5. No regression in the Plan 198 acceptance suite, including
   `test_quantiles_representation_yields_explicit_unavailable`
   (`tests/unit/services/forecast_lab/test_snapshot.py:346-390`), which must still assert
   `unsupported_representation` for its 9-level forecast.
6. **The count field belongs to the representation, and the wrong pairing is unrepresentable**: a
   `representation: "quantiles"` entry carries `quantile_level_count` and **no** `ensemble_size` key
   at all; a `representation: "members"` entry carries `ensemble_size` and **no**
   `quantile_level_count` key. No entry reports a member count it does not have, and no builder can
   construct one that does (the FORBID-EXTRA RULE). **This holds for
   `combined_forecast` too, not only for `sapphire_forecasts[]`** — the block emits
   `representation: "members"` with `ensemble_size` equal to the stored row's member count.
7. The document is `forecast-lab-snapshot/v2` end to end — literal, committed schema filename,
   fixture, spec, both callers' descriptions, **the `snapshot_id` prefix (`fls2-`) and a
   version-neutral verification-limitation sentence** — and the drift test compares against the v2
   file. **Out of AC7's scope, deliberately:** the `/api/v1` route prefix and the per-station
   `verification.method_version: "forecast-comparison/v1"` (`VerificationSchema`,
   `forecast_lab_schemas.py:317` — *not* `ComparisonSemanticsSchema`), which version different
   things and stay as they are.
8. Exactly one `sapphire_forecasts[]` entry carries `is_primary: true` whenever the station has at
   least one *renderable* forecast, and it is the highest-priority renderable one — never a
   higher-priority entry that the builder rendered unavailable.
9. **Every `ModelCombinationStrategy` value is dispatched explicitly:** `BMA` exports the `_bma` row and never the `_pooled` one even when both are
   stored; `CONSENSUS` exports `available: false` / `no_combined_forecast` even when `_pooled` and
   `_bma` rows are stored; no sentinel key from a non-selected strategy ever reaches
   `aligned_daily_comparison[].sapphire`. "Performs no lookup" is an implementation note in the
   `match` arm, **not** an acceptance criterion — it has no observable consequence in the document,
   so it is not tested (see the T1 exit note).
10. **A rendered combined forecast counts as a SAPPHIRE forecast in the roll-ups:** it
    sets `availability.sapphire_forecast`, feeds `status.sapphire_forecasts.status`, and
    participates in `status.sapphire_forecasts.latest_available_at` — including on a station whose
    assigned models produced nothing renderable, where the combined row is the *only* SAPPHIRE
    forecast present.

## Not in scope

The `_pooled` row's `qc_status` is `raw` while every individual forecast is `qc_passed`
(**re-confirmed by direct query 2026-08-27T16:0xZ**: `_pooled | members | raw | pooled |
["nwp_regression","nwp_rainfall_runoff","linear_regression_daily"]`, alongside five `qc_passed`
rows). That may be correct — a combined forecast may not be QC'd by design — or a gap in Plan 026.
**Establish which before exporting `qc_status` for it**; do not paper over it here. Note the
asymmetry this creates for T1: the block the map would treat as the deployment's *best* forecast is
also the only one carrying no QC verdict. That is why the T1 block carries no `qc_status` field at
all — an absent field is honest; a `"raw"` the map cannot interpret is not.

## Dependency graph

T1 and T2 both edit `api/forecast_lab_schemas.py` and `services/forecast_lab/snapshot.py` — the same
nested-discriminated-union pattern (`available` × `representation`, applied to the combined block in
T1 and to the per-model entry in T2) and the same `_sapphire_entries`/`build_snapshot` region — so
they run **sequentially**, not in parallel. T3 regenerates the schema and fixture from the finished
models and therefore depends on both. **Both tasks are inside the one v2 cutover** — T2 cannot ship
alone against `v1` (see the box in T2), so this graph is the only shipping order, not merely the
convenient one.

```json
{
  "phases": [
    {
      "id": "phase-1",
      "tasks": ["T1"],
      "parallel": false
    },
    {
      "id": "phase-2",
      "tasks": ["T2"],
      "parallel": false,
      "depends_on": ["phase-1"]
    },
    {
      "id": "phase-3",
      "tasks": ["T3"],
      "parallel": false,
      "depends_on": ["phase-2"]
    }
  ]
}
```

## Appendix A — review provenance

Kept out of the task bodies on purpose: the sections above state only the current rule. This is the
trail, one line each, for anyone re-checking how a decision was reached.

- **Rounds 1-3** (`plan` workflow, 20 agents, Codex an independent reviewer every round) fixed the
  contract: `v2` not `v1`; quantile entries are not ensemble entries on the wire; the combined block
  is strategy-gated and always-present-and-discriminated; the combined forecast joins the daily
  comparison; the exhaustive `ModelCombinationStrategy` dispatch and its `BMA` locking test; the
  combined-only roll-up lock; the shared `_is_renderable` predicate; the duplicate-quantile-level
  rule. The loop terminated on `maxRounds`, not on convergence.
- **Round 4** (verification pass over the folded text) replaced the tag-plus-nullable-counts wire
  shape with the nested discriminated union, retracted the claim that T2 was separately shippable
  against `v1`, weighed the third-`sapphire_forecasts[]`-variant alternative on its merits, dropped
  the `CONSENSUS` recording-fake spy test as locking an unobservable, and named the RECOGNISED-SET
  RULE once.
- **Round 5** added the `Protocol` type contract for the shared rendered-sources list, corrected the
  REST-route dependency citation to `:135`, and collapsed this review history out of the task bodies
  into this appendix.
- **Two errors the loop produced and a later round caught**, worth remembering when reviewing the
  citations here: round 2 named `ComparisonSemanticsSchema.method_version` (the field is on
  `VerificationSchema`), and round 1 cited `min_operational_quantile_levels` as support for exact
  level-set matching (it is a count floor). Check a cited `file:line` before relying on it.
- **One finding no round produced:** the fixture's station-2091 identity, from the SAPPHIRE-flow-map
  consumer agent. Structure reviews do not catch a real code wearing another station's metadata.
- **Round 6 — a manual `codex exec` pass, run because rounds 4-5 had NO working Codex reviewer.**
  Both automated Codex calls that run died at the 10-minute cap in `Reading additional input from
  stdin` (exit 143): `.claude/workflows/plan.js:279` carries the mandatory `< /dev/null` only as
  *prose instructing an agent*, and the agent omitted it. So rounds 4-5 were Claude-only and did not
  meet the CLAUDE.md independent-review floor, despite the run reporting normally. The manual pass
  (`codex exec --sandbox read-only … < /dev/null`, exit 0) then found **two majors five Claude-only
  rounds had missed**: the `Protocol`'s `UtcDatetime` annotation, which pyright strict rejects, and
  a propagation test that is green against the bug it targets. It also **confirmed** the nested
  discriminated union under the locked Pydantic 2.12.5, the `isinstance` subclass behaviour, and
  that the `BMA` and roll-up locks genuinely fail their targeted implementations. Verdict:
  `NEEDS_CHANGES`, no blockers. **Lesson: a green `plan` run is not evidence Codex reviewed
  anything — check `codexFailedRounds` before trusting the round.**
