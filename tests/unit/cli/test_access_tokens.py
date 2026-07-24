"""Plan 147 Slice C (Codex round 2): the access-token CLI must fail closed —
refuse to run without a readable, non-empty pepper — for EVERY subcommand,
including `list`/`revoke`, not just `create`. The pepper is loaded BEFORE the
DB engine, so a missing pepper raises without needing a database."""

from __future__ import annotations

from uuid import uuid4

import pytest

import sapphire_flow.api.security as security
from sapphire_flow.api.security import PepperNotConfiguredError
from sapphire_flow.cli.access_tokens import main


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

    def test_whitespace_only_env_pepper_fails_closed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ACCESS_TOKEN_PEPPER", "   \t")
        with pytest.raises(PepperNotConfiguredError):
            main(["list"])
