from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, nullcontext
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest

from sapphire_flow.cli import deployment_install as helper
from tests.unit.cli.test_deployment_rehearsal import NOW, prepared_client

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from prefect.client.orchestration import PrefectClient


class TestPersistentGuard:
    def test_rehearsal_marker_cannot_authorize_installation_helper(self) -> None:
        with pytest.raises(
            helper.DeploymentCheckError, match="helper_environment_refused"
        ):
            helper.require_environment(
                {
                    "SAPPHIRE_REHEARSAL_RUN_ID": "a" * 32,
                    "PREFECT_API_URL": helper.PREFECT_URL,
                }
            )

    def test_verify_only_reads_paused_state(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from prefect.client import orchestration

        fake = prepared_client()
        asyncio.run(helper.jobs(fake.client(), "pause"))
        created = list(fake.created)

        @asynccontextmanager
        async def client() -> AsyncIterator[PrefectClient]:
            yield fake.client()

        async def refuse_write(*args: Any, **kwargs: Any) -> None:
            pytest.fail("verification attempted a Prefect write")

        def refuse_api(*args: Any, **kwargs: Any) -> None:
            pytest.fail("verification attempted to mint a token")

        monkeypatch.setenv(helper.MARKER, "a" * 32)
        monkeypatch.setenv("PREFECT_API_URL", helper.PREFECT_URL)
        monkeypatch.delenv("SAPPHIRE_REHEARSAL_RUN_ID", raising=False)
        monkeypatch.setattr(orchestration, "get_client", client)
        monkeypatch.setattr(fake, "create_work_pool", refuse_write)
        monkeypatch.setattr(fake, "update_deployment_schedule", refuse_write)
        monkeypatch.setattr(helper, "api", refuse_api)
        asyncio.run(helper.check("verify-state", NOW))
        assert fake.created == created


class TestProbeToken:
    @pytest.mark.parametrize("failure", [None, "api", "revocation"])
    def test_revokes_and_confirms_temporary_token_on_success_and_failure(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        failure: str | None,
    ) -> None:
        from sapphire_flow.api import security
        from sapphire_flow.cli import access_tokens
        from sapphire_flow.config import deployment_identity
        from sapphire_flow.db import engine as db_engine
        from sapphire_flow.store import access_token_store, tenant_store
        from sapphire_flow.types.enums import AccessTokenRole

        token_id = uuid4()
        tenant_id = uuid4()
        created: list[dict[str, Any]] = []
        revoked: list[Any] = []
        disposed: list[bool] = []
        used: list[str | None] = []
        canary = "PRIVATE_TOKEN_CANARY"

        class Engine:
            def begin(self) -> Any:
                return nullcontext(object())

            def connect(self) -> Any:
                return nullcontext(object())

            def dispose(self) -> None:
                disposed.append(True)

        class Tenants:
            def __init__(self, conn: Any) -> None:
                pass

            def fetch_tenant_by_code(self, code: str) -> Any:
                return SimpleNamespace(id=tenant_id)

        class Tokens:
            def __init__(self, conn: Any) -> None:
                pass

            def fetch_token(self, identifier: Any) -> Any:
                assert identifier == token_id
                return SimpleNamespace(disabled_at=NOW if revoked else None)

        def create(conn: Any, **kwargs: Any) -> str:
            created.append(kwargs)
            assert kwargs["id_gen"]() == token_id
            return canary

        def revoke(conn: Any, **kwargs: Any) -> None:
            revoked.append(kwargs["token_id"])

        def request(path: str, token: str | None = None) -> tuple[int, bytes]:
            used.append(token)
            if path.endswith("health"):
                return 200, b"{}"
            if token is None:
                return 401, b"{}"
            assert token == canary
            if revoked:
                return (200 if failure == "revocation" else 401), b"{}"
            return (502 if failure == "api" else 200), b'{"items":[],"total":0}'

        monkeypatch.setenv(
            "DATABASE_URL", "postgresql://sapphire_api:synthetic@postgres/sapphire"
        )
        monkeypatch.setattr(db_engine, "create_engine_from_env", Engine)

        def identity(path: str) -> SimpleNamespace:
            return SimpleNamespace(
                global_admin=False, writable_tenants=frozenset({"synthetic"})
            )

        monkeypatch.setattr(
            deployment_identity,
            "load_deployment_identity_config",
            identity,
        )
        monkeypatch.setattr(tenant_store, "PgTenantStore", Tenants)
        monkeypatch.setattr(access_token_store, "PgAccessTokenStore", Tokens)
        monkeypatch.setattr(access_tokens, "create_token", create)
        monkeypatch.setattr(access_tokens, "revoke_token", revoke)
        monkeypatch.setattr(
            security, "load_access_token_pepper", lambda: "private-pepper"
        )
        monkeypatch.setattr(helper, "request", request)
        if failure:
            with pytest.raises(
                helper.DeploymentCheckError,
                match="api_authenticated_check_failed|probe_token_revocation_failed",
            ):
                helper.api(NOW, id_gen=lambda: token_id)
        else:
            helper.api(NOW, id_gen=lambda: token_id)
        assert revoked == [token_id]
        assert created[0]["role"] == AccessTokenRole.CONSUMER
        assert created[0]["station_ids"] == frozenset()
        assert created[0]["tenant_id"] == tenant_id
        assert used == [None, None, canary, canary]
        assert disposed == [True]
        output = capsys.readouterr()
        assert canary not in output.out + output.err
