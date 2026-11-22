# Onboard a new station (administrator steps)

**Who this is for:** the hydrosolutions facilitator doing a first onboarding for an
organization, or anyone rehearsing it in a sandbox. **What this is:** the steps of the
[onboarding guide](onboarding-start-here.md) in the order to run them. Most steps are one
command (Plan 540 tools; each is a dry run until you add `--apply`). The parts that are still
by hand are written out so they can be repeated exactly: the sandbox, the weather scope in the
config file, the Gateway dashboard jobs, and the synthetic data a sandbox needs. **What this
is not:** a finished admin dashboard. For model training, evaluation and artifact activation,
see [Train, evaluate and activate a model](model-onboarding.md).

Rehearsed end to end on 2026-11-16 in a throwaway sandbox with the synthetic Trishuli
station (code `00001`, #452): basin imported, weather history stored, observations and
discharge checked, `cmal_small` imported, one 10-day forecast stored. The sandbox forecast
used a Swiss-trained model on synthetic data: it proves the plumbing, **not** a forecast
anyone should read.

## What you can and cannot rehearse offline

On a laptop with only the repository you can run Part 0, Steps 3, 4 (with the test package
below), 5a, 5b, 6 and 7, and the Step 8 dry run. Everything else needs something only
hydrosolutions holds:

| Needs | Steps | Who supplies it |
|---|---|---|
| The Gateway key and a registered, subscribed station | Gateway registration and subscription, Step 5c (weather history), `station_readiness --check-gateway`, the first forecast cycle | hydrosolutions (key, Gateway access) |
| A trained model artifact file with its provenance values | "Model and first forecast", items 1 and 2; so Step 8 `--apply` and the first cycle | The model team ([model onboarding guide](model-onboarding.md)) |
| An approved basin handover (`<handover>/package`) | Step 4 for a real station | hydrosolutions, from the basin extraction and review |

Without a model assignment the Step 8 dry run always ends `REFUSED` (conditions 4 and 6 always
fail, 7 usually does, and 5 if the weather bindings are missing); that is the expected result
of an offline rehearsal, not a fault.

## Step order

The sections below follow the step numbers, so "Model and first forecast" appears after
Step 8 but is **run before it** (rows 11 and 12 of the table). Run in this order; "needs" says
what must exist before the step works.

| Order | Step | Needs first | Tool |
|---|---|---|---|
| 1 | Part 0: sandbox, config file, tenant, Prefect (rehearsal only) | nothing | by hand |
| 2 | Register the station with the Gateway (checklist steps 1 to 3) | the config file; the approved handover | `prepare_gateway` |
| 3 | Subscribe; start the ERA5 job and the IFS backfill (checklist steps 4, 5) | registration confirmed | `prepare_gateway`, Gateway dashboard |
| 4 | Step 3: create the station record | the tenant | `create_station` |
| 5 | Step 4: import the basin | the station record (same code and network) | `import_basin_package` |
| 6 | Step 5a: group | the station record | `scripts/create_station_group.py` |
| 7 | Step 5b: weather scope and weather bindings | group, basin import | config file, `onboard_nepal weather-bindings` |
| 8 | Step 5c: weather history | bindings, subscription, ERA5 job finished, workers running | `onboard_nepal history` |
| 9 | Step 6: observations, rating curve, derived discharge | the station record with its datum | `load_station_observations` |
| 10 | Step 7: readiness report | steps above (it reports what is missing) | `station_readiness` |
| 11 | Model: import the artifact, assign it to the group | group | `import-model-artifact`, `assign_group_model` |
| 12 | Step 8: switch the station on | everything above, including the assigned model | `promote_station` |
| 13 | First forecast cycle | the station operational; Gateway data present | `run_forecast_cycle_flow` |

## Rules

- Do this in a **sandbox first**, never against a shared host without the owner's go-ahead.
- Keep the evidence folder, API key file and delivered data **outside any Git checkout**.
  Keep the Gateway key in `~/.config/sapphire-flow/secrets/sapphire_dg_api_key` (mode 600,
  the project's convention, [security standard](../standards/security.md)); there is no key
  manager. **Point the tools at the file, never at the key:** in your own terminal run
  `export RECAP_API_KEY_FILE=~/.config/sapphire-flow/secrets/sapphire_dg_api_key` (a path).
  The workers, the forecast cycle and `station_readiness --check-gateway` read the key from
  that file themselves ([Plan 540](../plans/540-general-station-onboarding-tools.md), M0).
  The key is never typed into a command or message, and an automated assistant must not read
  the file. The `prepare_gateway` commands take the same path as `--secret-file`. The lookup
  order is: `/run/secrets/sapphire_dg_api_key` (containers), then `RECAP_API_KEY_FILE`, then
  `RECAP_API_KEY` (local development only; avoid it here, it puts the key itself in the
  environment).
- One station at a time. Write down every manual step you ran in the evidence folder.
- **Start with a new, empty evidence folder.** The Gateway tool keeps a state file per
  folder and refuses to repeat an upload it recorded as confirmed (see
  [Before Part 0](#before-part-0-register-the-station-with-the-gateway)).
- Run everything from a checkout of this repository. Install once, **before anything else**:
  `uv sync --extra aquacast` (without the extra the basin import prints a `No module named
  'aquacast'` error and the model import fails). All Python snippets below use
  `uv run python - <<'EOF' ... EOF`.
- **Replace the example values.** The commands use the Trishuli example (code `00001`,
  network `research`, tenant `chwrr`). Use your own station code, network and tenant in every
  command; `create_station list` shows which tenants and networks exist.
- **Two lists in the Gateway runbook.** [recap-gateway-runbook.md](recap-gateway-runbook.md)
  has an *admin checklist* (steps 1 to 7: 1 Prepare, 2 Register, 3 Reconcile, 4 Subscribe,
  5 History, 6 Resume, 7 Status) and, further down, a *technical list* with different numbers
  (2 Prepare, 3 Register, 4 Subscribe, ...). In this guide, "checklist step N" always means the
  admin checklist. Steps called "Step 3" to "Step 8" with no other word are this guide's own.

## Who does what

| Task | The person (administrator) | An assistant, if one helps you |
|---|---|---|
| Point the environment at the key file (`export RECAP_API_KEY_FILE=<path>`) | Yes, in your own terminal | Never. Must not read the key file |
| Start the Prefect workers (they read the key from the file) | Yes | Never. Stop and ask the person |
| Run the forecast cycle (reads the key) | Yes | Never. Stop and ask the person |
| Run the read-only Gateway check before the first cycle (`station_readiness --check-gateway`, reads the key) | Yes | Never |
| Start the manual Gateway dashboard jobs (ERA5 targeted run, IFS backfill) | Yes. The IFS backfill is shared by all basins: under the owner's standing rule (2026-11-17) an administrator may start it and must record it in the evidence folder | Never |
| `prepare_gateway` previews and status (take a key **file path**, not the key) | Yes | Yes, without reading the key file |
| `prepare_gateway` commands that write to the shared Gateway: `register`, `reconcile`, `subscribe --confirm`, `history --job-reference`, `resume` | Yes | Only with the person's or owner's go-ahead for that command; never read the key file |
| Database container, Prefect server, config files, station and basin steps, SQL checks | Yes | Yes |
| Teardown (stop only what you started) | Yes | Yes, only with the pid files |
| Promote a station on a shared host (`promote_station --apply`) | Only while the owner has switched `promotion_allowed` on for that run | Never |

## Values you need first

| Value | Example (Trishuli) | Where from |
|---|---|---|
| Tenant code | `chwrr` (sandbox: use your own sandbox tenant) | Deployment config |
| Station code, network | `00001`, `research` | The approved basin handover (`package/manifest.json` network, `basins.gpkg` station code) |
| Name, longitude, latitude | Trishuli, 85.33996, 28.16434 | Station register |
| Timezone | `Asia/Kathmandu` | Station register |
| Gauge datum (m above sea level) | 1480 (invented for the sandbox) | **Ask for it.** The elevation of the gauge zero. Without it, water-level QC skips its range checks |
| Gateway name | `trishuli_00001` | What you registered in the [Gateway runbook](recap-gateway-runbook.md) |
| Name inside the handover package | `trishuli_research_test` | `package/manifest.json`, key `gateway_hru_names` |
| Rating table | invented for the sandbox | The organization's real rating table. A sandbox may invent one |

## Before Part 0: register the station with the Gateway

Run checklist steps 1 to 3 of the [Gateway runbook](recap-gateway-runbook.md) (1 prepare and
confirm, 2 register, 3 `reconcile --outcome confirmed`; its inputs table at the top lists the
values you need) with a **new, empty evidence folder** and the approved handover. For `--config` use the
sandbox config file from Part 0 below (create it first; its `[adapters.recap_gateway]` table is
enough). Create the evidence folder **private**: `mkdir -m 700 "$EVIDENCE"`. With any wider mode
(for example the default 755) the tool holds with "Gateway preparation held: the evidence folder
must be private; run `chmod 700 <folder>`" and exit status 2. The tool prints a very long status
JSON (and many `model_discovered` log lines on stderr); the field to read is
`registration_status`. After the upload succeeds, `register` reports
`reconciliation_required`: that is expected; confirm with `reconcile --outcome confirmed`,
where `--evidence` is one honest sentence tying the Gateway admin panel entry to this upload
(name, status READY, upload time) and `--actor` is your name.

**When the only evidence is the tool's own HTTP success.** The tool records "the upload request
succeeded" separately from "the shape exists under this name". The first is not proof of the
second, and the tool does not check the `--evidence` text (any non-empty sentence is
accepted), so it is only as good as what you write. If you cannot see the entry in the Gateway
admin panel (or have no attributed confirmation from the Gateway administrator), do **not**
run `reconcile --outcome confirmed`: leave the registration at `reconciliation_required` and
ask the Gateway administrator to check. A timeout or unclear answer to the upload stays held,
also after a restart; do not rename or retry. Only attributed proof that the shape was **not**
created allows `reconcile --outcome not_created`, followed by a deliberate `register` of the
same recorded name.

**Scope file for the Trishuli rehearsal** (the runbook describes the fields; these are the
values that worked. The Gateway product values come from the 2026-10-21 to 2026-11-15 probe log
and are marked DRAFT/not reviewed in the file itself. Move the history `end` and the forecast
cycle to a recent date: the ERA5 history ends about 8 days before today, and the forecast cycle
must be one the Gateway already holds):

```json
{
 "model_id": "cmal_small",
 "time_step_seconds": 86400,
 "history": {
  "start": "2022-03-01T00:00:00Z",
  "end": "2026-11-09T00:00:00Z",
  "purpose": "Trishuli_00001 test-site rehearsal (#452): synthetic station, sandbox only"
 },
 "forecast": {
  "cycles": [
   "2026-11-16T00:00:00Z"
  ],
  "first_lead": "PT0S",
  "end_lead": "P10D",
  "members": [
   0
  ],
  "level_type": "sfc",
  "level": null,
  "control_grid": "0p1",
  "product_grid_reference": "DRAFT - IFS control forecast as served by the Gateway; grid not confirmed",
  "source_runs": [
   [
    "2026-11-16T00:00:00Z",
    {
     "semantics": "utc_instant",
     "timestamp": "2026-11-16T00:00:00Z"
    }
   ]
  ]
 },
 "contracts": [
  {
   "stream": "era5_history",
   "subscription_source": "era5",
   "cadence": "PT1H",
   "provenance": "era5_land",
   "reference": "DRAFT from Gateway probe log 2026-10-21..2026-11-15 (#452); not reviewed",
   "date_end": "inclusive",
   "publication_lag": "P8D",
   "historical_source_run": {
    "semantics": "utc_date_midnight",
    "value": null
   },
   "subscription_parameters": [
    {
     "canonical": "temperature",
     "parameter_name": "2m_temperature",
     "forecast_type": null
    },
    {
     "canonical": "precipitation",
     "parameter_name": "total_precipitation",
     "forecast_type": null
    }
   ]
  },
  {
   "stream": "ifs_forecast",
   "subscription_source": "ifs",
   "cadence": "PT3H",
   "provenance": "ifs",
   "reference": "DRAFT from Gateway probe log 2026-10-21..2026-11-15 (#452); not reviewed",
   "date_end": "inclusive",
   "publication_lag": "PT0S",
   "historical_source_run": {
    "semantics": "unresolved",
    "value": null
   },
   "forecast_lead_bands": [
    {
     "start": "PT0S",
     "end": "P6D",
     "cadence": "PT3H"
    },
    {
     "start": "P6D",
     "end": "P10D",
     "cadence": "PT6H"
    }
   ],
   "subscription_parameters": [
    {
     "canonical": "temperature",
     "parameter_name": "2t:fc:sfc",
     "forecast_type": "fc"
    },
    {
     "canonical": "precipitation",
     "parameter_name": "tp:fc:sfc",
     "forecast_type": "fc"
    }
   ]
  }
 ]
}
```

`--vatro-review` takes the integration review reference. A **sandbox rehearsal** may use a
clearly labelled placeholder (`SANDBOX-REHEARSAL-<date>`); a real station needs the real
reference from the Gateway integration review.

- `register` on a workspace that already records the name as `confirmed` does nothing and
  prints the old status. It does **not** upload again.
- **If the shapefile was deleted on the Gateway afterwards** (for example a test station
  removed by the Gateway administrator), the state file still says `confirmed`, and the tool
  cannot reset a confirmed registration (`reconcile --outcome not_created` is refused for it;
  that outcome is only for an upload that timed out or was unclear). Do not delete or edit
  state, and do not rename the Gateway name. **Stop and ask the owner.** For a throwaway
  rehearsal name that is yours to use, the owner may approve a new evidence folder with a fresh
  prepare, confirm, register and reconcile; keep the old folder for the record.

## After registration: subscribe, then start the Gateway data jobs

Registering only stores the basin shape on the Gateway. It does not yet deliver weather for
it. Three more things must happen on the Gateway side, **before** the weather history in
Step 5c. Skipping them was the first thing that stopped the 2026-11-17 walkthrough.

1. **Subscribe** the new Gateway name to the ERA5 and IFS weather parameters (checklist step 4).
   Run the preview first, read it, then confirm the revision it prints (the variables are the
   ones in the runbook's inputs table; `SUBSCRIPTION_REVISION` is the revision the preview prints):

   ```bash
   uv run python -m sapphire_flow.cli.prepare_gateway --workspace "$EVIDENCE" --handover "$HANDOVER" subscribe --config "$CONFIG" --secret-file "$KEY_FILE"
   uv run python -m sapphire_flow.cli.prepare_gateway --workspace "$EVIDENCE" --handover "$HANDOVER" subscribe --confirm "$SUBSCRIPTION_REVISION" --config "$CONFIG" --secret-file "$KEY_FILE"
   ```

   The preview should list only your new name (the rehearsal showed 4 creations). Without a
   subscription the weather history job fails (see "When a run fails" below).
2. **The ERA5 history job, started by the person on the Gateway dashboard** (checklist step 5).
   It is `era5_land_backfill__targeted`: one manual run for your new basin only. The
   [runbook](recap-gateway-runbook.md) ("Step 5 in detail") lists the fields. An assistant never
   starts it.
3. **The IFS backfill, started by the administrator.** Forecast runs made
   before your basin existed do not contain it, so the last 7 days of the `ifs__subdaily` job
   must be re-processed. This is the **only shared Gateway action** in this guide: it
   re-processes runs for every basin subscribed to that job. The owner's standing rule
   (2026-11-17) lets an administrator start it without asking each time; write the time, the job
   and the reason in the evidence folder. An assistant never
   starts it.

Things that look wrong but are normal:

- The dashboard may show **no run identifier** for the job. The tool accepts any free text as the
  job reference (the rehearsal used one), so write something honest and do not wait for an
  identifier. Recording it needs the stream and your name as well:
  `history --stream era5_history --actor "$ACTOR" --job-reference "<text>"`. The command does
  not print the reference back; it is stored in the evidence folder's `state.json`.
- `resume` (checklist step 6) reported the station "eligible" while the IFS backfill was still
  running. "Eligible" means the Gateway coverage checks passed. It is **not** the signal that the
  forecast cycle can run.

**Wait rule before the first cycle.** The dashboard showing the IFS backfill as finished is
not enough. In the rehearsal the first cycles stopped with `missing_support` (see below). The
cause is **inferred, not observed**: the Gateway's combined weather product probably had not yet
caught up with the backfill, and a later cycle with no change worked. So: if the first cycle
reports `missing_support`, wait and try again. To look before you run the cycle, the person
runs the **read-only Gateway check** (needs the basin imported, so the station has its
polygon binding, and the Gateway key through `RECAP_API_KEY_FILE`). It asks the Gateway for the
station's precipitation and temperature over the model's lookback (30 days without `--model`;
`cmal_small` also needs 30) ending yesterday, with one request per parameter, and lists days
with no data. Run it in the shell where `RECAP_API_KEY_FILE` is set, with the same
`SAPPHIRE_CONFIG` and `SAPPHIRE_CONFIG_OVERLAY` as the cycle:

```bash
uv run python -m sapphire_flow.cli.station_readiness --tenant chwrr --network research \
  --code 00001 --model cmal_small --weather-scope trishuli --check-gateway
```

It prints the whole readiness report (see Step 7) and, after the counted checks, a separate
section that is never part of the "N checks failed" line. Illustrative output (only the
relevant lines; the day counts and dates are examples):

```text
station 00001 (research)
...
PASS  weather_wait_facts  reanalysis=bound forecast=bound operational=bound; stored reanalysis: all 23 expected days present; 7 newest days not expected yet (Gateway lag, filled by the operational product at forecast time)
optional sections (not counted in the line below):
PASS  gateway_data  OK: all 30 days present
0 checks failed, 13 passed
```

Data still missing (wait for the backfill and run it again):

```text
FAIL  gateway_data  precipitation missing 3 of 30 days: 2026-11-14, 2026-11-15, 2026-11-16; temperature missing 3 of 30 days: 2026-11-14, 2026-11-15, 2026-11-16; try again after the backfill
```

If the Gateway or the key cannot be reached, or the station has no basin-average polygon
binding, the line reads `UNKNOWN  gateway_data  <reason>; not a pass` and the command still
exits 0 (exit 2 is only for a usage error or an unknown station). Every day present is a good
sign, not a guarantee: it is the same request the cycle makes.

## Part 0: a throwaway sandbox (rehearsal only)

```bash
docker run -d --rm --name sapphire-onboarding-sandbox-db -e POSTGRES_PASSWORD=sandbox \
  -e POSTGRES_DB=sapphire -p 55452:5432 postgis/postgis:16-3.4
export DATABASE_URL=postgresql+psycopg://postgres:sandbox@127.0.0.1:55452/sapphire
uv run alembic upgrade head
```

The database container is started with `--rm`, so stopping it (see Tear down) also **deletes
its data**. That is the point of a throwaway sandbox; keep anything you need elsewhere.

Use the `16-3.4` image: `16-3.5` has no arm64 build. On Apple-silicon laptops `16-3.4` also
prints a `linux/amd64` platform warning and runs under emulation (slow but fine).

The model extra (`uv sync --extra aquacast`, see Rules) must be installed. Without it a `No
module named 'aquacast'` message appears during the basin import and the model import fails
with `model 'cmal_small' is not discoverable`.

Create a config file outside the checkout, for example `sandbox-config.toml`:

```toml
[tenants.chwrr]
name = "Sandbox tenant"

[deployment]
writable_tenants = ["chwrr"]
operator = "admin test"
promotion_allowed = true   # sandbox only; Step 8 refuses --apply without it

[adapters.recap_gateway]
base_url = "https://recap.ieasyhydro.org/sdk"
timeout_s = 300
verify_tls = true
staleness_threshold_hours = 6.0
hru_metadata_source = "manual_gpkg_upload"
max_retries = 3
```

Create the tenant: `SAPPHIRE_CONFIG=sandbox-config.toml uv run python -m sapphire_flow.cli.provision_tenants`.

**Prefect (needed for the history job and the model import).** A separate server, so
nothing else is touched:

```bash
export SANDBOX_DIR=/path/outside/checkout/sandbox      # for this rehearsal only; use the same value in every shell below
export PREFECT_HOME="$SANDBOX_DIR/prefect-home" PREFECT_API_URL=http://127.0.0.1:4299/api
mkdir -p "$SANDBOX_DIR/pids" && { rm -f "$SANDBOX_DIR"/pids/*.pid 2>/dev/null || true; }
uv run prefect server start --host 127.0.0.1 --port 4299 &
echo $! > "$SANDBOX_DIR/pids/server.pid"
```

Register the deployments you need. The registrar assumes the Docker image path `/app`; in a
sandbox point it at your checkout and register only the two deployments this runbook uses.
The command takes no options: do not run it with `--help` (it starts a real registration
instead of printing help).

```bash
SAPPHIRE_FLOW_SOURCE_ROOT=$PWD \
SAPPHIRE_ONLY_DEPLOYMENTS=ingest-operational-era5-history,import-model-artifact \
uv run python -m sapphire_flow.cli.register_deployments
```

**The person starts the workers** (never an assistant: they read the Gateway key). Open a
terminal in the repository checkout, set the same variables as above (`SANDBOX_DIR`,
`PREFECT_HOME`, `PREFECT_API_URL`, `DATABASE_URL`, `SAPPHIRE_CONFIG`, and two folders outside
the checkout: `SAPPHIRE_DATA_DIR`, `SAPPHIRE_INCOMING_DIR`), then point `RECAP_API_KEY_FILE` at
the key file and start one worker per pool (`ingest` and `default`). Only the path is in the
environment; the workers read the key from the file. Each worker's process id goes into a pid
file, so the tear down stops exactly these and nothing else:

```bash
export RECAP_API_KEY_FILE=~/.config/sapphire-flow/secrets/sapphire_dg_api_key
uv run prefect worker start --pool ingest --type process > "$SANDBOX_DIR/worker-ingest.log" 2>&1 &
echo $! > "$SANDBOX_DIR/pids/worker-ingest.pid"
uv run prefect worker start --pool default --type process > "$SANDBOX_DIR/worker-default.log" 2>&1 &
echo $! > "$SANDBOX_DIR/pids/worker-default.pid"
```

The worker logs are in `$SANDBOX_DIR`. Leave this terminal open while you work, and close it or
`unset RECAP_API_KEY_FILE` when you are done.

**Tear down** (always, also after a failed run). Run this block in **bash** (type `bash`
first if your shell is zsh: zsh stops with "no matches found" when a `*.pid` pattern matches
nothing). Never use `pkill -f "prefect ..."`: other Prefect servers and workers on the same
machine (your other checkouts, a staging stack) would be stopped too.

```bash
: "${SANDBOX_DIR:?set SANDBOX_DIR to the folder you used above}"
for w in worker-ingest worker-default server; do   # jobs the launcher started first, then the launcher itself
  [ -f "$SANDBOX_DIR/pids/$w.pid" ] || continue
  pid="$(cat "$SANDBOX_DIR/pids/$w.pid")"
  ps -p "$pid" -o command= | grep -q prefect || continue   # a stale pid file must never hit a stranger
  for child in $(pgrep -P "$pid"); do kill "$child" 2>/dev/null; done
done
for f in "$SANDBOX_DIR"/pids/*.pid; do
  [ -f "$f" ] || continue
  pid="$(cat "$f")"
  ps -p "$pid" -o command= | grep -q prefect && kill "$pid" 2>/dev/null
done
rm -f "$SANDBOX_DIR"/pids/*.pid
docker stop sapphire-onboarding-sandbox-db         # --rm: this also deletes the container and its data
docker ps -a --filter name=sapphire-onboarding-sandbox-db   # must list no container
lsof -nP -iTCP:4299 -iTCP:55452 -sTCP:LISTEN       # Prefect server and database ports: must print nothing
```

The pid files hold the `uv run` launcher processes, not the real server and workers. The real
ones stop only through the `pgrep -P` step above (it finds the launchers' children), which is
why that step comes first. (macOS `xargs` has no `-r` option, so the loop does not use it.)

If `lsof` still shows a process on port 4299 or 55452 and it is not the sandbox's, do not
kill it: tell the owner. Check also `pgrep -lf "prefect (worker|server)"`: anything left that
is not yours belongs to someone else; leave it alone.

## Step 3: create the station record

One command. It is a dry run until you add `--apply`. Ask the gauge type first: a staff or
pressure gauge gives the height above the gauge zero; a bridge radar/ultrasonic sensor gives
the distance down to the water, so it needs the sensor elevation as well. In both cases the
datum is the elevation of the gauge zero in m a.s.l. In a terminal, if you give none of the gauge
options, it asks for them (and, with `--apply`, "Write this station? [y/N]"). Without a terminal
pass `--gauge-type {staff,bridge-sensor}`, `--datum-masl` (and `--sensor-masl`, which must be
above the datum), or `--datum-unknown` (water-level range checks stay off). `--parameters` is
what the station measures; give `discharge` too if the model target is discharge.

```bash
export SAPPHIRE_CONFIG=sandbox-config.toml   # the config file from Part 0, or pass --config
uv run python -m sapphire_flow.cli.create_station list    # tenants and networks that exist
uv run python -m sapphire_flow.cli.create_station --tenant chwrr --code 00001 \
  --network research --name Trishuli --lon 85.33996 --lat 28.16434 \
  --timezone Asia/Kathmandu --parameters water_level,discharge \
  --gauge-type staff --datum-masl 1480 --gateway-name trishuli_00001 [--apply]
```

`--code` and `--network` must equal the handover package's, or the basin import holds the
basin. The pair is unique across all tenants (a duplicate is refused), and **this guide has no
tool to change a station's code, network or tenant afterwards**: check `--tenant`, `--code` and
`--network` against the handover before you add `--apply`. `--gateway-name` is required. It must
be lower-case letters, digits and underscores; it is only checked and printed into the "next
steps" lines, it is not stored on the station. The command prints `DRY RUN (nothing written)` or
`APPLIED`, the station, the gauge and a worked conversion example. The station id shown by the
dry run is **not** the id `--apply` writes (a new one is drawn each time): take ids from the
database, never from a dry run. Exit 0 ok, 1 refused or
failed (the reason is in the log), 2 bad options. `forecast_targets` (what the station is
forecast for) is not set here; Step 8 sets it.

## Step 4: import the basin

This one has a tool: `uv run python -m sapphire_flow.cli.import_basin_package --package-dir <handover>/package`
([importer runbook](basin-static-importer-runbook.md)). Expect the log line `basin_importer.cli.report` with `accepted=1`, `imported_basins=1`, `onboarding_held=0`.

**Sandbox without a handover (optional, unverified).** For a real station the package comes from
hydrosolutions. For an offline rehearsal the repository holds one test package,
`tests/fixtures/basin_static/nepal-dhm-basins` (see the README next to it): station code
`123`, network `dhm`, polygon name `g_123`. Create the station record in Step 3 with that code
and network and use `--package-dir tests/fixtures/basin_static/nepal-dhm-basins`; wherever this
guide says `00001` and `research`, read `123` and `dhm`, and `g_123` for `g_00001`. Not yet
rehearsed as-is: the 2026-11-22 walkthrough copied the package, changed its code and recomputed
its checksums (the manifest checksums are written `sha256:<hex>`). If the import holds a basin,
the importer report names the reason; compare code and network first. The package declares the
Gateway name `nepal_dhm_v1` (the `original_gateway_hru_name` in Step 5b); your runtime name is
the one you register. Import the basin before a model is assigned to the group, because an
assigned model makes missing static attributes a hold reason. This package is a test fixture:
never use it for a real station.

## Step 5: group, weather scope, weather history

**5a. Create a group, or add the station to an existing one.** One command, any tenant your
host may write (`--tenant` and `--network` are required, there is no default). Dry run is the
default; add `--apply` to write. Re-running with an existing group name adds only the missing
members. Keep listing **all** members in the scope below (the scope must match the group exactly).

```bash
SAPPHIRE_CONFIG=sandbox-config.toml uv run python scripts/create_station_group.py \
    --tenant chwrr --network research --name my-group --station-code 00001 --apply
```

It prints `DRY RUN (nothing written)` or `APPLIED`, the group name and id, and the station
codes it adds (`add:`), keeps (`already:`) or cannot use (`UNKNOWN:`, `OTHER TENANT:`; exit 1);
a re-run with nothing to change says "no change (idempotent re-run)". An unknown code also
prints the networks that have stations in the tenant. The group id printed by a dry run is not
the id `--apply` writes (for a group that does not exist yet). Look both ids up in the database for 5b:
`select id from stations where code = '00001' and network = 'research'` and
`select id from station_groups where name = 'my-group'` (add the tenant if several tenants use
the same group name).

**5b. Weather scope.** Add to the config file (ids from 5a):

```toml
[weather_history.scopes.trishuli]
group_id = "<group id>"
original_gateway_hru_name = "<package/manifest.json, gateway_hru_names>"
runtime_gateway_hru_name = "trishuli_00001"      # the name you registered
station_polygons = [{station_id = "<station id>", polygon_name = "g_00001"}]
```

The package may carry a different Gateway name from the one you registered; the runtime name
is what every later tool uses, so keep it identical in the Gateway, the scope and your notes.
Then run `weather-bindings` (a dry run unless you add `--apply`; run it without `--apply` first and
read the per-station `nepal_onboarding.weather_bindings` lines, which show the Gateway name
and the bindings it would add). It creates all three weather bindings the cycle needs:
reanalysis `era5_land`, forecast `ifs_ecmwf`, operational `recap_ecmwf_operational`.
It works for any tenant: pass your tenant code as `--tenant`. Run it after the group exists and
the station's basin package is imported (order: group, basin import, weather-bindings).
`--confirm-t8` is a chwrr/Nepal-host acceptance flag: required for `--tenant chwrr`, optional
for other tenants. `--confirm-package-joins` is always required.

```bash
uv run python -m sapphire_flow.cli.onboard_nepal weather-bindings --config sandbox-config.toml \
  --tenant chwrr --confirm-t8 --weather-scope trishuli --confirm-package-joins   # first without --apply, then add it
```

**5c. Weather history.** First make sure the Gateway side is done: the name is **subscribed**
and the **ERA5 history job has finished** (see "After registration" above). Otherwise this job
fails with only `Operational ERA5 history failed`. Then submit through the deployed job (the
person starts the Prefect worker, because it reads the Gateway key). This command accepts only
`--tenant chwrr` and always needs `--confirm-t8`; `--start` is inclusive and `--end` exclusive,
both with a time zone:

```bash
uv run python -m sapphire_flow.cli.onboard_nepal history --config sandbox-config.toml --tenant chwrr \
  --confirm-t8 --weather-scope trishuli --start 2022-03-01T00:00:00Z --end 2026-11-09T00:00:00Z
```

There is no dry run for this command when `--weather-scope` is given: `--dry-run` is refused
(it exits 2 with a usage message naming the reason).

Success means the job was submitted, not finished: wait for the worker to log `Completed`, then
check `select parameter, count(*), min(valid_time), max(valid_time) from historical_forcing group by 1`.
The Gateway's ERA5 trails real time by about 8 days: that gap is filled at forecast time by the
operational product (created by `weather-bindings` in 5b).

**Keeping the history current.** This job imports a fixed window once. A host that is to keep
its weather history current needs the separate rolling refresh
(`refresh-operational-era5-history`), which is **off unless the host sets both
`SCHEDULE_REFRESH_OPERATIONAL_ERA5` (a UTC cron) and `SAPPHIRE_ERA5_WEATHER_SCOPE`** when it
registers deployments ([orchestration standard](../standards/orchestration.md)). Without it
stored history stops growing, and the readiness report says so in `weather_wait_facts`
(status 2026-11-21: the Nepal host had no schedule yet and its history ended 26 October; this
sentence will go stale). The refresh re-imports the last 30 days that
ERA5 should already have, so a missed day is repaired by the next run; a longer outage needs a
manual run of this job.

## Step 6: observations, datum, rating, quality checks

Three facts decide whether this works:

1. **Ask for the gauge datum** (metres above sea level of the gauge zero). It is stored on the
   station at creation (`water_level_datum_masl`, Step 3). Without it the range and
   gross-outlier checks are skipped, and the QC rule version reads `1.2-datum-skip` while every
   value "passes"; the load command refuses that unless you pass `--allow-unchecked`.
2. **Stored water level is metres above sea level.** QC subtracts the datum to get gauge height.
   The load command converts for you (staff gauge `datum + reading`; bridge sensor
   `sensor elevation - reading`). A value stored as gauge height by hand fails the range check
   (it reads about `-datum`).
3. **Discharge needs a rating table.** Load the organization's table as a rating curve, then
   derive discharge from water level. A sandbox may invent a table (the Trishuli rehearsal used
   `Q = 60 * (h + 0.2) ** 1.8`, h = gauge height in m, labelled SYNTHETIC in `rating_type_label`).

**Sandbox only: make a synthetic gauge-height series.** A real station has real data; skip
this. The forecast cycle needs daily discharge up to the issue date, and quality checks (QC)
mark odd-looking values `suspect`, which the cycle then treats as missing days. A first
rehearsal series with random noise gave 24 suspect days of 31 (the spike rule), and a smooth
sine wave gave flat runs at its turning points (the frozen-sensor rule). So the rule is
**no noise and no flat runs**: this series is a base level plus smooth storm pulses with a
slow decay, built from a fixed seed, and every day differs from the day before by at least 3 mm.
It runs from 2022-03-01 to today and writes the CSV the load command below reads (a pure
script: no database), so run it first:

```bash
uv run python - <<'EOF'
import csv, math, os, random
from datetime import date, timedelta
START, END = date(2022, 3, 1), date.today()      # END must be today: the cycle needs data up to the issue date
OUT = os.environ["SANDBOX_DIR"] + "/gauge_heights.csv"
rng = random.Random(452)
n = (END - START).days + 1
storms, d = [], 0
while d < n:                                    # a storm every 3 to 8 days, 0.08 to 0.18 m high
    storms.append((d, rng.uniform(0.08, 0.18)))
    d += rng.randint(3, 8)
def pulse(t, amp, rise=6.0):                    # smooth rise to a peak after `rise` days, then slow decay
    x = t / rise
    return amp * x * math.exp(1 - x) if t >= 0 else 0.0
gauge = [0.8 + sum(pulse(i - s, a) for s, a in storms if i - s < 60) for i in range(n)]
for i in range(1, n):                           # no flat days: every day moves by at least 3 mm
    step = gauge[i] - gauge[i - 1]
    if abs(step) < 0.003:
        gauge[i] = gauge[i - 1] + math.copysign(0.003, step or gauge[i - 1] - gauge[i - 2])
gauge = [round(g, 4) for g in gauge]
with open(OUT, "w", newline="") as f:
    w = csv.writer(f); w.writerow(["timestamp_npt", "water_level_m"])
    for i, h in enumerate(gauge):
        w.writerow([f"{START + timedelta(days=i)}T00:00:00", h])
print(n, "days; gauge height", min(gauge), "to", max(gauge), "m")   # must stay below 1.5 (the table's top)
EOF
```

The synthetic rating table (stage in m above the gauge zero, which is what the default
`--level-kind gauge-reading` expects) goes with it:

```bash
uv run python - <<'EOF'
import csv, os
OUT = os.environ["SANDBOX_DIR"] + "/rating.csv"
with open(OUT, "w", newline="") as f:             # SYNTHETIC: Q = 60 * (h + 0.2) ** 1.8, h in m
    w = csv.writer(f); w.writerow(["stage_m", "q_m3s"])
    for g in range(-15, 151, 5):
        w.writerow([g / 100, round(60 * (g / 100 + 0.2) ** 1.8, 3)])
EOF
```

If the printed maximum gauge height is 1.5 m or more, extend the range in `rating.csv`: a level
outside the table is skipped by `derive-discharge` (counted, never extrapolated or clamped). A
series whose values merely read `suspect` is stored, so read the counts the load prints. You can
also drive a simple linear reservoir with the stored ERA5 precipitation instead; the same rule
applies. A command that generates and loads the synthetic series in one step (Plan 540 M3d-2)
is deferred.

**Loading observations (Plan 540 M3a, M3b).** `load_station_observations` reads the station's
gauge type and datum (set at station creation), converts the reading, stores it and writes one
audit row, then runs the same Stage 1 quality checks as live ingest (in memory on a dry run).
Dry run by default; `--apply` writes. Exit 0 ok, 2 usage or bad file, 3 refused: the station has
no datum (range and outlier checks would be skipped), no quality rule applies to the series (too
few readings per day), or the stored data changed while the checks ran. With `--apply` a
refusal rolls the whole load back ("NOTHING WAS SAVED"). `--allow-unchecked` accepts the first
two, not the third:

```bash
uv run python -m sapphire_flow.cli.load_station_observations water-level \
  --tenant chwrr --station-code 00001 --network research --csv "$SANDBOX_DIR/gauge_heights.csv" \
  --time-column timestamp_npt --value-column water_level_m   # add --apply to write
```

`discharge` (measured discharge, for a station that has it) takes the same flags except
`--value-kind`. The command prints one worked example (`example: reading 2 m -> stored 1482 m
a.s.l. (staff gauge, datum 1480)`), the first and last UTC instant, the QC rule version, the
counts (passed, failed, suspect, unchecked, unjudged) and "N of 30 daily values usable
(passed or unchecked)" (`--lookback-days` and `--as-of` change the window). A new station has
no history baseline yet, so it prints "history-based check not run: no baseline yet" (that
check is not counted as passed). The time zone defaults to the station's; override with
`--utc-offset-minutes 345` or `--zone Asia/Kathmandu`. If every value is above the gauge zero it
warns that the values look already absolute: if so, pass `--value-kind level-masl` (`--strict`
turns the warning into exit 2).


Check the result: `select qc_rule_version, qc_status, count(*) from observations group by 1,2`
must show `1.2-datum`, not `1.2-datum-skip`.

**Rating curve and discharge (Plan 540 M3c).** The same command family stores the table and
derives discharge. Both dry run by default; `--apply` writes. The table's stage column is read like
the observations (`--level-kind gauge-reading`, the default, uses the station's gauge type and
datum; `level-masl` takes metres above sea level as they are) and is validated (at least 2
points, strictly increasing stage, non-decreasing discharge, discharge above 0 for `log_linear`).
A sandbox may invent a table: label it so (`--label SYNTHETIC`):

```bash
uv run python -m sapphire_flow.cli.load_station_observations rating \
  --tenant chwrr --station-code 00001 --network research --csv "$SANDBOX_DIR/rating.csv" \
  --level-column stage_m --discharge-column q_m3s --label SYNTHETIC   # add --apply
uv run python -m sapphire_flow.cli.load_station_observations derive-discharge \
  --tenant chwrr --station-code 00001 --network research             # add --apply
```

An active curve is refused (exit 3) unless you pass `--supersede` (the old curve ends at
`--valid-from`, default now; the new one is the next version). `rating` prints the curve's stage
range next to the stored water-level range and warns when they do not overlap at all (usually a
wrong datum). For a bridge sensor a larger reading means a lower level, so discharge must fall
as the reading rises (the error names the file line). `derive-discharge` converts every stored
measured water level in `--start`/`--end` whose quality status is passed or unchecked (the same
"usable" rule as the forecast cycle) with the active curve (or `--curve-id`), stores it as
`rating_curve_derived` with the curve id, **skips and counts** levels that failed or are
suspect, levels not quality-checked yet (raw), and levels outside the table (never stores a
clamped value). It applies the active curve to every level in the window, so re-running it
after `--supersede` rewrites the derived values. Then runs the same
Stage 1 quality checks (rule version `1.2`, no datum) and prints "N of 30 daily values usable".
Exit 0 ok, 2 usage/validation, 3 refused (including a refused quality check, which rolls the
whole run back).

## Step 7: readiness

```bash
uv run python -m sapphire_flow.cli.station_readiness \
  --tenant chwrr --network research --code 00001 [--model cmal_small] [--weather-scope trishuli] [--json]
```

It writes nothing and gives **facts, not a verdict**: one PASS / FAIL / NOT_APPLICABLE line per
fact (station, datum, basin, statics, weather bindings, weather history, observations, rating
curve, discharge series, group and model, discharge lookback, naming, weather wait facts) and
the last line "N checks failed, M passed". Exit 0 whenever the report ran, 2 for a usage error,
an unknown station or a station of another tenant. Without `--model` the statics check is
NOT_APPLICABLE and the lookback is 30 days; without `--weather-scope` the naming check skips
the comparison with the scope. The `weather_wait_facts` line shows each binding role (bound /
missing / inactive) and the lookback days without stored reanalysis. The newest 7 days of a 30-day lookback are not
expected (the lookback ends yesterday and ERA5 lags by about 8 days; the operational product fills them at forecast time), so
they are not counted as missing: that line tells you whether to wait for the Gateway backfill
or fix a binding. The report does not show the outcome of the station's last forecast cycle
(read it in the cycle logs). Hand the output to the hydrologist.

Read the lines, not only the count: how many pass depends on what you gave it. A first offline
run with no model and no Gateway data shows fewer passes than the "13 passed" in the examples
below (no `--model` makes `statics` not applicable). Without `--model`, `group_and_model`
passes whenever the station is in a group, even when it prints `assignments=none` (it then adds
"no model assigned yet; pass --model to check one"); give
`--model` to make it fail unless that model has an ACTIVE assignment, and read the
`assignments=` part before you treat the station as ready for promotion. `rating_curve` reads
NOT_APPLICABLE while discharge is not a forecast target and not derived from water level alone: a
station that lists `discharge` among its measured parameters (as the Step 3 example does) shows
NOT_APPLICABLE until Step 8 sets the target, even if a curve is already stored. A repeated
`load_station_observations ... --apply` of the same file reports `rows written or changed: 0`
and `0 passed`: nothing new was written and the quality checks were not repeated; it is not a
failure.

**Operational readiness is not the same as enough history for training.** The counted lines
answer "can the forecast cycle run for this station with the model assigned to it". Whether the
stored history is long enough to train or evaluate a model is a separate, optional section:
add `--model M --start ISO --end ISO --time-step-hours H [--minimum-samples N]` (all three of
start, end and step together, and `--model` is required; the step must be one the model
supports). It adds `training_samples` after the counted lines: how many complete training
windows the stored history gives in that window (PASS needs more than 0 and at least
`--minimum-samples`, default 0). Thin history there does not change the operational checks.
Training-sample counts describe available history; for model-specific training requirements,
evaluation and artifact activation, see [Train, evaluate and activate a model](model-onboarding.md).

Two optional sections can follow the counted checks. They are listed under "optional sections
(not counted in the line below)" and never change the last line: `gateway_data`
(`--check-gateway`, described under "Wait rule before the first cycle" above) and
`training_samples`. Illustrative lines:

```text
optional sections (not counted in the line below):
PASS  gateway_data  OK: all 30 days present
FAIL  training_samples  0 complete samples in 2022-03-01..2026-11-09 (lookback 30 + horizon 10 steps)
0 checks failed, 13 passed
```

`onboard_nepal qualify` accepts only the six fixed DHM gauges ("missing DHM station 447") and
only discharge; use this report for any other station. If the command is unavailable, the SQL
below gives the same facts to hand to the hydrologist. In the rehearsal the station code is
`00001`; add `where code = '00001'` (or `s.code`, for the joins) when the database holds more
than one station.

```sql
select code, network, station_status, measured_parameters, water_level_datum_masl, forecast_targets from stations;
select parameter, source, qc_rule_version, qc_status, count(*), min(timestamp), max(timestamp)
  from observations group by 1,2,3,4;
select parameter, count(*), min(valid_time), max(valid_time) from historical_forcing group by 1;
select count(*) from rating_curves;
```

Ready for a model means: observations and weather history overlap for the training or lookback
window, QC ran with the datum, a rating curve exists if the target is discharge, and the model's
static attributes resolve (the aliases in `services/caravan_statics.py` map the model's names to
the package's HydroATLAS names).

## Step 8: switch the station on

Collection and the forecast cycle pick up only `operational` stations. ⛔ Do this step AFTER
"Model and first forecast" items 1 and 2 below (import the artifact, assign the model to the
group): conditions 4, 6 and 7 need the ACTIVE model assignment, so before it the command always
refuses. Dry run first (always allowed, writes nothing), then `--apply`:

```bash
uv run python -m sapphire_flow.cli.promote_station \
  --tenant chwrr --network research --code 00001 --operator "admin test" \
  --weather-scope trishuli [--target discharge] [--apply]
```

It prints seven blocking conditions, all of which must hold, and lists every failing one:

1. status is `onboarding` (`suspended` / `decommissioned` are refused; a station that is already
   `operational` with forecast targets set is a no-op, exit 0);
2. datum and gauge type set if `water_level` is measured;
3. basin set and present;
4. the station is in a group with an ACTIVE model assignment (the model is derived from it, there
   is no `--model`);
5. reanalysis, forecast and operational weather bindings ACTIVE, and the polygon binding's
   Gateway name and polygon name equal the `--weather-scope` entry's (without `--weather-scope`
   this condition fails);
6. the model's static features all resolve;
7. every day of the model's lookback has a usable discharge value (window ends yesterday, like
   the cycle), and an active rating curve exists when water level is measured and the target is
   discharge (with `--target water_level` and measured discharge it is required only if discharge
   derived from level appears in the last 60 days).

`--apply` writes `forecast_targets`, the `operational` status and one `STATION_STATUS_CHANGE`
audit row in one transaction. Exit 0 ok, 2 usage / unknown station, 3 refused (blocking
condition, another tenant's station, or `promotion_allowed` off). `--operator` is required and
is only a label in the audit row. A dry run ends with "Re-run with --apply to write." (or, when
the host switch is off, "NOTE: --apply would be refused"). For a bridge-sensor station it also
prints a warning that the live feed must already deliver converted values (m a.s.l.).

⛔ On a shared host, promotion is the owner's decision: it starts live collection and puts the
station into the forecast cycle, and demoting it later can lose observations. **In the MVP the
only approval is the host switch** `[deployment] promotion_allowed` (default `false`): `--apply`
is refused (exit 3) unless the config file says `promotion_allowed = true`. The sandbox config
above sets it; on a shared host the owner turns it on for one run and off again. It is read from
the config file (`--config` or `SAPPHIRE_CONFIG`), so it is only as strong as control over who
can write that file. The dry run says when `--apply` would be refused for this reason.

Procedure on a shared host: the owner sets `promotion_allowed = true`, the administrator runs
`--apply` once, and the owner sets it back to `false` straight afterwards. There is no
per-person administrator approval in the MVP (a logged-in approval is deferred); `--operator`
is an unverified label.

## Model and first forecast

Follow the [model onboarding guide](model-onboarding.md) for training, evaluation, artifact import
and activation; this station guide covers station readiness, manual group assignment of an
already-imported model, and station promotion. Station promotion does not activate a model
artifact. The training flow can promote artifacts and create assignments automatically when its
checks pass, so the manual `assign_group_model` step is not needed after every training run.

1. **Import the trained artifact** (an existing, trusted file; for `cmal_small` the one already
   in use). Follow the [artifact import runbook](model-artifact-import-runbook.md). In a
   sandbox, put the file in the staging folder (`SAPPHIRE_INCOMING_DIR`), check its SHA-256
   against the source, and take the six provenance values (`trained_at`, training period,
   config hash, ...) from the source host's `model_artifact_provenance` record, or for
   `cmal_small` from the table in [Plan 307](../plans/307-import-a-model-artifact-from-a-staged-path.md).
   `source_repository` may stay empty in a sandbox; the config hash cannot be recomputed
   without the owner's training tree, so use the value recorded in Plan 307 (not the file vendored
   in this repository). That is acceptable for a rehearsal only. Run the `import-model-artifact` deployment with
   `group_id` set to your group.
2. **Assign the model to the group** (after the artifact import; dry run first, then `--apply`):

   ```bash
   uv run python -m sapphire_flow.cli.assign_group_model --tenant chwrr \
     --group-name my-group --model cmal_small --time-step-hours 24 [--priority 1] [--apply]
   ```

   It refuses a model without a `models` row (import the artifact first), an unknown group
   and an INACTIVE assignment. Re-running an identical ACTIVE assignment changes nothing; an
   ACTIVE one that differs is overwritten (time step, priority, `created_at`).
3. **Forecast configuration (not in the guide before):** run with the base `config.toml`, the
   overlays `nepal-cloud.toml` and `nepal-daily-weather.toml`, and a last overlay of your own:

   ```toml
   [operational_weather]
   route = "recap_combined"
   automatic_issue_timezone = "Asia/Kathmandu"

   [adapters.recap_gateway]            # the base config has no such table: this overlay must carry all keys
   base_url = "https://recap.ieasyhydro.org/sdk"
   timeout_s = 300
   verify_tls = true
   staleness_threshold_hours = 6.0
   hru_metadata_source = "manual_gpkg_upload"
   max_retries = 3
   max_cycle_age_hours = 60.0

   [adapters.weather_forecast]
   enabled = true
   type = "recap_gateway"
   ```

**Run one cycle.** The **person** runs it, in a shell where `RECAP_API_KEY_FILE` is set (an
assistant must stop and ask). First read "When a run fails": the observation coverage query must
give 30 (the readiness `discharge_lookback` line counts days in the station's local time zone, the SQL query uses UTC dates, so the two can differ by a day), and the read-only
Gateway check must show data for every day. Use an explicit
UTC-midnight `cycle_time`, no older than the 60-hour limit:

```bash
SAPPHIRE_CONFIG=config.toml \
SAPPHIRE_CONFIG_OVERLAY=config/overlays/nepal-cloud.toml,config/overlays/nepal-daily-weather.toml,/path/to/my-last-overlay.toml \
  uv run python - <<'EOF'
from sapphire_flow.flows.run_forecast_cycle import run_forecast_cycle_flow as f
r = f(cycle_time="2026-11-16T00:00:00Z"); print(r.health, r.forecasts_stored, r.errors)
EOF
```

`forecasts_stored=1` and a `forecasts` row (10 days, 7 quantiles) is the pass. The cycle needs
**observations up to the issue date**; a washed-away or test gauge has none, so a sandbox must
load a synthetic series that runs to today.

## What a good result looks like

**"Degraded" is expected in a sandbox.** The rehearsal ended `DEGRADED`, `forecasts_stored=1`,
no errors. The warnings in that run, and why they do not make the forecast wrong:

- `nwp.grid_stale` (logged as an error, and written as critical to the health table): the
  stored weather grid was 32 to 37 hours old, against a 6-hour limit from the Gateway
  settings. The cause: an **older cycle was replayed** (the sandbox forecasts for a past
  cycle time) and the sandbox has no live grid refresh. A live deployment refreshes it.
- `alert.suppressed_fallback_only`: the model `cmal_small` is declared as **not yet allowed to
  raise alerts** (its alert eligibility is `no_event_information`, set by the owner until it has
  been seen to work on Swiss rivers). So its forecast is stored and ranked but raises no alert.
- Staleness of observations and weather is measured against the **clock**, so replaying an older
  cycle always looks stale.

Also: the forecast **level will not match** the synthetic series. The model is trained on Swiss
rivers. The rehearsal proves the plumbing only; nobody should read the numbers.

**The verified end state of the rehearsal** (check yours against it):

- [ ] one station (`00001`), `operational`, with the gauge datum set;
- [ ] a rating curve whose label says `SYNTHETIC`;
- [ ] every day of the 30-day lookback has discharge with status passed (coverage query = 30);
- [ ] the group, with the `cmal_small` assignment;
- [ ] three weather bindings (from Step 5b, `weather-bindings --apply`);
- [ ] one forecast with quantiles, from the real model.

## When a run fails: finding the real reason

**Why the error looks empty.** The weather history job hides the real error on purpose: the
Gateway library can put the API key inside the web address it reports, so the flow replaces any
error with the line `Operational ERA5 history failed`. That line is all the worker log and the
Prefect dashboard show. In the rehearsal the real cause (the station was not subscribed to the
ERA5 parameters) was found only by asking the Gateway's ERA5 history endpoint directly. The
read-only check in "After registration" above asks the combined weather product, so it does not
show this cause. So when that job fails, go through the Gateway steps in order:
subscribed? ERA5 job finished? dates no later than about 8 days before today? Then rerun.

**The cycle is easier to read.** It prints structured log lines in the terminal where the person
ran it, and its final lines list the errors. Two lines mattered:

- `run_group_forecast.station_inputs_unavailable`: the station was skipped. Its `reason` field
  names the cause. The rehearsal saw `inputs_unavailable` (weather) and `cadence_mismatch`
  (observations).
- `operational_inputs.cadence_mismatch_skip`: the **discharge observations** have gaps. It is
  about the observations, not the weather. Its `error` text names the gap (for example "a gap of
  10 days").

**What `missing_support` means.** It is a weather reason, in the cycle's error list as
"required operational past weather is blocked: missing_support". It means the weather the model
needs is not fully there. For the past leg that is any of: there is no stored past weather
snapshot (this is what a Gateway "source data missing" answer produces); the stored window is
shorter than the model's lookback; no day in it has samples; or a required day has no
precipitation or no temperature for the polygon. For the future leg it means no stored forecast
covers the times and members the model asks for. It does **not** mean our code rejected good
data: in the rehearsal, the frames the Gateway later returned were reduced offline and gave a
value for all 30 days of both variables.

**Check observation coverage before the cycle.** The cycle accepts a daily discharge value only
if its quality status is `qc_passed` or `qc_unchecked`. A `qc_suspect`, `qc_failed` or missing
day counts as a gap and stops the station with `cadence_mismatch`. Count the usable days in
the model's lookback (30 days for `cmal_small`; adjust the date to your cycle time). Expect 30:

```sql
select count(distinct (o.timestamp at time zone 'UTC')::date) as usable_days
  from observations o join stations s on s.id = o.station_id
 where s.code = '00001' and o.parameter = 'discharge' and o.value is not null
   and o.qc_status in ('qc_passed', 'qc_unchecked')
   and o.timestamp >= timestamptz '2026-11-16 00:00+00' - interval '30 days'
   and o.timestamp <  timestamptz '2026-11-16 00:00+00';
```

(The cycle's own window edge can differ by a day. Fewer than 30 means gaps: list the days
with the same query and fix them.) In the rehearsal the first synthetic series gave 7 usable
days of 30 and the stop above; the final series had every day passed. The readiness report's
`discharge_lookback` line counts days in the station's local time zone (the SQL above uses UTC dates, so the counts can differ by a day) and lists the missing ones.

## What the cycle told us when it stopped (so you can recognize it)

| Message | Cause |
|---|---|
| `0 FORECAST weather-source binding(s), expected exactly 1` | `weather-bindings --apply` (Step 5b) not run |
| `missing_required_target` / `operational_inputs.no_observations` | No observations up to the issue date, or no discharge (step 6) |
| `nwp_disabled ... mode=runoff_only` | `[adapters.weather_forecast]` still disabled by the Nepal overlay |
| `selected future weather requires a candidate-aware source` | `type = "recap_gateway"` missing |
| `[adapters.recap_gateway] missing required field(s)` | Only an overlay with part of the keys defines the table (e.g. `nepal-daily-weather.toml` has one); add all keys in your last overlay |
| `recap Gateway API key not found` | None of `/run/secrets/sapphire_dg_api_key`, `RECAP_API_KEY_FILE` or `RECAP_API_KEY` is set in the shell that runs the cycle or the worker. Export `RECAP_API_KEY_FILE=<path to the key file>`; an unreadable or empty file named there is its own error and does not fall through |
| `missing DHM station 447` | `qualify` used for a station that is not one of the six |
| `required operational past weather is blocked: missing_support` | The Gateway's weather for the past days is incomplete or not caught up (see "When a run fails"); wait and run the read-only check |
| `operational_inputs.cadence_mismatch_skip` | Gaps in the discharge observations (suspect or failed days count as gaps) |

Status of the tools this guide uses, and what is still deferred (Gateway runbook fixes beyond
the first message change, further Gateway tool messages, the synthetic-series command and a
dashboard promotion action), is in
[Plan 540](../plans/540-general-station-onboarding-tools.md), "Implementation status".
