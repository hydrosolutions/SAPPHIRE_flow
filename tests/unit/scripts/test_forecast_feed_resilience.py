from __future__ import annotations

import argparse
import importlib.util
import runpy
import sys
from pathlib import Path
from typing import Any

import pytest

_ROOT = Path(__file__).parents[3]


@pytest.fixture
def feed(monkeypatch: pytest.MonkeyPatch) -> Any:
    path = _ROOT / "scripts/forecast_feed_resilience.py"
    assert path.is_file(), "domain-named loose implementation is required"
    spec = importlib.util.spec_from_file_location("forecast_feed_resilience", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module


class TestLegacyCompatibility:
    def test_legacy_entrypoint_delegates_to_domain_main(
        self, feed: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        called: list[str] = []
        monkeypatch.setattr(feed, "main", lambda: called.append("main"))
        runpy.run_path(
            str(_ROOT / "scripts/plan100_forecast_feed_resilience.py"),
            run_name="__main__",
        )
        assert called == ["main"]

    @pytest.mark.parametrize(
        "command,function,extra",
        [
            ("capture-snapshot", "capture_snapshot", ["--output-dir", "out"]),
            ("audit-priorities", "audit_priorities", []),
            ("reconcile-priorities", "reconcile_priorities", []),
            ("audit-floor", "audit_floor", []),
            ("audit-forecast-alerts", "audit_forecast_alerts", []),
        ],
    )
    def test_same_five_commands_dispatch_without_database(
        self,
        feed: Any,
        monkeypatch: pytest.MonkeyPatch,
        command: str,
        function: str,
        extra: list[str],
    ) -> None:
        received: list[argparse.Namespace] = []
        monkeypatch.setattr(feed, function, received.append)
        monkeypatch.setattr(sys, "argv", ["legacy", command, *extra])
        feed.main()
        assert len(received) == 1
        if command == "capture-snapshot":
            assert received[0].blackout_start == "2026-07-03T00:00:00+00:00"
            assert received[0].blackout_end == "2026-07-06T23:59:59+00:00"
        if command == "reconcile-priorities":
            assert received[0].apply is False
            assert received[0].maintenance_mode_confirmed is False
            assert received[0].backup_reference is None
            assert received[0].allow_override == []

    def test_legacy_help_is_native_parser_help(
        self,
        feed: Any,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setattr(sys, "argv", ["legacy", "--help"])
        with pytest.raises(SystemExit) as exc:
            runpy.run_path(
                str(_ROOT / "scripts/plan100_forecast_feed_resilience.py"),
                run_name="__main__",
            )
        assert exc.value.code == 0
        assert "reconcile-priorities" in capsys.readouterr().out

    def test_dry_run_delegates_only_to_audit(
        self, feed: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        received: list[argparse.Namespace] = []
        monkeypatch.setattr(feed, "audit_priorities", received.append)
        args = argparse.Namespace(apply=False)
        feed.reconcile_priorities(args)
        assert received == [args]

    @pytest.mark.parametrize(
        "backup,maintenance,message",
        [
            (None, True, "--backup-reference is required"),
            ("snapshot", False, "--maintenance-mode-confirmed is required"),
        ],
    )
    def test_apply_requires_existing_confirmations(
        self, feed: Any, backup: str | None, maintenance: bool, message: str
    ) -> None:
        args = argparse.Namespace(
            apply=True, backup_reference=backup, maintenance_mode_confirmed=maintenance
        )
        with pytest.raises(RuntimeError, match=message):
            feed.reconcile_priorities(args)


class TestDirectCliHelp:
    @pytest.mark.parametrize("subcommand", [None, "reconcile-priorities"])
    def test_both_entrypoints_work_from_unrelated_directory(
        self,
        tmp_path: Path,
        subcommand: str | None,
    ) -> None:
        import os
        import subprocess

        env = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(
                (
                    "DATABASE_",
                    "SAPPHIRE_",
                    "PREFECT_",
                    "PG",
                    "AWS_",
                    "AZURE_",
                    "GOOGLE_",
                    "RECAP_",
                    "METEOSWISS_",
                    "BAFU_",
                    "BIPAD_",
                    "DHM_",
                )
            )
        }
        outputs = []
        for filename in (
            "plan100_forecast_feed_resilience.py",
            "forecast_feed_resilience.py",
        ):
            args = [sys.executable, str(_ROOT / "scripts" / filename)]
            if subcommand is not None:
                args.append(subcommand)
            args.append("--help")
            result = subprocess.run(
                args,
                cwd=tmp_path,
                env=env,
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
            assert result.returncode == 0, result.stderr
            outputs.append(
                " ".join(result.stdout.replace(filename, "ENTRYPOINT").split())
            )
        assert outputs[0] == outputs[1]
        expected = (
            [
                "capture-snapshot",
                "audit-priorities",
                "reconcile-priorities",
                "audit-floor",
                "audit-forecast-alerts",
            ]
            if subcommand is None
            else [
                "--apply",
                "--backup-reference",
                "--maintenance-mode-confirmed",
                "--allow-override",
                "--config",
                "--output",
            ]
        )
        assert all(item in outputs[0] for item in expected)
