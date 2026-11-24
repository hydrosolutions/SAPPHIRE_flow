# Train, evaluate and activate a model

Use this guide after [preparing the station](new-station-by-hand.md). It applies
to any deployment. You need an installed model, an explicit station or group
scope, and access to that deployment's Prefect environment.

**Model onboarding writes data and automatically promotes and assigns artifacts
that pass its checks. It is not a dry run.** For an experiment, the administrator
must provide a separate database and artifact directory and verify the worker's
destination before starting. Changing your terminal's database variable does
not redirect a deployed Prefect worker. Keep credentials and real data outside
the public repository.

## Choose the route

| You have | Use |
|---|---|
| A model that supports training here, plus suitable history | The training procedure below |
| A trusted, already trained checkpoint | The [artifact import runbook](model-artifact-import-runbook.md), then the station guide's [group assignment procedure](new-station-by-hand.md#model-and-first-forecast) |

Import the checkpoint before group assignment: that command requires a
registered model row. Registration alone does not prove that an artifact is
suitable or evaluated. The training flow registers its model itself, so it does
not require an imported checkpoint. Both routes can activate artifacts; neither
is an evaluation-only preview.

## Check the right kind of readiness

`cmal_nepal_static_encoder` registers the supplied daily CMAL configuration via
the existing Aquacast extra. It has group scope, 78 Caravan static attributes,
365 past daily steps and a declared forecast maximum of 15 days (minimum one).
Weather inputs are precipitation, temperature, `thermal_net_radiation` and
`solar_net_radiation`; all weather channels require complete supplied values.
Missing discharge is permitted within its declared 365-value budget. Both net
radiation components are distinct mean fluxes in W/m², not generic radiation.
This registration enables neither assignments nor alerts. Its input coverage,
physical daily periods, import provenance and artifact suitability must still
be qualified. Do not invent training dates to satisfy the current import API;
the FI provenance contract and consumer policy must be coordinated first.

| Question | Check |
|---|---|
| Can an existing model run now? | The station guide's [readiness report](new-station-by-hand.md#step-7-readiness), including current input availability and model lookback. Read the checks: exit 0 means the report ran, not that every check passed. |
| Can this model be trained? | Its required historical variables, cadence, QC, usable coverage and training window. The operational report does not establish this; a dedicated training-window view is planned separately. |
| Can it be evaluated independently? | Reference observations and adequate samples in a window excluded from fitting and preprocessing. Check the artifact's complete training history, including pretraining. |

An imported model may be usable without local training history. A successful fit
does not establish independent skill or current operational input availability.
For each run, record the model/image version, scope, training/evaluation windows,
source/QC restrictions and existing gate/assignment-priority settings. There is
no universal minimum history length for all models.

Declare station forecast targets and supply the model's required weather and
static attributes before training. Do not lower gates, fabricate reference data
or substitute variables merely to make onboarding pass. Use the installed
model's qualified time convention; UTC storage timestamps do not establish the
physical daily period. Pending local-day qualification still applies.

## Train an explicit scope

In the CLI environment configured for the intended Prefect server, inspect the
registered deployments:

```bash
uv run prefect deployment ls
uv run prefect deployment inspect 'onboard-model/onboard-model'
```

The standard registration uses `onboard-model/onboard-model` (flow/deployment).
Use the name actually listed on your installation. Confirm its worker and
configuration point to the intended destination. No command below selects a
different database or artifact directory for that worker.

Replace every placeholder and choose the period before submitting. This daily
example uses explicit aware UTC bounds; they are examples, not a Nepal-day
conversion. Station identifiers are UUIDs, not gauge codes.

```bash
uv run prefect deployment run 'onboard-model/onboard-model' --watch --params - <<'JSON'
{
  "model_id": "YOUR_INSTALLED_MODEL",
  "station_ids": ["YOUR_STATION_UUID"],
  "period_start": "2010-01-01T00:00:00+00:00",
  "period_end": "2013-01-01T00:00:00+00:00",
  "time_step_hours": 24,
  "tenant_code": "YOUR_TENANT",
  "operator": "YOUR_OPERATOR"
}
JSON
```

For a group-scoped model replace `station_ids` with `group_ids` containing the
intended group UUIDs. Supply the scope matching the model; do not supply both or
omit scope and rely on defaults. A group is one training unit, not a collection
of independent station fits. Group failure handling does not split it silently.
The cadence must match the model. Assignment priority uses deployment settings
unless explicitly overridden through the existing `assignment_priority` parameter.

Keep the returned flow-run ID. A timeout or interrupted watcher does not prove
the run stopped: inspect that run before submitting again.

## Read every unit's result

Open the run and worker logs. On a normally returned run,
`model.onboarding_unit_result` records each station/group UUID, model, outcome
and artifact UUID (or null). The final `model.onboarding_completed` event gives
promoted, failed, skipped and gate-rejected counts. Earlier diagnostic events
explain the reasons. A Prefect `Completed` state can include zero promotions.

| Outcome | Meaning and action |
|---|---|
| `skipped_compat` | Targets, required inputs or another compatibility check failed; correct the reported prerequisite. |
| `failed_smoke_test` | The model failed its synthetic contract check; investigate the model/check, not the station's historical row count. |
| `skipped_no_data` | Assembly returned no usable training data; inspect the selected period and sources. |
| `failed_training` | Training failed for this unit; inspect its diagnostic and inputs. |
| `skipped_insufficient_eval` | Too little usable evaluation evidence; artifact remains in training. |
| `gate_rejected` | Existing evaluation gates did not pass; artifact remains in training. |
| `promoted` | Artifact promotion completed; verify the resulting artifact **and assignment**. A deliberately inactive assignment is protected and may remain inactive. |

The first six outcomes allow other units to continue. A refusal does not delete
an earlier working artifact or assignment. Unexpected exceptions during input
assembly, hindcast/skill processing, lineage, storage, deserialization, promotion
or assignment can instead abort the run. Such a run has no complete final result
summary; inspect progress logs and persisted state. Units not reported are not
proven successful. The service-level helper has additional failure outcomes;
do not expect those from this Prefect flow.

Runs are not atomic: earlier writes remain if a later unit fails. Promotion
precedes assignment, so an assignment error can leave an active artifact without
a new assignment. After correcting a problem, rerun only the affected scope.
Retraining creates new artifacts; it is not an automatic resume or an idempotent
replay. Do not repeatedly retrain already successful stations.

## Evaluate and verify activation

The onboarding flow's built-in hindcast uses its **training window**. Treat those
scores as in-sample checks, not independent validation. For held-out evaluation:

1. Choose a reference-covered period excluded from all fitting/preprocessing.
2. Run the existing `run-hindcast/run-hindcast` deployment with the exact
   `model_id`, `artifact_id`, station or group, explicit `period_start` and
   `period_end`, and model cadence (`time_step`, a duration, rather than the
   onboarding flow's `time_step_hours`). Supply and retain a fresh UUID as
   `hindcast_run_id`. Inspect its registered parameter schema first.
3. After checking actual stored hindcasts and failed issue dates, run
   `compute-skills/compute-skills` for each station/parameter with that same
   `hindcast_run_id`, model and artifact. Use a new `generation_id` for a new
   evaluation; preserve it when retrying the same computation. The evaluation
   period comes from the selected hindcasts, not a separate period argument.
4. Read scores for that run/generation, their lead times, periods and sample
   counts. Do not mix the onboarding run with held-out results or reuse their
   mixture as promotion evidence. Reanalysis hindcasts are not archived-weather
   forecast verification. Record the forcing source and time-grid limitations.

These are write operations, not dry runs. For preliminary scientific experiments,
keep hindcasts and skills in the isolated environment until adoption is agreed.

Before calling a model operational, read back the exact artifact ID, active
status, scope and training dates, and the intended active assignment/cadence/
priority. Check a real forecast's identity, horizon and values. Import verification
is described in the [import runbook](model-artifact-import-runbook.md#4-verify-what-was-written);
group assignment remains in the station guide. Model artifact activation is
different from making the **station** operational. Station promotion and its
prerequisites belong to that guide; this workflow does not promote a station.

### Qualifying CMAL net-radiation inputs

The Recap channels are `solar_net_radiation` (ERA5
`surface_net_solar_radiation`, IFS `ssr`) and `thermal_net_radiation`
(ERA5 `surface_net_thermal_radiation`, IFS `str`). Gateway values are already
**differenced interval energy in J/m²**, stamped at the **start** of each
interval. For UTC day D, sum Gateway hourly increments D00 through D23.
This equals the raw ERA5-Land accumulation at D+1 00Z, which belongs to day D.
Do not add an hour before daily aggregation, difference increments again, or
shift daily model labels. Temperature remains instantaneous: average D00..D23.
Daily radiation is the sum of interval energies divided by 86400 seconds;
for mixed forecast intervals this is a duration-weighted mean of their fluxes.

Sources: [Gateway timestamp policy](https://github.com/hydrosolutions/recap-dg-client/blob/6bcc6d949a33ddda042f844d36bc2710620a9ccb/README.md#timestamp-policy),
[ECMWF accumulation convention](https://confluence.ecmwf.int/spaces/CKB/pages/140385202/ERA5-Land+data+documentation),
and [ERA5-Land daily aggregation](https://developers.google.com/earth-engine/datasets/catalog/ECMWF_ERA5_LAND_DAILY_AGGR).

After checking native bounds, source handover, the IFS 3h/6h transition and
final interval against the deployed endpoint, prefer this scoped configuration:

```toml
normalized_interval_contract = "gateway_native_start_v1"
normalization_revision = "gateway-docs:6bcc6d949a33ddda042f844d36bc2710620a9ccb"
```

The existing key names are retained for compatibility. For the native contract,
`normalization_revision` must be `gateway-docs:` followed by a full lowercase
40-hex documentation commit. It records an **operator attestation**, not automatic
verification of the server or training lineage. Archive the pinned source and
boundary evidence before enabling it. Default `unqualified` still refuses radiation.
Legacy `normalized_start_v1` remains accepted for existing qualified callers;
it describes the same physical start-labelled support but has a different
radiation acquisition fingerprint. Neither contract performs a time shift.
Historical forcing retains the original source run as its version, not this
attestation: operational cache fingerprints do not protect historical imports.

**Sub-daily forecasting needs a separate timing review.** This daily agreement
does not qualify end-labelled hourly model inputs, SnowMapper `rof` inputs,
local-day boundaries, or their query/resampling rules. Revisit the adapter and
the model's input convention before enabling those paths. A blanket +1 hour is
not valid for three-hour or six-hour accumulations.

Use the existing `ingest-operational-era5-history` deployment with its optional
`parameters` list to request just the two missing radiation names. The default
remains precipitation and temperature. Verify 365 consecutive complete daily
values for every model channel/station, source identity and hourly coverage.
ERA5 energy is divided by 3600; IFS energy by the qualified 3h/6h duration.
Daily radiation conserves energy through duration weighting; signed thermal
flux is valid. Missing support remains missing and is never gap-filled.

The qualified IFS schedule admits **00z and 12z** through end lead 360 hours:
three-hour intervals before lead 144, then six-hour intervals, with the final
START-labelled interval at lead 354. This follows the
[ECMWF control forecast schedule](https://www.ecmwf.int/en/forecasts/datasets/open-data).
The shared horizon also bounds temperature snapshots, precipitation and complete
daily support. The existing conservative 72-hour admission remains for 06z/18z;
those cycles need separate qualification before expansion. This is a maximum
admissible schedule, not a claim that the Gateway supplies the entire tail.
Check actual coverage even when requesting `horizon_days=15`: a source tail of
eleven days may yield fewer complete days on a shifted model grid. Do not lower
the scoped operational minimum or pad missing periods without an owner decision.

Add these exact names to the scoped Nepal NWP capabilities only after qualifying
ingestion. Keep global capabilities unchanged. The selected UTC-daily control
route negotiates a common complete prefix for FI group models whose future
variables all explicitly permit a shorter horizon and share one ceiling. The
static encoder can use 1–15 complete days; with 11 available it receives 11,
without padding. All group stations must have support unless the cycle explicitly
selects eligible station/model pairs. In that case every selected pair must be
prepared and supported; missing bindings or weather cannot shrink that selection.
The Nepal static encoder may therefore run for its 17 operational opt-ins while
four other group stations remain onboarding. This does not promote those stations. Leading/interior holes
reject that candidate and trigger the existing bounded cycle walk-back.
Exact, mixed, station-only and ensemble requirements keep their strict selected
request behavior. A full 15 days requires issue-day 00Z with starts through
lead354 (end360); older00Z cycles can provide shorter supported prefixes.
The model returns its actual horizon and DATA_AVAILABILITY warning on a short
run. Longer horizons carry greater uncertainty; preserve all output quantiles.

The negotiation retains at most65,536 raw records across single-member tracks;
it reuses the accepted candidate rather than fetching twice. Overflow refuses
that track. Final attempts and receipts describe exactly the chosen days; old
v1/v2 evidence bytes are unchanged. A same-day retry gets a new cache generation,
so an11-day receipt remains valid when a later refresh supplies12days.
Nonzero-grid/subdaily and non00Z long-range qualification remain separate work.

Capacity measured with actual typed codecs on 20 November: 380 native-hourly,
four-channel days occupy **11,708,471 bytes in v1**, **11,730,261 in v2**;
sequential Python allocation peak was **47,329,297 bytes** (not host RSS).
Both meet the 12 MiB station gate for a proposed scoped 16 MiB cache. The global
2 MiB default remains unchanged. The full 21-station static-encoder capture needs more than the original six-station
128 MiB ceiling. After deploying compatible readers, opt the explicit Nepal
profile into `operational_weather.max_evidence_bytes = 301989888` (288 MiB),
below the shared 384 MiB absolute reader ceiling. The global capture default
stays 32 MiB; the setting applies to every model in that deployment. The separate
16 MiB station-cache setting is unchanged. Do not discard native support or split
immutable captures to fit a limit.

Linux qualification on 22 November: the live 365/11-day capture was 244,936,417
decoded bytes. The conservative 380-day retained-weather/15-day envelope was
255,522,016 decoded bytes, about 12.08 MB compressed. The latter keeps model
inputs at the declared 365 days and uses synthetic future extension solely for
capacity testing, never for forecasts. Actual capture followed by validation
peaked at 2.26 GiB RSS under the owner-approved 3 GiB worker budget. Fresh API,
retry and store validation stayed below 2 GiB; the concurrent API/retry maxima
summed to 2.84 GiB. All 21 sequential store validations took about 8.1 minutes.
These are capacity results, not completed deployment or operational forecasts.
Larger payloads and higher concurrency require separate qualification.

Current readers retain the v1/v2 layouts and old hashes. Radiation samples add
vocabulary under `rad-int-v1`; old binaries cannot read those new samples.
Radiation-enabled fingerprints bind channel sets, source mappings, units and
normalization revision. Roll back only to a compatible reader and preserve
artifacts, cache and evidence. Migration downgrade conservatively refuses when
any operational cache/evidence exists, or radiation forcing remains.

Import the exact checkpoint with explicit unknown training provenance, then run
isolated real-input qualification and verify the authenticated API listing and
detail. Do not alter existing climatology or `cmal_small` assignments/schedules.
API availability does not grant publication approval or establish independent
skill with unknown training exposure.

Before any radiation history write, also verify the **reanalysis endpoint's**
original `source_run` is the UTC day of each hourly row (not a later product
release date). Preserve a qualification record containing the exact interval qualification
revision, `rad-int-v1`, station/channel list, requested window and source-run
identities. Historical rows keep the original source run in `version`; they do
not independently store the attestation revision. A wrong attestation cannot be
repaired by repeating an insert, so archive this record before ingestion.
After resolving group capture capacity, set `max_cache_bytes = 16777216` in the
scoped Nepal `[operational_weather]` overlay and record the effective value.
The unchanged global default is too small for this 365-day model.

Capacity rollout: once evidence above 128 MiB is stored, release 0.1.1174 is no
longer a compatible rollback image. Retain a reader supporting 384 MiB; revert
capture configuration and the new assignment while keeping that reader. Lowering
the capture setting never makes existing evidence disposable. Each forecast row
may retain a copy of group evidence: the measured compression suggests about
242 MiB per 21-station issuance before database/index/WAL/backup overhead. Measure
actual growth during the first authorized issuance; no retention deletion is authorized.

Before activation refresh memory measurements: worker capture/validation below
3 GiB, fresh API/retry/store readers below 2 GiB each, paired readers below 4 GiB,
and worker baseline plus evidence below 6 GiB of its 8 GiB limit. Reserve at least
4 GiB host MemAvailable after budgeting for the paired readers. These bounds do
not qualify higher API concurrency.

## Configured fine-tuning (issue 538)

The opt-in `fine_tuning` request on `train-models` separates an experiment's
weather source and fitting/evaluation periods from operational defaults. Its
configuration includes an explicit model, approved donor, assigned group, tenant,
operator, RNG seed and model-owned parameters. It operates on the whole group;
station filters do not create a smaller group experiment.

`python -m sapphire_flow.cli.fine_tune preview --config experiment.toml` validates
configuration and displays its digest. This is not a live-data or authorization
check. `start --config experiment.toml` submits the same request through the
configured Prefect deployment. Reusing the request UUID returns the existing run;
changing its content requires a new UUID. Old deployments without the request
parameter are refused. The worker independently checks deployment-authorized
tenant, group membership, assignment, donor and warm-start support before fitting.

For the Swiss pilot, choose `data.weather_source = "hybrid"` to use the existing
MeteoSwiss historical selection, including its explicitly recorded preliminary
precipitation tail where applicable. Do not change operational source settings.
Model-specific `training.parameters` must match the pinned model's schema; the
orchestrator does not invent alternative CMAL parameters. Evaluation dates follow
fitting dates; unknown donor pretraining exposure prevents an independent-holdout
claim even when the local fitting/evaluation periods are disjoint.

The worker saves the candidate and its receipt atomically, without promotion.
Candidate and donor bytes are checked against their retained receipt hashes before
deserialization. Their hindcasts use the configured evaluation issue-time range;
metric rows carry their actual valid-time periods and sample counts. Reports show
station gaps and source/version evidence. They do not rank models: exact common
observation samples and donor pretraining exposure have not been established.
A report with stored skill for both artifacts makes the candidate PENDING_APPROVAL;
this means ready for human review, not that coverage is complete or skill improved.

Use the template in `config/examples/fine-tuning.toml`, replacing every placeholder.
For Aquacast, consult the [configuration mapping and supplied LoRA reference](aquacast-fine-tuning.md).
The research YAML is not interchangeable with CMAL-small's donor template.
The same CLI provides:

```sh
uv run python -m sapphire_flow.cli.fine_tune start --config experiment.toml
uv run python -m sapphire_flow.cli.fine_tune review --artifact UUID --tenant CODE --operator NAME
uv run python -m sapphire_flow.cli.fine_tune approve --artifact UUID --tenant CODE --operator NAME --reviewed-sha256 DIGEST --reason "review rationale"
uv run python -m sapphire_flow.cli.fine_tune reject --artifact UUID --tenant CODE --operator NAME --reviewed-sha256 DIGEST --reason "review rationale"
uv run python -m sapphire_flow.cli.fine_tune evaluate --config experiment.toml --artifact UUID
```

Review reads the persisted report, including its digest; approval/rejection never
trusts edited local JSON. These commands use the trusted host operations identity
from `SAPPHIRE_CONFIG` (or `--host-config`), its writable-tenant allowlist and
`deployment.promotion_allowed`. This is an operations command, not a new human
login or bearer-token write API. Approval atomically supersedes the incumbent,
activates the candidate and records the operator, reason and reviewed digest.
A changed roster, incumbent or report refuses approval. An identical repeated
admin decision is a no-op; a conflicting decision fails.

The worker serializes fitting/evaluation for each tenant/model/group on a dedicated
PostgreSQL advisory connection; admin decisions acquire the same scope lock.
No database transaction stays open for the duration of fitting. Ordinary operational
forecasts keep reading the active artifact. This lock does not reserve CPU/RAM or
exclude unrelated operational jobs: schedule the pilot around measured host load.
A repeated request with a retained candidate refuses to fit again. Evaluation
failure leaves it TRAINING/inactive with an incomplete receipt; `evaluate` retries
that candidate without fitting again. A deliberate new fit requires a new request
UUID. Successful training does not authorize deployment or live promotion.

Swiss runtime qualification remains required before the first production run:
verify the pinned model's trainer/finetune schema, exact weather/discharge coverage,
approved donor, registered deployment and a bounded resource/time budget. No pilot
configuration or learning-rate defaults are claimed by the template.

### Standard model-scoped issuance

The forecast cycle accepts `model_ids=["cmal_nepal_static_encoder"]` with
`data_use="standard"`. Omit `model_ids` to retain normal discovery. The selector
filters installed models and active assignments in memory; group-only selections
use the operational group members for both weather preparation and bounded horizon
negotiation. It does not assign,
activate or promote anything. Empty, duplicate, unknown or whitespace-bearing
identifiers are refused, as is selection on a provisional-rating run. If the
configuration enables daily evaluation, its configured model must be selected too;
use a dedicated configuration for an independent model-only deployment.

Standard UTC-daily FI group models declaring discharge-only past targets receive
an explicit calendar of NaNs where accepted discharge observations are absent.
The target's own declared lookback is preserved even when weather needs a longer
window. FI's unchanged `max_nan` gate decides whether prediction may proceed;
this does not impute discharge, create observations, pad future weather or add
rating lineage. Missing discharge remains visible in observation-quality flags.
Other target/cadence routes and provisional rating evidence checks are unchanged.

For the Nepal static encoder, qualify this standard route independently of the
existing CMAL-small provisional deployment. Check persisted forecasts and API
listing/detail, model/artifact identity, finite ordered quantiles, actual horizon,
missing-input flags and recurrence before declaring it operational. A completed
Prefect run alone does not establish that this model produced forecasts.

The replacement start-stamped model uses an explicitly selected group job and its
declared daily grid. Weather preparation, complete-period horizon negotiation,
cache identity and station/group assembly carry that grid together. Past weather
ends at the grid boundary at or before the actual issue; future weather starts
there. The issue and weather-cycle eligibility cutoff are unchanged. A cycle
without the full first period must fall back to an eligible complete cycle or
refuse; it cannot fabricate the pre-issue part of that period. The existing
UTC-midnight path remains unchanged for models without a declared grid.
The older UTC-day retained-cache shortcut is disabled on this V2 path. Period-aware
current-snapshot gap filling and receipt-proved donor selection still follow the
configured gap policy; a failed refresh does not reuse a V1 retained snapshot.

The declared-grid standard path supplies missing discharge on the model's own
calendar. Stored DHM daily values with a different physical boundary are excluded,
not relabelled, and no observation availability timestamp is invented. Observation
coverage flags retain the missing inputs. Mixed or unknown/known group grids,
END-labelled inputs and unscoped selections refuse. This input integration alone
does not activate the replacement: output-period persistence/API qualification,
artifact registration and isolated worker deployment remain required.


### 23 November static-encoder CMAL output contract

The separately registered `cmal_nepal_static_encoder_20261123` uses the owner's
23 November checkpoint with Aquacast 0.1.396 and ForecastInterface 0.2.10. Its
vendored inference config changes only CPU device, batch size (256), and loader
workers (0); scientific training provenance remains explicitly unknown.

New declared-grid forecasts retain `period_definition` (step/origin in seconds,
START/END label, aggregation) in storage and the detail, listing and network
overview API. For this CMAL it is daily, origin 64800, START, mean discharge in
m³/s. The real issue time stays unchanged: a 00:00 UTC issue begins with the
previous day's 18:00 UTC period (signed start lead −6 hours). FI offset is 1,
measured from the last history sample, not from the issue time. Overview support
uses physical period bounds and retains original labels. Legacy forecasts have
null period metadata; no historical row is relabelled.

The new model requires an isolated worker and explicit model selection as in the
replacement plan. Registration in the package does not activate the artifact or
publish forecasts. Legacy skill, hindcast and event-review paths refuse/exclude
these period-qualified forecasts until observation support is qualified. Mixed
known/unknown period ensembles cannot be combined. Daily evaluation stays off
for this isolated job. Revisit temporal aggregation before sub-daily deployment.


Daily CMAL source-cycle policy (owner confirmed 24 November): apply
`config/overlays/nepal-cmal-daily.toml` only to the isolated CMAL job and worker.
Its 12-hour UTC candidate cadence admits **00z and 12z only**, including fallback;
06z and 18z are reserved for later use. This cycle policy is independent of the
model's START18UTC aggregation boundary. `minimum_negotiated_horizon_days = 11`
refuses short candidates and walks back within the existing age bound. Use the
longest common complete prefix from an accepted cycle, up to the model's 15 days;
never silently reduce below 11 or synthesize missing periods. Existing jobs keep
their defaults. Verify the merged registered-job and actual-worker config both
contain these settings before activation; changing this file does not deploy it.


Compatibility: API map contract 3.2 adds optional `period_definition`. Stored
legacy periods stay SQL NULL and the API omits the new field for those rows,
preserving existing response bytes. The default negotiated minimum is omitted
from serialized configuration to preserve existing execution/snapshot fingerprints;
non-default minima (including CMAL's 11) remain part of execution identity.
Legacy inserts omit the new nullable column as well.
Role bootstrapping grants period-column reads only when revision 0091's column
exists, so historical schema upgrade/downgrade checks remain supported. Diagnostic
forecast snapshots include the safe period metadata without exposing lineage.
