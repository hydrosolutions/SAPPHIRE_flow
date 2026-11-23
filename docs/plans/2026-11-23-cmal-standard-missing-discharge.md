# Standard CMAL issuance with declared missing discharge

status: READY

## Evidence and outcome

Nepal runs 0.1.1181. Its static encoder declares 365 past discharge steps and
`max_nan=365`; all four weather inputs have zero NaN tolerance. Live weather
selection resolves, but the provisional-rating route encounters historical water
levels without capture evidence. These remain hard failures: no rating evidence,
custody, validity, identity or reconciliation check may be weakened.

Use the standard measured-observation route for this model. Its empty discharge
history currently lacks the required column and is rejected before the FI gate.
Represent missing past discharge on its actual UTC daily calendar, then let the
existing FI gate enforce the declared tolerance. This is not imputation and must
not pad future weather, invent observations or count missing data as complete.

The standard cycle also discovers point-input climatology models, which cannot
share the selected basin-weather preparation route. Add explicit model selection
to the existing cycle so a normal deployment can run static CMAL independently.

## Bounded implementation

1. In operational input assembly, normalize past discharge for standard UTC-daily
   **FI group** models with an explicit discharge-only past-target declaration.
   Use a separate standard-route predicate (rating inputs absent; no requirements
   override; model-owned discharge-only declaration and GroupInputGate). Build the
   complete [past_targets_start, past_targets_end) UTC daily calendar, then left-join
   validated observed labels and fill missing cells with Float64 NaN. Reuse label
   validation, not the provisional helper’s observed-span calendar. Retain real values,
   NaNs, declared per-feature lookbacks, observation QC/freshness facts and units.
   Preserve target lookbacks in the existing FI requirements projection and trim
   the normalized standard target calendar to its declared window. Read target
   observations only within that window so QC, evidence and short-lookback checks
   describe consumed inputs. Mark normalized-window metadata explicitly so the
   new coverage diagnostic cannot affect other routes.
   Wholly absent and partial daily discharge must retain an observation coverage
   quality flag; extend the existing discharge diagnostic to this declared daily
   route without creating provisional lineage. Never enter the rating reader in a standard run. Preserve current provisional
   hard-failure behavior and all other model/cadence/target routes.
2. Add an optional `model_ids` list at the forecast-cycle boundary, for standard
   runs only. None preserves existing discovery. Reject an empty list, duplicates,
   malformed/unknown IDs and use with provisional purpose. Convert validated IDs
   to ModelId. Validate structure/purpose before store setup; validate unknown IDs
   after model discovery but before writes. Reject excluding a configured daily
   evaluation model so an independently scoped run cannot alter its admission. Filter models and active assignments
   in memory before preparation, priorities, combination, group discovery and
   execution. For scoped group-only runs, pass the operational group pairs to
   both weather preparation and horizon negotiation; never derive eligibility
   from available weather. Preserve raw all-status assignments for drift checks. Stations with
   no selected assignment skip without failure. Do not alter stored assignments or promotion.
3. Test through public assembly/cycle behavior: absent and partial observed
   discharge reach a tolerant FI model as correctly aligned missing values;
   leading/trailing gaps count toward max_nan and stricter max_nan prevents predict; per-feature Q7/weather30 windows remain
   correct; standard output has no provisional lineage. Preserve non-FI, other
   cadence/target and rating-evidence failure tests. Test standard model selection
   excludes unrelated point climatology preparation, defaults remain unchanged,
   invalid selections fail before writes, higher-priority unselected assignments do
   not fail a station, and real selected-weather receipts and
   horizon negotiation remain intact.
4. Update onboarding/touchpoint docs. Run focused regressions, lint, changed-file
   types and full CI after final code changes. Obtain one independent Claude patch
   review; keep reports local. Hold at owner merge and release approval.

## Operational follow-through after reviewed release

Qualify a normal standard deployment with `model_ids=["cmal_nepal_static_encoder"]`
using the existing four-channel selected-weather configuration and native model
artifact. Preserve the 17 operational / four onboarding station split. Verify
finite ordered quantiles, actual horizon, standard purpose, missing-input flags,
API listing/detail access, memory, and recurrence. Keep climatology and CMAL-small
deployments intact. Restore the unused forecast-only 365-day rating override and
remove static CMAL from provisional opt-ins after safely draining its failed
qualification; leave scientific provenance unknown and station statuses unchanged.

## Contract and review

FI protocol, InputRequirement, ModelResult and model/input docs were checked.
Past-known max_nan is enforced before predict and missing values within tolerance
reach the model unchanged. Future AT_MOST short-tail rules remain untouched.
No FI contract or model requirement changes are proposed.

Independent Claude plan review completed; full report stays local. Its three
blocking findings are incorporated above: full calendar alignment, separate
standard-route eligibility, and assignment filtering. Validation timing and
missing-input flags are explicit. Orchestrator marked READY after resolving them.

```json
{"phases":[{"id":"implementation","depends_on":[]},{"id":"validation","depends_on":["implementation"]},{"id":"review_pr","depends_on":["validation"]},{"id":"deployment","depends_on":["review_pr"],"gate":"owner merge and release approval"}]}
```
