# Synthetic offline validation scenario

These files test configuration and service-wiring consistency. They contain
synthetic scientific choices, a fake release identity and references to nonexistent
secret deliveries. **Never launch this fixture.** No station/model rows, artifacts,
credentials or provider access are supplied or certified.

The supported subset registers `forecast-cycle` and `backup-database`; it does not
remove previously registered jobs. The forecast uses stored data with external
forecast weather disabled. All five application containers keep the same config
mount; the idle ingest worker remains present. Existing runtime overrides are
unverified even when this desired configuration passes.

From a checkout with its Python environment and trusted Compose installed, create
an external copy (choose a fresh directory):

```bash
uv run --no-sync python - <<'PY'
import json
from pathlib import Path

checkout = Path.cwd().resolve()
private = Path('/tmp/sapphire-offline-example')
private.mkdir(mode=0o700)  # Refuse to overwrite an existing directory.
replacements = {
    '@CHECKOUT@': json.dumps(str(checkout))[1:-1],
    '@PROFILE_DIR@': json.dumps(str(private.resolve()))[1:-1],
}
for name in ('application.toml', 'profile.toml', 'installation.toml', 'compose.yml'):
    text = (checkout / 'tests/fixtures/deployment_profile' / name).read_text()
    for marker, value in replacements.items():
        text = text.replace(marker, value)
    (private / name).write_text(text)
PY

uv run --no-sync python tools/deployment_profile.py \
  --profile-file /tmp/sapphire-offline-example/profile.toml check \
  --installation /tmp/sapphire-offline-example/installation.toml --format json
```

Expected: exit 0, `PASS_OFFLINE`, no offline diagnostics/pending checks, and a
nonempty `unverified_runtime` list. The command renders Compose without a daemon;
it never starts services, reads secret contents or creates secret files. Missing
Compose remains pending. Use the same source checkout named by the installation;
a different checkout remains outside this validation boundary. Share JSON rather
than local text output when private paths must remain hidden.

`required_inputs = []` means this scenario has no unresolved **local declarations
or referenced files**. It does not claim onboarding or scientific readiness.
Do not create dummy station/model files or remove outstanding local requirements
to obtain a pass. For an actual deployment, start with the generic administrator
[quick-start](../../../docs/deployment-quickstart.md), obtain the agency's actual
choices and preserve the runtime handoff. Field details and coverage limits live
in the [profile reference](../../../docs/deployment/profiles.md).
