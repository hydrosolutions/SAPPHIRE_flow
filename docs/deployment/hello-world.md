# Disposable deployment smoke test

The worker-to-internal-API command has passed local qualification. It is not yet a
released installation check: independent patch review and final full-suite CI remain
required, with the dedicated smoke CI gate and administrator release guidance in
HW1c of [the implementation plan](../plans/deployment-hello-world.md).

For development qualification, prepare the normal application image using the
[existing image build and release-identity instructions](../standards/cicd.md#release-identity-tags-and-receipts).
Its release target, source revision, smoke code and resources must match the
checkout. The command never pulls, builds or retrieves dependency credentials.
Use a clean checkout for qualification: the identity checks compare the recorded
revision and smoke files, not every uncommitted application change.
Preload the exact PostGIS and Prefect images pinned in `docker-compose.smoke.yml`.
PostGIS uses `linux/amd64`, including emulation on an ARM Docker host; native ARM
PostGIS support is not claimed by this scenario. Use a local Unix-socket Docker
endpoint and Docker Compose v2 or newer.

Run from the matching checkout, with a new absolute report directory whose parent
already exists:

```sh
uv run python tools/deployment_smoke.py run --image sapphire-flow:<prepared-version> --report-dir /absolute/path/smoke-report
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
uv run python tools/deployment_smoke.py cleanup --report-dir /absolute/path/smoke-report
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
