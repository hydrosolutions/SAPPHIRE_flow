# Correct false spike flags before scientific reruns

status: READY

Independent Claude plan review completed November 24; orchestrator resolved the
findings below before implementation. Full review remains local at
`/tmp/spike-qc-plan-review.txt`. Owner retains PR merge and deployment approval.

Related: #548, #538. Owner authorized correction, selective historical repair,
then an impact assessment before any retraining or hindcast rerun. Preserve the
approved Mac mini BAFU outlier-policy change and unrelated local configuration.

## Evidence

`services/qc.py::_apply_spike` compares the middle observation with both neighbours
but does not require a local extremum. Therefore a sufficiently steep monotonic
rise or fall is classified as a spike. Stored flag details demonstrate this for
31,317 historical imported BAFU discharge rows and 123 measured rows on November
24. Historical counts split into 26,096 training, 1,040 validation/boundary and
4,181 evaluation rows. These are flags, not yet proven recoverable observations:
other flags, source protection and current values still govern eligibility.

## S1 — Small shared algorithm fix

After existing neighbour/value availability checks and the relative-mode
zero-reference guard, return judged/no flag when
the middle value lies within the closed interval between its two neighbours.
Both relative and absolute spike modes then require a strict extremum before
their existing magnitude test. Preserve thresholds, zero-reference behavior,
missing-context behavior and real positive/negative spikes. No interpolation,
range-limit change or rate-of-change relaxation. This shared mathematical
correction applies consistently to all deployments; no Nepal server access or
Aquacast change is required.

Tests use the public checker for rising/falling ramps, flat/tied values and real
peaks/troughs in both modes; verify judged coverage so a cleared false spike does
not become unchecked. Include a recorded monotonic example. Run QC/ingest and
onboarding tests, lint/types, then full CI before owner merge. Independent patch
review is required. Update the QC touchpoint documentation and use the release
identity to distinguish corrected algorithm behavior; do not silently rewrite
operator-defined rule versions or custom configurations.

A strict extremum is `value > max(prev,next)` or `value < min(prev,next)`.
Do not redefine source grouping: duplicate-time neighbours make a temporal spike
unevaluable (require prev.time < time < next.time). Test this, null neighbours,
and existing QC segment boundaries through the public coverage checker.

## S2 — Selective BAFU repair

Use the existing backed-up, guarded QC-repair procedure from the November 24
outlier repair, with independent review of the concrete repair scope. Do not
blanket reset statuses or re-run all QC rules against a new baseline.

Preview candidate rows where a stored spike flag explicitly records a middle
value between its two neighbours. Parse only the known exact detail format;
require finite values, agreement with the current observation value, and an
actual false-spike flag. Preserve malformed/unrecognized cases for review.
Reconfirm the current source-series neighbours for historical rows where those
neighbours are recoverable; unrecoverable/disagreeing contexts are held, not
promoted from the detail string alone. Match the original source-filtered reader
and segment boundaries; ambiguity or duplicate timestamps is held. Restrict writes
to BAFU discharge and explicitly named sources and periods.

Back up original rows and configuration, acquire the existing station/parameter
QC locks in stable order, compare the full original row before writing, remove
only the proven false spike flag, and preserve other flags and measured values.
Reapply the effective range check before promoting any previously suspect row;
unknown flag severities or snapshot changes abort. Keep protected-source/read
holds intact. Store a repair-version marker and transactional audit with counts,
backup hash and affected station/time ranges. Validate the transaction with
rollback before committing. Keep historical repair and any measured-row repair
separate so recurrence under the old deployed algorithm is not hidden.

The corrected spike test and the effective range test must both judge each
repair target. Existing aggregation marks a row passed when at least one rule
judged it and no flags remain; it does not require every rule to judge it. A
retained failed/suspect flag still determines status. Stamp
`qc_rule_version='1.2-spike-extremum-1'` and retain original flag detail in the
backup. Report reconfirmed, held, malformed and promoted counts separately.
Do not repair measured rows before deployment or route repaired historical rows
through the old algorithm.

No historical write waits on a costly model run. Live measured-row repair and
prevention require the corrected code deployed after owner merge/authorization.
Preserve the already-approved BAFU-only seasonal-outlier policy; do not restore
that rule while deploying this patch.

## S3 — Decide the necessary reruns

Use the actual repaired/now-eligible rows, not the initial flag count. Intersect
their station/time ranges with the fine-tuning fitting/validation/evaluation
periods and declared model input lookbacks. Distinguish:

- changed fitting targets: candidate training may need repeating;
- changed hindcast target inputs: forecasts may need regenerating;
- only changed verification pairs: rescore stored hindcasts without retraining;
- unchanged/unavailable rows: no rerun justified by this repair.

Prove that the relevant model's pipeline excluded/masked the repaired observations
before claiming input changes. A flag on an included row does not justify a rerun.

Do not assume climatology and CMAL consume the same inputs. Preserve run/artifact
identities and label prior evaluation as affected until the audit settles it.
Use the merged version-3 quantile scorer for eventual new skill generations.
No automatic candidate acceptance or activation and no new expensive run in this
patch. Existing rate-of-change flags need separate context review; none are
blanket-cleared. Then resume thresholds #630 and administrator review #454.

```json
{"S1": [], "S2": ["S1"], "S3": ["S2"]}
```
