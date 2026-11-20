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
**differenced interval energy in J/m²**. Its raw timestamps mark interval end;
training uses interval start. The separately owned gateway-adapter correction
must normalize labels before this path is enabled. This change does not shift
labels, difference accumulations, or stitch products again.

After proving that correction at midnight, source handover, lead 144 and the
final interval, set these fields in the scoped `[adapters.recap_gateway]` overlay:

```toml
normalized_interval_contract = "normalized_start_v1"
normalization_revision = "<exact independently corrected adapter release or commit>"
```

These fields attest to deployed behavior; they do not implement it. Default
`unqualified` refuses radiation requests. Retain the revision and read-only
raw/end versus normalized/start boundary evidence with the qualification record.
Never enable this attestation on the uncorrected adapter.

Use the existing `ingest-operational-era5-history` deployment with its optional
`parameters` list to request just the two missing radiation names. The default
remains precipitation and temperature. Verify 365 consecutive complete daily
values for every model channel/station, source identity and hourly coverage.
ERA5 energy is divided by 3600; IFS energy by the qualified 3h/6h duration.
Daily radiation conserves energy through duration weighting; signed thermal
flux is valid. Missing support remains missing and is never gap-filled.

Add these exact names to the scoped Nepal NWP capabilities only after qualifying
ingestion. Keep global capabilities unchanged. Qualify the 15-day future request
with issue-day 00Z, past end equal to future start at midnight, and normalized
starts through lead 354 (end 360). An older cycle provides only 14 days and does
not satisfy the selected route's full 15-day request. The model's AT_MOST
contract has not been changed.

Capacity measured with actual typed codecs on 20 November: 380 native-hourly,
four-channel days occupy **11,708,471 bytes in v1**, **11,730,261 in v2**;
sequential Python allocation peak was **47,329,297 bytes** (not host RSS).
Both meet the 12 MiB station gate for a proposed scoped 16 MiB cache. The global
2 MiB default remains unchanged. Six station histories alone total
**70,250,826 bytes**, above the unchanged 32 MiB evidence decoder ceiling and
24 MiB qualification gate. **Multi-station CMAL activation is not qualified.**
Resolve group evidence capacity explicitly before activation; do not discard
native support, split immutable captures ad hoc, or raise limits silently.
Measure complete captures, host memory headroom and source-response sizes before
choosing concurrency; retain sequential preparation meanwhile.

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
