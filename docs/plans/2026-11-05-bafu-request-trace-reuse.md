---
status: READY
created: 2026-11-05
title: Reuse validated BAFU trace extraction within one run read
risk: high
release_target: 0.1.1105
---

# Reuse validated BAFU trace extraction within one run read

## Authority and outcome

This is the READY plan for PR #448 at planning head
`5f98d2dac2f730b9359acc4671604eb107e22f1f`. The owner authorized the narrow restoration fix and explicitly commissioned
the additional targeted scientific safety review on 2026-11-05. Independent
Claude and Codex plan reviews were completed and their findings corrected.
The owner-commissioned scientific review found no blockers. The root
orchestrator sets READY for the bounded implementation and verification below.
Commit/push remain separately controlled; merge and deployment are not authorized.

The bounded change is to reuse each selected BAFU trace within one
`read_bafu_run(...)` invocation and one already-loaded Polars frame. A valid
strict read must execute `_trace_series` four times rather than eight. A valid
snapshot-compatible read remains four executions. Keep `RELEASE_TARGET`
`0.1.1105` in the same PR.

Do not change public signatures or cache across calls, paths, frames, files,
requests, stations, or processes. Do not delete or fuse validation, precompute
all traces, use eager `setdefault`, or change archive enumeration, DB work,
queries, schemas, selectors, output/API semantics, ingest, Prefect, frontend,
authorization, QC, publication, Aquacast, or capacity setup. Direct third-party
ingest and input-quality/degraded-state work remain deferred.

## Fixed evidence and acceptance

Workflow run `41997902188` at the planning head is the genuine CI RED: the
isolated heavy production node reported p95 `1.1089523356499995` s. The local
cProfile report (4,320 extractions and about 0.258 s/request in the nested
subtree) is diagnostic only. The previously observed local value near 0.715 s
is not new speed evidence and is not acceptance.

Acceptance remains: p95 strictly `<1.0` s; raw payload `<=393216` bytes; at
most 12 queries/request; and exactly 134 stations × 10 steps × 7 quantiles at
unchanged precision. Full CI on the exact final base/head pair is required.

## Exact design and preserved order

In `read_bafu_run`, after the parquet frame is read and found non-empty, create
an empty dict. On a miss, a narrow private lookup executes
`_trace_series(frame, trace_name)`, inserts the completed list under the
unmodified lookup name, and returns it. Do not prefill keys. Stored lists are
not mutated. Pass the dict as a required, no-default parameter only to private
helpers that need it. Keep both public reader signatures unchanged. Use the
existing inline annotation style; any private alias belongs under
`TYPE_CHECKING`. Do not use Python 3.12 `type` syntax or a runtime alias that
binds `UtcDatetime`.

Replace only the existing extraction call sites. Preserve this order:

1. parquet read failure, then empty-frame result;
2. explicit unit validation, then station/issue/metric identity validation;
3. strict raw file size, JSON, issue, trace identity and axes checks, followed
   in raw trace order by each trace's unit check, raw point parsing, parquet
   lookup, and exact equality check;
4. percentile-band extraction, polygon shape, and forward/backward-axis match;
5. Median, Min, and Max extraction, in that order, all before canonical-axis
   validation or any matching check;
6. canonical-axis uniqueness/order validation;
7. Median, Min, and Max axis matching, in that order;
8. point construction, non-monotonic flagging, the independent Min-trace unit
   lookup in file order, native step, and result construction.

Parquet remains authoritative output even when raw and parquet values compare
equal, including raw `-0.0` versus parquet `0.0`. Preserve exception types,
reasons/messages, raw rules, omitted `yaxis == "y"` behavior, identities,
shapes, exact equality, point order, finite-to-`None` conversion, tie behavior,
and flags. Keep `.sort("point_index")`; duplicate-index tie order has no new
contract. Stop on an observable duplicate-index parity counterexample.

## Owned paths and baseline manifest

The root-adopted plan is changed before P0. After READY, runtime behavior may
change only in:

- `src/sapphire_flow/services/forecast_lab/bafu_archive.py`.

Test changes are limited to
`tests/unit/services/forecast_lab/test_bafu_archive.py`; documentation changes
are limited to one short read-boundary note in `docs/touchpoint-maps.md`.
Existing overview and snapshot tests are verification consumers, not an excuse
to edit them. `tests/unit/api/test_forecast_lab_route.py` is neither edited nor
claimed as reader coverage because its autouse fixture sets `archive_path=None`.
No other runtime, test, fixture, benchmark, workflow, or documentation file is
owned.

P0 creates a baseline manifest outside the repository and P3 recomputes it.
Every entry records repository-relative path, extraction selector, byte length,
and lowercase SHA-256. No hash is copied from this candidate. Methods are:

- whole-file entries hash exact file bytes;
- Python-node entries parse the UTF-8 text with `ast`, select the unique named
  top-level `FunctionDef`, `ImportFrom`, `Assign`, or `AnnAssign`, take its exact
  `ast.get_source_segment` text without normalization, encode UTF-8, and hash;
- for the CI gate, hash the exact UTF-8 source slice starting at
  `- name: Prove integration shard partition` and ending immediately before
  `- name: Run integration tests`, with no normalization.

The manifest must cover whole files
`tests/fixtures/reference/bafu_q_forecast_2135.json`,
`tests/fakes/bafu_archive.py`,
`tests/integration/store/swiss_overview_benchmark.py`,
`tests/swiss_overview_benchmark_diagnostics.py`, and
`tests/integration/store/test_swiss_overview_performance.py`; the exact
`_rows_from_fixture` and `_write_run` definitions plus `_ROOT`, `_FIXTURE`, and
`_SCHEMA` assignments in `test_bafu_archive.py`; the benchmark's three helper
`ImportFrom` nodes importing `write_raw_evidence`, `_rows_from_fixture` /
`_write_run`, and `record_then_assert_budgets`; the `NAMED_GROUPS`,
`EXECUTION_GROUPS`, `PERFORMANCE_NODE`, `PERFORMANCE_FILE`, and
`BENCHMARK_NODES` assignments in `tools/integration_shards.py`; and the CI gate
slice above. Also record the adopted READY plan's whole-file hash separately.
This avoids locking the whole editable reader test module. Existing fixture
builders remain unchanged.

## Tests and mutation proof

First inventory and reuse existing reader, snapshot, overview, reference-fixture,
orientation, order, identity, shape, axis, raw-evidence, and non-monotonic
coverage. The whole snapshot unit file is the actual consumer check. Overview
classification is secondary evidence and does not replace exact reader results.

Before runtime edits, add the following behavior-characterization cases through
public `read_bafu_run` or `read_latest_bafu_run`; they must be green on the
current source:

- wrong explicit unit plus a null time still returns `unverified_unit` before
  extraction;
- an even percentile band plus null Median `valid_time` returns the existing
  polygon `parse_error` and exact message;
- mismatched Median axis plus null Min time raises the existing
  `AttributeError`; repeat separately with null Max time, proving both delayed
  Min and delayed Max mutations wrong;
- valid unit and identity, a null time in one trace, and no raw file returns
  `unverified_raw_source`, catching extraction eagerly moved after unit/identity;
- two paths with identical names but different values, and one path rewritten
  between calls, each reflect only its current frame;
- exact names `"Min / Max"` and `"Min. / Max."` remain isolated;
- raw `-0.0` versus parquet `0.0` passes evidence equality and the output's
  parquet value has positive sign, asserted with `math.copysign(1.0, value)`;
- a separate explicit-policy case reorders Min rows before the unchanged
  `_write_run` and mixes `m3/s` / `m³/s`; `run.unit` remains the first Min row's
  file-order spelling, not the cached sorted trace's spelling.

Null-time `AttributeError` is existing behavior deliberately pinned for ordering.
This plan neither fixes it nor treats that characterization as a new blocker.
Use per-trace nulls; do not attempt a mixed naive/timezone parquet column.

Add exactly one narrow extraction-count contract test, parametrized for valid
strict and snapshot policies, through public `read_bafu_run`. A monkeypatch spy
may observe only `_trace_series` execution names/counts. Assert an
order-insensitive multiset containing each exact trace name once (four total).
Do not inspect the cache, helper calls, dict representation, or order. There is
no public deterministic work-count metric, so this private execution boundary
is justified. The strict 8→4 assertion is the genuine optimization RED on the
old source; the public behavior cases are characterization, not fake RED.
Introduce the count assertion with P2 after the P1 pre-change characterization
gate.

Against the final helper, temporarily prove focused REDs for: eager extraction;
delayed Min; delayed Max; cross-call/path or mutable-default reuse; normalized
or colliding names; raw-point cache seeding; unit lookup folded into cached
series; and no reuse/lookup bypass. Record which test kills each mutant, restore
immediately, and finish with no mutant present.

## Verification and one-shot gates

Use the repository's native `uv` environment only after implementation begins.
P1 runs the reader characterizations before runtime edits. P2/P3 run:

- all of `tests/unit/services/forecast_lab/test_bafu_archive.py`;
- all of `tests/unit/services/forecast_lab/test_snapshot.py`;
- existing relevant Swiss overview service and API unit tests;
- Ruff format/check on every changed Python file;
- `uv run pyright src/sapphire_flow/services/forecast_lab/bafu_archive.py`;
- the source ratchet using `uv run pyright --outputjson src/` and
  `tools/pyright_ratchet.py` with output outside the repository.

Do not claim Forecast Lab API coverage of the reader. Complete code, tests,
docs, mutant restoration, static checks, independent Claude and Codex patch
reviews, any authorized high-risk review, all corrections, and the P3 manifest
recheck before freezing the patch.

Docker capable of starting `postgis/postgis:16-3.4` via testcontainers is a
prerequisite for the local smoke. After final freeze, run the original
unprofiled production smoke exactly once, with profiling unset and its artifact
outside the repository:

```text
env -u SWISS_OVERVIEW_PROFILE_DIR DATABASE_URL=postgresql://test:test@localhost:5432/sapphire_test SWISS_OVERVIEW_BENCHMARK_OUTPUT=/tmp/bafu-trace-reuse-local-smoke.json uv run pytest tests/integration/store/test_swiss_overview_performance.py::TestSwissOverviewPerformance::test_134_station_bounded_reads -v -m 'not slow'
```

If setup fails and no output record exists, classify it as an environmental stop,
not evidence that the optimization failed. Preserve evidence and obtain a root
disposition before any retry. If a record exists and any latency, payload,
query, shape, or output check misses, stop with no retry until a reviewed patch
change makes focused gates green or a separately authorized broader optimization
exists.

Final acceptance is fresh, unprofiled full CI, including the production node,
full suite, and image gates, on the exact final base/head pair. Record both full
SHAs. A moved base invalidates the pair and requires normal fresh CI; if the
moved base conflicts with target `0.1.1105`, stop and escalate. Any measured
local or exact-pair CI miss stops; do not manually retry for green. The existing
Aquacast capacity/image proof remains unchanged. Root must explicitly authorize
the outward commit and push only after final checks. Owner retains approval and
merge; no deployment is authorized.

## Phases and authority

- **PlanReviewGate:** disposition all plan reviews. Root adopts this text in the
  tracked plan, cites actual owner commissioning/delegation for the additional
  high-risk review or leaves that authority unresolved, sets READY, and records
  the adopted hash. No source/test work precedes READY.
- **P0:** lock exact head/base, signatures, coverage inventory, CI RED, and the
  reproducible baseline manifest.
- **P1:** add and run only pre-change-green behavior characterizations.
- **P2:** add the extraction-count RED, implement one-invocation reuse, add the
  touchpoint note, prove/restore mutants, and run focused/static checks.
- **P3:** complete independent patch reviews plus only properly commissioned
  high-risk review; resolve findings, rerun affected checks, recompute the
  manifest, and freeze.
- **P4:** after Docker prerequisite confirmation, run the local smoke once and
  apply the environmental-stop versus measured-miss rules.
- **P5:** root authorizes commit/push; fresh full CI must pass on the exact
  final pair. Owner alone approves/merges.

```json
{
  "PlanReviewGate": [],
  "P0": ["PlanReviewGate"],
  "P1": ["P0"],
  "P2": ["P1"],
  "P3": ["P2"],
  "P4": ["P3"],
  "P5": ["P4"]
}
```

Stop on a second runtime file, public signature change, cross-invocation/frame
cache, changed ordering or observable behavior, changed locked input, fixture
helper edit, unauthorized review, release-target conflict, duplicate-index
counterexample, local measured miss, exact-pair CI miss, or need to widen scope.
A miss grants no authority to profile, retry, change budgets/fixtures, optimize
other layers, merge, or deploy.
