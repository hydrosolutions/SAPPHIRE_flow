"""Owned disposable outputs for local development only; not a forecast launcher."""

from __future__ import annotations

import os
import subprocess
import sys
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

import sqlalchemy as sa

from sapphire_flow.store.committed_forecast_writer import PgCommittedForecastWriter
from sapphire_flow.store.daily_forecast_store import PgDailyForecastStore
from sapphire_flow.store.forecast_store import PgForecastStore
from sapphire_flow.store.rejected_forecast_store import PgRejectedForecastStore

if TYPE_CHECKING:
    from testcontainers.postgres import PostgresContainer

_REPO_ROOT = Path(__file__).resolve().parents[3]


class LocalOutputState(Enum):
    OPEN = "open"
    RETAINED = "retained"
    DELETED = "deleted"


def _new_container() -> PostgresContainer:
    docker_host = os.environ.get("DOCKER_HOST", "")
    override = os.environ.get("TESTCONTAINERS_HOST_OVERRIDE", "localhost")
    if (docker_host and not docker_host.startswith("unix://")) or override not in (
        "localhost",
        "127.0.0.1",
        "::1",
    ):
        raise RuntimeError("Local outputs forbid remote Docker or host overrides")
    try:
        from testcontainers.postgres import PostgresContainer
    except ImportError as exc:
        raise RuntimeError(
            "Local outputs require optional development testcontainers tooling"
        ) from exc
    container = PostgresContainer(
        image="postgis/postgis:16-3.4",
        username="local_replay",
        password=uuid4().hex,
        dbname="local_replay",
    )
    # No remote Docker daemon / production-host provisioning mode.
    if container.get_docker_client().client.api.base_url != "http+docker://localhost":
        container.stop()
        raise RuntimeError(
            "Local disposable outputs require a local Unix Docker socket"
        )
    # Docker accepts (host IP, ephemeral port); testcontainers' annotation omits it.
    container.with_bind_ports(5432, ("127.0.0.1", None))  # type: ignore[arg-type]
    return container


def _migrate(engine: sa.Engine) -> None:
    # env.py deliberately prefers DATABASE_URL. Isolate that override in a child
    # process, never mutate the parent environment or use the configured live URL.
    env = {
        **os.environ,
        "DATABASE_URL": engine.url.render_as_string(hide_password=False),
    }
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            str(_REPO_ROOT / "alembic.ini"),
            "upgrade",
            "head",
        ],
        cwd=_REPO_ROOT,
        env=env,
        capture_output=True,
        timeout=180,
        check=False,
    )
    if result.returncode:
        # Connection diagnostics may contain credentials; do not include output.
        raise RuntimeError("Owned local output database migration failed")


class LocalOneShotOutputs:
    """Creation owns a new container. close retains evidence; cleanup deletes it."""

    def __init__(self) -> None:
        self._container = _new_container()
        self._engine: sa.Engine | None = None
        self._connection: sa.Connection | None = None
        self._state = LocalOutputState.OPEN
        try:
            self._container.start()
            resource_id = self._container.get_wrapped_container().id
            if not resource_id:
                raise RuntimeError("Owned local container has no resource identity")
            self._resource_id = resource_id
            self._engine = sa.create_engine(
                self._container.get_connection_url().replace("+psycopg2", "+psycopg"),
                connect_args={"connect_timeout": 5},
            )
            _migrate(self._engine)
            self._connection = self._engine.connect()
            self._ledger = PgDailyForecastStore(self._engine.begin)
            self._accepted_writer = PgCommittedForecastWriter(self._engine.begin)
            self._accepted_reader = PgForecastStore(self._connection)
            self._rejected = PgRejectedForecastStore(
                self._connection, transaction_factory=self._engine.begin
            )
        except BaseException:
            self.cleanup()
            raise

    @property
    def resource_id(self) -> str:
        return self._resource_id

    @property
    def state(self) -> LocalOutputState:
        return self._state

    def _require_open(self) -> None:
        if self._state is not LocalOutputState.OPEN:
            raise RuntimeError("Local output runtime is closed")

    @property
    def engine(self) -> sa.Engine:
        """Local staging only. No automatic metadata, input or artifact copy."""
        self._require_open()
        assert self._engine is not None
        return self._engine

    @property
    def ledger(self) -> PgDailyForecastStore:
        self._require_open()
        return self._ledger

    @property
    def accepted_writer(self) -> PgCommittedForecastWriter:
        self._require_open()
        return self._accepted_writer

    @property
    def accepted_reader(self) -> PgForecastStore:
        self._require_open()
        return self._accepted_reader

    @property
    def rejected(self) -> PgRejectedForecastStore:
        self._require_open()
        return self._rejected

    def close(self) -> None:
        """Release connections, retaining this container for local evidence review."""
        if self._state is not LocalOutputState.OPEN:
            return
        try:
            if self._connection is not None:
                self._connection.close()
        finally:
            if self._engine is not None:
                self._engine.dispose()
            self._state = LocalOutputState.RETAINED

    def cleanup(self) -> None:
        """Delete only the container created by this instance; retryable on failure."""
        if self._state is LocalOutputState.DELETED:
            return
        try:
            self.close()
        finally:
            self._container.stop()
            self._state = LocalOutputState.DELETED


def create_local_one_shot_outputs() -> LocalOneShotOutputs:
    return LocalOneShotOutputs()
