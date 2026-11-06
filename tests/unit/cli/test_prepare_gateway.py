from __future__ import annotations

import json
from types import SimpleNamespace
from typing import TYPE_CHECKING

from pydantic import TypeAdapter

from sapphire_flow.cli import prepare_gateway
from sapphire_flow.services.gateway_preparation import proposal_revision
from sapphire_flow.types.gateway_preparation import DataProposal
from tests.unit.basin_operator.conftest import (
    synthetic_package as synthetic_package_fixture,
)
from tests.unit.services.test_gateway_preparation import approved as approved_fixture
from tests.unit.services.test_gateway_preparation import proposal, requirements

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


approved = approved_fixture
synthetic_package = synthetic_package_fixture


def arguments(workspace: Path, approved: Path) -> list[str]:
    return ["--workspace", str(workspace), "--handover", str(approved)]


def test_help_and_scope_prepare_without_remote_construction(
    approved: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    spec = proposal()
    payload = TypeAdapter(DataProposal).dump_python(spec, mode="json")
    source = {
        key: payload[key] for key in ("model_id", "history", "forecast", "contracts")
    }
    source["time_step_seconds"] = 86400
    path = tmp_path / "scope.json"
    path.write_text(json.dumps(source))
    monkeypatch.setattr(
        prepare_gateway,
        "discover_models",
        lambda: {
            "synthetic_cmal_contract": SimpleNamespace(data_requirements=requirements())
        },
    )

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("no Gateway or database construction allowed")

    monkeypatch.setattr(prepare_gateway, "load_recap_api_key", forbidden)
    workspace = tmp_path / "evidence"
    argv = arguments(workspace, approved)
    assert prepare_gateway.run([*argv, "prepare", "--scope", str(path)]) == 2
    output = json.loads(capsys.readouterr().out)
    assert output["proposal_revision"] == proposal_revision(spec)
    assert output["stations"][0]["outcome"] == "held"
    assert (
        prepare_gateway.run(
            [
                *argv,
                "prepare",
                "--scope",
                str(path),
                "--confirm",
                proposal_revision(spec),
            ]
        )
        == 2
    )
    capsys.readouterr()
    assert prepare_gateway.run([*argv, "status"]) == 2
    assert json.loads(capsys.readouterr().out)["stations"][0]["station_code"] == "001.0"


def test_bad_scope_never_prints_validation_input(
    approved: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "bad.json"
    source.write_text('{"time_step_seconds": "SENTINEL-SECRET"}')
    assert (
        prepare_gateway.run(
            [
                *arguments(tmp_path / "evidence", approved),
                "prepare",
                "--scope",
                str(source),
            ]
        )
        == 2
    )
    captured = capsys.readouterr()
    assert "SENTINEL-SECRET" not in captured.out + captured.err
