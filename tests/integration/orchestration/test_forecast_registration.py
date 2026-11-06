"""Opt-in, disposable Prefect 3.6.25 process-worker / 3.6.29 server contract."""

from __future__ import annotations

import asyncio
import importlib
import json
import os
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit
from uuid import uuid4

import pytest

if TYPE_CHECKING:
    from collections.abc import Iterator

SERVER_IMAGE = (
    "prefecthq/prefect:3-python3.11@sha256:"
    "369d8f0bb1fbf84c28b90d9af62dea8ef1b90d261fbc321f211dbba96883613c"
)
OVERLAY = "SAPPHIRE_CONFIG_OVERLAY"
FORECAST_OVERLAY = "SAPPHIRE_FORECAST_CONFIG_OVERLAY"
MARKERS = (
    OVERLAY,
    FORECAST_OVERLAY,
    "NHS_POOL",
    "NHS_DEPLOYMENT",
    "NHS_RUN",
    "NHS_WORKER",
)
PROBE_SOURCE = """from pathlib import Path
import json
import os
from prefect import flow, __version__

@flow(name="nhs-registration-probe", persist_result=False)
def probe(output: str) -> None:
    assert __version__ == "3.6.25"
    keys = (
        "SAPPHIRE_CONFIG_OVERLAY", "SAPPHIRE_FORECAST_CONFIG_OVERLAY",
        "NHS_POOL", "NHS_DEPLOYMENT", "NHS_RUN", "NHS_WORKER"
    )
    Path(output).write_text(json.dumps({key: os.environ.get(key) for key in keys}))
"""

pytestmark = pytest.mark.skipif(
    os.environ.get("SAPPHIRE_TEST_PREFECT_REGISTRATION") != "1",
    reason="requires explicitly authorized disposable local Docker infrastructure",
)


def _local_docker_socket(raw: str | None) -> str:
    if raw is None or any(ord(char) < 33 for char in raw):
        raise ValueError("DOCKER_HOST must name an explicit absolute local unix socket")
    parsed = urlsplit(raw)
    if (
        not raw.startswith("unix:///")
        or parsed.scheme != "unix"
        or parsed.netloc
        or parsed.query
        or parsed.fragment
        or "?" in raw
        or "#" in raw
        or "%" in parsed.path
        or parsed.path.startswith("//")
        or not Path(parsed.path).is_absolute()
    ):
        raise ValueError("DOCKER_HOST must name an explicit absolute local unix socket")
    return raw


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "",
        "tcp://127.0.0.1:2375",
        "ssh://localhost",
        "unix://localhost/var/run/docker.sock",
        "unix://relative.sock",
        "unix:///var/run/docker.sock?option=1",
        "unix:///var/run/docker.sock#fragment",
        "unix:///var/run/docker.sock?",
        "unix:///var/run/docker.sock#",
        "unix:relative.sock",
        "unix:////host/socket",
        "unix:///tmp/%2fsocket",
    ],
)
def test_docker_endpoint_rejects_implicit_or_nonlocal_transport(
    raw: str | None,
) -> None:
    with pytest.raises(ValueError, match="explicit absolute local unix socket"):
        _local_docker_socket(raw)


def test_docker_endpoint_preserves_explicit_local_socket() -> None:
    value = "unix:///var/run/docker.sock"
    assert _local_docker_socket(value) == value


@pytest.fixture
def prefect_server(tmp_path: Path) -> Iterator[tuple[str, Path]]:
    from docker.errors import ImageNotFound
    from docker.types import LogConfig

    import docker

    endpoint = _local_docker_socket(os.environ.get("DOCKER_HOST"))
    logs = Path(os.environ.get("NHS_PREFECT_HARNESS_LOG_DIR", str(tmp_path / "logs")))
    logs.mkdir(parents=True, exist_ok=True)
    client = docker.DockerClient(base_url=endpoint)
    try:
        client.images.get(SERVER_IMAGE)
    except ImageNotFound:
        client.close()
        pytest.skip("pinned Prefect server image is not local; never pull in this test")
    version = client.containers.run(
        SERVER_IMAGE,
        ["version"],
        entrypoint="prefect",
        network_mode="none",
        log_config=LogConfig(type="json-file"),
        remove=True,
    ).decode()
    (logs / "server-version.txt").write_text(version)
    assert "3.6.29" in version
    # Docker Desktop does not publish ports on internal networks. This isolated
    # test stack is loopback-bound, but NOT OS-egress-isolated (server or worker).
    network = client.networks.create(f"nhs-registration-{uuid4().hex}", internal=False)
    container = None
    import socket

    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        host_port = reservation.getsockname()[1]
    try:
        container = client.containers.run(
            SERVER_IMAGE,
            ["server", "start", "--host", "0.0.0.0"],
            entrypoint="prefect",
            detach=True,
            log_config=LogConfig(type="json-file"),
            network=network.name,
            ports={"4200/tcp": ("127.0.0.1", host_port)},
            healthcheck={
                "test": [
                    "CMD",
                    "python",
                    "-c",
                    (
                        "import urllib.request; "
                        "urllib.request.urlopen("
                        "'http://127.0.0.1:4200/api/health', timeout=2)"
                    ),
                ],
                "interval": 1_000_000_000,
                "timeout": 3_000_000_000,
                "retries": 60,
            },
            environment={
                "PREFECT_HOME": "/tmp/prefect-registration",
                "PREFECT_SERVER_ANALYTICS_ENABLED": "false",
                "PREFECT_SERVER_UI_ENABLED": "false",
                "PREFECT_API_DATABASE_CONNECTION_URL": "sqlite+aiosqlite:////tmp/prefect-registration/server.db",
            },
        )
        # Docker's bounded native health event replaces application-side polling.
        from datetime import UTC, datetime, timedelta

        events = client.events(  # pyright: ignore[reportUnknownMemberType]
            since=datetime.now(UTC) - timedelta(seconds=10),
            until=datetime.now(UTC) + timedelta(seconds=90),
            filters={"container": container.id, "event": "health_status"},
            decode=True,
        )
        try:
            healthy = any(
                event.get("Action") == "health_status: healthy" for event in events
            )
        finally:
            events.close()
        if not healthy:
            raise RuntimeError("disposable Prefect server did not become healthy")
        container.reload()
        port = container.attrs["NetworkSettings"]["Ports"]["4200/tcp"][0]["HostPort"]
        yield f"http://127.0.0.1:{port}/api", logs
    finally:
        if container is not None:
            (logs / "server.log").write_bytes(container.logs())
            container.remove(force=True, v=True)
        network.remove()
        client.close()


def test_real_registration_preservation_and_worker_precedence(
    prefect_server: tuple[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import prefect
    from prefect.context import SettingsContext
    from prefect.settings import Profile, Settings

    assert prefect.__version__ == "3.6.25"
    api_url, logs = prefect_server
    (logs / "client-version.txt").write_text(prefect.__version__ + "\n")
    probe_directory = tmp_path / "src"
    probe_directory.mkdir()
    (probe_directory / "nhs_registration_probe.py").write_text(PROBE_SOURCE)
    monkeypatch.syspath_prepend(str(probe_directory))  # pyright: ignore[reportUnknownMemberType]
    # Host worker is local-only by construction, not OS-egress-isolated.
    for key in tuple(os.environ):
        if key not in {"PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "TMPDIR"}:
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("PREFECT_PROFILES_PATH", str(tmp_path / "profiles.toml"))
    monkeypatch.setenv("PREFECT_API_URL", api_url)
    monkeypatch.setenv("PREFECT_HOME", str(tmp_path / "client-home"))
    monkeypatch.setenv("PREFECT_SERVER_ALLOW_EPHEMERAL_MODE", "false")
    monkeypatch.setenv("PREFECT_SERVER_ANALYTICS_ENABLED", "false")
    monkeypatch.setenv("PREFECT_LOGGING_TO_API_ENABLED", "false")
    monkeypatch.delenv("PREFECT_API_KEY", raising=False)
    monkeypatch.setenv(OVERLAY, "worker-overlay")
    monkeypatch.setenv("NHS_WORKER", "worker")
    for key in ("NHS_POOL", "NHS_DEPLOYMENT", "NHS_RUN"):
        monkeypatch.delenv(key, raising=False)
    # Build settings anew: temporary_settings would retain credentials already
    # loaded into the caller's SettingsContext before environment cleanup.
    with SettingsContext(
        profile=Profile(name="local-test", settings={}), settings=Settings()
    ):
        asyncio.run(_exercise_registration(tmp_path, logs, monkeypatch))


async def _exercise_registration(
    directory: Path, logs: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from prefect.client.orchestration import get_client
    from prefect.client.schemas.actions import (
        DeploymentUpdate,
        WorkPoolCreate,
        WorkPoolUpdate,
    )
    from prefect.workers.process import ProcessWorker

    from sapphire_flow.cli import register_deployments as registration
    from sapphire_flow.cli.register_deployments import (
        _register_one as register_one,  # pyright: ignore[reportPrivateUsage]
    )
    from sapphire_flow.config.river_stations import (
        FORECAST_PROVIDER_PATH,
        ForecastConfigOverlay,
    )
    from tests.unit.tools.test_forecast_config import configuration
    from tools.forecast_config import verify_forecast_configuration

    monkeypatch.setattr(registration, "FLOW_SOURCE_ROOT", str(directory))
    probe = importlib.import_module("nhs_registration_probe").probe
    pool = f"nhs-registration-{uuid4().hex}"
    template = ProcessWorker.get_default_base_job_template()
    pool_environment = {OVERLAY: "pool-overlay", "NHS_POOL": "pool"}
    template["variables"]["properties"]["env"]["default"] = pool_environment
    evidence: dict[str, Any] = {}
    async with get_client() as client:
        await client.create_work_pool(
            WorkPoolCreate(name=pool, type="process", base_job_template=template)
        )
        spec = registration.DeploymentSpec(
            flow_module="nhs_registration_probe",
            flow_attr="probe",
            deployment_name="forecast-cycle",
            work_pool_name=pool,
        )
        name = f"{probe.name}/forecast-cycle"
        sourced = await probe.afrom_source(
            source=str(directory), entrypoint="src/nhs_registration_probe.py:probe"
        )
        deploy_arguments = dict(
            name="forecast-cycle",
            work_pool_name=pool,
            build=False,
            push=False,
            print_next_steps=False,
        )
        deployment_id = await sourced.adeploy(**deploy_arguments)
        unrelated_environment = {
            "NHS_DEPLOYMENT": "deployment",
            OVERLAY: "unrelated-overlay",
        }
        unrelated = {"env": unrelated_environment, "stream_output": False}
        await client.update_deployment(
            deployment_id, DeploymentUpdate(job_variables=unrelated)
        )
        # Original registration omitted job_variables; measure real preservation.
        await sourced.adeploy(**deploy_arguments)
        baseline = await client.read_deployment(deployment_id)
        evidence["omitted_job_variables"] = baseline.job_variables
        assert baseline.job_variables == unrelated
        sibling_id = await sourced.adeploy(
            **{**deploy_arguments, "name": "unrelated-job"},
            job_variables={"env": {"NHS_DEPLOYMENT": "sibling"}},
        )
        sibling_before = await client.read_deployment(sibling_id)
        selection = ForecastConfigOverlay(
            paths=(directory / "base-overlay.toml", FORECAST_PROVIDER_PATH)
        )
        configured = replace(spec, forecast_config_overlay=selection)
        expected = {
            OVERLAY: "worker-overlay",
            FORECAST_OVERLAY: selection.environment_value(),
            "NHS_POOL": "pool",
            "NHS_DEPLOYMENT": "deployment",
            "NHS_RUN": None,
            "NHS_WORKER": "worker",
        }

        async def execute(
            label: str, job_variables: dict[str, Any] | None = None
        ) -> dict[str, str | None]:
            output = directory / f"{label}.json"
            run = await client.create_flow_run_from_deployment(
                deployment_id,
                parameters={"output": str(output)},
                job_variables=job_variables,
            )
            await asyncio.wait_for(
                ProcessWorker(work_pool_name=pool, name=f"probe-{uuid4().hex}").start(
                    run_once=True
                ),
                timeout=120,
            )
            finished = await client.read_flow_run(run.id)
            assert finished.state is not None and finished.state.is_completed(), (
                finished.state
            )
            observed = json.loads(output.read_text())
            assert set(observed) == set(MARKERS)
            evidence[label] = observed
            return observed

        try:
            evidence["registrar_submitted"] = {
                FORECAST_OVERLAY: selection.environment_value()
            }
            for _ in range(2):
                await register_one(configured)
            registered = await client.read_deployment_by_name(name)
            assert registered.id == deployment_id
            assert registered.schedules == []
            assert registered.job_variables == {
                **unrelated,
                "env": {
                    **unrelated_environment,
                    FORECAST_OVERLAY: selection.environment_value(),
                },
            }
            evidence["registrar_persisted"] = registered.job_variables
            assert await execute("registrar_observed") == expected
            assert await execute("restart") == expected
            assert await execute(
                "run_override",
                {"env": {FORECAST_OVERLAY: "run-selection", "NHS_RUN": "run"}},
            ) == {**expected, FORECAST_OVERLAY: "run-selection", "NHS_RUN": "run"}
            # Generic env still follows the measured old behavior; it is unrelated
            # configuration, not the corrected registrar's owned key.
            assert (
                await execute(
                    "generic_worker_shadows_run", {"env": {OVERLAY: "run-generic"}}
                )
            )[OVERLAY] == "worker-overlay"
            monkeypatch.delenv(OVERLAY)
            assert (await execute("generic_worker_absent"))[
                OVERLAY
            ] == "unrelated-overlay"
            monkeypatch.setenv(OVERLAY, "worker-overlay")
            await register_one(spec)
            cleared = await client.read_deployment(deployment_id)
            assert cleared.job_variables == unrelated
            evidence["registrar_cleared"] = cleared.job_variables
            assert await execute("allowed_clear") == {
                **expected,
                FORECAST_OVERLAY: None,
            }
            sibling_after = await client.read_deployment(sibling_id)
            assert sibling_after.job_variables == sibling_before.job_variables

            # Deliberately bypass activation preflight to measure WHY each
            # competing authority must be rejected, not to endorse its use.
            pool_environment[FORECAST_OVERLAY] = "pool-selection"
            shadow_metadata = configuration()
            shadow_metadata["pool"]["forecast_overlay_in_defaults"] = True
            with pytest.raises(ValueError, match="shadowed by pool"):
                verify_forecast_configuration(shadow_metadata)
            await client.update_work_pool(
                pool, WorkPoolUpdate(base_job_template=template)
            )
            assert (await execute("forbidden_pool_after_clear"))[
                FORECAST_OVERLAY
            ] == "pool-selection"
            await register_one(configured)
            assert (await execute("deployment_over_pool"))[
                FORECAST_OVERLAY
            ] == selection.environment_value()
            monkeypatch.setenv(FORECAST_OVERLAY, "worker-selection")
            worker_shadow_metadata = configuration()
            worker_shadow_metadata["baseline"]["services"]["prefect-worker"][
                "environment"
            ][FORECAST_OVERLAY] = "worker-selection"
            with pytest.raises(ValueError, match="must not be set on service"):
                verify_forecast_configuration(worker_shadow_metadata)
            assert (
                await execute(
                    "forbidden_worker_over_run",
                    {"env": {FORECAST_OVERLAY: "run-selection"}},
                )
            )[FORECAST_OVERLAY] == "worker-selection"
            await register_one(spec)
            assert (await execute("forbidden_worker_after_clear"))[
                FORECAST_OVERLAY
            ] == "worker-selection"
            monkeypatch.delenv(FORECAST_OVERLAY)
            del pool_environment[FORECAST_OVERLAY]
            await client.update_work_pool(
                pool, WorkPoolUpdate(base_job_template=template)
            )
            assert (await execute("restored_allowed_clear"))[FORECAST_OVERLAY] is None

            await client.update_deployment(
                deployment_id, DeploymentUpdate(job_variables={})
            )
            empty_patch = await client.read_deployment(deployment_id)
            evidence["empty_patch_job_variables"] = empty_patch.job_variables
            assert empty_patch.job_variables == unrelated
            # A nonempty replacement does not deep-merge unrelated keys.
            await client.update_deployment(
                deployment_id,
                DeploymentUpdate(
                    job_variables={"env": {"NHS_DEPLOYMENT": "replacement"}}
                ),
            )
            replacement = await client.read_deployment(deployment_id)
            evidence["nonempty_patch_job_variables"] = replacement.job_variables
            assert replacement.job_variables == {
                "env": {"NHS_DEPLOYMENT": "replacement"}
            }
            await client.delete_deployment(deployment_id)
            await register_one(spec)
            default = await client.read_deployment_by_name(name)
            deployment_id = default.id
            assert default.job_variables == {}
            assert (await execute("default"))[FORECAST_OVERLAY] is None
            await register_one(configured)
            await register_one(spec)
            empty = await client.read_deployment(deployment_id)
            assert empty.job_variables == {"env": {}}
            assert (await execute("last_owned_key_cleared"))[FORECAST_OVERLAY] is None
        finally:
            (logs / "observations.json").write_text(
                json.dumps(evidence, indent=2) + "\n"
            )
