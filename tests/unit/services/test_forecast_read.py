from __future__ import annotations

import traceback
from types import SimpleNamespace

import pytest

from sapphire_flow.services.forecast_read import (
    ForecastReadUnavailableError,
    require_standard_forecast_results,
    require_standard_forecast_store,
)
from sapphire_flow.types.enums import ForecastDataUse


class TestOrdinaryReadPolicy:
    @pytest.mark.parametrize(
        "guard", [require_standard_forecast_store, require_standard_forecast_results]
    )
    def test_unknown_purpose_and_class_have_safe_rendered_error(
        self, guard: object
    ) -> None:
        protected = SimpleNamespace(data_use="protected-input-canary-8675309")
        try:
            if guard is require_standard_forecast_store:
                require_standard_forecast_store(protected)
            else:
                require_standard_forecast_results([protected])
        except ForecastReadUnavailableError as exc:
            rendered = "".join(traceback.format_exception(exc))
            assert "protected-input-canary-8675309" not in rendered
            assert str(exc) == "Ordinary forecast reads are unavailable"
        else:
            pytest.fail("unsafe input accepted")

    def test_missing_class_is_not_standard(self) -> None:
        with pytest.raises(
            ForecastReadUnavailableError, match="Ordinary forecast reads"
        ):
            require_standard_forecast_results([object()])

    def test_standard_results_remain_accepted(self) -> None:
        require_standard_forecast_results(
            [SimpleNamespace(data_use=ForecastDataUse.STANDARD)]
        )
