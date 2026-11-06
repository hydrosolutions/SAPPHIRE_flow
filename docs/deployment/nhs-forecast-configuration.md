# NHS forecast configuration

NHS (formerly DHM) keeps the existing `DhmConfig` schema and `dhm` identifiers.
The forecast uses the same approved provider identity file as native ingest.
Do not commit that operational file or change its contents as part of this work.

## Registration boundary

`SAPPHIRE_FORECAST_CONFIG_OVERLAY` is an optional, complete ordered list of absolute
container overlay paths for the `forecast-cycle` deployment. Unset or empty disables
it. Empty elements, relative/noncanonical paths and unresolved substitutions refuse
registration. Other deployments keep their existing environment.

Registration reads the existing forecast job variables and changes only
`env.SAPPHIRE_FORECAST_CONFIG_OVERLAY`. Disabling removes that key explicitly. Unrelated job
variables and environment entries are preserved. Malformed mapping shapes refuse;
values are never printed. Registration is a single-writer operation with the relevant
pool quiesced. A skipped forecast deployment stays skipped and reports the unused
selection. This behavior must be verified with the supported real Prefect versions,
not inferred from a mock or path-only metadata.

This selection is not mount installation, provider polling or permission for live
operations. The read-only file must be available before activation. Clear the owned
selection with the current image before removing its mount or reverting the image.
STANDARD runs share the deployment; they must keep their existing behavior.

## Maintained wiring and preflight

The additive `docker-compose.nhs-forecast.yml` must be installed with reviewed
bytes in the existing project, `/srv/sapphire-nepal21-test-runtime`. It does not
replace the base Compose file or the existing weather/model/provisional overlays.
Its source is the same approved host provider file already mounted read-only by
ingest. The worker target is `/app/config/overlays/nhs-provider.toml`.

The project's protected `.env` durably holds these non-secret settings alongside
its existing release identity. Preserve all unrelated values and permissions:

- `NHS_PROVIDER_CONFIG_SOURCE`: absolute path to that existing provider file.
- `NHS_FORECAST_JOB_OVERLAYS`: initially empty; generate the enabled value from
  the actual worker chain in the approved render **without** the new overlay.
  The existing files alone own the generic worker chain. The new overlay never
  assigns worker environment. A stale registered prefix fails closed at runtime.
- `COMPOSE_FILE`: the complete existing ordered file list, including the NHS overlay
  that adds only the worker bind and init selection. Never replace it with just two
  base/cloud files. Normal and unattended invocation must use this project `.env`.

Generate only the reviewed path value with the project environment:

```text
uv run python tools/forecast_config.py generate --base-overlays <approved-chain> --stage disabled
uv run python tools/forecast_config.py generate --base-overlays <approved-chain> --stage enabled
```

The output key is the registration input `SAPPHIRE_FORECAST_CONFIG_OVERLAY`;
place its value in the maintained `NHS_FORECAST_JOB_OVERLAYS` setting. Generation
neither reads secrets nor writes settings, mounts files or registers deployments.

`uv run python tools/forecast_config.py verify` accepts one JSON object on stdin:
`baseline` and `candidate` are complete `docker compose config --format json`
results without/with the NHS overlay; `stage` is `disabled` or `enabled`;
`provider_source` and `ingest_provider_target` are the approved absolute paths.
`pool` contains only `pool_name`, `forecast_overlay_in_defaults`,
`forecast_overlay_in_template` and `template_ambiguous`. The latter three booleans
come from actual pool metadata, not absent path overrides. All must be false: the
forecast-specific key must be absent from pool defaults and literal template
configuration, and the template must support the verified job environment path.
Unrelated pool variables remain allowed and unchanged. The checker also rejects
the forecast key in **every non-init service environment in both renders**, including
values expanded from env_file, so additional worker service names cannot escape.
Root inventories actual default-pool workers and verifies their observed process
absence too; a render alone does not describe unmanaged workers. The tool never
changes a pool or contacts Prefect.

Capture both renders directly in memory and pipe their combined envelope to the
checker. Do not save/print full rendered environment, real provider contents or
raw pool metadata. The tool returns safe status/path fields or value-free refusal
messages. Validate with a clean application environment as well, proving project
`.env` supplies the settings rather than an interactive export. The checker permits
only the new provider bind and init registration selection; it checks unchanged
worker chain, unrelated environment/services/mounts and shared ingest source.

Rendering is not file/readability or reader-authority proof. Root must verify
regular-file existence, effective in-container hashes and actual worker-UID
readability for both workers, and compare all opted-in reference identities using
safe booleans/hashes. Missing or mismatched identity is not a recoverable model gap.
The same application loader must differ only under `adapters.river_stations`;
STANDARD configuration and ordinary ingestion remain unchanged.

## Activation, routine changes and rollback

These are requirements for a separately authorized root operation, not permission
to execute one. Quiesce the default pool and verify no active jobs before changes.

1. Keep the maintained registration value empty. Install the reviewed overlay and
   recreate the default worker with the shared read-only file mount. Preserve other
   jobs and the prior restart policy.
2. Verify both in-container file hashes/readability and the disabled render.
3. Generate/maintain the enabled registration value from the verified worker chain.
   Verify the enabled render and safe pool record. Register through the maintained
   init path, read back stored job variables and the actual child-process config.
   Only then resume the pool. Do not supply run-level environment overrides.
4. On routine upgrade/restart, verify the current and replacement mount/chain before
   preserving registration. A changed base chain requires regenerated selection and
   re-registration. Never activate by running init before mount availability.
5. Before **any image rollback or mount removal**, use the current image to set the
   maintained value empty, register, and verify the owned key is absent while
   unrelated variables remain. Verify the key is still absent in pool/template and
   worker environments, and observe its absence in a new child process; stored
   deployment absence alone is insufficient. Then remove/revert wiring or image.
   Verify init cannot reintroduce the removed selection. No data or attempt rows are deleted.

File bind mounts may retain an old inode after atomic provider replacement. This
change does not edit that file. Later approved shared-file changes require parsed
validation and coordinated recreation/hash checks of both affected workers.
Other code on the default worker can read the non-secret mapping. Missing required
`.env` values fail the whole Compose render. An invalid forecast selection or
malformed stored forecast job variables can fail the shared init service and block
all dependent worker starts, including ingest and backup. Preflight is mandatory
for every selection or `.env` edit. Root accepts this fail-closed shared-init
behavior for source delivery, not live activation; registration order is unchanged.
Recover from captured approved configuration under root control; do not weaken
validation or print arbitrary environment to recover.

### Maintained invocation inventory

Every operation below uses the existing project directory and complete durable
`COMPOSE_FILE`. Any explicit `-f` invocation overrides that setting: preserve every
existing file and append the NHS overlay in the reviewed order. A two-file command
is not an equivalent runtime command for this opted-in project.

| Operation | Required handling |
| --- | --- |
| Receipt/image identity validation | Keep existing release identity checks; any rendered-service validation uses the complete file list and the NHS checker before runtime changes. Image delivery alone is not activation. |
| Initial bootstrap / worker recreation | Use disabled registration, complete overlay list, then prove mount availability before enabling. |
| Upgrade / unattended start / ordinary restart | Use the same project `.env` and complete list; preflight mounts and chain before preserving/re-registering. |
| Init rerun / registration | Pass the maintained init selection; no run-level env override. Preserve one-writer/quiesced discipline. |
| Role or secret rotation | Retain the same complete Compose list and unrelated settings; verify provider wiring is unchanged before services resume. |
| Rollback | Clear/read back with the current image first, then revert image or remove mount. |

The host-build examples in `nepal-cloud-host.md` are historical bootstrap guidance;
for an NHS-enabled project they require this complete-list/lifecycle supplement.
The Actions delivery route does not activate Compose and gains no new authority.
Mac-mini launchd/bootstrap recipes and the separate `sapphire-nepal` forcing stack
are different projects and remain unchanged; do not reuse their explicit file lists
for this cloud runtime. Captured host-specific invocation lists remain part of the
operator's approval record, not invented repository defaults.


## Qualification evidence and remaining operational gates

The integration suite uses synthetic provider/reference bindings and the real
production store factory in a disposable committed-write PostGIS database. It does
not substitute an injected native reader for the loader. The separate real Prefect
probe qualifies actual registration and process environment behavior, not provider
file availability or scientific forecast skill. Local test fixtures are not live
acceptance, and no restricted checkpoint/provider data belongs in CI.

After these changes, a normal run may still refuse for incomplete P/T or the model's
strict Q declaration. AQ declaration adoption and the separate operational-weather
vision remain independent. Historical Trishuli `00001` is excluded from active NHS
configuration, polling and forecasts.

## Runtime selection and consistent configuration

The supported Prefect process runner lets worker environment overwrite a same-named
job value. Therefore registration does **not** write the generic worker-owned
`SAPPHIRE_CONFIG_OVERLAY`. The distinct forecast-specific key reaches the forecast
job and is consumed only at its PROVISIONAL_RATING configuration boundary.

When enabled, its full chain must equal the effective worker chain plus exactly the
reviewed `/app/config/overlays/nhs-provider.toml` target. Registration rejects other
final targets and malformed/duplicate paths. The runtime reads the base/worker chain
once and the provider file once, derives the in-use DeploymentConfig and DhmConfig
from that snapshot, and replaces the complete provider table rather than inheriting
leftover HydroScraper settings. The provider file may contain only
`adapters.river_stations`. All other configuration is preserved. This is not a claim
of atomicity for unchanged weather helpers' existing file reads.

An injected DeploymentConfig combined with enabled selection refuses explicitly.
Combining injected NativeRatingInputs with enabled selection is an unsupported,
nonproduction residual: the joint-snapshot guarantee does not cover an injected
reader. Production constructs the reader from the selected snapshot.
Absent/empty selection preserves the old loader. STANDARD ignores the new key and
retains its normal path, including when the provider override is unavailable.
New boundary errors are value-free. Ordinary loader signatures and behavior remain
compatible. Compare effective parsed DhmConfig with ingest as well as file hashes:
per-process variable interpolation can make identical files parse differently.

Real Prefect qualification also establishes that omitted job_variables and an empty
PATCH preserve existing variables, while a nonempty PATCH replaces the top-level
mapping. Always submit the full preserved mapping. Clearing the final owned env key
uses explicit `env: {}` and still requires actual child absence checks. No assertion
that baseline registration resets unrelated variables is made.

## CI security maintenance

PR validation found CVE-2026-49272 in the inherited OAuthLib 4.0.0 lock record.
The narrow repair upgrades only that package record to 4.0.1, retaining Prefect,
Apprise, requests-oauthlib and all other pins. The dependency chain is Prefect →
Apprise → requests-oauthlib → OAuthLib. The upstream advisory affects OAuthLib
3.3.0 through versions below 4.0.1 and fixes missing device-code validation on the
OAuth device-flow server endpoint. Applications implementing that server flow must
supply the new validator hook; SAPPHIRE Flow contains no such implementation.
This dependency repair does not change provider selection or authorize activation.
See [the upstream advisory](https://github.com/advisories/GHSA-3jp2-3mrg-2vxc) and
[4.0.1 release notes](https://github.com/oauthlib/oauthlib/releases/tag/v4.0.1).
