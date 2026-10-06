from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import sys
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Never, Protocol, TypeGuard
from uuid import UUID

import polars as pl
import pytest
import sqlalchemy as sa
from geoalchemy2 import Geometry
from shapely import wkt
from sqlalchemy import event
from sqlalchemy.engine.default import DefaultExecutionContext
from sqlalchemy.sql.dml import Update

from sapphire_flow.config.deployment_identity import DeploymentIdentityConfig
from sapphire_flow.db import metadata as db
from sapphire_flow.services import nepal_onboarding
from sapphire_flow.services.provisional_discharge import convert_provisional_discharge
from sapphire_flow.store.basin_store import PgBasinStore
from sapphire_flow.store.historical_forcing_store import PgHistoricalForcingStore
from sapphire_flow.store.observation_store import PgObservationStore
from sapphire_flow.store.provisional_discharge_store import PgProvisionalDischargeStore
from sapphire_flow.store.rating_curve_store import PgRatingCurveStore
from sapphire_flow.store.recap_gateway_polygon_store import RecapGatewayPolygonStore
from sapphire_flow.store.station_store import PgStationStore
from sapphire_flow.store.tenant_store import PgTenantStore
from sapphire_flow.types.basin import Basin
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.domain import GeoCoord, QcFlag
from sapphire_flow.types.enums import (
    EnsembleMode,
    GaugingStatus,
    InterpolationMethod,
    ObservationSource,
    QcStatus,
    SpatialRepresentation,
    StationKind,
    StationOwnership,
    StationStatus,
    WeatherSourceRole,
    WeatherSourceStatus,
)
from sapphire_flow.types.historical_forcing import RawHistoricalForcing
from sapphire_flow.types.ids import (
    BasinId,
    MeasurementFeedEvidenceId,
    ObservationId,
    RatingCurveId,
    RatingReferenceProofId,
    StationId,
    TenantId,
)
from sapphire_flow.types.model import ModelDataRequirements
from sapphire_flow.types.nepal_onboarding import TrainingWindow
from sapphire_flow.types.observation import Observation
from sapphire_flow.types.rating_curve import RatingCurve
from sapphire_flow.types.rating_reference import (
    CurveSnapshot,
    MeasurementFeedEvidence,
    MeasurementSnapshot,
    RatingReferenceProof,
)
from sapphire_flow.types.station import (
    GatewayPolygonBindingRow,
    StationConfig,
    StationWeatherSource,
)
from tests.fakes.fake_models import FakeStationForecastModel
from tests.integration.store.test_provisional_discharge_store import (
    permit_fixture,
    seed_reference,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence

    from sqlalchemy.engine.interfaces import ExecutionContext

    from sapphire_flow.protocols.adapters import WeatherReanalysisSource
    from sapphire_flow.protocols.forecast_model import (
        GroupForecastModel,
        StationForecastModel,
    )
    from sapphire_flow.protocols.stores import (
        BasinStore,
        ObservationStore,
        StationStore,
    )
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.model import StationTrainingData


class QualificationScenario(Enum):
    CLEAN = "clean"
    MIXED = "mixed"
    SHORT = "short"
    PROTECTED_ONLY = "protected_only"


# Frozen reviewed literals; no production result or serializer builds the oracle.
_ORACLE_TEXT = (
    '{\n  "base": "58f56d56eddfde4f79afdb95de466ff4f8dc6e20",\n  "proven'
    'ance": "literal stdlib-only planning arithmetic; no project impor'
    'ts or test/model execution",\n  "clock": "2026-01-20T00:00:00+00:0'
    '0",\n  "start": "2026-01-10T00:00:00+00:00",\n  "end": "2026-01-20T'
    '00:00:00+00:00",\n  "initial_station_time": "2026-01-09T23:00:00+0'
    '0:00",\n  "tenant": {\n    "id": "00000000-0000-0000-0000-000000016'
    '75f",\n    "code": "chwrr",\n    "name": "CHWRR Nepal"\n  },\n  "appl'
    'ication_identity": {\n    "global_admin": false,\n    "writable_ten'
    'ants": [\n      "chwrr"\n    ],\n    "operator": "fixture-qualificat'
    'ion-owner"\n  },\n  "stations": [\n    {\n      "id": "00000000-0000-'
    '0000-0000-000000016379",\n      "tenant_id": "00000000-0000-0000-0'
    '000-00000001675f",\n      "code": "447",\n      "name": "Synthetic '
    'qualification 447",\n      "location": {\n        "lon": 85.05,\n   '
    '     "lat": 27.05,\n        "altitude_masl": null\n      },\n      "'
    'station_kind": "river",\n      "basin_id": "00000000-0000-0000-000'
    '0-000000017701",\n      "timezone": "UTC",\n      "regulation_type"'
    ': null,\n      "forecast_targets": null,\n      "measured_parameter'
    's": [\n        "discharge",\n        "water_level"\n      ],\n      "'
    'station_status": "onboarding",\n      "created_at": "2026-01-09T23'
    ':00:00+00:00",\n      "updated_at": "2026-01-09T23:00:00+00:00",\n '
    '     "network": "dhm",\n      "ownership": "own",\n      "wigos_id"'
    ': null,\n      "gauging_status": "gauged",\n      "water_level_datu'
    'm_masl": null,\n      "water_level_unit": "m"\n    },\n    {\n      "'
    'id": "00000000-0000-0000-0000-00000001637a",\n      "tenant_id": "'
    '00000000-0000-0000-0000-00000001675f",\n      "code": "450",\n     '
    ' "name": "Synthetic qualification 450",\n      "location": {\n     '
    '   "lon": 85.05,\n        "lat": 27.05,\n        "altitude_masl": n'
    'ull\n      },\n      "station_kind": "river",\n      "basin_id": "00'
    '000000-0000-0000-0000-000000017702",\n      "timezone": "UTC",\n   '
    '   "regulation_type": null,\n      "forecast_targets": null,\n     '
    ' "measured_parameters": [\n        "discharge",\n        "water_lev'
    'el"\n      ],\n      "station_status": "onboarding",\n      "created'
    '_at": "2026-01-09T23:00:00+00:00",\n      "updated_at": "2026-01-0'
    '9T23:00:00+00:00",\n      "network": "dhm",\n      "ownership": "ow'
    'n",\n      "wigos_id": null,\n      "gauging_status": "gauged",\n   '
    '   "water_level_datum_masl": null,\n      "water_level_unit": "m"\n'
    '    },\n    {\n      "id": "00000000-0000-0000-0000-00000001637b",\n'
    '      "tenant_id": "00000000-0000-0000-0000-00000001675f",\n      '
    '"code": "604.5",\n      "name": "Synthetic qualification 604.5",\n '
    '     "location": {\n        "lon": 85.05,\n        "lat": 27.05,\n  '
    '      "altitude_masl": null\n      },\n      "station_kind": "river'
    '",\n      "basin_id": "00000000-0000-0000-0000-000000017703",\n    '
    '  "timezone": "UTC",\n      "regulation_type": null,\n      "foreca'
    'st_targets": null,\n      "measured_parameters": [\n        "discha'
    'rge",\n        "water_level"\n      ],\n      "station_status": "onb'
    'oarding",\n      "created_at": "2026-01-09T23:00:00+00:00",\n      '
    '"updated_at": "2026-01-09T23:00:00+00:00",\n      "network": "dhm"'
    ',\n      "ownership": "own",\n      "wigos_id": null,\n      "gaugin'
    'g_status": "gauged",\n      "water_level_datum_masl": null,\n      '
    '"water_level_unit": "m"\n    },\n    {\n      "id": "00000000-0000-0'
    '000-0000-00000001637c",\n      "tenant_id": "00000000-0000-0000-00'
    '00-00000001675f",\n      "code": "647",\n      "name": "Synthetic q'
    'ualification 647",\n      "location": {\n        "lon": 85.05,\n    '
    '    "lat": 27.05,\n        "altitude_masl": null\n      },\n      "s'
    'tation_kind": "river",\n      "basin_id": "00000000-0000-0000-0000'
    '-000000017704",\n      "timezone": "UTC",\n      "regulation_type":'
    ' null,\n      "forecast_targets": null,\n      "measured_parameters'
    '": [\n        "discharge",\n        "water_level"\n      ],\n      "s'
    'tation_status": "onboarding",\n      "created_at": "2026-01-09T23:'
    '00:00+00:00",\n      "updated_at": "2026-01-09T23:00:00+00:00",\n  '
    '    "network": "dhm",\n      "ownership": "own",\n      "wigos_id":'
    ' null,\n      "gauging_status": "gauged",\n      "water_level_datum'
    '_masl": null,\n      "water_level_unit": "m"\n    },\n    {\n      "i'
    'd": "00000000-0000-0000-0000-00000001637d",\n      "tenant_id": "0'
    '0000000-0000-0000-0000-00000001675f",\n      "code": "670",\n      '
    '"name": "Synthetic qualification 670",\n      "location": {\n      '
    '  "lon": 85.05,\n        "lat": 27.05,\n        "altitude_masl": nu'
    'll\n      },\n      "station_kind": "river",\n      "basin_id": "000'
    '00000-0000-0000-0000-000000017705",\n      "timezone": "UTC",\n    '
    '  "regulation_type": null,\n      "forecast_targets": null,\n      '
    '"measured_parameters": [\n        "discharge",\n        "water_leve'
    'l"\n      ],\n      "station_status": "onboarding",\n      "created_'
    'at": "2026-01-09T23:00:00+00:00",\n      "updated_at": "2026-01-09'
    'T23:00:00+00:00",\n      "network": "dhm",\n      "ownership": "own'
    '",\n      "wigos_id": null,\n      "gauging_status": "gauged",\n    '
    '  "water_level_datum_masl": null,\n      "water_level_unit": "m"\n '
    '   },\n    {\n      "id": "00000000-0000-0000-0000-00000001637e",\n '
    '     "tenant_id": "00000000-0000-0000-0000-00000001675f",\n      "'
    'code": "684",\n      "name": "Synthetic qualification 684",\n      '
    '"location": {\n        "lon": 85.05,\n        "lat": 27.05,\n       '
    ' "altitude_masl": null\n      },\n      "station_kind": "river",\n  '
    '    "basin_id": "00000000-0000-0000-0000-000000017706",\n      "ti'
    'mezone": "UTC",\n      "regulation_type": null,\n      "forecast_ta'
    'rgets": null,\n      "measured_parameters": [\n        "discharge",'
    '\n        "water_level"\n      ],\n      "station_status": "onboardi'
    'ng",\n      "created_at": "2026-01-09T23:00:00+00:00",\n      "upda'
    'ted_at": "2026-01-09T23:00:00+00:00",\n      "network": "dhm",\n   '
    '   "ownership": "own",\n      "wigos_id": null,\n      "gauging_sta'
    'tus": "gauged",\n      "water_level_datum_masl": null,\n      "wate'
    'r_level_unit": "m"\n    }\n  ],\n  "basins": [\n    {\n      "id": "00'
    '000000-0000-0000-0000-000000017701",\n      "code": "synthetic-447'
    '",\n      "name": "Synthetic qualification basin 447",\n      "geom'
    'etry_wkt": "MULTIPOLYGON (((85 27, 85.1 27, 85.1 27.1, 85 27.1, 8'
    '5 27)))",\n      "area_km2": 100.0,\n      "attributes": {\n        '
    '"area_km2": 100.0\n      },\n      "regional_basin": null,\n      "b'
    'and_geometries": null,\n      "created_at": "2026-01-09T23:00:00+0'
    '0:00",\n      "network": "qualification-fixture",\n      "package_i'
    'd": null\n    },\n    {\n      "id": "00000000-0000-0000-0000-000000'
    '017702",\n      "code": "synthetic-450",\n      "name": "Synthetic '
    'qualification basin 450",\n      "geometry_wkt": "MULTIPOLYGON ((('
    '85 27, 85.1 27, 85.1 27.1, 85 27.1, 85 27)))",\n      "area_km2": '
    '100.0,\n      "attributes": {\n        "area_km2": 100.0\n      },\n '
    '     "regional_basin": null,\n      "band_geometries": null,\n     '
    ' "created_at": "2026-01-09T23:00:00+00:00",\n      "network": "qua'
    'lification-fixture",\n      "package_id": null\n    },\n    {\n      '
    '"id": "00000000-0000-0000-0000-000000017703",\n      "code": "synt'
    'hetic-604.5",\n      "name": "Synthetic qualification basin 604.5"'
    ',\n      "geometry_wkt": "MULTIPOLYGON (((85 27, 85.1 27, 85.1 27.'
    '1, 85 27.1, 85 27)))",\n      "area_km2": 100.0,\n      "attributes'
    '": {\n        "area_km2": 100.0\n      },\n      "regional_basin": n'
    'ull,\n      "band_geometries": null,\n      "created_at": "2026-01-'
    '09T23:00:00+00:00",\n      "network": "qualification-fixture",\n   '
    '   "package_id": null\n    },\n    {\n      "id": "00000000-0000-000'
    '0-0000-000000017704",\n      "code": "synthetic-647",\n      "name"'
    ': "Synthetic qualification basin 647",\n      "geometry_wkt": "MUL'
    'TIPOLYGON (((85 27, 85.1 27, 85.1 27.1, 85 27.1, 85 27)))",\n     '
    ' "area_km2": 100.0,\n      "attributes": {\n        "area_km2": 100'
    '.0\n      },\n      "regional_basin": null,\n      "band_geometries"'
    ': null,\n      "created_at": "2026-01-09T23:00:00+00:00",\n      "n'
    'etwork": "qualification-fixture",\n      "package_id": null\n    },'
    '\n    {\n      "id": "00000000-0000-0000-0000-000000017705",\n      '
    '"code": "synthetic-670",\n      "name": "Synthetic qualification b'
    'asin 670",\n      "geometry_wkt": "MULTIPOLYGON (((85 27, 85.1 27,'
    ' 85.1 27.1, 85 27.1, 85 27)))",\n      "area_km2": 100.0,\n      "a'
    'ttributes": {\n        "area_km2": 100.0\n      },\n      "regional_'
    'basin": null,\n      "band_geometries": null,\n      "created_at": '
    '"2026-01-09T23:00:00+00:00",\n      "network": "qualification-fixt'
    'ure",\n      "package_id": null\n    },\n    {\n      "id": "00000000'
    '-0000-0000-0000-000000017706",\n      "code": "synthetic-684",\n   '
    '   "name": "Synthetic qualification basin 684",\n      "geometry_w'
    'kt": "MULTIPOLYGON (((85 27, 85.1 27, 85.1 27.1, 85 27.1, 85 27))'
    ')",\n      "area_km2": 100.0,\n      "attributes": {\n        "area_'
    'km2": 100.0\n      },\n      "regional_basin": null,\n      "band_ge'
    'ometries": null,\n      "created_at": "2026-01-09T23:00:00+00:00",'
    '\n      "network": "qualification-fixture",\n      "package_id": nu'
    'll\n    }\n  ],\n  "weather_binding": {\n    "nwp_source": "era5_land'
    '",\n    "extraction_type": "basin_average",\n    "status": "active"'
    ',\n    "role": "reanalysis"\n  },\n  "gateway_bindings": [\n    {\n   '
    '   "station_id": "00000000-0000-0000-0000-000000016379",\n      "b'
    'asin_id": "00000000-0000-0000-0000-000000017701",\n      "gateway_'
    'hru_name": "nepal6_20260923",\n      "name": "g_447",\n      "spati'
    'al_type": "basin_average",\n      "band_id": null,\n      "package_'
    'id": null,\n      "imported_at": "2026-01-09T23:00:00+00:00"\n    }'
    ',\n    {\n      "station_id": "00000000-0000-0000-0000-00000001637a'
    '",\n      "basin_id": "00000000-0000-0000-0000-000000017702",\n    '
    '  "gateway_hru_name": "nepal6_20260923",\n      "name": "g_450",\n '
    '     "spatial_type": "basin_average",\n      "band_id": null,\n    '
    '  "package_id": null,\n      "imported_at": "2026-01-09T23:00:00+0'
    '0:00"\n    },\n    {\n      "station_id": "00000000-0000-0000-0000-0'
    '0000001637b",\n      "basin_id": "00000000-0000-0000-0000-00000001'
    '7703",\n      "gateway_hru_name": "nepal6_20260923",\n      "name":'
    ' "g_604_5",\n      "spatial_type": "basin_average",\n      "band_id'
    '": null,\n      "package_id": null,\n      "imported_at": "2026-01-'
    '09T23:00:00+00:00"\n    },\n    {\n      "station_id": "00000000-000'
    '0-0000-0000-00000001637c",\n      "basin_id": "00000000-0000-0000-'
    '0000-000000017704",\n      "gateway_hru_name": "nepal6_20260923",\n'
    '      "name": "g_647",\n      "spatial_type": "basin_average",\n   '
    '   "band_id": null,\n      "package_id": null,\n      "imported_at"'
    ': "2026-01-09T23:00:00+00:00"\n    },\n    {\n      "station_id": "0'
    '0000000-0000-0000-0000-00000001637d",\n      "basin_id": "00000000'
    '-0000-0000-0000-000000017705",\n      "gateway_hru_name": "nepal6_'
    '20260923",\n      "name": "g_670",\n      "spatial_type": "basin_av'
    'erage",\n      "band_id": null,\n      "package_id": null,\n      "i'
    'mported_at": "2026-01-09T23:00:00+00:00"\n    },\n    {\n      "stat'
    'ion_id": "00000000-0000-0000-0000-00000001637e",\n      "basin_id"'
    ': "00000000-0000-0000-0000-000000017706",\n      "gateway_hru_name'
    '": "nepal6_20260923",\n      "name": "g_684",\n      "spatial_type"'
    ': "basin_average",\n      "band_id": null,\n      "package_id": nul'
    'l,\n      "imported_at": "2026-01-09T23:00:00+00:00"\n    }\n  ],\n  '
    '"ordinary": [\n    {\n      "id": "00000000-0000-0000-0000-00000001'
    'adb1",\n      "station_id": "00000000-0000-0000-0000-000000016379"'
    ',\n      "timestamp": "2026-01-10T00:00:00+00:00",\n      "paramete'
    'r": "discharge",\n      "value": 20.0,\n      "source": "manual_imp'
    'ort",\n      "rating_curve_id": null,\n      "rating_curve_correcti'
    'on_version": null,\n      "qc_status": "qc_passed",\n      "qc_flag'
    's": [\n        {\n          "rule_id": "range_check",\n          "ru'
    'le_version": "1.2",\n          "status": "qc_passed",\n          "d'
    'etail": null\n        }\n      ],\n      "qc_rule_version": "1.2",\n '
    '     "created_at": "2026-01-20T00:00:00+00:00",\n      "delivery_i'
    'd": "dhm-barkhk-2026-09-08"\n    },\n    {\n      "id": "00000000-00'
    '00-0000-0000-00000001adb2",\n      "station_id": "00000000-0000-00'
    '00-0000-000000016379",\n      "timestamp": "2026-01-11T00:00:00+00'
    ':00",\n      "parameter": "discharge",\n      "value": 21.0,\n      '
    '"source": "manual_import",\n      "rating_curve_id": null,\n      "'
    'rating_curve_correction_version": null,\n      "qc_status": "qc_pa'
    'ssed",\n      "qc_flags": [\n        {\n          "rule_id": "range_'
    'check",\n          "rule_version": "1.2",\n          "status": "qc_'
    'passed",\n          "detail": null\n        }\n      ],\n      "qc_ru'
    'le_version": "1.2",\n      "created_at": "2026-01-20T00:00:00+00:0'
    '0",\n      "delivery_id": "dhm-barkhk-2026-09-08"\n    },\n    {\n   '
    '   "id": "00000000-0000-0000-0000-00000001adb3",\n      "station_i'
    'd": "00000000-0000-0000-0000-000000016379",\n      "timestamp": "2'
    '026-01-12T00:00:00+00:00",\n      "parameter": "discharge",\n      '
    '"value": 22.0,\n      "source": "manual_import",\n      "rating_cur'
    've_id": null,\n      "rating_curve_correction_version": null,\n    '
    '  "qc_status": "qc_passed",\n      "qc_flags": [\n        {\n       '
    '   "rule_id": "range_check",\n          "rule_version": "1.2",\n   '
    '       "status": "qc_passed",\n          "detail": null\n        }\n'
    '      ],\n      "qc_rule_version": "1.2",\n      "created_at": "202'
    '6-01-20T00:00:00+00:00",\n      "delivery_id": "dhm-barkhk-2026-09'
    '-08"\n    },\n    {\n      "id": "00000000-0000-0000-0000-00000001ad'
    'b4",\n      "station_id": "00000000-0000-0000-0000-000000016379",\n'
    '      "timestamp": "2026-01-13T00:00:00+00:00",\n      "parameter"'
    ': "discharge",\n      "value": 23.0,\n      "source": "manual_impor'
    't",\n      "rating_curve_id": null,\n      "rating_curve_correction'
    '_version": null,\n      "qc_status": "qc_passed",\n      "qc_flags"'
    ': [\n        {\n          "rule_id": "range_check",\n          "rule'
    '_version": "1.2",\n          "status": "qc_passed",\n          "det'
    'ail": null\n        }\n      ],\n      "qc_rule_version": "1.2",\n   '
    '   "created_at": "2026-01-20T00:00:00+00:00",\n      "delivery_id"'
    ': "dhm-barkhk-2026-09-08"\n    },\n    {\n      "id": "00000000-0000'
    '-0000-0000-00000001adb5",\n      "station_id": "00000000-0000-0000'
    '-0000-000000016379",\n      "timestamp": "2026-01-14T00:00:00+00:0'
    '0",\n      "parameter": "discharge",\n      "value": 24.0,\n      "s'
    'ource": "manual_import",\n      "rating_curve_id": null,\n      "ra'
    'ting_curve_correction_version": null,\n      "qc_status": "qc_pass'
    'ed",\n      "qc_flags": [\n        {\n          "rule_id": "range_ch'
    'eck",\n          "rule_version": "1.2",\n          "status": "qc_pa'
    'ssed",\n          "detail": null\n        }\n      ],\n      "qc_rule'
    '_version": "1.2",\n      "created_at": "2026-01-20T00:00:00+00:00"'
    ',\n      "delivery_id": "dhm-barkhk-2026-09-08"\n    },\n    {\n     '
    ' "id": "00000000-0000-0000-0000-00000001adb6",\n      "station_id"'
    ': "00000000-0000-0000-0000-000000016379",\n      "timestamp": "202'
    '6-01-15T00:00:00+00:00",\n      "parameter": "discharge",\n      "v'
    'alue": 25.0,\n      "source": "manual_import",\n      "rating_curve'
    '_id": null,\n      "rating_curve_correction_version": null,\n      '
    '"qc_status": "qc_passed",\n      "qc_flags": [\n        {\n         '
    ' "rule_id": "range_check",\n          "rule_version": "1.2",\n     '
    '     "status": "qc_passed",\n          "detail": null\n        }\n  '
    '    ],\n      "qc_rule_version": "1.2",\n      "created_at": "2026-'
    '01-20T00:00:00+00:00",\n      "delivery_id": "dhm-barkhk-2026-09-0'
    '8"\n    },\n    {\n      "id": "00000000-0000-0000-0000-00000001adb7'
    '",\n      "station_id": "00000000-0000-0000-0000-000000016379",\n  '
    '    "timestamp": "2026-01-16T00:00:00+00:00",\n      "parameter": '
    '"discharge",\n      "value": 26.0,\n      "source": "manual_import"'
    ',\n      "rating_curve_id": null,\n      "rating_curve_correction_v'
    'ersion": null,\n      "qc_status": "qc_passed",\n      "qc_flags": '
    '[\n        {\n          "rule_id": "range_check",\n          "rule_v'
    'ersion": "1.2",\n          "status": "qc_passed",\n          "detai'
    'l": null\n        }\n      ],\n      "qc_rule_version": "1.2",\n     '
    ' "created_at": "2026-01-20T00:00:00+00:00",\n      "delivery_id": '
    '"dhm-barkhk-2026-09-08"\n    },\n    {\n      "id": "00000000-0000-0'
    '000-0000-00000001adb8",\n      "station_id": "00000000-0000-0000-0'
    '000-000000016379",\n      "timestamp": "2026-01-17T00:00:00+00:00"'
    ',\n      "parameter": "discharge",\n      "value": 27.0,\n      "sou'
    'rce": "manual_import",\n      "rating_curve_id": null,\n      "rati'
    'ng_curve_correction_version": null,\n      "qc_status": "qc_passed'
    '",\n      "qc_flags": [\n        {\n          "rule_id": "range_chec'
    'k",\n          "rule_version": "1.2",\n          "status": "qc_pass'
    'ed",\n          "detail": null\n        }\n      ],\n      "qc_rule_v'
    'ersion": "1.2",\n      "created_at": "2026-01-20T00:00:00+00:00",\n'
    '      "delivery_id": "dhm-barkhk-2026-09-08"\n    },\n    {\n      "'
    'id": "00000000-0000-0000-0000-00000001adb9",\n      "station_id": '
    '"00000000-0000-0000-0000-000000016379",\n      "timestamp": "2026-'
    '01-18T00:00:00+00:00",\n      "parameter": "discharge",\n      "val'
    'ue": 28.0,\n      "source": "manual_import",\n      "rating_curve_i'
    'd": null,\n      "rating_curve_correction_version": null,\n      "q'
    'c_status": "qc_passed",\n      "qc_flags": [\n        {\n          "'
    'rule_id": "range_check",\n          "rule_version": "1.2",\n       '
    '   "status": "qc_passed",\n          "detail": null\n        }\n    '
    '  ],\n      "qc_rule_version": "1.2",\n      "created_at": "2026-01'
    '-20T00:00:00+00:00",\n      "delivery_id": "dhm-barkhk-2026-09-08"'
    '\n    },\n    {\n      "id": "00000000-0000-0000-0000-00000001adba",'
    '\n      "station_id": "00000000-0000-0000-0000-000000016379",\n    '
    '  "timestamp": "2026-01-19T00:00:00+00:00",\n      "parameter": "d'
    'ischarge",\n      "value": 29.0,\n      "source": "manual_import",\n'
    '      "rating_curve_id": null,\n      "rating_curve_correction_ver'
    'sion": null,\n      "qc_status": "qc_passed",\n      "qc_flags": [\n'
    '        {\n          "rule_id": "range_check",\n          "rule_ver'
    'sion": "1.2",\n          "status": "qc_passed",\n          "detail"'
    ': null\n        }\n      ],\n      "qc_rule_version": "1.2",\n      "'
    'created_at": "2026-01-20T00:00:00+00:00",\n      "delivery_id": "d'
    'hm-barkhk-2026-09-08"\n    },\n    {\n      "id": "00000000-0000-000'
    '0-0000-00000001adbb",\n      "station_id": "00000000-0000-0000-000'
    '0-00000001637a",\n      "timestamp": "2026-01-10T00:00:00+00:00",\n'
    '      "parameter": "discharge",\n      "value": 20.0,\n      "sourc'
    'e": "manual_import",\n      "rating_curve_id": null,\n      "rating'
    '_curve_correction_version": null,\n      "qc_status": "qc_passed",'
    '\n      "qc_flags": [\n        {\n          "rule_id": "range_check"'
    ',\n          "rule_version": "1.2",\n          "status": "qc_passed'
    '",\n          "detail": null\n        }\n      ],\n      "qc_rule_ver'
    'sion": "1.2",\n      "created_at": "2026-01-20T00:00:00+00:00",\n  '
    '    "delivery_id": "dhm-barkhk-2026-09-08"\n    },\n    {\n      "id'
    '": "00000000-0000-0000-0000-00000001adbc",\n      "station_id": "0'
    '0000000-0000-0000-0000-00000001637a",\n      "timestamp": "2026-01'
    '-11T00:00:00+00:00",\n      "parameter": "discharge",\n      "value'
    '": 21.0,\n      "source": "manual_import",\n      "rating_curve_id"'
    ': null,\n      "rating_curve_correction_version": null,\n      "qc_'
    'status": "qc_passed",\n      "qc_flags": [\n        {\n          "ru'
    'le_id": "range_check",\n          "rule_version": "1.2",\n         '
    ' "status": "qc_passed",\n          "detail": null\n        }\n      '
    '],\n      "qc_rule_version": "1.2",\n      "created_at": "2026-01-2'
    '0T00:00:00+00:00",\n      "delivery_id": "dhm-barkhk-2026-09-08"\n '
    '   },\n    {\n      "id": "00000000-0000-0000-0000-00000001adbd",\n '
    '     "station_id": "00000000-0000-0000-0000-00000001637a",\n      '
    '"timestamp": "2026-01-12T00:00:00+00:00",\n      "parameter": "dis'
    'charge",\n      "value": 22.0,\n      "source": "manual_import",\n  '
    '    "rating_curve_id": null,\n      "rating_curve_correction_versi'
    'on": null,\n      "qc_status": "qc_passed",\n      "qc_flags": [\n  '
    '      {\n          "rule_id": "range_check",\n          "rule_versi'
    'on": "1.2",\n          "status": "qc_passed",\n          "detail": '
    'null\n        }\n      ],\n      "qc_rule_version": "1.2",\n      "cr'
    'eated_at": "2026-01-20T00:00:00+00:00",\n      "delivery_id": "dhm'
    '-barkhk-2026-09-08"\n    },\n    {\n      "id": "00000000-0000-0000-'
    '0000-00000001adbe",\n      "station_id": "00000000-0000-0000-0000-'
    '00000001637a",\n      "timestamp": "2026-01-13T00:00:00+00:00",\n  '
    '    "parameter": "discharge",\n      "value": 23.0,\n      "source"'
    ': "manual_import",\n      "rating_curve_id": null,\n      "rating_c'
    'urve_correction_version": null,\n      "qc_status": "qc_passed",\n '
    '     "qc_flags": [\n        {\n          "rule_id": "range_check",\n'
    '          "rule_version": "1.2",\n          "status": "qc_passed",'
    '\n          "detail": null\n        }\n      ],\n      "qc_rule_versi'
    'on": "1.2",\n      "created_at": "2026-01-20T00:00:00+00:00",\n    '
    '  "delivery_id": "dhm-barkhk-2026-09-08"\n    },\n    {\n      "id":'
    ' "00000000-0000-0000-0000-00000001adbf",\n      "station_id": "000'
    '00000-0000-0000-0000-00000001637a",\n      "timestamp": "2026-01-1'
    '4T00:00:00+00:00",\n      "parameter": "discharge",\n      "value":'
    ' 24.0,\n      "source": "manual_import",\n      "rating_curve_id": '
    'null,\n      "rating_curve_correction_version": null,\n      "qc_st'
    'atus": "qc_passed",\n      "qc_flags": [\n        {\n          "rule'
    '_id": "range_check",\n          "rule_version": "1.2",\n          "'
    'status": "qc_passed",\n          "detail": null\n        }\n      ],'
    '\n      "qc_rule_version": "1.2",\n      "created_at": "2026-01-20T'
    '00:00:00+00:00",\n      "delivery_id": "dhm-barkhk-2026-09-08"\n   '
    ' },\n    {\n      "id": "00000000-0000-0000-0000-00000001adc0",\n   '
    '   "station_id": "00000000-0000-0000-0000-00000001637a",\n      "t'
    'imestamp": "2026-01-15T00:00:00+00:00",\n      "parameter": "disch'
    'arge",\n      "value": 25.0,\n      "source": "manual_import",\n    '
    '  "rating_curve_id": null,\n      "rating_curve_correction_version'
    '": null,\n      "qc_status": "qc_passed",\n      "qc_flags": [\n    '
    '    {\n          "rule_id": "range_check",\n          "rule_version'
    '": "1.2",\n          "status": "qc_passed",\n          "detail": nu'
    'll\n        }\n      ],\n      "qc_rule_version": "1.2",\n      "crea'
    'ted_at": "2026-01-20T00:00:00+00:00",\n      "delivery_id": "dhm-b'
    'arkhk-2026-09-08"\n    },\n    {\n      "id": "00000000-0000-0000-00'
    '00-00000001adc1",\n      "station_id": "00000000-0000-0000-0000-00'
    '000001637a",\n      "timestamp": "2026-01-16T00:00:00+00:00",\n    '
    '  "parameter": "discharge",\n      "value": 26.0,\n      "source": '
    '"manual_import",\n      "rating_curve_id": null,\n      "rating_cur'
    've_correction_version": null,\n      "qc_status": "qc_passed",\n   '
    '   "qc_flags": [\n        {\n          "rule_id": "range_check",\n  '
    '        "rule_version": "1.2",\n          "status": "qc_passed",\n '
    '         "detail": null\n        }\n      ],\n      "qc_rule_version'
    '": "1.2",\n      "created_at": "2026-01-20T00:00:00+00:00",\n      '
    '"delivery_id": "dhm-barkhk-2026-09-08"\n    },\n    {\n      "id": "'
    '00000000-0000-0000-0000-00000001adc2",\n      "station_id": "00000'
    '000-0000-0000-0000-00000001637a",\n      "timestamp": "2026-01-17T'
    '00:00:00+00:00",\n      "parameter": "discharge",\n      "value": 2'
    '7.0,\n      "source": "manual_import",\n      "rating_curve_id": nu'
    'll,\n      "rating_curve_correction_version": null,\n      "qc_stat'
    'us": "qc_passed",\n      "qc_flags": [\n        {\n          "rule_i'
    'd": "range_check",\n          "rule_version": "1.2",\n          "st'
    'atus": "qc_passed",\n          "detail": null\n        }\n      ],\n '
    '     "qc_rule_version": "1.2",\n      "created_at": "2026-01-20T00'
    ':00:00+00:00",\n      "delivery_id": "dhm-barkhk-2026-09-08"\n    }'
    ',\n    {\n      "id": "00000000-0000-0000-0000-00000001adc3",\n     '
    ' "station_id": "00000000-0000-0000-0000-00000001637a",\n      "tim'
    'estamp": "2026-01-18T00:00:00+00:00",\n      "parameter": "dischar'
    'ge",\n      "value": 28.0,\n      "source": "manual_import",\n      '
    '"rating_curve_id": null,\n      "rating_curve_correction_version":'
    ' null,\n      "qc_status": "qc_passed",\n      "qc_flags": [\n      '
    '  {\n          "rule_id": "range_check",\n          "rule_version":'
    ' "1.2",\n          "status": "qc_passed",\n          "detail": null'
    '\n        }\n      ],\n      "qc_rule_version": "1.2",\n      "create'
    'd_at": "2026-01-20T00:00:00+00:00",\n      "delivery_id": "dhm-bar'
    'khk-2026-09-08"\n    },\n    {\n      "id": "00000000-0000-0000-0000'
    '-00000001adc4",\n      "station_id": "00000000-0000-0000-0000-0000'
    '0001637a",\n      "timestamp": "2026-01-19T00:00:00+00:00",\n      '
    '"parameter": "discharge",\n      "value": 29.0,\n      "source": "m'
    'anual_import",\n      "rating_curve_id": null,\n      "rating_curve'
    '_correction_version": null,\n      "qc_status": "qc_passed",\n     '
    ' "qc_flags": [\n        {\n          "rule_id": "range_check",\n    '
    '      "rule_version": "1.2",\n          "status": "qc_passed",\n   '
    '       "detail": null\n        }\n      ],\n      "qc_rule_version":'
    ' "1.2",\n      "created_at": "2026-01-20T00:00:00+00:00",\n      "d'
    'elivery_id": "dhm-barkhk-2026-09-08"\n    },\n    {\n      "id": "00'
    '000000-0000-0000-0000-00000001adc5",\n      "station_id": "0000000'
    '0-0000-0000-0000-00000001637b",\n      "timestamp": "2026-01-10T00'
    ':00:00+00:00",\n      "parameter": "discharge",\n      "value": 20.'
    '0,\n      "source": "manual_import",\n      "rating_curve_id": null'
    ',\n      "rating_curve_correction_version": null,\n      "qc_status'
    '": "qc_passed",\n      "qc_flags": [\n        {\n          "rule_id"'
    ': "range_check",\n          "rule_version": "1.2",\n          "stat'
    'us": "qc_passed",\n          "detail": null\n        }\n      ],\n   '
    '   "qc_rule_version": "1.2",\n      "created_at": "2026-01-20T00:0'
    '0:00+00:00",\n      "delivery_id": "dhm-barkhk-2026-09-08"\n    },\n'
    '    {\n      "id": "00000000-0000-0000-0000-00000001adc6",\n      "'
    'station_id": "00000000-0000-0000-0000-00000001637b",\n      "times'
    'tamp": "2026-01-11T00:00:00+00:00",\n      "parameter": "discharge'
    '",\n      "value": 21.0,\n      "source": "manual_import",\n      "r'
    'ating_curve_id": null,\n      "rating_curve_correction_version": n'
    'ull,\n      "qc_status": "qc_passed",\n      "qc_flags": [\n        '
    '{\n          "rule_id": "range_check",\n          "rule_version": "'
    '1.2",\n          "status": "qc_passed",\n          "detail": null\n '
    '       }\n      ],\n      "qc_rule_version": "1.2",\n      "created_'
    'at": "2026-01-20T00:00:00+00:00",\n      "delivery_id": "dhm-barkh'
    'k-2026-09-08"\n    },\n    {\n      "id": "00000000-0000-0000-0000-0'
    '0000001adc7",\n      "station_id": "00000000-0000-0000-0000-000000'
    '01637b",\n      "timestamp": "2026-01-12T00:00:00+00:00",\n      "p'
    'arameter": "discharge",\n      "value": 22.0,\n      "source": "man'
    'ual_import",\n      "rating_curve_id": null,\n      "rating_curve_c'
    'orrection_version": null,\n      "qc_status": "qc_passed",\n      "'
    'qc_flags": [\n        {\n          "rule_id": "range_check",\n      '
    '    "rule_version": "1.2",\n          "status": "qc_passed",\n     '
    '     "detail": null\n        }\n      ],\n      "qc_rule_version": "'
    '1.2",\n      "created_at": "2026-01-20T00:00:00+00:00",\n      "del'
    'ivery_id": "dhm-barkhk-2026-09-08"\n    },\n    {\n      "id": "0000'
    '0000-0000-0000-0000-00000001adc8",\n      "station_id": "00000000-'
    '0000-0000-0000-00000001637b",\n      "timestamp": "2026-01-13T00:0'
    '0:00+00:00",\n      "parameter": "discharge",\n      "value": 23.0,'
    '\n      "source": "manual_import",\n      "rating_curve_id": null,\n'
    '      "rating_curve_correction_version": null,\n      "qc_status":'
    ' "qc_passed",\n      "qc_flags": [\n        {\n          "rule_id": '
    '"range_check",\n          "rule_version": "1.2",\n          "status'
    '": "qc_passed",\n          "detail": null\n        }\n      ],\n     '
    ' "qc_rule_version": "1.2",\n      "created_at": "2026-01-20T00:00:'
    '00+00:00",\n      "delivery_id": "dhm-barkhk-2026-09-08"\n    },\n  '
    '  {\n      "id": "00000000-0000-0000-0000-00000001adc9",\n      "st'
    'ation_id": "00000000-0000-0000-0000-00000001637b",\n      "timesta'
    'mp": "2026-01-14T00:00:00+00:00",\n      "parameter": "discharge",'
    '\n      "value": 24.0,\n      "source": "manual_import",\n      "rat'
    'ing_curve_id": null,\n      "rating_curve_correction_version": nul'
    'l,\n      "qc_status": "qc_passed",\n      "qc_flags": [\n        {\n'
    '          "rule_id": "range_check",\n          "rule_version": "1.'
    '2",\n          "status": "qc_passed",\n          "detail": null\n   '
    '     }\n      ],\n      "qc_rule_version": "1.2",\n      "created_at'
    '": "2026-01-20T00:00:00+00:00",\n      "delivery_id": "dhm-barkhk-'
    '2026-09-08"\n    },\n    {\n      "id": "00000000-0000-0000-0000-000'
    '00001adca",\n      "station_id": "00000000-0000-0000-0000-00000001'
    '637b",\n      "timestamp": "2026-01-15T00:00:00+00:00",\n      "par'
    'ameter": "discharge",\n      "value": 25.0,\n      "source": "manua'
    'l_import",\n      "rating_curve_id": null,\n      "rating_curve_cor'
    'rection_version": null,\n      "qc_status": "qc_passed",\n      "qc'
    '_flags": [\n        {\n          "rule_id": "range_check",\n        '
    '  "rule_version": "1.2",\n          "status": "qc_passed",\n       '
    '   "detail": null\n        }\n      ],\n      "qc_rule_version": "1.'
    '2",\n      "created_at": "2026-01-20T00:00:00+00:00",\n      "deliv'
    'ery_id": "dhm-barkhk-2026-09-08"\n    },\n    {\n      "id": "000000'
    '00-0000-0000-0000-00000001adcb",\n      "station_id": "00000000-00'
    '00-0000-0000-00000001637b",\n      "timestamp": "2026-01-16T00:00:'
    '00+00:00",\n      "parameter": "discharge",\n      "value": 26.0,\n '
    '     "source": "manual_import",\n      "rating_curve_id": null,\n  '
    '    "rating_curve_correction_version": null,\n      "qc_status": "'
    'qc_passed",\n      "qc_flags": [\n        {\n          "rule_id": "r'
    'ange_check",\n          "rule_version": "1.2",\n          "status":'
    ' "qc_passed",\n          "detail": null\n        }\n      ],\n      "'
    'qc_rule_version": "1.2",\n      "created_at": "2026-01-20T00:00:00'
    '+00:00",\n      "delivery_id": "dhm-barkhk-2026-09-08"\n    },\n    '
    '{\n      "id": "00000000-0000-0000-0000-00000001adcc",\n      "stat'
    'ion_id": "00000000-0000-0000-0000-00000001637b",\n      "timestamp'
    '": "2026-01-17T00:00:00+00:00",\n      "parameter": "discharge",\n '
    '     "value": 27.0,\n      "source": "manual_import",\n      "ratin'
    'g_curve_id": null,\n      "rating_curve_correction_version": null,'
    '\n      "qc_status": "qc_passed",\n      "qc_flags": [\n        {\n  '
    '        "rule_id": "range_check",\n          "rule_version": "1.2"'
    ',\n          "status": "qc_passed",\n          "detail": null\n     '
    '   }\n      ],\n      "qc_rule_version": "1.2",\n      "created_at":'
    ' "2026-01-20T00:00:00+00:00",\n      "delivery_id": "dhm-barkhk-20'
    '26-09-08"\n    },\n    {\n      "id": "00000000-0000-0000-0000-00000'
    '001adcd",\n      "station_id": "00000000-0000-0000-0000-0000000163'
    '7b",\n      "timestamp": "2026-01-18T00:00:00+00:00",\n      "param'
    'eter": "discharge",\n      "value": 28.0,\n      "source": "manual_'
    'import",\n      "rating_curve_id": null,\n      "rating_curve_corre'
    'ction_version": null,\n      "qc_status": "qc_passed",\n      "qc_f'
    'lags": [\n        {\n          "rule_id": "range_check",\n          '
    '"rule_version": "1.2",\n          "status": "qc_passed",\n         '
    ' "detail": null\n        }\n      ],\n      "qc_rule_version": "1.2"'
    ',\n      "created_at": "2026-01-20T00:00:00+00:00",\n      "deliver'
    'y_id": "dhm-barkhk-2026-09-08"\n    },\n    {\n      "id": "00000000'
    '-0000-0000-0000-00000001adce",\n      "station_id": "00000000-0000'
    '-0000-0000-00000001637b",\n      "timestamp": "2026-01-19T00:00:00'
    '+00:00",\n      "parameter": "discharge",\n      "value": 29.0,\n   '
    '   "source": "manual_import",\n      "rating_curve_id": null,\n    '
    '  "rating_curve_correction_version": null,\n      "qc_status": "qc'
    '_passed",\n      "qc_flags": [\n        {\n          "rule_id": "ran'
    'ge_check",\n          "rule_version": "1.2",\n          "status": "'
    'qc_passed",\n          "detail": null\n        }\n      ],\n      "qc'
    '_rule_version": "1.2",\n      "created_at": "2026-01-20T00:00:00+0'
    '0:00",\n      "delivery_id": "dhm-barkhk-2026-09-08"\n    },\n    {\n'
    '      "id": "00000000-0000-0000-0000-00000001adcf",\n      "statio'
    'n_id": "00000000-0000-0000-0000-00000001637c",\n      "timestamp":'
    ' "2026-01-10T00:00:00+00:00",\n      "parameter": "discharge",\n   '
    '   "value": 20.0,\n      "source": "manual_import",\n      "rating_'
    'curve_id": null,\n      "rating_curve_correction_version": null,\n '
    '     "qc_status": "qc_passed",\n      "qc_flags": [\n        {\n    '
    '      "rule_id": "range_check",\n          "rule_version": "1.2",\n'
    '          "status": "qc_passed",\n          "detail": null\n       '
    ' }\n      ],\n      "qc_rule_version": "1.2",\n      "created_at": "'
    '2026-01-20T00:00:00+00:00",\n      "delivery_id": "dhm-barkhk-2026'
    '-09-08"\n    },\n    {\n      "id": "00000000-0000-0000-0000-0000000'
    '1add0",\n      "station_id": "00000000-0000-0000-0000-00000001637c'
    '",\n      "timestamp": "2026-01-11T00:00:00+00:00",\n      "paramet'
    'er": "discharge",\n      "value": 21.0,\n      "source": "manual_im'
    'port",\n      "rating_curve_id": null,\n      "rating_curve_correct'
    'ion_version": null,\n      "qc_status": "qc_passed",\n      "qc_fla'
    'gs": [\n        {\n          "rule_id": "range_check",\n          "r'
    'ule_version": "1.2",\n          "status": "qc_passed",\n          "'
    'detail": null\n        }\n      ],\n      "qc_rule_version": "1.2",\n'
    '      "created_at": "2026-01-20T00:00:00+00:00",\n      "delivery_'
    'id": "dhm-barkhk-2026-09-08"\n    },\n    {\n      "id": "00000000-0'
    '000-0000-0000-00000001add1",\n      "station_id": "00000000-0000-0'
    '000-0000-00000001637c",\n      "timestamp": "2026-01-12T00:00:00+0'
    '0:00",\n      "parameter": "discharge",\n      "value": 22.0,\n     '
    ' "source": "manual_import",\n      "rating_curve_id": null,\n      '
    '"rating_curve_correction_version": null,\n      "qc_status": "qc_p'
    'assed",\n      "qc_flags": [\n        {\n          "rule_id": "range'
    '_check",\n          "rule_version": "1.2",\n          "status": "qc'
    '_passed",\n          "detail": null\n        }\n      ],\n      "qc_r'
    'ule_version": "1.2",\n      "created_at": "2026-01-20T00:00:00+00:'
    '00",\n      "delivery_id": "dhm-barkhk-2026-09-08"\n    },\n    {\n  '
    '    "id": "00000000-0000-0000-0000-00000001add2",\n      "station_'
    'id": "00000000-0000-0000-0000-00000001637c",\n      "timestamp": "'
    '2026-01-13T00:00:00+00:00",\n      "parameter": "discharge",\n     '
    ' "value": 23.0,\n      "source": "manual_import",\n      "rating_cu'
    'rve_id": null,\n      "rating_curve_correction_version": null,\n   '
    '   "qc_status": "qc_passed",\n      "qc_flags": [\n        {\n      '
    '    "rule_id": "range_check",\n          "rule_version": "1.2",\n  '
    '        "status": "qc_passed",\n          "detail": null\n        }'
    '\n      ],\n      "qc_rule_version": "1.2",\n      "created_at": "20'
    '26-01-20T00:00:00+00:00",\n      "delivery_id": "dhm-barkhk-2026-0'
    '9-08"\n    },\n    {\n      "id": "00000000-0000-0000-0000-00000001a'
    'dd3",\n      "station_id": "00000000-0000-0000-0000-00000001637c",'
    '\n      "timestamp": "2026-01-14T00:00:00+00:00",\n      "parameter'
    '": "discharge",\n      "value": 24.0,\n      "source": "manual_impo'
    'rt",\n      "rating_curve_id": null,\n      "rating_curve_correctio'
    'n_version": null,\n      "qc_status": "qc_passed",\n      "qc_flags'
    '": [\n        {\n          "rule_id": "range_check",\n          "rul'
    'e_version": "1.2",\n          "status": "qc_passed",\n          "de'
    'tail": null\n        }\n      ],\n      "qc_rule_version": "1.2",\n  '
    '    "created_at": "2026-01-20T00:00:00+00:00",\n      "delivery_id'
    '": "dhm-barkhk-2026-09-08"\n    },\n    {\n      "id": "00000000-000'
    '0-0000-0000-00000001add4",\n      "station_id": "00000000-0000-000'
    '0-0000-00000001637c",\n      "timestamp": "2026-01-15T00:00:00+00:'
    '00",\n      "parameter": "discharge",\n      "value": 25.0,\n      "'
    'source": "manual_import",\n      "rating_curve_id": null,\n      "r'
    'ating_curve_correction_version": null,\n      "qc_status": "qc_pas'
    'sed",\n      "qc_flags": [\n        {\n          "rule_id": "range_c'
    'heck",\n          "rule_version": "1.2",\n          "status": "qc_p'
    'assed",\n          "detail": null\n        }\n      ],\n      "qc_rul'
    'e_version": "1.2",\n      "created_at": "2026-01-20T00:00:00+00:00'
    '",\n      "delivery_id": "dhm-barkhk-2026-09-08"\n    },\n    {\n    '
    '  "id": "00000000-0000-0000-0000-00000001add5",\n      "station_id'
    '": "00000000-0000-0000-0000-00000001637c",\n      "timestamp": "20'
    '26-01-16T00:00:00+00:00",\n      "parameter": "discharge",\n      "'
    'value": 26.0,\n      "source": "manual_import",\n      "rating_curv'
    'e_id": null,\n      "rating_curve_correction_version": null,\n     '
    ' "qc_status": "qc_passed",\n      "qc_flags": [\n        {\n        '
    '  "rule_id": "range_check",\n          "rule_version": "1.2",\n    '
    '      "status": "qc_passed",\n          "detail": null\n        }\n '
    '     ],\n      "qc_rule_version": "1.2",\n      "created_at": "2026'
    '-01-20T00:00:00+00:00",\n      "delivery_id": "dhm-barkhk-2026-09-'
    '08"\n    },\n    {\n      "id": "00000000-0000-0000-0000-00000001add'
    '6",\n      "station_id": "00000000-0000-0000-0000-00000001637c",\n '
    '     "timestamp": "2026-01-17T00:00:00+00:00",\n      "parameter":'
    ' "discharge",\n      "value": 27.0,\n      "source": "manual_import'
    '",\n      "rating_curve_id": null,\n      "rating_curve_correction_'
    'version": null,\n      "qc_status": "qc_passed",\n      "qc_flags":'
    ' [\n        {\n          "rule_id": "range_check",\n          "rule_'
    'version": "1.2",\n          "status": "qc_passed",\n          "deta'
    'il": null\n        }\n      ],\n      "qc_rule_version": "1.2",\n    '
    '  "created_at": "2026-01-20T00:00:00+00:00",\n      "delivery_id":'
    ' "dhm-barkhk-2026-09-08"\n    },\n    {\n      "id": "00000000-0000-'
    '0000-0000-00000001add7",\n      "station_id": "00000000-0000-0000-'
    '0000-00000001637c",\n      "timestamp": "2026-01-18T00:00:00+00:00'
    '",\n      "parameter": "discharge",\n      "value": 28.0,\n      "so'
    'urce": "manual_import",\n      "rating_curve_id": null,\n      "rat'
    'ing_curve_correction_version": null,\n      "qc_status": "qc_passe'
    'd",\n      "qc_flags": [\n        {\n          "rule_id": "range_che'
    'ck",\n          "rule_version": "1.2",\n          "status": "qc_pas'
    'sed",\n          "detail": null\n        }\n      ],\n      "qc_rule_'
    'version": "1.2",\n      "created_at": "2026-01-20T00:00:00+00:00",'
    '\n      "delivery_id": "dhm-barkhk-2026-09-08"\n    },\n    {\n      '
    '"id": "00000000-0000-0000-0000-00000001add8",\n      "station_id":'
    ' "00000000-0000-0000-0000-00000001637c",\n      "timestamp": "2026'
    '-01-19T00:00:00+00:00",\n      "parameter": "discharge",\n      "va'
    'lue": 29.0,\n      "source": "manual_import",\n      "rating_curve_'
    'id": null,\n      "rating_curve_correction_version": null,\n      "'
    'qc_status": "qc_passed",\n      "qc_flags": [\n        {\n          '
    '"rule_id": "range_check",\n          "rule_version": "1.2",\n      '
    '    "status": "qc_passed",\n          "detail": null\n        }\n   '
    '   ],\n      "qc_rule_version": "1.2",\n      "created_at": "2026-0'
    '1-20T00:00:00+00:00",\n      "delivery_id": "dhm-barkhk-2026-09-08'
    '"\n    },\n    {\n      "id": "00000000-0000-0000-0000-00000001add9"'
    ',\n      "station_id": "00000000-0000-0000-0000-00000001637d",\n   '
    '   "timestamp": "2026-01-10T00:00:00+00:00",\n      "parameter": "'
    'discharge",\n      "value": 20.0,\n      "source": "manual_import",'
    '\n      "rating_curve_id": null,\n      "rating_curve_correction_ve'
    'rsion": null,\n      "qc_status": "qc_passed",\n      "qc_flags": ['
    '\n        {\n          "rule_id": "range_check",\n          "rule_ve'
    'rsion": "1.2",\n          "status": "qc_passed",\n          "detail'
    '": null\n        }\n      ],\n      "qc_rule_version": "1.2",\n      '
    '"created_at": "2026-01-20T00:00:00+00:00",\n      "delivery_id": "'
    'dhm-barkhk-2026-09-08"\n    },\n    {\n      "id": "00000000-0000-00'
    '00-0000-00000001adda",\n      "station_id": "00000000-0000-0000-00'
    '00-00000001637d",\n      "timestamp": "2026-01-11T00:00:00+00:00",'
    '\n      "parameter": "discharge",\n      "value": 21.0,\n      "sour'
    'ce": "manual_import",\n      "rating_curve_id": null,\n      "ratin'
    'g_curve_correction_version": null,\n      "qc_status": "qc_passed"'
    ',\n      "qc_flags": [\n        {\n          "rule_id": "range_check'
    '",\n          "rule_version": "1.2",\n          "status": "qc_passe'
    'd",\n          "detail": null\n        }\n      ],\n      "qc_rule_ve'
    'rsion": "1.2",\n      "created_at": "2026-01-20T00:00:00+00:00",\n '
    '     "delivery_id": "dhm-barkhk-2026-09-08"\n    },\n    {\n      "i'
    'd": "00000000-0000-0000-0000-00000001addb",\n      "station_id": "'
    '00000000-0000-0000-0000-00000001637d",\n      "timestamp": "2026-0'
    '1-12T00:00:00+00:00",\n      "parameter": "discharge",\n      "valu'
    'e": 22.0,\n      "source": "manual_import",\n      "rating_curve_id'
    '": null,\n      "rating_curve_correction_version": null,\n      "qc'
    '_status": "qc_passed",\n      "qc_flags": [\n        {\n          "r'
    'ule_id": "range_check",\n          "rule_version": "1.2",\n        '
    '  "status": "qc_passed",\n          "detail": null\n        }\n     '
    ' ],\n      "qc_rule_version": "1.2",\n      "created_at": "2026-01-'
    '20T00:00:00+00:00",\n      "delivery_id": "dhm-barkhk-2026-09-08"\n'
    '    },\n    {\n      "id": "00000000-0000-0000-0000-00000001addc",\n'
    '      "station_id": "00000000-0000-0000-0000-00000001637d",\n     '
    ' "timestamp": "2026-01-13T00:00:00+00:00",\n      "parameter": "di'
    'scharge",\n      "value": 23.0,\n      "source": "manual_import",\n '
    '     "rating_curve_id": null,\n      "rating_curve_correction_vers'
    'ion": null,\n      "qc_status": "qc_passed",\n      "qc_flags": [\n '
    '       {\n          "rule_id": "range_check",\n          "rule_vers'
    'ion": "1.2",\n          "status": "qc_passed",\n          "detail":'
    ' null\n        }\n      ],\n      "qc_rule_version": "1.2",\n      "c'
    'reated_at": "2026-01-20T00:00:00+00:00",\n      "delivery_id": "dh'
    'm-barkhk-2026-09-08"\n    },\n    {\n      "id": "00000000-0000-0000'
    '-0000-00000001addd",\n      "station_id": "00000000-0000-0000-0000'
    '-00000001637d",\n      "timestamp": "2026-01-14T00:00:00+00:00",\n '
    '     "parameter": "discharge",\n      "value": 24.0,\n      "source'
    '": "manual_import",\n      "rating_curve_id": null,\n      "rating_'
    'curve_correction_version": null,\n      "qc_status": "qc_passed",\n'
    '      "qc_flags": [\n        {\n          "rule_id": "range_check",'
    '\n          "rule_version": "1.2",\n          "status": "qc_passed"'
    ',\n          "detail": null\n        }\n      ],\n      "qc_rule_vers'
    'ion": "1.2",\n      "created_at": "2026-01-20T00:00:00+00:00",\n   '
    '   "delivery_id": "dhm-barkhk-2026-09-08"\n    },\n    {\n      "id"'
    ': "00000000-0000-0000-0000-00000001adde",\n      "station_id": "00'
    '000000-0000-0000-0000-00000001637d",\n      "timestamp": "2026-01-'
    '15T00:00:00+00:00",\n      "parameter": "discharge",\n      "value"'
    ': 25.0,\n      "source": "manual_import",\n      "rating_curve_id":'
    ' null,\n      "rating_curve_correction_version": null,\n      "qc_s'
    'tatus": "qc_passed",\n      "qc_flags": [\n        {\n          "rul'
    'e_id": "range_check",\n          "rule_version": "1.2",\n          '
    '"status": "qc_passed",\n          "detail": null\n        }\n      ]'
    ',\n      "qc_rule_version": "1.2",\n      "created_at": "2026-01-20'
    'T00:00:00+00:00",\n      "delivery_id": "dhm-barkhk-2026-09-08"\n  '
    '  },\n    {\n      "id": "00000000-0000-0000-0000-00000001addf",\n  '
    '    "station_id": "00000000-0000-0000-0000-00000001637d",\n      "'
    'timestamp": "2026-01-16T00:00:00+00:00",\n      "parameter": "disc'
    'harge",\n      "value": 26.0,\n      "source": "manual_import",\n   '
    '   "rating_curve_id": null,\n      "rating_curve_correction_versio'
    'n": null,\n      "qc_status": "qc_passed",\n      "qc_flags": [\n   '
    '     {\n          "rule_id": "range_check",\n          "rule_versio'
    'n": "1.2",\n          "status": "qc_passed",\n          "detail": n'
    'ull\n        }\n      ],\n      "qc_rule_version": "1.2",\n      "cre'
    'ated_at": "2026-01-20T00:00:00+00:00",\n      "delivery_id": "dhm-'
    'barkhk-2026-09-08"\n    },\n    {\n      "id": "00000000-0000-0000-0'
    '000-00000001ade0",\n      "station_id": "00000000-0000-0000-0000-0'
    '0000001637d",\n      "timestamp": "2026-01-17T00:00:00+00:00",\n   '
    '   "parameter": "discharge",\n      "value": 27.0,\n      "source":'
    ' "manual_import",\n      "rating_curve_id": null,\n      "rating_cu'
    'rve_correction_version": null,\n      "qc_status": "qc_passed",\n  '
    '    "qc_flags": [\n        {\n          "rule_id": "range_check",\n '
    '         "rule_version": "1.2",\n          "status": "qc_passed",\n'
    '          "detail": null\n        }\n      ],\n      "qc_rule_versio'
    'n": "1.2",\n      "created_at": "2026-01-20T00:00:00+00:00",\n     '
    ' "delivery_id": "dhm-barkhk-2026-09-08"\n    },\n    {\n      "id": '
    '"00000000-0000-0000-0000-00000001ade1",\n      "station_id": "0000'
    '0000-0000-0000-0000-00000001637d",\n      "timestamp": "2026-01-18'
    'T00:00:00+00:00",\n      "parameter": "discharge",\n      "value": '
    '28.0,\n      "source": "manual_import",\n      "rating_curve_id": n'
    'ull,\n      "rating_curve_correction_version": null,\n      "qc_sta'
    'tus": "qc_passed",\n      "qc_flags": [\n        {\n          "rule_'
    'id": "range_check",\n          "rule_version": "1.2",\n          "s'
    'tatus": "qc_passed",\n          "detail": null\n        }\n      ],\n'
    '      "qc_rule_version": "1.2",\n      "created_at": "2026-01-20T0'
    '0:00:00+00:00",\n      "delivery_id": "dhm-barkhk-2026-09-08"\n    '
    '},\n    {\n      "id": "00000000-0000-0000-0000-00000001ade2",\n    '
    '  "station_id": "00000000-0000-0000-0000-00000001637d",\n      "ti'
    'mestamp": "2026-01-19T00:00:00+00:00",\n      "parameter": "discha'
    'rge",\n      "value": 29.0,\n      "source": "manual_import",\n     '
    ' "rating_curve_id": null,\n      "rating_curve_correction_version"'
    ': null,\n      "qc_status": "qc_passed",\n      "qc_flags": [\n     '
    '   {\n          "rule_id": "range_check",\n          "rule_version"'
    ': "1.2",\n          "status": "qc_passed",\n          "detail": nul'
    'l\n        }\n      ],\n      "qc_rule_version": "1.2",\n      "creat'
    'ed_at": "2026-01-20T00:00:00+00:00",\n      "delivery_id": "dhm-ba'
    'rkhk-2026-09-08"\n    },\n    {\n      "id": "00000000-0000-0000-000'
    '0-00000001ade3",\n      "station_id": "00000000-0000-0000-0000-000'
    '00001637e",\n      "timestamp": "2026-01-10T00:00:00+00:00",\n     '
    ' "parameter": "discharge",\n      "value": 20.0,\n      "source": "'
    'manual_import",\n      "rating_curve_id": null,\n      "rating_curv'
    'e_correction_version": null,\n      "qc_status": "qc_passed",\n    '
    '  "qc_flags": [\n        {\n          "rule_id": "range_check",\n   '
    '       "rule_version": "1.2",\n          "status": "qc_passed",\n  '
    '        "detail": null\n        }\n      ],\n      "qc_rule_version"'
    ': "1.2",\n      "created_at": "2026-01-20T00:00:00+00:00",\n      "'
    'delivery_id": "dhm-barkhk-2026-09-08"\n    },\n    {\n      "id": "0'
    '0000000-0000-0000-0000-00000001ade4",\n      "station_id": "000000'
    '00-0000-0000-0000-00000001637e",\n      "timestamp": "2026-01-11T0'
    '0:00:00+00:00",\n      "parameter": "discharge",\n      "value": 21'
    '.0,\n      "source": "manual_import",\n      "rating_curve_id": nul'
    'l,\n      "rating_curve_correction_version": null,\n      "qc_statu'
    's": "qc_passed",\n      "qc_flags": [\n        {\n          "rule_id'
    '": "range_check",\n          "rule_version": "1.2",\n          "sta'
    'tus": "qc_passed",\n          "detail": null\n        }\n      ],\n  '
    '    "qc_rule_version": "1.2",\n      "created_at": "2026-01-20T00:'
    '00:00+00:00",\n      "delivery_id": "dhm-barkhk-2026-09-08"\n    },'
    '\n    {\n      "id": "00000000-0000-0000-0000-00000001ade5",\n      '
    '"station_id": "00000000-0000-0000-0000-00000001637e",\n      "time'
    'stamp": "2026-01-12T00:00:00+00:00",\n      "parameter": "discharg'
    'e",\n      "value": 22.0,\n      "source": "manual_import",\n      "'
    'rating_curve_id": null,\n      "rating_curve_correction_version": '
    'null,\n      "qc_status": "qc_passed",\n      "qc_flags": [\n       '
    ' {\n          "rule_id": "range_check",\n          "rule_version": '
    '"1.2",\n          "status": "qc_passed",\n          "detail": null\n'
    '        }\n      ],\n      "qc_rule_version": "1.2",\n      "created'
    '_at": "2026-01-20T00:00:00+00:00",\n      "delivery_id": "dhm-bark'
    'hk-2026-09-08"\n    },\n    {\n      "id": "00000000-0000-0000-0000-'
    '00000001ade6",\n      "station_id": "00000000-0000-0000-0000-00000'
    '001637e",\n      "timestamp": "2026-01-13T00:00:00+00:00",\n      "'
    'parameter": "discharge",\n      "value": 23.0,\n      "source": "ma'
    'nual_import",\n      "rating_curve_id": null,\n      "rating_curve_'
    'correction_version": null,\n      "qc_status": "qc_passed",\n      '
    '"qc_flags": [\n        {\n          "rule_id": "range_check",\n     '
    '     "rule_version": "1.2",\n          "status": "qc_passed",\n    '
    '      "detail": null\n        }\n      ],\n      "qc_rule_version": '
    '"1.2",\n      "created_at": "2026-01-20T00:00:00+00:00",\n      "de'
    'livery_id": "dhm-barkhk-2026-09-08"\n    },\n    {\n      "id": "000'
    '00000-0000-0000-0000-00000001ade7",\n      "station_id": "00000000'
    '-0000-0000-0000-00000001637e",\n      "timestamp": "2026-01-14T00:'
    '00:00+00:00",\n      "parameter": "discharge",\n      "value": 24.0'
    ',\n      "source": "manual_import",\n      "rating_curve_id": null,'
    '\n      "rating_curve_correction_version": null,\n      "qc_status"'
    ': "qc_passed",\n      "qc_flags": [\n        {\n          "rule_id":'
    ' "range_check",\n          "rule_version": "1.2",\n          "statu'
    's": "qc_passed",\n          "detail": null\n        }\n      ],\n    '
    '  "qc_rule_version": "1.2",\n      "created_at": "2026-01-20T00:00'
    ':00+00:00",\n      "delivery_id": "dhm-barkhk-2026-09-08"\n    },\n '
    '   {\n      "id": "00000000-0000-0000-0000-00000001ade8",\n      "s'
    'tation_id": "00000000-0000-0000-0000-00000001637e",\n      "timest'
    'amp": "2026-01-15T00:00:00+00:00",\n      "parameter": "discharge"'
    ',\n      "value": 25.0,\n      "source": "manual_import",\n      "ra'
    'ting_curve_id": null,\n      "rating_curve_correction_version": nu'
    'll,\n      "qc_status": "qc_passed",\n      "qc_flags": [\n        {'
    '\n          "rule_id": "range_check",\n          "rule_version": "1'
    '.2",\n          "status": "qc_passed",\n          "detail": null\n  '
    '      }\n      ],\n      "qc_rule_version": "1.2",\n      "created_a'
    't": "2026-01-20T00:00:00+00:00",\n      "delivery_id": "dhm-barkhk'
    '-2026-09-08"\n    },\n    {\n      "id": "00000000-0000-0000-0000-00'
    '000001ade9",\n      "station_id": "00000000-0000-0000-0000-0000000'
    '1637e",\n      "timestamp": "2026-01-16T00:00:00+00:00",\n      "pa'
    'rameter": "discharge",\n      "value": 26.0,\n      "source": "manu'
    'al_import",\n      "rating_curve_id": null,\n      "rating_curve_co'
    'rrection_version": null,\n      "qc_status": "qc_passed",\n      "q'
    'c_flags": [\n        {\n          "rule_id": "range_check",\n       '
    '   "rule_version": "1.2",\n          "status": "qc_passed",\n      '
    '    "detail": null\n        }\n      ],\n      "qc_rule_version": "1'
    '.2",\n      "created_at": "2026-01-20T00:00:00+00:00",\n      "deli'
    'very_id": "dhm-barkhk-2026-09-08"\n    },\n    {\n      "id": "00000'
    '000-0000-0000-0000-00000001adea",\n      "station_id": "00000000-0'
    '000-0000-0000-00000001637e",\n      "timestamp": "2026-01-17T00:00'
    ':00+00:00",\n      "parameter": "discharge",\n      "value": 27.0,\n'
    '      "source": "manual_import",\n      "rating_curve_id": null,\n '
    '     "rating_curve_correction_version": null,\n      "qc_status": '
    '"qc_passed",\n      "qc_flags": [\n        {\n          "rule_id": "'
    'range_check",\n          "rule_version": "1.2",\n          "status"'
    ': "qc_passed",\n          "detail": null\n        }\n      ],\n      '
    '"qc_rule_version": "1.2",\n      "created_at": "2026-01-20T00:00:0'
    '0+00:00",\n      "delivery_id": "dhm-barkhk-2026-09-08"\n    },\n   '
    ' {\n      "id": "00000000-0000-0000-0000-00000001adeb",\n      "sta'
    'tion_id": "00000000-0000-0000-0000-00000001637e",\n      "timestam'
    'p": "2026-01-18T00:00:00+00:00",\n      "parameter": "discharge",\n'
    '      "value": 28.0,\n      "source": "manual_import",\n      "rati'
    'ng_curve_id": null,\n      "rating_curve_correction_version": null'
    ',\n      "qc_status": "qc_passed",\n      "qc_flags": [\n        {\n '
    '         "rule_id": "range_check",\n          "rule_version": "1.2'
    '",\n          "status": "qc_passed",\n          "detail": null\n    '
    '    }\n      ],\n      "qc_rule_version": "1.2",\n      "created_at"'
    ': "2026-01-20T00:00:00+00:00",\n      "delivery_id": "dhm-barkhk-2'
    '026-09-08"\n    },\n    {\n      "id": "00000000-0000-0000-0000-0000'
    '0001adec",\n      "station_id": "00000000-0000-0000-0000-000000016'
    '37e",\n      "timestamp": "2026-01-19T00:00:00+00:00",\n      "para'
    'meter": "discharge",\n      "value": 29.0,\n      "source": "manual'
    '_import",\n      "rating_curve_id": null,\n      "rating_curve_corr'
    'ection_version": null,\n      "qc_status": "qc_passed",\n      "qc_'
    'flags": [\n        {\n          "rule_id": "range_check",\n         '
    ' "rule_version": "1.2",\n          "status": "qc_passed",\n        '
    '  "detail": null\n        }\n      ],\n      "qc_rule_version": "1.2'
    '",\n      "created_at": "2026-01-20T00:00:00+00:00",\n      "delive'
    'ry_id": "dhm-barkhk-2026-09-08"\n    }\n  ],\n  "forcing": [\n    {\n '
    '     "station_id": "00000000-0000-0000-0000-000000016379",\n      '
    '"source": "recap_era5_land_reanalysis",\n      "version": "synthet'
    'ic-v1",\n      "valid_time": "2026-01-10T00:00:00+00:00",\n      "p'
    'arameter": "precipitation",\n      "spatial_type": "basin_average"'
    ',\n      "band_id": null,\n      "member_id": null,\n      "value": '
    '10.0\n    },\n    {\n      "station_id": "00000000-0000-0000-0000-00'
    '0000016379",\n      "source": "recap_era5_land_reanalysis",\n      '
    '"version": "synthetic-v1",\n      "valid_time": "2026-01-10T00:00:'
    '00+00:00",\n      "parameter": "temperature",\n      "spatial_type"'
    ': "basin_average",\n      "band_id": null,\n      "member_id": null'
    ',\n      "value": 10.0\n    },\n    {\n      "station_id": "00000000-'
    '0000-0000-0000-000000016379",\n      "source": "recap_era5_land_re'
    'analysis",\n      "version": "synthetic-v1",\n      "valid_time": "'
    '2026-01-11T00:00:00+00:00",\n      "parameter": "precipitation",\n '
    '     "spatial_type": "basin_average",\n      "band_id": null,\n    '
    '  "member_id": null,\n      "value": 10.0\n    },\n    {\n      "stat'
    'ion_id": "00000000-0000-0000-0000-000000016379",\n      "source": '
    '"recap_era5_land_reanalysis",\n      "version": "synthetic-v1",\n  '
    '    "valid_time": "2026-01-11T00:00:00+00:00",\n      "parameter":'
    ' "temperature",\n      "spatial_type": "basin_average",\n      "ban'
    'd_id": null,\n      "member_id": null,\n      "value": 10.0\n    },\n'
    '    {\n      "station_id": "00000000-0000-0000-0000-000000016379",'
    '\n      "source": "recap_era5_land_reanalysis",\n      "version": "'
    'synthetic-v1",\n      "valid_time": "2026-01-12T00:00:00+00:00",\n '
    '     "parameter": "precipitation",\n      "spatial_type": "basin_a'
    'verage",\n      "band_id": null,\n      "member_id": null,\n      "v'
    'alue": 10.0\n    },\n    {\n      "station_id": "00000000-0000-0000-'
    '0000-000000016379",\n      "source": "recap_era5_land_reanalysis",'
    '\n      "version": "synthetic-v1",\n      "valid_time": "2026-01-12'
    'T00:00:00+00:00",\n      "parameter": "temperature",\n      "spatia'
    'l_type": "basin_average",\n      "band_id": null,\n      "member_id'
    '": null,\n      "value": 10.0\n    },\n    {\n      "station_id": "00'
    '000000-0000-0000-0000-000000016379",\n      "source": "recap_era5_'
    'land_reanalysis",\n      "version": "synthetic-v1",\n      "valid_t'
    'ime": "2026-01-13T00:00:00+00:00",\n      "parameter": "precipitat'
    'ion",\n      "spatial_type": "basin_average",\n      "band_id": nul'
    'l,\n      "member_id": null,\n      "value": 10.0\n    },\n    {\n    '
    '  "station_id": "00000000-0000-0000-0000-000000016379",\n      "so'
    'urce": "recap_era5_land_reanalysis",\n      "version": "synthetic-'
    'v1",\n      "valid_time": "2026-01-13T00:00:00+00:00",\n      "para'
    'meter": "temperature",\n      "spatial_type": "basin_average",\n   '
    '   "band_id": null,\n      "member_id": null,\n      "value": 10.0\n'
    '    },\n    {\n      "station_id": "00000000-0000-0000-0000-0000000'
    '16379",\n      "source": "recap_era5_land_reanalysis",\n      "vers'
    'ion": "synthetic-v1",\n      "valid_time": "2026-01-14T00:00:00+00'
    ':00",\n      "parameter": "precipitation",\n      "spatial_type": "'
    'basin_average",\n      "band_id": null,\n      "member_id": null,\n '
    '     "value": 10.0\n    },\n    {\n      "station_id": "00000000-000'
    '0-0000-0000-000000016379",\n      "source": "recap_era5_land_reana'
    'lysis",\n      "version": "synthetic-v1",\n      "valid_time": "202'
    '6-01-14T00:00:00+00:00",\n      "parameter": "temperature",\n      '
    '"spatial_type": "basin_average",\n      "band_id": null,\n      "me'
    'mber_id": null,\n      "value": 10.0\n    },\n    {\n      "station_i'
    'd": "00000000-0000-0000-0000-000000016379",\n      "source": "reca'
    'p_era5_land_reanalysis",\n      "version": "synthetic-v1",\n      "'
    'valid_time": "2026-01-15T00:00:00+00:00",\n      "parameter": "pre'
    'cipitation",\n      "spatial_type": "basin_average",\n      "band_i'
    'd": null,\n      "member_id": null,\n      "value": 10.0\n    },\n   '
    ' {\n      "station_id": "00000000-0000-0000-0000-000000016379",\n  '
    '    "source": "recap_era5_land_reanalysis",\n      "version": "syn'
    'thetic-v1",\n      "valid_time": "2026-01-15T00:00:00+00:00",\n    '
    '  "parameter": "temperature",\n      "spatial_type": "basin_averag'
    'e",\n      "band_id": null,\n      "member_id": null,\n      "value"'
    ': 10.0\n    },\n    {\n      "station_id": "00000000-0000-0000-0000-'
    '000000016379",\n      "source": "recap_era5_land_reanalysis",\n    '
    '  "version": "synthetic-v1",\n      "valid_time": "2026-01-16T00:0'
    '0:00+00:00",\n      "parameter": "precipitation",\n      "spatial_t'
    'ype": "basin_average",\n      "band_id": null,\n      "member_id": '
    'null,\n      "value": 10.0\n    },\n    {\n      "station_id": "00000'
    '000-0000-0000-0000-000000016379",\n      "source": "recap_era5_lan'
    'd_reanalysis",\n      "version": "synthetic-v1",\n      "valid_time'
    '": "2026-01-16T00:00:00+00:00",\n      "parameter": "temperature",'
    '\n      "spatial_type": "basin_average",\n      "band_id": null,\n  '
    '    "member_id": null,\n      "value": 10.0\n    },\n    {\n      "st'
    'ation_id": "00000000-0000-0000-0000-000000016379",\n      "source"'
    ': "recap_era5_land_reanalysis",\n      "version": "synthetic-v1",\n'
    '      "valid_time": "2026-01-17T00:00:00+00:00",\n      "parameter'
    '": "precipitation",\n      "spatial_type": "basin_average",\n      '
    '"band_id": null,\n      "member_id": null,\n      "value": 10.0\n   '
    ' },\n    {\n      "station_id": "00000000-0000-0000-0000-0000000163'
    '79",\n      "source": "recap_era5_land_reanalysis",\n      "version'
    '": "synthetic-v1",\n      "valid_time": "2026-01-17T00:00:00+00:00'
    '",\n      "parameter": "temperature",\n      "spatial_type": "basin'
    '_average",\n      "band_id": null,\n      "member_id": null,\n      '
    '"value": 10.0\n    },\n    {\n      "station_id": "00000000-0000-000'
    '0-0000-000000016379",\n      "source": "recap_era5_land_reanalysis'
    '",\n      "version": "synthetic-v1",\n      "valid_time": "2026-01-'
    '18T00:00:00+00:00",\n      "parameter": "precipitation",\n      "sp'
    'atial_type": "basin_average",\n      "band_id": null,\n      "membe'
    'r_id": null,\n      "value": 10.0\n    },\n    {\n      "station_id":'
    ' "00000000-0000-0000-0000-000000016379",\n      "source": "recap_e'
    'ra5_land_reanalysis",\n      "version": "synthetic-v1",\n      "val'
    'id_time": "2026-01-18T00:00:00+00:00",\n      "parameter": "temper'
    'ature",\n      "spatial_type": "basin_average",\n      "band_id": n'
    'ull,\n      "member_id": null,\n      "value": 10.0\n    },\n    {\n  '
    '    "station_id": "00000000-0000-0000-0000-000000016379",\n      "'
    'source": "recap_era5_land_reanalysis",\n      "version": "syntheti'
    'c-v1",\n      "valid_time": "2026-01-19T00:00:00+00:00",\n      "pa'
    'rameter": "precipitation",\n      "spatial_type": "basin_average",'
    '\n      "band_id": null,\n      "member_id": null,\n      "value": 1'
    '0.0\n    },\n    {\n      "station_id": "00000000-0000-0000-0000-000'
    '000016379",\n      "source": "recap_era5_land_reanalysis",\n      "'
    'version": "synthetic-v1",\n      "valid_time": "2026-01-19T00:00:0'
    '0+00:00",\n      "parameter": "temperature",\n      "spatial_type":'
    ' "basin_average",\n      "band_id": null,\n      "member_id": null,'
    '\n      "value": 10.0\n    },\n    {\n      "station_id": "00000000-0'
    '000-0000-0000-00000001637a",\n      "source": "recap_era5_land_rea'
    'nalysis",\n      "version": "synthetic-v1",\n      "valid_time": "2'
    '026-01-10T00:00:00+00:00",\n      "parameter": "precipitation",\n  '
    '    "spatial_type": "basin_average",\n      "band_id": null,\n     '
    ' "member_id": null,\n      "value": 10.0\n    },\n    {\n      "stati'
    'on_id": "00000000-0000-0000-0000-00000001637a",\n      "source": "'
    'recap_era5_land_reanalysis",\n      "version": "synthetic-v1",\n   '
    '   "valid_time": "2026-01-10T00:00:00+00:00",\n      "parameter": '
    '"temperature",\n      "spatial_type": "basin_average",\n      "band'
    '_id": null,\n      "member_id": null,\n      "value": 10.0\n    },\n '
    '   {\n      "station_id": "00000000-0000-0000-0000-00000001637a",\n'
    '      "source": "recap_era5_land_reanalysis",\n      "version": "s'
    'ynthetic-v1",\n      "valid_time": "2026-01-11T00:00:00+00:00",\n  '
    '    "parameter": "precipitation",\n      "spatial_type": "basin_av'
    'erage",\n      "band_id": null,\n      "member_id": null,\n      "va'
    'lue": 10.0\n    },\n    {\n      "station_id": "00000000-0000-0000-0'
    '000-00000001637a",\n      "source": "recap_era5_land_reanalysis",\n'
    '      "version": "synthetic-v1",\n      "valid_time": "2026-01-11T'
    '00:00:00+00:00",\n      "parameter": "temperature",\n      "spatial'
    '_type": "basin_average",\n      "band_id": null,\n      "member_id"'
    ': null,\n      "value": 10.0\n    },\n    {\n      "station_id": "000'
    '00000-0000-0000-0000-00000001637a",\n      "source": "recap_era5_l'
    'and_reanalysis",\n      "version": "synthetic-v1",\n      "valid_ti'
    'me": "2026-01-12T00:00:00+00:00",\n      "parameter": "precipitati'
    'on",\n      "spatial_type": "basin_average",\n      "band_id": null'
    ',\n      "member_id": null,\n      "value": 10.0\n    },\n    {\n     '
    ' "station_id": "00000000-0000-0000-0000-00000001637a",\n      "sou'
    'rce": "recap_era5_land_reanalysis",\n      "version": "synthetic-v'
    '1",\n      "valid_time": "2026-01-12T00:00:00+00:00",\n      "param'
    'eter": "temperature",\n      "spatial_type": "basin_average",\n    '
    '  "band_id": null,\n      "member_id": null,\n      "value": 10.0\n '
    '   },\n    {\n      "station_id": "00000000-0000-0000-0000-00000001'
    '637a",\n      "source": "recap_era5_land_reanalysis",\n      "versi'
    'on": "synthetic-v1",\n      "valid_time": "2026-01-13T00:00:00+00:'
    '00",\n      "parameter": "precipitation",\n      "spatial_type": "b'
    'asin_average",\n      "band_id": null,\n      "member_id": null,\n  '
    '    "value": 10.0\n    },\n    {\n      "station_id": "00000000-0000'
    '-0000-0000-00000001637a",\n      "source": "recap_era5_land_reanal'
    'ysis",\n      "version": "synthetic-v1",\n      "valid_time": "2026'
    '-01-13T00:00:00+00:00",\n      "parameter": "temperature",\n      "'
    'spatial_type": "basin_average",\n      "band_id": null,\n      "mem'
    'ber_id": null,\n      "value": 10.0\n    },\n    {\n      "station_id'
    '": "00000000-0000-0000-0000-00000001637a",\n      "source": "recap'
    '_era5_land_reanalysis",\n      "version": "synthetic-v1",\n      "v'
    'alid_time": "2026-01-14T00:00:00+00:00",\n      "parameter": "prec'
    'ipitation",\n      "spatial_type": "basin_average",\n      "band_id'
    '": null,\n      "member_id": null,\n      "value": 10.0\n    },\n    '
    '{\n      "station_id": "00000000-0000-0000-0000-00000001637a",\n   '
    '   "source": "recap_era5_land_reanalysis",\n      "version": "synt'
    'hetic-v1",\n      "valid_time": "2026-01-14T00:00:00+00:00",\n     '
    ' "parameter": "temperature",\n      "spatial_type": "basin_average'
    '",\n      "band_id": null,\n      "member_id": null,\n      "value":'
    ' 10.0\n    },\n    {\n      "station_id": "00000000-0000-0000-0000-0'
    '0000001637a",\n      "source": "recap_era5_land_reanalysis",\n     '
    ' "version": "synthetic-v1",\n      "valid_time": "2026-01-15T00:00'
    ':00+00:00",\n      "parameter": "precipitation",\n      "spatial_ty'
    'pe": "basin_average",\n      "band_id": null,\n      "member_id": n'
    'ull,\n      "value": 10.0\n    },\n    {\n      "station_id": "000000'
    '00-0000-0000-0000-00000001637a",\n      "source": "recap_era5_land'
    '_reanalysis",\n      "version": "synthetic-v1",\n      "valid_time"'
    ': "2026-01-15T00:00:00+00:00",\n      "parameter": "temperature",\n'
    '      "spatial_type": "basin_average",\n      "band_id": null,\n   '
    '   "member_id": null,\n      "value": 10.0\n    },\n    {\n      "sta'
    'tion_id": "00000000-0000-0000-0000-00000001637a",\n      "source":'
    ' "recap_era5_land_reanalysis",\n      "version": "synthetic-v1",\n '
    '     "valid_time": "2026-01-16T00:00:00+00:00",\n      "parameter"'
    ': "precipitation",\n      "spatial_type": "basin_average",\n      "'
    'band_id": null,\n      "member_id": null,\n      "value": 10.0\n    '
    '},\n    {\n      "station_id": "00000000-0000-0000-0000-00000001637'
    'a",\n      "source": "recap_era5_land_reanalysis",\n      "version"'
    ': "synthetic-v1",\n      "valid_time": "2026-01-16T00:00:00+00:00"'
    ',\n      "parameter": "temperature",\n      "spatial_type": "basin_'
    'average",\n      "band_id": null,\n      "member_id": null,\n      "'
    'value": 10.0\n    },\n    {\n      "station_id": "00000000-0000-0000'
    '-0000-00000001637a",\n      "source": "recap_era5_land_reanalysis"'
    ',\n      "version": "synthetic-v1",\n      "valid_time": "2026-01-1'
    '7T00:00:00+00:00",\n      "parameter": "precipitation",\n      "spa'
    'tial_type": "basin_average",\n      "band_id": null,\n      "member'
    '_id": null,\n      "value": 10.0\n    },\n    {\n      "station_id": '
    '"00000000-0000-0000-0000-00000001637a",\n      "source": "recap_er'
    'a5_land_reanalysis",\n      "version": "synthetic-v1",\n      "vali'
    'd_time": "2026-01-17T00:00:00+00:00",\n      "parameter": "tempera'
    'ture",\n      "spatial_type": "basin_average",\n      "band_id": nu'
    'll,\n      "member_id": null,\n      "value": 10.0\n    },\n    {\n   '
    '   "station_id": "00000000-0000-0000-0000-00000001637a",\n      "s'
    'ource": "recap_era5_land_reanalysis",\n      "version": "synthetic'
    '-v1",\n      "valid_time": "2026-01-18T00:00:00+00:00",\n      "par'
    'ameter": "precipitation",\n      "spatial_type": "basin_average",\n'
    '      "band_id": null,\n      "member_id": null,\n      "value": 10'
    '.0\n    },\n    {\n      "station_id": "00000000-0000-0000-0000-0000'
    '0001637a",\n      "source": "recap_era5_land_reanalysis",\n      "v'
    'ersion": "synthetic-v1",\n      "valid_time": "2026-01-18T00:00:00'
    '+00:00",\n      "parameter": "temperature",\n      "spatial_type": '
    '"basin_average",\n      "band_id": null,\n      "member_id": null,\n'
    '      "value": 10.0\n    },\n    {\n      "station_id": "00000000-00'
    '00-0000-0000-00000001637a",\n      "source": "recap_era5_land_rean'
    'alysis",\n      "version": "synthetic-v1",\n      "valid_time": "20'
    '26-01-19T00:00:00+00:00",\n      "parameter": "precipitation",\n   '
    '   "spatial_type": "basin_average",\n      "band_id": null,\n      '
    '"member_id": null,\n      "value": 10.0\n    },\n    {\n      "statio'
    'n_id": "00000000-0000-0000-0000-00000001637a",\n      "source": "r'
    'ecap_era5_land_reanalysis",\n      "version": "synthetic-v1",\n    '
    '  "valid_time": "2026-01-19T00:00:00+00:00",\n      "parameter": "'
    'temperature",\n      "spatial_type": "basin_average",\n      "band_'
    'id": null,\n      "member_id": null,\n      "value": 10.0\n    },\n  '
    '  {\n      "station_id": "00000000-0000-0000-0000-00000001637b",\n '
    '     "source": "recap_era5_land_reanalysis",\n      "version": "sy'
    'nthetic-v1",\n      "valid_time": "2026-01-10T00:00:00+00:00",\n   '
    '   "parameter": "precipitation",\n      "spatial_type": "basin_ave'
    'rage",\n      "band_id": null,\n      "member_id": null,\n      "val'
    'ue": 10.0\n    },\n    {\n      "station_id": "00000000-0000-0000-00'
    '00-00000001637b",\n      "source": "recap_era5_land_reanalysis",\n '
    '     "version": "synthetic-v1",\n      "valid_time": "2026-01-10T0'
    '0:00:00+00:00",\n      "parameter": "temperature",\n      "spatial_'
    'type": "basin_average",\n      "band_id": null,\n      "member_id":'
    ' null,\n      "value": 10.0\n    },\n    {\n      "station_id": "0000'
    '0000-0000-0000-0000-00000001637b",\n      "source": "recap_era5_la'
    'nd_reanalysis",\n      "version": "synthetic-v1",\n      "valid_tim'
    'e": "2026-01-11T00:00:00+00:00",\n      "parameter": "precipitatio'
    'n",\n      "spatial_type": "basin_average",\n      "band_id": null,'
    '\n      "member_id": null,\n      "value": 10.0\n    },\n    {\n      '
    '"station_id": "00000000-0000-0000-0000-00000001637b",\n      "sour'
    'ce": "recap_era5_land_reanalysis",\n      "version": "synthetic-v1'
    '",\n      "valid_time": "2026-01-11T00:00:00+00:00",\n      "parame'
    'ter": "temperature",\n      "spatial_type": "basin_average",\n     '
    ' "band_id": null,\n      "member_id": null,\n      "value": 10.0\n  '
    '  },\n    {\n      "station_id": "00000000-0000-0000-0000-000000016'
    '37b",\n      "source": "recap_era5_land_reanalysis",\n      "versio'
    'n": "synthetic-v1",\n      "valid_time": "2026-01-12T00:00:00+00:0'
    '0",\n      "parameter": "precipitation",\n      "spatial_type": "ba'
    'sin_average",\n      "band_id": null,\n      "member_id": null,\n   '
    '   "value": 10.0\n    },\n    {\n      "station_id": "00000000-0000-'
    '0000-0000-00000001637b",\n      "source": "recap_era5_land_reanaly'
    'sis",\n      "version": "synthetic-v1",\n      "valid_time": "2026-'
    '01-12T00:00:00+00:00",\n      "parameter": "temperature",\n      "s'
    'patial_type": "basin_average",\n      "band_id": null,\n      "memb'
    'er_id": null,\n      "value": 10.0\n    },\n    {\n      "station_id"'
    ': "00000000-0000-0000-0000-00000001637b",\n      "source": "recap_'
    'era5_land_reanalysis",\n      "version": "synthetic-v1",\n      "va'
    'lid_time": "2026-01-13T00:00:00+00:00",\n      "parameter": "preci'
    'pitation",\n      "spatial_type": "basin_average",\n      "band_id"'
    ': null,\n      "member_id": null,\n      "value": 10.0\n    },\n    {'
    '\n      "station_id": "00000000-0000-0000-0000-00000001637b",\n    '
    '  "source": "recap_era5_land_reanalysis",\n      "version": "synth'
    'etic-v1",\n      "valid_time": "2026-01-13T00:00:00+00:00",\n      '
    '"parameter": "temperature",\n      "spatial_type": "basin_average"'
    ',\n      "band_id": null,\n      "member_id": null,\n      "value": '
    '10.0\n    },\n    {\n      "station_id": "00000000-0000-0000-0000-00'
    '000001637b",\n      "source": "recap_era5_land_reanalysis",\n      '
    '"version": "synthetic-v1",\n      "valid_time": "2026-01-14T00:00:'
    '00+00:00",\n      "parameter": "precipitation",\n      "spatial_typ'
    'e": "basin_average",\n      "band_id": null,\n      "member_id": nu'
    'll,\n      "value": 10.0\n    },\n    {\n      "station_id": "0000000'
    '0-0000-0000-0000-00000001637b",\n      "source": "recap_era5_land_'
    'reanalysis",\n      "version": "synthetic-v1",\n      "valid_time":'
    ' "2026-01-14T00:00:00+00:00",\n      "parameter": "temperature",\n '
    '     "spatial_type": "basin_average",\n      "band_id": null,\n    '
    '  "member_id": null,\n      "value": 10.0\n    },\n    {\n      "stat'
    'ion_id": "00000000-0000-0000-0000-00000001637b",\n      "source": '
    '"recap_era5_land_reanalysis",\n      "version": "synthetic-v1",\n  '
    '    "valid_time": "2026-01-15T00:00:00+00:00",\n      "parameter":'
    ' "precipitation",\n      "spatial_type": "basin_average",\n      "b'
    'and_id": null,\n      "member_id": null,\n      "value": 10.0\n    }'
    ',\n    {\n      "station_id": "00000000-0000-0000-0000-00000001637b'
    '",\n      "source": "recap_era5_land_reanalysis",\n      "version":'
    ' "synthetic-v1",\n      "valid_time": "2026-01-15T00:00:00+00:00",'
    '\n      "parameter": "temperature",\n      "spatial_type": "basin_a'
    'verage",\n      "band_id": null,\n      "member_id": null,\n      "v'
    'alue": 10.0\n    },\n    {\n      "station_id": "00000000-0000-0000-'
    '0000-00000001637b",\n      "source": "recap_era5_land_reanalysis",'
    '\n      "version": "synthetic-v1",\n      "valid_time": "2026-01-16'
    'T00:00:00+00:00",\n      "parameter": "precipitation",\n      "spat'
    'ial_type": "basin_average",\n      "band_id": null,\n      "member_'
    'id": null,\n      "value": 10.0\n    },\n    {\n      "station_id": "'
    '00000000-0000-0000-0000-00000001637b",\n      "source": "recap_era'
    '5_land_reanalysis",\n      "version": "synthetic-v1",\n      "valid'
    '_time": "2026-01-16T00:00:00+00:00",\n      "parameter": "temperat'
    'ure",\n      "spatial_type": "basin_average",\n      "band_id": nul'
    'l,\n      "member_id": null,\n      "value": 10.0\n    },\n    {\n    '
    '  "station_id": "00000000-0000-0000-0000-00000001637b",\n      "so'
    'urce": "recap_era5_land_reanalysis",\n      "version": "synthetic-'
    'v1",\n      "valid_time": "2026-01-17T00:00:00+00:00",\n      "para'
    'meter": "precipitation",\n      "spatial_type": "basin_average",\n '
    '     "band_id": null,\n      "member_id": null,\n      "value": 10.'
    '0\n    },\n    {\n      "station_id": "00000000-0000-0000-0000-00000'
    '001637b",\n      "source": "recap_era5_land_reanalysis",\n      "ve'
    'rsion": "synthetic-v1",\n      "valid_time": "2026-01-17T00:00:00+'
    '00:00",\n      "parameter": "temperature",\n      "spatial_type": "'
    'basin_average",\n      "band_id": null,\n      "member_id": null,\n '
    '     "value": 10.0\n    },\n    {\n      "station_id": "00000000-000'
    '0-0000-0000-00000001637b",\n      "source": "recap_era5_land_reana'
    'lysis",\n      "version": "synthetic-v1",\n      "valid_time": "202'
    '6-01-18T00:00:00+00:00",\n      "parameter": "precipitation",\n    '
    '  "spatial_type": "basin_average",\n      "band_id": null,\n      "'
    'member_id": null,\n      "value": 10.0\n    },\n    {\n      "station'
    '_id": "00000000-0000-0000-0000-00000001637b",\n      "source": "re'
    'cap_era5_land_reanalysis",\n      "version": "synthetic-v1",\n     '
    ' "valid_time": "2026-01-18T00:00:00+00:00",\n      "parameter": "t'
    'emperature",\n      "spatial_type": "basin_average",\n      "band_i'
    'd": null,\n      "member_id": null,\n      "value": 10.0\n    },\n   '
    ' {\n      "station_id": "00000000-0000-0000-0000-00000001637b",\n  '
    '    "source": "recap_era5_land_reanalysis",\n      "version": "syn'
    'thetic-v1",\n      "valid_time": "2026-01-19T00:00:00+00:00",\n    '
    '  "parameter": "precipitation",\n      "spatial_type": "basin_aver'
    'age",\n      "band_id": null,\n      "member_id": null,\n      "valu'
    'e": 10.0\n    },\n    {\n      "station_id": "00000000-0000-0000-000'
    '0-00000001637b",\n      "source": "recap_era5_land_reanalysis",\n  '
    '    "version": "synthetic-v1",\n      "valid_time": "2026-01-19T00'
    ':00:00+00:00",\n      "parameter": "temperature",\n      "spatial_t'
    'ype": "basin_average",\n      "band_id": null,\n      "member_id": '
    'null,\n      "value": 10.0\n    },\n    {\n      "station_id": "00000'
    '000-0000-0000-0000-00000001637c",\n      "source": "recap_era5_lan'
    'd_reanalysis",\n      "version": "synthetic-v1",\n      "valid_time'
    '": "2026-01-10T00:00:00+00:00",\n      "parameter": "precipitation'
    '",\n      "spatial_type": "basin_average",\n      "band_id": null,\n'
    '      "member_id": null,\n      "value": 10.0\n    },\n    {\n      "'
    'station_id": "00000000-0000-0000-0000-00000001637c",\n      "sourc'
    'e": "recap_era5_land_reanalysis",\n      "version": "synthetic-v1"'
    ',\n      "valid_time": "2026-01-10T00:00:00+00:00",\n      "paramet'
    'er": "temperature",\n      "spatial_type": "basin_average",\n      '
    '"band_id": null,\n      "member_id": null,\n      "value": 10.0\n   '
    ' },\n    {\n      "station_id": "00000000-0000-0000-0000-0000000163'
    '7c",\n      "source": "recap_era5_land_reanalysis",\n      "version'
    '": "synthetic-v1",\n      "valid_time": "2026-01-11T00:00:00+00:00'
    '",\n      "parameter": "precipitation",\n      "spatial_type": "bas'
    'in_average",\n      "band_id": null,\n      "member_id": null,\n    '
    '  "value": 10.0\n    },\n    {\n      "station_id": "00000000-0000-0'
    '000-0000-00000001637c",\n      "source": "recap_era5_land_reanalys'
    'is",\n      "version": "synthetic-v1",\n      "valid_time": "2026-0'
    '1-11T00:00:00+00:00",\n      "parameter": "temperature",\n      "sp'
    'atial_type": "basin_average",\n      "band_id": null,\n      "membe'
    'r_id": null,\n      "value": 10.0\n    },\n    {\n      "station_id":'
    ' "00000000-0000-0000-0000-00000001637c",\n      "source": "recap_e'
    'ra5_land_reanalysis",\n      "version": "synthetic-v1",\n      "val'
    'id_time": "2026-01-12T00:00:00+00:00",\n      "parameter": "precip'
    'itation",\n      "spatial_type": "basin_average",\n      "band_id":'
    ' null,\n      "member_id": null,\n      "value": 10.0\n    },\n    {\n'
    '      "station_id": "00000000-0000-0000-0000-00000001637c",\n     '
    ' "source": "recap_era5_land_reanalysis",\n      "version": "synthe'
    'tic-v1",\n      "valid_time": "2026-01-12T00:00:00+00:00",\n      "'
    'parameter": "temperature",\n      "spatial_type": "basin_average",'
    '\n      "band_id": null,\n      "member_id": null,\n      "value": 1'
    '0.0\n    },\n    {\n      "station_id": "00000000-0000-0000-0000-000'
    '00001637c",\n      "source": "recap_era5_land_reanalysis",\n      "'
    'version": "synthetic-v1",\n      "valid_time": "2026-01-13T00:00:0'
    '0+00:00",\n      "parameter": "precipitation",\n      "spatial_type'
    '": "basin_average",\n      "band_id": null,\n      "member_id": nul'
    'l,\n      "value": 10.0\n    },\n    {\n      "station_id": "00000000'
    '-0000-0000-0000-00000001637c",\n      "source": "recap_era5_land_r'
    'eanalysis",\n      "version": "synthetic-v1",\n      "valid_time": '
    '"2026-01-13T00:00:00+00:00",\n      "parameter": "temperature",\n  '
    '    "spatial_type": "basin_average",\n      "band_id": null,\n     '
    ' "member_id": null,\n      "value": 10.0\n    },\n    {\n      "stati'
    'on_id": "00000000-0000-0000-0000-00000001637c",\n      "source": "'
    'recap_era5_land_reanalysis",\n      "version": "synthetic-v1",\n   '
    '   "valid_time": "2026-01-14T00:00:00+00:00",\n      "parameter": '
    '"precipitation",\n      "spatial_type": "basin_average",\n      "ba'
    'nd_id": null,\n      "member_id": null,\n      "value": 10.0\n    },'
    '\n    {\n      "station_id": "00000000-0000-0000-0000-00000001637c"'
    ',\n      "source": "recap_era5_land_reanalysis",\n      "version": '
    '"synthetic-v1",\n      "valid_time": "2026-01-14T00:00:00+00:00",\n'
    '      "parameter": "temperature",\n      "spatial_type": "basin_av'
    'erage",\n      "band_id": null,\n      "member_id": null,\n      "va'
    'lue": 10.0\n    },\n    {\n      "station_id": "00000000-0000-0000-0'
    '000-00000001637c",\n      "source": "recap_era5_land_reanalysis",\n'
    '      "version": "synthetic-v1",\n      "valid_time": "2026-01-15T'
    '00:00:00+00:00",\n      "parameter": "precipitation",\n      "spati'
    'al_type": "basin_average",\n      "band_id": null,\n      "member_i'
    'd": null,\n      "value": 10.0\n    },\n    {\n      "station_id": "0'
    '0000000-0000-0000-0000-00000001637c",\n      "source": "recap_era5'
    '_land_reanalysis",\n      "version": "synthetic-v1",\n      "valid_'
    'time": "2026-01-15T00:00:00+00:00",\n      "parameter": "temperatu'
    're",\n      "spatial_type": "basin_average",\n      "band_id": null'
    ',\n      "member_id": null,\n      "value": 10.0\n    },\n    {\n     '
    ' "station_id": "00000000-0000-0000-0000-00000001637c",\n      "sou'
    'rce": "recap_era5_land_reanalysis",\n      "version": "synthetic-v'
    '1",\n      "valid_time": "2026-01-16T00:00:00+00:00",\n      "param'
    'eter": "precipitation",\n      "spatial_type": "basin_average",\n  '
    '    "band_id": null,\n      "member_id": null,\n      "value": 10.0'
    '\n    },\n    {\n      "station_id": "00000000-0000-0000-0000-000000'
    '01637c",\n      "source": "recap_era5_land_reanalysis",\n      "ver'
    'sion": "synthetic-v1",\n      "valid_time": "2026-01-16T00:00:00+0'
    '0:00",\n      "parameter": "temperature",\n      "spatial_type": "b'
    'asin_average",\n      "band_id": null,\n      "member_id": null,\n  '
    '    "value": 10.0\n    },\n    {\n      "station_id": "00000000-0000'
    '-0000-0000-00000001637c",\n      "source": "recap_era5_land_reanal'
    'ysis",\n      "version": "synthetic-v1",\n      "valid_time": "2026'
    '-01-17T00:00:00+00:00",\n      "parameter": "precipitation",\n     '
    ' "spatial_type": "basin_average",\n      "band_id": null,\n      "m'
    'ember_id": null,\n      "value": 10.0\n    },\n    {\n      "station_'
    'id": "00000000-0000-0000-0000-00000001637c",\n      "source": "rec'
    'ap_era5_land_reanalysis",\n      "version": "synthetic-v1",\n      '
    '"valid_time": "2026-01-17T00:00:00+00:00",\n      "parameter": "te'
    'mperature",\n      "spatial_type": "basin_average",\n      "band_id'
    '": null,\n      "member_id": null,\n      "value": 10.0\n    },\n    '
    '{\n      "station_id": "00000000-0000-0000-0000-00000001637c",\n   '
    '   "source": "recap_era5_land_reanalysis",\n      "version": "synt'
    'hetic-v1",\n      "valid_time": "2026-01-18T00:00:00+00:00",\n     '
    ' "parameter": "precipitation",\n      "spatial_type": "basin_avera'
    'ge",\n      "band_id": null,\n      "member_id": null,\n      "value'
    '": 10.0\n    },\n    {\n      "station_id": "00000000-0000-0000-0000'
    '-00000001637c",\n      "source": "recap_era5_land_reanalysis",\n   '
    '   "version": "synthetic-v1",\n      "valid_time": "2026-01-18T00:'
    '00:00+00:00",\n      "parameter": "temperature",\n      "spatial_ty'
    'pe": "basin_average",\n      "band_id": null,\n      "member_id": n'
    'ull,\n      "value": 10.0\n    },\n    {\n      "station_id": "000000'
    '00-0000-0000-0000-00000001637c",\n      "source": "recap_era5_land'
    '_reanalysis",\n      "version": "synthetic-v1",\n      "valid_time"'
    ': "2026-01-19T00:00:00+00:00",\n      "parameter": "precipitation"'
    ',\n      "spatial_type": "basin_average",\n      "band_id": null,\n '
    '     "member_id": null,\n      "value": 10.0\n    },\n    {\n      "s'
    'tation_id": "00000000-0000-0000-0000-00000001637c",\n      "source'
    '": "recap_era5_land_reanalysis",\n      "version": "synthetic-v1",'
    '\n      "valid_time": "2026-01-19T00:00:00+00:00",\n      "paramete'
    'r": "temperature",\n      "spatial_type": "basin_average",\n      "'
    'band_id": null,\n      "member_id": null,\n      "value": 10.0\n    '
    '},\n    {\n      "station_id": "00000000-0000-0000-0000-00000001637'
    'd",\n      "source": "recap_era5_land_reanalysis",\n      "version"'
    ': "synthetic-v1",\n      "valid_time": "2026-01-10T00:00:00+00:00"'
    ',\n      "parameter": "precipitation",\n      "spatial_type": "basi'
    'n_average",\n      "band_id": null,\n      "member_id": null,\n     '
    ' "value": 10.0\n    },\n    {\n      "station_id": "00000000-0000-00'
    '00-0000-00000001637d",\n      "source": "recap_era5_land_reanalysi'
    's",\n      "version": "synthetic-v1",\n      "valid_time": "2026-01'
    '-10T00:00:00+00:00",\n      "parameter": "temperature",\n      "spa'
    'tial_type": "basin_average",\n      "band_id": null,\n      "member'
    '_id": null,\n      "value": 10.0\n    },\n    {\n      "station_id": '
    '"00000000-0000-0000-0000-00000001637d",\n      "source": "recap_er'
    'a5_land_reanalysis",\n      "version": "synthetic-v1",\n      "vali'
    'd_time": "2026-01-11T00:00:00+00:00",\n      "parameter": "precipi'
    'tation",\n      "spatial_type": "basin_average",\n      "band_id": '
    'null,\n      "member_id": null,\n      "value": 10.0\n    },\n    {\n '
    '     "station_id": "00000000-0000-0000-0000-00000001637d",\n      '
    '"source": "recap_era5_land_reanalysis",\n      "version": "synthet'
    'ic-v1",\n      "valid_time": "2026-01-11T00:00:00+00:00",\n      "p'
    'arameter": "temperature",\n      "spatial_type": "basin_average",\n'
    '      "band_id": null,\n      "member_id": null,\n      "value": 10'
    '.0\n    },\n    {\n      "station_id": "00000000-0000-0000-0000-0000'
    '0001637d",\n      "source": "recap_era5_land_reanalysis",\n      "v'
    'ersion": "synthetic-v1",\n      "valid_time": "2026-01-12T00:00:00'
    '+00:00",\n      "parameter": "precipitation",\n      "spatial_type"'
    ': "basin_average",\n      "band_id": null,\n      "member_id": null'
    ',\n      "value": 10.0\n    },\n    {\n      "station_id": "00000000-'
    '0000-0000-0000-00000001637d",\n      "source": "recap_era5_land_re'
    'analysis",\n      "version": "synthetic-v1",\n      "valid_time": "'
    '2026-01-12T00:00:00+00:00",\n      "parameter": "temperature",\n   '
    '   "spatial_type": "basin_average",\n      "band_id": null,\n      '
    '"member_id": null,\n      "value": 10.0\n    },\n    {\n      "statio'
    'n_id": "00000000-0000-0000-0000-00000001637d",\n      "source": "r'
    'ecap_era5_land_reanalysis",\n      "version": "synthetic-v1",\n    '
    '  "valid_time": "2026-01-13T00:00:00+00:00",\n      "parameter": "'
    'precipitation",\n      "spatial_type": "basin_average",\n      "ban'
    'd_id": null,\n      "member_id": null,\n      "value": 10.0\n    },\n'
    '    {\n      "station_id": "00000000-0000-0000-0000-00000001637d",'
    '\n      "source": "recap_era5_land_reanalysis",\n      "version": "'
    'synthetic-v1",\n      "valid_time": "2026-01-13T00:00:00+00:00",\n '
    '     "parameter": "temperature",\n      "spatial_type": "basin_ave'
    'rage",\n      "band_id": null,\n      "member_id": null,\n      "val'
    'ue": 10.0\n    },\n    {\n      "station_id": "00000000-0000-0000-00'
    '00-00000001637d",\n      "source": "recap_era5_land_reanalysis",\n '
    '     "version": "synthetic-v1",\n      "valid_time": "2026-01-14T0'
    '0:00:00+00:00",\n      "parameter": "precipitation",\n      "spatia'
    'l_type": "basin_average",\n      "band_id": null,\n      "member_id'
    '": null,\n      "value": 10.0\n    },\n    {\n      "station_id": "00'
    '000000-0000-0000-0000-00000001637d",\n      "source": "recap_era5_'
    'land_reanalysis",\n      "version": "synthetic-v1",\n      "valid_t'
    'ime": "2026-01-14T00:00:00+00:00",\n      "parameter": "temperatur'
    'e",\n      "spatial_type": "basin_average",\n      "band_id": null,'
    '\n      "member_id": null,\n      "value": 10.0\n    },\n    {\n      '
    '"station_id": "00000000-0000-0000-0000-00000001637d",\n      "sour'
    'ce": "recap_era5_land_reanalysis",\n      "version": "synthetic-v1'
    '",\n      "valid_time": "2026-01-15T00:00:00+00:00",\n      "parame'
    'ter": "precipitation",\n      "spatial_type": "basin_average",\n   '
    '   "band_id": null,\n      "member_id": null,\n      "value": 10.0\n'
    '    },\n    {\n      "station_id": "00000000-0000-0000-0000-0000000'
    '1637d",\n      "source": "recap_era5_land_reanalysis",\n      "vers'
    'ion": "synthetic-v1",\n      "valid_time": "2026-01-15T00:00:00+00'
    ':00",\n      "parameter": "temperature",\n      "spatial_type": "ba'
    'sin_average",\n      "band_id": null,\n      "member_id": null,\n   '
    '   "value": 10.0\n    },\n    {\n      "station_id": "00000000-0000-'
    '0000-0000-00000001637d",\n      "source": "recap_era5_land_reanaly'
    'sis",\n      "version": "synthetic-v1",\n      "valid_time": "2026-'
    '01-16T00:00:00+00:00",\n      "parameter": "precipitation",\n      '
    '"spatial_type": "basin_average",\n      "band_id": null,\n      "me'
    'mber_id": null,\n      "value": 10.0\n    },\n    {\n      "station_i'
    'd": "00000000-0000-0000-0000-00000001637d",\n      "source": "reca'
    'p_era5_land_reanalysis",\n      "version": "synthetic-v1",\n      "'
    'valid_time": "2026-01-16T00:00:00+00:00",\n      "parameter": "tem'
    'perature",\n      "spatial_type": "basin_average",\n      "band_id"'
    ': null,\n      "member_id": null,\n      "value": 10.0\n    },\n    {'
    '\n      "station_id": "00000000-0000-0000-0000-00000001637d",\n    '
    '  "source": "recap_era5_land_reanalysis",\n      "version": "synth'
    'etic-v1",\n      "valid_time": "2026-01-17T00:00:00+00:00",\n      '
    '"parameter": "precipitation",\n      "spatial_type": "basin_averag'
    'e",\n      "band_id": null,\n      "member_id": null,\n      "value"'
    ': 10.0\n    },\n    {\n      "station_id": "00000000-0000-0000-0000-'
    '00000001637d",\n      "source": "recap_era5_land_reanalysis",\n    '
    '  "version": "synthetic-v1",\n      "valid_time": "2026-01-17T00:0'
    '0:00+00:00",\n      "parameter": "temperature",\n      "spatial_typ'
    'e": "basin_average",\n      "band_id": null,\n      "member_id": nu'
    'll,\n      "value": 10.0\n    },\n    {\n      "station_id": "0000000'
    '0-0000-0000-0000-00000001637d",\n      "source": "recap_era5_land_'
    'reanalysis",\n      "version": "synthetic-v1",\n      "valid_time":'
    ' "2026-01-18T00:00:00+00:00",\n      "parameter": "precipitation",'
    '\n      "spatial_type": "basin_average",\n      "band_id": null,\n  '
    '    "member_id": null,\n      "value": 10.0\n    },\n    {\n      "st'
    'ation_id": "00000000-0000-0000-0000-00000001637d",\n      "source"'
    ': "recap_era5_land_reanalysis",\n      "version": "synthetic-v1",\n'
    '      "valid_time": "2026-01-18T00:00:00+00:00",\n      "parameter'
    '": "temperature",\n      "spatial_type": "basin_average",\n      "b'
    'and_id": null,\n      "member_id": null,\n      "value": 10.0\n    }'
    ',\n    {\n      "station_id": "00000000-0000-0000-0000-00000001637d'
    '",\n      "source": "recap_era5_land_reanalysis",\n      "version":'
    ' "synthetic-v1",\n      "valid_time": "2026-01-19T00:00:00+00:00",'
    '\n      "parameter": "precipitation",\n      "spatial_type": "basin'
    '_average",\n      "band_id": null,\n      "member_id": null,\n      '
    '"value": 10.0\n    },\n    {\n      "station_id": "00000000-0000-000'
    '0-0000-00000001637d",\n      "source": "recap_era5_land_reanalysis'
    '",\n      "version": "synthetic-v1",\n      "valid_time": "2026-01-'
    '19T00:00:00+00:00",\n      "parameter": "temperature",\n      "spat'
    'ial_type": "basin_average",\n      "band_id": null,\n      "member_'
    'id": null,\n      "value": 10.0\n    },\n    {\n      "station_id": "'
    '00000000-0000-0000-0000-00000001637e",\n      "source": "recap_era'
    '5_land_reanalysis",\n      "version": "synthetic-v1",\n      "valid'
    '_time": "2026-01-10T00:00:00+00:00",\n      "parameter": "precipit'
    'ation",\n      "spatial_type": "basin_average",\n      "band_id": n'
    'ull,\n      "member_id": null,\n      "value": 10.0\n    },\n    {\n  '
    '    "station_id": "00000000-0000-0000-0000-00000001637e",\n      "'
    'source": "recap_era5_land_reanalysis",\n      "version": "syntheti'
    'c-v1",\n      "valid_time": "2026-01-10T00:00:00+00:00",\n      "pa'
    'rameter": "temperature",\n      "spatial_type": "basin_average",\n '
    '     "band_id": null,\n      "member_id": null,\n      "value": 10.'
    '0\n    },\n    {\n      "station_id": "00000000-0000-0000-0000-00000'
    '001637e",\n      "source": "recap_era5_land_reanalysis",\n      "ve'
    'rsion": "synthetic-v1",\n      "valid_time": "2026-01-11T00:00:00+'
    '00:00",\n      "parameter": "precipitation",\n      "spatial_type":'
    ' "basin_average",\n      "band_id": null,\n      "member_id": null,'
    '\n      "value": 10.0\n    },\n    {\n      "station_id": "00000000-0'
    '000-0000-0000-00000001637e",\n      "source": "recap_era5_land_rea'
    'nalysis",\n      "version": "synthetic-v1",\n      "valid_time": "2'
    '026-01-11T00:00:00+00:00",\n      "parameter": "temperature",\n    '
    '  "spatial_type": "basin_average",\n      "band_id": null,\n      "'
    'member_id": null,\n      "value": 10.0\n    },\n    {\n      "station'
    '_id": "00000000-0000-0000-0000-00000001637e",\n      "source": "re'
    'cap_era5_land_reanalysis",\n      "version": "synthetic-v1",\n     '
    ' "valid_time": "2026-01-12T00:00:00+00:00",\n      "parameter": "p'
    'recipitation",\n      "spatial_type": "basin_average",\n      "band'
    '_id": null,\n      "member_id": null,\n      "value": 10.0\n    },\n '
    '   {\n      "station_id": "00000000-0000-0000-0000-00000001637e",\n'
    '      "source": "recap_era5_land_reanalysis",\n      "version": "s'
    'ynthetic-v1",\n      "valid_time": "2026-01-12T00:00:00+00:00",\n  '
    '    "parameter": "temperature",\n      "spatial_type": "basin_aver'
    'age",\n      "band_id": null,\n      "member_id": null,\n      "valu'
    'e": 10.0\n    },\n    {\n      "station_id": "00000000-0000-0000-000'
    '0-00000001637e",\n      "source": "recap_era5_land_reanalysis",\n  '
    '    "version": "synthetic-v1",\n      "valid_time": "2026-01-13T00'
    ':00:00+00:00",\n      "parameter": "precipitation",\n      "spatial'
    '_type": "basin_average",\n      "band_id": null,\n      "member_id"'
    ': null,\n      "value": 10.0\n    },\n    {\n      "station_id": "000'
    '00000-0000-0000-0000-00000001637e",\n      "source": "recap_era5_l'
    'and_reanalysis",\n      "version": "synthetic-v1",\n      "valid_ti'
    'me": "2026-01-13T00:00:00+00:00",\n      "parameter": "temperature'
    '",\n      "spatial_type": "basin_average",\n      "band_id": null,\n'
    '      "member_id": null,\n      "value": 10.0\n    },\n    {\n      "'
    'station_id": "00000000-0000-0000-0000-00000001637e",\n      "sourc'
    'e": "recap_era5_land_reanalysis",\n      "version": "synthetic-v1"'
    ',\n      "valid_time": "2026-01-14T00:00:00+00:00",\n      "paramet'
    'er": "precipitation",\n      "spatial_type": "basin_average",\n    '
    '  "band_id": null,\n      "member_id": null,\n      "value": 10.0\n '
    '   },\n    {\n      "station_id": "00000000-0000-0000-0000-00000001'
    '637e",\n      "source": "recap_era5_land_reanalysis",\n      "versi'
    'on": "synthetic-v1",\n      "valid_time": "2026-01-14T00:00:00+00:'
    '00",\n      "parameter": "temperature",\n      "spatial_type": "bas'
    'in_average",\n      "band_id": null,\n      "member_id": null,\n    '
    '  "value": 10.0\n    },\n    {\n      "station_id": "00000000-0000-0'
    '000-0000-00000001637e",\n      "source": "recap_era5_land_reanalys'
    'is",\n      "version": "synthetic-v1",\n      "valid_time": "2026-0'
    '1-15T00:00:00+00:00",\n      "parameter": "precipitation",\n      "'
    'spatial_type": "basin_average",\n      "band_id": null,\n      "mem'
    'ber_id": null,\n      "value": 10.0\n    },\n    {\n      "station_id'
    '": "00000000-0000-0000-0000-00000001637e",\n      "source": "recap'
    '_era5_land_reanalysis",\n      "version": "synthetic-v1",\n      "v'
    'alid_time": "2026-01-15T00:00:00+00:00",\n      "parameter": "temp'
    'erature",\n      "spatial_type": "basin_average",\n      "band_id":'
    ' null,\n      "member_id": null,\n      "value": 10.0\n    },\n    {\n'
    '      "station_id": "00000000-0000-0000-0000-00000001637e",\n     '
    ' "source": "recap_era5_land_reanalysis",\n      "version": "synthe'
    'tic-v1",\n      "valid_time": "2026-01-16T00:00:00+00:00",\n      "'
    'parameter": "precipitation",\n      "spatial_type": "basin_average'
    '",\n      "band_id": null,\n      "member_id": null,\n      "value":'
    ' 10.0\n    },\n    {\n      "station_id": "00000000-0000-0000-0000-0'
    '0000001637e",\n      "source": "recap_era5_land_reanalysis",\n     '
    ' "version": "synthetic-v1",\n      "valid_time": "2026-01-16T00:00'
    ':00+00:00",\n      "parameter": "temperature",\n      "spatial_type'
    '": "basin_average",\n      "band_id": null,\n      "member_id": nul'
    'l,\n      "value": 10.0\n    },\n    {\n      "station_id": "00000000'
    '-0000-0000-0000-00000001637e",\n      "source": "recap_era5_land_r'
    'eanalysis",\n      "version": "synthetic-v1",\n      "valid_time": '
    '"2026-01-17T00:00:00+00:00",\n      "parameter": "precipitation",\n'
    '      "spatial_type": "basin_average",\n      "band_id": null,\n   '
    '   "member_id": null,\n      "value": 10.0\n    },\n    {\n      "sta'
    'tion_id": "00000000-0000-0000-0000-00000001637e",\n      "source":'
    ' "recap_era5_land_reanalysis",\n      "version": "synthetic-v1",\n '
    '     "valid_time": "2026-01-17T00:00:00+00:00",\n      "parameter"'
    ': "temperature",\n      "spatial_type": "basin_average",\n      "ba'
    'nd_id": null,\n      "member_id": null,\n      "value": 10.0\n    },'
    '\n    {\n      "station_id": "00000000-0000-0000-0000-00000001637e"'
    ',\n      "source": "recap_era5_land_reanalysis",\n      "version": '
    '"synthetic-v1",\n      "valid_time": "2026-01-18T00:00:00+00:00",\n'
    '      "parameter": "precipitation",\n      "spatial_type": "basin_'
    'average",\n      "band_id": null,\n      "member_id": null,\n      "'
    'value": 10.0\n    },\n    {\n      "station_id": "00000000-0000-0000'
    '-0000-00000001637e",\n      "source": "recap_era5_land_reanalysis"'
    ',\n      "version": "synthetic-v1",\n      "valid_time": "2026-01-1'
    '8T00:00:00+00:00",\n      "parameter": "temperature",\n      "spati'
    'al_type": "basin_average",\n      "band_id": null,\n      "member_i'
    'd": null,\n      "value": 10.0\n    },\n    {\n      "station_id": "0'
    '0000000-0000-0000-0000-00000001637e",\n      "source": "recap_era5'
    '_land_reanalysis",\n      "version": "synthetic-v1",\n      "valid_'
    'time": "2026-01-19T00:00:00+00:00",\n      "parameter": "precipita'
    'tion",\n      "spatial_type": "basin_average",\n      "band_id": nu'
    'll,\n      "member_id": null,\n      "value": 10.0\n    },\n    {\n   '
    '   "station_id": "00000000-0000-0000-0000-00000001637e",\n      "s'
    'ource": "recap_era5_land_reanalysis",\n      "version": "synthetic'
    '-v1",\n      "valid_time": "2026-01-19T00:00:00+00:00",\n      "par'
    'ameter": "temperature",\n      "spatial_type": "basin_average",\n  '
    '    "band_id": null,\n      "member_id": null,\n      "value": 10.0'
    '\n    }\n  ],\n  "curve": {\n    "id": "00000000-0000-0000-0000-00000'
    '0017ed1",\n    "station_id": "00000000-0000-0000-0000-000000016379'
    '",\n    "version": 1,\n    "valid_from": "2025-01-10T00:00:00+00:00'
    '",\n    "valid_to": "2026-01-09T00:00:00+00:00",\n    "points": [\n '
    '     {\n        "water_level": 1.0,\n        "discharge": 4.0\n     '
    ' },\n      {\n        "water_level": 2.0,\n        "discharge": 1000'
    '0.0\n      }\n    ],\n    "interpolation": "linear",\n    "uploaded_b'
    'y": null,\n    "created_at": "2026-01-09T23:00:00+00:00",\n    "del'
    'ivery_id": null,\n    "rating_type_label": null\n  },\n  "levels": ['
    '\n    {\n      "id": "00000000-0000-0000-0000-0000000186a1",\n      '
    '"station_id": "00000000-0000-0000-0000-000000016379",\n      "time'
    'stamp": "2026-01-10T00:00:00+00:00",\n      "parameter": "water_le'
    'vel",\n      "value": 2.0,\n      "source": "measured",\n      "rati'
    'ng_curve_id": null,\n      "rating_curve_correction_version": null'
    ',\n      "qc_status": "qc_passed",\n      "qc_flags": [\n        {\n '
    '         "rule_id": "range_check",\n          "rule_version": "fix'
    'ture-level-v1",\n          "status": "qc_passed",\n          "detai'
    'l": null\n        }\n      ],\n      "qc_rule_version": "fixture-lev'
    'el-v1",\n      "created_at": "2026-01-20T00:00:00+00:00",\n      "d'
    'elivery_id": null\n    },\n    {\n      "id": "00000000-0000-0000-00'
    '00-0000000186a2",\n      "station_id": "00000000-0000-0000-0000-00'
    '0000016379",\n      "timestamp": "2026-01-11T00:00:00+00:00",\n    '
    '  "parameter": "water_level",\n      "value": 2.0,\n      "source":'
    ' "measured",\n      "rating_curve_id": null,\n      "rating_curve_c'
    'orrection_version": null,\n      "qc_status": "qc_passed",\n      "'
    'qc_flags": [\n        {\n          "rule_id": "range_check",\n      '
    '    "rule_version": "fixture-level-v1",\n          "status": "qc_p'
    'assed",\n          "detail": null\n        }\n      ],\n      "qc_rul'
    'e_version": "fixture-level-v1",\n      "created_at": "2026-01-20T0'
    '0:00:00+00:00",\n      "delivery_id": null\n    },\n    {\n      "id"'
    ': "00000000-0000-0000-0000-0000000186a3",\n      "station_id": "00'
    '000000-0000-0000-0000-000000016379",\n      "timestamp": "2026-01-'
    '12T00:00:00+00:00",\n      "parameter": "water_level",\n      "valu'
    'e": 2.0,\n      "source": "measured",\n      "rating_curve_id": nul'
    'l,\n      "rating_curve_correction_version": null,\n      "qc_statu'
    's": "qc_passed",\n      "qc_flags": [\n        {\n          "rule_id'
    '": "range_check",\n          "rule_version": "fixture-level-v1",\n '
    '         "status": "qc_passed",\n          "detail": null\n        '
    '}\n      ],\n      "qc_rule_version": "fixture-level-v1",\n      "cr'
    'eated_at": "2026-01-20T00:00:00+00:00",\n      "delivery_id": null'
    '\n    },\n    {\n      "id": "00000000-0000-0000-0000-0000000186a4",'
    '\n      "station_id": "00000000-0000-0000-0000-000000016379",\n    '
    '  "timestamp": "2026-01-13T00:00:00+00:00",\n      "parameter": "w'
    'ater_level",\n      "value": 2.0,\n      "source": "measured",\n    '
    '  "rating_curve_id": null,\n      "rating_curve_correction_version'
    '": null,\n      "qc_status": "qc_passed",\n      "qc_flags": [\n    '
    '    {\n          "rule_id": "range_check",\n          "rule_version'
    '": "fixture-level-v1",\n          "status": "qc_passed",\n         '
    ' "detail": null\n        }\n      ],\n      "qc_rule_version": "fixt'
    'ure-level-v1",\n      "created_at": "2026-01-20T00:00:00+00:00",\n '
    '     "delivery_id": null\n    },\n    {\n      "id": "00000000-0000-'
    '0000-0000-0000000186a5",\n      "station_id": "00000000-0000-0000-'
    '0000-000000016379",\n      "timestamp": "2026-01-14T00:00:00+00:00'
    '",\n      "parameter": "water_level",\n      "value": 2.0,\n      "s'
    'ource": "measured",\n      "rating_curve_id": null,\n      "rating_'
    'curve_correction_version": null,\n      "qc_status": "qc_passed",\n'
    '      "qc_flags": [\n        {\n          "rule_id": "range_check",'
    '\n          "rule_version": "fixture-level-v1",\n          "status"'
    ': "qc_passed",\n          "detail": null\n        }\n      ],\n      '
    '"qc_rule_version": "fixture-level-v1",\n      "created_at": "2026-'
    '01-20T00:00:00+00:00",\n      "delivery_id": null\n    },\n    {\n   '
    '   "id": "00000000-0000-0000-0000-0000000186a6",\n      "station_i'
    'd": "00000000-0000-0000-0000-000000016379",\n      "timestamp": "2'
    '026-01-15T00:00:00+00:00",\n      "parameter": "water_level",\n    '
    '  "value": 2.0,\n      "source": "measured",\n      "rating_curve_i'
    'd": null,\n      "rating_curve_correction_version": null,\n      "q'
    'c_status": "qc_passed",\n      "qc_flags": [\n        {\n          "'
    'rule_id": "range_check",\n          "rule_version": "fixture-level'
    '-v1",\n          "status": "qc_passed",\n          "detail": null\n '
    '       }\n      ],\n      "qc_rule_version": "fixture-level-v1",\n  '
    '    "created_at": "2026-01-20T00:00:00+00:00",\n      "delivery_id'
    '": null\n    },\n    {\n      "id": "00000000-0000-0000-0000-0000000'
    '186a7",\n      "station_id": "00000000-0000-0000-0000-000000016379'
    '",\n      "timestamp": "2026-01-16T00:00:00+00:00",\n      "paramet'
    'er": "water_level",\n      "value": 2.0,\n      "source": "measured'
    '",\n      "rating_curve_id": null,\n      "rating_curve_correction_'
    'version": null,\n      "qc_status": "qc_passed",\n      "qc_flags":'
    ' [\n        {\n          "rule_id": "range_check",\n          "rule_'
    'version": "fixture-level-v1",\n          "status": "qc_passed",\n  '
    '        "detail": null\n        }\n      ],\n      "qc_rule_version"'
    ': "fixture-level-v1",\n      "created_at": "2026-01-20T00:00:00+00'
    ':00",\n      "delivery_id": null\n    },\n    {\n      "id": "0000000'
    '0-0000-0000-0000-0000000186a8",\n      "station_id": "00000000-000'
    '0-0000-0000-000000016379",\n      "timestamp": "2026-01-17T00:00:0'
    '0+00:00",\n      "parameter": "water_level",\n      "value": 2.0,\n '
    '     "source": "measured",\n      "rating_curve_id": null,\n      "'
    'rating_curve_correction_version": null,\n      "qc_status": "qc_pa'
    'ssed",\n      "qc_flags": [\n        {\n          "rule_id": "range_'
    'check",\n          "rule_version": "fixture-level-v1",\n          "'
    'status": "qc_passed",\n          "detail": null\n        }\n      ],'
    '\n      "qc_rule_version": "fixture-level-v1",\n      "created_at":'
    ' "2026-01-20T00:00:00+00:00",\n      "delivery_id": null\n    },\n  '
    '  {\n      "id": "00000000-0000-0000-0000-0000000186a9",\n      "st'
    'ation_id": "00000000-0000-0000-0000-000000016379",\n      "timesta'
    'mp": "2026-01-18T00:00:00+00:00",\n      "parameter": "water_level'
    '",\n      "value": 2.0,\n      "source": "measured",\n      "rating_'
    'curve_id": null,\n      "rating_curve_correction_version": null,\n '
    '     "qc_status": "qc_passed",\n      "qc_flags": [\n        {\n    '
    '      "rule_id": "range_check",\n          "rule_version": "fixtur'
    'e-level-v1",\n          "status": "qc_passed",\n          "detail":'
    ' null\n        }\n      ],\n      "qc_rule_version": "fixture-level-'
    'v1",\n      "created_at": "2026-01-20T00:00:00+00:00",\n      "deli'
    'very_id": null\n    },\n    {\n      "id": "00000000-0000-0000-0000-'
    '0000000186aa",\n      "station_id": "00000000-0000-0000-0000-00000'
    '0016379",\n      "timestamp": "2026-01-19T00:00:00+00:00",\n      "'
    'parameter": "water_level",\n      "value": 2.0,\n      "source": "m'
    'easured",\n      "rating_curve_id": null,\n      "rating_curve_corr'
    'ection_version": null,\n      "qc_status": "qc_passed",\n      "qc_'
    'flags": [\n        {\n          "rule_id": "range_check",\n         '
    ' "rule_version": "fixture-level-v1",\n          "status": "qc_pass'
    'ed",\n          "detail": null\n        }\n      ],\n      "qc_rule_v'
    'ersion": "fixture-level-v1",\n      "created_at": "2026-01-20T00:0'
    '0:00+00:00",\n      "delivery_id": null\n    }\n  ],\n  "reference_pr'
    'oof": {\n    "tenant_id": "00000000-0000-0000-0000-00000001675f",\n'
    '    "station_id": "00000000-0000-0000-0000-000000016379",\n    "en'
    'dpoint": "https://example.invalid/nepal-qualification-levels/",\n '
    '   "api_station_id": 91001,\n    "evidence_reference": "nepal-read'
    'iness-isolation-v1",\n    "verified_by": "fixture-reviewer",\n    "'
    'verified_at": "2026-01-20T00:00:00+00:00",\n    "id": "00000000-00'
    '00-0000-0000-000000017ed2",\n    "rating_curve_id": "00000000-0000'
    '-0000-0000-000000017ed1",\n    "curve": {\n      "content": "{\\"cre'
    'ated_at\\":\\"2026-01-09T23:00:00+00:00\\",\\"delivery_id\\":null,\\"id'
    '\\":\\"00000000-0000-0000-0000-000000017ed1\\",\\"interpolation\\":\\"l'
    'inear\\",\\"points\\":[{\\"discharge\\":4.0,\\"water_level\\":1.0},{\\"di'
    'scharge\\":10000.0,\\"water_level\\":2.0}],\\"rating_type_label\\":nul'
    'l,\\"station_id\\":\\"00000000-0000-0000-0000-000000016379\\",\\"uploa'
    'ded_by\\":null,\\"valid_from\\":\\"2025-01-10T00:00:00+00:00\\",\\"vali'
    'd_to\\":\\"2026-01-09T00:00:00+00:00\\",\\"version\\":1}"\n    },\n    "'
    'level_unit": "m",\n    "curve_unit": "m",\n    "level_reference": "'
    'gauge_zero",\n    "curve_reference": "gauge_zero",\n    "offset_m":'
    ' 0.0\n  },\n  "feed_evidence": [\n    {\n      "tenant_id": "00000000'
    '-0000-0000-0000-00000001675f",\n      "station_id": "00000000-0000'
    '-0000-0000-000000016379",\n      "endpoint": "https://example.inva'
    'lid/nepal-qualification-levels/",\n      "api_station_id": 91001,\n'
    '      "evidence_reference": "nepal-readiness-isolation-v1",\n     '
    ' "verified_by": "fixture-reviewer",\n      "verified_at": "2026-01'
    '-20T00:00:00+00:00",\n      "id": "00000000-0000-0000-0000-0000000'
    '182b9",\n      "observation_id": "00000000-0000-0000-0000-00000001'
    '86a1",\n      "measurement": {\n        "content": "{\\"created_at\\"'
    ':\\"2026-01-20T00:00:00+00:00\\",\\"delivery_id\\":null,\\"id\\":\\"0000'
    '0000-0000-0000-0000-0000000186a1\\",\\"parameter\\":\\"water_level\\",'
    '\\"rating_curve_correction_version\\":null,\\"rating_curve_id\\":null'
    ',\\"source\\":\\"measured\\",\\"station_id\\":\\"00000000-0000-0000-0000'
    '-000000016379\\",\\"timestamp\\":\\"2026-01-10T00:00:00+00:00\\",\\"val'
    'ue\\":2.0}"\n      }\n    },\n    {\n      "tenant_id": "00000000-0000'
    '-0000-0000-00000001675f",\n      "station_id": "00000000-0000-0000'
    '-0000-000000016379",\n      "endpoint": "https://example.invalid/n'
    'epal-qualification-levels/",\n      "api_station_id": 91001,\n     '
    ' "evidence_reference": "nepal-readiness-isolation-v1",\n      "ver'
    'ified_by": "fixture-reviewer",\n      "verified_at": "2026-01-20T0'
    '0:00:00+00:00",\n      "id": "00000000-0000-0000-0000-0000000182ba'
    '",\n      "observation_id": "00000000-0000-0000-0000-0000000186a2"'
    ',\n      "measurement": {\n        "content": "{\\"created_at\\":\\"20'
    '26-01-20T00:00:00+00:00\\",\\"delivery_id\\":null,\\"id\\":\\"00000000-'
    '0000-0000-0000-0000000186a2\\",\\"parameter\\":\\"water_level\\",\\"rat'
    'ing_curve_correction_version\\":null,\\"rating_curve_id\\":null,\\"so'
    'urce\\":\\"measured\\",\\"station_id\\":\\"00000000-0000-0000-0000-0000'
    '00016379\\",\\"timestamp\\":\\"2026-01-11T00:00:00+00:00\\",\\"value\\":'
    '2.0}"\n      }\n    },\n    {\n      "tenant_id": "00000000-0000-0000'
    '-0000-00000001675f",\n      "station_id": "00000000-0000-0000-0000'
    '-000000016379",\n      "endpoint": "https://example.invalid/nepal-'
    'qualification-levels/",\n      "api_station_id": 91001,\n      "evi'
    'dence_reference": "nepal-readiness-isolation-v1",\n      "verified'
    '_by": "fixture-reviewer",\n      "verified_at": "2026-01-20T00:00:'
    '00+00:00",\n      "id": "00000000-0000-0000-0000-0000000182bb",\n  '
    '    "observation_id": "00000000-0000-0000-0000-0000000186a3",\n   '
    '   "measurement": {\n        "content": "{\\"created_at\\":\\"2026-01'
    '-20T00:00:00+00:00\\",\\"delivery_id\\":null,\\"id\\":\\"00000000-0000-'
    '0000-0000-0000000186a3\\",\\"parameter\\":\\"water_level\\",\\"rating_c'
    'urve_correction_version\\":null,\\"rating_curve_id\\":null,\\"source\\'
    '":\\"measured\\",\\"station_id\\":\\"00000000-0000-0000-0000-000000016'
    '379\\",\\"timestamp\\":\\"2026-01-12T00:00:00+00:00\\",\\"value\\":2.0}"'
    '\n      }\n    },\n    {\n      "tenant_id": "00000000-0000-0000-0000'
    '-00000001675f",\n      "station_id": "00000000-0000-0000-0000-0000'
    '00016379",\n      "endpoint": "https://example.invalid/nepal-quali'
    'fication-levels/",\n      "api_station_id": 91001,\n      "evidence'
    '_reference": "nepal-readiness-isolation-v1",\n      "verified_by":'
    ' "fixture-reviewer",\n      "verified_at": "2026-01-20T00:00:00+00'
    ':00",\n      "id": "00000000-0000-0000-0000-0000000182bc",\n      "'
    'observation_id": "00000000-0000-0000-0000-0000000186a4",\n      "m'
    'easurement": {\n        "content": "{\\"created_at\\":\\"2026-01-20T0'
    '0:00:00+00:00\\",\\"delivery_id\\":null,\\"id\\":\\"00000000-0000-0000-'
    '0000-0000000186a4\\",\\"parameter\\":\\"water_level\\",\\"rating_curve_'
    'correction_version\\":null,\\"rating_curve_id\\":null,\\"source\\":\\"m'
    'easured\\",\\"station_id\\":\\"00000000-0000-0000-0000-000000016379\\"'
    ',\\"timestamp\\":\\"2026-01-13T00:00:00+00:00\\",\\"value\\":2.0}"\n    '
    '  }\n    },\n    {\n      "tenant_id": "00000000-0000-0000-0000-0000'
    '0001675f",\n      "station_id": "00000000-0000-0000-0000-000000016'
    '379",\n      "endpoint": "https://example.invalid/nepal-qualificat'
    'ion-levels/",\n      "api_station_id": 91001,\n      "evidence_refe'
    'rence": "nepal-readiness-isolation-v1",\n      "verified_by": "fix'
    'ture-reviewer",\n      "verified_at": "2026-01-20T00:00:00+00:00",'
    '\n      "id": "00000000-0000-0000-0000-0000000182bd",\n      "obser'
    'vation_id": "00000000-0000-0000-0000-0000000186a5",\n      "measur'
    'ement": {\n        "content": "{\\"created_at\\":\\"2026-01-20T00:00:'
    '00+00:00\\",\\"delivery_id\\":null,\\"id\\":\\"00000000-0000-0000-0000-'
    '0000000186a5\\",\\"parameter\\":\\"water_level\\",\\"rating_curve_corre'
    'ction_version\\":null,\\"rating_curve_id\\":null,\\"source\\":\\"measur'
    'ed\\",\\"station_id\\":\\"00000000-0000-0000-0000-000000016379\\",\\"ti'
    'mestamp\\":\\"2026-01-14T00:00:00+00:00\\",\\"value\\":2.0}"\n      }\n '
    '   },\n    {\n      "tenant_id": "00000000-0000-0000-0000-000000016'
    '75f",\n      "station_id": "00000000-0000-0000-0000-000000016379",'
    '\n      "endpoint": "https://example.invalid/nepal-qualification-l'
    'evels/",\n      "api_station_id": 91001,\n      "evidence_reference'
    '": "nepal-readiness-isolation-v1",\n      "verified_by": "fixture-'
    'reviewer",\n      "verified_at": "2026-01-20T00:00:00+00:00",\n    '
    '  "id": "00000000-0000-0000-0000-0000000182be",\n      "observatio'
    'n_id": "00000000-0000-0000-0000-0000000186a6",\n      "measurement'
    '": {\n        "content": "{\\"created_at\\":\\"2026-01-20T00:00:00+00'
    ':00\\",\\"delivery_id\\":null,\\"id\\":\\"00000000-0000-0000-0000-00000'
    '00186a6\\",\\"parameter\\":\\"water_level\\",\\"rating_curve_correction'
    '_version\\":null,\\"rating_curve_id\\":null,\\"source\\":\\"measured\\",'
    '\\"station_id\\":\\"00000000-0000-0000-0000-000000016379\\",\\"timesta'
    'mp\\":\\"2026-01-15T00:00:00+00:00\\",\\"value\\":2.0}"\n      }\n    },'
    '\n    {\n      "tenant_id": "00000000-0000-0000-0000-00000001675f",'
    '\n      "station_id": "00000000-0000-0000-0000-000000016379",\n    '
    '  "endpoint": "https://example.invalid/nepal-qualification-levels'
    '/",\n      "api_station_id": 91001,\n      "evidence_reference": "n'
    'epal-readiness-isolation-v1",\n      "verified_by": "fixture-revie'
    'wer",\n      "verified_at": "2026-01-20T00:00:00+00:00",\n      "id'
    '": "00000000-0000-0000-0000-0000000182bf",\n      "observation_id"'
    ': "00000000-0000-0000-0000-0000000186a7",\n      "measurement": {\n'
    '        "content": "{\\"created_at\\":\\"2026-01-20T00:00:00+00:00\\"'
    ',\\"delivery_id\\":null,\\"id\\":\\"00000000-0000-0000-0000-0000000186'
    'a7\\",\\"parameter\\":\\"water_level\\",\\"rating_curve_correction_vers'
    'ion\\":null,\\"rating_curve_id\\":null,\\"source\\":\\"measured\\",\\"sta'
    'tion_id\\":\\"00000000-0000-0000-0000-000000016379\\",\\"timestamp\\":'
    '\\"2026-01-16T00:00:00+00:00\\",\\"value\\":2.0}"\n      }\n    },\n    '
    '{\n      "tenant_id": "00000000-0000-0000-0000-00000001675f",\n    '
    '  "station_id": "00000000-0000-0000-0000-000000016379",\n      "en'
    'dpoint": "https://example.invalid/nepal-qualification-levels/",\n '
    '     "api_station_id": 91001,\n      "evidence_reference": "nepal-'
    'readiness-isolation-v1",\n      "verified_by": "fixture-reviewer",'
    '\n      "verified_at": "2026-01-20T00:00:00+00:00",\n      "id": "0'
    '0000000-0000-0000-0000-0000000182c0",\n      "observation_id": "00'
    '000000-0000-0000-0000-0000000186a8",\n      "measurement": {\n     '
    '   "content": "{\\"created_at\\":\\"2026-01-20T00:00:00+00:00\\",\\"de'
    'livery_id\\":null,\\"id\\":\\"00000000-0000-0000-0000-0000000186a8\\",'
    '\\"parameter\\":\\"water_level\\",\\"rating_curve_correction_version\\"'
    ':null,\\"rating_curve_id\\":null,\\"source\\":\\"measured\\",\\"station_'
    'id\\":\\"00000000-0000-0000-0000-000000016379\\",\\"timestamp\\":\\"202'
    '6-01-17T00:00:00+00:00\\",\\"value\\":2.0}"\n      }\n    },\n    {\n   '
    '   "tenant_id": "00000000-0000-0000-0000-00000001675f",\n      "st'
    'ation_id": "00000000-0000-0000-0000-000000016379",\n      "endpoin'
    't": "https://example.invalid/nepal-qualification-levels/",\n      '
    '"api_station_id": 91001,\n      "evidence_reference": "nepal-readi'
    'ness-isolation-v1",\n      "verified_by": "fixture-reviewer",\n    '
    '  "verified_at": "2026-01-20T00:00:00+00:00",\n      "id": "000000'
    '00-0000-0000-0000-0000000182c1",\n      "observation_id": "0000000'
    '0-0000-0000-0000-0000000186a9",\n      "measurement": {\n        "c'
    'ontent": "{\\"created_at\\":\\"2026-01-20T00:00:00+00:00\\",\\"deliver'
    'y_id\\":null,\\"id\\":\\"00000000-0000-0000-0000-0000000186a9\\",\\"par'
    'ameter\\":\\"water_level\\",\\"rating_curve_correction_version\\":null'
    ',\\"rating_curve_id\\":null,\\"source\\":\\"measured\\",\\"station_id\\":'
    '\\"00000000-0000-0000-0000-000000016379\\",\\"timestamp\\":\\"2026-01-'
    '18T00:00:00+00:00\\",\\"value\\":2.0}"\n      }\n    },\n    {\n      "t'
    'enant_id": "00000000-0000-0000-0000-00000001675f",\n      "station'
    '_id": "00000000-0000-0000-0000-000000016379",\n      "endpoint": "'
    'https://example.invalid/nepal-qualification-levels/",\n      "api_'
    'station_id": 91001,\n      "evidence_reference": "nepal-readiness-'
    'isolation-v1",\n      "verified_by": "fixture-reviewer",\n      "ve'
    'rified_at": "2026-01-20T00:00:00+00:00",\n      "id": "00000000-00'
    '00-0000-0000-0000000182c2",\n      "observation_id": "00000000-000'
    '0-0000-0000-0000000186aa",\n      "measurement": {\n        "conten'
    't": "{\\"created_at\\":\\"2026-01-20T00:00:00+00:00\\",\\"delivery_id\\'
    '":null,\\"id\\":\\"00000000-0000-0000-0000-0000000186aa\\",\\"paramete'
    'r\\":\\"water_level\\",\\"rating_curve_correction_version\\":null,\\"ra'
    'ting_curve_id\\":null,\\"source\\":\\"measured\\",\\"station_id\\":\\"000'
    '00000-0000-0000-0000-000000016379\\",\\"timestamp\\":\\"2026-01-19T00'
    ':00:00+00:00\\",\\"value\\":2.0}"\n      }\n    }\n  ],\n  "permission":'
    ' {\n    "tenant_id": "00000000-0000-0000-0000-00000001675f",\n    "'
    'state": "enabled",\n    "permission_reference": "fixture-only",\n  '
    '  "inventory_digest": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
    'aaaaaaaaaaaaaaaaaaaaaa"\n  },\n  "protected_rows": [\n    {\n      "f'
    'ingerprint": "6a3a8ad5082154e4a8be461cf4da6be6814fe310e3b10274510'
    '0b207b6eb6196",\n      "tenant_id": "00000000-0000-0000-0000-00000'
    '001675f",\n      "station_id": "00000000-0000-0000-0000-0000000163'
    '79",\n      "observation_id": "00000000-0000-0000-0000-0000000186a'
    '1",\n      "rating_curve_id": "00000000-0000-0000-0000-000000017ed'
    '1",\n      "feed_evidence_id": "00000000-0000-0000-0000-0000000182'
    'b9",\n      "reference_proof_id": "00000000-0000-0000-0000-0000000'
    '17ed2",\n      "discharge": 10000.0,\n      "content": "{\\"conversi'
    'on_version\\":\\"expired-rating-linear-reference-v1\\",\\"curve\\":{\\"'
    'created_at\\":\\"2026-01-09T23:00:00+00:00\\",\\"delivery_id\\":null,\\'
    '"id\\":\\"00000000-0000-0000-0000-000000017ed1\\",\\"interpolation\\":'
    '\\"linear\\",\\"points\\":[{\\"discharge\\":4.0,\\"water_level\\":1.0},{\\'
    '"discharge\\":10000.0,\\"water_level\\":2.0}],\\"rating_type_label\\":'
    'null,\\"station_id\\":\\"00000000-0000-0000-0000-000000016379\\",\\"up'
    'loaded_by\\":null,\\"valid_from\\":\\"2025-01-10T00:00:00+00:00\\",\\"v'
    'alid_to\\":\\"2026-01-09T00:00:00+00:00\\",\\"version\\":1},\\"discharg'
    'e\\":10000.0,\\"feed_evidence\\":{\\"api_station_id\\":91001,\\"endpoin'
    't\\":\\"https://example.invalid/nepal-qualification-levels/\\",\\"evi'
    'dence_reference\\":\\"nepal-readiness-isolation-v1\\",\\"id\\":\\"00000'
    '000-0000-0000-0000-0000000182b9\\",\\"measurement\\":{\\"content\\":\\"'
    '{\\\\\\"created_at\\\\\\":\\\\\\"2026-01-20T00:00:00+00:00\\\\\\",\\\\\\"deliver'
    'y_id\\\\\\":null,\\\\\\"id\\\\\\":\\\\\\"00000000-0000-0000-0000-0000000186a1'
    '\\\\\\",\\\\\\"parameter\\\\\\":\\\\\\"water_level\\\\\\",\\\\\\"rating_curve_co'
    'rrection_version\\\\\\":null,\\\\\\"rating_curve_id\\\\\\":null,\\\\\\"source'
    '\\\\\\":\\\\\\"measured\\\\\\",\\\\\\"station_id\\\\\\":\\\\\\"00000000-0000-000'
    '0-0000-000000016379\\\\\\",\\\\\\"timestamp\\\\\\":\\\\\\"2026-01-10T00:00:00'
    '+00:00\\\\\\",\\\\\\"value\\\\\\":2.0}\\"},\\"observation_id\\":\\"00000000-00'
    '00-0000-0000-0000000186a1\\",\\"station_id\\":\\"00000000-0000-0000-0'
    '000-000000016379\\",\\"tenant_id\\":\\"00000000-0000-0000-0000-000000'
    '01675f\\",\\"verified_at\\":\\"2026-01-20T00:00:00+00:00\\",\\"verified'
    '_by\\":\\"fixture-reviewer\\"},\\"measurement\\":{\\"created_at\\":\\"202'
    '6-01-20T00:00:00+00:00\\",\\"delivery_id\\":null,\\"id\\":\\"00000000-0'
    '000-0000-0000-0000000186a1\\",\\"parameter\\":\\"water_level\\",\\"rati'
    'ng_curve_correction_version\\":null,\\"rating_curve_id\\":null,\\"sou'
    'rce\\":\\"measured\\",\\"station_id\\":\\"00000000-0000-0000-0000-00000'
    '0016379\\",\\"timestamp\\":\\"2026-01-10T00:00:00+00:00\\",\\"value\\":2'
    '.0},\\"qc\\":{\\"qc_flags\\":[{\\"detail\\":null,\\"rule_id\\":\\"range_ch'
    'eck\\",\\"rule_version\\":\\"fixture-level-v1\\",\\"status\\":\\"qc_passe'
    'd\\"}],\\"qc_rule_version\\":\\"fixture-level-v1\\",\\"qc_status\\":\\"qc'
    '_passed\\"},\\"reference_proof\\":{\\"api_station_id\\":91001,\\"curve\\'
    '":{\\"content\\":\\"{\\\\\\"created_at\\\\\\":\\\\\\"2026-01-09T23:00:00+00:0'
    '0\\\\\\",\\\\\\"delivery_id\\\\\\":null,\\\\\\"id\\\\\\":\\\\\\"00000000-0000-00'
    '00-0000-000000017ed1\\\\\\",\\\\\\"interpolation\\\\\\":\\\\\\"linear\\\\\\",\\'
    '\\\\"points\\\\\\":[{\\\\\\"discharge\\\\\\":4.0,\\\\\\"water_level\\\\\\":1.0},'
    '{\\\\\\"discharge\\\\\\":10000.0,\\\\\\"water_level\\\\\\":2.0}],\\\\\\"rating_t'
    'ype_label\\\\\\":null,\\\\\\"station_id\\\\\\":\\\\\\"00000000-0000-0000-0000'
    '-000000016379\\\\\\",\\\\\\"uploaded_by\\\\\\":null,\\\\\\"valid_from\\\\\\":\\'
    '\\\\"2025-01-10T00:00:00+00:00\\\\\\",\\\\\\"valid_to\\\\\\":\\\\\\"2026-01-09T'
    '00:00:00+00:00\\\\\\",\\\\\\"version\\\\\\":1}\\"},\\"curve_reference\\":\\"ga'
    'uge_zero\\",\\"curve_unit\\":\\"m\\",\\"endpoint\\":\\"https://example.in'
    'valid/nepal-qualification-levels/\\",\\"evidence_reference\\":\\"nepa'
    'l-readiness-isolation-v1\\",\\"id\\":\\"00000000-0000-0000-0000-00000'
    '0017ed2\\",\\"level_reference\\":\\"gauge_zero\\",\\"level_unit\\":\\"m\\"'
    ',\\"offset_m\\":0.0,\\"rating_curve_id\\":\\"00000000-0000-0000-0000-0'
    '00000017ed1\\",\\"station_id\\":\\"00000000-0000-0000-0000-0000000163'
    '79\\",\\"tenant_id\\":\\"00000000-0000-0000-0000-00000001675f\\",\\"ver'
    'ified_at\\":\\"2026-01-20T00:00:00+00:00\\",\\"verified_by\\":\\"fixtur'
    'e-reviewer\\"}}",\n      "captured_at": "2026-01-20T00:00:00+00:00"'
    '\n    },\n    {\n      "fingerprint": "daf7cf62371b5a4d9014ba90c8f3e'
    'f9f33b1b8cf120b6bfe787e798068d52ba5",\n      "tenant_id": "0000000'
    '0-0000-0000-0000-00000001675f",\n      "station_id": "00000000-000'
    '0-0000-0000-000000016379",\n      "observation_id": "00000000-0000'
    '-0000-0000-0000000186a2",\n      "rating_curve_id": "00000000-0000'
    '-0000-0000-000000017ed1",\n      "feed_evidence_id": "00000000-000'
    '0-0000-0000-0000000182ba",\n      "reference_proof_id": "00000000-'
    '0000-0000-0000-000000017ed2",\n      "discharge": 10000.0,\n      "'
    'content": "{\\"conversion_version\\":\\"expired-rating-linear-refere'
    'nce-v1\\",\\"curve\\":{\\"created_at\\":\\"2026-01-09T23:00:00+00:00\\",'
    '\\"delivery_id\\":null,\\"id\\":\\"00000000-0000-0000-0000-000000017ed'
    '1\\",\\"interpolation\\":\\"linear\\",\\"points\\":[{\\"discharge\\":4.0,\\'
    '"water_level\\":1.0},{\\"discharge\\":10000.0,\\"water_level\\":2.0}],'
    '\\"rating_type_label\\":null,\\"station_id\\":\\"00000000-0000-0000-00'
    '00-000000016379\\",\\"uploaded_by\\":null,\\"valid_from\\":\\"2025-01-1'
    '0T00:00:00+00:00\\",\\"valid_to\\":\\"2026-01-09T00:00:00+00:00\\",\\"v'
    'ersion\\":1},\\"discharge\\":10000.0,\\"feed_evidence\\":{\\"api_statio'
    'n_id\\":91001,\\"endpoint\\":\\"https://example.invalid/nepal-qualifi'
    'cation-levels/\\",\\"evidence_reference\\":\\"nepal-readiness-isolati'
    'on-v1\\",\\"id\\":\\"00000000-0000-0000-0000-0000000182ba\\",\\"measure'
    'ment\\":{\\"content\\":\\"{\\\\\\"created_at\\\\\\":\\\\\\"2026-01-20T00:00:00'
    '+00:00\\\\\\",\\\\\\"delivery_id\\\\\\":null,\\\\\\"id\\\\\\":\\\\\\"00000000-00'
    '00-0000-0000-0000000186a2\\\\\\",\\\\\\"parameter\\\\\\":\\\\\\"water_level\\\\'
    '\\",\\\\\\"rating_curve_correction_version\\\\\\":null,\\\\\\"rating_curve_'
    'id\\\\\\":null,\\\\\\"source\\\\\\":\\\\\\"measured\\\\\\",\\\\\\"station_id\\\\'
    '\\":\\\\\\"00000000-0000-0000-0000-000000016379\\\\\\",\\\\\\"timestamp\\\\\\"'
    ':\\\\\\"2026-01-11T00:00:00+00:00\\\\\\",\\\\\\"value\\\\\\":2.0}\\"},\\"observ'
    'ation_id\\":\\"00000000-0000-0000-0000-0000000186a2\\",\\"station_id\\'
    '":\\"00000000-0000-0000-0000-000000016379\\",\\"tenant_id\\":\\"000000'
    '00-0000-0000-0000-00000001675f\\",\\"verified_at\\":\\"2026-01-20T00:'
    '00:00+00:00\\",\\"verified_by\\":\\"fixture-reviewer\\"},\\"measurement'
    '\\":{\\"created_at\\":\\"2026-01-20T00:00:00+00:00\\",\\"delivery_id\\":'
    'null,\\"id\\":\\"00000000-0000-0000-0000-0000000186a2\\",\\"parameter\\'
    '":\\"water_level\\",\\"rating_curve_correction_version\\":null,\\"rati'
    'ng_curve_id\\":null,\\"source\\":\\"measured\\",\\"station_id\\":\\"00000'
    '000-0000-0000-0000-000000016379\\",\\"timestamp\\":\\"2026-01-11T00:0'
    '0:00+00:00\\",\\"value\\":2.0},\\"qc\\":{\\"qc_flags\\":[{\\"detail\\":nul'
    'l,\\"rule_id\\":\\"range_check\\",\\"rule_version\\":\\"fixture-level-v1'
    '\\",\\"status\\":\\"qc_passed\\"}],\\"qc_rule_version\\":\\"fixture-level'
    '-v1\\",\\"qc_status\\":\\"qc_passed\\"},\\"reference_proof\\":{\\"api_sta'
    'tion_id\\":91001,\\"curve\\":{\\"content\\":\\"{\\\\\\"created_at\\\\\\":\\\\\\"'
    '2026-01-09T23:00:00+00:00\\\\\\",\\\\\\"delivery_id\\\\\\":null,\\\\\\"id\\\\\\"'
    ':\\\\\\"00000000-0000-0000-0000-000000017ed1\\\\\\",\\\\\\"interpolation\\\\'
    '\\":\\\\\\"linear\\\\\\",\\\\\\"points\\\\\\":[{\\\\\\"discharge\\\\\\":4.0,\\\\'
    '\\"water_level\\\\\\":1.0},{\\\\\\"discharge\\\\\\":10000.0,\\\\\\"water_level'
    '\\\\\\":2.0}],\\\\\\"rating_type_label\\\\\\":null,\\\\\\"station_id\\\\\\":\\\\'
    '\\"00000000-0000-0000-0000-000000016379\\\\\\",\\\\\\"uploaded_by\\\\\\":nu'
    'll,\\\\\\"valid_from\\\\\\":\\\\\\"2025-01-10T00:00:00+00:00\\\\\\",\\\\\\"valid'
    '_to\\\\\\":\\\\\\"2026-01-09T00:00:00+00:00\\\\\\",\\\\\\"version\\\\\\":1}\\"},'
    '\\"curve_reference\\":\\"gauge_zero\\",\\"curve_unit\\":\\"m\\",\\"endpoin'
    't\\":\\"https://example.invalid/nepal-qualification-levels/\\",\\"evi'
    'dence_reference\\":\\"nepal-readiness-isolation-v1\\",\\"id\\":\\"00000'
    '000-0000-0000-0000-000000017ed2\\",\\"level_reference\\":\\"gauge_zer'
    'o\\",\\"level_unit\\":\\"m\\",\\"offset_m\\":0.0,\\"rating_curve_id\\":\\"0'
    '0000000-0000-0000-0000-000000017ed1\\",\\"station_id\\":\\"00000000-0'
    '000-0000-0000-000000016379\\",\\"tenant_id\\":\\"00000000-0000-0000-0'
    '000-00000001675f\\",\\"verified_at\\":\\"2026-01-20T00:00:00+00:00\\",'
    '\\"verified_by\\":\\"fixture-reviewer\\"}}",\n      "captured_at": "20'
    '26-01-20T00:00:00+00:00"\n    },\n    {\n      "fingerprint": "c8b02'
    '7c32a18a84b8b02e29e16b1d58e7c9f6c97c692b3e1bbefb1b3993fe44c",\n   '
    '   "tenant_id": "00000000-0000-0000-0000-00000001675f",\n      "st'
    'ation_id": "00000000-0000-0000-0000-000000016379",\n      "observa'
    'tion_id": "00000000-0000-0000-0000-0000000186a3",\n      "rating_c'
    'urve_id": "00000000-0000-0000-0000-000000017ed1",\n      "feed_evi'
    'dence_id": "00000000-0000-0000-0000-0000000182bb",\n      "referen'
    'ce_proof_id": "00000000-0000-0000-0000-000000017ed2",\n      "disc'
    'harge": 10000.0,\n      "content": "{\\"conversion_version\\":\\"expi'
    'red-rating-linear-reference-v1\\",\\"curve\\":{\\"created_at\\":\\"2026'
    '-01-09T23:00:00+00:00\\",\\"delivery_id\\":null,\\"id\\":\\"00000000-00'
    '00-0000-0000-000000017ed1\\",\\"interpolation\\":\\"linear\\",\\"points'
    '\\":[{\\"discharge\\":4.0,\\"water_level\\":1.0},{\\"discharge\\":10000.'
    '0,\\"water_level\\":2.0}],\\"rating_type_label\\":null,\\"station_id\\"'
    ':\\"00000000-0000-0000-0000-000000016379\\",\\"uploaded_by\\":null,\\"'
    'valid_from\\":\\"2025-01-10T00:00:00+00:00\\",\\"valid_to\\":\\"2026-01'
    '-09T00:00:00+00:00\\",\\"version\\":1},\\"discharge\\":10000.0,\\"feed_'
    'evidence\\":{\\"api_station_id\\":91001,\\"endpoint\\":\\"https://examp'
    'le.invalid/nepal-qualification-levels/\\",\\"evidence_reference\\":\\'
    '"nepal-readiness-isolation-v1\\",\\"id\\":\\"00000000-0000-0000-0000-'
    '0000000182bb\\",\\"measurement\\":{\\"content\\":\\"{\\\\\\"created_at\\\\\\"'
    ':\\\\\\"2026-01-20T00:00:00+00:00\\\\\\",\\\\\\"delivery_id\\\\\\":null,\\\\\\"i'
    'd\\\\\\":\\\\\\"00000000-0000-0000-0000-0000000186a3\\\\\\",\\\\\\"parameter\\'
    '\\\\":\\\\\\"water_level\\\\\\",\\\\\\"rating_curve_correction_version\\\\\\":n'
    'ull,\\\\\\"rating_curve_id\\\\\\":null,\\\\\\"source\\\\\\":\\\\\\"measured\\\\'
    '\\",\\\\\\"station_id\\\\\\":\\\\\\"00000000-0000-0000-0000-000000016379\\\\\\'
    '",\\\\\\"timestamp\\\\\\":\\\\\\"2026-01-12T00:00:00+00:00\\\\\\",\\\\\\"value\\'
    '\\\\":2.0}\\"},\\"observation_id\\":\\"00000000-0000-0000-0000-00000001'
    '86a3\\",\\"station_id\\":\\"00000000-0000-0000-0000-000000016379\\",\\"'
    'tenant_id\\":\\"00000000-0000-0000-0000-00000001675f\\",\\"verified_a'
    't\\":\\"2026-01-20T00:00:00+00:00\\",\\"verified_by\\":\\"fixture-revie'
    'wer\\"},\\"measurement\\":{\\"created_at\\":\\"2026-01-20T00:00:00+00:0'
    '0\\",\\"delivery_id\\":null,\\"id\\":\\"00000000-0000-0000-0000-0000000'
    '186a3\\",\\"parameter\\":\\"water_level\\",\\"rating_curve_correction_v'
    'ersion\\":null,\\"rating_curve_id\\":null,\\"source\\":\\"measured\\",\\"'
    'station_id\\":\\"00000000-0000-0000-0000-000000016379\\",\\"timestamp'
    '\\":\\"2026-01-12T00:00:00+00:00\\",\\"value\\":2.0},\\"qc\\":{\\"qc_flag'
    's\\":[{\\"detail\\":null,\\"rule_id\\":\\"range_check\\",\\"rule_version\\'
    '":\\"fixture-level-v1\\",\\"status\\":\\"qc_passed\\"}],\\"qc_rule_versi'
    'on\\":\\"fixture-level-v1\\",\\"qc_status\\":\\"qc_passed\\"},\\"referenc'
    'e_proof\\":{\\"api_station_id\\":91001,\\"curve\\":{\\"content\\":\\"{\\\\\\'
    '"created_at\\\\\\":\\\\\\"2026-01-09T23:00:00+00:00\\\\\\",\\\\\\"delivery_id'
    '\\\\\\":null,\\\\\\"id\\\\\\":\\\\\\"00000000-0000-0000-0000-000000017ed1\\\\\\"'
    ',\\\\\\"interpolation\\\\\\":\\\\\\"linear\\\\\\",\\\\\\"points\\\\\\":[{\\\\\\"'
    'discharge\\\\\\":4.0,\\\\\\"water_level\\\\\\":1.0},{\\\\\\"discharge\\\\\\":100'
    '00.0,\\\\\\"water_level\\\\\\":2.0}],\\\\\\"rating_type_label\\\\\\":null,\\\\\\'
    '"station_id\\\\\\":\\\\\\"00000000-0000-0000-0000-000000016379\\\\\\",\\\\\\"'
    'uploaded_by\\\\\\":null,\\\\\\"valid_from\\\\\\":\\\\\\"2025-01-10T00:00:00+0'
    '0:00\\\\\\",\\\\\\"valid_to\\\\\\":\\\\\\"2026-01-09T00:00:00+00:00\\\\\\",\\\\'
    '\\"version\\\\\\":1}\\"},\\"curve_reference\\":\\"gauge_zero\\",\\"curve_un'
    'it\\":\\"m\\",\\"endpoint\\":\\"https://example.invalid/nepal-qualifica'
    'tion-levels/\\",\\"evidence_reference\\":\\"nepal-readiness-isolation'
    '-v1\\",\\"id\\":\\"00000000-0000-0000-0000-000000017ed2\\",\\"level_ref'
    'erence\\":\\"gauge_zero\\",\\"level_unit\\":\\"m\\",\\"offset_m\\":0.0,\\"r'
    'ating_curve_id\\":\\"00000000-0000-0000-0000-000000017ed1\\",\\"stati'
    'on_id\\":\\"00000000-0000-0000-0000-000000016379\\",\\"tenant_id\\":\\"'
    '00000000-0000-0000-0000-00000001675f\\",\\"verified_at\\":\\"2026-01-'
    '20T00:00:00+00:00\\",\\"verified_by\\":\\"fixture-reviewer\\"}}",\n    '
    '  "captured_at": "2026-01-20T00:00:00+00:00"\n    },\n    {\n      "'
    'fingerprint": "bf70f1f321dd000f255c8156566cf585ce07c368c7321e071b'
    '21dc057db141e9",\n      "tenant_id": "00000000-0000-0000-0000-0000'
    '0001675f",\n      "station_id": "00000000-0000-0000-0000-000000016'
    '379",\n      "observation_id": "00000000-0000-0000-0000-0000000186'
    'a4",\n      "rating_curve_id": "00000000-0000-0000-0000-000000017e'
    'd1",\n      "feed_evidence_id": "00000000-0000-0000-0000-000000018'
    '2bc",\n      "reference_proof_id": "00000000-0000-0000-0000-000000'
    '017ed2",\n      "discharge": 10000.0,\n      "content": "{\\"convers'
    'ion_version\\":\\"expired-rating-linear-reference-v1\\",\\"curve\\":{\\'
    '"created_at\\":\\"2026-01-09T23:00:00+00:00\\",\\"delivery_id\\":null,'
    '\\"id\\":\\"00000000-0000-0000-0000-000000017ed1\\",\\"interpolation\\"'
    ':\\"linear\\",\\"points\\":[{\\"discharge\\":4.0,\\"water_level\\":1.0},{'
    '\\"discharge\\":10000.0,\\"water_level\\":2.0}],\\"rating_type_label\\"'
    ':null,\\"station_id\\":\\"00000000-0000-0000-0000-000000016379\\",\\"u'
    'ploaded_by\\":null,\\"valid_from\\":\\"2025-01-10T00:00:00+00:00\\",\\"'
    'valid_to\\":\\"2026-01-09T00:00:00+00:00\\",\\"version\\":1},\\"dischar'
    'ge\\":10000.0,\\"feed_evidence\\":{\\"api_station_id\\":91001,\\"endpoi'
    'nt\\":\\"https://example.invalid/nepal-qualification-levels/\\",\\"ev'
    'idence_reference\\":\\"nepal-readiness-isolation-v1\\",\\"id\\":\\"0000'
    '0000-0000-0000-0000-0000000182bc\\",\\"measurement\\":{\\"content\\":\\'
    '"{\\\\\\"created_at\\\\\\":\\\\\\"2026-01-20T00:00:00+00:00\\\\\\",\\\\\\"delive'
    'ry_id\\\\\\":null,\\\\\\"id\\\\\\":\\\\\\"00000000-0000-0000-0000-0000000186a'
    '4\\\\\\",\\\\\\"parameter\\\\\\":\\\\\\"water_level\\\\\\",\\\\\\"rating_curve_c'
    'orrection_version\\\\\\":null,\\\\\\"rating_curve_id\\\\\\":null,\\\\\\"sourc'
    'e\\\\\\":\\\\\\"measured\\\\\\",\\\\\\"station_id\\\\\\":\\\\\\"00000000-0000-00'
    '00-0000-000000016379\\\\\\",\\\\\\"timestamp\\\\\\":\\\\\\"2026-01-13T00:00:0'
    '0+00:00\\\\\\",\\\\\\"value\\\\\\":2.0}\\"},\\"observation_id\\":\\"00000000-0'
    '000-0000-0000-0000000186a4\\",\\"station_id\\":\\"00000000-0000-0000-'
    '0000-000000016379\\",\\"tenant_id\\":\\"00000000-0000-0000-0000-00000'
    '001675f\\",\\"verified_at\\":\\"2026-01-20T00:00:00+00:00\\",\\"verifie'
    'd_by\\":\\"fixture-reviewer\\"},\\"measurement\\":{\\"created_at\\":\\"20'
    '26-01-20T00:00:00+00:00\\",\\"delivery_id\\":null,\\"id\\":\\"00000000-'
    '0000-0000-0000-0000000186a4\\",\\"parameter\\":\\"water_level\\",\\"rat'
    'ing_curve_correction_version\\":null,\\"rating_curve_id\\":null,\\"so'
    'urce\\":\\"measured\\",\\"station_id\\":\\"00000000-0000-0000-0000-0000'
    '00016379\\",\\"timestamp\\":\\"2026-01-13T00:00:00+00:00\\",\\"value\\":'
    '2.0},\\"qc\\":{\\"qc_flags\\":[{\\"detail\\":null,\\"rule_id\\":\\"range_c'
    'heck\\",\\"rule_version\\":\\"fixture-level-v1\\",\\"status\\":\\"qc_pass'
    'ed\\"}],\\"qc_rule_version\\":\\"fixture-level-v1\\",\\"qc_status\\":\\"q'
    'c_passed\\"},\\"reference_proof\\":{\\"api_station_id\\":91001,\\"curve'
    '\\":{\\"content\\":\\"{\\\\\\"created_at\\\\\\":\\\\\\"2026-01-09T23:00:00+00:'
    '00\\\\\\",\\\\\\"delivery_id\\\\\\":null,\\\\\\"id\\\\\\":\\\\\\"00000000-0000-0'
    '000-0000-000000017ed1\\\\\\",\\\\\\"interpolation\\\\\\":\\\\\\"linear\\\\\\",\\'
    '\\\\"points\\\\\\":[{\\\\\\"discharge\\\\\\":4.0,\\\\\\"water_level\\\\\\":1.0},'
    '{\\\\\\"discharge\\\\\\":10000.0,\\\\\\"water_level\\\\\\":2.0}],\\\\\\"rating_t'
    'ype_label\\\\\\":null,\\\\\\"station_id\\\\\\":\\\\\\"00000000-0000-0000-0000'
    '-000000016379\\\\\\",\\\\\\"uploaded_by\\\\\\":null,\\\\\\"valid_from\\\\\\":\\'
    '\\\\"2025-01-10T00:00:00+00:00\\\\\\",\\\\\\"valid_to\\\\\\":\\\\\\"2026-01-09T'
    '00:00:00+00:00\\\\\\",\\\\\\"version\\\\\\":1}\\"},\\"curve_reference\\":\\"ga'
    'uge_zero\\",\\"curve_unit\\":\\"m\\",\\"endpoint\\":\\"https://example.in'
    'valid/nepal-qualification-levels/\\",\\"evidence_reference\\":\\"nepa'
    'l-readiness-isolation-v1\\",\\"id\\":\\"00000000-0000-0000-0000-00000'
    '0017ed2\\",\\"level_reference\\":\\"gauge_zero\\",\\"level_unit\\":\\"m\\"'
    ',\\"offset_m\\":0.0,\\"rating_curve_id\\":\\"00000000-0000-0000-0000-0'
    '00000017ed1\\",\\"station_id\\":\\"00000000-0000-0000-0000-0000000163'
    '79\\",\\"tenant_id\\":\\"00000000-0000-0000-0000-00000001675f\\",\\"ver'
    'ified_at\\":\\"2026-01-20T00:00:00+00:00\\",\\"verified_by\\":\\"fixtur'
    'e-reviewer\\"}}",\n      "captured_at": "2026-01-20T00:00:00+00:00"'
    '\n    },\n    {\n      "fingerprint": "dffcb9a2f6127e96920b462ff0ac3'
    'd15122af972ced1c8602fa0c248c6628465",\n      "tenant_id": "0000000'
    '0-0000-0000-0000-00000001675f",\n      "station_id": "00000000-000'
    '0-0000-0000-000000016379",\n      "observation_id": "00000000-0000'
    '-0000-0000-0000000186a5",\n      "rating_curve_id": "00000000-0000'
    '-0000-0000-000000017ed1",\n      "feed_evidence_id": "00000000-000'
    '0-0000-0000-0000000182bd",\n      "reference_proof_id": "00000000-'
    '0000-0000-0000-000000017ed2",\n      "discharge": 10000.0,\n      "'
    'content": "{\\"conversion_version\\":\\"expired-rating-linear-refere'
    'nce-v1\\",\\"curve\\":{\\"created_at\\":\\"2026-01-09T23:00:00+00:00\\",'
    '\\"delivery_id\\":null,\\"id\\":\\"00000000-0000-0000-0000-000000017ed'
    '1\\",\\"interpolation\\":\\"linear\\",\\"points\\":[{\\"discharge\\":4.0,\\'
    '"water_level\\":1.0},{\\"discharge\\":10000.0,\\"water_level\\":2.0}],'
    '\\"rating_type_label\\":null,\\"station_id\\":\\"00000000-0000-0000-00'
    '00-000000016379\\",\\"uploaded_by\\":null,\\"valid_from\\":\\"2025-01-1'
    '0T00:00:00+00:00\\",\\"valid_to\\":\\"2026-01-09T00:00:00+00:00\\",\\"v'
    'ersion\\":1},\\"discharge\\":10000.0,\\"feed_evidence\\":{\\"api_statio'
    'n_id\\":91001,\\"endpoint\\":\\"https://example.invalid/nepal-qualifi'
    'cation-levels/\\",\\"evidence_reference\\":\\"nepal-readiness-isolati'
    'on-v1\\",\\"id\\":\\"00000000-0000-0000-0000-0000000182bd\\",\\"measure'
    'ment\\":{\\"content\\":\\"{\\\\\\"created_at\\\\\\":\\\\\\"2026-01-20T00:00:00'
    '+00:00\\\\\\",\\\\\\"delivery_id\\\\\\":null,\\\\\\"id\\\\\\":\\\\\\"00000000-00'
    '00-0000-0000-0000000186a5\\\\\\",\\\\\\"parameter\\\\\\":\\\\\\"water_level\\\\'
    '\\",\\\\\\"rating_curve_correction_version\\\\\\":null,\\\\\\"rating_curve_'
    'id\\\\\\":null,\\\\\\"source\\\\\\":\\\\\\"measured\\\\\\",\\\\\\"station_id\\\\'
    '\\":\\\\\\"00000000-0000-0000-0000-000000016379\\\\\\",\\\\\\"timestamp\\\\\\"'
    ':\\\\\\"2026-01-14T00:00:00+00:00\\\\\\",\\\\\\"value\\\\\\":2.0}\\"},\\"observ'
    'ation_id\\":\\"00000000-0000-0000-0000-0000000186a5\\",\\"station_id\\'
    '":\\"00000000-0000-0000-0000-000000016379\\",\\"tenant_id\\":\\"000000'
    '00-0000-0000-0000-00000001675f\\",\\"verified_at\\":\\"2026-01-20T00:'
    '00:00+00:00\\",\\"verified_by\\":\\"fixture-reviewer\\"},\\"measurement'
    '\\":{\\"created_at\\":\\"2026-01-20T00:00:00+00:00\\",\\"delivery_id\\":'
    'null,\\"id\\":\\"00000000-0000-0000-0000-0000000186a5\\",\\"parameter\\'
    '":\\"water_level\\",\\"rating_curve_correction_version\\":null,\\"rati'
    'ng_curve_id\\":null,\\"source\\":\\"measured\\",\\"station_id\\":\\"00000'
    '000-0000-0000-0000-000000016379\\",\\"timestamp\\":\\"2026-01-14T00:0'
    '0:00+00:00\\",\\"value\\":2.0},\\"qc\\":{\\"qc_flags\\":[{\\"detail\\":nul'
    'l,\\"rule_id\\":\\"range_check\\",\\"rule_version\\":\\"fixture-level-v1'
    '\\",\\"status\\":\\"qc_passed\\"}],\\"qc_rule_version\\":\\"fixture-level'
    '-v1\\",\\"qc_status\\":\\"qc_passed\\"},\\"reference_proof\\":{\\"api_sta'
    'tion_id\\":91001,\\"curve\\":{\\"content\\":\\"{\\\\\\"created_at\\\\\\":\\\\\\"'
    '2026-01-09T23:00:00+00:00\\\\\\",\\\\\\"delivery_id\\\\\\":null,\\\\\\"id\\\\\\"'
    ':\\\\\\"00000000-0000-0000-0000-000000017ed1\\\\\\",\\\\\\"interpolation\\\\'
    '\\":\\\\\\"linear\\\\\\",\\\\\\"points\\\\\\":[{\\\\\\"discharge\\\\\\":4.0,\\\\'
    '\\"water_level\\\\\\":1.0},{\\\\\\"discharge\\\\\\":10000.0,\\\\\\"water_level'
    '\\\\\\":2.0}],\\\\\\"rating_type_label\\\\\\":null,\\\\\\"station_id\\\\\\":\\\\'
    '\\"00000000-0000-0000-0000-000000016379\\\\\\",\\\\\\"uploaded_by\\\\\\":nu'
    'll,\\\\\\"valid_from\\\\\\":\\\\\\"2025-01-10T00:00:00+00:00\\\\\\",\\\\\\"valid'
    '_to\\\\\\":\\\\\\"2026-01-09T00:00:00+00:00\\\\\\",\\\\\\"version\\\\\\":1}\\"},'
    '\\"curve_reference\\":\\"gauge_zero\\",\\"curve_unit\\":\\"m\\",\\"endpoin'
    't\\":\\"https://example.invalid/nepal-qualification-levels/\\",\\"evi'
    'dence_reference\\":\\"nepal-readiness-isolation-v1\\",\\"id\\":\\"00000'
    '000-0000-0000-0000-000000017ed2\\",\\"level_reference\\":\\"gauge_zer'
    'o\\",\\"level_unit\\":\\"m\\",\\"offset_m\\":0.0,\\"rating_curve_id\\":\\"0'
    '0000000-0000-0000-0000-000000017ed1\\",\\"station_id\\":\\"00000000-0'
    '000-0000-0000-000000016379\\",\\"tenant_id\\":\\"00000000-0000-0000-0'
    '000-00000001675f\\",\\"verified_at\\":\\"2026-01-20T00:00:00+00:00\\",'
    '\\"verified_by\\":\\"fixture-reviewer\\"}}",\n      "captured_at": "20'
    '26-01-20T00:00:00+00:00"\n    },\n    {\n      "fingerprint": "3a9fb'
    '61c4cdffc7078a632df912d9eaa00fa46229a211e9e0d20e7825d3171a0",\n   '
    '   "tenant_id": "00000000-0000-0000-0000-00000001675f",\n      "st'
    'ation_id": "00000000-0000-0000-0000-000000016379",\n      "observa'
    'tion_id": "00000000-0000-0000-0000-0000000186a6",\n      "rating_c'
    'urve_id": "00000000-0000-0000-0000-000000017ed1",\n      "feed_evi'
    'dence_id": "00000000-0000-0000-0000-0000000182be",\n      "referen'
    'ce_proof_id": "00000000-0000-0000-0000-000000017ed2",\n      "disc'
    'harge": 10000.0,\n      "content": "{\\"conversion_version\\":\\"expi'
    'red-rating-linear-reference-v1\\",\\"curve\\":{\\"created_at\\":\\"2026'
    '-01-09T23:00:00+00:00\\",\\"delivery_id\\":null,\\"id\\":\\"00000000-00'
    '00-0000-0000-000000017ed1\\",\\"interpolation\\":\\"linear\\",\\"points'
    '\\":[{\\"discharge\\":4.0,\\"water_level\\":1.0},{\\"discharge\\":10000.'
    '0,\\"water_level\\":2.0}],\\"rating_type_label\\":null,\\"station_id\\"'
    ':\\"00000000-0000-0000-0000-000000016379\\",\\"uploaded_by\\":null,\\"'
    'valid_from\\":\\"2025-01-10T00:00:00+00:00\\",\\"valid_to\\":\\"2026-01'
    '-09T00:00:00+00:00\\",\\"version\\":1},\\"discharge\\":10000.0,\\"feed_'
    'evidence\\":{\\"api_station_id\\":91001,\\"endpoint\\":\\"https://examp'
    'le.invalid/nepal-qualification-levels/\\",\\"evidence_reference\\":\\'
    '"nepal-readiness-isolation-v1\\",\\"id\\":\\"00000000-0000-0000-0000-'
    '0000000182be\\",\\"measurement\\":{\\"content\\":\\"{\\\\\\"created_at\\\\\\"'
    ':\\\\\\"2026-01-20T00:00:00+00:00\\\\\\",\\\\\\"delivery_id\\\\\\":null,\\\\\\"i'
    'd\\\\\\":\\\\\\"00000000-0000-0000-0000-0000000186a6\\\\\\",\\\\\\"parameter\\'
    '\\\\":\\\\\\"water_level\\\\\\",\\\\\\"rating_curve_correction_version\\\\\\":n'
    'ull,\\\\\\"rating_curve_id\\\\\\":null,\\\\\\"source\\\\\\":\\\\\\"measured\\\\'
    '\\",\\\\\\"station_id\\\\\\":\\\\\\"00000000-0000-0000-0000-000000016379\\\\\\'
    '",\\\\\\"timestamp\\\\\\":\\\\\\"2026-01-15T00:00:00+00:00\\\\\\",\\\\\\"value\\'
    '\\\\":2.0}\\"},\\"observation_id\\":\\"00000000-0000-0000-0000-00000001'
    '86a6\\",\\"station_id\\":\\"00000000-0000-0000-0000-000000016379\\",\\"'
    'tenant_id\\":\\"00000000-0000-0000-0000-00000001675f\\",\\"verified_a'
    't\\":\\"2026-01-20T00:00:00+00:00\\",\\"verified_by\\":\\"fixture-revie'
    'wer\\"},\\"measurement\\":{\\"created_at\\":\\"2026-01-20T00:00:00+00:0'
    '0\\",\\"delivery_id\\":null,\\"id\\":\\"00000000-0000-0000-0000-0000000'
    '186a6\\",\\"parameter\\":\\"water_level\\",\\"rating_curve_correction_v'
    'ersion\\":null,\\"rating_curve_id\\":null,\\"source\\":\\"measured\\",\\"'
    'station_id\\":\\"00000000-0000-0000-0000-000000016379\\",\\"timestamp'
    '\\":\\"2026-01-15T00:00:00+00:00\\",\\"value\\":2.0},\\"qc\\":{\\"qc_flag'
    's\\":[{\\"detail\\":null,\\"rule_id\\":\\"range_check\\",\\"rule_version\\'
    '":\\"fixture-level-v1\\",\\"status\\":\\"qc_passed\\"}],\\"qc_rule_versi'
    'on\\":\\"fixture-level-v1\\",\\"qc_status\\":\\"qc_passed\\"},\\"referenc'
    'e_proof\\":{\\"api_station_id\\":91001,\\"curve\\":{\\"content\\":\\"{\\\\\\'
    '"created_at\\\\\\":\\\\\\"2026-01-09T23:00:00+00:00\\\\\\",\\\\\\"delivery_id'
    '\\\\\\":null,\\\\\\"id\\\\\\":\\\\\\"00000000-0000-0000-0000-000000017ed1\\\\\\"'
    ',\\\\\\"interpolation\\\\\\":\\\\\\"linear\\\\\\",\\\\\\"points\\\\\\":[{\\\\\\"'
    'discharge\\\\\\":4.0,\\\\\\"water_level\\\\\\":1.0},{\\\\\\"discharge\\\\\\":100'
    '00.0,\\\\\\"water_level\\\\\\":2.0}],\\\\\\"rating_type_label\\\\\\":null,\\\\\\'
    '"station_id\\\\\\":\\\\\\"00000000-0000-0000-0000-000000016379\\\\\\",\\\\\\"'
    'uploaded_by\\\\\\":null,\\\\\\"valid_from\\\\\\":\\\\\\"2025-01-10T00:00:00+0'
    '0:00\\\\\\",\\\\\\"valid_to\\\\\\":\\\\\\"2026-01-09T00:00:00+00:00\\\\\\",\\\\'
    '\\"version\\\\\\":1}\\"},\\"curve_reference\\":\\"gauge_zero\\",\\"curve_un'
    'it\\":\\"m\\",\\"endpoint\\":\\"https://example.invalid/nepal-qualifica'
    'tion-levels/\\",\\"evidence_reference\\":\\"nepal-readiness-isolation'
    '-v1\\",\\"id\\":\\"00000000-0000-0000-0000-000000017ed2\\",\\"level_ref'
    'erence\\":\\"gauge_zero\\",\\"level_unit\\":\\"m\\",\\"offset_m\\":0.0,\\"r'
    'ating_curve_id\\":\\"00000000-0000-0000-0000-000000017ed1\\",\\"stati'
    'on_id\\":\\"00000000-0000-0000-0000-000000016379\\",\\"tenant_id\\":\\"'
    '00000000-0000-0000-0000-00000001675f\\",\\"verified_at\\":\\"2026-01-'
    '20T00:00:00+00:00\\",\\"verified_by\\":\\"fixture-reviewer\\"}}",\n    '
    '  "captured_at": "2026-01-20T00:00:00+00:00"\n    },\n    {\n      "'
    'fingerprint": "02e7760a1aecd9d3e8d99f4b555407bb1edfedc065642ab865'
    '8430c43646365d",\n      "tenant_id": "00000000-0000-0000-0000-0000'
    '0001675f",\n      "station_id": "00000000-0000-0000-0000-000000016'
    '379",\n      "observation_id": "00000000-0000-0000-0000-0000000186'
    'a7",\n      "rating_curve_id": "00000000-0000-0000-0000-000000017e'
    'd1",\n      "feed_evidence_id": "00000000-0000-0000-0000-000000018'
    '2bf",\n      "reference_proof_id": "00000000-0000-0000-0000-000000'
    '017ed2",\n      "discharge": 10000.0,\n      "content": "{\\"convers'
    'ion_version\\":\\"expired-rating-linear-reference-v1\\",\\"curve\\":{\\'
    '"created_at\\":\\"2026-01-09T23:00:00+00:00\\",\\"delivery_id\\":null,'
    '\\"id\\":\\"00000000-0000-0000-0000-000000017ed1\\",\\"interpolation\\"'
    ':\\"linear\\",\\"points\\":[{\\"discharge\\":4.0,\\"water_level\\":1.0},{'
    '\\"discharge\\":10000.0,\\"water_level\\":2.0}],\\"rating_type_label\\"'
    ':null,\\"station_id\\":\\"00000000-0000-0000-0000-000000016379\\",\\"u'
    'ploaded_by\\":null,\\"valid_from\\":\\"2025-01-10T00:00:00+00:00\\",\\"'
    'valid_to\\":\\"2026-01-09T00:00:00+00:00\\",\\"version\\":1},\\"dischar'
    'ge\\":10000.0,\\"feed_evidence\\":{\\"api_station_id\\":91001,\\"endpoi'
    'nt\\":\\"https://example.invalid/nepal-qualification-levels/\\",\\"ev'
    'idence_reference\\":\\"nepal-readiness-isolation-v1\\",\\"id\\":\\"0000'
    '0000-0000-0000-0000-0000000182bf\\",\\"measurement\\":{\\"content\\":\\'
    '"{\\\\\\"created_at\\\\\\":\\\\\\"2026-01-20T00:00:00+00:00\\\\\\",\\\\\\"delive'
    'ry_id\\\\\\":null,\\\\\\"id\\\\\\":\\\\\\"00000000-0000-0000-0000-0000000186a'
    '7\\\\\\",\\\\\\"parameter\\\\\\":\\\\\\"water_level\\\\\\",\\\\\\"rating_curve_c'
    'orrection_version\\\\\\":null,\\\\\\"rating_curve_id\\\\\\":null,\\\\\\"sourc'
    'e\\\\\\":\\\\\\"measured\\\\\\",\\\\\\"station_id\\\\\\":\\\\\\"00000000-0000-00'
    '00-0000-000000016379\\\\\\",\\\\\\"timestamp\\\\\\":\\\\\\"2026-01-16T00:00:0'
    '0+00:00\\\\\\",\\\\\\"value\\\\\\":2.0}\\"},\\"observation_id\\":\\"00000000-0'
    '000-0000-0000-0000000186a7\\",\\"station_id\\":\\"00000000-0000-0000-'
    '0000-000000016379\\",\\"tenant_id\\":\\"00000000-0000-0000-0000-00000'
    '001675f\\",\\"verified_at\\":\\"2026-01-20T00:00:00+00:00\\",\\"verifie'
    'd_by\\":\\"fixture-reviewer\\"},\\"measurement\\":{\\"created_at\\":\\"20'
    '26-01-20T00:00:00+00:00\\",\\"delivery_id\\":null,\\"id\\":\\"00000000-'
    '0000-0000-0000-0000000186a7\\",\\"parameter\\":\\"water_level\\",\\"rat'
    'ing_curve_correction_version\\":null,\\"rating_curve_id\\":null,\\"so'
    'urce\\":\\"measured\\",\\"station_id\\":\\"00000000-0000-0000-0000-0000'
    '00016379\\",\\"timestamp\\":\\"2026-01-16T00:00:00+00:00\\",\\"value\\":'
    '2.0},\\"qc\\":{\\"qc_flags\\":[{\\"detail\\":null,\\"rule_id\\":\\"range_c'
    'heck\\",\\"rule_version\\":\\"fixture-level-v1\\",\\"status\\":\\"qc_pass'
    'ed\\"}],\\"qc_rule_version\\":\\"fixture-level-v1\\",\\"qc_status\\":\\"q'
    'c_passed\\"},\\"reference_proof\\":{\\"api_station_id\\":91001,\\"curve'
    '\\":{\\"content\\":\\"{\\\\\\"created_at\\\\\\":\\\\\\"2026-01-09T23:00:00+00:'
    '00\\\\\\",\\\\\\"delivery_id\\\\\\":null,\\\\\\"id\\\\\\":\\\\\\"00000000-0000-0'
    '000-0000-000000017ed1\\\\\\",\\\\\\"interpolation\\\\\\":\\\\\\"linear\\\\\\",\\'
    '\\\\"points\\\\\\":[{\\\\\\"discharge\\\\\\":4.0,\\\\\\"water_level\\\\\\":1.0},'
    '{\\\\\\"discharge\\\\\\":10000.0,\\\\\\"water_level\\\\\\":2.0}],\\\\\\"rating_t'
    'ype_label\\\\\\":null,\\\\\\"station_id\\\\\\":\\\\\\"00000000-0000-0000-0000'
    '-000000016379\\\\\\",\\\\\\"uploaded_by\\\\\\":null,\\\\\\"valid_from\\\\\\":\\'
    '\\\\"2025-01-10T00:00:00+00:00\\\\\\",\\\\\\"valid_to\\\\\\":\\\\\\"2026-01-09T'
    '00:00:00+00:00\\\\\\",\\\\\\"version\\\\\\":1}\\"},\\"curve_reference\\":\\"ga'
    'uge_zero\\",\\"curve_unit\\":\\"m\\",\\"endpoint\\":\\"https://example.in'
    'valid/nepal-qualification-levels/\\",\\"evidence_reference\\":\\"nepa'
    'l-readiness-isolation-v1\\",\\"id\\":\\"00000000-0000-0000-0000-00000'
    '0017ed2\\",\\"level_reference\\":\\"gauge_zero\\",\\"level_unit\\":\\"m\\"'
    ',\\"offset_m\\":0.0,\\"rating_curve_id\\":\\"00000000-0000-0000-0000-0'
    '00000017ed1\\",\\"station_id\\":\\"00000000-0000-0000-0000-0000000163'
    '79\\",\\"tenant_id\\":\\"00000000-0000-0000-0000-00000001675f\\",\\"ver'
    'ified_at\\":\\"2026-01-20T00:00:00+00:00\\",\\"verified_by\\":\\"fixtur'
    'e-reviewer\\"}}",\n      "captured_at": "2026-01-20T00:00:00+00:00"'
    '\n    },\n    {\n      "fingerprint": "e3708317ba25bba7be9adb661e426'
    '20edcd3f922323386fffdaed9ab2fb8558b",\n      "tenant_id": "0000000'
    '0-0000-0000-0000-00000001675f",\n      "station_id": "00000000-000'
    '0-0000-0000-000000016379",\n      "observation_id": "00000000-0000'
    '-0000-0000-0000000186a8",\n      "rating_curve_id": "00000000-0000'
    '-0000-0000-000000017ed1",\n      "feed_evidence_id": "00000000-000'
    '0-0000-0000-0000000182c0",\n      "reference_proof_id": "00000000-'
    '0000-0000-0000-000000017ed2",\n      "discharge": 10000.0,\n      "'
    'content": "{\\"conversion_version\\":\\"expired-rating-linear-refere'
    'nce-v1\\",\\"curve\\":{\\"created_at\\":\\"2026-01-09T23:00:00+00:00\\",'
    '\\"delivery_id\\":null,\\"id\\":\\"00000000-0000-0000-0000-000000017ed'
    '1\\",\\"interpolation\\":\\"linear\\",\\"points\\":[{\\"discharge\\":4.0,\\'
    '"water_level\\":1.0},{\\"discharge\\":10000.0,\\"water_level\\":2.0}],'
    '\\"rating_type_label\\":null,\\"station_id\\":\\"00000000-0000-0000-00'
    '00-000000016379\\",\\"uploaded_by\\":null,\\"valid_from\\":\\"2025-01-1'
    '0T00:00:00+00:00\\",\\"valid_to\\":\\"2026-01-09T00:00:00+00:00\\",\\"v'
    'ersion\\":1},\\"discharge\\":10000.0,\\"feed_evidence\\":{\\"api_statio'
    'n_id\\":91001,\\"endpoint\\":\\"https://example.invalid/nepal-qualifi'
    'cation-levels/\\",\\"evidence_reference\\":\\"nepal-readiness-isolati'
    'on-v1\\",\\"id\\":\\"00000000-0000-0000-0000-0000000182c0\\",\\"measure'
    'ment\\":{\\"content\\":\\"{\\\\\\"created_at\\\\\\":\\\\\\"2026-01-20T00:00:00'
    '+00:00\\\\\\",\\\\\\"delivery_id\\\\\\":null,\\\\\\"id\\\\\\":\\\\\\"00000000-00'
    '00-0000-0000-0000000186a8\\\\\\",\\\\\\"parameter\\\\\\":\\\\\\"water_level\\\\'
    '\\",\\\\\\"rating_curve_correction_version\\\\\\":null,\\\\\\"rating_curve_'
    'id\\\\\\":null,\\\\\\"source\\\\\\":\\\\\\"measured\\\\\\",\\\\\\"station_id\\\\'
    '\\":\\\\\\"00000000-0000-0000-0000-000000016379\\\\\\",\\\\\\"timestamp\\\\\\"'
    ':\\\\\\"2026-01-17T00:00:00+00:00\\\\\\",\\\\\\"value\\\\\\":2.0}\\"},\\"observ'
    'ation_id\\":\\"00000000-0000-0000-0000-0000000186a8\\",\\"station_id\\'
    '":\\"00000000-0000-0000-0000-000000016379\\",\\"tenant_id\\":\\"000000'
    '00-0000-0000-0000-00000001675f\\",\\"verified_at\\":\\"2026-01-20T00:'
    '00:00+00:00\\",\\"verified_by\\":\\"fixture-reviewer\\"},\\"measurement'
    '\\":{\\"created_at\\":\\"2026-01-20T00:00:00+00:00\\",\\"delivery_id\\":'
    'null,\\"id\\":\\"00000000-0000-0000-0000-0000000186a8\\",\\"parameter\\'
    '":\\"water_level\\",\\"rating_curve_correction_version\\":null,\\"rati'
    'ng_curve_id\\":null,\\"source\\":\\"measured\\",\\"station_id\\":\\"00000'
    '000-0000-0000-0000-000000016379\\",\\"timestamp\\":\\"2026-01-17T00:0'
    '0:00+00:00\\",\\"value\\":2.0},\\"qc\\":{\\"qc_flags\\":[{\\"detail\\":nul'
    'l,\\"rule_id\\":\\"range_check\\",\\"rule_version\\":\\"fixture-level-v1'
    '\\",\\"status\\":\\"qc_passed\\"}],\\"qc_rule_version\\":\\"fixture-level'
    '-v1\\",\\"qc_status\\":\\"qc_passed\\"},\\"reference_proof\\":{\\"api_sta'
    'tion_id\\":91001,\\"curve\\":{\\"content\\":\\"{\\\\\\"created_at\\\\\\":\\\\\\"'
    '2026-01-09T23:00:00+00:00\\\\\\",\\\\\\"delivery_id\\\\\\":null,\\\\\\"id\\\\\\"'
    ':\\\\\\"00000000-0000-0000-0000-000000017ed1\\\\\\",\\\\\\"interpolation\\\\'
    '\\":\\\\\\"linear\\\\\\",\\\\\\"points\\\\\\":[{\\\\\\"discharge\\\\\\":4.0,\\\\'
    '\\"water_level\\\\\\":1.0},{\\\\\\"discharge\\\\\\":10000.0,\\\\\\"water_level'
    '\\\\\\":2.0}],\\\\\\"rating_type_label\\\\\\":null,\\\\\\"station_id\\\\\\":\\\\'
    '\\"00000000-0000-0000-0000-000000016379\\\\\\",\\\\\\"uploaded_by\\\\\\":nu'
    'll,\\\\\\"valid_from\\\\\\":\\\\\\"2025-01-10T00:00:00+00:00\\\\\\",\\\\\\"valid'
    '_to\\\\\\":\\\\\\"2026-01-09T00:00:00+00:00\\\\\\",\\\\\\"version\\\\\\":1}\\"},'
    '\\"curve_reference\\":\\"gauge_zero\\",\\"curve_unit\\":\\"m\\",\\"endpoin'
    't\\":\\"https://example.invalid/nepal-qualification-levels/\\",\\"evi'
    'dence_reference\\":\\"nepal-readiness-isolation-v1\\",\\"id\\":\\"00000'
    '000-0000-0000-0000-000000017ed2\\",\\"level_reference\\":\\"gauge_zer'
    'o\\",\\"level_unit\\":\\"m\\",\\"offset_m\\":0.0,\\"rating_curve_id\\":\\"0'
    '0000000-0000-0000-0000-000000017ed1\\",\\"station_id\\":\\"00000000-0'
    '000-0000-0000-000000016379\\",\\"tenant_id\\":\\"00000000-0000-0000-0'
    '000-00000001675f\\",\\"verified_at\\":\\"2026-01-20T00:00:00+00:00\\",'
    '\\"verified_by\\":\\"fixture-reviewer\\"}}",\n      "captured_at": "20'
    '26-01-20T00:00:00+00:00"\n    },\n    {\n      "fingerprint": "d378f'
    'c4d462d2bd01c9f3e36cca6a96a1b1fc6fd0e8a0fca52549115225a3f06",\n   '
    '   "tenant_id": "00000000-0000-0000-0000-00000001675f",\n      "st'
    'ation_id": "00000000-0000-0000-0000-000000016379",\n      "observa'
    'tion_id": "00000000-0000-0000-0000-0000000186a9",\n      "rating_c'
    'urve_id": "00000000-0000-0000-0000-000000017ed1",\n      "feed_evi'
    'dence_id": "00000000-0000-0000-0000-0000000182c1",\n      "referen'
    'ce_proof_id": "00000000-0000-0000-0000-000000017ed2",\n      "disc'
    'harge": 10000.0,\n      "content": "{\\"conversion_version\\":\\"expi'
    'red-rating-linear-reference-v1\\",\\"curve\\":{\\"created_at\\":\\"2026'
    '-01-09T23:00:00+00:00\\",\\"delivery_id\\":null,\\"id\\":\\"00000000-00'
    '00-0000-0000-000000017ed1\\",\\"interpolation\\":\\"linear\\",\\"points'
    '\\":[{\\"discharge\\":4.0,\\"water_level\\":1.0},{\\"discharge\\":10000.'
    '0,\\"water_level\\":2.0}],\\"rating_type_label\\":null,\\"station_id\\"'
    ':\\"00000000-0000-0000-0000-000000016379\\",\\"uploaded_by\\":null,\\"'
    'valid_from\\":\\"2025-01-10T00:00:00+00:00\\",\\"valid_to\\":\\"2026-01'
    '-09T00:00:00+00:00\\",\\"version\\":1},\\"discharge\\":10000.0,\\"feed_'
    'evidence\\":{\\"api_station_id\\":91001,\\"endpoint\\":\\"https://examp'
    'le.invalid/nepal-qualification-levels/\\",\\"evidence_reference\\":\\'
    '"nepal-readiness-isolation-v1\\",\\"id\\":\\"00000000-0000-0000-0000-'
    '0000000182c1\\",\\"measurement\\":{\\"content\\":\\"{\\\\\\"created_at\\\\\\"'
    ':\\\\\\"2026-01-20T00:00:00+00:00\\\\\\",\\\\\\"delivery_id\\\\\\":null,\\\\\\"i'
    'd\\\\\\":\\\\\\"00000000-0000-0000-0000-0000000186a9\\\\\\",\\\\\\"parameter\\'
    '\\\\":\\\\\\"water_level\\\\\\",\\\\\\"rating_curve_correction_version\\\\\\":n'
    'ull,\\\\\\"rating_curve_id\\\\\\":null,\\\\\\"source\\\\\\":\\\\\\"measured\\\\'
    '\\",\\\\\\"station_id\\\\\\":\\\\\\"00000000-0000-0000-0000-000000016379\\\\\\'
    '",\\\\\\"timestamp\\\\\\":\\\\\\"2026-01-18T00:00:00+00:00\\\\\\",\\\\\\"value\\'
    '\\\\":2.0}\\"},\\"observation_id\\":\\"00000000-0000-0000-0000-00000001'
    '86a9\\",\\"station_id\\":\\"00000000-0000-0000-0000-000000016379\\",\\"'
    'tenant_id\\":\\"00000000-0000-0000-0000-00000001675f\\",\\"verified_a'
    't\\":\\"2026-01-20T00:00:00+00:00\\",\\"verified_by\\":\\"fixture-revie'
    'wer\\"},\\"measurement\\":{\\"created_at\\":\\"2026-01-20T00:00:00+00:0'
    '0\\",\\"delivery_id\\":null,\\"id\\":\\"00000000-0000-0000-0000-0000000'
    '186a9\\",\\"parameter\\":\\"water_level\\",\\"rating_curve_correction_v'
    'ersion\\":null,\\"rating_curve_id\\":null,\\"source\\":\\"measured\\",\\"'
    'station_id\\":\\"00000000-0000-0000-0000-000000016379\\",\\"timestamp'
    '\\":\\"2026-01-18T00:00:00+00:00\\",\\"value\\":2.0},\\"qc\\":{\\"qc_flag'
    's\\":[{\\"detail\\":null,\\"rule_id\\":\\"range_check\\",\\"rule_version\\'
    '":\\"fixture-level-v1\\",\\"status\\":\\"qc_passed\\"}],\\"qc_rule_versi'
    'on\\":\\"fixture-level-v1\\",\\"qc_status\\":\\"qc_passed\\"},\\"referenc'
    'e_proof\\":{\\"api_station_id\\":91001,\\"curve\\":{\\"content\\":\\"{\\\\\\'
    '"created_at\\\\\\":\\\\\\"2026-01-09T23:00:00+00:00\\\\\\",\\\\\\"delivery_id'
    '\\\\\\":null,\\\\\\"id\\\\\\":\\\\\\"00000000-0000-0000-0000-000000017ed1\\\\\\"'
    ',\\\\\\"interpolation\\\\\\":\\\\\\"linear\\\\\\",\\\\\\"points\\\\\\":[{\\\\\\"'
    'discharge\\\\\\":4.0,\\\\\\"water_level\\\\\\":1.0},{\\\\\\"discharge\\\\\\":100'
    '00.0,\\\\\\"water_level\\\\\\":2.0}],\\\\\\"rating_type_label\\\\\\":null,\\\\\\'
    '"station_id\\\\\\":\\\\\\"00000000-0000-0000-0000-000000016379\\\\\\",\\\\\\"'
    'uploaded_by\\\\\\":null,\\\\\\"valid_from\\\\\\":\\\\\\"2025-01-10T00:00:00+0'
    '0:00\\\\\\",\\\\\\"valid_to\\\\\\":\\\\\\"2026-01-09T00:00:00+00:00\\\\\\",\\\\'
    '\\"version\\\\\\":1}\\"},\\"curve_reference\\":\\"gauge_zero\\",\\"curve_un'
    'it\\":\\"m\\",\\"endpoint\\":\\"https://example.invalid/nepal-qualifica'
    'tion-levels/\\",\\"evidence_reference\\":\\"nepal-readiness-isolation'
    '-v1\\",\\"id\\":\\"00000000-0000-0000-0000-000000017ed2\\",\\"level_ref'
    'erence\\":\\"gauge_zero\\",\\"level_unit\\":\\"m\\",\\"offset_m\\":0.0,\\"r'
    'ating_curve_id\\":\\"00000000-0000-0000-0000-000000017ed1\\",\\"stati'
    'on_id\\":\\"00000000-0000-0000-0000-000000016379\\",\\"tenant_id\\":\\"'
    '00000000-0000-0000-0000-00000001675f\\",\\"verified_at\\":\\"2026-01-'
    '20T00:00:00+00:00\\",\\"verified_by\\":\\"fixture-reviewer\\"}}",\n    '
    '  "captured_at": "2026-01-20T00:00:00+00:00"\n    },\n    {\n      "'
    'fingerprint": "a2fb8e48a70c9f7ead0101cd25e9c8613d2a0d801937382d5f'
    '2c0735dae85a76",\n      "tenant_id": "00000000-0000-0000-0000-0000'
    '0001675f",\n      "station_id": "00000000-0000-0000-0000-000000016'
    '379",\n      "observation_id": "00000000-0000-0000-0000-0000000186'
    'aa",\n      "rating_curve_id": "00000000-0000-0000-0000-000000017e'
    'd1",\n      "feed_evidence_id": "00000000-0000-0000-0000-000000018'
    '2c2",\n      "reference_proof_id": "00000000-0000-0000-0000-000000'
    '017ed2",\n      "discharge": 10000.0,\n      "content": "{\\"convers'
    'ion_version\\":\\"expired-rating-linear-reference-v1\\",\\"curve\\":{\\'
    '"created_at\\":\\"2026-01-09T23:00:00+00:00\\",\\"delivery_id\\":null,'
    '\\"id\\":\\"00000000-0000-0000-0000-000000017ed1\\",\\"interpolation\\"'
    ':\\"linear\\",\\"points\\":[{\\"discharge\\":4.0,\\"water_level\\":1.0},{'
    '\\"discharge\\":10000.0,\\"water_level\\":2.0}],\\"rating_type_label\\"'
    ':null,\\"station_id\\":\\"00000000-0000-0000-0000-000000016379\\",\\"u'
    'ploaded_by\\":null,\\"valid_from\\":\\"2025-01-10T00:00:00+00:00\\",\\"'
    'valid_to\\":\\"2026-01-09T00:00:00+00:00\\",\\"version\\":1},\\"dischar'
    'ge\\":10000.0,\\"feed_evidence\\":{\\"api_station_id\\":91001,\\"endpoi'
    'nt\\":\\"https://example.invalid/nepal-qualification-levels/\\",\\"ev'
    'idence_reference\\":\\"nepal-readiness-isolation-v1\\",\\"id\\":\\"0000'
    '0000-0000-0000-0000-0000000182c2\\",\\"measurement\\":{\\"content\\":\\'
    '"{\\\\\\"created_at\\\\\\":\\\\\\"2026-01-20T00:00:00+00:00\\\\\\",\\\\\\"delive'
    'ry_id\\\\\\":null,\\\\\\"id\\\\\\":\\\\\\"00000000-0000-0000-0000-0000000186a'
    'a\\\\\\",\\\\\\"parameter\\\\\\":\\\\\\"water_level\\\\\\",\\\\\\"rating_curve_c'
    'orrection_version\\\\\\":null,\\\\\\"rating_curve_id\\\\\\":null,\\\\\\"sourc'
    'e\\\\\\":\\\\\\"measured\\\\\\",\\\\\\"station_id\\\\\\":\\\\\\"00000000-0000-00'
    '00-0000-000000016379\\\\\\",\\\\\\"timestamp\\\\\\":\\\\\\"2026-01-19T00:00:0'
    '0+00:00\\\\\\",\\\\\\"value\\\\\\":2.0}\\"},\\"observation_id\\":\\"00000000-0'
    '000-0000-0000-0000000186aa\\",\\"station_id\\":\\"00000000-0000-0000-'
    '0000-000000016379\\",\\"tenant_id\\":\\"00000000-0000-0000-0000-00000'
    '001675f\\",\\"verified_at\\":\\"2026-01-20T00:00:00+00:00\\",\\"verifie'
    'd_by\\":\\"fixture-reviewer\\"},\\"measurement\\":{\\"created_at\\":\\"20'
    '26-01-20T00:00:00+00:00\\",\\"delivery_id\\":null,\\"id\\":\\"00000000-'
    '0000-0000-0000-0000000186aa\\",\\"parameter\\":\\"water_level\\",\\"rat'
    'ing_curve_correction_version\\":null,\\"rating_curve_id\\":null,\\"so'
    'urce\\":\\"measured\\",\\"station_id\\":\\"00000000-0000-0000-0000-0000'
    '00016379\\",\\"timestamp\\":\\"2026-01-19T00:00:00+00:00\\",\\"value\\":'
    '2.0},\\"qc\\":{\\"qc_flags\\":[{\\"detail\\":null,\\"rule_id\\":\\"range_c'
    'heck\\",\\"rule_version\\":\\"fixture-level-v1\\",\\"status\\":\\"qc_pass'
    'ed\\"}],\\"qc_rule_version\\":\\"fixture-level-v1\\",\\"qc_status\\":\\"q'
    'c_passed\\"},\\"reference_proof\\":{\\"api_station_id\\":91001,\\"curve'
    '\\":{\\"content\\":\\"{\\\\\\"created_at\\\\\\":\\\\\\"2026-01-09T23:00:00+00:'
    '00\\\\\\",\\\\\\"delivery_id\\\\\\":null,\\\\\\"id\\\\\\":\\\\\\"00000000-0000-0'
    '000-0000-000000017ed1\\\\\\",\\\\\\"interpolation\\\\\\":\\\\\\"linear\\\\\\",\\'
    '\\\\"points\\\\\\":[{\\\\\\"discharge\\\\\\":4.0,\\\\\\"water_level\\\\\\":1.0},'
    '{\\\\\\"discharge\\\\\\":10000.0,\\\\\\"water_level\\\\\\":2.0}],\\\\\\"rating_t'
    'ype_label\\\\\\":null,\\\\\\"station_id\\\\\\":\\\\\\"00000000-0000-0000-0000'
    '-000000016379\\\\\\",\\\\\\"uploaded_by\\\\\\":null,\\\\\\"valid_from\\\\\\":\\'
    '\\\\"2025-01-10T00:00:00+00:00\\\\\\",\\\\\\"valid_to\\\\\\":\\\\\\"2026-01-09T'
    '00:00:00+00:00\\\\\\",\\\\\\"version\\\\\\":1}\\"},\\"curve_reference\\":\\"ga'
    'uge_zero\\",\\"curve_unit\\":\\"m\\",\\"endpoint\\":\\"https://example.in'
    'valid/nepal-qualification-levels/\\",\\"evidence_reference\\":\\"nepa'
    'l-readiness-isolation-v1\\",\\"id\\":\\"00000000-0000-0000-0000-00000'
    '0017ed2\\",\\"level_reference\\":\\"gauge_zero\\",\\"level_unit\\":\\"m\\"'
    ',\\"offset_m\\":0.0,\\"rating_curve_id\\":\\"00000000-0000-0000-0000-0'
    '00000017ed1\\",\\"station_id\\":\\"00000000-0000-0000-0000-0000000163'
    '79\\",\\"tenant_id\\":\\"00000000-0000-0000-0000-00000001675f\\",\\"ver'
    'ified_at\\":\\"2026-01-20T00:00:00+00:00\\",\\"verified_by\\":\\"fixtur'
    'e-reviewer\\"}}",\n      "captured_at": "2026-01-20T00:00:00+00:00"'
    '\n    }\n  ],\n  "reference_content": "{\\"api_station_id\\":91001,\\"c'
    'urve\\":{\\"content\\":\\"{\\\\\\"created_at\\\\\\":\\\\\\"2026-01-09T23:00:00'
    '+00:00\\\\\\",\\\\\\"delivery_id\\\\\\":null,\\\\\\"id\\\\\\":\\\\\\"00000000-00'
    '00-0000-0000-000000017ed1\\\\\\",\\\\\\"interpolation\\\\\\":\\\\\\"linear\\\\\\'
    '",\\\\\\"points\\\\\\":[{\\\\\\"discharge\\\\\\":4.0,\\\\\\"water_level\\\\\\":1'
    '.0},{\\\\\\"discharge\\\\\\":10000.0,\\\\\\"water_level\\\\\\":2.0}],\\\\\\"rati'
    'ng_type_label\\\\\\":null,\\\\\\"station_id\\\\\\":\\\\\\"00000000-0000-0000-'
    '0000-000000016379\\\\\\",\\\\\\"uploaded_by\\\\\\":null,\\\\\\"valid_from\\\\\\"'
    ':\\\\\\"2025-01-10T00:00:00+00:00\\\\\\",\\\\\\"valid_to\\\\\\":\\\\\\"2026-01-0'
    '9T00:00:00+00:00\\\\\\",\\\\\\"version\\\\\\":1}\\"},\\"curve_reference\\":\\"'
    'gauge_zero\\",\\"curve_unit\\":\\"m\\",\\"endpoint\\":\\"https://example.'
    'invalid/nepal-qualification-levels/\\",\\"evidence_reference\\":\\"ne'
    'pal-readiness-isolation-v1\\",\\"id\\":\\"00000000-0000-0000-0000-000'
    '000017ed2\\",\\"level_reference\\":\\"gauge_zero\\",\\"level_unit\\":\\"m'
    '\\",\\"offset_m\\":0.0,\\"rating_curve_id\\":\\"00000000-0000-0000-0000'
    '-000000017ed1\\",\\"station_id\\":\\"00000000-0000-0000-0000-00000001'
    '6379\\",\\"tenant_id\\":\\"00000000-0000-0000-0000-00000001675f\\",\\"v'
    'erified_at\\":\\"2026-01-20T00:00:00+00:00\\",\\"verified_by\\":\\"fixt'
    'ure-reviewer\\"}",\n  "reference_fingerprint": "f0619d4efecba824862'
    '94212dbee1e7e5e23347dc222cf59151c09d7189e17de",\n  "feed_contents"'
    ': [\n    {\n      "content": "{\\"api_station_id\\":91001,\\"endpoint\\'
    '":\\"https://example.invalid/nepal-qualification-levels/\\",\\"evide'
    'nce_reference\\":\\"nepal-readiness-isolation-v1\\",\\"id\\":\\"0000000'
    '0-0000-0000-0000-0000000182b9\\",\\"measurement\\":{\\"content\\":\\"{\\'
    '\\\\"created_at\\\\\\":\\\\\\"2026-01-20T00:00:00+00:00\\\\\\",\\\\\\"delivery_'
    'id\\\\\\":null,\\\\\\"id\\\\\\":\\\\\\"00000000-0000-0000-0000-0000000186a1\\\\'
    '\\",\\\\\\"parameter\\\\\\":\\\\\\"water_level\\\\\\",\\\\\\"rating_curve_correc'
    'tion_version\\\\\\":null,\\\\\\"rating_curve_id\\\\\\":null,\\\\\\"source\\\\\\"'
    ':\\\\\\"measured\\\\\\",\\\\\\"station_id\\\\\\":\\\\\\"00000000-0000-0000-0000-'
    '000000016379\\\\\\",\\\\\\"timestamp\\\\\\":\\\\\\"2026-01-10T00:00:00+00:00\\'
    '\\\\",\\\\\\"value\\\\\\":2.0}\\"},\\"observation_id\\":\\"00000000-0000-0000'
    '-0000-0000000186a1\\",\\"station_id\\":\\"00000000-0000-0000-0000-000'
    '000016379\\",\\"tenant_id\\":\\"00000000-0000-0000-0000-00000001675f\\'
    '",\\"verified_at\\":\\"2026-01-20T00:00:00+00:00\\",\\"verified_by\\":\\'
    '"fixture-reviewer\\"}",\n      "fingerprint": "edd828dd40d64b35a5b4'
    '44e8e6c36aa55671fc4c790c77eaaa615e6260dc9b5e"\n    },\n    {\n      '
    '"content": "{\\"api_station_id\\":91001,\\"endpoint\\":\\"https://exam'
    'ple.invalid/nepal-qualification-levels/\\",\\"evidence_reference\\":'
    '\\"nepal-readiness-isolation-v1\\",\\"id\\":\\"00000000-0000-0000-0000'
    '-0000000182ba\\",\\"measurement\\":{\\"content\\":\\"{\\\\\\"created_at\\\\\\'
    '":\\\\\\"2026-01-20T00:00:00+00:00\\\\\\",\\\\\\"delivery_id\\\\\\":null,\\\\\\"'
    'id\\\\\\":\\\\\\"00000000-0000-0000-0000-0000000186a2\\\\\\",\\\\\\"parameter'
    '\\\\\\":\\\\\\"water_level\\\\\\",\\\\\\"rating_curve_correction_version\\\\\\":'
    'null,\\\\\\"rating_curve_id\\\\\\":null,\\\\\\"source\\\\\\":\\\\\\"measured\\\\'
    '\\",\\\\\\"station_id\\\\\\":\\\\\\"00000000-0000-0000-0000-000000016379\\\\\\'
    '",\\\\\\"timestamp\\\\\\":\\\\\\"2026-01-11T00:00:00+00:00\\\\\\",\\\\\\"value\\'
    '\\\\":2.0}\\"},\\"observation_id\\":\\"00000000-0000-0000-0000-00000001'
    '86a2\\",\\"station_id\\":\\"00000000-0000-0000-0000-000000016379\\",\\"'
    'tenant_id\\":\\"00000000-0000-0000-0000-00000001675f\\",\\"verified_a'
    't\\":\\"2026-01-20T00:00:00+00:00\\",\\"verified_by\\":\\"fixture-revie'
    'wer\\"}",\n      "fingerprint": "a513d2d907216c4f5db6e8bf444e7f3f80'
    'dc91e77281488109e0707b11454a63"\n    },\n    {\n      "content": "{\\'
    '"api_station_id\\":91001,\\"endpoint\\":\\"https://example.invalid/ne'
    'pal-qualification-levels/\\",\\"evidence_reference\\":\\"nepal-readin'
    'ess-isolation-v1\\",\\"id\\":\\"00000000-0000-0000-0000-0000000182bb\\'
    '",\\"measurement\\":{\\"content\\":\\"{\\\\\\"created_at\\\\\\":\\\\\\"2026-01-'
    '20T00:00:00+00:00\\\\\\",\\\\\\"delivery_id\\\\\\":null,\\\\\\"id\\\\\\":\\\\\\"'
    '00000000-0000-0000-0000-0000000186a3\\\\\\",\\\\\\"parameter\\\\\\":\\\\\\"wa'
    'ter_level\\\\\\",\\\\\\"rating_curve_correction_version\\\\\\":null,\\\\\\"ra'
    'ting_curve_id\\\\\\":null,\\\\\\"source\\\\\\":\\\\\\"measured\\\\\\",\\\\\\"sta'
    'tion_id\\\\\\":\\\\\\"00000000-0000-0000-0000-000000016379\\\\\\",\\\\\\"time'
    'stamp\\\\\\":\\\\\\"2026-01-12T00:00:00+00:00\\\\\\",\\\\\\"value\\\\\\":2.0}\\"'
    '},\\"observation_id\\":\\"00000000-0000-0000-0000-0000000186a3\\",\\"s'
    'tation_id\\":\\"00000000-0000-0000-0000-000000016379\\",\\"tenant_id\\'
    '":\\"00000000-0000-0000-0000-00000001675f\\",\\"verified_at\\":\\"2026'
    '-01-20T00:00:00+00:00\\",\\"verified_by\\":\\"fixture-reviewer\\"}",\n '
    '     "fingerprint": "424c0e91a0c476aed73b78c69627be48d00164ef297e'
    '5832696be0cdcf136308"\n    },\n    {\n      "content": "{\\"api_stati'
    'on_id\\":91001,\\"endpoint\\":\\"https://example.invalid/nepal-qualif'
    'ication-levels/\\",\\"evidence_reference\\":\\"nepal-readiness-isolat'
    'ion-v1\\",\\"id\\":\\"00000000-0000-0000-0000-0000000182bc\\",\\"measur'
    'ement\\":{\\"content\\":\\"{\\\\\\"created_at\\\\\\":\\\\\\"2026-01-20T00:00:0'
    '0+00:00\\\\\\",\\\\\\"delivery_id\\\\\\":null,\\\\\\"id\\\\\\":\\\\\\"00000000-0'
    '000-0000-0000-0000000186a4\\\\\\",\\\\\\"parameter\\\\\\":\\\\\\"water_level\\'
    '\\\\",\\\\\\"rating_curve_correction_version\\\\\\":null,\\\\\\"rating_curve'
    '_id\\\\\\":null,\\\\\\"source\\\\\\":\\\\\\"measured\\\\\\",\\\\\\"station_id\\'
    '\\\\":\\\\\\"00000000-0000-0000-0000-000000016379\\\\\\",\\\\\\"timestamp\\\\\\'
    '":\\\\\\"2026-01-13T00:00:00+00:00\\\\\\",\\\\\\"value\\\\\\":2.0}\\"},\\"obser'
    'vation_id\\":\\"00000000-0000-0000-0000-0000000186a4\\",\\"station_id'
    '\\":\\"00000000-0000-0000-0000-000000016379\\",\\"tenant_id\\":\\"00000'
    '000-0000-0000-0000-00000001675f\\",\\"verified_at\\":\\"2026-01-20T00'
    ':00:00+00:00\\",\\"verified_by\\":\\"fixture-reviewer\\"}",\n      "fin'
    'gerprint": "974ed18e2b605aafa20e67bc8919fef1152964eb5061599afc48c'
    '64ddc455fd7"\n    },\n    {\n      "content": "{\\"api_station_id\\":9'
    '1001,\\"endpoint\\":\\"https://example.invalid/nepal-qualification-l'
    'evels/\\",\\"evidence_reference\\":\\"nepal-readiness-isolation-v1\\",'
    '\\"id\\":\\"00000000-0000-0000-0000-0000000182bd\\",\\"measurement\\":{'
    '\\"content\\":\\"{\\\\\\"created_at\\\\\\":\\\\\\"2026-01-20T00:00:00+00:00\\\\'
    '\\",\\\\\\"delivery_id\\\\\\":null,\\\\\\"id\\\\\\":\\\\\\"00000000-0000-0000-00'
    '00-0000000186a5\\\\\\",\\\\\\"parameter\\\\\\":\\\\\\"water_level\\\\\\",\\\\\\"'
    'rating_curve_correction_version\\\\\\":null,\\\\\\"rating_curve_id\\\\\\":'
    'null,\\\\\\"source\\\\\\":\\\\\\"measured\\\\\\",\\\\\\"station_id\\\\\\":\\\\\\'
    '"00000000-0000-0000-0000-000000016379\\\\\\",\\\\\\"timestamp\\\\\\":\\\\\\"2'
    '026-01-14T00:00:00+00:00\\\\\\",\\\\\\"value\\\\\\":2.0}\\"},\\"observation_'
    'id\\":\\"00000000-0000-0000-0000-0000000186a5\\",\\"station_id\\":\\"00'
    '000000-0000-0000-0000-000000016379\\",\\"tenant_id\\":\\"00000000-000'
    '0-0000-0000-00000001675f\\",\\"verified_at\\":\\"2026-01-20T00:00:00+'
    '00:00\\",\\"verified_by\\":\\"fixture-reviewer\\"}",\n      "fingerprin'
    't": "474e0c8241a7f4b2f0698a325f20ffedc6b9fceebb4c66210aa1b205d934'
    '65b9"\n    },\n    {\n      "content": "{\\"api_station_id\\":91001,\\"'
    'endpoint\\":\\"https://example.invalid/nepal-qualification-levels/\\'
    '",\\"evidence_reference\\":\\"nepal-readiness-isolation-v1\\",\\"id\\":'
    '\\"00000000-0000-0000-0000-0000000182be\\",\\"measurement\\":{\\"conte'
    'nt\\":\\"{\\\\\\"created_at\\\\\\":\\\\\\"2026-01-20T00:00:00+00:00\\\\\\",\\\\'
    '\\"delivery_id\\\\\\":null,\\\\\\"id\\\\\\":\\\\\\"00000000-0000-0000-0000-000'
    '0000186a6\\\\\\",\\\\\\"parameter\\\\\\":\\\\\\"water_level\\\\\\",\\\\\\"rating'
    '_curve_correction_version\\\\\\":null,\\\\\\"rating_curve_id\\\\\\":null,\\'
    '\\\\"source\\\\\\":\\\\\\"measured\\\\\\",\\\\\\"station_id\\\\\\":\\\\\\"000000'
    '00-0000-0000-0000-000000016379\\\\\\",\\\\\\"timestamp\\\\\\":\\\\\\"2026-01-'
    '15T00:00:00+00:00\\\\\\",\\\\\\"value\\\\\\":2.0}\\"},\\"observation_id\\":\\"'
    '00000000-0000-0000-0000-0000000186a6\\",\\"station_id\\":\\"00000000-'
    '0000-0000-0000-000000016379\\",\\"tenant_id\\":\\"00000000-0000-0000-'
    '0000-00000001675f\\",\\"verified_at\\":\\"2026-01-20T00:00:00+00:00\\"'
    ',\\"verified_by\\":\\"fixture-reviewer\\"}",\n      "fingerprint": "30'
    '0cd66fac8b286c8372fd5a4dedcea49fce8c7549770933f0783c7ea8808ffe"\n '
    '   },\n    {\n      "content": "{\\"api_station_id\\":91001,\\"endpoin'
    't\\":\\"https://example.invalid/nepal-qualification-levels/\\",\\"evi'
    'dence_reference\\":\\"nepal-readiness-isolation-v1\\",\\"id\\":\\"00000'
    '000-0000-0000-0000-0000000182bf\\",\\"measurement\\":{\\"content\\":\\"'
    '{\\\\\\"created_at\\\\\\":\\\\\\"2026-01-20T00:00:00+00:00\\\\\\",\\\\\\"deliver'
    'y_id\\\\\\":null,\\\\\\"id\\\\\\":\\\\\\"00000000-0000-0000-0000-0000000186a7'
    '\\\\\\",\\\\\\"parameter\\\\\\":\\\\\\"water_level\\\\\\",\\\\\\"rating_curve_co'
    'rrection_version\\\\\\":null,\\\\\\"rating_curve_id\\\\\\":null,\\\\\\"source'
    '\\\\\\":\\\\\\"measured\\\\\\",\\\\\\"station_id\\\\\\":\\\\\\"00000000-0000-000'
    '0-0000-000000016379\\\\\\",\\\\\\"timestamp\\\\\\":\\\\\\"2026-01-16T00:00:00'
    '+00:00\\\\\\",\\\\\\"value\\\\\\":2.0}\\"},\\"observation_id\\":\\"00000000-00'
    '00-0000-0000-0000000186a7\\",\\"station_id\\":\\"00000000-0000-0000-0'
    '000-000000016379\\",\\"tenant_id\\":\\"00000000-0000-0000-0000-000000'
    '01675f\\",\\"verified_at\\":\\"2026-01-20T00:00:00+00:00\\",\\"verified'
    '_by\\":\\"fixture-reviewer\\"}",\n      "fingerprint": "3520fc002d0b9'
    '88fa47cc929e5664e038124fb5f0783b8a9ab3fc944103375c7"\n    },\n    {'
    '\n      "content": "{\\"api_station_id\\":91001,\\"endpoint\\":\\"https'
    '://example.invalid/nepal-qualification-levels/\\",\\"evidence_refer'
    'ence\\":\\"nepal-readiness-isolation-v1\\",\\"id\\":\\"00000000-0000-00'
    '00-0000-0000000182c0\\",\\"measurement\\":{\\"content\\":\\"{\\\\\\"create'
    'd_at\\\\\\":\\\\\\"2026-01-20T00:00:00+00:00\\\\\\",\\\\\\"delivery_id\\\\\\":nu'
    'll,\\\\\\"id\\\\\\":\\\\\\"00000000-0000-0000-0000-0000000186a8\\\\\\",\\\\\\"pa'
    'rameter\\\\\\":\\\\\\"water_level\\\\\\",\\\\\\"rating_curve_correction_versi'
    'on\\\\\\":null,\\\\\\"rating_curve_id\\\\\\":null,\\\\\\"source\\\\\\":\\\\\\"me'
    'asured\\\\\\",\\\\\\"station_id\\\\\\":\\\\\\"00000000-0000-0000-0000-0000000'
    '16379\\\\\\",\\\\\\"timestamp\\\\\\":\\\\\\"2026-01-17T00:00:00+00:00\\\\\\",\\'
    '\\\\"value\\\\\\":2.0}\\"},\\"observation_id\\":\\"00000000-0000-0000-0000'
    '-0000000186a8\\",\\"station_id\\":\\"00000000-0000-0000-0000-00000001'
    '6379\\",\\"tenant_id\\":\\"00000000-0000-0000-0000-00000001675f\\",\\"v'
    'erified_at\\":\\"2026-01-20T00:00:00+00:00\\",\\"verified_by\\":\\"fixt'
    'ure-reviewer\\"}",\n      "fingerprint": "fae61aeca06b94a3608c6bce2'
    '72d3461a75cefbd475ca7db9bb0ca338c7c88da"\n    },\n    {\n      "cont'
    'ent": "{\\"api_station_id\\":91001,\\"endpoint\\":\\"https://example.i'
    'nvalid/nepal-qualification-levels/\\",\\"evidence_reference\\":\\"nep'
    'al-readiness-isolation-v1\\",\\"id\\":\\"00000000-0000-0000-0000-0000'
    '000182c1\\",\\"measurement\\":{\\"content\\":\\"{\\\\\\"created_at\\\\\\":\\\\\\'
    '"2026-01-20T00:00:00+00:00\\\\\\",\\\\\\"delivery_id\\\\\\":null,\\\\\\"id\\\\\\'
    '":\\\\\\"00000000-0000-0000-0000-0000000186a9\\\\\\",\\\\\\"parameter\\\\\\":'
    '\\\\\\"water_level\\\\\\",\\\\\\"rating_curve_correction_version\\\\\\":null,'
    '\\\\\\"rating_curve_id\\\\\\":null,\\\\\\"source\\\\\\":\\\\\\"measured\\\\\\",'
    '\\\\\\"station_id\\\\\\":\\\\\\"00000000-0000-0000-0000-000000016379\\\\\\",\\'
    '\\\\"timestamp\\\\\\":\\\\\\"2026-01-18T00:00:00+00:00\\\\\\",\\\\\\"value\\\\\\'
    '":2.0}\\"},\\"observation_id\\":\\"00000000-0000-0000-0000-0000000186'
    'a9\\",\\"station_id\\":\\"00000000-0000-0000-0000-000000016379\\",\\"te'
    'nant_id\\":\\"00000000-0000-0000-0000-00000001675f\\",\\"verified_at\\'
    '":\\"2026-01-20T00:00:00+00:00\\",\\"verified_by\\":\\"fixture-reviewe'
    'r\\"}",\n      "fingerprint": "77c1d05a2abb790a5883d544c0da4c32f633'
    'a45664345de5d4c5a348fdf0f8dc"\n    },\n    {\n      "content": "{\\"a'
    'pi_station_id\\":91001,\\"endpoint\\":\\"https://example.invalid/nepa'
    'l-qualification-levels/\\",\\"evidence_reference\\":\\"nepal-readines'
    's-isolation-v1\\",\\"id\\":\\"00000000-0000-0000-0000-0000000182c2\\",'
    '\\"measurement\\":{\\"content\\":\\"{\\\\\\"created_at\\\\\\":\\\\\\"2026-01-20'
    'T00:00:00+00:00\\\\\\",\\\\\\"delivery_id\\\\\\":null,\\\\\\"id\\\\\\":\\\\\\"00'
    '000000-0000-0000-0000-0000000186aa\\\\\\",\\\\\\"parameter\\\\\\":\\\\\\"wate'
    'r_level\\\\\\",\\\\\\"rating_curve_correction_version\\\\\\":null,\\\\\\"rati'
    'ng_curve_id\\\\\\":null,\\\\\\"source\\\\\\":\\\\\\"measured\\\\\\",\\\\\\"stati'
    'on_id\\\\\\":\\\\\\"00000000-0000-0000-0000-000000016379\\\\\\",\\\\\\"timest'
    'amp\\\\\\":\\\\\\"2026-01-19T00:00:00+00:00\\\\\\",\\\\\\"value\\\\\\":2.0}\\"},'
    '\\"observation_id\\":\\"00000000-0000-0000-0000-0000000186aa\\",\\"sta'
    'tion_id\\":\\"00000000-0000-0000-0000-000000016379\\",\\"tenant_id\\":'
    '\\"00000000-0000-0000-0000-00000001675f\\",\\"verified_at\\":\\"2026-0'
    '1-20T00:00:00+00:00\\",\\"verified_by\\":\\"fixture-reviewer\\"}",\n   '
    '   "fingerprint": "bcb9f69903d18f1b559aaeb70655bdd183f99b4d026bda'
    '1f60652a83b6ffce6a"\n    }\n  ],\n  "cases": [\n    {\n      "scenario'
    '": "clean",\n      "public_calls": 1,\n      "assembler_calls": 6,\n'
    '      "reports": [\n        {\n          "station_id": "00000000-00'
    '00-0000-0000-000000016379",\n          "code": "447",\n          "s'
    'tatus": "ready_for_model_onboarding",\n          "reasons": [],\n  '
    '        "qc_counts": [\n            [\n              "qc_passed",\n '
    '             10\n            ]\n          ],\n          "qc_versions'
    '": [\n            "1.2"\n          ],\n          "usable_observation'
    's": 10,\n          "complete_samples": 8,\n          "overlap_start'
    '": "2026-01-10T00:00:00+00:00",\n          "overlap_end": "2026-01'
    '-19T00:00:00+00:00"\n        },\n        {\n          "station_id": '
    '"00000000-0000-0000-0000-00000001637a",\n          "code": "450",\n'
    '          "status": "ready_for_model_onboarding",\n          "reas'
    'ons": [],\n          "qc_counts": [\n            [\n              "q'
    'c_passed",\n              10\n            ]\n          ],\n          '
    '"qc_versions": [\n            "1.2"\n          ],\n          "usable'
    '_observations": 10,\n          "complete_samples": 8,\n          "o'
    'verlap_start": "2026-01-10T00:00:00+00:00",\n          "overlap_en'
    'd": "2026-01-19T00:00:00+00:00"\n        },\n        {\n          "s'
    'tation_id": "00000000-0000-0000-0000-00000001637b",\n          "co'
    'de": "604.5",\n          "status": "ready_for_model_onboarding",\n '
    '         "reasons": [],\n          "qc_counts": [\n            [\n  '
    '            "qc_passed",\n              10\n            ]\n         '
    ' ],\n          "qc_versions": [\n            "1.2"\n          ],\n   '
    '       "usable_observations": 10,\n          "complete_samples": 8'
    ',\n          "overlap_start": "2026-01-10T00:00:00+00:00",\n       '
    '   "overlap_end": "2026-01-19T00:00:00+00:00"\n        },\n        '
    '{\n          "station_id": "00000000-0000-0000-0000-00000001637c",'
    '\n          "code": "647",\n          "status": "ready_for_model_on'
    'boarding",\n          "reasons": [],\n          "qc_counts": [\n    '
    '        [\n              "qc_passed",\n              10\n           '
    ' ]\n          ],\n          "qc_versions": [\n            "1.2"\n    '
    '      ],\n          "usable_observations": 10,\n          "complete'
    '_samples": 8,\n          "overlap_start": "2026-01-10T00:00:00+00:'
    '00",\n          "overlap_end": "2026-01-19T00:00:00+00:00"\n       '
    ' },\n        {\n          "station_id": "00000000-0000-0000-0000-00'
    '000001637d",\n          "code": "670",\n          "status": "ready_'
    'for_model_onboarding",\n          "reasons": [],\n          "qc_cou'
    'nts": [\n            [\n              "qc_passed",\n              10'
    '\n            ]\n          ],\n          "qc_versions": [\n          '
    '  "1.2"\n          ],\n          "usable_observations": 10,\n       '
    '   "complete_samples": 8,\n          "overlap_start": "2026-01-10T'
    '00:00:00+00:00",\n          "overlap_end": "2026-01-19T00:00:00+00'
    ':00"\n        },\n        {\n          "station_id": "00000000-0000-'
    '0000-0000-00000001637e",\n          "code": "684",\n          "stat'
    'us": "ready_for_model_onboarding",\n          "reasons": [],\n     '
    '     "qc_counts": [\n            [\n              "qc_passed",\n    '
    '          10\n            ]\n          ],\n          "qc_versions": '
    '[\n            "1.2"\n          ],\n          "usable_observations":'
    ' 10,\n          "complete_samples": 8,\n          "overlap_start": '
    '"2026-01-10T00:00:00+00:00",\n          "overlap_end": "2026-01-19'
    'T00:00:00+00:00"\n        }\n      ],\n      "assembler_results": {\n'
    '        "00000000-0000-0000-0000-000000016379": {\n          "past'
    '_targets": [\n            {\n              "timestamp": "2026-01-10'
    'T00:00:00+00:00",\n              "discharge": 20.0\n            },\n'
    '            {\n              "timestamp": "2026-01-11T00:00:00+00:'
    '00",\n              "discharge": 21.0\n            },\n            {'
    '\n              "timestamp": "2026-01-12T00:00:00+00:00",\n        '
    '      "discharge": 22.0\n            },\n            {\n            '
    '  "timestamp": "2026-01-13T00:00:00+00:00",\n              "discha'
    'rge": 23.0\n            },\n            {\n              "timestamp"'
    ': "2026-01-14T00:00:00+00:00",\n              "discharge": 24.0\n  '
    '          },\n            {\n              "timestamp": "2026-01-15'
    'T00:00:00+00:00",\n              "discharge": 25.0\n            },\n'
    '            {\n              "timestamp": "2026-01-16T00:00:00+00:'
    '00",\n              "discharge": 26.0\n            },\n            {'
    '\n              "timestamp": "2026-01-17T00:00:00+00:00",\n        '
    '      "discharge": 27.0\n            },\n            {\n            '
    '  "timestamp": "2026-01-18T00:00:00+00:00",\n              "discha'
    'rge": 28.0\n            },\n            {\n              "timestamp"'
    ': "2026-01-19T00:00:00+00:00",\n              "discharge": 29.0\n  '
    '          }\n          ],\n          "past_dynamic": [\n            '
    '{\n              "timestamp": "2026-01-10T00:00:00+00:00",\n       '
    '       "precipitation": 10.0,\n              "temperature": 10.0\n '
    '           },\n            {\n              "timestamp": "2026-01-1'
    '1T00:00:00+00:00",\n              "precipitation": 10.0,\n         '
    '     "temperature": 10.0\n            },\n            {\n           '
    '   "timestamp": "2026-01-12T00:00:00+00:00",\n              "preci'
    'pitation": 10.0,\n              "temperature": 10.0\n            },'
    '\n            {\n              "timestamp": "2026-01-13T00:00:00+00'
    ':00",\n              "precipitation": 10.0,\n              "tempera'
    'ture": 10.0\n            },\n            {\n              "timestamp'
    '": "2026-01-14T00:00:00+00:00",\n              "precipitation": 10'
    '.0,\n              "temperature": 10.0\n            },\n            '
    '{\n              "timestamp": "2026-01-15T00:00:00+00:00",\n       '
    '       "precipitation": 10.0,\n              "temperature": 10.0\n '
    '           },\n            {\n              "timestamp": "2026-01-1'
    '6T00:00:00+00:00",\n              "precipitation": 10.0,\n         '
    '     "temperature": 10.0\n            },\n            {\n           '
    '   "timestamp": "2026-01-17T00:00:00+00:00",\n              "preci'
    'pitation": 10.0,\n              "temperature": 10.0\n            },'
    '\n            {\n              "timestamp": "2026-01-18T00:00:00+00'
    ':00",\n              "precipitation": 10.0,\n              "tempera'
    'ture": 10.0\n            },\n            {\n              "timestamp'
    '": "2026-01-19T00:00:00+00:00",\n              "precipitation": 10'
    '.0,\n              "temperature": 10.0\n            }\n          ],\n'
    '          "future_dynamic": [],\n          "static": [\n           '
    ' {\n              "area_km2": 100.0\n            }\n          ],\n   '
    '       "time_step_seconds": 86400,\n          "val_start": null\n  '
    '      },\n        "00000000-0000-0000-0000-00000001637a": {\n      '
    '    "past_targets": [\n            {\n              "timestamp": "2'
    '026-01-10T00:00:00+00:00",\n              "discharge": 20.0\n      '
    '      },\n            {\n              "timestamp": "2026-01-11T00:'
    '00:00+00:00",\n              "discharge": 21.0\n            },\n    '
    '        {\n              "timestamp": "2026-01-12T00:00:00+00:00",'
    '\n              "discharge": 22.0\n            },\n            {\n   '
    '           "timestamp": "2026-01-13T00:00:00+00:00",\n            '
    '  "discharge": 23.0\n            },\n            {\n              "t'
    'imestamp": "2026-01-14T00:00:00+00:00",\n              "discharge"'
    ': 24.0\n            },\n            {\n              "timestamp": "2'
    '026-01-15T00:00:00+00:00",\n              "discharge": 25.0\n      '
    '      },\n            {\n              "timestamp": "2026-01-16T00:'
    '00:00+00:00",\n              "discharge": 26.0\n            },\n    '
    '        {\n              "timestamp": "2026-01-17T00:00:00+00:00",'
    '\n              "discharge": 27.0\n            },\n            {\n   '
    '           "timestamp": "2026-01-18T00:00:00+00:00",\n            '
    '  "discharge": 28.0\n            },\n            {\n              "t'
    'imestamp": "2026-01-19T00:00:00+00:00",\n              "discharge"'
    ': 29.0\n            }\n          ],\n          "past_dynamic": [\n   '
    '         {\n              "timestamp": "2026-01-10T00:00:00+00:00"'
    ',\n              "precipitation": 10.0,\n              "temperature'
    '": 10.0\n            },\n            {\n              "timestamp": "'
    '2026-01-11T00:00:00+00:00",\n              "precipitation": 10.0,\n'
    '              "temperature": 10.0\n            },\n            {\n  '
    '            "timestamp": "2026-01-12T00:00:00+00:00",\n           '
    '   "precipitation": 10.0,\n              "temperature": 10.0\n     '
    '       },\n            {\n              "timestamp": "2026-01-13T00'
    ':00:00+00:00",\n              "precipitation": 10.0,\n             '
    ' "temperature": 10.0\n            },\n            {\n              "'
    'timestamp": "2026-01-14T00:00:00+00:00",\n              "precipita'
    'tion": 10.0,\n              "temperature": 10.0\n            },\n   '
    '         {\n              "timestamp": "2026-01-15T00:00:00+00:00"'
    ',\n              "precipitation": 10.0,\n              "temperature'
    '": 10.0\n            },\n            {\n              "timestamp": "'
    '2026-01-16T00:00:00+00:00",\n              "precipitation": 10.0,\n'
    '              "temperature": 10.0\n            },\n            {\n  '
    '            "timestamp": "2026-01-17T00:00:00+00:00",\n           '
    '   "precipitation": 10.0,\n              "temperature": 10.0\n     '
    '       },\n            {\n              "timestamp": "2026-01-18T00'
    ':00:00+00:00",\n              "precipitation": 10.0,\n             '
    ' "temperature": 10.0\n            },\n            {\n              "'
    'timestamp": "2026-01-19T00:00:00+00:00",\n              "precipita'
    'tion": 10.0,\n              "temperature": 10.0\n            }\n    '
    '      ],\n          "future_dynamic": [],\n          "static": [\n  '
    '          {\n              "area_km2": 100.0\n            }\n       '
    '   ],\n          "time_step_seconds": 86400,\n          "val_start"'
    ': null\n        },\n        "00000000-0000-0000-0000-00000001637b":'
    ' {\n          "past_targets": [\n            {\n              "times'
    'tamp": "2026-01-10T00:00:00+00:00",\n              "discharge": 20'
    '.0\n            },\n            {\n              "timestamp": "2026-'
    '01-11T00:00:00+00:00",\n              "discharge": 21.0\n          '
    '  },\n            {\n              "timestamp": "2026-01-12T00:00:0'
    '0+00:00",\n              "discharge": 22.0\n            },\n        '
    '    {\n              "timestamp": "2026-01-13T00:00:00+00:00",\n   '
    '           "discharge": 23.0\n            },\n            {\n       '
    '       "timestamp": "2026-01-14T00:00:00+00:00",\n              "d'
    'ischarge": 24.0\n            },\n            {\n              "times'
    'tamp": "2026-01-15T00:00:00+00:00",\n              "discharge": 25'
    '.0\n            },\n            {\n              "timestamp": "2026-'
    '01-16T00:00:00+00:00",\n              "discharge": 26.0\n          '
    '  },\n            {\n              "timestamp": "2026-01-17T00:00:0'
    '0+00:00",\n              "discharge": 27.0\n            },\n        '
    '    {\n              "timestamp": "2026-01-18T00:00:00+00:00",\n   '
    '           "discharge": 28.0\n            },\n            {\n       '
    '       "timestamp": "2026-01-19T00:00:00+00:00",\n              "d'
    'ischarge": 29.0\n            }\n          ],\n          "past_dynami'
    'c": [\n            {\n              "timestamp": "2026-01-10T00:00:'
    '00+00:00",\n              "precipitation": 10.0,\n              "te'
    'mperature": 10.0\n            },\n            {\n              "time'
    'stamp": "2026-01-11T00:00:00+00:00",\n              "precipitation'
    '": 10.0,\n              "temperature": 10.0\n            },\n       '
    '     {\n              "timestamp": "2026-01-12T00:00:00+00:00",\n  '
    '            "precipitation": 10.0,\n              "temperature": 1'
    '0.0\n            },\n            {\n              "timestamp": "2026'
    '-01-13T00:00:00+00:00",\n              "precipitation": 10.0,\n    '
    '          "temperature": 10.0\n            },\n            {\n      '
    '        "timestamp": "2026-01-14T00:00:00+00:00",\n              "'
    'precipitation": 10.0,\n              "temperature": 10.0\n         '
    '   },\n            {\n              "timestamp": "2026-01-15T00:00:'
    '00+00:00",\n              "precipitation": 10.0,\n              "te'
    'mperature": 10.0\n            },\n            {\n              "time'
    'stamp": "2026-01-16T00:00:00+00:00",\n              "precipitation'
    '": 10.0,\n              "temperature": 10.0\n            },\n       '
    '     {\n              "timestamp": "2026-01-17T00:00:00+00:00",\n  '
    '            "precipitation": 10.0,\n              "temperature": 1'
    '0.0\n            },\n            {\n              "timestamp": "2026'
    '-01-18T00:00:00+00:00",\n              "precipitation": 10.0,\n    '
    '          "temperature": 10.0\n            },\n            {\n      '
    '        "timestamp": "2026-01-19T00:00:00+00:00",\n              "'
    'precipitation": 10.0,\n              "temperature": 10.0\n         '
    '   }\n          ],\n          "future_dynamic": [],\n          "stat'
    'ic": [\n            {\n              "area_km2": 100.0\n            '
    '}\n          ],\n          "time_step_seconds": 86400,\n          "v'
    'al_start": null\n        },\n        "00000000-0000-0000-0000-00000'
    '001637c": {\n          "past_targets": [\n            {\n           '
    '   "timestamp": "2026-01-10T00:00:00+00:00",\n              "disch'
    'arge": 20.0\n            },\n            {\n              "timestamp'
    '": "2026-01-11T00:00:00+00:00",\n              "discharge": 21.0\n '
    '           },\n            {\n              "timestamp": "2026-01-1'
    '2T00:00:00+00:00",\n              "discharge": 22.0\n            },'
    '\n            {\n              "timestamp": "2026-01-13T00:00:00+00'
    ':00",\n              "discharge": 23.0\n            },\n            '
    '{\n              "timestamp": "2026-01-14T00:00:00+00:00",\n       '
    '       "discharge": 24.0\n            },\n            {\n           '
    '   "timestamp": "2026-01-15T00:00:00+00:00",\n              "disch'
    'arge": 25.0\n            },\n            {\n              "timestamp'
    '": "2026-01-16T00:00:00+00:00",\n              "discharge": 26.0\n '
    '           },\n            {\n              "timestamp": "2026-01-1'
    '7T00:00:00+00:00",\n              "discharge": 27.0\n            },'
    '\n            {\n              "timestamp": "2026-01-18T00:00:00+00'
    ':00",\n              "discharge": 28.0\n            },\n            '
    '{\n              "timestamp": "2026-01-19T00:00:00+00:00",\n       '
    '       "discharge": 29.0\n            }\n          ],\n          "pa'
    'st_dynamic": [\n            {\n              "timestamp": "2026-01-'
    '10T00:00:00+00:00",\n              "precipitation": 10.0,\n        '
    '      "temperature": 10.0\n            },\n            {\n          '
    '    "timestamp": "2026-01-11T00:00:00+00:00",\n              "prec'
    'ipitation": 10.0,\n              "temperature": 10.0\n            }'
    ',\n            {\n              "timestamp": "2026-01-12T00:00:00+0'
    '0:00",\n              "precipitation": 10.0,\n              "temper'
    'ature": 10.0\n            },\n            {\n              "timestam'
    'p": "2026-01-13T00:00:00+00:00",\n              "precipitation": 1'
    '0.0,\n              "temperature": 10.0\n            },\n           '
    ' {\n              "timestamp": "2026-01-14T00:00:00+00:00",\n      '
    '        "precipitation": 10.0,\n              "temperature": 10.0\n'
    '            },\n            {\n              "timestamp": "2026-01-'
    '15T00:00:00+00:00",\n              "precipitation": 10.0,\n        '
    '      "temperature": 10.0\n            },\n            {\n          '
    '    "timestamp": "2026-01-16T00:00:00+00:00",\n              "prec'
    'ipitation": 10.0,\n              "temperature": 10.0\n            }'
    ',\n            {\n              "timestamp": "2026-01-17T00:00:00+0'
    '0:00",\n              "precipitation": 10.0,\n              "temper'
    'ature": 10.0\n            },\n            {\n              "timestam'
    'p": "2026-01-18T00:00:00+00:00",\n              "precipitation": 1'
    '0.0,\n              "temperature": 10.0\n            },\n           '
    ' {\n              "timestamp": "2026-01-19T00:00:00+00:00",\n      '
    '        "precipitation": 10.0,\n              "temperature": 10.0\n'
    '            }\n          ],\n          "future_dynamic": [],\n      '
    '    "static": [\n            {\n              "area_km2": 100.0\n   '
    '         }\n          ],\n          "time_step_seconds": 86400,\n   '
    '       "val_start": null\n        },\n        "00000000-0000-0000-0'
    '000-00000001637d": {\n          "past_targets": [\n            {\n  '
    '            "timestamp": "2026-01-10T00:00:00+00:00",\n           '
    '   "discharge": 20.0\n            },\n            {\n              "'
    'timestamp": "2026-01-11T00:00:00+00:00",\n              "discharge'
    '": 21.0\n            },\n            {\n              "timestamp": "'
    '2026-01-12T00:00:00+00:00",\n              "discharge": 22.0\n     '
    '       },\n            {\n              "timestamp": "2026-01-13T00'
    ':00:00+00:00",\n              "discharge": 23.0\n            },\n   '
    '         {\n              "timestamp": "2026-01-14T00:00:00+00:00"'
    ',\n              "discharge": 24.0\n            },\n            {\n  '
    '            "timestamp": "2026-01-15T00:00:00+00:00",\n           '
    '   "discharge": 25.0\n            },\n            {\n              "'
    'timestamp": "2026-01-16T00:00:00+00:00",\n              "discharge'
    '": 26.0\n            },\n            {\n              "timestamp": "'
    '2026-01-17T00:00:00+00:00",\n              "discharge": 27.0\n     '
    '       },\n            {\n              "timestamp": "2026-01-18T00'
    ':00:00+00:00",\n              "discharge": 28.0\n            },\n   '
    '         {\n              "timestamp": "2026-01-19T00:00:00+00:00"'
    ',\n              "discharge": 29.0\n            }\n          ],\n    '
    '      "past_dynamic": [\n            {\n              "timestamp": '
    '"2026-01-10T00:00:00+00:00",\n              "precipitation": 10.0,'
    '\n              "temperature": 10.0\n            },\n            {\n '
    '             "timestamp": "2026-01-11T00:00:00+00:00",\n          '
    '    "precipitation": 10.0,\n              "temperature": 10.0\n    '
    '        },\n            {\n              "timestamp": "2026-01-12T0'
    '0:00:00+00:00",\n              "precipitation": 10.0,\n            '
    '  "temperature": 10.0\n            },\n            {\n              '
    '"timestamp": "2026-01-13T00:00:00+00:00",\n              "precipit'
    'ation": 10.0,\n              "temperature": 10.0\n            },\n  '
    '          {\n              "timestamp": "2026-01-14T00:00:00+00:00'
    '",\n              "precipitation": 10.0,\n              "temperatur'
    'e": 10.0\n            },\n            {\n              "timestamp": '
    '"2026-01-15T00:00:00+00:00",\n              "precipitation": 10.0,'
    '\n              "temperature": 10.0\n            },\n            {\n '
    '             "timestamp": "2026-01-16T00:00:00+00:00",\n          '
    '    "precipitation": 10.0,\n              "temperature": 10.0\n    '
    '        },\n            {\n              "timestamp": "2026-01-17T0'
    '0:00:00+00:00",\n              "precipitation": 10.0,\n            '
    '  "temperature": 10.0\n            },\n            {\n              '
    '"timestamp": "2026-01-18T00:00:00+00:00",\n              "precipit'
    'ation": 10.0,\n              "temperature": 10.0\n            },\n  '
    '          {\n              "timestamp": "2026-01-19T00:00:00+00:00'
    '",\n              "precipitation": 10.0,\n              "temperatur'
    'e": 10.0\n            }\n          ],\n          "future_dynamic": ['
    '],\n          "static": [\n            {\n              "area_km2": '
    '100.0\n            }\n          ],\n          "time_step_seconds": 8'
    '6400,\n          "val_start": null\n        },\n        "00000000-00'
    '00-0000-0000-00000001637e": {\n          "past_targets": [\n       '
    '     {\n              "timestamp": "2026-01-10T00:00:00+00:00",\n  '
    '            "discharge": 20.0\n            },\n            {\n      '
    '        "timestamp": "2026-01-11T00:00:00+00:00",\n              "'
    'discharge": 21.0\n            },\n            {\n              "time'
    'stamp": "2026-01-12T00:00:00+00:00",\n              "discharge": 2'
    '2.0\n            },\n            {\n              "timestamp": "2026'
    '-01-13T00:00:00+00:00",\n              "discharge": 23.0\n         '
    '   },\n            {\n              "timestamp": "2026-01-14T00:00:'
    '00+00:00",\n              "discharge": 24.0\n            },\n       '
    '     {\n              "timestamp": "2026-01-15T00:00:00+00:00",\n  '
    '            "discharge": 25.0\n            },\n            {\n      '
    '        "timestamp": "2026-01-16T00:00:00+00:00",\n              "'
    'discharge": 26.0\n            },\n            {\n              "time'
    'stamp": "2026-01-17T00:00:00+00:00",\n              "discharge": 2'
    '7.0\n            },\n            {\n              "timestamp": "2026'
    '-01-18T00:00:00+00:00",\n              "discharge": 28.0\n         '
    '   },\n            {\n              "timestamp": "2026-01-19T00:00:'
    '00+00:00",\n              "discharge": 29.0\n            }\n        '
    '  ],\n          "past_dynamic": [\n            {\n              "tim'
    'estamp": "2026-01-10T00:00:00+00:00",\n              "precipitatio'
    'n": 10.0,\n              "temperature": 10.0\n            },\n      '
    '      {\n              "timestamp": "2026-01-11T00:00:00+00:00",\n '
    '             "precipitation": 10.0,\n              "temperature": '
    '10.0\n            },\n            {\n              "timestamp": "202'
    '6-01-12T00:00:00+00:00",\n              "precipitation": 10.0,\n   '
    '           "temperature": 10.0\n            },\n            {\n     '
    '         "timestamp": "2026-01-13T00:00:00+00:00",\n              '
    '"precipitation": 10.0,\n              "temperature": 10.0\n        '
    '    },\n            {\n              "timestamp": "2026-01-14T00:00'
    ':00+00:00",\n              "precipitation": 10.0,\n              "t'
    'emperature": 10.0\n            },\n            {\n              "tim'
    'estamp": "2026-01-15T00:00:00+00:00",\n              "precipitatio'
    'n": 10.0,\n              "temperature": 10.0\n            },\n      '
    '      {\n              "timestamp": "2026-01-16T00:00:00+00:00",\n '
    '             "precipitation": 10.0,\n              "temperature": '
    '10.0\n            },\n            {\n              "timestamp": "202'
    '6-01-17T00:00:00+00:00",\n              "precipitation": 10.0,\n   '
    '           "temperature": 10.0\n            },\n            {\n     '
    '         "timestamp": "2026-01-18T00:00:00+00:00",\n              '
    '"precipitation": 10.0,\n              "temperature": 10.0\n        '
    '    },\n            {\n              "timestamp": "2026-01-19T00:00'
    ':00+00:00",\n              "precipitation": 10.0,\n              "t'
    'emperature": 10.0\n            }\n          ],\n          "future_dy'
    'namic": [],\n          "static": [\n            {\n              "ar'
    'ea_km2": 100.0\n            }\n          ],\n          "time_step_se'
    'conds": 86400,\n          "val_start": null\n        }\n      },\n   '
    '   "delete_ordinary_ids": [],\n      "allowed_station_update_attem'
    'pts": 6,\n      "station_targets": [\n        [\n          "discharg'
    'e"\n        ],\n        [\n          "discharge"\n        ],\n        '
    '[\n          "discharge"\n        ],\n        [\n          "discharge'
    '"\n        ],\n        [\n          "discharge"\n        ],\n        ['
    '\n          "discharge"\n        ]\n      ],\n      "station_updated_'
    'at": "2026-01-20T00:00:00+00:00",\n      "observation_count": 60,\n'
    '      "protected_count": 0,\n      "forcing_count": 120,\n      "pu'
    'blic_call_inputs": {\n        "training_window": {\n          "star'
    't": "2026-01-10T00:00:00+00:00",\n          "end": "2026-01-20T00:'
    '00:00+00:00",\n          "time_step_seconds": 86400,\n          "mi'
    'nimum_samples": 2\n        },\n        "now": "2026-01-20T00:00:00+'
    '00:00",\n        "application_identity": {\n          "global_admin'
    '": false,\n          "writable_tenants": [\n            "chwrr"\n   '
    '       ],\n          "operator": "fixture-qualification-owner"\n   '
    '     },\n        "model_requirements": {\n          "target_paramet'
    'ers": [\n            "discharge"\n          ],\n          "past_dyna'
    'mic_features": [\n            "precipitation",\n            "temper'
    'ature"\n          ],\n          "future_dynamic_features": [],\n    '
    '      "static_features": [],\n          "supported_time_steps_seco'
    'nds": [\n            86400\n          ],\n          "lookback_steps"'
    ': 2,\n          "forecast_horizon_steps": 1,\n          "spatial_in'
    'put_type": "basin_average",\n          "ensemble_mode": "single",\n'
    '          "declared_aggregations": [],\n          "declared_lookba'
    'cks": [],\n          "declared_horizon_semantics": null,\n         '
    ' "declared_min_future_steps": null,\n          "required_past_targ'
    'ets": null\n        },\n        "session_user": "test",\n        "cu'
    'rrent_user": "test",\n        "transaction_isolation": "read commi'
    'tted",\n        "model_carrier": "Fresh per-call FakeStationForeca'
    "stModel instance with local14-field requirements override; shared"
    ' class unchanged"\n      }\n    },\n    {\n      "scenario": "mixed",'
    '\n      "public_calls": 1,\n      "assembler_calls": 6,\n      "repo'
    'rts": [\n        {\n          "station_id": "00000000-0000-0000-000'
    '0-000000016379",\n          "code": "447",\n          "status": "re'
    'ady_for_model_onboarding",\n          "reasons": [],\n          "qc'
    '_counts": [\n            [\n              "qc_passed",\n            '
    '  10\n            ]\n          ],\n          "qc_versions": [\n      '
    '      "1.2"\n          ],\n          "usable_observations": 10,\n   '
    '       "complete_samples": 8,\n          "overlap_start": "2026-01'
    '-10T00:00:00+00:00",\n          "overlap_end": "2026-01-19T00:00:0'
    '0+00:00"\n        },\n        {\n          "station_id": "00000000-0'
    '000-0000-0000-00000001637a",\n          "code": "450",\n          "'
    'status": "ready_for_model_onboarding",\n          "reasons": [],\n '
    '         "qc_counts": [\n            [\n              "qc_passed",\n'
    '              10\n            ]\n          ],\n          "qc_version'
    's": [\n            "1.2"\n          ],\n          "usable_observatio'
    'ns": 10,\n          "complete_samples": 8,\n          "overlap_star'
    't": "2026-01-10T00:00:00+00:00",\n          "overlap_end": "2026-0'
    '1-19T00:00:00+00:00"\n        },\n        {\n          "station_id":'
    ' "00000000-0000-0000-0000-00000001637b",\n          "code": "604.5'
    '",\n          "status": "ready_for_model_onboarding",\n          "r'
    'easons": [],\n          "qc_counts": [\n            [\n             '
    ' "qc_passed",\n              10\n            ]\n          ],\n       '
    '   "qc_versions": [\n            "1.2"\n          ],\n          "usa'
    'ble_observations": 10,\n          "complete_samples": 8,\n         '
    ' "overlap_start": "2026-01-10T00:00:00+00:00",\n          "overlap'
    '_end": "2026-01-19T00:00:00+00:00"\n        },\n        {\n         '
    ' "station_id": "00000000-0000-0000-0000-00000001637c",\n          '
    '"code": "647",\n          "status": "ready_for_model_onboarding",\n'
    '          "reasons": [],\n          "qc_counts": [\n            [\n '
    '             "qc_passed",\n              10\n            ]\n        '
    '  ],\n          "qc_versions": [\n            "1.2"\n          ],\n  '
    '        "usable_observations": 10,\n          "complete_samples": '
    '8,\n          "overlap_start": "2026-01-10T00:00:00+00:00",\n      '
    '    "overlap_end": "2026-01-19T00:00:00+00:00"\n        },\n       '
    ' {\n          "station_id": "00000000-0000-0000-0000-00000001637d"'
    ',\n          "code": "670",\n          "status": "ready_for_model_o'
    'nboarding",\n          "reasons": [],\n          "qc_counts": [\n   '
    '         [\n              "qc_passed",\n              10\n          '
    '  ]\n          ],\n          "qc_versions": [\n            "1.2"\n   '
    '       ],\n          "usable_observations": 10,\n          "complet'
    'e_samples": 8,\n          "overlap_start": "2026-01-10T00:00:00+00'
    ':00",\n          "overlap_end": "2026-01-19T00:00:00+00:00"\n      '
    '  },\n        {\n          "station_id": "00000000-0000-0000-0000-0'
    '0000001637e",\n          "code": "684",\n          "status": "ready'
    '_for_model_onboarding",\n          "reasons": [],\n          "qc_co'
    'unts": [\n            [\n              "qc_passed",\n              1'
    '0\n            ]\n          ],\n          "qc_versions": [\n         '
    '   "1.2"\n          ],\n          "usable_observations": 10,\n      '
    '    "complete_samples": 8,\n          "overlap_start": "2026-01-10'
    'T00:00:00+00:00",\n          "overlap_end": "2026-01-19T00:00:00+0'
    '0:00"\n        }\n      ],\n      "assembler_results": {\n        "00'
    '000000-0000-0000-0000-000000016379": {\n          "past_targets": '
    '[\n            {\n              "timestamp": "2026-01-10T00:00:00+0'
    '0:00",\n              "discharge": 20.0\n            },\n           '
    ' {\n              "timestamp": "2026-01-11T00:00:00+00:00",\n      '
    '        "discharge": 21.0\n            },\n            {\n          '
    '    "timestamp": "2026-01-12T00:00:00+00:00",\n              "disc'
    'harge": 22.0\n            },\n            {\n              "timestam'
    'p": "2026-01-13T00:00:00+00:00",\n              "discharge": 23.0\n'
    '            },\n            {\n              "timestamp": "2026-01-'
    '14T00:00:00+00:00",\n              "discharge": 24.0\n            }'
    ',\n            {\n              "timestamp": "2026-01-15T00:00:00+0'
    '0:00",\n              "discharge": 25.0\n            },\n           '
    ' {\n              "timestamp": "2026-01-16T00:00:00+00:00",\n      '
    '        "discharge": 26.0\n            },\n            {\n          '
    '    "timestamp": "2026-01-17T00:00:00+00:00",\n              "disc'
    'harge": 27.0\n            },\n            {\n              "timestam'
    'p": "2026-01-18T00:00:00+00:00",\n              "discharge": 28.0\n'
    '            },\n            {\n              "timestamp": "2026-01-'
    '19T00:00:00+00:00",\n              "discharge": 29.0\n            }'
    '\n          ],\n          "past_dynamic": [\n            {\n         '
    '     "timestamp": "2026-01-10T00:00:00+00:00",\n              "pre'
    'cipitation": 10.0,\n              "temperature": 10.0\n            '
    '},\n            {\n              "timestamp": "2026-01-11T00:00:00+'
    '00:00",\n              "precipitation": 10.0,\n              "tempe'
    'rature": 10.0\n            },\n            {\n              "timesta'
    'mp": "2026-01-12T00:00:00+00:00",\n              "precipitation": '
    '10.0,\n              "temperature": 10.0\n            },\n          '
    '  {\n              "timestamp": "2026-01-13T00:00:00+00:00",\n     '
    '         "precipitation": 10.0,\n              "temperature": 10.0'
    '\n            },\n            {\n              "timestamp": "2026-01'
    '-14T00:00:00+00:00",\n              "precipitation": 10.0,\n       '
    '       "temperature": 10.0\n            },\n            {\n         '
    '     "timestamp": "2026-01-15T00:00:00+00:00",\n              "pre'
    'cipitation": 10.0,\n              "temperature": 10.0\n            '
    '},\n            {\n              "timestamp": "2026-01-16T00:00:00+'
    '00:00",\n              "precipitation": 10.0,\n              "tempe'
    'rature": 10.0\n            },\n            {\n              "timesta'
    'mp": "2026-01-17T00:00:00+00:00",\n              "precipitation": '
    '10.0,\n              "temperature": 10.0\n            },\n          '
    '  {\n              "timestamp": "2026-01-18T00:00:00+00:00",\n     '
    '         "precipitation": 10.0,\n              "temperature": 10.0'
    '\n            },\n            {\n              "timestamp": "2026-01'
    '-19T00:00:00+00:00",\n              "precipitation": 10.0,\n       '
    '       "temperature": 10.0\n            }\n          ],\n          "'
    'future_dynamic": [],\n          "static": [\n            {\n        '
    '      "area_km2": 100.0\n            }\n          ],\n          "tim'
    'e_step_seconds": 86400,\n          "val_start": null\n        },\n  '
    '      "00000000-0000-0000-0000-00000001637a": {\n          "past_t'
    'argets": [\n            {\n              "timestamp": "2026-01-10T0'
    '0:00:00+00:00",\n              "discharge": 20.0\n            },\n  '
    '          {\n              "timestamp": "2026-01-11T00:00:00+00:00'
    '",\n              "discharge": 21.0\n            },\n            {\n '
    '             "timestamp": "2026-01-12T00:00:00+00:00",\n          '
    '    "discharge": 22.0\n            },\n            {\n              '
    '"timestamp": "2026-01-13T00:00:00+00:00",\n              "discharg'
    'e": 23.0\n            },\n            {\n              "timestamp": '
    '"2026-01-14T00:00:00+00:00",\n              "discharge": 24.0\n    '
    '        },\n            {\n              "timestamp": "2026-01-15T0'
    '0:00:00+00:00",\n              "discharge": 25.0\n            },\n  '
    '          {\n              "timestamp": "2026-01-16T00:00:00+00:00'
    '",\n              "discharge": 26.0\n            },\n            {\n '
    '             "timestamp": "2026-01-17T00:00:00+00:00",\n          '
    '    "discharge": 27.0\n            },\n            {\n              '
    '"timestamp": "2026-01-18T00:00:00+00:00",\n              "discharg'
    'e": 28.0\n            },\n            {\n              "timestamp": '
    '"2026-01-19T00:00:00+00:00",\n              "discharge": 29.0\n    '
    '        }\n          ],\n          "past_dynamic": [\n            {\n'
    '              "timestamp": "2026-01-10T00:00:00+00:00",\n         '
    '     "precipitation": 10.0,\n              "temperature": 10.0\n   '
    '         },\n            {\n              "timestamp": "2026-01-11T'
    '00:00:00+00:00",\n              "precipitation": 10.0,\n           '
    '   "temperature": 10.0\n            },\n            {\n             '
    ' "timestamp": "2026-01-12T00:00:00+00:00",\n              "precipi'
    'tation": 10.0,\n              "temperature": 10.0\n            },\n '
    '           {\n              "timestamp": "2026-01-13T00:00:00+00:0'
    '0",\n              "precipitation": 10.0,\n              "temperatu'
    're": 10.0\n            },\n            {\n              "timestamp":'
    ' "2026-01-14T00:00:00+00:00",\n              "precipitation": 10.0'
    ',\n              "temperature": 10.0\n            },\n            {\n'
    '              "timestamp": "2026-01-15T00:00:00+00:00",\n         '
    '     "precipitation": 10.0,\n              "temperature": 10.0\n   '
    '         },\n            {\n              "timestamp": "2026-01-16T'
    '00:00:00+00:00",\n              "precipitation": 10.0,\n           '
    '   "temperature": 10.0\n            },\n            {\n             '
    ' "timestamp": "2026-01-17T00:00:00+00:00",\n              "precipi'
    'tation": 10.0,\n              "temperature": 10.0\n            },\n '
    '           {\n              "timestamp": "2026-01-18T00:00:00+00:0'
    '0",\n              "precipitation": 10.0,\n              "temperatu'
    're": 10.0\n            },\n            {\n              "timestamp":'
    ' "2026-01-19T00:00:00+00:00",\n              "precipitation": 10.0'
    ',\n              "temperature": 10.0\n            }\n          ],\n  '
    '        "future_dynamic": [],\n          "static": [\n            {'
    '\n              "area_km2": 100.0\n            }\n          ],\n     '
    '     "time_step_seconds": 86400,\n          "val_start": null\n    '
    '    },\n        "00000000-0000-0000-0000-00000001637b": {\n        '
    '  "past_targets": [\n            {\n              "timestamp": "202'
    '6-01-10T00:00:00+00:00",\n              "discharge": 20.0\n        '
    '    },\n            {\n              "timestamp": "2026-01-11T00:00'
    ':00+00:00",\n              "discharge": 21.0\n            },\n      '
    '      {\n              "timestamp": "2026-01-12T00:00:00+00:00",\n '
    '             "discharge": 22.0\n            },\n            {\n     '
    '         "timestamp": "2026-01-13T00:00:00+00:00",\n              '
    '"discharge": 23.0\n            },\n            {\n              "tim'
    'estamp": "2026-01-14T00:00:00+00:00",\n              "discharge": '
    '24.0\n            },\n            {\n              "timestamp": "202'
    '6-01-15T00:00:00+00:00",\n              "discharge": 25.0\n        '
    '    },\n            {\n              "timestamp": "2026-01-16T00:00'
    ':00+00:00",\n              "discharge": 26.0\n            },\n      '
    '      {\n              "timestamp": "2026-01-17T00:00:00+00:00",\n '
    '             "discharge": 27.0\n            },\n            {\n     '
    '         "timestamp": "2026-01-18T00:00:00+00:00",\n              '
    '"discharge": 28.0\n            },\n            {\n              "tim'
    'estamp": "2026-01-19T00:00:00+00:00",\n              "discharge": '
    '29.0\n            }\n          ],\n          "past_dynamic": [\n     '
    '       {\n              "timestamp": "2026-01-10T00:00:00+00:00",\n'
    '              "precipitation": 10.0,\n              "temperature":'
    ' 10.0\n            },\n            {\n              "timestamp": "20'
    '26-01-11T00:00:00+00:00",\n              "precipitation": 10.0,\n  '
    '            "temperature": 10.0\n            },\n            {\n    '
    '          "timestamp": "2026-01-12T00:00:00+00:00",\n             '
    ' "precipitation": 10.0,\n              "temperature": 10.0\n       '
    '     },\n            {\n              "timestamp": "2026-01-13T00:0'
    '0:00+00:00",\n              "precipitation": 10.0,\n              "'
    'temperature": 10.0\n            },\n            {\n              "ti'
    'mestamp": "2026-01-14T00:00:00+00:00",\n              "precipitati'
    'on": 10.0,\n              "temperature": 10.0\n            },\n     '
    '       {\n              "timestamp": "2026-01-15T00:00:00+00:00",\n'
    '              "precipitation": 10.0,\n              "temperature":'
    ' 10.0\n            },\n            {\n              "timestamp": "20'
    '26-01-16T00:00:00+00:00",\n              "precipitation": 10.0,\n  '
    '            "temperature": 10.0\n            },\n            {\n    '
    '          "timestamp": "2026-01-17T00:00:00+00:00",\n             '
    ' "precipitation": 10.0,\n              "temperature": 10.0\n       '
    '     },\n            {\n              "timestamp": "2026-01-18T00:0'
    '0:00+00:00",\n              "precipitation": 10.0,\n              "'
    'temperature": 10.0\n            },\n            {\n              "ti'
    'mestamp": "2026-01-19T00:00:00+00:00",\n              "precipitati'
    'on": 10.0,\n              "temperature": 10.0\n            }\n      '
    '    ],\n          "future_dynamic": [],\n          "static": [\n    '
    '        {\n              "area_km2": 100.0\n            }\n         '
    ' ],\n          "time_step_seconds": 86400,\n          "val_start": '
    'null\n        },\n        "00000000-0000-0000-0000-00000001637c": {'
    '\n          "past_targets": [\n            {\n              "timesta'
    'mp": "2026-01-10T00:00:00+00:00",\n              "discharge": 20.0'
    '\n            },\n            {\n              "timestamp": "2026-01'
    '-11T00:00:00+00:00",\n              "discharge": 21.0\n            '
    '},\n            {\n              "timestamp": "2026-01-12T00:00:00+'
    '00:00",\n              "discharge": 22.0\n            },\n          '
    '  {\n              "timestamp": "2026-01-13T00:00:00+00:00",\n     '
    '         "discharge": 23.0\n            },\n            {\n         '
    '     "timestamp": "2026-01-14T00:00:00+00:00",\n              "dis'
    'charge": 24.0\n            },\n            {\n              "timesta'
    'mp": "2026-01-15T00:00:00+00:00",\n              "discharge": 25.0'
    '\n            },\n            {\n              "timestamp": "2026-01'
    '-16T00:00:00+00:00",\n              "discharge": 26.0\n            '
    '},\n            {\n              "timestamp": "2026-01-17T00:00:00+'
    '00:00",\n              "discharge": 27.0\n            },\n          '
    '  {\n              "timestamp": "2026-01-18T00:00:00+00:00",\n     '
    '         "discharge": 28.0\n            },\n            {\n         '
    '     "timestamp": "2026-01-19T00:00:00+00:00",\n              "dis'
    'charge": 29.0\n            }\n          ],\n          "past_dynamic"'
    ': [\n            {\n              "timestamp": "2026-01-10T00:00:00'
    '+00:00",\n              "precipitation": 10.0,\n              "temp'
    'erature": 10.0\n            },\n            {\n              "timest'
    'amp": "2026-01-11T00:00:00+00:00",\n              "precipitation":'
    ' 10.0,\n              "temperature": 10.0\n            },\n         '
    '   {\n              "timestamp": "2026-01-12T00:00:00+00:00",\n    '
    '          "precipitation": 10.0,\n              "temperature": 10.'
    '0\n            },\n            {\n              "timestamp": "2026-0'
    '1-13T00:00:00+00:00",\n              "precipitation": 10.0,\n      '
    '        "temperature": 10.0\n            },\n            {\n        '
    '      "timestamp": "2026-01-14T00:00:00+00:00",\n              "pr'
    'ecipitation": 10.0,\n              "temperature": 10.0\n           '
    ' },\n            {\n              "timestamp": "2026-01-15T00:00:00'
    '+00:00",\n              "precipitation": 10.0,\n              "temp'
    'erature": 10.0\n            },\n            {\n              "timest'
    'amp": "2026-01-16T00:00:00+00:00",\n              "precipitation":'
    ' 10.0,\n              "temperature": 10.0\n            },\n         '
    '   {\n              "timestamp": "2026-01-17T00:00:00+00:00",\n    '
    '          "precipitation": 10.0,\n              "temperature": 10.'
    '0\n            },\n            {\n              "timestamp": "2026-0'
    '1-18T00:00:00+00:00",\n              "precipitation": 10.0,\n      '
    '        "temperature": 10.0\n            },\n            {\n        '
    '      "timestamp": "2026-01-19T00:00:00+00:00",\n              "pr'
    'ecipitation": 10.0,\n              "temperature": 10.0\n           '
    ' }\n          ],\n          "future_dynamic": [],\n          "static'
    '": [\n            {\n              "area_km2": 100.0\n            }\n'
    '          ],\n          "time_step_seconds": 86400,\n          "val'
    '_start": null\n        },\n        "00000000-0000-0000-0000-0000000'
    '1637d": {\n          "past_targets": [\n            {\n             '
    ' "timestamp": "2026-01-10T00:00:00+00:00",\n              "dischar'
    'ge": 20.0\n            },\n            {\n              "timestamp":'
    ' "2026-01-11T00:00:00+00:00",\n              "discharge": 21.0\n   '
    '         },\n            {\n              "timestamp": "2026-01-12T'
    '00:00:00+00:00",\n              "discharge": 22.0\n            },\n '
    '           {\n              "timestamp": "2026-01-13T00:00:00+00:0'
    '0",\n              "discharge": 23.0\n            },\n            {\n'
    '              "timestamp": "2026-01-14T00:00:00+00:00",\n         '
    '     "discharge": 24.0\n            },\n            {\n             '
    ' "timestamp": "2026-01-15T00:00:00+00:00",\n              "dischar'
    'ge": 25.0\n            },\n            {\n              "timestamp":'
    ' "2026-01-16T00:00:00+00:00",\n              "discharge": 26.0\n   '
    '         },\n            {\n              "timestamp": "2026-01-17T'
    '00:00:00+00:00",\n              "discharge": 27.0\n            },\n '
    '           {\n              "timestamp": "2026-01-18T00:00:00+00:0'
    '0",\n              "discharge": 28.0\n            },\n            {\n'
    '              "timestamp": "2026-01-19T00:00:00+00:00",\n         '
    '     "discharge": 29.0\n            }\n          ],\n          "past'
    '_dynamic": [\n            {\n              "timestamp": "2026-01-10'
    'T00:00:00+00:00",\n              "precipitation": 10.0,\n          '
    '    "temperature": 10.0\n            },\n            {\n            '
    '  "timestamp": "2026-01-11T00:00:00+00:00",\n              "precip'
    'itation": 10.0,\n              "temperature": 10.0\n            },\n'
    '            {\n              "timestamp": "2026-01-12T00:00:00+00:'
    '00",\n              "precipitation": 10.0,\n              "temperat'
    'ure": 10.0\n            },\n            {\n              "timestamp"'
    ': "2026-01-13T00:00:00+00:00",\n              "precipitation": 10.'
    '0,\n              "temperature": 10.0\n            },\n            {'
    '\n              "timestamp": "2026-01-14T00:00:00+00:00",\n        '
    '      "precipitation": 10.0,\n              "temperature": 10.0\n  '
    '          },\n            {\n              "timestamp": "2026-01-15'
    'T00:00:00+00:00",\n              "precipitation": 10.0,\n          '
    '    "temperature": 10.0\n            },\n            {\n            '
    '  "timestamp": "2026-01-16T00:00:00+00:00",\n              "precip'
    'itation": 10.0,\n              "temperature": 10.0\n            },\n'
    '            {\n              "timestamp": "2026-01-17T00:00:00+00:'
    '00",\n              "precipitation": 10.0,\n              "temperat'
    'ure": 10.0\n            },\n            {\n              "timestamp"'
    ': "2026-01-18T00:00:00+00:00",\n              "precipitation": 10.'
    '0,\n              "temperature": 10.0\n            },\n            {'
    '\n              "timestamp": "2026-01-19T00:00:00+00:00",\n        '
    '      "precipitation": 10.0,\n              "temperature": 10.0\n  '
    '          }\n          ],\n          "future_dynamic": [],\n        '
    '  "static": [\n            {\n              "area_km2": 100.0\n     '
    '       }\n          ],\n          "time_step_seconds": 86400,\n     '
    '     "val_start": null\n        },\n        "00000000-0000-0000-000'
    '0-00000001637e": {\n          "past_targets": [\n            {\n    '
    '          "timestamp": "2026-01-10T00:00:00+00:00",\n             '
    ' "discharge": 20.0\n            },\n            {\n              "ti'
    'mestamp": "2026-01-11T00:00:00+00:00",\n              "discharge":'
    ' 21.0\n            },\n            {\n              "timestamp": "20'
    '26-01-12T00:00:00+00:00",\n              "discharge": 22.0\n       '
    '     },\n            {\n              "timestamp": "2026-01-13T00:0'
    '0:00+00:00",\n              "discharge": 23.0\n            },\n     '
    '       {\n              "timestamp": "2026-01-14T00:00:00+00:00",\n'
    '              "discharge": 24.0\n            },\n            {\n    '
    '          "timestamp": "2026-01-15T00:00:00+00:00",\n             '
    ' "discharge": 25.0\n            },\n            {\n              "ti'
    'mestamp": "2026-01-16T00:00:00+00:00",\n              "discharge":'
    ' 26.0\n            },\n            {\n              "timestamp": "20'
    '26-01-17T00:00:00+00:00",\n              "discharge": 27.0\n       '
    '     },\n            {\n              "timestamp": "2026-01-18T00:0'
    '0:00+00:00",\n              "discharge": 28.0\n            },\n     '
    '       {\n              "timestamp": "2026-01-19T00:00:00+00:00",\n'
    '              "discharge": 29.0\n            }\n          ],\n      '
    '    "past_dynamic": [\n            {\n              "timestamp": "2'
    '026-01-10T00:00:00+00:00",\n              "precipitation": 10.0,\n '
    '             "temperature": 10.0\n            },\n            {\n   '
    '           "timestamp": "2026-01-11T00:00:00+00:00",\n            '
    '  "precipitation": 10.0,\n              "temperature": 10.0\n      '
    '      },\n            {\n              "timestamp": "2026-01-12T00:'
    '00:00+00:00",\n              "precipitation": 10.0,\n              '
    '"temperature": 10.0\n            },\n            {\n              "t'
    'imestamp": "2026-01-13T00:00:00+00:00",\n              "precipitat'
    'ion": 10.0,\n              "temperature": 10.0\n            },\n    '
    '        {\n              "timestamp": "2026-01-14T00:00:00+00:00",'
    '\n              "precipitation": 10.0,\n              "temperature"'
    ': 10.0\n            },\n            {\n              "timestamp": "2'
    '026-01-15T00:00:00+00:00",\n              "precipitation": 10.0,\n '
    '             "temperature": 10.0\n            },\n            {\n   '
    '           "timestamp": "2026-01-16T00:00:00+00:00",\n            '
    '  "precipitation": 10.0,\n              "temperature": 10.0\n      '
    '      },\n            {\n              "timestamp": "2026-01-17T00:'
    '00:00+00:00",\n              "precipitation": 10.0,\n              '
    '"temperature": 10.0\n            },\n            {\n              "t'
    'imestamp": "2026-01-18T00:00:00+00:00",\n              "precipitat'
    'ion": 10.0,\n              "temperature": 10.0\n            },\n    '
    '        {\n              "timestamp": "2026-01-19T00:00:00+00:00",'
    '\n              "precipitation": 10.0,\n              "temperature"'
    ': 10.0\n            }\n          ],\n          "future_dynamic": [],'
    '\n          "static": [\n            {\n              "area_km2": 10'
    '0.0\n            }\n          ],\n          "time_step_seconds": 864'
    '00,\n          "val_start": null\n        }\n      },\n      "delete_'
    'ordinary_ids": [],\n      "allowed_station_update_attempts": 6,\n  '
    '    "station_targets": [\n        [\n          "discharge"\n        '
    '],\n        [\n          "discharge"\n        ],\n        [\n         '
    ' "discharge"\n        ],\n        [\n          "discharge"\n        ]'
    ',\n        [\n          "discharge"\n        ],\n        [\n          '
    '"discharge"\n        ]\n      ],\n      "station_updated_at": "2026-'
    '01-20T00:00:00+00:00",\n      "observation_count": 70,\n      "prot'
    'ected_count": 10,\n      "forcing_count": 120,\n      "public_call_'
    'inputs": {\n        "training_window": {\n          "start": "2026-'
    '01-10T00:00:00+00:00",\n          "end": "2026-01-20T00:00:00+00:0'
    '0",\n          "time_step_seconds": 86400,\n          "minimum_samp'
    'les": 2\n        },\n        "now": "2026-01-20T00:00:00+00:00",\n  '
    '      "application_identity": {\n          "global_admin": false,\n'
    '          "writable_tenants": [\n            "chwrr"\n          ],\n'
    '          "operator": "fixture-qualification-owner"\n        },\n  '
    '      "model_requirements": {\n          "target_parameters": [\n  '
    '          "discharge"\n          ],\n          "past_dynamic_featur'
    'es": [\n            "precipitation",\n            "temperature"\n   '
    '       ],\n          "future_dynamic_features": [],\n          "sta'
    'tic_features": [],\n          "supported_time_steps_seconds": [\n  '
    '          86400\n          ],\n          "lookback_steps": 2,\n     '
    '     "forecast_horizon_steps": 1,\n          "spatial_input_type":'
    ' "basin_average",\n          "ensemble_mode": "single",\n          '
    '"declared_aggregations": [],\n          "declared_lookbacks": [],\n'
    '          "declared_horizon_semantics": null,\n          "declared'
    '_min_future_steps": null,\n          "required_past_targets": null'
    '\n        },\n        "session_user": "test",\n        "current_user'
    '": "test",\n        "transaction_isolation": "read committed",\n   '
    '     "model_carrier": "Fresh per-call FakeStationForecastModel in'
    "stance with local14-field requirements override; shared class unc"
    'hanged"\n      }\n    },\n    {\n      "scenario": "short",\n      "pu'
    'blic_calls": 1,\n      "assembler_calls": 6,\n      "reports": [\n  '
    '      {\n          "station_id": "00000000-0000-0000-0000-00000001'
    '6379",\n          "code": "447",\n          "status": "held",\n     '
    '     "reasons": [\n            "insufficient_complete_training_sam'
    'ples"\n          ],\n          "qc_counts": [\n            [\n       '
    '       "qc_passed",\n              2\n            ]\n          ],\n  '
    '        "qc_versions": [\n            "1.2"\n          ],\n         '
    ' "usable_observations": 2,\n          "complete_samples": 0,\n     '
    '     "overlap_start": "2026-01-10T00:00:00+00:00",\n          "ove'
    'rlap_end": "2026-01-11T00:00:00+00:00"\n        },\n        {\n     '
    '     "station_id": "00000000-0000-0000-0000-00000001637a",\n      '
    '    "code": "450",\n          "status": "ready_for_model_onboardin'
    'g",\n          "reasons": [],\n          "qc_counts": [\n           '
    ' [\n              "qc_passed",\n              10\n            ]\n    '
    '      ],\n          "qc_versions": [\n            "1.2"\n          ]'
    ',\n          "usable_observations": 10,\n          "complete_sample'
    's": 8,\n          "overlap_start": "2026-01-10T00:00:00+00:00",\n  '
    '        "overlap_end": "2026-01-19T00:00:00+00:00"\n        },\n   '
    '     {\n          "station_id": "00000000-0000-0000-0000-000000016'
    '37b",\n          "code": "604.5",\n          "status": "ready_for_m'
    'odel_onboarding",\n          "reasons": [],\n          "qc_counts":'
    ' [\n            [\n              "qc_passed",\n              10\n    '
    '        ]\n          ],\n          "qc_versions": [\n            "1.'
    '2"\n          ],\n          "usable_observations": 10,\n          "c'
    'omplete_samples": 8,\n          "overlap_start": "2026-01-10T00:00'
    ':00+00:00",\n          "overlap_end": "2026-01-19T00:00:00+00:00"\n'
    '        },\n        {\n          "station_id": "00000000-0000-0000-'
    '0000-00000001637c",\n          "code": "647",\n          "status": '
    '"ready_for_model_onboarding",\n          "reasons": [],\n          '
    '"qc_counts": [\n            [\n              "qc_passed",\n         '
    '     10\n            ]\n          ],\n          "qc_versions": [\n   '
    '         "1.2"\n          ],\n          "usable_observations": 10,\n'
    '          "complete_samples": 8,\n          "overlap_start": "2026'
    '-01-10T00:00:00+00:00",\n          "overlap_end": "2026-01-19T00:0'
    '0:00+00:00"\n        },\n        {\n          "station_id": "0000000'
    '0-0000-0000-0000-00000001637d",\n          "code": "670",\n        '
    '  "status": "ready_for_model_onboarding",\n          "reasons": []'
    ',\n          "qc_counts": [\n            [\n              "qc_passed'
    '",\n              10\n            ]\n          ],\n          "qc_vers'
    'ions": [\n            "1.2"\n          ],\n          "usable_observa'
    'tions": 10,\n          "complete_samples": 8,\n          "overlap_s'
    'tart": "2026-01-10T00:00:00+00:00",\n          "overlap_end": "202'
    '6-01-19T00:00:00+00:00"\n        },\n        {\n          "station_i'
    'd": "00000000-0000-0000-0000-00000001637e",\n          "code": "68'
    '4",\n          "status": "ready_for_model_onboarding",\n          "'
    'reasons": [],\n          "qc_counts": [\n            [\n            '
    '  "qc_passed",\n              10\n            ]\n          ],\n      '
    '    "qc_versions": [\n            "1.2"\n          ],\n          "us'
    'able_observations": 10,\n          "complete_samples": 8,\n        '
    '  "overlap_start": "2026-01-10T00:00:00+00:00",\n          "overla'
    'p_end": "2026-01-19T00:00:00+00:00"\n        }\n      ],\n      "ass'
    'embler_results": {\n        "00000000-0000-0000-0000-000000016379"'
    ': {\n          "past_targets": [\n            {\n              "time'
    'stamp": "2026-01-10T00:00:00+00:00",\n              "discharge": 2'
    '0.0\n            },\n            {\n              "timestamp": "2026'
    '-01-11T00:00:00+00:00",\n              "discharge": 21.0\n         '
    '   }\n          ],\n          "past_dynamic": [\n            {\n     '
    '         "timestamp": "2026-01-10T00:00:00+00:00",\n              '
    '"precipitation": 10.0,\n              "temperature": 10.0\n        '
    '    },\n            {\n              "timestamp": "2026-01-11T00:00'
    ':00+00:00",\n              "precipitation": 10.0,\n              "t'
    'emperature": 10.0\n            },\n            {\n              "tim'
    'estamp": "2026-01-12T00:00:00+00:00",\n              "precipitatio'
    'n": 10.0,\n              "temperature": 10.0\n            },\n      '
    '      {\n              "timestamp": "2026-01-13T00:00:00+00:00",\n '
    '             "precipitation": 10.0,\n              "temperature": '
    '10.0\n            },\n            {\n              "timestamp": "202'
    '6-01-14T00:00:00+00:00",\n              "precipitation": 10.0,\n   '
    '           "temperature": 10.0\n            },\n            {\n     '
    '         "timestamp": "2026-01-15T00:00:00+00:00",\n              '
    '"precipitation": 10.0,\n              "temperature": 10.0\n        '
    '    },\n            {\n              "timestamp": "2026-01-16T00:00'
    ':00+00:00",\n              "precipitation": 10.0,\n              "t'
    'emperature": 10.0\n            },\n            {\n              "tim'
    'estamp": "2026-01-17T00:00:00+00:00",\n              "precipitatio'
    'n": 10.0,\n              "temperature": 10.0\n            },\n      '
    '      {\n              "timestamp": "2026-01-18T00:00:00+00:00",\n '
    '             "precipitation": 10.0,\n              "temperature": '
    '10.0\n            },\n            {\n              "timestamp": "202'
    '6-01-19T00:00:00+00:00",\n              "precipitation": 10.0,\n   '
    '           "temperature": 10.0\n            }\n          ],\n       '
    '   "future_dynamic": [],\n          "static": [\n            {\n    '
    '          "area_km2": 100.0\n            }\n          ],\n          '
    '"time_step_seconds": 86400,\n          "val_start": null\n        }'
    ',\n        "00000000-0000-0000-0000-00000001637a": {\n          "pa'
    'st_targets": [\n            {\n              "timestamp": "2026-01-'
    '10T00:00:00+00:00",\n              "discharge": 20.0\n            }'
    ',\n            {\n              "timestamp": "2026-01-11T00:00:00+0'
    '0:00",\n              "discharge": 21.0\n            },\n           '
    ' {\n              "timestamp": "2026-01-12T00:00:00+00:00",\n      '
    '        "discharge": 22.0\n            },\n            {\n          '
    '    "timestamp": "2026-01-13T00:00:00+00:00",\n              "disc'
    'harge": 23.0\n            },\n            {\n              "timestam'
    'p": "2026-01-14T00:00:00+00:00",\n              "discharge": 24.0\n'
    '            },\n            {\n              "timestamp": "2026-01-'
    '15T00:00:00+00:00",\n              "discharge": 25.0\n            }'
    ',\n            {\n              "timestamp": "2026-01-16T00:00:00+0'
    '0:00",\n              "discharge": 26.0\n            },\n           '
    ' {\n              "timestamp": "2026-01-17T00:00:00+00:00",\n      '
    '        "discharge": 27.0\n            },\n            {\n          '
    '    "timestamp": "2026-01-18T00:00:00+00:00",\n              "disc'
    'harge": 28.0\n            },\n            {\n              "timestam'
    'p": "2026-01-19T00:00:00+00:00",\n              "discharge": 29.0\n'
    '            }\n          ],\n          "past_dynamic": [\n          '
    '  {\n              "timestamp": "2026-01-10T00:00:00+00:00",\n     '
    '         "precipitation": 10.0,\n              "temperature": 10.0'
    '\n            },\n            {\n              "timestamp": "2026-01'
    '-11T00:00:00+00:00",\n              "precipitation": 10.0,\n       '
    '       "temperature": 10.0\n            },\n            {\n         '
    '     "timestamp": "2026-01-12T00:00:00+00:00",\n              "pre'
    'cipitation": 10.0,\n              "temperature": 10.0\n            '
    '},\n            {\n              "timestamp": "2026-01-13T00:00:00+'
    '00:00",\n              "precipitation": 10.0,\n              "tempe'
    'rature": 10.0\n            },\n            {\n              "timesta'
    'mp": "2026-01-14T00:00:00+00:00",\n              "precipitation": '
    '10.0,\n              "temperature": 10.0\n            },\n          '
    '  {\n              "timestamp": "2026-01-15T00:00:00+00:00",\n     '
    '         "precipitation": 10.0,\n              "temperature": 10.0'
    '\n            },\n            {\n              "timestamp": "2026-01'
    '-16T00:00:00+00:00",\n              "precipitation": 10.0,\n       '
    '       "temperature": 10.0\n            },\n            {\n         '
    '     "timestamp": "2026-01-17T00:00:00+00:00",\n              "pre'
    'cipitation": 10.0,\n              "temperature": 10.0\n            '
    '},\n            {\n              "timestamp": "2026-01-18T00:00:00+'
    '00:00",\n              "precipitation": 10.0,\n              "tempe'
    'rature": 10.0\n            },\n            {\n              "timesta'
    'mp": "2026-01-19T00:00:00+00:00",\n              "precipitation": '
    '10.0,\n              "temperature": 10.0\n            }\n          ]'
    ',\n          "future_dynamic": [],\n          "static": [\n         '
    '   {\n              "area_km2": 100.0\n            }\n          ],\n '
    '         "time_step_seconds": 86400,\n          "val_start": null\n'
    '        },\n        "00000000-0000-0000-0000-00000001637b": {\n    '
    '      "past_targets": [\n            {\n              "timestamp": '
    '"2026-01-10T00:00:00+00:00",\n              "discharge": 20.0\n    '
    '        },\n            {\n              "timestamp": "2026-01-11T0'
    '0:00:00+00:00",\n              "discharge": 21.0\n            },\n  '
    '          {\n              "timestamp": "2026-01-12T00:00:00+00:00'
    '",\n              "discharge": 22.0\n            },\n            {\n '
    '             "timestamp": "2026-01-13T00:00:00+00:00",\n          '
    '    "discharge": 23.0\n            },\n            {\n              '
    '"timestamp": "2026-01-14T00:00:00+00:00",\n              "discharg'
    'e": 24.0\n            },\n            {\n              "timestamp": '
    '"2026-01-15T00:00:00+00:00",\n              "discharge": 25.0\n    '
    '        },\n            {\n              "timestamp": "2026-01-16T0'
    '0:00:00+00:00",\n              "discharge": 26.0\n            },\n  '
    '          {\n              "timestamp": "2026-01-17T00:00:00+00:00'
    '",\n              "discharge": 27.0\n            },\n            {\n '
    '             "timestamp": "2026-01-18T00:00:00+00:00",\n          '
    '    "discharge": 28.0\n            },\n            {\n              '
    '"timestamp": "2026-01-19T00:00:00+00:00",\n              "discharg'
    'e": 29.0\n            }\n          ],\n          "past_dynamic": [\n '
    '           {\n              "timestamp": "2026-01-10T00:00:00+00:0'
    '0",\n              "precipitation": 10.0,\n              "temperatu'
    're": 10.0\n            },\n            {\n              "timestamp":'
    ' "2026-01-11T00:00:00+00:00",\n              "precipitation": 10.0'
    ',\n              "temperature": 10.0\n            },\n            {\n'
    '              "timestamp": "2026-01-12T00:00:00+00:00",\n         '
    '     "precipitation": 10.0,\n              "temperature": 10.0\n   '
    '         },\n            {\n              "timestamp": "2026-01-13T'
    '00:00:00+00:00",\n              "precipitation": 10.0,\n           '
    '   "temperature": 10.0\n            },\n            {\n             '
    ' "timestamp": "2026-01-14T00:00:00+00:00",\n              "precipi'
    'tation": 10.0,\n              "temperature": 10.0\n            },\n '
    '           {\n              "timestamp": "2026-01-15T00:00:00+00:0'
    '0",\n              "precipitation": 10.0,\n              "temperatu'
    're": 10.0\n            },\n            {\n              "timestamp":'
    ' "2026-01-16T00:00:00+00:00",\n              "precipitation": 10.0'
    ',\n              "temperature": 10.0\n            },\n            {\n'
    '              "timestamp": "2026-01-17T00:00:00+00:00",\n         '
    '     "precipitation": 10.0,\n              "temperature": 10.0\n   '
    '         },\n            {\n              "timestamp": "2026-01-18T'
    '00:00:00+00:00",\n              "precipitation": 10.0,\n           '
    '   "temperature": 10.0\n            },\n            {\n             '
    ' "timestamp": "2026-01-19T00:00:00+00:00",\n              "precipi'
    'tation": 10.0,\n              "temperature": 10.0\n            }\n  '
    '        ],\n          "future_dynamic": [],\n          "static": [\n'
    '            {\n              "area_km2": 100.0\n            }\n     '
    '     ],\n          "time_step_seconds": 86400,\n          "val_star'
    't": null\n        },\n        "00000000-0000-0000-0000-00000001637c'
    '": {\n          "past_targets": [\n            {\n              "tim'
    'estamp": "2026-01-10T00:00:00+00:00",\n              "discharge": '
    '20.0\n            },\n            {\n              "timestamp": "202'
    '6-01-11T00:00:00+00:00",\n              "discharge": 21.0\n        '
    '    },\n            {\n              "timestamp": "2026-01-12T00:00'
    ':00+00:00",\n              "discharge": 22.0\n            },\n      '
    '      {\n              "timestamp": "2026-01-13T00:00:00+00:00",\n '
    '             "discharge": 23.0\n            },\n            {\n     '
    '         "timestamp": "2026-01-14T00:00:00+00:00",\n              '
    '"discharge": 24.0\n            },\n            {\n              "tim'
    'estamp": "2026-01-15T00:00:00+00:00",\n              "discharge": '
    '25.0\n            },\n            {\n              "timestamp": "202'
    '6-01-16T00:00:00+00:00",\n              "discharge": 26.0\n        '
    '    },\n            {\n              "timestamp": "2026-01-17T00:00'
    ':00+00:00",\n              "discharge": 27.0\n            },\n      '
    '      {\n              "timestamp": "2026-01-18T00:00:00+00:00",\n '
    '             "discharge": 28.0\n            },\n            {\n     '
    '         "timestamp": "2026-01-19T00:00:00+00:00",\n              '
    '"discharge": 29.0\n            }\n          ],\n          "past_dyna'
    'mic": [\n            {\n              "timestamp": "2026-01-10T00:0'
    '0:00+00:00",\n              "precipitation": 10.0,\n              "'
    'temperature": 10.0\n            },\n            {\n              "ti'
    'mestamp": "2026-01-11T00:00:00+00:00",\n              "precipitati'
    'on": 10.0,\n              "temperature": 10.0\n            },\n     '
    '       {\n              "timestamp": "2026-01-12T00:00:00+00:00",\n'
    '              "precipitation": 10.0,\n              "temperature":'
    ' 10.0\n            },\n            {\n              "timestamp": "20'
    '26-01-13T00:00:00+00:00",\n              "precipitation": 10.0,\n  '
    '            "temperature": 10.0\n            },\n            {\n    '
    '          "timestamp": "2026-01-14T00:00:00+00:00",\n             '
    ' "precipitation": 10.0,\n              "temperature": 10.0\n       '
    '     },\n            {\n              "timestamp": "2026-01-15T00:0'
    '0:00+00:00",\n              "precipitation": 10.0,\n              "'
    'temperature": 10.0\n            },\n            {\n              "ti'
    'mestamp": "2026-01-16T00:00:00+00:00",\n              "precipitati'
    'on": 10.0,\n              "temperature": 10.0\n            },\n     '
    '       {\n              "timestamp": "2026-01-17T00:00:00+00:00",\n'
    '              "precipitation": 10.0,\n              "temperature":'
    ' 10.0\n            },\n            {\n              "timestamp": "20'
    '26-01-18T00:00:00+00:00",\n              "precipitation": 10.0,\n  '
    '            "temperature": 10.0\n            },\n            {\n    '
    '          "timestamp": "2026-01-19T00:00:00+00:00",\n             '
    ' "precipitation": 10.0,\n              "temperature": 10.0\n       '
    '     }\n          ],\n          "future_dynamic": [],\n          "st'
    'atic": [\n            {\n              "area_km2": 100.0\n          '
    '  }\n          ],\n          "time_step_seconds": 86400,\n          '
    '"val_start": null\n        },\n        "00000000-0000-0000-0000-000'
    '00001637d": {\n          "past_targets": [\n            {\n         '
    '     "timestamp": "2026-01-10T00:00:00+00:00",\n              "dis'
    'charge": 20.0\n            },\n            {\n              "timesta'
    'mp": "2026-01-11T00:00:00+00:00",\n              "discharge": 21.0'
    '\n            },\n            {\n              "timestamp": "2026-01'
    '-12T00:00:00+00:00",\n              "discharge": 22.0\n            '
    '},\n            {\n              "timestamp": "2026-01-13T00:00:00+'
    '00:00",\n              "discharge": 23.0\n            },\n          '
    '  {\n              "timestamp": "2026-01-14T00:00:00+00:00",\n     '
    '         "discharge": 24.0\n            },\n            {\n         '
    '     "timestamp": "2026-01-15T00:00:00+00:00",\n              "dis'
    'charge": 25.0\n            },\n            {\n              "timesta'
    'mp": "2026-01-16T00:00:00+00:00",\n              "discharge": 26.0'
    '\n            },\n            {\n              "timestamp": "2026-01'
    '-17T00:00:00+00:00",\n              "discharge": 27.0\n            '
    '},\n            {\n              "timestamp": "2026-01-18T00:00:00+'
    '00:00",\n              "discharge": 28.0\n            },\n          '
    '  {\n              "timestamp": "2026-01-19T00:00:00+00:00",\n     '
    '         "discharge": 29.0\n            }\n          ],\n          "'
    'past_dynamic": [\n            {\n              "timestamp": "2026-0'
    '1-10T00:00:00+00:00",\n              "precipitation": 10.0,\n      '
    '        "temperature": 10.0\n            },\n            {\n        '
    '      "timestamp": "2026-01-11T00:00:00+00:00",\n              "pr'
    'ecipitation": 10.0,\n              "temperature": 10.0\n           '
    ' },\n            {\n              "timestamp": "2026-01-12T00:00:00'
    '+00:00",\n              "precipitation": 10.0,\n              "temp'
    'erature": 10.0\n            },\n            {\n              "timest'
    'amp": "2026-01-13T00:00:00+00:00",\n              "precipitation":'
    ' 10.0,\n              "temperature": 10.0\n            },\n         '
    '   {\n              "timestamp": "2026-01-14T00:00:00+00:00",\n    '
    '          "precipitation": 10.0,\n              "temperature": 10.'
    '0\n            },\n            {\n              "timestamp": "2026-0'
    '1-15T00:00:00+00:00",\n              "precipitation": 10.0,\n      '
    '        "temperature": 10.0\n            },\n            {\n        '
    '      "timestamp": "2026-01-16T00:00:00+00:00",\n              "pr'
    'ecipitation": 10.0,\n              "temperature": 10.0\n           '
    ' },\n            {\n              "timestamp": "2026-01-17T00:00:00'
    '+00:00",\n              "precipitation": 10.0,\n              "temp'
    'erature": 10.0\n            },\n            {\n              "timest'
    'amp": "2026-01-18T00:00:00+00:00",\n              "precipitation":'
    ' 10.0,\n              "temperature": 10.0\n            },\n         '
    '   {\n              "timestamp": "2026-01-19T00:00:00+00:00",\n    '
    '          "precipitation": 10.0,\n              "temperature": 10.'
    '0\n            }\n          ],\n          "future_dynamic": [],\n    '
    '      "static": [\n            {\n              "area_km2": 100.0\n '
    '           }\n          ],\n          "time_step_seconds": 86400,\n '
    '         "val_start": null\n        },\n        "00000000-0000-0000'
    '-0000-00000001637e": {\n          "past_targets": [\n            {\n'
    '              "timestamp": "2026-01-10T00:00:00+00:00",\n         '
    '     "discharge": 20.0\n            },\n            {\n             '
    ' "timestamp": "2026-01-11T00:00:00+00:00",\n              "dischar'
    'ge": 21.0\n            },\n            {\n              "timestamp":'
    ' "2026-01-12T00:00:00+00:00",\n              "discharge": 22.0\n   '
    '         },\n            {\n              "timestamp": "2026-01-13T'
    '00:00:00+00:00",\n              "discharge": 23.0\n            },\n '
    '           {\n              "timestamp": "2026-01-14T00:00:00+00:0'
    '0",\n              "discharge": 24.0\n            },\n            {\n'
    '              "timestamp": "2026-01-15T00:00:00+00:00",\n         '
    '     "discharge": 25.0\n            },\n            {\n             '
    ' "timestamp": "2026-01-16T00:00:00+00:00",\n              "dischar'
    'ge": 26.0\n            },\n            {\n              "timestamp":'
    ' "2026-01-17T00:00:00+00:00",\n              "discharge": 27.0\n   '
    '         },\n            {\n              "timestamp": "2026-01-18T'
    '00:00:00+00:00",\n              "discharge": 28.0\n            },\n '
    '           {\n              "timestamp": "2026-01-19T00:00:00+00:0'
    '0",\n              "discharge": 29.0\n            }\n          ],\n  '
    '        "past_dynamic": [\n            {\n              "timestamp"'
    ': "2026-01-10T00:00:00+00:00",\n              "precipitation": 10.'
    '0,\n              "temperature": 10.0\n            },\n            {'
    '\n              "timestamp": "2026-01-11T00:00:00+00:00",\n        '
    '      "precipitation": 10.0,\n              "temperature": 10.0\n  '
    '          },\n            {\n              "timestamp": "2026-01-12'
    'T00:00:00+00:00",\n              "precipitation": 10.0,\n          '
    '    "temperature": 10.0\n            },\n            {\n            '
    '  "timestamp": "2026-01-13T00:00:00+00:00",\n              "precip'
    'itation": 10.0,\n              "temperature": 10.0\n            },\n'
    '            {\n              "timestamp": "2026-01-14T00:00:00+00:'
    '00",\n              "precipitation": 10.0,\n              "temperat'
    'ure": 10.0\n            },\n            {\n              "timestamp"'
    ': "2026-01-15T00:00:00+00:00",\n              "precipitation": 10.'
    '0,\n              "temperature": 10.0\n            },\n            {'
    '\n              "timestamp": "2026-01-16T00:00:00+00:00",\n        '
    '      "precipitation": 10.0,\n              "temperature": 10.0\n  '
    '          },\n            {\n              "timestamp": "2026-01-17'
    'T00:00:00+00:00",\n              "precipitation": 10.0,\n          '
    '    "temperature": 10.0\n            },\n            {\n            '
    '  "timestamp": "2026-01-18T00:00:00+00:00",\n              "precip'
    'itation": 10.0,\n              "temperature": 10.0\n            },\n'
    '            {\n              "timestamp": "2026-01-19T00:00:00+00:'
    '00",\n              "precipitation": 10.0,\n              "temperat'
    'ure": 10.0\n            }\n          ],\n          "future_dynamic":'
    ' [],\n          "static": [\n            {\n              "area_km2"'
    ': 100.0\n            }\n          ],\n          "time_step_seconds":'
    ' 86400,\n          "val_start": null\n        }\n      },\n      "del'
    'ete_ordinary_ids": [\n        "00000000-0000-0000-0000-00000001adb'
    '3",\n        "00000000-0000-0000-0000-00000001adb4",\n        "0000'
    '0000-0000-0000-0000-00000001adb5",\n        "00000000-0000-0000-00'
    '00-00000001adb6",\n        "00000000-0000-0000-0000-00000001adb7",'
    '\n        "00000000-0000-0000-0000-00000001adb8",\n        "0000000'
    '0-0000-0000-0000-00000001adb9",\n        "00000000-0000-0000-0000-'
    '00000001adba"\n      ],\n      "allowed_station_update_attempts": 6'
    ',\n      "station_targets": [\n        null,\n        [\n          "d'
    'ischarge"\n        ],\n        [\n          "discharge"\n        ],\n '
    '       [\n          "discharge"\n        ],\n        [\n          "di'
    'scharge"\n        ],\n        [\n          "discharge"\n        ]\n   '
    '   ],\n      "station_updated_at": "2026-01-20T00:00:00+00:00",\n  '
    '    "observation_count": 62,\n      "protected_count": 10,\n      "'
    'forcing_count": 120,\n      "public_call_inputs": {\n        "train'
    'ing_window": {\n          "start": "2026-01-10T00:00:00+00:00",\n  '
    '        "end": "2026-01-20T00:00:00+00:00",\n          "time_step_'
    'seconds": 86400,\n          "minimum_samples": 2\n        },\n      '
    '  "now": "2026-01-20T00:00:00+00:00",\n        "application_identi'
    'ty": {\n          "global_admin": false,\n          "writable_tenan'
    'ts": [\n            "chwrr"\n          ],\n          "operator": "fi'
    'xture-qualification-owner"\n        },\n        "model_requirements'
    '": {\n          "target_parameters": [\n            "discharge"\n   '
    '       ],\n          "past_dynamic_features": [\n            "preci'
    'pitation",\n            "temperature"\n          ],\n          "futu'
    're_dynamic_features": [],\n          "static_features": [],\n      '
    '    "supported_time_steps_seconds": [\n            86400\n         '
    ' ],\n          "lookback_steps": 2,\n          "forecast_horizon_st'
    'eps": 1,\n          "spatial_input_type": "basin_average",\n       '
    '   "ensemble_mode": "single",\n          "declared_aggregations": '
    '[],\n          "declared_lookbacks": [],\n          "declared_horiz'
    'on_semantics": null,\n          "declared_min_future_steps": null,'
    '\n          "required_past_targets": null\n        },\n        "sess'
    'ion_user": "test",\n        "current_user": "test",\n        "trans'
    'action_isolation": "read committed",\n        "model_carrier": "Fr'
    "esh per-call FakeStationForecastModel instance with local14-field"
    ' requirements override; shared class unchanged"\n      }\n    },\n  '
    '  {\n      "scenario": "protected_only",\n      "public_calls": 1,\n'
    '      "assembler_calls": 5,\n      "reports": [\n        {\n        '
    '  "station_id": "00000000-0000-0000-0000-000000016379",\n         '
    ' "code": "447",\n          "status": "held",\n          "reasons": '
    '[\n            "no_current_qc_passed_delivery_history",\n          '
    '  "insufficient_complete_training_samples"\n          ],\n         '
    ' "qc_counts": [],\n          "qc_versions": [],\n          "usable_'
    'observations": 0,\n          "complete_samples": 0,\n          "ove'
    'rlap_start": null,\n          "overlap_end": null\n        },\n     '
    '   {\n          "station_id": "00000000-0000-0000-0000-00000001637'
    'a",\n          "code": "450",\n          "status": "ready_for_model'
    '_onboarding",\n          "reasons": [],\n          "qc_counts": [\n '
    '           [\n              "qc_passed",\n              10\n        '
    '    ]\n          ],\n          "qc_versions": [\n            "1.2"\n '
    '         ],\n          "usable_observations": 10,\n          "compl'
    'ete_samples": 8,\n          "overlap_start": "2026-01-10T00:00:00+'
    '00:00",\n          "overlap_end": "2026-01-19T00:00:00+00:00"\n    '
    '    },\n        {\n          "station_id": "00000000-0000-0000-0000'
    '-00000001637b",\n          "code": "604.5",\n          "status": "r'
    'eady_for_model_onboarding",\n          "reasons": [],\n          "q'
    'c_counts": [\n            [\n              "qc_passed",\n           '
    '   10\n            ]\n          ],\n          "qc_versions": [\n     '
    '       "1.2"\n          ],\n          "usable_observations": 10,\n  '
    '        "complete_samples": 8,\n          "overlap_start": "2026-0'
    '1-10T00:00:00+00:00",\n          "overlap_end": "2026-01-19T00:00:'
    '00+00:00"\n        },\n        {\n          "station_id": "00000000-'
    '0000-0000-0000-00000001637c",\n          "code": "647",\n          '
    '"status": "ready_for_model_onboarding",\n          "reasons": [],\n'
    '          "qc_counts": [\n            [\n              "qc_passed",'
    '\n              10\n            ]\n          ],\n          "qc_versio'
    'ns": [\n            "1.2"\n          ],\n          "usable_observati'
    'ons": 10,\n          "complete_samples": 8,\n          "overlap_sta'
    'rt": "2026-01-10T00:00:00+00:00",\n          "overlap_end": "2026-'
    '01-19T00:00:00+00:00"\n        },\n        {\n          "station_id"'
    ': "00000000-0000-0000-0000-00000001637d",\n          "code": "670"'
    ',\n          "status": "ready_for_model_onboarding",\n          "re'
    'asons": [],\n          "qc_counts": [\n            [\n              '
    '"qc_passed",\n              10\n            ]\n          ],\n        '
    '  "qc_versions": [\n            "1.2"\n          ],\n          "usab'
    'le_observations": 10,\n          "complete_samples": 8,\n          '
    '"overlap_start": "2026-01-10T00:00:00+00:00",\n          "overlap_'
    'end": "2026-01-19T00:00:00+00:00"\n        },\n        {\n          '
    '"station_id": "00000000-0000-0000-0000-00000001637e",\n          "'
    'code": "684",\n          "status": "ready_for_model_onboarding",\n '
    '         "reasons": [],\n          "qc_counts": [\n            [\n  '
    '            "qc_passed",\n              10\n            ]\n         '
    ' ],\n          "qc_versions": [\n            "1.2"\n          ],\n   '
    '       "usable_observations": 10,\n          "complete_samples": 8'
    ',\n          "overlap_start": "2026-01-10T00:00:00+00:00",\n       '
    '   "overlap_end": "2026-01-19T00:00:00+00:00"\n        }\n      ],\n'
    '      "assembler_results": {\n        "00000000-0000-0000-0000-000'
    '00001637a": {\n          "past_targets": [\n            {\n         '
    '     "timestamp": "2026-01-10T00:00:00+00:00",\n              "dis'
    'charge": 20.0\n            },\n            {\n              "timesta'
    'mp": "2026-01-11T00:00:00+00:00",\n              "discharge": 21.0'
    '\n            },\n            {\n              "timestamp": "2026-01'
    '-12T00:00:00+00:00",\n              "discharge": 22.0\n            '
    '},\n            {\n              "timestamp": "2026-01-13T00:00:00+'
    '00:00",\n              "discharge": 23.0\n            },\n          '
    '  {\n              "timestamp": "2026-01-14T00:00:00+00:00",\n     '
    '         "discharge": 24.0\n            },\n            {\n         '
    '     "timestamp": "2026-01-15T00:00:00+00:00",\n              "dis'
    'charge": 25.0\n            },\n            {\n              "timesta'
    'mp": "2026-01-16T00:00:00+00:00",\n              "discharge": 26.0'
    '\n            },\n            {\n              "timestamp": "2026-01'
    '-17T00:00:00+00:00",\n              "discharge": 27.0\n            '
    '},\n            {\n              "timestamp": "2026-01-18T00:00:00+'
    '00:00",\n              "discharge": 28.0\n            },\n          '
    '  {\n              "timestamp": "2026-01-19T00:00:00+00:00",\n     '
    '         "discharge": 29.0\n            }\n          ],\n          "'
    'past_dynamic": [\n            {\n              "timestamp": "2026-0'
    '1-10T00:00:00+00:00",\n              "precipitation": 10.0,\n      '
    '        "temperature": 10.0\n            },\n            {\n        '
    '      "timestamp": "2026-01-11T00:00:00+00:00",\n              "pr'
    'ecipitation": 10.0,\n              "temperature": 10.0\n           '
    ' },\n            {\n              "timestamp": "2026-01-12T00:00:00'
    '+00:00",\n              "precipitation": 10.0,\n              "temp'
    'erature": 10.0\n            },\n            {\n              "timest'
    'amp": "2026-01-13T00:00:00+00:00",\n              "precipitation":'
    ' 10.0,\n              "temperature": 10.0\n            },\n         '
    '   {\n              "timestamp": "2026-01-14T00:00:00+00:00",\n    '
    '          "precipitation": 10.0,\n              "temperature": 10.'
    '0\n            },\n            {\n              "timestamp": "2026-0'
    '1-15T00:00:00+00:00",\n              "precipitation": 10.0,\n      '
    '        "temperature": 10.0\n            },\n            {\n        '
    '      "timestamp": "2026-01-16T00:00:00+00:00",\n              "pr'
    'ecipitation": 10.0,\n              "temperature": 10.0\n           '
    ' },\n            {\n              "timestamp": "2026-01-17T00:00:00'
    '+00:00",\n              "precipitation": 10.0,\n              "temp'
    'erature": 10.0\n            },\n            {\n              "timest'
    'amp": "2026-01-18T00:00:00+00:00",\n              "precipitation":'
    ' 10.0,\n              "temperature": 10.0\n            },\n         '
    '   {\n              "timestamp": "2026-01-19T00:00:00+00:00",\n    '
    '          "precipitation": 10.0,\n              "temperature": 10.'
    '0\n            }\n          ],\n          "future_dynamic": [],\n    '
    '      "static": [\n            {\n              "area_km2": 100.0\n '
    '           }\n          ],\n          "time_step_seconds": 86400,\n '
    '         "val_start": null\n        },\n        "00000000-0000-0000'
    '-0000-00000001637b": {\n          "past_targets": [\n            {\n'
    '              "timestamp": "2026-01-10T00:00:00+00:00",\n         '
    '     "discharge": 20.0\n            },\n            {\n             '
    ' "timestamp": "2026-01-11T00:00:00+00:00",\n              "dischar'
    'ge": 21.0\n            },\n            {\n              "timestamp":'
    ' "2026-01-12T00:00:00+00:00",\n              "discharge": 22.0\n   '
    '         },\n            {\n              "timestamp": "2026-01-13T'
    '00:00:00+00:00",\n              "discharge": 23.0\n            },\n '
    '           {\n              "timestamp": "2026-01-14T00:00:00+00:0'
    '0",\n              "discharge": 24.0\n            },\n            {\n'
    '              "timestamp": "2026-01-15T00:00:00+00:00",\n         '
    '     "discharge": 25.0\n            },\n            {\n             '
    ' "timestamp": "2026-01-16T00:00:00+00:00",\n              "dischar'
    'ge": 26.0\n            },\n            {\n              "timestamp":'
    ' "2026-01-17T00:00:00+00:00",\n              "discharge": 27.0\n   '
    '         },\n            {\n              "timestamp": "2026-01-18T'
    '00:00:00+00:00",\n              "discharge": 28.0\n            },\n '
    '           {\n              "timestamp": "2026-01-19T00:00:00+00:0'
    '0",\n              "discharge": 29.0\n            }\n          ],\n  '
    '        "past_dynamic": [\n            {\n              "timestamp"'
    ': "2026-01-10T00:00:00+00:00",\n              "precipitation": 10.'
    '0,\n              "temperature": 10.0\n            },\n            {'
    '\n              "timestamp": "2026-01-11T00:00:00+00:00",\n        '
    '      "precipitation": 10.0,\n              "temperature": 10.0\n  '
    '          },\n            {\n              "timestamp": "2026-01-12'
    'T00:00:00+00:00",\n              "precipitation": 10.0,\n          '
    '    "temperature": 10.0\n            },\n            {\n            '
    '  "timestamp": "2026-01-13T00:00:00+00:00",\n              "precip'
    'itation": 10.0,\n              "temperature": 10.0\n            },\n'
    '            {\n              "timestamp": "2026-01-14T00:00:00+00:'
    '00",\n              "precipitation": 10.0,\n              "temperat'
    'ure": 10.0\n            },\n            {\n              "timestamp"'
    ': "2026-01-15T00:00:00+00:00",\n              "precipitation": 10.'
    '0,\n              "temperature": 10.0\n            },\n            {'
    '\n              "timestamp": "2026-01-16T00:00:00+00:00",\n        '
    '      "precipitation": 10.0,\n              "temperature": 10.0\n  '
    '          },\n            {\n              "timestamp": "2026-01-17'
    'T00:00:00+00:00",\n              "precipitation": 10.0,\n          '
    '    "temperature": 10.0\n            },\n            {\n            '
    '  "timestamp": "2026-01-18T00:00:00+00:00",\n              "precip'
    'itation": 10.0,\n              "temperature": 10.0\n            },\n'
    '            {\n              "timestamp": "2026-01-19T00:00:00+00:'
    '00",\n              "precipitation": 10.0,\n              "temperat'
    'ure": 10.0\n            }\n          ],\n          "future_dynamic":'
    ' [],\n          "static": [\n            {\n              "area_km2"'
    ': 100.0\n            }\n          ],\n          "time_step_seconds":'
    ' 86400,\n          "val_start": null\n        },\n        "00000000-'
    '0000-0000-0000-00000001637c": {\n          "past_targets": [\n     '
    '       {\n              "timestamp": "2026-01-10T00:00:00+00:00",\n'
    '              "discharge": 20.0\n            },\n            {\n    '
    '          "timestamp": "2026-01-11T00:00:00+00:00",\n             '
    ' "discharge": 21.0\n            },\n            {\n              "ti'
    'mestamp": "2026-01-12T00:00:00+00:00",\n              "discharge":'
    ' 22.0\n            },\n            {\n              "timestamp": "20'
    '26-01-13T00:00:00+00:00",\n              "discharge": 23.0\n       '
    '     },\n            {\n              "timestamp": "2026-01-14T00:0'
    '0:00+00:00",\n              "discharge": 24.0\n            },\n     '
    '       {\n              "timestamp": "2026-01-15T00:00:00+00:00",\n'
    '              "discharge": 25.0\n            },\n            {\n    '
    '          "timestamp": "2026-01-16T00:00:00+00:00",\n             '
    ' "discharge": 26.0\n            },\n            {\n              "ti'
    'mestamp": "2026-01-17T00:00:00+00:00",\n              "discharge":'
    ' 27.0\n            },\n            {\n              "timestamp": "20'
    '26-01-18T00:00:00+00:00",\n              "discharge": 28.0\n       '
    '     },\n            {\n              "timestamp": "2026-01-19T00:0'
    '0:00+00:00",\n              "discharge": 29.0\n            }\n      '
    '    ],\n          "past_dynamic": [\n            {\n              "t'
    'imestamp": "2026-01-10T00:00:00+00:00",\n              "precipitat'
    'ion": 10.0,\n              "temperature": 10.0\n            },\n    '
    '        {\n              "timestamp": "2026-01-11T00:00:00+00:00",'
    '\n              "precipitation": 10.0,\n              "temperature"'
    ': 10.0\n            },\n            {\n              "timestamp": "2'
    '026-01-12T00:00:00+00:00",\n              "precipitation": 10.0,\n '
    '             "temperature": 10.0\n            },\n            {\n   '
    '           "timestamp": "2026-01-13T00:00:00+00:00",\n            '
    '  "precipitation": 10.0,\n              "temperature": 10.0\n      '
    '      },\n            {\n              "timestamp": "2026-01-14T00:'
    '00:00+00:00",\n              "precipitation": 10.0,\n              '
    '"temperature": 10.0\n            },\n            {\n              "t'
    'imestamp": "2026-01-15T00:00:00+00:00",\n              "precipitat'
    'ion": 10.0,\n              "temperature": 10.0\n            },\n    '
    '        {\n              "timestamp": "2026-01-16T00:00:00+00:00",'
    '\n              "precipitation": 10.0,\n              "temperature"'
    ': 10.0\n            },\n            {\n              "timestamp": "2'
    '026-01-17T00:00:00+00:00",\n              "precipitation": 10.0,\n '
    '             "temperature": 10.0\n            },\n            {\n   '
    '           "timestamp": "2026-01-18T00:00:00+00:00",\n            '
    '  "precipitation": 10.0,\n              "temperature": 10.0\n      '
    '      },\n            {\n              "timestamp": "2026-01-19T00:'
    '00:00+00:00",\n              "precipitation": 10.0,\n              '
    '"temperature": 10.0\n            }\n          ],\n          "future_'
    'dynamic": [],\n          "static": [\n            {\n              "'
    'area_km2": 100.0\n            }\n          ],\n          "time_step_'
    'seconds": 86400,\n          "val_start": null\n        },\n        "'
    '00000000-0000-0000-0000-00000001637d": {\n          "past_targets"'
    ': [\n            {\n              "timestamp": "2026-01-10T00:00:00'
    '+00:00",\n              "discharge": 20.0\n            },\n         '
    '   {\n              "timestamp": "2026-01-11T00:00:00+00:00",\n    '
    '          "discharge": 21.0\n            },\n            {\n        '
    '      "timestamp": "2026-01-12T00:00:00+00:00",\n              "di'
    'scharge": 22.0\n            },\n            {\n              "timest'
    'amp": "2026-01-13T00:00:00+00:00",\n              "discharge": 23.'
    '0\n            },\n            {\n              "timestamp": "2026-0'
    '1-14T00:00:00+00:00",\n              "discharge": 24.0\n           '
    ' },\n            {\n              "timestamp": "2026-01-15T00:00:00'
    '+00:00",\n              "discharge": 25.0\n            },\n         '
    '   {\n              "timestamp": "2026-01-16T00:00:00+00:00",\n    '
    '          "discharge": 26.0\n            },\n            {\n        '
    '      "timestamp": "2026-01-17T00:00:00+00:00",\n              "di'
    'scharge": 27.0\n            },\n            {\n              "timest'
    'amp": "2026-01-18T00:00:00+00:00",\n              "discharge": 28.'
    '0\n            },\n            {\n              "timestamp": "2026-0'
    '1-19T00:00:00+00:00",\n              "discharge": 29.0\n           '
    ' }\n          ],\n          "past_dynamic": [\n            {\n       '
    '       "timestamp": "2026-01-10T00:00:00+00:00",\n              "p'
    'recipitation": 10.0,\n              "temperature": 10.0\n          '
    '  },\n            {\n              "timestamp": "2026-01-11T00:00:0'
    '0+00:00",\n              "precipitation": 10.0,\n              "tem'
    'perature": 10.0\n            },\n            {\n              "times'
    'tamp": "2026-01-12T00:00:00+00:00",\n              "precipitation"'
    ': 10.0,\n              "temperature": 10.0\n            },\n        '
    '    {\n              "timestamp": "2026-01-13T00:00:00+00:00",\n   '
    '           "precipitation": 10.0,\n              "temperature": 10'
    '.0\n            },\n            {\n              "timestamp": "2026-'
    '01-14T00:00:00+00:00",\n              "precipitation": 10.0,\n     '
    '         "temperature": 10.0\n            },\n            {\n       '
    '       "timestamp": "2026-01-15T00:00:00+00:00",\n              "p'
    'recipitation": 10.0,\n              "temperature": 10.0\n          '
    '  },\n            {\n              "timestamp": "2026-01-16T00:00:0'
    '0+00:00",\n              "precipitation": 10.0,\n              "tem'
    'perature": 10.0\n            },\n            {\n              "times'
    'tamp": "2026-01-17T00:00:00+00:00",\n              "precipitation"'
    ': 10.0,\n              "temperature": 10.0\n            },\n        '
    '    {\n              "timestamp": "2026-01-18T00:00:00+00:00",\n   '
    '           "precipitation": 10.0,\n              "temperature": 10'
    '.0\n            },\n            {\n              "timestamp": "2026-'
    '01-19T00:00:00+00:00",\n              "precipitation": 10.0,\n     '
    '         "temperature": 10.0\n            }\n          ],\n         '
    ' "future_dynamic": [],\n          "static": [\n            {\n      '
    '        "area_km2": 100.0\n            }\n          ],\n          "t'
    'ime_step_seconds": 86400,\n          "val_start": null\n        },\n'
    '        "00000000-0000-0000-0000-00000001637e": {\n          "past'
    '_targets": [\n            {\n              "timestamp": "2026-01-10'
    'T00:00:00+00:00",\n              "discharge": 20.0\n            },\n'
    '            {\n              "timestamp": "2026-01-11T00:00:00+00:'
    '00",\n              "discharge": 21.0\n            },\n            {'
    '\n              "timestamp": "2026-01-12T00:00:00+00:00",\n        '
    '      "discharge": 22.0\n            },\n            {\n            '
    '  "timestamp": "2026-01-13T00:00:00+00:00",\n              "discha'
    'rge": 23.0\n            },\n            {\n              "timestamp"'
    ': "2026-01-14T00:00:00+00:00",\n              "discharge": 24.0\n  '
    '          },\n            {\n              "timestamp": "2026-01-15'
    'T00:00:00+00:00",\n              "discharge": 25.0\n            },\n'
    '            {\n              "timestamp": "2026-01-16T00:00:00+00:'
    '00",\n              "discharge": 26.0\n            },\n            {'
    '\n              "timestamp": "2026-01-17T00:00:00+00:00",\n        '
    '      "discharge": 27.0\n            },\n            {\n            '
    '  "timestamp": "2026-01-18T00:00:00+00:00",\n              "discha'
    'rge": 28.0\n            },\n            {\n              "timestamp"'
    ': "2026-01-19T00:00:00+00:00",\n              "discharge": 29.0\n  '
    '          }\n          ],\n          "past_dynamic": [\n            '
    '{\n              "timestamp": "2026-01-10T00:00:00+00:00",\n       '
    '       "precipitation": 10.0,\n              "temperature": 10.0\n '
    '           },\n            {\n              "timestamp": "2026-01-1'
    '1T00:00:00+00:00",\n              "precipitation": 10.0,\n         '
    '     "temperature": 10.0\n            },\n            {\n           '
    '   "timestamp": "2026-01-12T00:00:00+00:00",\n              "preci'
    'pitation": 10.0,\n              "temperature": 10.0\n            },'
    '\n            {\n              "timestamp": "2026-01-13T00:00:00+00'
    ':00",\n              "precipitation": 10.0,\n              "tempera'
    'ture": 10.0\n            },\n            {\n              "timestamp'
    '": "2026-01-14T00:00:00+00:00",\n              "precipitation": 10'
    '.0,\n              "temperature": 10.0\n            },\n            '
    '{\n              "timestamp": "2026-01-15T00:00:00+00:00",\n       '
    '       "precipitation": 10.0,\n              "temperature": 10.0\n '
    '           },\n            {\n              "timestamp": "2026-01-1'
    '6T00:00:00+00:00",\n              "precipitation": 10.0,\n         '
    '     "temperature": 10.0\n            },\n            {\n           '
    '   "timestamp": "2026-01-17T00:00:00+00:00",\n              "preci'
    'pitation": 10.0,\n              "temperature": 10.0\n            },'
    '\n            {\n              "timestamp": "2026-01-18T00:00:00+00'
    ':00",\n              "precipitation": 10.0,\n              "tempera'
    'ture": 10.0\n            },\n            {\n              "timestamp'
    '": "2026-01-19T00:00:00+00:00",\n              "precipitation": 10'
    '.0,\n              "temperature": 10.0\n            }\n          ],\n'
    '          "future_dynamic": [],\n          "static": [\n           '
    ' {\n              "area_km2": 100.0\n            }\n          ],\n   '
    '       "time_step_seconds": 86400,\n          "val_start": null\n  '
    '      }\n      },\n      "delete_ordinary_ids": [\n        "00000000'
    '-0000-0000-0000-00000001adb1",\n        "00000000-0000-0000-0000-0'
    '0000001adb2",\n        "00000000-0000-0000-0000-00000001adb3",\n   '
    '     "00000000-0000-0000-0000-00000001adb4",\n        "00000000-00'
    '00-0000-0000-00000001adb5",\n        "00000000-0000-0000-0000-0000'
    '0001adb6",\n        "00000000-0000-0000-0000-00000001adb7",\n      '
    '  "00000000-0000-0000-0000-00000001adb8",\n        "00000000-0000-'
    '0000-0000-00000001adb9",\n        "00000000-0000-0000-0000-0000000'
    '1adba"\n      ],\n      "allowed_station_update_attempts": 6,\n     '
    ' "station_targets": [\n        null,\n        [\n          "discharg'
    'e"\n        ],\n        [\n          "discharge"\n        ],\n        '
    '[\n          "discharge"\n        ],\n        [\n          "discharge'
    '"\n        ],\n        [\n          "discharge"\n        ]\n      ],\n '
    '     "station_updated_at": "2026-01-20T00:00:00+00:00",\n      "ob'
    'servation_count": 60,\n      "protected_count": 10,\n      "forcing'
    '_count": 120,\n      "public_call_inputs": {\n        "training_win'
    'dow": {\n          "start": "2026-01-10T00:00:00+00:00",\n         '
    ' "end": "2026-01-20T00:00:00+00:00",\n          "time_step_seconds'
    '": 86400,\n          "minimum_samples": 2\n        },\n        "now"'
    ': "2026-01-20T00:00:00+00:00",\n        "application_identity": {\n'
    '          "global_admin": false,\n          "writable_tenants": [\n'
    '            "chwrr"\n          ],\n          "operator": "fixture-q'
    'ualification-owner"\n        },\n        "model_requirements": {\n  '
    '        "target_parameters": [\n            "discharge"\n          '
    '],\n          "past_dynamic_features": [\n            "precipitatio'
    'n",\n            "temperature"\n          ],\n          "future_dyna'
    'mic_features": [],\n          "static_features": [],\n          "su'
    'pported_time_steps_seconds": [\n            86400\n          ],\n   '
    '       "lookback_steps": 2,\n          "forecast_horizon_steps": 1'
    ',\n          "spatial_input_type": "basin_average",\n          "ens'
    'emble_mode": "single",\n          "declared_aggregations": [],\n   '
    '       "declared_lookbacks": [],\n          "declared_horizon_sema'
    'ntics": null,\n          "declared_min_future_steps": null,\n      '
    '    "required_past_targets": null\n        },\n        "session_use'
    'r": "test",\n        "current_user": "test",\n        "transaction_'
    'isolation": "read committed",\n        "model_carrier": "Fresh per'
    "-call FakeStationForecastModel instance with local14-field requir"
    'ements override; shared class unchanged"\n      }\n    }\n  ],\n  "fr'
    'ame_schemas": {\n    "past_targets": {\n      "timestamp": "Datetim'
    'e(us,UTC)",\n      "discharge": "Float64"\n    },\n    "past_dynamic'
    '": {\n      "timestamp": "Datetime(us,UTC)",\n      "precipitation"'
    ': "Float64",\n      "temperature": "Float64"\n    },\n    "future_dy'
    'namic": {\n      "timestamp": "Datetime(us,UTC)"\n    },\n    "stati'
    'c": {\n      "area_km2": "Float64"\n    }\n  },\n  "unchanged_tables"'
    ': [\n    "access_token_stations",\n    "access_tokens",\n    "alerts'
    '",\n    "audit_log",\n    "basin_static_packages",\n    "basin_versi'
    'ons",\n    "basins",\n    "calculated_station_formulas",\n    "clim_'
    'baselines",\n    "flow_regime_configs",\n    "forecast_evidence",\n '
    '   "forecast_evidence_blobs",\n    "forecast_input_stations",\n    '
    '"forecast_preservation_attestations",\n    "forecast_publication_d'
    'ecisions",\n    "forecast_publication_events",\n    "forecast_publi'
    'cation_selections",\n    "forecast_publication_sequence",\n    "for'
    'ecast_qc_overrides",\n    "forecast_values",\n    "forecasts",\n    '
    '"group_model_assignments",\n    "hindcast_forecasts",\n    "hindcas'
    't_values",\n    "historical_forcing",\n    "human_station_grants",\n'
    '    "measurement_feed_evidence",\n    "model_artifact_basin_versio'
    'ns",\n    "model_artifact_provenance",\n    "model_artifact_warm_st'
    'art",\n    "model_artifacts",\n    "model_assignments",\n    "model_'
    'states",\n    "models",\n    "observation_versions",\n    "observati'
    'ons",\n    "parameters",\n    "pipeline_health",\n    "protected_bac'
    'kup_forecast_proofs",\n    "protected_backup_health",\n    "provisi'
    'onal_discharge_permissions",\n    "provisional_discharges",\n    "r'
    'ating_curves",\n    "rating_reference_proofs",\n    "recap_gateway_'
    'polygon_bindings",\n    "rejected_forecasts",\n    "skill_diagrams"'
    ',\n    "skill_generations",\n    "skill_scores",\n    "station_group'
    '_members",\n    "station_groups",\n    "station_thresholds",\n    "s'
    'tation_weather_sources",\n    "tenants",\n    "user_external_identi'
    'ties",\n    "users",\n    "weather_forecasts"\n  ],\n  "model_require'
    'ments": {\n    "target_parameters": [\n      "discharge"\n    ],\n   '
    ' "past_dynamic_features": [\n      "precipitation",\n      "tempera'
    'ture"\n    ],\n    "future_dynamic_features": [],\n    "static_featu'
    'res": [],\n    "supported_time_steps_seconds": [\n      86400\n    ]'
    ',\n    "lookback_steps": 2,\n    "forecast_horizon_steps": 1,\n    "'
    'spatial_input_type": "basin_average",\n    "ensemble_mode": "singl'
    'e",\n    "declared_aggregations": [],\n    "declared_lookbacks": []'
    ',\n    "declared_horizon_semantics": null,\n    "declared_min_futur'
    'e_steps": null,\n    "required_past_targets": null\n  },\n  "trainin'
    'g_window": {\n    "start": "2026-01-10T00:00:00+00:00",\n    "end":'
    ' "2026-01-20T00:00:00+00:00",\n    "time_step_seconds": 86400,\n   '
    ' "minimum_samples": 2\n  },\n  "basin_oracle_contract": {\n    "basi'
    'ns_section_role": "Domain Basin constructor inputs only; created_'
    "at is required domain input but ignored by PgBasinStore.store_bas"
    'in. Never compare this field to persisted created_at.",\n    "pers'
    'isted_projection_fields": [\n      "id",\n      "code",\n      "name'
    '",\n      "geometry_wkt",\n      "area_km2",\n      "attributes",\n  '
    '    "regional_basin",\n      "band_geometries",\n      "network",\n '
    '     "package_id"\n    ],\n    "basins_setup_generated": {\n      "c'
    'reated_at": "server now(), transaction start; capture aware times'
    'tamp after seed, exact after-call equality"\n    },\n    "version_f'
    'ields_literal": {\n      "version": 1,\n      "package_id": null,\n '
    '     "band_geometries": null,\n      "gateway_mapping": null,\n    '
    '  "superseded_at": null\n    },\n    "version_fields_from_literal_b'
    'asin": [\n      "basin_id<-id",\n      "geometry<-geometry_wkt",\n  '
    '    "attributes",\n      "area_km2"\n    ],\n    "version_setup_gene'
    'rated": {\n      "id": "client UUID4 in PgBasinStore, associate by'
    ' literal basin_id+version1; unique UUID, capture exact",\n      "c'
    'reated_at": "server clock_timestamp(), not injected C; aware time'
    'stamp, capture exact"\n    },\n    "version_other_defaults": "No ot'
    "her server-default columns in pinned basin_versions metadata; id "
    "is client generated, superseded_at is explicitly NULL. Preserve a"
    'll row fields; unknown schema changes STOP.",\n    "projection_rea'
    'dback": "Check persisted literal fields; compare geometry to lite'
    "ral WKT semantics. Capture entire actual projection/version raw r"
    "ows including IDs and times before call; require exact after equa"
    'lity; never patch DB clock or rewrite store."\n  },\n  "station_arr'
    'ay_comparison": {\n    "scope": [\n      "context.compiled_paramete'
    'rs pre-bind Python UPDATE values",\n      "raw stations readback",'
    '\n      "typed PgStationStore StationConfig readback"\n    ],\n    "'
    'compiled_parameters": {\n      "measured_parameters": "list[str], '
    'unique members exactly discharge and water_level; unordered",\n   '
    '   "forecast_targets_initial_or_HELD": "None (Python pre-bind nul'
    'l input); not []",\n      "forecast_targets_READY": "list[str], un'
    'ique member discharge"\n    },\n    "raw_readback": {\n      "measur'
    'ed_parameters": "non-null list of unique strings with exact two-m'
    'ember set",\n      "forecast_targets_initial_or_HELD": "driver-dec'
    'oded None; [] fails this raw oracle",\n      "forecast_targets_REA'
    'DY": "list with unique member discharge",\n      "null_limit": "Dr'
    "iver-decoded None does not distinguish SQL NULL from JSON null in"
    " nullable JSONB. No such storage-encoding claim. Raw [] is distin"
    'guishable and rejected for these literal cases."\n    },\n    "type'
    'd_readback": {\n      "measured_parameters": "frozenset({discharge'
    ',water_level})",\n      "forecast_targets_initial_or_HELD": "None"'
    ',\n      "forecast_targets_READY": "frozenset({discharge})",\n     '
    ' "lossiness": "_row_to_station maps raw falsy forecast_targets (N'
    "one or []) to None; typed output cannot independently distinguish"
    " them. store_station/update_station also map falsy domain targets"
    ' to pre-bind None; empty-set serialization not tested."\n    },\n  '
    '  "helper": "New typed nullable boundary checker: handle value is'
    " None FIRST, compare only against expected None and return; other"
    "wise require proper boundary container/list or frozenset and stri"
    "ng members, assert uniqueness before set comparison. Never call l"
    "en(None), never reuse component _station_sets unchanged, never no"
    'rmalize arbitrary JSON/arrays.",\n    "unchanged_other_fields": "A'
    "ll other station fields exact; all57 other tables exact raw row m"
    'ultisets; preserve exact timestamps/IDs/geometry."\n  },\n  "noncla'
    'ims": {\n    "role": "Owner disposable test only, no protected SEL'
    "ECT denial/no-effect SELECT detection, worker/operator login/gran"
    'ts or production owner authority",\n    "time": "UTC midnight-alig'
    "ned fast path only; not Kathmandu18:15Z bucket alignment or produ"
    'ction sample/overlap semantics",\n    "qc": "No failed/suspect/unc'
    'hecked inputs or QC/scientific policy validation",\n    "tenant_au'
    'dit": "Same-tenant positives only; audit inventory unchanged, no '
    'tenant-denial/audit-event coverage",\n    "canaries": "Protected s'
    "ame-date cohort at447 only; no newer-extreme/other-protected-stat"
    'ion/multiple-curve or cross-tenant matrix",\n    "station_mixup": '
    '"All six stations use identical numeric target/forcing series, so'
    " numeric assertions cannot discriminate a station mix-up that pre"
    "serves checked IDs. StationID/report/assembler-argument matching "
    'remains strict; no extra numeric station-discrimination claim."\n '
    ' },\n  "carrier_contract": {\n    "construction": "Fresh FakeStatio'
    "nForecastModel() instance once per public call; four distinct ret"
    'ained instance objects, not shared.",\n    "override": "Assign mod'
    "el.data_requirements = ModelDataRequirements(...) on that instanc"
    "e only, exactly the14 model_requirements fields; never assign Fak"
    "eStationForecastModel.data_requirements or mutate shared class de"
    'claration.",\n    "verification": "Retain class data_requirements '
    "object before each case; after call/finally assert identical clas"
    "s object and unchanged fields (POINT,720,5 and its other original"
    " fields). Assert per-call instance requirements equal literal14fi"
    "elds. Preserve references to four instances to verify distinct id"
    "entity without relying on recycled id() values. No extra qualific"
    "ation/model calls. Before any local override, compare the origina"
    "l class14fields against shared_class_requirements as well; do not"
    ' accept already-mutated ambient class state.",\n    "claim": "Loca'
    "lly overridden requirements carrier only; no validation of shared"
    ' fake declaration/model/FI behavior."\n  },\n  "shared_class_requir'
    'ements": {\n    "target_parameters": [\n      "discharge"\n    ],\n  '
    '  "past_dynamic_features": [\n      "precipitation",\n      "temper'
    'ature"\n    ],\n    "future_dynamic_features": [],\n    "static_feat'
    'ures": [],\n    "supported_time_steps_seconds": [\n      86400\n    '
    '],\n    "lookback_steps": 720,\n    "forecast_horizon_steps": 5,\n  '
    '  "spatial_input_type": "point",\n    "ensemble_mode": "single",\n '
    '   "declared_aggregations": [],\n    "declared_lookbacks": [],\n   '
    ' "declared_horizon_semantics": null,\n    "declared_min_future_ste'
    'ps": null,\n    "required_past_targets": null\n  }\n}\n'
)


def _text(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{label} must be a string")
    return value


def _text_field(row: Mapping[str, object], key: str) -> str:
    return _text(row[key], key)


def _nullable_text_field(row: Mapping[str, object], key: str) -> str | None:
    value = row[key]  # Missing key raises; None is only an explicit JSON null.
    return None if value is None else _text(value, key)


def _uuid_field(row: Mapping[str, object], key: str) -> UUID:
    return UUID(_text_field(row, key))


def _nullable_uuid_field(row: Mapping[str, object], key: str) -> UUID | None:
    value = _nullable_text_field(row, key)
    return None if value is None else UUID(value)


def _time_field(row: Mapping[str, object], key: str) -> UtcDatetime:
    return ensure_utc(datetime.fromisoformat(_text_field(row, key)))


def _nullable_time_field(row: Mapping[str, object], key: str) -> UtcDatetime | None:
    value = _nullable_text_field(row, key)
    return None if value is None else ensure_utc(datetime.fromisoformat(value))


def _integer_field(row: Mapping[str, object], key: str) -> int:
    value = row[key]
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{key} must be an integer, not bool")
    return value


def _float_field(row: Mapping[str, object], key: str) -> float:
    value = row[key]
    if not isinstance(value, float):
        raise TypeError(f"{key} must be a literal float")
    return value


class _DictionaryItems(Protocol):
    def items(self) -> Iterable[tuple[object, object]]: ...


def _is_dictionary(value: object) -> TypeGuard[_DictionaryItems]:
    return isinstance(value, dict)


def _is_list(value: object) -> TypeGuard[Sequence[object]]:
    return isinstance(value, list)


def _is_frozenset(value: object) -> TypeGuard[frozenset[object]]:
    return isinstance(value, frozenset)


def _record(value: object) -> dict[str, object]:
    if not _is_dictionary(value):
        raise TypeError("literal record must be an object")
    raw: _DictionaryItems = value
    result: dict[str, object] = {}
    for key, item in raw.items():
        result[_text(key, "record key")] = item
    return result


def _record_field(row: Mapping[str, object], key: str) -> dict[str, object]:
    return _record(row[key])


def _records_field(row: Mapping[str, object], key: str) -> list[dict[str, object]]:
    value = row[key]
    if not _is_list(value):
        raise TypeError(f"{key} must be an array")
    items: Sequence[object] = value
    return [_record(item) for item in items]


def _metres(row: Mapping[str, object], key: str) -> Literal["m"]:
    if _text_field(row, key) != "m":
        raise ValueError(f"{key} must be m")
    return "m"


def _reference(row: Mapping[str, object], key: str) -> Literal["gauge_zero", "masl"]:
    value = _text_field(row, key)
    if value == "gauge_zero":
        return "gauge_zero"
    if value == "masl":
        return "masl"
    raise ValueError(f"{key} must be gauge_zero or masl")


def _exact_keys(row: Mapping[str, object], keys: tuple[str, ...]) -> None:
    if set(row) != set(keys):
        raise ValueError("literal record has missing or unexpected fields")


def _point(row: Mapping[str, object]) -> dict[str, float]:
    _exact_keys(row, ("water_level", "discharge"))
    return {
        "water_level": _float_field(row, "water_level"),
        "discharge": _float_field(row, "discharge"),
    }


def _snapshot_content(row: Mapping[str, object], key: str) -> str:
    snapshot = _record_field(row, key)
    _exact_keys(snapshot, ("content",))
    return _text_field(snapshot, "content")


def _flag(row: Mapping[str, object]) -> QcFlag:
    _exact_keys(row, ("rule_id", "rule_version", "status", "detail"))
    return QcFlag(
        rule_id=_text_field(row, "rule_id"),
        rule_version=_text_field(row, "rule_version"),
        status=QcStatus(_text_field(row, "status")),
        detail=_nullable_text_field(row, "detail"),
    )


def _curve(row: Mapping[str, object]) -> RatingCurve:
    _exact_keys(
        row,
        (
            "id",
            "station_id",
            "version",
            "valid_from",
            "valid_to",
            "points",
            "interpolation",
            "uploaded_by",
            "created_at",
            "delivery_id",
            "rating_type_label",
        ),
    )
    points = [_point(point) for point in _records_field(row, "points")]
    return RatingCurve(
        id=RatingCurveId(_uuid_field(row, "id")),
        station_id=StationId(_uuid_field(row, "station_id")),
        version=_integer_field(row, "version"),
        valid_from=_time_field(row, "valid_from"),
        valid_to=_nullable_time_field(row, "valid_to"),
        points=points,
        interpolation=InterpolationMethod(_text_field(row, "interpolation")),
        uploaded_by=_nullable_uuid_field(row, "uploaded_by"),
        created_at=_time_field(row, "created_at"),
        delivery_id=_nullable_text_field(row, "delivery_id"),
        rating_type_label=_nullable_text_field(row, "rating_type_label"),
    )


def _proof(row: Mapping[str, object]) -> RatingReferenceProof:
    _exact_keys(
        row,
        (
            "id",
            "tenant_id",
            "station_id",
            "rating_curve_id",
            "curve",
            "endpoint",
            "api_station_id",
            "level_unit",
            "curve_unit",
            "level_reference",
            "curve_reference",
            "offset_m",
            "evidence_reference",
            "verified_by",
            "verified_at",
        ),
    )
    return RatingReferenceProof(
        id=RatingReferenceProofId(_uuid_field(row, "id")),
        tenant_id=TenantId(_uuid_field(row, "tenant_id")),
        station_id=StationId(_uuid_field(row, "station_id")),
        rating_curve_id=RatingCurveId(_uuid_field(row, "rating_curve_id")),
        curve=CurveSnapshot(content=_snapshot_content(row, "curve")),
        endpoint=_text_field(row, "endpoint"),
        api_station_id=_integer_field(row, "api_station_id"),
        level_unit=_metres(row, "level_unit"),
        curve_unit=_metres(row, "curve_unit"),
        level_reference=_reference(row, "level_reference"),
        curve_reference=_reference(row, "curve_reference"),
        offset_m=_float_field(row, "offset_m"),
        evidence_reference=_text_field(row, "evidence_reference"),
        verified_by=_text_field(row, "verified_by"),
        verified_at=_time_field(row, "verified_at"),
    )


def _feed(row: Mapping[str, object]) -> MeasurementFeedEvidence:
    _exact_keys(
        row,
        (
            "id",
            "tenant_id",
            "station_id",
            "observation_id",
            "measurement",
            "endpoint",
            "api_station_id",
            "evidence_reference",
            "verified_by",
            "verified_at",
        ),
    )
    return MeasurementFeedEvidence(
        id=MeasurementFeedEvidenceId(_uuid_field(row, "id")),
        tenant_id=TenantId(_uuid_field(row, "tenant_id")),
        station_id=StationId(_uuid_field(row, "station_id")),
        observation_id=ObservationId(_uuid_field(row, "observation_id")),
        measurement=MeasurementSnapshot(content=_snapshot_content(row, "measurement")),
        endpoint=_text_field(row, "endpoint"),
        api_station_id=_integer_field(row, "api_station_id"),
        evidence_reference=_text_field(row, "evidence_reference"),
        verified_by=_text_field(row, "verified_by"),
        verified_at=_time_field(row, "verified_at"),
    )


def _list(value: object) -> Sequence[object]:
    if not _is_list(value):
        raise TypeError("expected a literal array")
    return value


def _strings(value: object) -> list[str]:
    return [_text(item, "array item") for item in _list(value)]


def _json_value(value: object) -> object:
    if isinstance(value, Enum):
        return _json_value(value.value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if _is_dictionary(value):
        return {key: _json_value(item) for key, item in _record(value).items()}
    if _is_sequence(value):
        return [_json_value(item) for item in value]
    raise TypeError(f"unsupported evidence value: {type(value).__name__}")


def _is_sequence(value: object) -> TypeGuard[Sequence[object]]:
    return isinstance(value, (list, tuple))


def _oracle() -> dict[str, object]:
    assert hashlib.sha256(_ORACLE_TEXT.encode()).hexdigest() == (
        "9b5035b84e584813c42655c789f51552119245cbff78983b2b63e034154b5e4c"
    )
    return _record(json.loads(_ORACLE_TEXT))


def _observation(row: Mapping[str, object]) -> Observation:
    assert row["rating_curve_id"] is None
    assert row["rating_curve_correction_version"] is None
    result = Observation(
        id=ObservationId(_uuid_field(row, "id")),
        station_id=StationId(_uuid_field(row, "station_id")),
        timestamp=_time_field(row, "timestamp"),
        parameter=_text_field(row, "parameter"),
        value=_float_field(row, "value"),
        source=ObservationSource(_text_field(row, "source")),
        rating_curve_id=None,
        rating_curve_correction_version=None,
        qc_status=QcStatus(_text_field(row, "qc_status")),
        qc_flags=[_flag(flag) for flag in _records_field(row, "qc_flags")],
        qc_rule_version=_nullable_text_field(row, "qc_rule_version"),
        created_at=_time_field(row, "created_at"),
        delivery_id=_nullable_text_field(row, "delivery_id"),
    )
    assert _json_value(asdict(result)) == row
    return result


def _station(row: Mapping[str, object]) -> StationConfig:
    location = _record_field(row, "location")
    assert location["altitude_masl"] is None
    assert all(
        row[key] is None
        for key in (
            "regulation_type",
            "forecast_targets",
            "wigos_id",
            "water_level_datum_masl",
        )
    )
    return StationConfig(
        id=StationId(_uuid_field(row, "id")),
        tenant_id=TenantId(_uuid_field(row, "tenant_id")),
        code=_text_field(row, "code"),
        name=_text_field(row, "name"),
        location=GeoCoord(
            lon=_float_field(location, "lon"),
            lat=_float_field(location, "lat"),
            altitude_masl=None,
        ),
        station_kind=StationKind(_text_field(row, "station_kind")),
        basin_id=BasinId(_uuid_field(row, "basin_id")),
        timezone=_text_field(row, "timezone"),
        regulation_type=None,
        forecast_targets=None,
        measured_parameters=frozenset(_strings(row["measured_parameters"])),
        station_status=StationStatus(_text_field(row, "station_status")),
        created_at=_time_field(row, "created_at"),
        updated_at=_time_field(row, "updated_at"),
        network=_text_field(row, "network"),
        ownership=StationOwnership(_text_field(row, "ownership")),
        wigos_id=None,
        gauging_status=GaugingStatus(_text_field(row, "gauging_status")),
        water_level_datum_masl=None,
        water_level_unit=_text_field(row, "water_level_unit"),
    )


def _basin(row: Mapping[str, object]) -> Basin:
    assert all(
        row[key] is None for key in ("regional_basin", "band_geometries", "package_id")
    )
    return Basin(
        id=BasinId(_uuid_field(row, "id")),
        code=_text_field(row, "code"),
        name=_text_field(row, "name"),
        geometry=wkt.loads(_text_field(row, "geometry_wkt")),
        area_km2=_float_field(row, "area_km2"),
        attributes=_record_field(row, "attributes"),
        regional_basin=None,
        band_geometries=None,
        created_at=_time_field(row, "created_at"),
        network=_text_field(row, "network"),
        package_id=None,
    )


def _binding(row: Mapping[str, object]) -> GatewayPolygonBindingRow:
    assert row["band_id"] is None and row["package_id"] is None
    return GatewayPolygonBindingRow(
        station_id=StationId(_uuid_field(row, "station_id")),
        basin_id=BasinId(_uuid_field(row, "basin_id")),
        gateway_hru_name=_text_field(row, "gateway_hru_name"),
        name=_text_field(row, "name"),
        spatial_type=SpatialRepresentation(_text_field(row, "spatial_type")),
        band_id=None,
        package_id=None,
        imported_at=_time_field(row, "imported_at"),
    )


def _forcing(row: Mapping[str, object]) -> RawHistoricalForcing:
    assert row["band_id"] is None and row["member_id"] is None
    result = RawHistoricalForcing(
        station_id=StationId(_uuid_field(row, "station_id")),
        source=_text_field(row, "source"),
        version=_text_field(row, "version"),
        valid_time=_time_field(row, "valid_time"),
        parameter=_text_field(row, "parameter"),
        spatial_type=SpatialRepresentation(_text_field(row, "spatial_type")),
        band_id=None,
        member_id=None,
        value=_float_field(row, "value"),
    )
    assert _json_value(asdict(result)) == row
    return result


def _requirements_record(requirements: ModelDataRequirements) -> dict[str, object]:
    return {
        "target_parameters": sorted(requirements.target_parameters),
        "past_dynamic_features": sorted(requirements.past_dynamic_features),
        "future_dynamic_features": sorted(requirements.future_dynamic_features),
        "static_features": sorted(requirements.static_features),
        "supported_time_steps_seconds": sorted(
            step.total_seconds() for step in requirements.supported_time_steps
        ),
        "lookback_steps": requirements.lookback_steps,
        "forecast_horizon_steps": requirements.forecast_horizon_steps,
        "spatial_input_type": requirements.spatial_input_type.value,
        "ensemble_mode": requirements.ensemble_mode.value,
        "declared_aggregations": [
            [name, method.value]
            for name, method in sorted(requirements.declared_aggregations)
        ],
        "declared_lookbacks": [
            list(item) for item in sorted(requirements.declared_lookbacks)
        ],
        "declared_horizon_semantics": requirements.declared_horizon_semantics,
        "declared_min_future_steps": requirements.declared_min_future_steps,
        "required_past_targets": (
            None
            if requirements.required_past_targets is None
            else sorted(requirements.required_past_targets)
        ),
    }


def _new_model() -> FakeStationForecastModel:
    model = FakeStationForecastModel()
    model.data_requirements = ModelDataRequirements(
        target_parameters=frozenset({"discharge"}),
        past_dynamic_features=frozenset({"precipitation", "temperature"}),
        future_dynamic_features=frozenset(),
        static_features=frozenset(),
        supported_time_steps=frozenset({timedelta(days=1)}),
        lookback_steps=2,
        forecast_horizon_steps=1,
        spatial_input_type=SpatialRepresentation.BASIN_AVERAGE,
        ensemble_mode=EnsembleMode.SINGLE,
        declared_aggregations=frozenset(),
        declared_lookbacks=frozenset(),
        declared_horizon_semantics=None,
        declared_min_future_steps=None,
        required_past_targets=None,
    )
    assert (
        _requirements_record(model.data_requirements) == _oracle()["model_requirements"]
    )
    return model


def _forbidden_model_call(*args: object, **kwargs: object) -> Never:
    raise AssertionError("qualification must not execute model/artifact methods")


def _rows(conn: sa.Connection, name: str) -> list[dict[str, object]]:
    return [
        {str(key): value for key, value in row.items()}
        for row in conn.execute(sa.select(db.metadata.tables[name])).mappings()
    ]


def _inventory(conn: sa.Connection, names: Sequence[str]) -> dict[str, list[str]]:
    assert set(names) <= set(db.metadata.tables)
    geometry_columns = {
        "basins": ("geometry",),
        "basin_versions": ("geometry",),
        "stations": ("location",),
    }
    result: dict[str, list[str]] = {}
    for name in names:
        columns = geometry_columns.get(name, ())
        assert (
            tuple(
                column.name
                for column in db.metadata.tables[name].columns
                if isinstance(column.type, Geometry)
            )
            == columns
        )
        geometry_sql = "".join(
            f", encode(ST_AsEWKB(t.{column}), 'hex') AS {column}_ewkb_hex"
            for column in columns
        )
        # Metadata names above are fixed. Keep geometry associated with its row.
        query = sa.text(
            f"SELECT row_to_json(t)::text AS row_content{geometry_sql} "
            f"FROM public.{name} t"
        )
        result[name] = sorted(
            json.dumps(
                {
                    "row_content": _text(row["row_content"], "row content"),
                    "geometry_ewkb_hex": {
                        column: _text(row[f"{column}_ewkb_hex"], "EWKB hex")
                        for column in columns
                    },
                },
                sort_keys=True,
                allow_nan=False,
            )
            for row in conn.execute(query).mappings()
        )
    return result


def _same_rows(actual: Sequence[object], expected: Sequence[object]) -> None:
    assert len(actual) == len(expected)
    remaining = list(actual)
    for row in expected:
        assert row in remaining, row
        remaining.remove(row)
    assert not remaining


def _aware(value: object) -> None:
    assert isinstance(value, datetime) and value.utcoffset() is not None


def _owner_identity(conn: sa.Connection) -> dict[str, object]:
    result = {
        "session_user": conn.scalar(sa.text("SELECT session_user")),
        "current_user": conn.scalar(sa.text("SELECT current_user")),
        "transaction_isolation": conn.scalar(sa.text("SHOW transaction_isolation")),
    }
    assert result == {
        "session_user": "test",
        "current_user": "test",
        "transaction_isolation": "read committed",
    }
    owners = (
        conn.execute(
            sa.text(
                "SELECT tableowner FROM pg_tables WHERE schemaname='public' "
                "AND tablename IN ('stations', 'observations', "
                "'provisional_discharges', "
                "'rating_reference_proofs', 'measurement_feed_evidence')"
            )
        )
        .scalars()
        .all()
    )
    assert owners == ["test"] * 5
    return result


def _guards(conn: sa.Connection) -> list[str]:
    return sorted(
        _text(value, "trigger/function definition")
        for value in conn.execute(
            sa.text(
                "SELECT pg_get_triggerdef(t.oid) || E'\n' || "
                "pg_get_functiondef(t.tgfoid) FROM pg_trigger t "
                "JOIN pg_class c ON c.oid=t.tgrelid "
                "JOIN pg_namespace n ON n.oid=c.relnamespace "
                "WHERE n.nspname='public' AND NOT t.tgisinternal"
            )
        ).scalars()
    )


def _nullable_station_array(
    actual: object, expected: object, *, layer: Literal["raw", "compiled", "typed"]
) -> None:
    if expected is None:
        assert actual is None
        return
    wanted = _strings(expected)
    if layer == "typed":
        assert _is_frozenset(actual)
        members = [_text(item, "station set member") for item in actual]
    else:
        members = _strings(actual)
    assert len(members) == len(set(members)) == len(wanted)
    assert frozenset(members) == frozenset(wanted)


def _seed_basins_and_stations(
    conn: sa.Connection, oracle: Mapping[str, object]
) -> None:
    tenants = PgTenantStore(conn)
    literal_tenant = _record_field(oracle, "tenant")
    tid = TenantId(_uuid_field(literal_tenant, "id"))
    assert tenants.fetch_tenant(tid) is None
    assert tenants.fetch_tenant_by_code("chwrr") is None
    tenant = tenants.ensure_tenant(
        tenant_id=tid,
        code=_text_field(literal_tenant, "code"),
        name=_text_field(literal_tenant, "name"),
    )
    assert {"id": str(tenant.id), "code": tenant.code, "name": tenant.name} == (
        literal_tenant
    )
    _aware(tenant.created_at)
    basins = PgBasinStore(conn)
    stations = PgStationStore(conn)
    for literal in _records_field(oracle, "basins"):
        basin = _basin(literal)
        assert basins.fetch_basin(basin.id) is None
        assert basins.store_basin(basin) == basin.id
    for literal in _records_field(oracle, "stations"):
        station = _station(literal)
        assert stations.fetch_station(station.id) is None
        assert stations.fetch_station_by_code(station.code, station.network) is None
        stations.store_station(station)
        binding = _record_field(oracle, "weather_binding")
        stations.store_weather_source(
            StationWeatherSource(
                station_id=station.id,
                nwp_source=_text_field(binding, "nwp_source"),
                extraction_type=SpatialRepresentation(
                    _text_field(binding, "extraction_type")
                ),
                status=WeatherSourceStatus(_text_field(binding, "status")),
                role=WeatherSourceRole(_text_field(binding, "role")),
            )
        )
    for literal in _records_field(oracle, "gateway_bindings"):
        RecapGatewayPolygonStore(conn).store_binding(_binding(literal))
    permit_fixture(conn, tid)
    assert _json_value(_rows(conn, "provisional_discharge_permissions")) == [
        oracle["permission"]
    ]


def _seed_protected(conn: sa.Connection, oracle: Mapping[str, object]) -> None:
    curve = _curve(_record_field(oracle, "curve"))
    proof = _proof(_record_field(oracle, "reference_proof"))
    PgRatingCurveStore(conn).store_rating_curve(curve)
    seed_reference(conn, db.rating_reference_proofs, proof)
    levels = [_observation(row) for row in _records_field(oracle, "levels")]
    PgObservationStore(conn).store_observations(levels)
    feeds = [_feed(row) for row in _records_field(oracle, "feed_evidence")]
    now = _time_field(oracle, "clock")
    for level, feed, expected in zip(
        levels, feeds, _records_field(oracle, "protected_rows"), strict=True
    ):
        seed_reference(conn, db.measurement_feed_evidence, feed)
        result = convert_provisional_discharge(
            observation=level,
            curves=[curve],
            feed_evidence=feed,
            reference_proof=proof,
            at=now,
        )
        actual = {key: getattr(result, key) for key in expected if key != "captured_at"}
        actual["captured_at"] = now
        assert _json_value(actual) == expected
        assert (
            hashlib.sha256(result.content.encode()).hexdigest()
            == (expected["fingerprint"])
        )
        assert (
            PgProvisionalDischargeStore(conn).store_provisional_discharge(
                result, captured_at=now
            )
            == expected["fingerprint"]
        )


def _seed(
    conn: sa.Connection, oracle: Mapping[str, object], scenario: QualificationScenario
) -> None:
    for name in (
        "basins",
        "basin_versions",
        "stations",
        "station_weather_sources",
        "recap_gateway_polygon_bindings",
        "observations",
        "historical_forcing",
        "rating_curves",
        "rating_reference_proofs",
        "measurement_feed_evidence",
        "provisional_discharges",
        "provisional_discharge_permissions",
    ):
        assert not _rows(conn, name), ("unexpected preexisting seed relation", name)
    _seed_basins_and_stations(conn, oracle)
    PgObservationStore(conn).store_observations(
        [_observation(row) for row in _records_field(oracle, "ordinary")]
    )
    PgHistoricalForcingStore(conn).store_forcing(
        [_forcing(row) for row in _records_field(oracle, "forcing")]
    )
    if scenario is not QualificationScenario.CLEAN:
        _seed_protected(conn, oracle)


def _delete_controls(conn: sa.Connection, case: Mapping[str, object]) -> None:
    ids = [UUID(value) for value in _strings(case["delete_ordinary_ids"])]
    if not ids:
        return
    assert len(ids) == len(set(ids))
    parents = {
        row["observation_id"] for row in _rows(conn, "measurement_feed_evidence")
    } | {row["observation_id"] for row in _rows(conn, "provisional_discharges")}
    assert not set(ids) & parents
    before = _rows(conn, "observations")
    deleted = [row for row in before if row["id"] in ids]
    assert len(deleted) == len(ids)
    assert all(
        row["parameter"] == "discharge"
        and row["source"] == "manual_import"
        and row["rating_curve_id"] is None
        for row in deleted
    )
    conn.execute(sa.delete(db.observations).where(db.observations.c.id.in_(ids)))
    _same_rows(
        _rows(conn, "observations"), [row for row in before if row["id"] not in ids]
    )


def _check_basin_seed(conn: sa.Connection, oracle: Mapping[str, object]) -> None:
    store = PgBasinStore(conn)
    literal_basins = _records_field(oracle, "basins")
    versions = _rows(conn, "basin_versions")
    assert len(versions) == len(literal_basins) == 6
    version_ids: set[UUID] = set()
    for literal in literal_basins:
        expected = _basin(literal)
        actual = store.fetch_basin(expected.id)
        assert actual is not None
        _aware(actual.created_at)
        assert actual.geometry.equals(expected.geometry)
        assert (
            replace(actual, geometry=expected.geometry, created_at=expected.created_at)
            == expected
        )
        matches = [row for row in versions if row["basin_id"] == expected.id]
        assert len(matches) == 1
        version = matches[0]
        vid = version["id"]
        assert isinstance(vid, UUID) and vid.version == 4
        assert vid not in version_ids
        version_ids.add(vid)
        _aware(version["created_at"])
        assert {
            key: value
            for key, value in version.items()
            if key not in ("id", "created_at", "geometry")
        } == {
            "basin_id": expected.id,
            "package_id": None,
            "version": 1,
            "attributes": literal["attributes"],
            "area_km2": literal["area_km2"],
            "band_geometries": None,
            "gateway_mapping": None,
            "superseded_at": None,
        }
        geometry_text = conn.scalar(
            sa.select(sa.func.ST_AsText(db.basin_versions.c.geometry)).where(
                db.basin_versions.c.id == vid
            )
        )
        assert wkt.loads(_text(geometry_text, "version WKT")).equals(expected.geometry)


def _check_protected(
    conn: sa.Connection, oracle: Mapping[str, object], scenario: QualificationScenario
) -> list[str]:
    if scenario is QualificationScenario.CLEAN:
        assert all(
            not _rows(conn, name)
            for name in (
                "rating_curves",
                "rating_reference_proofs",
                "measurement_feed_evidence",
                "provisional_discharges",
            )
        )
        return []
    curve = _curve(_record_field(oracle, "curve"))
    assert PgRatingCurveStore(conn).fetch_all_curves_for_station(curve.station_id) == [
        curve
    ]
    _same_rows(
        [_json_value(row) for row in _rows(conn, "provisional_discharges")],
        _records_field(oracle, "protected_rows"),
    )
    proof = _record_field(oracle, "reference_proof")
    assert _json_value(_rows(conn, "rating_reference_proofs")) == [
        {
            **{
                key: proof[key]
                for key in ("id", "tenant_id", "station_id", "rating_curve_id")
            },
            "content": oracle["reference_content"],
            "fingerprint": oracle["reference_fingerprint"],
        }
    ]
    expected_feeds = [
        {
            **{
                key: feed[key]
                for key in ("id", "tenant_id", "station_id", "observation_id")
            },
            **content,
        }
        for feed, content in zip(
            _records_field(oracle, "feed_evidence"),
            _records_field(oracle, "feed_contents"),
            strict=True,
        )
    ]
    _same_rows(
        [_json_value(row) for row in _rows(conn, "measurement_feed_evidence")],
        expected_feeds,
    )
    documents = [
        *expected_feeds,
        {
            "content": oracle["reference_content"],
            "fingerprint": oracle["reference_fingerprint"],
        },
        *_records_field(oracle, "protected_rows"),
    ]
    for document in documents:
        content = _text_field(document, "content")
        assert hashlib.sha256(content.encode()).hexdigest() == document["fingerprint"]
    assert len(documents) == 21
    curve_content = _snapshot_content(proof, "curve")
    assert json.loads(curve_content) == oracle["curve"]
    return [
        hashlib.sha256(curve_content.encode()).hexdigest(),
        *[_text_field(row, "fingerprint") for row in documents],
    ]


def _check_seed(
    conn: sa.Connection,
    oracle: Mapping[str, object],
    case: Mapping[str, object],
    scenario: QualificationScenario,
) -> list[str]:
    _check_basin_seed(conn, oracle)
    station_store = PgStationStore(conn)
    for literal in _records_field(oracle, "stations"):
        station = _station(literal)
        assert station_store.fetch_station(station.id) == station
        raw_station = next(
            row for row in _rows(conn, "stations") if row["id"] == station.id
        )
        _nullable_station_array(
            raw_station["forecast_targets"], literal["forecast_targets"], layer="raw"
        )
        _nullable_station_array(
            raw_station["measured_parameters"],
            literal["measured_parameters"],
            layer="raw",
        )
        assert station_store.fetch_model_assignments(station.id) == []
        binding = _record_field(oracle, "weather_binding")
        assert _json_value(
            [asdict(row) for row in station_store.fetch_weather_sources(station.id)]
        ) == [{"station_id": str(station.id), **binding}]
    for literal in _records_field(oracle, "gateway_bindings"):
        binding = _binding(literal)
        assert RecapGatewayPolygonStore(conn).fetch_bindings_for_station(
            binding.station_id
        ) == [binding]
    for row in _rows(conn, "recap_gateway_polygon_bindings"):
        _aware(row["created_at"])
    excluded = _strings(case["delete_ordinary_ids"])
    ordinary = [
        row for row in _records_field(oracle, "ordinary") if row["id"] not in excluded
    ]
    expected_obs = ordinary + (
        []
        if scenario is QualificationScenario.CLEAN
        else _records_field(oracle, "levels")
    )
    rows = _rows(conn, "observations")
    assert len(rows) == case["observation_count"]
    _same_rows([_json_value(row) for row in rows], expected_obs)
    actual_delivery = PgObservationStore(conn).fetch_delivery_observations(
        "dhm-barkhk-2026-09-08",
        [_station(row).id for row in _records_field(oracle, "stations")],
    )
    _same_rows([_json_value(asdict(row)) for row in actual_delivery], ordinary)
    forcing = _rows(conn, "historical_forcing")
    assert len(forcing) == case["forcing_count"] == 120
    _same_rows(
        [
            _json_value(
                {
                    key: value
                    for key, value in row.items()
                    if key not in ("id", "created_at")
                }
            )
            for row in forcing
        ],
        _records_field(oracle, "forcing"),
    )
    ids: set[UUID] = set()
    for row in forcing:
        fid = row["id"]
        assert isinstance(fid, UUID) and fid.version == 4 and fid not in ids
        ids.add(fid)
        _aware(row["created_at"])
    assert len(_rows(conn, "provisional_discharges")) == case["protected_count"]
    assert not _rows(conn, "station_group_members")
    assert not _rows(conn, "calculated_station_formulas")
    return _check_protected(conn, oracle, scenario)


@dataclass(frozen=True, kw_only=True, slots=True)
class WriteAttempt:
    relation: str
    sql: str
    parameters: tuple[dict[str, object], ...]


class WriteObserver:
    def __init__(self, connection: sa.Connection) -> None:
        self.connection = connection
        self.attempts: list[WriteAttempt] = []
        self.unexpected: list[str] = []
        self.same_connection = True

    def before_cursor_execute(
        self,
        conn: sa.Connection,
        cursor: object,
        statement: str,
        parameters: object,
        context: ExecutionContext,
        executemany: bool,
    ) -> None:
        self.same_connection = self.same_connection and conn is self.connection
        compiled = context.compiled
        query = compiled.statement if compiled is not None else None
        if isinstance(query, Update):
            assert isinstance(query.table, sa.Table)
            assert isinstance(context, DefaultExecutionContext)
            self.attempts.append(
                WriteAttempt(
                    relation=query.table.name,
                    sql=statement,
                    parameters=tuple(
                        copy.deepcopy(_record(row))
                        for row in context.compiled_parameters
                    ),
                )
            )
        elif not re.match(r"\s*(SELECT\b|SAVEPOINT\b|RELEASE SAVEPOINT\b)", statement):
            self.unexpected.append(statement)


def _history_store(
    reader: nepal_onboarding.GatewayHistoryReader,
) -> PgHistoricalForcingStore:
    value: object = vars(reader)["_store"]
    assert isinstance(value, PgHistoricalForcingStore)
    return value


def _store_connection(
    store: PgObservationStore | PgHistoricalForcingStore | PgStationStore,
) -> sa.Connection:
    value: object = vars(store)["_conn"]
    assert isinstance(value, sa.Connection)
    return value


def _attempt_cleanup(
    action: Callable[[], None],
    errors: list[Exception],
) -> None:
    try:
        action()
    except Exception as error:
        errors.append(error)


def _retain_cleanup_errors(
    errors: Sequence[Exception],
    primary: BaseException | None,
) -> None:
    if not errors:
        return
    retained = primary if primary is not None else errors[0]
    secondary = errors if primary is not None else errors[1:]
    for error in secondary:
        retained.add_note(
            f"qualification cleanup/evidence failed: {type(error).__name__}: {error}"
        )
    if primary is None:
        raise retained


@contextmanager
def _owned_connection(engine: sa.Engine) -> Iterator[sa.Connection]:
    connection = engine.connect()
    try:
        yield connection
    finally:
        primary = sys.exception()
        errors: list[Exception] = []
        _attempt_cleanup(connection.close, errors)
        _retain_cleanup_errors(errors, primary)


def _frame(frame: pl.DataFrame, expected: Mapping[str, object]) -> list[object]:
    schema = {
        key: "Datetime(us,UTC)" if dtype == pl.Datetime("us", "UTC") else str(dtype)
        for key, dtype in frame.schema.items()
    }
    assert schema == expected
    assert frame.columns == list(expected)
    detached = frame.sort("timestamp") if "timestamp" in frame.columns else frame
    return [_json_value(row) for row in detached.to_dicts()]


def _training_record(
    data: StationTrainingData, oracle: Mapping[str, object]
) -> dict[str, object]:
    schemas = _record_field(oracle, "frame_schemas")
    assert data.static is not None
    return {
        "past_targets": _frame(
            data.past_targets, _record_field(schemas, "past_targets")
        ),
        "past_dynamic": _frame(
            data.past_dynamic, _record_field(schemas, "past_dynamic")
        ),
        "future_dynamic": _frame(
            data.future_dynamic, _record_field(schemas, "future_dynamic")
        ),
        "static": _frame(data.static, _record_field(schemas, "static")),
        "time_step_seconds": data.time_step.total_seconds(),
        "val_start": _json_value(data.val_start),
    }


def _check_writes(
    observer: WriteObserver, oracle: Mapping[str, object], case: Mapping[str, object]
) -> list[dict[str, object]]:
    assert observer.same_connection and not observer.unexpected
    assert len(observer.attempts) == case["allowed_station_update_attempts"] == 6
    targets = _list(case["station_targets"])
    for attempt, literal, target in zip(
        observer.attempts, _records_field(oracle, "stations"), targets, strict=True
    ):
        assert attempt.relation == "stations" and len(attempt.parameters) == 1
        values = attempt.parameters[0]
        for key, expected in (
            ("measured_parameters", literal["measured_parameters"]),
            ("forecast_targets", target),
        ):
            _nullable_station_array(values[key], expected, layer="compiled")
        location = _record_field(literal, "location")
        assert {
            key: value
            for key, value in values.items()
            if key not in ("measured_parameters", "forecast_targets")
        } == {
            "id_1": _uuid_field(literal, "id"),
            "name": literal["name"],
            "ST_MakePoint_1": location["lon"],
            "ST_MakePoint_2": location["lat"],
            "ST_SetSRID_1": 4326,
            "altitude_masl": location["altitude_masl"],
            "water_level_datum_masl": literal["water_level_datum_masl"],
            "water_level_unit": literal["water_level_unit"],
            "updated_at": _time_field(case, "station_updated_at"),
        }
    return [
        {
            "relation": row.relation,
            "operation": "update",
            "sql": row.sql,
            "compiled_prebind_parameters": _json_value(row.parameters),
        }
        for row in observer.attempts
    ]


def _check_station_changes(
    conn: sa.Connection,
    before: list[dict[str, object]],
    oracle: Mapping[str, object],
    case: Mapping[str, object],
) -> list[object]:
    after = _rows(conn, "stations")
    assert len(after) == len(before)
    actual_targets: list[object] = []
    ids = {_uuid_field(row, "id") for row in _records_field(oracle, "stations")}
    _same_rows(
        [row for row in after if row["id"] not in ids],
        [row for row in before if row["id"] not in ids],
    )
    for literal, target in zip(
        _records_field(oracle, "stations"), _list(case["station_targets"]), strict=True
    ):
        sid = _uuid_field(literal, "id")
        old = next(row for row in before if row["id"] == sid)
        current = next(row for row in after if row["id"] == sid)
        assert {
            key: value
            for key, value in current.items()
            if key not in ("forecast_targets", "measured_parameters", "updated_at")
        } == {
            key: value
            for key, value in old.items()
            if key not in ("forecast_targets", "measured_parameters", "updated_at")
        }
        _nullable_station_array(current["forecast_targets"], target, layer="raw")
        _nullable_station_array(
            current["measured_parameters"], literal["measured_parameters"], layer="raw"
        )
        assert current["updated_at"] == _time_field(case, "station_updated_at")
        actual = PgStationStore(conn).fetch_station(StationId(sid))
        assert actual is not None
        _nullable_station_array(actual.forecast_targets, target, layer="typed")
        _nullable_station_array(
            actual.measured_parameters, literal["measured_parameters"], layer="typed"
        )
        assert actual == replace(
            _station(literal),
            updated_at=_time_field(case, "station_updated_at"),
            forecast_targets=None if target is None else frozenset(_strings(target)),
        )
        actual_targets.append(copy.deepcopy(current["forecast_targets"]))
    return actual_targets


def environment_receipt() -> dict[str, object]:
    names = (
        "DATABASE_URL",
        "SAPPHIRE_DATA_DIR",
        "PREFECT_HOME",
        "PREFECT_API_URL",
        "PREFECT_SERVER_ALLOW_EPHEMERAL_MODE",
        "SKILL_PERSISTENCE_EVIDENCE_DIR",
        "NEPAL_READINESS_EVIDENCE_DIR",
    )
    return {
        key: None
        if os.environ.get(key) is None
        else hashlib.sha256(os.environ[key].encode()).hexdigest()
        for key in names
    }


def assert_shared_requirements() -> ModelDataRequirements:
    shared = FakeStationForecastModel.data_requirements
    assert _requirements_record(shared) == _oracle()["shared_class_requirements"]
    return shared


def _write_receipt(
    directory: Path, scenario: QualificationScenario, evidence: object
) -> None:
    configured = os.environ.get("NEPAL_READINESS_EVIDENCE_DIR")
    if configured == "":
        raise ValueError("NEPAL_READINESS_EVIDENCE_DIR must not be empty")
    destination = directory if configured is None else Path(configured)
    assert destination.is_dir() and not destination.is_symlink()
    content = json.dumps(_json_value(evidence), indent=2, allow_nan=False) + "\n"
    with (destination / f"{scenario.value}.json").open("x") as output:
        output.write(content)


def run_qualification_case(
    engine: sa.Engine,
    scenario: QualificationScenario,
    models: list[FakeStationForecastModel],
    *,
    evidence_dir: Path,
) -> dict[str, object]:
    oracle = _oracle()
    case = next(
        row
        for row in _records_field(oracle, "cases")
        if row["scenario"] == scenario.value
    )
    names = [*_strings(oracle["unchanged_tables"]), "stations"]
    assert len(names) == len(set(names)) == 58
    assert set(names) == set(db.metadata.tables)
    shared = assert_shared_requirements()
    original_assembler = nepal_onboarding.assemble_station_training_data
    model = _new_model()
    assert all(model is not previous for previous in models)
    models.append(model)
    window = TrainingWindow(
        start=_time_field(oracle, "start"),
        end=_time_field(oracle, "end"),
        time_step=timedelta(days=1),
        minimum_samples=2,
    )
    identity = DeploymentIdentityConfig(
        global_admin=False,
        writable_tenants=frozenset({"chwrr"}),
        operator="fixture-qualification-owner",
    )
    now = _time_field(oracle, "clock")
    frames: dict[str, object] = {}
    assembler_calls: list[dict[str, object]] = []
    evidence: dict[str, object] = {
        "scenario": scenario.value,
        "calls_started": 0,
        "calls_returned": 0,
        "assembler_calls": assembler_calls,
        "frames": frames,
        "binding_restored": False,
        "listener_removed": False,
        "shared_class_unchanged": False,
        "rollback_restored": False,
        "environment_before": environment_receipt(),
        "case_name": {
            QualificationScenario.CLEAN: (
                "test_clean_and_protected_histories_have_identical_qualification"
            ),
            QualificationScenario.MIXED: (
                "test_clean_and_protected_histories_have_identical_qualification"
            ),
            QualificationScenario.SHORT: (
                "test_short_ordinary_history_is_not_repaired_by_protected_history"
            ),
            QualificationScenario.PROTECTED_ONLY: (
                "test_protected_only_station_cannot_qualify"
            ),
        }[scenario],
    }
    baseline: dict[str, list[str]] | None = None
    original: BaseException | None = None
    try:
        with _owned_connection(engine) as conn:
            conn.execution_options(isolation_level="READ COMMITTED")
            assert conn.engine is engine
            transaction = conn.begin()
            try:
                revision = conn.scalar(
                    sa.text("SELECT version_num FROM alembic_version")
                )
                assert revision == "0071"
                database_now = conn.scalar(sa.select(sa.func.clock_timestamp()))
                assert isinstance(database_now, datetime) and now <= database_now
                roles = _owner_identity(conn)
                guards = _guards(conn)
                assert guards
                baseline = _inventory(conn, names)
                evidence["preseed_snapshot"] = baseline
                _seed(conn, oracle, scenario)
                _delete_controls(conn, case)
                evidence["protected_digests"] = _check_seed(
                    conn, oracle, case, scenario
                )
                before = _inventory(conn, _strings(oracle["unchanged_tables"]))
                station_before = _rows(conn, "stations")
                evidence["before_snapshot"] = before
                evidence["station_rows_before"] = _inventory(conn, ["stations"])
                evidence["guards_before"] = guards
                inputs: dict[str, object] = {
                    "training_window": {
                        "start": window.start.isoformat(),
                        "end": window.end.isoformat(),
                        "time_step_seconds": window.time_step.total_seconds(),
                        "minimum_samples": window.minimum_samples,
                    },
                    "now": now.isoformat(),
                    "application_identity": {
                        "global_admin": identity.global_admin,
                        "writable_tenants": sorted(identity.writable_tenants),
                        "operator": identity.operator,
                    },
                    "model_requirements": _requirements_record(model.data_requirements),
                    **roles,
                    "model_carrier": (
                        "Fresh per-call FakeStationForecastModel instance with "
                        "local14-field requirements override; shared class unchanged"
                    ),
                }
                assert inputs == case["public_call_inputs"]
                evidence["public_call_inputs"] = inputs

                def observe_assembler(
                    station_id: StationId,
                    model: StationForecastModel | GroupForecastModel,
                    period_start: UtcDatetime,
                    period_end: UtcDatetime,
                    time_step: timedelta,
                    forcing_source: WeatherReanalysisSource,
                    obs_store: ObservationStore,
                    basin_store: BasinStore,
                    station_store: StationStore,
                ) -> StationTrainingData | None:
                    assert model is models[-1]
                    assert (
                        _requirements_record(model.data_requirements)
                        == (oracle["model_requirements"])
                    )
                    assert (period_start, period_end, time_step) == (
                        window.start,
                        window.end,
                        window.time_step,
                    )
                    assert isinstance(
                        obs_store, nepal_onboarding.DeliveryObservationReader
                    )
                    assert isinstance(
                        forcing_source, nepal_onboarding.GatewayHistoryReader
                    )
                    assert isinstance(basin_store, PgBasinStore)
                    assert isinstance(station_store, PgStationStore)
                    assert type(obs_store) is nepal_onboarding.DeliveryObservationReader
                    assert type(forcing_source) is nepal_onboarding.GatewayHistoryReader
                    assert type(basin_store) is PgBasinStore
                    assert type(station_store) is PgStationStore
                    forcing_store = _history_store(forcing_source)
                    assert type(forcing_store) is PgHistoricalForcingStore
                    assert basin_store.connection is conn
                    assert all(
                        _store_connection(store) is conn
                        for store in (obs_store, forcing_store, station_store)
                    )
                    call: dict[str, object] = {
                        "station_id": str(station_id),
                        "start": period_start.isoformat(),
                        "end": period_end.isoformat(),
                        "time_step_seconds": time_step.total_seconds(),
                        "model_same_instance": True,
                        "requirements": _requirements_record(model.data_requirements),
                        "dependencies": [
                            type(store).__name__
                            for store in (
                                obs_store,
                                forcing_source,
                                forcing_store,
                                basin_store,
                                station_store,
                            )
                        ],
                        "same_connection": True,
                        "returned": False,
                    }
                    assembler_calls.append(call)
                    result = original_assembler(
                        station_id,
                        model,
                        period_start,
                        period_end,
                        time_step,
                        forcing_source,
                        obs_store,
                        basin_store,
                        station_store,
                    )
                    call["returned"] = True
                    assert result is not None
                    sid = str(station_id)
                    assert sid not in frames
                    frames[sid] = _training_record(result, oracle)
                    assert frames[sid] == _record_field(case, "assembler_results")[sid]
                    return result

                observer = WriteObserver(conn)
                listener = observer.before_cursor_execute
                event.listen(conn, "before_cursor_execute", listener)
                try:
                    with pytest.MonkeyPatch.context() as patch:
                        patch.setattr(
                            nepal_onboarding,
                            "assemble_station_training_data",
                            observe_assembler,
                        )
                        for name in (
                            "train",
                            "predict",
                            "serialize_artifact",
                            "deserialize_artifact",
                        ):
                            patch.setattr(model, name, _forbidden_model_call)
                        evidence["calls_started"] = 1
                        reports = nepal_onboarding.qualify_discharge_targets(
                            conn, identity, model, window, now=now
                        )
                        evidence["calls_returned"] = 1
                finally:
                    pending = sys.exception()
                    observer_errors: list[Exception] = []

                    def capture_attempts() -> None:
                        evidence["observed_write_attempts"] = [
                            {
                                "relation": attempt.relation,
                                "sql": attempt.sql,
                                "compiled_prebind_parameters": _json_value(
                                    attempt.parameters
                                ),
                            }
                            for attempt in observer.attempts
                        ]
                        evidence["unexpected_sql"] = observer.unexpected

                    def remove_listener() -> None:
                        event.remove(conn, "before_cursor_execute", listener)
                        evidence["listener_removed"] = not event.contains(
                            conn, "before_cursor_execute", listener
                        )
                        assert evidence["listener_removed"]

                    def check_binding_restored() -> None:
                        evidence["binding_restored"] = (
                            nepal_onboarding.assemble_station_training_data
                            is original_assembler
                        )
                        assert evidence["binding_restored"]

                    _attempt_cleanup(capture_attempts, observer_errors)
                    _attempt_cleanup(remove_listener, observer_errors)
                    _attempt_cleanup(check_binding_restored, observer_errors)
                    evidence["observer_cleanup_errors"] = [
                        f"{type(error).__name__}: {error}" for error in observer_errors
                    ]
                    _retain_cleanup_errors(observer_errors, pending)
                assert _owner_identity(conn) == roles
                actual_reports = [_json_value(asdict(report)) for report in reports]
                assert actual_reports == case["reports"]
                evidence["reports"] = actual_reports
                assert len(assembler_calls) == case["assembler_calls"]
                assert [call["station_id"] for call in assembler_calls] == list(
                    _record_field(case, "assembler_results")
                )
                assert frames == case["assembler_results"]
                evidence["writes"] = _check_writes(observer, oracle, case)
                actual_targets = _check_station_changes(
                    conn, station_before, oracle, case
                )
                evidence["station_targets"] = actual_targets
                after = _inventory(conn, _strings(oracle["unchanged_tables"]))
                evidence["after_snapshot"] = after
                assert after == before
                evidence["57_other_tables_unchanged"] = True
                evidence["station_rows_after"] = _inventory(conn, ["stations"])
                evidence["guards_after"] = _guards(conn)
                assert evidence["guards_after"] == guards
                evidence["guard_definitions_unchanged"] = True
                assert (
                    _check_protected(conn, oracle, scenario)
                    == (evidence["protected_digests"])
                )
                return {
                    "reports": actual_reports,
                    "frames": frames,
                    "station_targets": actual_targets,
                }
            finally:
                pending = sys.exception()
                transaction_errors: list[Exception] = []
                _attempt_cleanup(transaction.rollback, transaction_errors)
                evidence["transaction_cleanup_errors"] = [
                    f"{type(error).__name__}: {error}" for error in transaction_errors
                ]
                _retain_cleanup_errors(transaction_errors, pending)
    except BaseException as exc:
        original = exc
        raise
    finally:
        cleanup_errors: list[Exception] = []

        def check_shared_class() -> None:
            assert FakeStationForecastModel.data_requirements is shared
            assert_shared_requirements()
            evidence["shared_class_unchanged"] = True

        def check_assembler_binding() -> None:
            assert nepal_onboarding.assemble_station_training_data is original_assembler

        def check_rollback_snapshot() -> None:
            if baseline is not None:
                with _owned_connection(engine) as verify:
                    assert _inventory(verify, names) == baseline
                evidence["rollback_restored"] = True

        def check_case_environment() -> None:
            evidence["environment_after"] = environment_receipt()
            assert evidence["environment_after"] == evidence["environment_before"]

        _attempt_cleanup(check_shared_class, cleanup_errors)
        _attempt_cleanup(check_assembler_binding, cleanup_errors)
        _attempt_cleanup(check_rollback_snapshot, cleanup_errors)
        _attempt_cleanup(check_case_environment, cleanup_errors)
        evidence["final_cleanup_errors"] = [
            f"{type(error).__name__}: {error}" for error in cleanup_errors
        ]
        _attempt_cleanup(
            lambda: _write_receipt(evidence_dir, scenario, evidence), cleanup_errors
        )
        _retain_cleanup_errors(cleanup_errors, original)
