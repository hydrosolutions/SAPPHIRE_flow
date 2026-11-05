from __future__ import annotations

import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from sapphire_flow.basin_operator.workspace import BasinWorkspace

import pytest

from sapphire_flow.cli.basin_operator import main


def test_ambiguous_configuration_fails_before_server_or_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    workspace = tmp_path / "never-created"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "basin_operator",
            "--workspace",
            str(workspace),
            "--package-dir",
            "package",
            "--extractor-command",
            "missing.json",
        ],
    )
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2
    assert "not both" in capsys.readouterr().err
    assert not workspace.exists()


@pytest.mark.parametrize(
    "selection, expected",
    [([], "historical-v2"), (["--extractor-interface", "v0.2.0"], "v0.2.0")],
)
def test_cli_parses_interface_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, selection: list[str], expected: str
) -> None:
    import json
    from typing import Any

    from fastapi import FastAPI

    from sapphire_flow.cli import basin_operator

    command = tmp_path / "command.json"
    command.write_text(json.dumps([sys.executable]))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "basin_operator",
            "--workspace",
            str(tmp_path / "workspace"),
            "--extractor-command",
            str(command),
            *selection,
        ],
    )
    observed: list[str] = []

    def app(workspace: BasinWorkspace, **kwargs: Any) -> FastAPI:
        observed.append(workspace.interface.value)
        return FastAPI()

    monkeypatch.setattr(basin_operator, "create_operator_app", app)
    monkeypatch.setattr(basin_operator.uvicorn, "run", lambda *args, **kwargs: None)
    main()
    assert observed == [expected]
