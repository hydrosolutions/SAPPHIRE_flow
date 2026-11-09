# Forecast-specific rating-derived discharge input history

## Approved outcome

Give an authorized forecast reviewer a read-only view of the rating-derived daily
discharge used in SAP input assembly for one retained provisional forecast. The owner
approved the narrow security-boundary projection needed for actual discharge and
minimal curve context on 2026-11-09, with delivery prioritized for tomorrow. A
metadata-only view is not completion. This standalone addendum does not expand the
network-overview implementation or its threshold-conversion scope.

For the seven completed UTC days immediately before the selected forecast's actual
`issued_at` UTC date, expose the reconstructed rating-derived daily discharge where
frozen evidence exists, its unit (`m3/s`), daily resolution, source-support quality,
actual extrapolation flags, and minimal frozen curve identity and validity. Anchor
the interval to the forecast, never the current clock. Preserve the distinction
between daily aggregate and native sample support. Mark an absent derived day as
`no_retained_derived_day`, never as proof of missing model input. Measured-discharge
days are not reconstructed. Missing/corrupt evidence is an error, not a fabricated
value, empty success or fallback to today's observations.

Label values **rating-derived daily discharge used in SAP input assembly**. Do not
claim an exact final model tensor or complete model-input history. The retained
lineage records derived-support days, not all measured days, assembly bounds or
preprocessor outputs. Input resolution here is daily, not invented hourly values.

## Frozen evidence and scientific invariants

Use only immutable rating-derived rows linked by the selected forecast's frozen
lineage. Reproduce its stored daily aggregation policy from the stored discharge
values, accepted/rejected samples and warnings. Validate the complete selected-day
evidence before returning that day's result. Do not derive discharge again from
water levels or current curves. Include frozen curve identity/content identity and
validity interval; multiple curves in one day must remain distinguishable. Keep
measurement-time expiry separate from issue-time expiry and retain unassessed QC,
sparse support and assumed-reference warnings where present. No raw provider/feed,
reference payload or full source observations need leave the boundary.

Ordinary rating conversion clamps outside its curve. The existing provisional
conversion instead linearly extrapolates endpoints and rejects negative/nonfinite
results. Display the actual frozen extrapolation behavior. Do not change this
scientific policy or retroactively describe those forecasts as non-extrapolated.
This is unrelated to in-range-only conversion of reported thresholds.

## Narrow security contract

Add an explicitly reviewed forecast-specific read contract for reviewer/admin
principals. Authenticate and authorize the forecast's tenant/station and provisional
purpose using safe parent fields **before** reading lineage or derived evidence.
Consumer principals are denied; unauthorized and absent forecast identities remain
indistinguishable. Preserve existing principal scope and role rules; do not introduce
new user grants, general protected-table SELECT, owner credentials or API writes.

The owner authorizes a narrow new bounded database projection/migration for this
outcome, subject to independent security review. It accepts only a forecast ID,
not an arbitrary time range, station selector or fingerprint list. Preserve the
existing `native_rating_lineage` function unchanged. Use fixed search paths,
least-privilege execution, bounded input/body/output work and strict digest,
identity, count and policy checks. The application must perform request-scope
validation first; a database function does not itself know bearer-token scope.
Missing privileges/evidence and malformed frozen data fail closed with safe errors.

Keep the existing provisional list/detail `rating_provenance_status=not_exposed`
contract unchanged. Ordinary observation routes are not a `data_use` switch.
All new responses, errors and supported preflights must be `no-store` under the
existing API policy. No publication changes, writes, locks for publication selection,
or new broad browsing routes are authorized.

## Delivery and evidence

Deliver a minimal complete backend vertical slice: bounded projection, typed reader
and API contract, tests and affected documentation. No frontend work, threshold
work, model/FI changes, tensor persistence, ingestion/reconciliation changes, fresh
forecasts, training, backfills or provider calls. Coordinate shared forecast-reader,
authorization, touchpoint and release-target changes with the overview owner; do
not modify its branch or fold this work into overview A.

Acceptance must demonstrate authorized actual daily Q and curve context against
retained representative local fixtures, not just all-null responses. Test seven-day
UTC boundaries; measured precedence and absent-day meaning; sparse/rejected/
unassessed support; multiple curves; actual above/below extrapolation and expiry;
replay unaffected by changed current curves; digest/count/tenant/day mismatch;
unsupported policy and missing rows; bounded failure; consumer and cross-scope
denial before protected calls; unchanged ordinary/provisional contracts; denied
raw table access; no GET writes; and no-store success/errors. Real database-role
integration tests must prove the new least-privilege boundary, not only mocks.

The root commissions independent Claude, Codex and relevant security reviews and
disposes findings before source implementation. The owner approves and merges
publication and implementation PRs. This vision requires its own publication PR;
source implementation waits for accepted target-branch intent and the root's review
disposition. Local planning may proceed alongside publication. No live migration,
deployment or operational readback is authorized here: each needs a separately
bound approval for its exact target and action. Do not claim tomorrow's deployment
from a draft, open PR or passing local fixture alone.
