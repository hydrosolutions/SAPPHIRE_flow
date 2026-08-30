"""Plan 147 Slice C (Codex round 2): the access-token CLI must fail closed —
refuse to run without a readable, non-empty pepper — for EVERY subcommand,
including `list`/`revoke`, not just `create`. The pepper is loaded BEFORE the
DB engine, so a missing pepper raises without needing a database."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

import sapphire_flow.api.security as security
from sapphire_flow.api.security import PepperNotConfiguredError
from sapphire_flow.cli.access_tokens import _print_token_row, main
from sapphire_flow.types.auth import AccessToken
from sapphire_flow.types.datetime import UtcDatetime
from sapphire_flow.types.enums import AccessTokenRole
from sapphire_flow.types.ids import AccessTokenId, TenantId


@pytest.fixture(autouse=True)
def _no_pepper(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    """Neither the Docker-secret file nor the env var provides a pepper.

    Also stub `configure_cli_logging` (main() calls it) so this test never
    mutates the process-global structlog config — reconfiguring it here would
    break later log-assertion tests in the full suite (test pollution).
    """
    monkeypatch.delenv("ACCESS_TOKEN_PEPPER", raising=False)
    monkeypatch.setattr(
        security, "DEFAULT_ACCESS_TOKEN_PEPPER_PATH", tmp_path / "no-pepper-here"
    )
    monkeypatch.setattr(
        "sapphire_flow.logging.configure_cli_logging", lambda *a, **k: None
    )


class TestCliFailsClosedWithoutPepperForEverySubcommand:
    def test_list_fails_closed(self) -> None:
        with pytest.raises(PepperNotConfiguredError):
            main(["list"])

    def test_revoke_fails_closed(self) -> None:
        with pytest.raises(PepperNotConfiguredError):
            main(["revoke", str(uuid4())])

    def test_create_fails_closed(self) -> None:
        with pytest.raises(PepperNotConfiguredError):
            main(["create", "--name", "x", "--tenant", "sapphire"])

    def test_create_admin_fails_closed(self) -> None:
        with pytest.raises(PepperNotConfiguredError):
            main(["create-admin", "--name", "boot"])

    def test_show_fails_closed(self) -> None:
        with pytest.raises(PepperNotConfiguredError):
            main(["show", str(uuid4())])

    def test_grant_fails_closed(self) -> None:
        with pytest.raises(PepperNotConfiguredError):
            main(["grant", str(uuid4()), str(uuid4())])

    def test_revoke_station_fails_closed(self) -> None:
        with pytest.raises(PepperNotConfiguredError):
            main(["revoke-station", str(uuid4()), str(uuid4())])

    def test_set_scope_mode_fails_closed(self) -> None:
        with pytest.raises(PepperNotConfiguredError):
            main(["set-scope-mode", str(uuid4()), "tenant"])

    def test_whitespace_only_env_pepper_fails_closed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ACCESS_TOKEN_PEPPER", "   \t")
        with pytest.raises(PepperNotConfiguredError):
            main(["list"])


class TestPrintTokenRow:
    """Plan 202: the tenant column must render a UUID `tenant_id`, not raise.

    `UUID` implements no `__format__`, so `f"{uuid:36}"` is a `TypeError` that
    aborts the whole listing — one consumer token hid every token.
    """

    @staticmethod
    def _token(tenant_id: TenantId | None) -> AccessToken:
        role = AccessTokenRole.ADMIN if tenant_id is None else AccessTokenRole.CONSUMER
        return AccessToken(
            id=AccessTokenId(UUID("11111111-1111-1111-1111-111111111111")),
            token_hash="0" * 64,
            key_prefix="sapk_test",
            name="flow-map",
            role=role,
            tenant_id=tenant_id,
            pepper_version=1,
            expires_at=UtcDatetime(datetime(2027, 1, 1, tzinfo=UTC)),
            disabled_at=None,
            created_at=UtcDatetime(datetime(2026, 1, 1, tzinfo=UTC)),
            last_used_at=None,
            station_ids=frozenset(),
        )

    def test_renders_consumer_and_admin_tenants_at_a_fixed_width(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        tenant = TenantId(UUID("4b0f12d4-24da-4d2f-bf32-38eaaacdf214"))

        _print_token_row(self._token(tenant))
        _print_token_row(self._token(None))

        consumer, admin = capsys.readouterr().out.splitlines()
        # Width-locked: a bare str() fix that drops the `:36` passes on the
        # consumer row (a UUID is already 36 chars) but misaligns the admin one.
        assert "tenant=4b0f12d4-24da-4d2f-bf32-38eaaacdf214  active  " in consumer
        assert "tenant=-" + " " * 35 + "  active  " in admin


class TestArgumentParsingForNewVerbs:
    """Plan 215 T1/T2/T6: argparse validation happens BEFORE the pepper
    check, so these raise SystemExit(2) with no DB and no pepper needed —
    exercising the CLI argument parsing the plan's exit criteria call for."""

    def test_grant_requires_a_station_id(self) -> None:
        with pytest.raises(SystemExit):
            main(["grant", str(uuid4())])

    def test_revoke_station_requires_a_station_id(self) -> None:
        with pytest.raises(SystemExit):
            main(["revoke-station", str(uuid4())])

    def test_show_requires_a_token_id(self) -> None:
        with pytest.raises(SystemExit):
            main(["show"])

    def test_set_scope_mode_requires_a_mode(self) -> None:
        with pytest.raises(SystemExit):
            main(["set-scope-mode", str(uuid4())])

    def test_set_scope_mode_rejects_an_invalid_mode_choice(self) -> None:
        with pytest.raises(SystemExit):
            main(["set-scope-mode", str(uuid4()), "not-a-real-mode"])
