from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest
import sqlalchemy as sa

from sapphire_flow.cli import onboard_nepal
from sapphire_flow.config.deployment_identity import load_deployment_identity_config
from sapphire_flow.config.recap_gateway import load_recap_gateway_config

if TYPE_CHECKING:
    from sapphire_flow.config.deployment_identity import DeploymentIdentityConfig
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.nepal_onboarding import HistoryWindow


class TestOnboardNepal:
    @pytest.mark.parametrize(
        "scenario, expected_rows, expected_exit",
        [
            ("complete", 3, 0),
            ("partial", 3, 1),
            ("failure", 1, 1),
            ("dry_run", 0, 0),
        ],
    )
    def test_history_batches_commit_independently(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        scenario: str,
        expected_rows: int,
        expected_exit: int,
    ) -> None:
        monkeypatch.delenv("SAPPHIRE_CONFIG_OVERLAY", raising=False)
        engine = sa.create_engine(f"sqlite:///{tmp_path / 'batches.db'}")
        with engine.begin() as conn:
            conn.execute(sa.text("CREATE TABLE batches (ordinal INTEGER)"))
        monkeypatch.setattr(onboard_nepal, "create_engine_from_env", lambda: engine)
        monkeypatch.setattr(onboard_nepal, "build_history_adapter", lambda *a: None)
        calls = 0

        def ingest(
            conn: sa.Connection,
            identity: DeploymentIdentityConfig,
            adapter: object,
            window: HistoryWindow,
            *,
            now: UtcDatetime,
        ) -> tuple[SimpleNamespace, ...]:
            nonlocal calls
            calls += 1
            assert (window.end - window.start).days <= 31
            # Another connection sees previous commits, never the current batch.
            with engine.connect() as observer:
                assert observer.scalar(sa.text("SELECT count(*) FROM batches")) == (
                    0 if scenario == "dry_run" else calls - 1
                )
            conn.execute(
                sa.text("INSERT INTO batches VALUES (:ordinal)"), {"ordinal": calls}
            )
            if scenario == "failure" and calls == 2:
                raise RuntimeError("later batch failed")
            return (
                SimpleNamespace(
                    code="447",
                    parameter="temperature",
                    rows=0 if scenario == "partial" else 1,
                    start=window.start,
                    end=window.end,
                ),
            )

        monkeypatch.setattr(
            onboard_nepal,
            "ingest_recap_era5_reanalysis_flow",
            SimpleNamespace(fn=ingest),
        )
        args = [
            "history",
            "--config",
            "config/overlays/chwrr-import.toml",
            "--tenant",
            "chwrr",
            "--confirm-t8",
            "--start",
            "2020-01-01T00:00:00Z",
            "--end",
            "2020-03-11T00:00:00Z",
        ]
        if scenario == "dry_run":
            args.append("--dry-run")
        assert onboard_nepal.main(args) == expected_exit
        with engine.connect() as conn:
            assert conn.scalar(sa.text("SELECT count(*) FROM batches")) == expected_rows
        engine.dispose()

    def test_operator_overlays_scope_identity_and_configure_gateway(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SAPPHIRE_RECAP_BASE_URL", "https://gateway.example.org")
        monkeypatch.setenv(
            "SAPPHIRE_CONFIG_OVERLAY",
            "config/overlays/nepal-history.toml,config/overlays/chwrr-import.toml",
        )
        identity = load_deployment_identity_config(Path("config.toml"))
        assert identity.writable_tenants == frozenset({"chwrr"})
        assert not identity.global_admin
        assert (
            load_recap_gateway_config(Path("config.toml")).base_url
            == "https://gateway.example.org"
        )

    def test_wrong_identity_fails_before_database_connection(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("SAPPHIRE_CONFIG_OVERLAY", raising=False)

        def fail_if_connected() -> None:
            pytest.fail("wrong tenant must be rejected before database access")

        monkeypatch.setattr(onboard_nepal, "create_engine_from_env", fail_if_connected)
        assert (
            onboard_nepal.main(
                [
                    "history",
                    "--config",
                    "config.toml",
                    "--tenant",
                    "chwrr",
                    "--confirm-t8",
                    "--start",
                    "2020-01-01T00:00:00Z",
                    "--end",
                    "2021-01-01T00:00:00Z",
                ]
            )
            == 1
        )

    def test_basin_import_requires_snow_and_join_acknowledgments(self) -> None:
        with pytest.raises(SystemExit, match="2"):
            onboard_nepal.parser().parse_args(
                [
                    "basins",
                    "--config",
                    "config.toml",
                    "--tenant",
                    "chwrr",
                    "--confirm-t8",
                    "--package-dir",
                    "accepted-package",
                ]
            )
