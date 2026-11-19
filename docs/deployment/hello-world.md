# Disposable deployment smoke test

Use this generic synthetic scenario to check installation and service wiring before
configuring real stations. It runs independently of private deployment profiles;
a pass does not validate agency data, scientific choices or public host access.

Ask developer support for a normal application image and its matching source checkout,
or prepare the image using the
[existing image build and release-identity instructions](../standards/cicd.md#release-identity-tags-and-receipts).
Its release target, source revision, smoke code and resources must match the
checkout. Building may require private dependency credentials; running the synthetic
scenario requires no agency or provider credentials. The run command never pulls,
builds or retrieves dependency credentials.
Use a clean checkout for qualification: the identity checks compare the recorded
revision and smoke files, not every uncommitted application change.
Preserve checkout file permissions when copying to another host. In particular,
`docker/init-db.sh` must retain its tracked mode `0755`: the disposable database
container reads this bind-mounted script as a non-root user. A restrictive archive
extraction that changes it to `0700` prevents database initialization.
Before running, preload the infrastructure pins from `docker-compose.smoke.yml`:

```sh
docker pull --platform linux/amd64 postgis/postgis:16-3.5@sha256:94146ac37bc61e2322f88016056c5920729cb8c64c8542ed590af8fc2abdac07
docker pull prefecthq/prefect:3-python3.11@sha256:369d8f0bb1fbf84c28b90d9af62dea8ef1b90d261fbc321f211dbba96883613c
```
PostGIS uses `linux/amd64`, including emulation on an ARM Docker host; native ARM
PostGIS support is not claimed by this scenario. Use a local Unix-socket Docker
endpoint and Docker Compose v2 or newer. Install uv and Python 3.12 or newer on
the host. The commands below use only the standard library, skip project dependency
sync and refuse interpreter downloads; no host-side project environment is needed.

Run from the matching checkout, with a new absolute report directory whose parent
already exists:

```sh
uv run --no-project --no-python-downloads python tools/deployment_smoke.py run --image sapphire-flow:<prepared-version> --report-dir /absolute/path/smoke-report
```

The command refuses ambient Docker/Compose overrides. It creates only a unique
project named `sapphire-smoke-<random>`, an internal network,
two disposable data volumes and five services. No ports are published. A worker-side
HTTP client checks the API at `http://api:8000` inside that network; host-port access
is deferred and is not proved by a pass. No agency configuration or existing database
is used. Temporary
secret files live inside an owner-only directory; their read permissions inside
individual mounts support the Linux non-root application user. They are removed
on success and ordinary failure, including a failed cleanup attempt.

A pass means one real process worker ran the normal nested forecast cycle, the
loader's real QC accepted all 72 synthetic readings, the flow persisted weather,
and real HTTP requests from the worker confirmed unauthenticated access returns 401
and authenticated access returns all 60 expected forecast values plus the station's
no-floor metadata. It also means
all owned containers, volumes and networks were removed. It proves neither agency
readiness nor hydrological skill. The synthetic model deliberately has no event
information and no climatology floor. Expected cycle health is `degraded` solely
from alert suppression; expected input quality is `degraded` solely from the
existing cold-start classification. Fresh observations/weather and every other
quality field are checked separately; extra degradation cannot pass.

`report.json` retains safe identities, hashes, stages, comparison counts and
cleanup status, the API probe origin/target and `host_port_verified: false`,
plus elapsed time and available container memory/volume measurements.
It contains no tokens, passwords, connection URLs or raw child logs. Exit 0 requires
forecast and cleanup proof; 2 means preflight refusal; 1 means runtime, assertion
or cleanup failure. Runtime is bounded to ten minutes plus two for cleanup.
Resource minima remain qualification targets rather than measured guarantees.

If cleanup fails or execution is forcibly terminated, use the same directory:

```sh
uv run --no-project --no-python-downloads python tools/deployment_smoke.py cleanup --report-dir /absolute/path/smoke-report
```

Cleanup verifies the recorded local endpoint and per-resource ownership labels.
It refuses foreign resources. Never substitute a production project or run a global
Docker prune. Setup failures name a bounded stage such as observation loading/QC,
artifact import or registration; check the matching prerequisite, then retry with a
new report directory. A missing worker or malformed API response is a failure.
For a preflight `docker_command_failed`, first check that the local Docker daemon
is running and all three required images are already present.

For the opted-in integration test, set `SAPPHIRE_SMOKE_IMAGE` to the prepared image
and run `uv run pytest -m deployment tests/integration/test_deployment_smoke.py`.
This starts and removes the disposable stack; it is excluded from ordinary tests.

## Developer and CI qualification

The mandatory `Qualify deployment smoke twice` gate runs inside the existing image
build job, using that exact locally built image without exporting or rebuilding it.
The private verification path runs the same qualification as an independent image
component, retaining its failure alongside scan and SBOM outcomes.

```sh
uv run --no-project --no-python-downloads python tools/qualify_deployment_smoke.py run --image sapphire-flow:<prepared-version> --report-dir /absolute/path/qualification
```

This helper explicitly prepares the two pinned infrastructure images. It runs the
same smoke CLI twice in new directories beside an unrelated sentinel container,
network and volume. It checks their identity, running/start state and synthetic
content before and after each run, then removes only its own resources. It retains
`qualification.json` as an allowlisted summary; CI uploads that file alone, never
the raw run directories, logs, credentials or application image.

CI builds use `--image-identity ci-development`, accepting only the source-derived
`0.dev0+g<checkout HEAD>` identity. Administrator runs default to `release-target`;
there is no arbitrary expected-version override. `--runtime-timeout-seconds 1..600`
can only shorten a single run's normal ten-minute budget. Qualification uses four
minutes plus two for each run's cleanup, a nineteen-minute overall deadline with
two minutes reserved for recovery, and a twenty-minute CI step limit. Pulls have
ninety-second limits. A timeout fails qualification rather than skipping a check.

After interruption or incomplete qualification cleanup:

```sh
uv run --no-project --no-python-downloads python tools/qualify_deployment_smoke.py cleanup --report-dir /absolute/path/qualification
```

The helper verifies the local endpoint and sentinel labels before removing resources.
For real deployment preparation continue with the [quick-start](../deployment-quickstart.md)
and [private profile reference](profiles.md); this diagnostic never launches a profile.
