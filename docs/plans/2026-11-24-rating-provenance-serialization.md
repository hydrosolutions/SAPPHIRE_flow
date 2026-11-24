# Preserve rating provenance with cheaper serialization

status: READY

## Problem and scope

A synthetic inventory of 99,999 observations across three station-years reaches
its 30-second CPU budget on native Linux before provenance closure finishes.
An instrumented follow-up attributes substantial rating-derivation time to
`dataclasses.asdict` recursively copying UUIDs, timestamps and other metadata
immediately before JSON serialization (about 9.0s cumulative inside 17.5s of
derivation for an instrumented partial sample of about 16,800 conversions).
Profiling overhead is not qualification
evidence; the original limits and complete fixture remain the acceptance test.

Keep the canonical bytes, digests, numeric results, rejections, warnings, evidence
guards and excluded transient QC fields unchanged. No migration, configuration,
scientific-policy, FI, authentication, scheduling or deployment change. No cache,
new dependency or alternative derivation path.

## P0 — Freeze the old outputs

Before editing `src/`, capture deterministic synthetic content and fingerprints
with the unchanged implementation. Commit those goldens and their input recipe
first. Cover custody/native feeds, verified/assumed references, QC outcomes,
nonfinite values, extrapolation and retained curve metadata. Preserve the existing
historical golden fixtures. These outputs, rather than the new serializer, are
the compatibility oracle for P1.

## P1 — Serialize domain fields directly

Paths: `src/sapphire_flow/types/rating_reference.py` and
`src/sapphire_flow/services/rating_derived.py`.

Add explicit opt-in to the existing canonical JSON encoder for dataclass **instances** by
their declared fields, letting JSON traverse nested dataclasses without recursive
deep copies. Preserve datetime awareness/UTC handling, UUID and enum encoding,
key ordering, separators and nonfinite-value refusal. The existing default accepted
set is unchanged: dataclass instances still fail without opt-in. Only
`measurement_snapshot`, `curve_snapshot` and `derive_rating_sample` opt in.
Datetime, UUID and enum handling precede dataclass handling; use
`is_dataclass(item) and not isinstance(item, type)`. Dataclass classes and other
unsupported objects remain errors even with opt-in. No field-name cache.

Use shallow declared-field dictionaries where measurement/curve construction must
remove transient fields, replace nonfinite source values or sort curve points.
Pass feed/reference dataclasses directly into immediate canonical serialization.
`measurement_snapshot` excludes `qc_status`, `qc_flags`, `qc_rule_version`,
`qc_read_token`, `qc_context_segment` and `qc_context_requirement`.
Derived `source` excludes only the last three; it retains the first three,
including nested QC flags. Its existing `source["value"]` nonfinite replacement
must precede encoding. Select declared fields with `dataclasses.fields`, so
`init=False` fields and future fields retain the old inclusion behavior.
Final snapshots remain immutable strings: do not expose a mutable intermediate or
retain caller-owned lists/dictionaries. All existing validation still executes.

Acceptance: exact pre-change content and digests for verified and owner-assumed
references, custody and native capture feeds, nested QC flags, Unicode, aware
offset timestamps, nonfinite rejected readings and extra point metadata. Mutating
input containers afterward cannot change a completed snapshot. Unsupported values,
naive datetimes and invalid references remain rejected. Existing historical golden
fixtures continue to pass.

## P2 — Review, qualify and deliver

Paths: existing rating-reference/derivation unit and integration tests, synthetic
fixtures under `tests/fixtures/` if a new golden is needed, and the provenance
touchpoint in `docs/touchpoint-maps.md` as the maintained reference.

Use P0's frozen outputs; do not regenerate expected fingerprints using the
optimized implementation. Compare
dataclass serialization with the old `asdict` representation across domain variants.
Run affected tests, lint, formatting and changed-module types. One independent
patch review checks byte compatibility and preserved evidence semantics.

Retest the full synthetic inventory without profiler overhead at unchanged limits.
If using the delivered candidate image plus these proposed modules, record both
image and patch hashes and label it a patched test runtime, not an unchanged release.
Only the two frozen code files may be mounted read-only; no agency/config/credential
mounts. A temporary native cloud host must be fully removed after the test, with
independent cleanup readback. Any remaining limit failure remains HOLD.

Create a PR with the repository release-target bump and concise evidence. Full CI
must pass after the final code change before owner merge. No release publication or
live activation. A successful performance test qualifies only this feasibility
case; remaining inventory-runner checks and live-source admission stay separate.

```json
{"phases":[{"id":"P0","depends_on":[]},{"id":"P1","depends_on":["P0"]},{"id":"P2","depends_on":["P1"]}]}
```

## Review and history

One independent Claude review completed. Its three findings were addressed by
ordering golden capture before source edits, preserving the encoder's default
accepted values through explicit opt-in, and spelling out both QC exclusion sets
and nonfinite replacement. The orchestrator marks this bounded plan READY.
Full review reports and operational evidence remain private. This file records implementation history;
administrators need no configuration or migration steps for this optimization.
