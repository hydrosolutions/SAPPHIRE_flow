"""Plan 157 T2 — a cold-start `discover_models()` proof for the
forecast-cycle worker.

The plan's acceptance criterion is "a cold-start `discover_models()` test
in the deployed forecast-cycle worker" — i.e. inside the actual built
`sapphire-flow` container image. That full proof needs `docker build`
against `Dockerfile` (a private-git-dependency BUILD secret,
`recap_dg_client_token`, per `docs/standards/security.md` § recap-dg-client
distribution) and is exercised manually as part of the deploy procedure
(`docs/standards/cicd.md` § Upgrade Procedure) — the same reason no other
test in this suite builds the full application image (see
`tests/integration/db/test_role_bootstrap.py`'s `PostgresContainer`-only,
never a `docker build .`, pattern).

What THIS test proves, without a Docker/secret dependency, is the
MECHANISM the forecast-cycle worker's cold start actually exercises:
`discover_models()`, run in a genuinely FRESH interpreter process (not
this test process's warm import cache — the real risk class for a cold
start: import-order bugs, module-level caching, entry-point resolution
that only works once something else has already been imported), succeeds
and returns the expected native entry points. `prefect-worker-forecast-
cycle` (docker-compose.yml) runs this exact call at the start of every
`forecast-cycle` and `import-model-artifact` flow run
(flows/run_forecast_cycle.py, flows/import_model_artifact.py's
`_resolve_model_task`).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "pyproject.toml").is_file():
            return parent
    raise FileNotFoundError("pyproject.toml not found above test file")


class TestColdStartDiscoverModels:
    def test_discover_models_succeeds_in_a_fresh_process(self) -> None:
        script = (
            "import json\n"
            "from sapphire_flow.services.model_registry import discover_models\n"
            "print(json.dumps(sorted(str(k) for k in discover_models().keys())))\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=_repo_root(),
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )

        assert result.returncode == 0, (
            f"cold-start discover_models() failed:\nstdout={result.stdout}\n"
            f"stderr={result.stderr}"
        )

        discovered = json.loads(result.stdout.strip().splitlines()[-1])
        # Every native SAP3 entry point (pyproject.toml's own
        # sapphire_flow.models group) must survive a genuinely cold start —
        # this is the SAME call the forecast-cycle worker makes first.
        assert "linear_regression_daily" in discovered
        assert "nwp_regression" in discovered
