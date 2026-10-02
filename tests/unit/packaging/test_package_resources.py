from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PACKAGE = _REPO_ROOT / "src" / "sapphire_flow"


def test_package_resource_inventory_is_declared_in_pyproject() -> None:
    text = (_REPO_ROOT / "pyproject.toml").read_text()
    expected = [
        "py.typed",
        "data/icon_ch2_eps_grid.npz",
        "api/templates/**/*.html",
        "models/aquacast/configs/*.yaml",
    ]
    for item in expected:
        assert item in text


def test_current_package_resources_exist() -> None:
    resources = [
        _PACKAGE / "py.typed",
        _PACKAGE / "data" / "icon_ch2_eps_grid.npz",
        *_PACKAGE.glob("api/templates/**/*.html"),
        *_PACKAGE.glob("models/aquacast/configs/*.yaml"),
    ]
    assert resources
    assert all(path.exists() for path in resources)
    assert len(list(_PACKAGE.glob("api/templates/**/*.html"))) >= 1
    assert len(list(_PACKAGE.glob("models/aquacast/configs/*.yaml"))) >= 1
