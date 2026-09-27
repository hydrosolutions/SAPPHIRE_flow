from __future__ import annotations

import importlib.util
import sys
from datetime import timedelta
from pathlib import Path
from unittest.mock import MagicMock
from uuid import UUID

import pytest

from sapphire_flow.config.onboarding import OnboardingConfig, StationQcThresholdSpec
from sapphire_flow.types.domain import QcRuleParams, QcRuleSet
from sapphire_flow.types.ids import TenantId
from tests.conftest import make_station_config
from tests.fakes.fake_stores import FakeStationStore, FakeTenantStore

_SCRIPT_PATH = Path(__file__).parents[3] / "scripts" / "onboard.py"


@pytest.fixture()
def mod():
    spec = importlib.util.spec_from_file_location("onboard_script", _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["onboard_script"] = module
    spec.loader.exec_module(module)
    return module


class TestOnboardScriptMain:
    @pytest.mark.parametrize(
        "contents,diagnostic",
        [
            ('[qc_rules]\nversion = "test"\n', "explicit [onboarding]"),
            ("[onboarding]\n", "explicit [onboarding] and [qc_rules]"),
            (
                '[onboarding]\n[qc_rules]\nversion = "test"\n',
                "no station QC threshold blocks",
            ),
        ],
    )
    def test_validator_refuses_empty_or_incomplete_config_before_database(
        self,
        mod,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        contents: str,
        diagnostic: str,
    ) -> None:
        monkeypatch.delenv("SAPPHIRE_CONFIG_OVERLAY", raising=False)
        monkeypatch.setattr(mod.sa, "create_engine", MagicMock())
        path = tmp_path / "candidate.toml"
        path.write_text(contents)

        assert mod.main(["--validate-config", str(path)]) == 1
        assert diagnostic in capsys.readouterr().err
        mod.sa.create_engine.assert_not_called()

    def test_validate_config_branch_does_not_start_onboarding(
        self, mod, monkeypatch, tmp_path: Path
    ) -> None:
        validate = MagicMock(return_value=0)
        monkeypatch.setattr(mod, "_validate_config", validate)
        monkeypatch.setattr(mod, "_run_migrations", MagicMock())
        monkeypatch.delenv("DATABASE_URL", raising=False)
        path = tmp_path / "candidate.toml"

        assert mod.main(["--validate-config", str(path)]) == 0
        validate.assert_called_once_with(path, [])
        mod._run_migrations.assert_not_called()

    def test_validator_rejects_overlay_before_read_or_database(
        self, mod, monkeypatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("SAPPHIRE_CONFIG_OVERLAY", "other.toml")
        monkeypatch.setattr(mod.sa, "create_engine", MagicMock())
        path = tmp_path / "candidate.toml"
        path.write_text("[onboarding]\n")

        assert mod.main(["--validate-config", str(path)]) == 1
        mod.sa.create_engine.assert_not_called()

    def test_validator_waives_only_missing_unonboarded_network(
        self, mod, capsys: pytest.CaptureFixture[str]
    ) -> None:
        spec = StationQcThresholdSpec(
            tenant_code="sapphire",
            code="447",
            network="dhm",
            rule_id="range_check",
            parameter="discharge",
            time_step=timedelta(days=1),
            thresholds={"value_max": 5000.0},
        )
        config = OnboardingConfig(station_qc_thresholds=(spec,))
        rules = QcRuleSet(
            version="test",
            rules=(
                QcRuleParams(
                    rule_id="range_check",
                    rule_version="1",
                    parameter="discharge",
                    time_step=timedelta(days=1),
                    thresholds={"value_min": 0.0, "value_max": 10000.0},
                ),
            ),
        )
        tenants = FakeTenantStore().fetch_all_tenants()

        assert mod._validate_threshold_declarations(config, rules, tenants, [], []) == 1
        assert (
            mod._validate_threshold_declarations(config, rules, tenants, [], ["dhm"])
            == 0
        )
        assert "PENDING sapphire/dhm/447" in capsys.readouterr().out
        assert (
            mod._validate_threshold_declarations(
                config,
                rules,
                tenants,
                [make_station_config(code="450", network="dhm")],
                ["dhm"],
            )
            == 1
        )
        bad_rules = QcRuleSet(version="test", rules=())
        assert (
            mod._validate_threshold_declarations(
                config, bad_rules, tenants, [], ["dhm"]
            )
            == 1
        )

    def test_validator_never_waives_a_foreign_tenant_match(
        self, mod, capsys: pytest.CaptureFixture[str]
    ) -> None:
        spec = StationQcThresholdSpec(
            tenant_code="sapphire",
            code="447",
            network="dhm",
            rule_id="range_check",
            parameter="discharge",
            time_step=timedelta(days=1),
            thresholds={"value_max": 5000.0},
        )
        rules = QcRuleSet(
            version="test",
            rules=(
                QcRuleParams(
                    rule_id="range_check",
                    rule_version="1",
                    parameter="discharge",
                    time_step=timedelta(days=1),
                    thresholds={"value_min": 0.0, "value_max": 10000.0},
                ),
            ),
        )
        foreign = make_station_config(
            code="447",
            network="dhm",
            tenant_id=TenantId(UUID("00000000-0000-0000-0000-000000000002")),
        )

        result = mod._validate_threshold_declarations(
            OnboardingConfig(station_qc_thresholds=(spec,)),
            rules,
            FakeTenantStore().fetch_all_tenants(),
            [foreign],
            ["dhm"],
        )

        assert result == 1
        assert "REJECTED sapphire/dhm/447: tenant_mismatch" in capsys.readouterr().out

    def test_missing_tenant_waiver_requires_network_absent_database_wide(
        self, mod, capsys: pytest.CaptureFixture[str]
    ) -> None:
        spec = StationQcThresholdSpec(
            tenant_code="chwrr",
            code="447",
            network="dhm",
            rule_id="range_check",
            parameter="discharge",
            time_step=timedelta(days=1),
            thresholds={"value_max": 5000.0},
        )
        config = OnboardingConfig(station_qc_thresholds=(spec,))
        rules = QcRuleSet(
            version="test",
            rules=(
                QcRuleParams(
                    rule_id="range_check",
                    rule_version="1",
                    parameter="discharge",
                    time_step=timedelta(days=1),
                    thresholds={"value_min": 0.0, "value_max": 10000.0},
                ),
            ),
        )
        tenants = FakeTenantStore().fetch_all_tenants()

        assert (
            mod._validate_threshold_declarations(
                config, rules, tenants, [], ["dhm", "dhm"]
            )
            == 0
        )
        assert "PENDING chwrr/dhm/447" in capsys.readouterr().out
        assert (
            mod._validate_threshold_declarations(
                config,
                rules,
                tenants,
                [make_station_config(code="450", network="dhm")],
                ["dhm"],
            )
            == 1
        )
        assert "REJECTED chwrr/dhm/447: tenant_not_found" in capsys.readouterr().out
        assert (
            mod._validate_threshold_declarations(config, rules, tenants, [], ["DHM"])
            == 1
        )

    def test_repeatable_waiver_option_is_forwarded_case_sensitively(
        self, mod, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        validate = MagicMock(return_value=0)
        monkeypatch.setattr(mod, "_validate_config", validate)
        path = tmp_path / "candidate.toml"

        assert (
            mod.main(
                [
                    "--validate-config",
                    str(path),
                    "--allow-unonboarded-network",
                    "dhm",
                    "--allow-unonboarded-network",
                    "DHM",
                ]
            )
            == 0
        )
        validate.assert_called_once_with(path, ["dhm", "DHM"])

    def test_validator_uses_explicit_file_despite_config_env(
        self, mod, monkeypatch, tmp_path: Path
    ) -> None:
        selected = tmp_path / "selected.toml"
        selected.write_text(
            "[onboarding]\n"
            "[[onboarding.station_qc_thresholds]]\n"
            'tenant_code = "sapphire"\ncode = "2135"\nnetwork = "bafu"\n'
            'rule_id = "range_check"\nparameter = "discharge"\n'
            "time_step_seconds = 600\nthresholds = { value_max = 90.0 }\n"
            '[qc_rules]\nversion = "test"\n'
            "[[qc_rules.rules]]\n"
            'rule_id = "range_check"\nrule_version = "test"\n'
            'parameter = "discharge"\ntime_step_seconds = 600\n'
            "thresholds = { value_min = 0.0, value_max = 100.0 }\n"
        )
        monkeypatch.setenv("SAPPHIRE_CONFIG", str(tmp_path / "wrong.toml"))
        monkeypatch.delenv("SAPPHIRE_CONFIG_OVERLAY", raising=False)
        monkeypatch.setenv("DATABASE_URL", "postgresql://stub")
        engine = MagicMock()
        monkeypatch.setattr(mod.sa, "create_engine", MagicMock(return_value=engine))
        tenant_store = FakeTenantStore()
        monkeypatch.setattr(
            "sapphire_flow.store.tenant_store.PgTenantStore",
            lambda _conn: tenant_store,
        )
        station_store = FakeStationStore()
        station_store.store_station(make_station_config(code="2135"))
        monkeypatch.setattr(mod, "PgStationStore", lambda _conn: station_store)
        tenant_store.store_tenant = MagicMock(
            side_effect=AssertionError("tenant write")
        )
        station_store.store_station = MagicMock(
            side_effect=AssertionError("station write")
        )
        monkeypatch.setattr(mod, "_run_migrations", MagicMock())
        monkeypatch.setattr(mod, "onboard_from_camelsch", MagicMock())

        assert mod.main(["--validate-config", str(selected)]) == 0
        mod.sa.create_engine.assert_called_once()
        engine.dispose.assert_called_once()
        tenant_store.store_tenant.assert_not_called()
        station_store.store_station.assert_not_called()
        mod._run_migrations.assert_not_called()
        mod.onboard_from_camelsch.assert_not_called()

    def test_main_returns_nonzero_without_database_url(self, mod, monkeypatch) -> None:
        monkeypatch.delenv("DATABASE_URL", raising=False)
        result = mod.main([])
        assert result == 1

    def test_main_dry_run_returns_zero(self, mod, monkeypatch) -> None:
        monkeypatch.setenv("DATABASE_URL", "postgresql://stub")
        result = mod.main(["--dry-run"])
        assert result == 0

    def test_main_happy_path_invokes_camelsch_onboarder(self, mod, monkeypatch) -> None:
        monkeypatch.setenv("DATABASE_URL", "postgresql://stub")
        monkeypatch.setattr(mod.sa, "create_engine", MagicMock())
        monkeypatch.setattr(mod, "_run_migrations", MagicMock())
        monkeypatch.setattr(mod, "_load_qc_rules", MagicMock(return_value=[]))
        monkeypatch.setattr(mod, "_print_result", MagicMock())
        monkeypatch.setattr(
            "sapphire_flow.config.paths.resolve_artifact_dir",
            MagicMock(return_value=Path("/tmp")),
        )
        onboard_stub = MagicMock(return_value=MagicMock(errors=[]))
        monkeypatch.setattr(mod, "onboard_from_camelsch", onboard_stub)

        result = mod.main(["--data-dir", "/tmp/cam"])

        assert result == 0
        onboard_stub.assert_called_once()
        _, kwargs = onboard_stub.call_args
        assert kwargs["data_dir"] == Path("/tmp/cam")
