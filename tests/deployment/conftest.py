from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import pytest
from testcontainers.compose import DockerCompose

if TYPE_CHECKING:
    from collections.abc import Iterator

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILES = ["docker-compose.yml", "docker-compose.dev.yml"]
API_HOST_PORT = 8010  # dev overlay binds container 8000 to host 8010
API_BASE_URL = f"http://localhost:{API_HOST_PORT}"
HEALTH_PATH = "/api/v1/health"
REQUIRED_SERVICES = (
    "postgres",
    "prefect-server",
    "prefect-worker",
    "api",
    "caddy",
    "init",
)
FIRST_BOOT_TIMEOUT_S = 300
WARM_BOOT_TIMEOUT_S = 180
POLL_INTERVAL_S = 2.0


@dataclass(frozen=True, kw_only=True, slots=True)
class ComposeStackHandle:
    """Session handle exposing the running compose stack to tests."""

    compose: DockerCompose
    project_name: str
    api_base_url: str

    def get_service_container_id(self, service: str) -> str:
        container = self.compose.get_container(service_name=service, include_all=True)
        if container is None or not container.ID:
            raise RuntimeError(f"service {service!r} has no container")
        return container.ID


def _new_compose() -> DockerCompose:
    return DockerCompose(
        context=str(REPO_ROOT),
        compose_file_name=COMPOSE_FILES,
        pull=False,
        build=False,
        wait=False,
        keep_volumes=False,
    )


def _poll_api_health(timeout_s: float) -> None:
    deadline = time.monotonic() + timeout_s
    last_err: Exception | None = None
    url = f"{API_BASE_URL}{HEALTH_PATH}"
    while time.monotonic() < deadline:
        try:
            resp = httpx.get(url, timeout=5.0)
            if resp.status_code == 200:
                body = resp.json()
                if (
                    body.get("status") == "ok"
                    and body.get("prefect_status") == "ok"
                    and "checked_at" in body
                ):
                    return
                last_err = RuntimeError(f"unexpected health body: {body}")
            else:
                last_err = RuntimeError(f"health HTTP {resp.status_code}")
        except (httpx.HTTPError, ValueError) as exc:
            last_err = exc
        time.sleep(POLL_INTERVAL_S)
    raise TimeoutError(
        f"API health did not reach ok within {timeout_s}s; last error: {last_err!r}"
    )


def _assert_init_exited_zero(compose: DockerCompose) -> None:
    container = compose.get_container(service_name="init", include_all=True)
    if container is None:
        raise RuntimeError("init container not found")
    if container.ExitCode != 0:
        raise RuntimeError(
            f"init container exit code = {container.ExitCode} "
            f"(state={container.State!r})"
        )


def _resolve_project_name(compose: DockerCompose) -> str:
    # All containers in a compose project share the same Project label.
    for service in REQUIRED_SERVICES:
        try:
            container = compose.get_container(service_name=service, include_all=True)
        except Exception:
            continue
        if container is not None and container.Project:
            return container.Project
    raise RuntimeError("could not resolve compose project name from any service")


def _boot_stack(compose: DockerCompose, timeout_s: float) -> ComposeStackHandle:
    compose.start()
    try:
        _poll_api_health(timeout_s)
        _assert_init_exited_zero(compose)
        for service in REQUIRED_SERVICES:
            compose.get_container(service_name=service, include_all=True)
        project_name = _resolve_project_name(compose)
    except Exception:
        compose.stop()
        raise
    return ComposeStackHandle(
        compose=compose,
        project_name=project_name,
        api_base_url=API_BASE_URL,
    )


@pytest.fixture(scope="session")
def compose_stack() -> Iterator[ComposeStackHandle]:
    compose = _new_compose()
    handle = _boot_stack(compose, FIRST_BOOT_TIMEOUT_S)
    try:
        yield handle
    finally:
        compose.stop()


@pytest.fixture(scope="function")
def fresh_compose_stack() -> Iterator[ComposeStackHandle]:
    compose = _new_compose()
    handle = _boot_stack(compose, WARM_BOOT_TIMEOUT_S)
    try:
        yield handle
    finally:
        compose.stop()
