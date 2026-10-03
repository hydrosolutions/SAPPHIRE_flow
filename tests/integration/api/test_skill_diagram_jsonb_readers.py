from __future__ import annotations

import html
import json
import re
from dataclasses import replace
from typing import TYPE_CHECKING, cast
from uuid import UUID

import sqlalchemy as sa
from fastapi.testclient import TestClient

from sapphire_flow.api import app
from sapphire_flow.api.deps import get_connection
from sapphire_flow.api.routes import tables
from sapphire_flow.api.security import Principal, require_admin
from sapphire_flow.db.metadata import skill_diagrams
from sapphire_flow.types.enums import AccessTokenRole, ForcingType, SkillSource
from sapphire_flow.types.ids import AccessTokenId
from tests.integration.store.test_skill_diagram_jsonb import (
    make_public_diagram,
    producer_payload,
)

if TYPE_CHECKING:
    import httpx
    import pytest

    from tests.integration.services.skill_persistence_fixture import (
        SkillPersistenceCase,
    )

pytest_plugins = ("tests.integration.services.skill_persistence_fixture",)


class TestSkillDiagramJsonbReaders:
    def test_full_model_json_and_truncated_admin_previews(
        self,
        skill_persistence_case: SkillPersistenceCase,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from tests.integration.services.skill_persistence_fixture import (
            AID,
            MID,
            SID,
            C,
        )

        case = skill_persistence_case
        generation = UUID(int=68001)
        kind, payload = producer_payload("reliability_nan")
        canonical = replace(
            make_public_diagram(kind, payload), generation_id=generation
        )
        short: dict[str, object] = {"x": [0, 1], "y": [None, 1], "label": "<unsafe>"}
        legacy = replace(
            make_public_diagram("roc", short),
            id=UUID(int=66002),
            generation_id=generation,
        )
        orphan = replace(
            make_public_diagram("roc", {"orphan": "admin-only"}),
            id=UUID(int=66003),
            generation_id=UUID(int=68002),
        )
        case.skill_store.store_skill_diagrams([canonical, legacy, orphan])
        case.skill_store.publish_generation(
            generation_id=generation,
            station_id=SID,
            model_id=MID,
            model_artifact_id=AID,
            parameter="discharge",
            skill_source=SkillSource.HINDCAST_REANALYSIS,
            forcing_type=ForcingType.REANALYSIS,
            computation_version=2,
            published_at=C,
            score_count=0,
            diagram_count=2,
        )
        case.connection.execute(sa.text("SET LOCAL ROLE sapphire_api"))
        assert case.connection.scalar(sa.text("SELECT current_user")) == "sapphire_api"
        raw_canonical = case.connection.execute(
            sa.select(skill_diagrams.c.data).where(skill_diagrams.c.id == canonical.id)
        ).scalar_one()
        raw_short = case.connection.execute(
            sa.select(skill_diagrams.c.data).where(skill_diagrams.c.id == legacy.id)
        ).scalar_one()
        principal = Principal(
            token_id=AccessTokenId(UUID(int=99)),
            role=AccessTokenRole.ADMIN,
            tenant_id=None,
            station_ids=frozenset(),
        )
        old_overrides = app.dependency_overrides.copy()
        old_reflected = vars(tables)["_reflected"]
        try:
            app.dependency_overrides[get_connection] = lambda: case.connection
            app.dependency_overrides[require_admin] = lambda: principal
            monkeypatch.setattr(tables, "_reflected", None)
            with TestClient(app) as client:
                web = cast("httpx.Client", client)
                model = web.get(f"/models/{MID}/")
                assert model.status_code == 200
                texts = re.findall(r"const data = (.*?);", model.text)

                def reject(token: str) -> None:
                    raise ValueError("nonfinite numeric constant")

                data = [json.loads(text, parse_constant=reject) for text in texts]
                assert len(data) == 2
                reliability = next(value for value in data if "observed_freq" in value)
                assert reliability["observed_freq"] == [
                    0.0,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    1.0,
                ]
                assert reliability["sample_counts"] == [1, 0, 0, 0, 0, 0, 0, 0, 0, 1]
                assert short in data
                assert "orphan" not in model.text
                assert model.text.count('style="height:300px;"') == 2
                assert "<h4>reliability" in model.text
                assert "<unsafe>" not in model.text
                for path in ("/tables/skill_diagrams/", "/tables/skill_diagrams/rows"):
                    response = web.get(path)
                    assert response.status_code == 200
                    cells = [
                        html.unescape(value)
                        for value in re.findall(
                            r"<td[^>]*>(.*?)</td>", response.text, re.DOTALL
                        )
                    ]
                    assert json.dumps(raw_short) in cells
                    assert json.dumps(raw_canonical)[:120] + "..." in cells
                    assert json.dumps({"orphan": "admin-only"}) in cells
                    assert "<unsafe>" not in response.text
        finally:
            app.dependency_overrides.clear()
            app.dependency_overrides.update(old_overrides)
            monkeypatch.setattr(tables, "_reflected", old_reflected)
