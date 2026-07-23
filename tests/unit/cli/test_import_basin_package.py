"""Plan 120 Task 3A fixer round — thin CLI wiring tests.

Exercises ``main()``/``_run_import`` argument parsing, exit-code contract,
and the ``resolve_station(code, network)`` argument order against
``PgStationStore.fetch_station_by_code`` — none of which had ANY coverage
before this fixer round (a swapped-argument or inverted exit-code
regression here would previously pass CI undetected). Everything DB/engine-
facing is stubbed; no Postgres container needed.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from sapphire_flow.cli.import_basin_package import _run_import, main
from sapphire_flow.types.basin_package import BasinPackageImportReport


class TestRunImportWiring:
    def test_package_dir_and_resolve_station_argument_order(self) -> None:
        captured: dict[str, object] = {}

        def fake_import_basin_package_from_directory(
            package_dir, engine, *, resolve_station, assigned_model_features, clock
        ):  # noqa: ANN001, ANN202 - test double
            captured["package_dir"] = package_dir
            captured["resolve_station"] = resolve_station
            captured["assigned_model_features"] = assigned_model_features
            return BasinPackageImportReport(package_id="pkg", outcome="imported")

        mock_station = MagicMock()
        mock_station.id = "station-id-123"

        with (
            patch(
                "sapphire_flow.db.engine.create_engine_from_env",
                return_value=MagicMock(),
            ),
            patch(
                "sapphire_flow.services.model_registry.discover_models",
                return_value={},
            ),
            patch("sapphire_flow.store.station_store.PgStationStore") as mock_store_cls,
            patch(
                "sapphire_flow.services.basin_importer."
                "import_basin_package_from_directory",
                side_effect=fake_import_basin_package_from_directory,
            ),
        ):
            mock_store_cls.return_value.fetch_station_by_code.return_value = (
                mock_station
            )
            report = _run_import(Path("/tmp/some-package"))

        assert report.outcome == "imported"
        assert captured["package_dir"] == Path("/tmp/some-package")

        resolve_station = captured["resolve_station"]
        result = resolve_station("123", "dhm")  # type: ignore[operator]

        assert result == "station-id-123"
        # Argument ORDER matters: (code, network), never swapped.
        mock_store_cls.return_value.fetch_station_by_code.assert_called_with(
            "123", "dhm"
        )

    def test_assigned_model_features_seam_is_wired_not_left_none(self) -> None:
        """Fixer round, major finding: the CLI must NOT rely on
        ``import_basin_package_from_directory``'s ``assigned_model_features=
        None`` default — that silently treats every basin as unassigned to
        any model, downgrading a null required-static-feature to a warning
        instead of an onboarding hold. A regression that drops this kwarg
        (or passes ``None``) must fail here."""
        captured: dict[str, object] = {}

        def fake_import_basin_package_from_directory(
            package_dir, engine, *, resolve_station, assigned_model_features, clock
        ):  # noqa: ANN001, ANN202 - test double
            captured["assigned_model_features"] = assigned_model_features
            return BasinPackageImportReport(package_id="pkg", outcome="imported")

        with (
            patch(
                "sapphire_flow.db.engine.create_engine_from_env",
                return_value=MagicMock(),
            ),
            patch(
                "sapphire_flow.services.model_registry.discover_models",
                return_value={},
            ),
            patch("sapphire_flow.store.station_store.PgStationStore"),
            patch(
                "sapphire_flow.services.basin_importer."
                "import_basin_package_from_directory",
                side_effect=fake_import_basin_package_from_directory,
            ),
        ):
            _run_import(Path("/tmp/some-package"))

        assert captured["assigned_model_features"] is not None
        assert callable(captured["assigned_model_features"])

    def test_resolve_station_returns_none_when_unmatched(self) -> None:
        captured: dict[str, object] = {}

        def fake_import_basin_package_from_directory(
            package_dir, engine, *, resolve_station, assigned_model_features, clock
        ):  # noqa: ANN001, ANN202 - test double
            captured["resolve_station"] = resolve_station
            return BasinPackageImportReport(package_id="pkg", outcome="imported")

        with (
            patch(
                "sapphire_flow.db.engine.create_engine_from_env",
                return_value=MagicMock(),
            ),
            patch(
                "sapphire_flow.services.model_registry.discover_models",
                return_value={},
            ),
            patch("sapphire_flow.store.station_store.PgStationStore") as mock_store_cls,
            patch(
                "sapphire_flow.services.basin_importer."
                "import_basin_package_from_directory",
                side_effect=fake_import_basin_package_from_directory,
            ),
        ):
            mock_store_cls.return_value.fetch_station_by_code.return_value = None
            _run_import(Path("/tmp/some-package"))

        resolve_station = captured["resolve_station"]
        assert resolve_station("999", "dhm") is None  # type: ignore[operator]


class TestMainExitCodeContract:
    def test_exits_nonzero_when_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            sys, "argv", ["import_basin_package", "--package-dir", "/tmp/pkg"]
        )
        rejected = BasinPackageImportReport(
            package_id=None, outcome="rejected", rejection_reason="boom"
        )

        with (
            patch(
                "sapphire_flow.cli.import_basin_package._run_import",
                return_value=rejected,
            ),
            patch("sapphire_flow.logging.configure_cli_logging"),
            pytest.raises(SystemExit) as exc_info,
        ):
            main()

        assert exc_info.value.code == 1

    def test_does_not_exit_when_imported(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            sys, "argv", ["import_basin_package", "--package-dir", "/tmp/pkg"]
        )
        ok = BasinPackageImportReport(package_id="pkg", outcome="imported")

        with (
            patch(
                "sapphire_flow.cli.import_basin_package._run_import", return_value=ok
            ) as mock_run,
            patch("sapphire_flow.logging.configure_cli_logging"),
        ):
            main()  # must NOT raise SystemExit

        mock_run.assert_called_once_with(Path("/tmp/pkg"))

    def test_package_dir_argument_parsed_to_path(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            sys,
            "argv",
            ["import_basin_package", "--package-dir", "/some/dir/here"],
        )
        ok = BasinPackageImportReport(package_id="pkg", outcome="already_imported")

        with (
            patch(
                "sapphire_flow.cli.import_basin_package._run_import", return_value=ok
            ) as mock_run,
            patch("sapphire_flow.logging.configure_cli_logging"),
        ):
            main()

        mock_run.assert_called_once_with(Path("/some/dir/here"))
