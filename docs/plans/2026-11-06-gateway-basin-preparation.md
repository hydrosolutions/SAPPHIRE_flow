# Prepare approved basins in the Data Gateway

## Authority and completion record

- Vision: [Prepare approved basins in the Data Gateway](../../planning/visions/2026-11-06-prepare-approved-basins-in-the-data-gateway.md), merged in PR #466 and corrected in PR #467; current vision revision `fad38d087f9ac740050563e91239576ac3eae8d6`.
- Program #450, Effort #452; prerequisite #451 is landed. Integration ends before the later SAPPHIRE preparation stage.
- Initial planning base: `a8d6186a2b971d03134e44a8276666873065b89a`; current review base: `d105617f9d36f57fd2029d5d275af68b1cc0de1d`; implementation branch: `feat/gateway-preparation`.
- The owner instructed implementation after the independent plan reviews and the disposition below. The offline implementation is present for independent review. Task evidence below distinguishes executable capability, verification limits and outstanding live acceptance. This file records task-level design and verification notes; implementation PRs and the Effort delivery record follow `implement-vision`. Keep actual checks, evidence references and unresolved holds with their tasks; do not create a second local acceptance tracker.
- `docs/v0-scope.md` was read first. Current `AGENTS.md`, conventions, local basin-operator / FI / persistence touchpoint maps, types, security and logging standards govern the design. `docs/workflow.md` is absent at this base; `docs/plans/README.md` identifies it as retired. Do not restore the retired workflow. No plan-status label grants or withholds authority; follow the invoked global skill and current owner instructions.
- High risk: external writes, credentials and user-visible operational decisions. Independent Claude and Codex plan/patch reviews and an additional owner-commissioned relevant review are required. Vatro's integration/procedure review is a separate live prerequisite, not satisfied by agent review. Owner retains PR approval and merge.

## Owner scope disposition during implementation

On 2026-11-06 the owner narrowed this delivery/rehearsal to **control forecasts
only**. The cmal_small proposal requests two ERA5 P/T subscriptions and two exact
IFS fc P/T subscriptions (`2t:fc:sfc`, `tp:fc:sfc`). Retrieval still uses `2t`/`tp`;
subscription identity is not the retrieval variable. Fixed fc product grid is
`0p1`, not a caller-selectable client argument. New perturbed preparation support
is deferred; no perturbed availability/coverage requirement applies to eligibility.
Existing shared client/model APIs remain unchanged. References to general IFS
member/grid support below are constrained by this owner disposition.

Root separately commissioned six authenticated metadata-only GETs at 17:07 UTC.
The implementation agent made none. Their report is local external DATA, not a
Vatro review or weather-coverage result. It observed sources `era5`/`ifs`/`jsnow`,
current fc `0p1` / pf P/T `0p25`, and exact surface subscription identities.
No grammar, write acceptance, nullable flag semantics or native temporal contract
is inferred. Product/parameter identities are explicit proposal inputs.

## Outcome and boundaries

A facilitator starts from an immutable approved-only basin handover, reviews a model-based data proposal, confirms new registration and subscription scope, pauses for manual history jobs, and resumes to an honest per-station Gateway coverage result. Repeated runs reuse confirmed evidence. Unknown external outcomes stop safely.

No new infrastructure, public API, administrator dashboard, authentication system, scheduled flow, deployment, database import/ingest, model change, training, activation or publication. No changes to existing six-/twenty-one-station registrations, global parameters, shared-service configuration, external repositories or clients. No DELETE/cleanup mechanism. No shared IFS refresh/backfill without a separate owner-authorized operation outside this implementation. Changed geometry returns upstream; there is no geometry checking, redelineation, repair, comparison or Gateway geometry read-back gate here.

Only the already approved Trishuli research handover (`research`, code `00001`) is the first live case. Do not use the separate pending automatic extraction or transfer its approval. Exact controlled artifact location and checksum are operator inputs, not fixture paths or inferred station defaults.

## Source-grounded design

### Existing seams and deliberate reuse

1. `basin_operator/handover.py::export_handover` already writes `package/`, `approval.json`, `coordinates-and-snap.json`, `excluded-stations.csv`, the map, static tables and `checksums.sha256`. It checks original/derived geometry and unchanged attributes before export. Its approval decisions refer to `source_fingerprint`, while `derived_fingerprint` identifies the selected package plus selected inventory. `package/derivation.json` links the source and approved station codes. Preserve this format and the old approved handover.
2. `basin_operator/review.py::open_batch` and `services/basin_package_loader.py::load_basin_package` perform geometry validation. **Do not call them from Gateway admission.** Factor only the manifest/checksum/fingerprint calculation needed for integrity into a narrow shared helper, preserving existing digest bytes and keeping all existing review/import validation in place. Add and test attribute-only GeoPackage reading using the existing dependency (for example `pyogrio.read_dataframe(..., read_geometry=False)`); this is new code, not an existing repository helper. Read only feature identity columns; do not load/check geometry or parse geometry blobs to establish correspondence.
3. `cli/basin_operator.py` supplies the local facilitator pattern: explicit launch, private workspace and `fcntl` exclusive process lock. Choose a companion **local CLI**, `cli/prepare_gateway.py`, with `prepare`, `register`, `reconcile`, `subscribe`, `history`, `resume`, and `status` subcommands. This is not a new web surface. Link it from the existing basin operator guide and show the handover-to-Gateway transition. No change to the running review server is required.
4. Keep domain values in `types/gateway_preparation.py`, orchestration/local evidence in `services/gateway_preparation.py`, and supported upload/catalog/subscription HTTP calls in `adapters/recap_registration.py`. These names describe enduring responsibilities; do not create ticket-, phase-, or generic Manager/Runner/Handler architecture. Use existing `GatewayHruName`, `GatewayPolygonName`, `SpatialRepresentation`, UTC and model requirement vocabulary. Station identity here is `(network, text code)` plus package feature identity, not a fabricated SAP3 database station. Do not force the store-backed `GatewayPolygonRef` resolver into this pre-import stage.
5. Reuse `config/recap_gateway.py` configuration/secret loading and the installed `recap_client` weather/snow retrieval facade. `RecapClientLike`, `EcmwfApiLike`, `SnowApiLike` document those supported calls. No client fork, monkey patch or replacement retrieval stack. Narrow HTTP upload/catalog/subscription methods are SAP3-owned integration because the inspected client facade exposes retrieval, not onboarding. Use injected `httpx.Client`, boundary Pydantic parsing and typed outcomes, following existing adapter patterns (`adapters/dhm.py`). No new dependency is needed.
6. `RECAP_VARIABLES` is the mapping/unit register: precipitation is ERA5 `total_precipitation` versus IFS `tp`; temperature is ERA5 `2m_temperature` versus IFS `2t`; snow uses `hs`, `rof`, `swe`. Source units differ from SAP3 canonical units. Do not duplicate a competing variable table or silently add radiation support. New evidence describes source values and assumptions, not persisted/model-ready canonical inputs.
7. `services/gateway_coverage.py` is a supervised span manifest, not an actual completeness checker: its min/max span cannot detect interior holes and its key is member-agnostic. Reuse suitable key/window vocabulary, **not** its span predicate as proof of this stage's coverage. Do not update the training manifest automatically or change training gates.

### Public API evidence and unresolved semantics

`docs/requirements/01-data-gateway-requirements.md` §1 and §8 Q4 retain the June 2026 agreement that manual web upload is available and a programmatic upload client is a possible later, non-priority addition. This is historical capability context, not a workflow authority or a veto on the owner-approved integration. The later documented API surface does not alone establish its sanctioned integration semantics. Ask Vatro to confirm the supported path and reconcile this requirements document alongside the runbooks once confirmed; do not rewrite the old agreement as if the new path had always been established.

Unauthenticated read-only inspection of `https://recap.ieasyhydro.org/sdk/openapi.json` on 2026-11-06 returned HTTP 200. It documents:

- `POST /sdk/hru/vector-files/upload`: query `hru_code`, optional `description`, multipart `files`; send exactly the approved `package/basins.gpkg`, not the ZIP/static table. `UploadResponse` has required `message`, `detail`, optional string `warnings`. It has **no structured registration identifiers, polygon mapping or geometry read-back**.
- `GET /sdk/catalogs/parameters/{era5,ifs,jsnow}`; IFS parameter records distinguish `fc`/`pf`, level and grid. Subscription identity alone does not encode all these distinctions.
- `GET /sdk/catalogs/subscriptions/` requires `source`, accepts `hru_code`; POST creates a row. PUT `/sdk/catalogs/subscriptions/{source}/{hru_code}/{parameter_name}` updates that exact row. Payloads have `enabled`, and nullable `forecast_enabled`/`reanalysis_enabled`. Source strings and parameter selection semantics must be confirmed, not guessed from free-text schema fields.
- `/sdk/exports/era5-land/reanalysis`, `/ifs/forecast`, `/snow/reanalysis`, `/snow/forecast` support retrieval. Dates, cycle/hour, forecast type/member, level/grid and provenance belong to the request contract. Explicitly supply the HRU: export defaults such as `00003` must never select an existing station by omission. Do not use operational/gap-fill products as historical training evidence.
- `ApiKeyAuth` places `api_key` in the query. The installed client also uses this pattern. Never log full URLs, requests, response bodies or raw client exception text. Reuse the approved secret loader. At the new authenticated onboarding boundary, reject non-HTTPS base URLs and disabled TLS verification before any transport call, and refuse credential-bearing configured URLs. Force redirect following off for new SAP3-owned authenticated HTTP methods. Expose only allowlisted diagnostic fields. Retain bounded, sanitized useful warning/detail text, not raw transport objects. Prevent credential-bearing third-party HTTP logs before emission at the facilitator process boundary, not merely in SAP3 event formatting. Map retrieval failures from allowlisted typed/status fields without retaining raw messages, URL, params, body or exception chains in operator output/state. Test secret echoes and URL-containing failures across both new HTTPX calls and reused requests/urllib3 retrieval with third-party logging enabled.

This schema is documentation, not evidence of permissions, valid live requests or data readiness. Before live writes, Vatro must confirm successful upload interpretation, registration/feature identity evidence and uncertain-upload reconciliation; subscription source literals, IFS variant selection and snow flag semantics; date inclusivity, native cadence/support and expected publication lag; and the snow history procedure. If an interface cannot prove a fact, retain an explicit hold and route the gap through the owner to Vatro. Never require a new geometry endpoint or invent a DAG. No external changes are authorized.

### Owner disposition and retrieval-client follow-up

The owner accepted the five first-round plan corrections and commissioned the separate API/credential-safety review. Its logging/temporary-file findings and the HTTPS finding are incorporated below. The owner explicitly chose to proceed with existing client redirect behavior while raising it with Vatro as a lower-priority follow-up. This is not a claim that a redirect or key leak was observed, nor that all redirects forward the API key. Do not add a blanket redirect-disabled-client prerequisite. Keep current client behavior rather than changing, forking, or monkey-patching Vatro's code; no direct replacement retrieval stack is authorized. Vatro's broader integration/procedure review remains required before the full live rehearsal.

The pinned retrieval helper (commit `9340e4025e05a72729d93d1d6881abcd5f298ff1`, matching its inspected current main source) follows redirects and streams raw Parquet into its process working directory. Perform retrieval only within a private `0700` per-attempt working directory under the external evidence workspace, with restrictive file creation permissions. Resolve input/config paths before any working-directory change. Keep this process-scoped operation isolated from other work; do not change the running basin-review server. Under the workspace lock, clean only owned orphan download files before resuming, preserve aggregate evidence and record recovery without raw data. Never put client temporary files in the repository or source handover.

### Proposal and history scope

Read the selected installed model's requirements via the existing discovery/FI adaptation path; snapshot model identity, configuration hash when available, selected time step, spatial representation, variables, declared lookbacks/horizons/modes and basis. Do not load weights, call predict/train or change the adapter/model. Unsupported/unavailable models or requirements produce visible holds, not guessed defaults.

`cmal_small` is the initial selection. Vendored `models/aquacast/configs/cmal_small.yaml` has a 30-day lookback and ten-day horizon. The authoritative shim requirement test (`tests/unit/models/test_aquacast_shim.py`) establishes past discharge **and P/T**, and future P/T; the YAML alone is insufficient. Use the installed declaration, with regression coverage for this distinction. The FI protocol declares targets separately from past inputs and opaque training config; no training-year window can be deduced from lookback. Existing artifact split years describe the original training configuration, not permission to request those years for Trishuli.

Propose ERA5 P/T history for past forcing and historical teacher-forcing needs, IFS P/T for future forcing, no snow by default for cmal_small. Show Q/static needs as non-Gateway prerequisites for later SAPPHIRE preparation, not subscriptions or claims of model readiness. For a model with snow, map past snow to reanalysis and future snow to forecast separately. Unsupported required variables remain explicit holds even if an operator removes them from the requested subscriptions.

Operator chooses the history purpose and start/end period; the proposal explains model input support plus the independent training/evaluation-history choice still needed. ERA5 manual requests use whole calendar years: show the requested analysis interval and the expanded job years separately. The agreed **coverage verification interval** is explicit; never silently shorten it to returned data or source lag. Show lag assumptions and unavailable tail as holds until the operator explicitly revises scope. IFS evidence uses agreed cycle(s), forecast type/member set, level/grid and valid-time horizon, not a vague date range. Operator edits invalidate old confirmation and affected coverage evidence. Do not enable unused streams. An unresolved history period blocks scope confirmation, not just a footnote in a report.

### Durable execution and reconciliation

Use one private per-batch evidence directory outside the source handover and repository. Persist a schema-versioned, boundary-validated state snapshot by atomic replace with file/directory flush before a remote mutation; use an exclusive process lock for every mutating command. Inject clock and attempt identifiers. Unknown state versions/corrupt state stop; no reset-to-empty fallback. Document single-host custody: do not copy a workspace and operate both copies or start a second workspace to bypass a hold. A local lock cannot establish global Gateway uniqueness.

State contains source/derived fingerprints and checksums, unchanged inventory/feature joins, proposed versus confirmed HRU/name mapping, versioned proposal and explicit confirmation, action intent and completion evidence, sanitized warnings/failures, manual job references and actual coverage results per station. Store no credentials or raw measurement series. Preserve prior attempts and superseded proposals; no silent evidence overwrite.

Registration is at **GeoPackage/HRU** scope, not one POST per station. Record intent including upload byte checksum, requested name and approved feature set before sending. Hold an ambiguous response, timeout, connection loss, conflict, or restart with unfinished intent as `reconciliation_required`. Do not retry that POST automatically, rename and retry, or infer ownership from a collision. Check immutable upload bytes immediately before sending; do not permit source edits during use. A confirmed mapping requires supported registration evidence that ties this handover/upload to the requested HRU and exact features. In the absence of structured response fields, require an attributed operator/Vatro reconciliation record with an evidence reference; an unsubstantiated confirmation flag or successful sample fetch is not that proof. Optional admin-page review may supply evidence but is neither automated nor a repeated geometry review.

A successful HTTP upload can be recorded separately from confirmed registration identity. On resume, reuse confirmed identity/evidence for the same handover. Changed package identity/content invalidates use and goes upstream, never reuses the registration name. Reconciliation can record confirmed completion or supported proof of non-creation; only the latter permits a deliberate retry of the same recorded intent after operator confirmation. If absence cannot be proven, remain held. Never delete remotely to recover.

Subscriptions require confirmed registration and an exact proposal revision confirmation before any write. Read existing scoped rows, calculate and show the minimal delta, re-read before each write, and preserve unrelated HRUs, parameters and snow stream flags. Create only absent required rows; update only explicitly confirmed fields. Reconcile uncertain subscription writes by fresh read-back; if the exact desired state exists, record completion without a repeat. Conflicting/ambiguous concurrent changes require renewed confirmation, not a blanket overwrite. If the API cannot safely express the intended IFS/snow scope, hold. Persist each completed action so later station/variable failures do not discard progress. Never apply deletion or broad “replace all subscriptions”.

### Actual coverage, not job success

After manual jobs, retrieve every agreed `(HRU, polygon, source/stream, variable, interval)` and, for forecasts, cycle/type/member/level/grid requirement. Historical requests are bounded (reuse the existing 31-day request convention), with explicit half-open internal intervals translated to the provider's confirmed date semantics and filtered to the requested scope. Resume only verified chunks whose scope/input identity is unchanged; refresh incomplete chunks and freshness-sensitive forecasts. If no raw data is retained, durable chunk evidence must still include exact scope, observed grid/missing intervals, provenance, fetch time and response digest sufficient to audit its aggregate conclusion.

Check actual named columns, UTC timestamps, source provenance, finite usable values, expected support/grid and every interior interval, not only row counts/endpoints. Detect missing columns, empty responses, NaN/Inf, duplicate/conflicting timestamps, off-grid or out-of-range rows, missing members and forecast-fill rows in history. Do not let one station's values establish another's coverage. Unexpected extras cannot count toward requested cells. Batch failure must not erase completed station outcomes. Unknown cadence/time support or provenance is a hold, not “complete”. Never manufacture cadence from a successful sample; use supported product contracts confirmed with Vatro. Zero valid precipitation/snow values are valid finite data, not missing by magnitude.

Report requested versus returned usable support, compressed missing intervals/counts, source/cycle identity and publication-lag limitations. Any required gap yields a per-station hold. `eligible_for_preparation` means this **Gateway** scope passed, not SAPPHIRE persistence, physical unit/time qualification, FI compatibility, model readiness, training or activation. Include every approved station exactly once, visibly separating eligible and held; retain upstream exclusions as exclusions, never attempted registrations. No raw-data export in the handoff.

## Tasks and verification ledger

### T1 — Admit the immutable handover and retain resumable evidence

**Outcome:** only an applicable approved subset can create/resume a Gateway workspace, without geometry review.

**In:** shared pure manifest/checksum/fingerprint seam in `services/basin_package_loader.py`, `types/basin_package.py` and `basin_operator/review.py` only as necessary; new domain values and local persistence in `types/gateway_preparation.py` / `services/gateway_preparation.py`. Keep existing public review/import functions and digest output compatible. Parse checksum paths safely (reject traversal, symlinks, duplicates and omissions), manifest, derivation, decisions and coordinate inventory. Verify selected station codes, feature identities and source-to-derived approval applicability. Reconstruct `derived_fingerprint` with the same manifest/file/inventory semantics without `open_batch`, geometry loading or acceptance evaluation. No new approval schema requirement for the existing Trishuli package.

**Out:** scientific validation, geometry equality, modifying approvals, accepting pending packages, remote operations or SAP3 writes.

**Verification / RED:** first add failing `tests/unit/services/test_gateway_preparation.py` and `tests/unit/types/test_gateway_preparation.py` cases for old-format approved export round-trip; source-vs-derived fingerprint distinction; `00001` preservation; rejected pending/foreign decisions; changed files/manifest/inventory; unsafe paths; unknown/corrupt state; concurrent process lock; interrupted atomic persistence. Test that Gateway admission succeeds with geometry-review entrypoints forbidden, while existing basin export/import regressions still validate upstream. Before factoring fingerprint code, freeze fixture package/inventory inputs and pin their exact existing `compute_package_fingerprint` and review/handover source/derived fingerprints as golden literals. Verify those same digests after factoring; mutation-sensitivity tests alone cannot detect digest drift. These compatibility assertions must pass on the old code and remain green; the new geometry-free admission behavior supplies the RED cases. Assert source file hashes unchanged before/after every action. RED is planned, not run during planning.

**Docs:** update the handover boundary in `docs/operations/basin-operator-runbook.md`, basin/static contract if a shared fingerprint interface is documented, and local operator touchpoint map. **Implementation evidence (2026-11-06):** geometry-free attribute-only admission, checksum/path/decision/inventory checks, source/derived identity distinction, strict canonical state reload, private exclusive workspace and atomic fsync/replace persistence are implemented. Initial RED: new admission test failed collection because the module did not exist. GREEN: admission/review/export regressions pass, including research text code `00001`, forbidden geometry entrypoints, pending/foreign decisions, unsafe paths, corrupt/omitted state and interrupted replacement. Fixed pre-refactor literals were captured for the checked-in package fingerprint, content fingerprint and review fingerprint and remain equal. The approved-export derived identity is exercised by round-trip tests, **not a pre-refactor frozen derived-GeoPackage golden literal**; this is a remaining verification limitation, not a claimed completed golden baseline.

### T2 — Propose exact model-derived data requirements

**Outcome:** a facilitator can inspect/edit a complete proposal and confirm one exact revision before writes.

**In:** pure proposal functions in `services/gateway_preparation.py`, domain scope values, model requirement snapshot through existing discovery/adaptation, live catalog reads injected behind a narrow protocol. Explicit history purpose/period and forecasting scope; unsupported requirements/stream catalog entries are holds. Non-Gateway Q/statics are clearly separated. Proposal changes invalidate relevant confirmation/evidence without losing existing confirmed registration.

**Out:** training-year defaults inferred from lookback, model changes, FI schema changes, silent requirement removal, automatic snow enablement or global catalog writes.

**Verification / RED:** failing tests for cmal_small canonical past/future P/T and Q distinction; no snow default; past-only/future-only/both snow; variable names and source units from `RECAP_VARIABLES`; unknown variables/model, unsupported spatial/temporal shape; explicit calendar-year expansion and source lag; missing history period; IFS mode/member/grid scope; operator edit requires reconfirmation; omitted required input remains held. Use injected native requirement fakes plus the existing real-shim requirement test when the optional model environment is available. A skipped optional test is not proof of the installed live declaration.

**Docs:** proposal, model/history basis and hold semantics in the Gateway operator procedure (T5's section in the existing runbook), plus touchpoint mapping for requirement consumption. **Implementation evidence (2026-11-06):** native discovered requirements are snapshotted without weights/predict/train. Proposals retain canonical variables, Q/static prerequisites, lookbacks/horizons/modes/config hash, explicit history purpose/window, whole-year job expansion, fixed-control forecast scope and exact per-variant subscription bindings. Tests cover no implicit snow, past/future snow distinction, missing model/history/contracts and stale confirmation. Unsupported fields remain holds. Optional real Aquacast requirement module skipped because the extra is absent; synthetic adapted requirements and existing FI translation tests passed. This does not verify the installed live cmal_small declaration. No standalone pre-implementation T2 RED run was recorded.

### T3 — Register and reconcile scoped subscriptions safely

**Outcome:** supported Gateway writes have durable intent, explicit confirmation and safe recovery.

**In:** `adapters/recap_registration.py` with injected HTTP client and Pydantic response boundaries; existing Recap config/secret functions; registration/subscription state transitions in the preparation service. Whitelist supported HTTP methods/paths. No client internals modification. Upload the exact approved basins GeoPackage once per registration intent. Require confirmed identity evidence, list/read back subscriptions and apply only the confirmed minimal delta. Map auth, validation, missing capability, conflict, transport uncertainty and malformed replies to typed holds/errors with sanitized details.

**Out:** arbitrary endpoint proxy, API-key CLI arguments, parameter create/update, deletes, unscoped subscriptions, automatic POST retries, speculative names or geometry read-back.

**Verification / RED:** new `tests/unit/adapters/test_recap_registration.py` HTTP-boundary tests and service failure-injection tests. Verify multipart bytes/name and query scope; catalog/subscription parsing; stale confirmation no-write; wrong HRU response; 401/403, 400, 409, 5xx, timeout, malformed success, warnings; crash before request, after send, after success before local save; unknown upload stays held across restart with **zero second POSTs**. Operator reconciliation without supporting evidence is rejected. Read-back of a lost subscription response avoids duplicates; extra rows and opposite snow flags survive; concurrent change holds. Assert no request to forbidden methods/endpoints. Add fail-closed cases for non-HTTPS URLs and disabled TLS verification: zero transport calls and no sentinel-key disclosure. Sentinel key must not appear in captured logs, stdout/stderr, reports, exception strings or saved state, including reflected API errors and request URL traces.

**Docs:** update Gateway operations API/secret/reconciliation guidance and logging events. Reconcile `docs/requirements/01-data-gateway-requirements.md` §1/§8 Q4 with Vatro-confirmed capability/status, preserving the dated prior context and any unconfirmed limits. Keep query authentication implications explicit. **Implementation evidence (2026-11-06):** schema-bound upload/catalog/subscription adapter, durable upload intent, attributed reconciliation and confirmed minimal-delta subscription application are implemented. Initial RED: adapter tests failed collection before the module existed. GREEN covers exact multipart bytes, no redirects on SAP3 HTTPX calls, HTTPS/TLS refusal, secret echoes/log suppression, status/timeout failures, no error chains, wrong-HRU read-back, preservation of opposite snow flags, interrupted upload and lost subscription response without a duplicate write. Corrected control-only E2E verifies one upload plus four subscription writes and unchanged counts on reruns. Unknown write/flag semantics remain external prerequisites, not inferred from metadata.

### T4 — Pause for manual history and verify the complete requested scope

**Outcome:** manual jobs are attributable handoff steps; resume reports actual eligible/held coverage and safely reuses completed work.

**In:** manual instructions generated from confirmed HRUs/variables/years/streams, operator job references, and supported client retrieval. Add focused `services/gateway_data_coverage.py` for pure returned-data assessment if separation from orchestration improves clarity; keep existing training manifest unchanged. Chunk whole requested history and verify agreed forecast scope. Persist compact aggregate evidence and per-station reasons. Record source temporal/unit assumptions distinctly from later SAP3 compatibility.

**Out:** starting DAGs, IFS shared refreshes, invented snow procedure, raw data archive, DB writes, sample-based completeness or changing forecast gates.

**Verification / RED:** `tests/unit/services/test_gateway_data_coverage.py` and preparation tests with fake `RecapClientLike`. Complete multi-chunk windows, gaps inside apparently complete min/max bounds, missing feature/variable/member, NaN/Inf/zero, wrong source, duplicate/conflicting/off-grid/out-of-window data, inclusive/exclusive boundary and leap-year joins, lagged tail, per-station partial result. Test completed job plus empty HTTP success remains held, ERA5 job cannot complete snow, changed scope invalidates affected chunks, resume after one chunk/variable failure preserves prior verified evidence. Compare final results from uninterrupted and interrupted execution; no repeat registration or unrelated subscription writes. Test every requested cell is accounted for, not merely a sample. Add pinned real-client boundary tests with a fake network transport for private working-directory placement, restrictive permissions, interrupted download/orphan recovery, and credential-safe logging/error mapping; do not infer these properties from a fake client alone. Exercise third-party logging and reflected keys without making live requests. Tests must document unchanged supported client redirect behavior rather than claiming it has been disabled.

**Docs:** update coverage explanation in `docs/operations/recap-gateway-runbook.md`; clearly distinguish this evidence from supervised training spans and persistence. **Implementation evidence (2026-11-06):** manual instructions/job references, 31-day full-window history requests, separately requested control forecasts, aggregate per-station coverage and completed-chunk reuse are implemented. Pure tests cover interior gaps, leap day, zeros, NaN/Inf, missing columns, duplicates, off-grid/wrong-source/empty replies. A synthetic two-station 40-day scenario holds one station then resumes successfully without repeat writes. Actual pinned facade tests use fake requests transport and verify 0700 CWD, restrictive raw-file creation, cleanup/orphan ownership, safe errors/logs and **unchanged native redirect following**. The initial reviewed head supported timezone-aware UTC indexes and uniform cadence only. The focused repair below extends this to explicit piecewise forecast lead support and typed aware source-run identities; naive indexes remain unsupported. No live history/data fetch occurred in this pass. No standalone pre-implementation T4 RED run was recorded.

### T5 — Deliver the facilitator commands and numbered operator procedure

**Outcome:** one coherent local journey with readable confirmations, pause/resume, status and a handoff listing every station.

**In:** `cli/prepare_gateway.py`, native CLI tests in `tests/unit/cli/test_prepare_gateway.py`, a fake-transport end-to-end scenario, and an approved-handover link from the basin operator runbook. No new UI service. Each writing command checks persisted proposal/identity confirmation; preview/status/history rendering are non-writing to Gateway. A nonzero exit indicates held or failed requested work and still saves/prints the per-station report. Help distinguishes failed operation, held scope and complete Gateway preparation.

Use one authoritative new section **Prepare an approved basin handover** in `docs/operations/recap-gateway-runbook.md`, linked by the basin and Nepal onboarding runbooks. Correct the stale “no registration API” statement and distinguish historic six-station manual examples from this approved-handover route. Scope old geometry review to upstream extraction; never instruct Gateway-stage revalidation. Preserve unrelated historical operator procedures.

The runbook must provide these numbered steps with tested CLI invocations:

1. Select the approved-only handover and private evidence directory; preserve the source and research identity. Resume that directory for the same batch. Confirm no existing-station/shared-service scope is included.
2. Run `prepare`; inspect exact feature mapping, proposed fresh HRU, model basis, variables/streams, history interval versus ERA5 job years, source lag, and non-Gateway prerequisites. Edit deliberately and confirm the final scope revision. No write has happened yet.
3. After Vatro's review, with the required credentials and operator inputs available, run `register` once under the owner's existing authorization for the scoped new Trishuli upload; do not request repeat owner approval for that already-authorized action. Retain useful upload warnings and supported identity evidence. If outcome is uncertain, run `status`/`reconcile` with Vatro; do not retry or choose another name. Optional admin review is not geometry checking.
4. Preview the subscription delta, confirm it, and run `subscribe`. Retain read-back of exactly the required rows/flags and unchanged unrelated work. Missing capability remains held and is raised through the owner.
5. Run `history` to print exact manual instructions. For ERA5, use documented `era5_land_backfill__targeted`, explicit `shapefile_names`, `year_start`, `year_end`, and explicit subscribed variables rather than “all”. Use only Vatro-confirmed snow instructions; until confirmed, show a snow-procedure hold. Record actual job references. Never trigger shared `ifs__subdaily` from this workflow.
6. After manual processing, run `resume`. Read full-scope coverage and missing intervals for each station. Wait or resolve holds; do not accept submission success as data. IFS absence remains a hold without separate shared-run authority.
7. Save the aggregate handoff and source/evidence references. Pass eligible stations to the later preparation stage and keep held stations visible. Reuse this evidence on reruns; no station import, model execution or activation follows automatically.

**Out:** full admin dashboard, new auth, automatic deployment, and separate acceptance frameworks.

**Verification / RED:** CLI help and command tests through the project's native interpreter, no network by default. End-to-end synthetic two-station approved export → proposal → confirmed registration → subscriptions → manual pause → partial coverage → resume, including repeated commands with unchanged remote WRITE counts. Reuse completed historical checks; intentionally refresh forecast evidence on resume because same-cycle output can be overwritten. Verify no secret leakage, source mutation, excluded-station upload or accidental DB/Prefect construction. Existing `tests/unit/basin_operator/`, `tests/unit/docs/test_recap_runbook.py`, `tests/unit/config/test_recap_gateway_config.py` and Recap adapter tests must remain green. Runbook command names/options must match CLI help; test actual behavior, not only heading strings.

**Docs:** the three operator runbooks, touchpoint map and logging standard for new event fields. **Implementation evidence (2026-11-06):** all seven local commands and the numbered linked procedure are implemented. CLI tests exercise actual prepare/confirm/status behavior without credential/network construction and reject secret-bearing invalid scope without echo. Native `uv run python -m sapphire_flow.cli.prepare_gateway --help` matches the documented commands. Service E2E uses real approved export, real HTTP adapter/fake transport and explicit synthetic retrieval contracts; it is not a full CLI-through-facade live acceptance test. Existing basin/Recap/config/runbook regressions pass. No public service/DB/Prefect bootstrap was added.

### T6 — Review, verify and rehearse without overstating completion

**Outcome:** reviewed code and honest live evidence, with unresolved external work visibly held.

**In:** one implementation PR from `feat/gateway-preparation`; focused native tests for T1–T5, formatting/lint and changed-module type checks, then full default test suite after the last code change locally or in CI. Follow current `AGENTS.md` release-target policy: one patch advance of `RELEASE_TARGET` for the implementation PR using the guarded release tool; no package literal/version churn or release tags. CI must test the final head against the current base; refresh stale-green checks. Owner approves and merges; agents do not.

Independent Claude/Codex reviews plus the owner-commissioned relevant review cover the high-risk HTTP/state/security boundary. Retain full reports locally and concise findings/outcomes in the PR. Obtain Vatro's explicit review of integration and numbered procedure before full live rehearsal. This review must settle the API semantics listed above or leave affected behavior blocked; it cannot be replaced by schema visibility or fixtures.

**Verification / RED:** N/A for the manual owner/Vatro review and live operator activity. T1–T5 supply RED→GREEN tests. Planned native checks include `uv run pytest` with focused new files and existing basin/Recap/FI requirement regressions, `uv run ruff check`, `uv run ruff format --check`, changed-module `uv run pyright`, and full default CI (live markers excluded). No tests or project imports were executed during this planning pass; no live writes/data fetches occurred.

Live sequence, after the required reviews, credentials and operator inputs are available: the accepted vision already authorizes the scoped new Trishuli registration and data fetches. Do not add another owner-approval checkpoint for them. Confirm subscription scope before its writes; seek new authority only for expanded actions or changed shared-runtime overlap.

- Record controlled approved Trishuli artifact identity, code `00001`, network `research`, source checksum, software revision, confirmed model declaration, chosen history/cycle scope and Vatro review reference. Do not reuse DHM defaults or pending automatic output.
- Confirm a fresh registration name, execute once, and retain actual registration evidence and feature mapping. Enable only confirmed required subscriptions and retain read-back. Demonstrate non-interference through the exact outbound mutation ledger, live request receipts and deterministic HTTP tests: registration writes target only the confirmed fresh HRU and approved GeoPackage; no mutation targets an existing HRU, and no delete/cleanup path exists. Use supported scoped subscription reads to verify preservation of unrelated rows/flags within affected scope. Do not claim this is a complete remote registration-inventory comparison. Collect broader before/after registration evidence only if Vatro identifies a supported source; otherwise record that stronger observation as unavailable, not a success gate. Never publish credentials or raw data.
- Hand off manual ERA5 history, and snow only if required and procedure confirmed. Retain actual job references. Resume full-scope data retrieval and report measured usable coverage and holds. No automatic shared IFS operation.
- Rerun using the same workspace/evidence and demonstrate confirmed registration/subscriptions are reused, not additional trial registrations. Demonstrate destructive interruption/unknown-outcome and partial-batch behavior with deterministic fake tests, not by deliberately disrupting the live Gateway. Trishuli proves the real path; fixtures cannot substitute for missing live steps.
- Compare original source/checksum inventory after rehearsal. Retain aggregate evidence in the existing controlled local artifact location. Record concise references and results in this task, not credentials or raw observations.

A merged code PR can establish **implemented capability** while this task remains blocked by permissions, Vatro review, manual backfills or missing data. Record separately what code passed, which live steps actually ran, and what remains outstanding. Full vision success requires real registration, required subscription read-back, manual handoff and requested-scope coverage; incomplete history is not full live acceptance. Owner decides delivery claims; no fabricated success or automatic Effort closure.

**Docs:** replace only relevant provisional procedure assumptions with confirmed facts and evidence dates, including the Gateway requirements document and runbooks once Vatro confirms the supported integration path. **Offline verification evidence (2026-11-06):** locked normal environment installed with `uv sync --locked` and exact verified-HEAD editable SCM override. Latest focused command: **442 passed, 1 skipped**, two upstream deprecation warnings. Changed source/tests ruff checks pass; changed-module pyright: **0 errors**. Guarded `.venv/bin/python -I tools/release_target.py bump --base-sha d105617f9d36f57fd2029d5d275af68b1cc0de1d` advanced `RELEASE_TARGET` once from 0.1.1112 to 0.1.1113. Final full default `uv run pytest -q` completed after final code changes: **8 failed, 11914 passed, 69 skipped, 15 deselected**, 62 warnings, 3636.63 seconds. All eight failures are existing `tests/unit/ops/test_watchdog_qc_unchecked.py` assertions receiving an extra real-host low-disk alert (about 2% free versus its 5% threshold); that module does not inject a disk-space probe. An isolated rerun of that unchanged module reproduced **8 failed, 5 passed** with the same disk alert. No watchdog fix, disk cleanup, failure ignore or green full-suite claim is included. CI or a properly isolated full-suite rerun remains required before merge. Independent patch reviews, owner approval/merge, Vatro integration/procedure review and full live rehearsal remain outstanding. No implementation delivery/closure claim is made.

## Dependency graph

```json
{
  "tasks": [
    {"id": "T1", "depends_on": []},
    {"id": "T2", "depends_on": ["T1"]},
    {"id": "T3", "depends_on": ["T1", "T2"]},
    {"id": "T4", "depends_on": ["T2", "T3"]},
    {"id": "T5", "depends_on": ["T3", "T4"]},
    {"id": "T6", "depends_on": ["T5"]}
  ]
}
```

## Open external inputs, not permission to improvise

- **Vatro through owner:** upload success/identity evidence and ambiguous-outcome recovery; supported source/parameter variant and snow flag semantics; date/cadence/provenance/publication-lag contracts; supported snow manual history procedure. Until resolved, the associated live actions/coverage claims hold. The public API has no HRU identity-read endpoint, so the plan deliberately supports attributed reconciliation rather than assuming one exists.
- **Operator with owner/model input:** exact Trishuli artifact location/identity, new Gateway name, installed cmal_small declaration, history purpose/window and required forecast scope. These are per-run inputs, not new global defaults. History choice must not borrow original model split years silently.
- **Existing authority and required inputs:** the owner already permits the scoped new Trishuli shapefile registration and data fetches. Obtain the applicable credentials and operator inputs, Vatro review and explicit subscription-scope confirmation without asking again for the already-authorized upload/fetch. Ask the owner only for expanded actions such as deployment, existing-station changes, shared IFS work or changed overlap with Nepal-cloud / BAFU-Mac-mini runtimes. Shared IFS work remains excluded by default.

Every vision outcome has a verification route in T1–T6. Software/hold/idempotency behavior is executable offline. Real upload, permissions, subscription interpretation, manual history and full coverage remain externally gated and cannot be asserted from this planning pass. No extra checkout, infrastructure or external-repository edit is needed.

## Focused patch-review repair evidence — 2026-11-06

Root authorized repair of the five independent patch findings at reviewed head
`882ff10003d64002b2e98aa033dc52b7e0b5e85c`; this does not replace fresh review.

- T1/T4: state schema advances to 2. Version 1 is rejected and preserved, not
  automatically migrated/reset. Historical source-run semantics are explicit:
  exact text, fixed aware instant, explicitly declared UTC-date-midnight rule,
  confirmed null/absence, or unresolved hold. Every usable
  historical row must satisfy source and run contracts; validated source/run
  provenance is retained in chunk evidence. No actual ERA5 run rule is invented.
- T3: raw and standard query-encoded keys (percent-escape case variants included)
  are redacted before warning/detail truncation. Ordinary HTTPX transport and
  response-processing exceptions are safely mapped; BaseException interrupts
  still propagate. HRU names are parsed before durable mutation intent so local
  syntax errors remain correctable without fabricated remote reconciliation.
- T5: actual `run()` dispatch now covers registration/repeated uncertainty,
  reconciliation, subscription preview/stale/exact confirmation, manual history
  with/without job reference, full-scope resume and status. Tests use native-shaped
  config, a dummy secret file, the actual approved secret/config loaders, the real
  HTTPX adapter and pinned retrieval facade, with only network transports faked.
  They assert state/output secret safety, no duplicate writes, control-only calls,
  wrong historical provenance holds, partial station results and complete resume.
- RED evidence: five newly added security/local-name regressions failed before
  repair (5 failed / 42 passed); an unresolved historical-run test separately
  failed by returning complete coverage. GREEN after the control compatibility extension: **477 passed, 1 optional skip**
  across new tests and relevant basin/Recap/FI regressions; changed-module pyright
  **0 errors**. Lint/format checks pass. Aquacast's real declaration remains unverified.
- Final-head full default CI is required after repair. Per root instruction the
  one-hour known-low-disk local suite was not repeated. The 11914-pass/8-watchdog-
  failure result above is evidence for the **earlier reviewed head only**, not a
  repaired-head full-suite success. RELEASE_TARGET stays **0.1.1113**; no second bump.

Root explicitly extended the repair to represent the approved ten-day control
shape. Product contracts now accept validated contiguous lead bands, with uniform
cadence retained. The declared synthetic `[0,144h)`/3h + `[144,240h)`/6h schedule
requires exactly 64 points, independently of returned rows. Native-facade/CLI
regressions cover missing 3h/6h points, the 144h join, duplicate joins, wrong cycle,
off-grid and excluded 240h points. Typed aware run instants normalize to UTC;
history supports an opt-in UTC-date-midnight rule, validated across each date and
retained with unique observed run identities. No universal ERA5 rule is inferred.
Separate root-authorized metadata observations motivated these shapes but remain
external evidence, not Vatro contract confirmation or this agent's live work.
No aggregation, units, scientific adoption, or live eligibility is inferred.
The missing pre-factor derived-GeoPackage golden literal remains disclosed; existing
fixed digest baselines and export round-trips remain unchanged and green.

## Second focused repair — 2026-11-06

Root commissioned repairs following independent full-patch reviews of `ad3bad6e`.

- T3/T4/T5: an observed subscription mismatch now appends and durably saves an
  uncertain action before returning. Status and resume share a latest-action
  predicate; historical success cannot mask newer contradictory evidence. Actual
  CLI regression starts with complete coverage, disables one fake remote row,
  observes drift, reloads status, and refuses resume without new data calls. A
  renewed preview/exact confirmation restores the row with one PUT, no unrelated
  writes; stale confirmation cannot overwrite it. Prior audit records remain.
- T2/T5: every requested forecast cycle must have a resolved source-run contract
  before proposal confirmation or external writes. Missing, all-unresolved and
  partially declared cycles hold at the proposal and actual CLI boundaries.
  Typed instant equality-to-cycle, history provenance and declared lead support
  are unchanged. Valid complete control-shape scenarios remain green.
- Removed the redundant local `quote` import; no broader scope change.
- RED run: seven failures, 41 passes. Six failures reproduced the behavioral
  defects; the unresolved CLI fixture also exposed a noncanonical cycle-key
  setup, corrected to exercise two valid unresolved declarations. Final broader
  native verification: **484 passed, 1 optional skip**; all eight implementation
  modules pass pyright, and changed-file lint/format checks pass.
- RELEASE_TARGET remains 0.1.1113. No full-suite rerun on the low-disk host,
  live operation, client/model edit, deployment, push or PR. Fresh full-patch
  review and final-head full CI remain required; Vatro approval is separate.

## Third focused repair — 2026-11-06

Root commissioned scope/evidence/report corrections after independent complete
patch reviews of `575983e6`; incidental cleanup was not included.

- T1/T4: ChunkEvidence validates aware UTC dates, ordered nonempty intervals,
  nonnegative/coherent expected/usable/observed counts, canonical digests,
  missing/hold coherence, forecast identity and validated source/run identities.
  Honest failed fetches retain zero usable/observed rows, missing support and no
  invented provenance. Malformed state is refused without resetting/replacing it.
  Schema 2 stays compatible: the new optional observed count is unknown for old
  valid records, and loading does not rewrite their bytes.
- T4/T5: one declared chunk-scope derivation serves report and resume/cache.
  Eligibility requires every exact station/variable/window/cycle/member key and
  matching support counts/source/run contract/normalized identities, not arbitrary
  record cardinality or old hold-free flags. Wrong keys cannot substitute; invalid
  complete-looking counts/provenance cannot skip history retrieval. Unexpected
  records remain explicitly held and visible, not silently deleted.
- T2/T3: proposal and defensive preview reject shared subscription identities for
  distinct required variables/products before remote calls. Existing compatible
  same-canonical snow history/forecast sharing remains valid.
- T5: per-station CLI summaries now expose only whitelisted aggregate scope,
  requested/usable/observed support, missing counts/intervals, validated provenance,
  fetch time/digest, holds and declared publication-lag limitations. Complete,
  partial, wrong-scope, failed-fetch and legacy-unknown observations are tested;
  raw measurements and transport data remain private.
- RED: six actual CLI regressions failed (16 passed) before repair, demonstrating
  wrong-key/count/provenance eligibility, duplicate subscription identities and
  omitted aggregate output. Expanded construction/load/report/resume/cache and
  native-facade verification: **528 passed, 1 optional skip**. Changed-file
  lint/format and pyright across all eight implementation modules pass; exact
  final-head and log evidence are recorded in the third local handoff.
- Release target remains 0.1.1113. No live operations, external/client/model edits,
  push, PR, merge or full low-disk-host suite. Fresh full-patch review and final-head
  full CI remain required. Synthetic support is not Vatro or scientific acceptance.

## Current-base update — 2026-11-06

Root requested rebasing the four implementation/repair commits onto
`a85431a3d0de4416defff89590052ebb33eedc1d` before fresh review. All eight
implementation modules and five Gateway test modules remain byte-identical to
reviewed candidate `403ba25c`. The logging-document conflict preserved both the
peer observation-history section and the Gateway transport section; no peer source
was changed. The pinned vision exactly matches issue 452's still-linked
`fad38d087f9ac740050563e91239576ac3eae8d6` blob.

Current-checkout AGENTS, conventions, CI/CD release policy and installed global
skill govern. The current guarded helper computes RELEASE_TARGET **0.1.1114**
from the new base's **0.1.1113**; this supersedes prior-base target notes above and
is not a per-repair bump or release. The lockfile/project dependencies did not
change, so no sync was needed. Existing native environment checks used `--no-sync`.
Focused/relevant plus current watchdog/policy checks: **545 passed, 1 optional
skip**. The previously failing watchdog module now independently passes **14 tests**
with the peer's deterministic fixture fix preserved. All eight implementation
modules pass pyright; changed-file lint/format passes. The full suite was not rerun;
final-head full CI and fresh full-patch reviews remain required. No live operations,
external/client/model edits, push, PR, tag or merge occurred.

## Explicit secret-file boundary repair — 2026-11-06

Root commissioned the sole confirmed credential-selection finding at `920fb197`.
An explicit preparation CLI `--secret-file` now supplies the key directly or fails
closed before any client construction/remote operation. The strict reader uses the
existing file text/strip convention but has no environment-fallback path; a missing,
empty, whitespace-only, unreadable, disappearing or newly emptied file cannot
select ambient credentials. Safe errors omit file contents and raw read errors.
Omitting the option retains the unchanged generic loader's default-file/environment
behavior. No process-environment workaround or external-client edit was introduced.

Actual CLI RED: **9 failed, 33 passed**, reproducing fallback/client construction
for missing/empty/whitespace files. Actual CLI regressions cover all three remote
commands, read-time races, explicit-file precedence and both omitted-option sources.
Broader native GREEN: **566 passed, 1 optional skip**; all eight implementation
modules pass pyright, and changed-file lint/format passes. Exact commit evidence
is in the local key-file handoff.
Release target stays **0.1.1114**. Forecast refresh and complete historical reuse
are unchanged; T5's ambiguous remote call-count wording above is clarified to
remote WRITE counts. No full-hour suite, live operation, deployment, external edit,
push, PR, tag or merge. Fresh independent reviews and final-head full CI remain.

## Typed subscription failure repair — 2026-11-06

Root commissioned the sole remaining typed-diagnostic finding at `8a1e76dd`.
The owned JSON boundary now distinguishes subscription POST/PUT validation
(400/422), conflict (409), authorization (401/403), malformed replies and uncertain
transport outcomes using the existing closed GatewayFailure taxonomy. The enum
lives in domain types and is reused by the adapter; there is no types→adapter
import or parallel free-form taxonomy.

A failed write appends/fsyncs uncertain action evidence with its safe typed reason
**before** fresh read-back can fail or be interrupted. Read-back reconciliation,
write intents, drift holds and no automatic duplicate writes are unchanged. A later
verified row is accepted without erasing prior failures. CLI diagnostics expose
current outcome and typed failure history, not response bodies, URLs or keys.
Valid schema-2 records may omit the new optional reason: unknown remains null,
loading preserves bytes, and malformed new categories are rejected.

Actual adapter/CLI RED: **15 failed, 77 passed**. Regressions cover POST and PUT
400/422/409/401/403/transport/malformed replies, durable reasons after restart,
failed/interrupted read-back, successful lost-response/error reconciliation without
another write, and legacy state compatibility. Final broader native checks:
**600 passed, 1 optional skip**; all eight implementation modules pass pyright,
and changed-file lint/format checks pass. Exact evidence is in the local
subscription-reason handoff. RELEASE_TARGET stays0.1.1114; no
forecast/cache change, live/external operations, push, PR, tag or merge. Fresh
independent reviews and final-head full CI remain required.
