from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from sapphire_flow.cli.import_dhm_delivery import DELIVERY_TENANT_NAME
from sapphire_flow.config.declared_tenants import (
    load_declared_tenants,
    parse_declared_tenants,
)
from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.types.tenant import (
    DEFAULT_TENANT_CODE,
    DEFAULT_TENANT_NAME,
    DeclaredTenant,
)


class TestParseDeclaredTenants:
    def test_valid_table_returns_declared_tenants_sorted_by_code(self) -> None:
        raw = {"zeta": {"name": "Zeta"}, "chwrr": {"name": "CHWRR Nepal"}}
        assert parse_declared_tenants(raw) == (
            DeclaredTenant(code="chwrr", name="CHWRR Nepal"),
            DeclaredTenant(code="zeta", name="Zeta"),
        )

    def test_absent_tenants_is_empty(self) -> None:
        assert parse_declared_tenants(None) == ()

    def test_empty_table_is_empty(self) -> None:
        assert parse_declared_tenants({}) == ()

    @pytest.mark.parametrize(
        "code", ["CHWRR", "1abc", "a b", "", "a" * 33, "-x", "a.b", "é"]
    )
    def test_invalid_code_is_rejected(self, code: str) -> None:
        with pytest.raises(ConfigurationError, match="code"):
            parse_declared_tenants({code: {"name": "Name"}})

    @pytest.mark.parametrize("name", ["", "   ", "x" * 201])
    def test_blank_or_overlong_name_is_rejected(self, name: str) -> None:
        with pytest.raises(ConfigurationError, match="name"):
            parse_declared_tenants({"abc": {"name": name}})

    def test_missing_name_is_rejected(self) -> None:
        with pytest.raises(ConfigurationError, match="name"):
            parse_declared_tenants({"abc": {}})

    def test_id_key_is_rejected(self) -> None:
        with pytest.raises(ConfigurationError, match="abc"):
            parse_declared_tenants(
                {"abc": {"name": "N", "id": "00000000-0000-0000-0000-000000000009"}}
            )

    @pytest.mark.parametrize("raw", [["chwrr"], "chwrr", 3])
    def test_non_table_tenants_is_rejected(self, raw: object) -> None:
        with pytest.raises(ConfigurationError, match="table"):
            parse_declared_tenants(raw)

    def test_non_table_entry_is_rejected(self) -> None:
        with pytest.raises(ConfigurationError, match="abc"):
            parse_declared_tenants({"abc": "Name"})

    def test_sapphire_with_seeded_name_is_accepted(self) -> None:
        result = parse_declared_tenants({"sapphire": {"name": DEFAULT_TENANT_NAME}})
        assert result == (
            DeclaredTenant(code=DEFAULT_TENANT_CODE, name=DEFAULT_TENANT_NAME),
        )

    def test_sapphire_with_other_name_is_rejected(self) -> None:
        with pytest.raises(ConfigurationError, match="sapphire"):
            parse_declared_tenants({"sapphire": {"name": "Something else"}})


class TestLoadDeclaredTenants:
    def _write(self, path: Path, text: str) -> Path:
        path.write_text(text)
        return path

    def test_overlay_declaration_is_returned(self, tmp_path: Path) -> None:
        base = self._write(tmp_path / "base.toml", "[logging]\nlevel = 'INFO'\n")
        overlay = self._write(
            tmp_path / "o.toml", '[tenants.chwrr]\nname = "CHWRR Nepal"\n'
        )
        assert load_declared_tenants(base, [overlay]) == (
            DeclaredTenant(code="chwrr", name="CHWRR Nepal"),
        )

    def test_base_and_overlay_tables_merge_per_code(self, tmp_path: Path) -> None:
        base = self._write(tmp_path / "b.toml", '[tenants.aaa]\nname = "A"\n')
        overlay = self._write(tmp_path / "o.toml", '[tenants.bbb]\nname = "B"\n')
        assert [t.code for t in load_declared_tenants(base, [overlay])] == [
            "aaa",
            "bbb",
        ]

    def test_same_code_in_two_overlays_resolves_to_rightmost(
        self, tmp_path: Path
    ) -> None:
        base = self._write(tmp_path / "b.toml", "")
        first = self._write(tmp_path / "1.toml", '[tenants.aaa]\nname = "First"\n')
        second = self._write(tmp_path / "2.toml", '[tenants.aaa]\nname = "Second"\n')
        assert load_declared_tenants(base, [first, second]) == (
            DeclaredTenant(code="aaa", name="Second"),
        )

    def test_config_without_tenants_declares_nothing(self, tmp_path: Path) -> None:
        base = self._write(tmp_path / "b.toml", "[logging]\nlevel = 'INFO'\n")
        assert load_declared_tenants(base, []) == ()

    def test_unset_env_placeholder_elsewhere_does_not_fail(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("SAPPHIRE_FOO", raising=False)
        base = self._write(
            tmp_path / "b.toml",
            '[adapters.x]\ntoken = "${SAPPHIRE_FOO}"\n[tenants.aaa]\nname = "A"\n',
        )
        assert load_declared_tenants(base, []) == (
            DeclaredTenant(code="aaa", name="A"),
        )

    def test_placeholder_in_a_name_is_not_expanded(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SAPPHIRE_FOO", "expanded")
        base = self._write(
            tmp_path / "b.toml", '[tenants.aaa]\nname = "${SAPPHIRE_FOO}"\n'
        )
        assert load_declared_tenants(base, [])[0].name == "${SAPPHIRE_FOO}"

    def test_non_table_tenants_value_is_rejected(self, tmp_path: Path) -> None:
        base = self._write(tmp_path / "b.toml", 'tenants = ["chwrr"]\n')
        with pytest.raises(ConfigurationError, match="table"):
            load_declared_tenants(base, [])

    def test_missing_config_file_is_a_configuration_error(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigurationError, match="not found"):
            load_declared_tenants(tmp_path / "missing.toml", [])


class TestDeclaredNamesHaveOneSource:
    def test_mac_mini_overlay_declares_chwrr_with_the_delivery_name(self) -> None:
        root = next(
            p
            for p in Path(__file__).resolve().parents
            if (p / "docker-compose.yml").is_file()
        )
        overlay = root / "config/overlays/mac-mini.toml"
        declared = load_declared_tenants(root / "config.toml", [overlay])
        by_code = {t.code: t for t in declared}
        assert by_code["chwrr"].name == DELIVERY_TENANT_NAME
        assert "id" not in tomllib.loads(overlay.read_text())["tenants"]["chwrr"]

    def test_import_overlay_declares_no_tenants(self) -> None:
        root = next(
            p
            for p in Path(__file__).resolve().parents
            if (p / "docker-compose.yml").is_file()
        )
        raw = tomllib.loads((root / "config/overlays/chwrr-import.toml").read_text())
        assert "tenants" not in raw
