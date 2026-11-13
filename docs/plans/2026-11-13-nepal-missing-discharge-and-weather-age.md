# Nepal missing-discharge forecasts and 60-hour weather policy
status: READY

Independent Claude review completed. Owner approved ordinary-category outputs
without rating-derived inputs. Orchestrator releases all three items with the
bounded routing below; database and store purpose guards remain unchanged.

Owner scope: prepare a minimal patch for the Infomaniak MVP; do not deploy.

1. Change the Nepal overlay's selection and actual-age limits from 36 to 60 hours.
Retain four days of NWP grids, required by the existing fallback-window validator.
Preserve 00 UTC selection, complete P/T windows, and weather timestamps. Update
runbook and focused tests.
2. Missing discharge must not veto models whose FI declaration permits all discharge values missing. Retain rating reader identity/reconciliation failures. When no rating samples contribute, do not fabricate lineage. Preserve accepted measured Q. Represent completely missing past Q explicitly for the daily lookback and keep FI max_nan as the admission gate. Do not pad partially delivered series or relax weather. No model/checkpoint change.
3. In the existing cycle, no-rating outputs use STANDARD with no input lineage;
actual rating consumers remain PROVISIONAL_RATING. Construct ordinary success
and rejection stores alongside the existing stores. Route only ordinary outputs
of a provisional cycle to those stores, including QC rejections. Inject the two
stores in tests. Preserve aggregate counters, evidence and lineage guards. The
existing API serves these outputs under STANDARD; consumers filtering only
PROVISIONAL_RATING must also request STANDARD. No scheduler, database migration,
new output type, or relaxed store-purpose check.

Validation: missing-all Q admitted only by tolerant model; strict model refused; measured-only accepted; rating-present unchanged; hard identity holds unchanged; weather gaps refused; mixed-group persistence and QC-rejection routing; 60-hour boundary and unchanged defaults. Focused tests, Ruff, changed-module types; full suite before merge. Independent review before implementation; no deployment or merge.

## Phases

```json
{"phases":[{"id":"weather-policy","depends_on":[]},{"id":"discharge-and-routing","depends_on":[]},{"id":"validation","depends_on":["weather-policy","discharge-and-routing"]}]}
```

## Prepared patch

Both implementation phases are complete. Independent patch review identified
an overbroad no-rating fall-through; the patch now preserves the skip for ungated
and non-discharge-only models. The final focused run passed 94 tests; the broader
run before this restriction passed 269. Ruff and formatting pass. Changed-module
type checking reports the same 13 existing errors as the base operational-input
module, outside changed lines. Review reports remain local. Full-suite/CI and
owner approval remain required before merge. Final independent review found no
blockers; surrounding-code checks confirmed the daily normalization guard,
purpose-scoped PostgreSQL stores, and no post-write lookup through the wrong
store. The provisional cycle still does not emit alerts. No deployment was made.
