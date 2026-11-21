from __future__ import annotations

import asyncio
import json
import sys
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast
from uuid import uuid4

import pytest
from prefect.client.schemas.objects import StateType

from sapphire_flow.cli import deployment_rehearsal as helper

if TYPE_CHECKING:
    from prefect.client.orchestration import PrefectClient

NOW = datetime(2026, 11, 19, tzinfo=UTC)


class PrefectFake:
    def __init__(self) -> None:
        self.pool_objects: dict[str, Any] = {}
        self.created: list[tuple[str, bool]] = []
        self.deployments = [
            SimpleNamespace(id=uuid4(), name=name, work_pool_name=pool)
            for name, pool in helper.JOBS.items()
        ]
        self.schedules = {
            item.id: [SimpleNamespace(id=uuid4(), active=True)]
            for item in self.deployments
        }
        self.runs: list[Any] = []
        self.history: list[Any] = []
        self.heartbeat = NOW
        self.old_heartbeats: list[datetime] = []
        self.disappear = False

    async def read_work_pools(self, **kwargs: Any) -> list[Any]:
        return list(self.pool_objects.values())

    async def create_work_pool(self, item: Any) -> None:
        self.created.append((item.name, item.is_paused))
        self.pool_objects[item.name] = item

    async def read_work_pool(self, name: str) -> Any:
        return self.pool_objects[name]

    async def read_deployments(self, **kwargs: Any) -> list[Any]:
        return self.deployments

    async def read_deployment_schedules(self, identifier: Any) -> list[Any]:
        return list(self.schedules[identifier])

    async def update_deployment_schedule(
        self, identifier: Any, schedule_id: Any, **kwargs: Any
    ) -> None:
        if self.disappear:
            self.schedules[identifier] = []
        else:
            self.schedules[identifier][0].active = False

    async def read_flow_runs(self, **kwargs: Any) -> list[Any]:
        return self.runs

    async def read_flow_run_states(self, identifier: Any) -> list[Any]:
        return self.history

    async def read_workers_for_work_pool(self, name: str, **kwargs: Any) -> list[Any]:
        after = kwargs["worker_filter"].last_heartbeat_time.after_
        return [
            SimpleNamespace(last_heartbeat_time=heartbeat)
            for heartbeat in [*self.old_heartbeats, self.heartbeat]
            if heartbeat >= after
        ][: kwargs["limit"]]

    def client(self) -> PrefectClient:
        return cast("PrefectClient", self)


def prepared_client() -> PrefectFake:
    fake = PrefectFake()
    asyncio.run(helper.pools(fake.client(), "create"))
    return fake


class TestExecutionBarrier:
    def test_all_three_pools_created_paused_before_jobs_and_workers(self) -> None:
        fake = prepared_client()
        assert fake.created == [(name, True) for name in helper.POOLS]
        asyncio.run(helper.jobs(fake.client(), "pause"))
        asyncio.run(helper.workers(fake.client(), NOW))
        assert all(
            not schedule.active
            for values in fake.schedules.values()
            for schedule in values
        )

    def test_existing_pool_refuses_instead_of_adopting(self) -> None:
        fake = prepared_client()
        with pytest.raises(helper.RehearsalCheckError, match="existing_pools_refused"):
            asyncio.run(helper.pools(fake.client(), "create"))

    def test_schedule_disappearance_is_not_pause_evidence(self) -> None:
        fake = prepared_client()
        fake.disappear = True
        with pytest.raises(
            helper.RehearsalCheckError, match="schedule_pause_not_confirmed"
        ):
            asyncio.run(helper.jobs(fake.client(), "pause"))

    @pytest.mark.parametrize("condition", ["unpaused", "extra_job", "stale_worker"])
    def test_barrier_or_heartbeat_failure(self, condition: str) -> None:
        fake = prepared_client()
        asyncio.run(helper.jobs(fake.client(), "pause"))
        if condition == "unpaused":
            fake.pool_objects["default"].is_paused = False
        elif condition == "extra_job":
            fake.deployments.append(
                SimpleNamespace(name="unexpected", work_pool_name="default", id=uuid4())
            )
        else:
            fake.heartbeat = NOW - timedelta(minutes=3)
        with pytest.raises(
            helper.RehearsalCheckError,
            match="pool_pause_not_confirmed|deployment_inventory_invalid|workers_not_ready",
        ):
            asyncio.run(helper.workers(fake.client(), NOW))

    @pytest.mark.parametrize(
        "condition",
        ["running", "start_time", "past_running", "overflow", "history_overflow"],
    )
    def test_no_execution_checks_full_bounded_history(self, condition: str) -> None:
        fake = prepared_client()
        run = SimpleNamespace(
            id=uuid4(), state=SimpleNamespace(type=StateType.PENDING), start_time=None
        )
        fake.runs = [run]
        if condition == "running":
            run.state.type = StateType.RUNNING
        elif condition == "start_time":
            run.start_time = NOW
        elif condition == "past_running":
            fake.history = [
                SimpleNamespace(type=StateType.RUNNING),
                SimpleNamespace(type=StateType.PENDING),
            ]
        elif condition == "overflow":
            fake.runs = [run] * 101
        else:
            fake.history = [run.state] * 1001
        with pytest.raises(
            helper.RehearsalCheckError, match="unexpected_flow_execution|overflow"
        ):
            asyncio.run(helper.no_execution(fake.client()))

    def test_scheduled_and_pending_records_are_not_executions(self) -> None:
        fake = prepared_client()
        fake.runs = [
            SimpleNamespace(
                id=uuid4(),
                state=SimpleNamespace(type=StateType.PENDING),
                start_time=None,
            )
        ]
        fake.history = [SimpleNamespace(type=StateType.SCHEDULED)]
        asyncio.run(helper.no_execution(fake.client()))


class TestHelperBoundary:
    def test_marker_and_exact_prefect_url_required(self) -> None:
        with pytest.raises(
            helper.RehearsalCheckError, match="helper_environment_refused"
        ):
            helper.require_environment(
                {helper.MARKER: "a" * 32, "PREFECT_API_URL": "http://foreign/api"}
            )
        assert (
            helper.require_environment(
                {helper.MARKER: "a" * 32, "PREFECT_API_URL": helper.PREFECT_URL}
            )
            == "a" * 32
        )

    @pytest.mark.parametrize("code", ["workers_not_ready", "unexpected_flow_execution"])
    def test_expected_failure_is_safe_transport_json(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        code: str,
    ) -> None:
        async def failed(action: str, now: datetime) -> None:
            sys.stdout.write("PRIVATE_TOKEN_CANARY")
            raise helper.RehearsalCheckError(code)

        monkeypatch.setattr(helper, "check", failed)
        assert helper.main(["workers"]) == 0
        captured = capsys.readouterr()
        assert json.loads(captured.out) == {"status": "failed", "code": code}
        assert "PRIVATE_TOKEN_CANARY" not in captured.out + captured.err

    def test_dependency_error_is_not_an_output_channel(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        async def failed(action: str, now: datetime) -> None:
            raise ValueError("PRIVATE_TOKEN_CANARY")

        monkeypatch.setattr(helper, "check", failed)
        assert helper.main(["api"]) == 0
        assert json.loads(capsys.readouterr().out) == {
            "status": "failed",
            "code": "helper_check_failed",
        }


class TestTokenConfinement:
    @pytest.mark.parametrize("bad_field", [None, "limit", "offset", "items", "total"])
    def test_token_is_created_and_consumed_only_in_api_process(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        bad_field: str | None,
    ) -> None:
        from contextlib import nullcontext

        from sapphire_flow.api import security
        from sapphire_flow.cli import access_tokens
        from sapphire_flow.config import deployment_identity
        from sapphire_flow.db import engine as db_engine
        from sapphire_flow.store import tenant_store
        from sapphire_flow.types.enums import AccessTokenRole

        canary = "PRIVATE_TOKEN_CANARY"
        used: list[str | None] = []
        created: list[dict[str, Any]] = []
        disposed: list[bool] = []
        tenant = SimpleNamespace(id=uuid4())

        class Engine:
            def begin(self) -> Any:
                return nullcontext(object())

            def dispose(self) -> None:
                disposed.append(True)

        class Store:
            def __init__(self, connection: Any) -> None:
                pass

            def fetch_tenant_by_code(self, code: str) -> Any:
                assert code == "synthetic"
                return tenant

        def engine() -> Any:
            return Engine()

        def identity(path: str) -> Any:
            return SimpleNamespace(
                global_admin=False, writable_tenants=frozenset({"synthetic"})
            )

        def token(connection: Any, **kwargs: Any) -> str:
            created.append(kwargs)
            return canary

        def pepper() -> str:
            return "synthetic-pepper"

        def request(path: str, token: str | None = None) -> tuple[int, bytes]:
            used.append(token)
            if path.endswith("health"):
                return 200, b"{}"
            if token is None:
                return 401, b"{}"
            assert token == canary
            payload: dict[str, Any] = {
                "items": [],
                "total": 0,
                "limit": 50,
                "offset": 0,
            }
            if bad_field:
                payload[bad_field] = ["unexpected"] if bad_field == "items" else 99
            return 200, json.dumps(payload).encode()

        monkeypatch.setenv(
            "DATABASE_URL", "postgresql://sapphire_api:synthetic@postgres/sapphire"
        )
        monkeypatch.setattr(db_engine, "create_engine_from_env", engine)
        monkeypatch.setattr(
            deployment_identity, "load_deployment_identity_config", identity
        )
        monkeypatch.setattr(tenant_store, "PgTenantStore", Store)
        monkeypatch.setattr(access_tokens, "create_token", token)
        monkeypatch.setattr(security, "load_access_token_pepper", pepper)
        monkeypatch.setattr(helper, "request", request)
        if bad_field:
            with pytest.raises(
                helper.RehearsalCheckError, match="api_collection_not_empty"
            ):
                helper.api(NOW)
        else:
            assert helper.api(NOW) is None
        assert created[0]["role"] == AccessTokenRole.CONSUMER
        assert created[0]["station_ids"] == frozenset()
        assert created[0]["tenant_id"] == tenant.id
        assert used == [None, None, canary]
        assert disposed == [True]
        captured = capsys.readouterr()
        assert canary not in captured.out + captured.err


class TestRetainedWorkerHeartbeats:
    def test_old_records_do_not_block_a_restarted_worker(self) -> None:
        fake = prepared_client()
        asyncio.run(helper.jobs(fake.client(), "pause"))
        fake.old_heartbeats = [NOW - timedelta(days=1)] * 200
        asyncio.run(helper.workers(fake.client(), NOW))

    def test_two_recent_workers_are_not_accepted(self) -> None:
        fake = prepared_client()
        asyncio.run(helper.jobs(fake.client(), "pause"))
        fake.old_heartbeats = [NOW - timedelta(seconds=10)]
        with pytest.raises(helper.RehearsalCheckError, match="workers_not_ready"):
            asyncio.run(helper.workers(fake.client(), NOW))
