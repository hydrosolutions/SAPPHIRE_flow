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

## Gap 1 — quantile models render as unavailable

D5 built a deliberate guard: a `representation != members` forecast returns
`"unsupported_representation"` rather than relabelling outer quantiles as `minimum`/`maximum`. **The
guard is correct and is doing its job** — this plan does not weaken it.

But D5's stated premise was *"All 232 stored forecasts on the mini are `members` (verified), so
quantile support is not built."* That was true when written and **POOLED made it false the same
week.** Both fallback-tier models emit 7 quantile levels and 0 members.

**Decision needed (owner):** are the fallback models worth showing at all? They are the
*floor* — a research comparison UI arguably wants them precisely as the baseline everything else
must beat. If yes, map the stored quantile levels onto the envelope **without inventing the tails**:
`p25`/`median`/`p75` come from real stored levels; `minimum`/`maximum` must be **`null`**, because a
7-level quantile forecast does not contain the ensemble extremes and fabricating them is the exact
error the guard exists to prevent. If no, keep the guard and close this gap as WONTFIX.

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

**T2 — Quantile envelope, only if the owner says yes.** Map stored levels to `p25`/`median`/`p75`;
`minimum`/`maximum` stay `null`. **Locking test: a 7-level quantile forecast must NEVER produce a
non-null `minimum` or `maximum`** — proven RED against an implementation that uses the outer levels.
*Exit:* the above, plus the D5 guard still firing for any representation that is neither `members`
nor a recognised quantile set.

**T3 — Schema, fixture, spec.** Regenerate `forecast-lab-snapshot-v1.schema.json`, extend the
committed fixture to include a combined forecast, update `docs/spec/forecast-lab-snapshot.md`.
*Open question for review:* both changes are **additive**, so `v1` should still be honest — but if
the reviewer judges a consumer could break, say so and bump rather than argue.
*Exit:* the schema-drift test passes; the fixture validates.

## Acceptance criteria

1. A POOLED deployment renders **all** models the cycle produced, plus the combined forecast.
2. A `primary` deployment is unchanged — `combined_forecast` absent with a reason, no error.
3. A quantile forecast never yields a non-null `minimum`/`maximum` (T2 only).
4. `source_model_ids` matches the DB exactly, including the fallback-tier exclusion.
5. No regression in the Plan 198 acceptance suite.

## Not in scope

The `_pooled` row's `qc_status` is `raw` while every individual forecast is `qc_passed` (observed
2026-08-27). That may be correct — a combined forecast may not be QC'd by design — or a gap in Plan
026. **Establish which before exporting `qc_status` for it**; do not paper over it here.
