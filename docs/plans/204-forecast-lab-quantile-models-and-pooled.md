---
status: DRAFT
created: 2026-08-27
plan: 204
title: Forecast Lab shows 3 of 6 forecasts once POOLED is on — quantile models and `_pooled` are both invisible
scope: Two additions to the existing forecast-lab-snapshot/v1 contract so the export renders what the deployment now produces. No new tables, no change to the forecast cycle, no schema version bump unless the review says one is required.
depends_on: [198]
blocks: []
source: Surfaced 2026-08-27 by the `forecast_combination_strategy = pooled` trial (PR #214)
---

# Plan 204 — the Forecast Lab cannot see half of what the cycle now produces

## Status

**DRAFT.** Not for implementation until the owner confirms.

## ⛔ Proportionality

**Two gaps, both narrow.** Neither is a bug in what Plan 198 shipped — both are consequences of a
config change made after the contract was written. Do not reopen Plan 198's settled decisions
(F3's percentile orientation, the cut T4/T9b/T10/T11, `licence_status`, the deviation table).
Reviewers: "no findings" is a complete review.

### Reviewers: DO NOT OVER-ENGINEER THIS PLAN (owner instruction, 2026-08-27)

This is a **three-task, additive change to an export that already works in production** — verified
end-to-end on real data the same day (see the next section). It is not an architecture round. The
owner's explicit instruction for this review is to **hold the scope**, and a review that grows the
plan is a worse review than one that finds nothing.

**In scope for findings:** the T1/T2/T3 contract shape is wrong or ambiguous; a stated fact is false;
an acceptance criterion does not actually lock its behaviour; a locking test would pass against the
buggy implementation; the change breaks an existing `v1` consumer.

**Explicitly OUT of scope — do not propose, and reject if proposed:**

- New abstractions, registries, strategy objects or plug-in points for "future representations".
  There are exactly two representations (`members`, `quantiles`) and a CHECK constraint enforcing it.
- Generalising the quantile mapping beyond the measured level set. Handle the levels this deployment
  stores; fall back to the D5 guard otherwise. That fallback IS the generality.
- Backfill, migration, recomputation, or any change to the forecast cycle, Plan 026 combination, or
  the models themselves. This plan reads what the cycle already wrote.
- Performance work. The export is 2 stations and 364 KB, built in under a second.
- Reopening `v1` versus `v2`. T3 already asks the single honest question; answer it yes or no.
- Verification metrics, CRPSS, thresholds, or anything from the Flow Map integration audit. Plan 111
  G1 gates that and the licence letter is unsent.
- Expanding the `p05`/`p95` fork below — the owner has decided it (option (a)).

**If a reviewer believes a genuinely blocking problem sits outside these bounds, say so in one
sentence and stop there** — do not design the fix into this plan.

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
   order-statistic path.
2. **`0.05`/`0.95` are NOT the extremes** — which is exactly why they cannot fill `minimum`/
   `maximum`. A 5th-percentile value rendered as "minimum" would read on the map as the lower bound
   of the forecast, understating the tail by construction. This is the concrete form of the error
   D5's guard was built to prevent, and the reason AC3's locking test matters.

**Residual fork — RESOLVED by the owner 2026-08-27: option (a), `v1` envelope unchanged.** the `v1` envelope has **no slot for the
0.05/0.90/0.10/0.95 levels**, so mapping to `p25`/`median`/`p75` silently *discards four of the
seven stored levels* — including the widest band the fallback models publish. Options: (a) accept
the loss, `v1` envelope unchanged, the map shows a narrower band for fallbacks than the model
actually emits; (b) extend the envelope with optional `p05`/`p95` (additive; `null` for member
forecasts unless we also compute them there, which is trivial and arguably better). **The owner chose (a) — accept the loss, ship the
comparison the map asked for — explicitly "for now, may change later".** So: do NOT add `p05`/`p95`
in this plan, and reviewers must not reopen it. Two obligations follow from the "may change later",
and they are the whole of what (a) costs:

1. `comparison_semantics` must state that a quantile forecast's envelope is built from **stored
   levels** and that its extremes are **unavailable**, so a consumer never reads a `null` `minimum`
   as a data error or as a zero.
2. The discarded levels must be recorded as a **known, deliberate omission** in
   `docs/spec/forecast-lab-snapshot.md` — naming the four dropped levels — so that revisiting this
   is a documented one-line change and not a rediscovery. Adding `p05`/`p95` later stays additive
   and does not force a `v2`; note that too.

## Gap 2 — `_pooled` is structurally invisible

The snapshot iterates the station's **assigned models** (D17b, `fetch_active_model_assignments`).
`_pooled` is an `artifact_scope = 'virtual'` sentinel with **no assignment row**, so no iteration
ever reaches it — regardless of representation.

This is the forecast a comparison UI would most want: a 92-member combined ensemble over the three
skill-tier models, with `source_model_ids` recording exactly which contributed. It is also the
deployment's best forecast under the Plan 026 design.

**Shape (proposed, review it):** a sibling `combined_forecast` block rather than a seventh entry in
`sapphire_forecasts` — it is not a model, has no artifact, no `is_primary`, and carries
`combination_strategy` + `source_model_ids` that no per-model entry has. Forcing it into the model
list would mean nullable fields on every ordinary entry.

## Tasks

**T1 — Expose `_pooled` as a `combined_forecast` block.** Fetch the latest `_pooled` forecast per
station (sentinel `model_id`, not via assignments), render it with `combination_strategy` and
`source_model_ids`, absent-with-reason when the deployment is on `primary`.
*Exit:* `uv run pytest tests/unit/services/forecast_lab/ tests/unit/api/` green, plus a test proving
a `primary`-mode deployment still renders (the block is absent, not an error).

**T2 — Quantile envelope. IN SCOPE (owner confirmed 2026-08-27).** Look up the stored `0.25`/
`0.50`/`0.75` levels into `p25`/`median`/`p75`; `minimum`/`maximum` stay `null`. Exact lookup, not
interpolation — do not route a quantile forecast through the member order-statistic path.
**Locking test: a 7-level quantile forecast must NEVER produce a non-null `minimum` or `maximum`** —
proven RED against an implementation that uses the outer levels (`0.05`/`0.95`), which is the
plausible wrong implementation a reviewer would otherwise wave through.
*Second locking test:* a quantile forecast **missing** any of the three required levels must fall
back to the D5 guard, not emit a partial envelope — the set above is what this deployment stores
today, not a contract the models are bound to.
*Exit:* the above, plus the D5 guard still firing for any representation that is neither `members`
nor a recognised quantile set.

**T3 — Schema, fixture, spec.** Regenerate `forecast-lab-snapshot-v1.schema.json`, extend the
committed fixture to include a combined forecast, update `docs/spec/forecast-lab-snapshot.md`.
Also document the **working CLI invocation** (`/entrypoint.sh python -m …`, see the Plan 198
verification above) and the `licence_status: "unresolved"` expectation, so an operator and the map
team both stop at the spec.
*Open question for review:* both changes are **additive**, so `v1` should still be honest — but if
the reviewer judges a consumer could break, say so and bump rather than argue.
Record the four dropped quantile levels (`0.05`/`0.10`/`0.90`/`0.95`) as a deliberate omission
under option (a), and state that adding `p05`/`p95` later would be additive.
*Exit:* the schema-drift test passes; the fixture validates; the documented CLI command is the one
that actually works on the mini.

## Acceptance criteria

1. A POOLED deployment renders **all** models the cycle produced, plus the combined forecast.
2. A `primary` deployment is unchanged — `combined_forecast` absent with a reason, no error.
3. A quantile forecast never yields a non-null `minimum`/`maximum` (T2 only).
4. `source_model_ids` matches the DB exactly, including the fallback-tier exclusion.
5. No regression in the Plan 198 acceptance suite.

## Not in scope

The `_pooled` row's `qc_status` is `raw` while every individual forecast is `qc_passed`
(**re-confirmed by direct query 2026-08-27T16:0xZ**: `_pooled | members | raw | pooled |
["nwp_regression","nwp_rainfall_runoff","linear_regression_daily"]`, alongside five `qc_passed`
rows). That may be correct — a combined forecast may not be QC'd by design — or a gap in Plan 026.
**Establish which before exporting `qc_status` for it**; do not paper over it here. Note the
asymmetry this creates for T1: the block the map would treat as the deployment's *best* forecast is
also the only one carrying no QC verdict.
