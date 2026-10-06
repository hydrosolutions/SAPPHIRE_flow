from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol, TypeGuard, cast
from uuid import UUID

import pytest
import sqlalchemy as sa
from sqlalchemy import event
from sqlalchemy.exc import DBAPIError

from sapphire_flow.config.onboarding import CalculatedStationSpec, ComponentSpec
from sapphire_flow.db import metadata as db
from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.services.calculated_station_onboarding import (
    onboard_calculated_station,
)
from sapphire_flow.services.provisional_discharge import convert_provisional_discharge
from sapphire_flow.store.provisional_discharge_store import PgProvisionalDischargeStore
from sapphire_flow.store.rating_curve_store import PgRatingCurveStore
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.domain import GeoCoord, QcFlag
from sapphire_flow.types.enums import (
    GaugingStatus,
    InterpolationMethod,
    ObservationSource,
    QcStatus,
    StationKind,
    StationOwnership,
    StationStatus,
)
from sapphire_flow.types.ids import (
    MeasurementFeedEvidenceId,
    ObservationId,
    RatingCurveId,
    RatingReferenceProofId,
    StationId,
    TenantId,
)
from sapphire_flow.types.observation import Observation
from sapphire_flow.types.rating_curve import RatingCurve
from sapphire_flow.types.rating_reference import (
    CurveSnapshot,
    MeasurementFeedEvidence,
    MeasurementSnapshot,
    RatingReferenceProof,
)
from sapphire_flow.types.station import StationConfig
from tests.integration.store.test_provisional_discharge_store import (
    permit_fixture,
    seed_reference,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping, Sequence

    from sapphire_flow.services.calculated_station_onboarding import (
        CalculatedOnboardingOutcome,
    )
    from sapphire_flow.store.calculated_station_formula_store import PgFormulaStore
    from sapphire_flow.store.observation_store import PgObservationStore
    from sapphire_flow.store.station_store import PgStationStore
    from sapphire_flow.types.datetime import UtcDatetime


class ComponentStores(Protocol):
    @property
    def obs(self) -> PgObservationStore: ...

    @property
    def station(self) -> PgStationStore: ...

    @property
    def formula(self) -> PgFormulaStore: ...


class ComponentScenario(Enum):
    CLEAN = "clean"
    MIXED = "mixed"
    CHANGED = "changed"
    PROTECTED_ONLY = "protected_only"


# Frozen independently reviewed oracle; no production arithmetic/serializer builds it.
_ORACLE_TEXT = (
    '{\n  "base": "0e9ba0778c7cb43364095f26cc503f6c0567c1e5",\n  "status": "DRAF'
    'T literal independent oracle; stdlib only, no project execution",\n  "clock":'
    ' "2026-01-10T04:00:00+00:00",\n  "window_start": "2026-01-09T23:00:00+00:00"'
    ',\n  "window_end": "2026-01-10T03:00:00+00:00",\n  "tenant_id": "00000000-0'
    '000-0000-0000-000000000001",\n  "stations": [\n    {\n      "location": {\n  '
    '      "lon": 8.0,\n        "lat": 47.0,\n        "altitude_masl": null\n    '
    '  },\n      "station_kind": "river",\n      "basin_id": null,\n      "timez'
    'one": "UTC",\n      "regulation_type": null,\n      "forecast_targets": [\n'
    '        "discharge"\n      ],\n      "measured_parameters": [\n        "disch'
    'arge",\n        "water_level"\n      ],\n      "station_status": "operationa'
    'l",\n      "created_at": "2026-01-10T04:00:00+00:00",\n      "updated_at": '
    '"2026-01-10T04:00:00+00:00",\n      "network": "component-isolation",\n     '
    ' "ownership": "own",\n      "wigos_id": null,\n      "gauging_status": "g'
    'auged",\n      "water_level_datum_masl": null,\n      "water_level_unit": "m'
    '",\n      "tenant_id": "00000000-0000-0000-0000-000000000001",\n      "id":'
    ' "00000000-0000-0000-0000-000000013c68",\n      "code": "component-a",\n    '
    '  "name": "Component A"\n    },\n    {\n      "location": {\n        "lon"'
    ': 8.0,\n        "lat": 47.0,\n        "altitude_masl": null\n      },\n      '
    '"station_kind": "river",\n      "basin_id": null,\n      "timezone": "UTC'
    '",\n      "regulation_type": null,\n      "forecast_targets": [\n        "di'
    'scharge"\n      ],\n      "measured_parameters": [\n        "discharge",\n   '
    '     "water_level"\n      ],\n      "station_status": "operational",\n      '
    '"created_at": "2026-01-10T04:00:00+00:00",\n      "updated_at": "2026-01-10'
    'T04:00:00+00:00",\n      "network": "component-isolation",\n      "ownership'
    '": "own",\n      "wigos_id": null,\n      "gauging_status": "gauged",\n  '
    '    "water_level_datum_masl": null,\n      "water_level_unit": "m",\n      '
    '"tenant_id": "00000000-0000-0000-0000-000000000001",\n      "id": "00000000'
    '-0000-0000-0000-000000014050",\n      "code": "component-b",\n      "name":'
    ' "Component B"\n    }\n  ],\n  "curves": [\n    {\n      "id": "00000000-00'
    '00-0000-0000-000000014439",\n      "station_id": "00000000-0000-0000-0000-000'
    '000013c68",\n      "version": 1,\n      "valid_from": "2025-01-10T00:00:00+0'
    '0:00",\n      "valid_to": "2026-01-10T01:30:00+00:00",\n      "points": [\n'
    '        {\n          "water_level": 1.0,\n          "discharge": 4.0\n        '
    '},\n        {\n          "water_level": 2.0,\n          "discharge": 10000.0\n'
    '        }\n      ],\n      "interpolation": "linear",\n      "uploaded_by": '
    'null,\n      "created_at": "2026-01-09T00:00:00+00:00",\n      "delivery_id"'
    ': null,\n      "rating_type_label": null\n    },\n    {\n      "id": "0000000'
    '0-0000-0000-0000-00000001443a",\n      "station_id": "00000000-0000-0000-0000'
    '-000000014050",\n      "version": 1,\n      "valid_from": "2025-01-10T00:00:'
    '00+00:00",\n      "valid_to": "2026-01-10T01:30:00+00:00",\n      "points":'
    ' [\n        {\n          "water_level": 1.0,\n          "discharge": 4.0\n    '
    '    },\n        {\n          "water_level": 2.0,\n          "discharge": 10000'
    '.0\n        }\n      ],\n      "interpolation": "linear",\n      "uploaded_by'
    '": null,\n      "created_at": "2026-01-09T00:00:00+00:00",\n      "delivery_'
    'id": null,\n      "rating_type_label": null\n    }\n  ],\n  "ordinary_observat'
    'ions": [\n    {\n      "id": "00000000-0000-0000-0000-000000014821",\n      '
    '"station_id": "00000000-0000-0000-0000-000000013c68",\n      "timestamp": "'
    '2026-01-10T00:00:00+00:00",\n      "parameter": "discharge",\n      "value"'
    ': 4.0,\n      "source": "measured",\n      "rating_curve_id": null,\n      '
    '"rating_curve_correction_version": null,\n      "qc_status": "qc_passed",\n '
    '     "qc_flags": [\n        {\n          "rule_id": "range_check",\n        '
    '  "rule_version": "fixture-v1",\n          "status": "qc_passed",\n       '
    '   "detail": null\n        }\n      ],\n      "qc_rule_version": "fixture-v1'
    '",\n      "created_at": "2026-01-10T04:00:00+00:00",\n      "delivery_id": '
    'null\n    },\n    {\n      "id": "00000000-0000-0000-0000-000000014822",\n    '
    '  "station_id": "00000000-0000-0000-0000-000000013c68",\n      "timestamp": '
    '"2026-01-10T00:00:00+00:00",\n      "parameter": "discharge",\n      "value'
    '": 40.0,\n      "source": "rating_curve_derived",\n      "rating_curve_id":'
    ' "00000000-0000-0000-0000-000000014439",\n      "rating_curve_correction_vers'
    'ion": "fixture-v1",\n      "qc_status": "qc_passed",\n      "qc_flags": ['
    '\n        {\n          "rule_id": "range_check",\n          "rule_version": '
    '"fixture-v1",\n          "status": "qc_passed",\n          "detail": null'
    '\n        }\n      ],\n      "qc_rule_version": "fixture-v1",\n      "created'
    '_at": "2026-01-10T04:00:00+00:00",\n      "delivery_id": null\n    },\n    {'
    '\n      "id": "00000000-0000-0000-0000-000000014823",\n      "station_id": '
    '"00000000-0000-0000-0000-000000013c68",\n      "timestamp": "2026-01-10T01:00'
    ':00+00:00",\n      "parameter": "discharge",\n      "value": 6.0,\n      "'
    'source": "rating_curve_derived",\n      "rating_curve_id": "00000000-0000-00'
    '00-0000-000000014439",\n      "rating_curve_correction_version": "fixture-v1"'
    ',\n      "qc_status": "qc_passed",\n      "qc_flags": [\n        {\n        '
    '  "rule_id": "range_check",\n          "rule_version": "fixture-v1",\n    '
    '      "status": "qc_passed",\n          "detail": null\n        }\n      ],'
    '\n      "qc_rule_version": "fixture-v1",\n      "created_at": "2026-01-10T0'
    '4:00:00+00:00",\n      "delivery_id": null\n    },\n    {\n      "id": "0000'
    '0000-0000-0000-0000-000000014824",\n      "station_id": "00000000-0000-0000-0'
    '000-000000013c68",\n      "timestamp": "2026-01-10T02:00:00+00:00",\n      "'
    'parameter": "discharge",\n      "value": 8.0,\n      "source": "measured"'
    ',\n      "rating_curve_id": null,\n      "rating_curve_correction_version": nu'
    'll,\n      "qc_status": "qc_passed",\n      "qc_flags": [\n        {\n      '
    '    "rule_id": "range_check",\n          "rule_version": "fixture-v1",\n  '
    '        "status": "qc_passed",\n          "detail": null\n        }\n      ]'
    ',\n      "qc_rule_version": "fixture-v1",\n      "created_at": "2026-01-10T'
    '04:00:00+00:00",\n      "delivery_id": null\n    },\n    {\n      "id": "000'
    '00000-0000-0000-0000-000000014825",\n      "station_id": "00000000-0000-0000-'
    '0000-000000014050",\n      "timestamp": "2026-01-10T00:00:00+00:00",\n      '
    '"parameter": "discharge",\n      "value": 8.0,\n      "source": "measured'
    '",\n      "rating_curve_id": null,\n      "rating_curve_correction_version": '
    'null,\n      "qc_status": "qc_passed",\n      "qc_flags": [\n        {\n    '
    '      "rule_id": "range_check",\n          "rule_version": "fixture-v1",\n'
    '          "status": "qc_passed",\n          "detail": null\n        }\n     '
    ' ],\n      "qc_rule_version": "fixture-v1",\n      "created_at": "2026-01-1'
    '0T04:00:00+00:00",\n      "delivery_id": null\n    },\n    {\n      "id": "0'
    '0000000-0000-0000-0000-000000014826",\n      "station_id": "00000000-0000-000'
    '0-0000-000000014050",\n      "timestamp": "2026-01-10T01:00:00+00:00",\n     '
    ' "parameter": "discharge",\n      "value": 12.0,\n      "source": "measur'
    'ed",\n      "rating_curve_id": null,\n      "rating_curve_correction_version"'
    ': null,\n      "qc_status": "qc_suspect",\n      "qc_flags": [\n        {\n '
    '         "rule_id": "range_check",\n          "rule_version": "fixture-v1"'
    ',\n          "status": "qc_suspect",\n          "detail": null\n        }\n '
    '     ],\n      "qc_rule_version": "fixture-v1",\n      "created_at": "2026-'
    '01-10T04:00:00+00:00",\n      "delivery_id": null\n    }\n  ],\n  "protected_l'
    'evels": [\n    {\n      "id": "00000000-0000-0000-0000-000000014c09",\n      '
    '"station_id": "00000000-0000-0000-0000-000000013c68",\n      "timestamp": "'
    '2026-01-09T23:30:00+00:00",\n      "parameter": "water_level",\n      "value'
    '": 2.0,\n      "source": "measured",\n      "rating_curve_id": null,\n     '
    ' "rating_curve_correction_version": null,\n      "qc_status": "qc_passed",\n'
    '      "qc_flags": [\n        {\n          "rule_id": "range_check",\n       '
    '   "rule_version": "fixture-v1",\n          "status": "qc_passed",\n      '
    '    "detail": null\n        }\n      ],\n      "qc_rule_version": "fixture-v1'
    '",\n      "created_at": "2026-01-10T04:00:00+00:00",\n      "delivery_id": '
    'null\n    },\n    {\n      "id": "00000000-0000-0000-0000-000000014c0a",\n    '
    '  "station_id": "00000000-0000-0000-0000-000000013c68",\n      "timestamp": '
    '"2026-01-10T00:30:00+00:00",\n      "parameter": "water_level",\n      "val'
    'ue": 2.0,\n      "source": "measured",\n      "rating_curve_id": null,\n   '
    '   "rating_curve_correction_version": null,\n      "qc_status": "qc_passed",'
    '\n      "qc_flags": [\n        {\n          "rule_id": "range_check",\n     '
    '     "rule_version": "fixture-v1",\n          "status": "qc_passed",\n    '
    '      "detail": null\n        }\n      ],\n      "qc_rule_version": "fixture-'
    'v1",\n      "created_at": "2026-01-10T04:00:00+00:00",\n      "delivery_id"'
    ': null\n    },\n    {\n      "id": "00000000-0000-0000-0000-000000014c0b",\n  '
    '    "station_id": "00000000-0000-0000-0000-000000013c68",\n      "timestamp"'
    ': "2026-01-10T02:00:00+00:00",\n      "parameter": "water_level",\n      "v'
    'alue": 2.0,\n      "source": "measured",\n      "rating_curve_id": null,\n '
    '     "rating_curve_correction_version": null,\n      "qc_status": "qc_passed'
    '",\n      "qc_flags": [\n        {\n          "rule_id": "range_check",\n  '
    '        "rule_version": "fixture-v1",\n          "status": "qc_passed",\n '
    '         "detail": null\n        }\n      ],\n      "qc_rule_version": "fixtu'
    're-v1",\n      "created_at": "2026-01-10T04:00:00+00:00",\n      "delivery_i'
    'd": null\n    },\n    {\n      "id": "00000000-0000-0000-0000-000000014c0c",'
    '\n      "station_id": "00000000-0000-0000-0000-000000014050",\n      "timesta'
    'mp": "2026-01-09T23:30:00+00:00",\n      "parameter": "water_level",\n     '
    ' "value": 2.0,\n      "source": "measured",\n      "rating_curve_id": null'
    ',\n      "rating_curve_correction_version": null,\n      "qc_status": "qc_pas'
    'sed",\n      "qc_flags": [\n        {\n          "rule_id": "range_check",'
    '\n          "rule_version": "fixture-v1",\n          "status": "qc_passed"'
    ',\n          "detail": null\n        }\n      ],\n      "qc_rule_version": "f'
    'ixture-v1",\n      "created_at": "2026-01-10T04:00:00+00:00",\n      "delive'
    'ry_id": null\n    },\n    {\n      "id": "00000000-0000-0000-0000-000000014c0d'
    '",\n      "station_id": "00000000-0000-0000-0000-000000014050",\n      "time'
    'stamp": "2026-01-10T00:30:00+00:00",\n      "parameter": "water_level",\n  '
    '    "value": 2.0,\n      "source": "measured",\n      "rating_curve_id": n'
    'ull,\n      "rating_curve_correction_version": null,\n      "qc_status": "qc_'
    'passed",\n      "qc_flags": [\n        {\n          "rule_id": "range_check'
    '",\n          "rule_version": "fixture-v1",\n          "status": "qc_passe'
    'd",\n          "detail": null\n        }\n      ],\n      "qc_rule_version": '
    '"fixture-v1",\n      "created_at": "2026-01-10T04:00:00+00:00",\n      "del'
    'ivery_id": null\n    },\n    {\n      "id": "00000000-0000-0000-0000-000000014'
    'c0e",\n      "station_id": "00000000-0000-0000-0000-000000014050",\n      "t'
    'imestamp": "2026-01-10T02:00:00+00:00",\n      "parameter": "water_level",'
    '\n      "value": 2.0,\n      "source": "measured",\n      "rating_curve_id'
    '": null,\n      "rating_curve_correction_version": null,\n      "qc_status": '
    '"qc_passed",\n      "qc_flags": [\n        {\n          "rule_id": "range_c'
    'heck",\n          "rule_version": "fixture-v1",\n          "status": "qc_p'
    'assed",\n          "detail": null\n        }\n      ],\n      "qc_rule_version'
    '": "fixture-v1",\n      "created_at": "2026-01-10T04:00:00+00:00",\n      '
    '"delivery_id": null\n    }\n  ],\n  "reference_proofs": [\n    {\n      "tena'
    'nt_id": "00000000-0000-0000-0000-000000000001",\n      "station_id": "000000'
    '00-0000-0000-0000-000000013c68",\n      "endpoint": "https://example.invalid/'
    'levels/",\n      "api_station_id": 81000,\n      "evidence_reference": "comp'
    'onent-isolation-v1",\n      "verified_by": "fixture-reviewer",\n      "verif'
    'ied_at": "2026-01-10T04:00:00+00:00",\n      "id": "00000000-0000-0000-0000-'
    '000000014ff1",\n      "rating_curve_id": "00000000-0000-0000-0000-00000001443'
    '9",\n      "curve": {\n        "content": "{\\"created_at\\":\\"2026-01-0'
    '9T00:00:00+00:00\\",\\"delivery_id\\":null,\\"id\\":\\"00000000-0000-0000-00'
    '00-000000014439\\",\\"interpolation\\":\\"linear\\",\\"points\\":[{\\"disc'
    'harge\\":4.0,\\"water_level\\":1.0},{\\"discharge\\":10000.0,\\"water_level'
    '\\":2.0}],\\"rating_type_label\\":null,\\"station_id\\":\\"00000000-0000-000'
    '0-0000-000000013c68\\",\\"uploaded_by\\":null,\\"valid_from\\":\\"2025-01-10'
    'T00:00:00+00:00\\",\\"valid_to\\":\\"2026-01-10T01:30:00+00:00\\",\\"version'
    '\\":1}"\n      },\n      "level_unit": "m",\n      "curve_unit": "m",\n '
    '     "level_reference": "gauge_zero",\n      "curve_reference": "gauge_zero'
    '",\n      "offset_m": 0.0\n    },\n    {\n      "tenant_id": "00000000-0000-'
    '0000-0000-000000000001",\n      "station_id": "00000000-0000-0000-0000-000000'
    '014050",\n      "endpoint": "https://example.invalid/levels/",\n      "api_s'
    'tation_id": 82000,\n      "evidence_reference": "component-isolation-v1",\n  '
    '    "verified_by": "fixture-reviewer",\n      "verified_at": "2026-01-10T04'
    ':00:00+00:00",\n      "id": "00000000-0000-0000-0000-000000014ff2",\n      "'
    'rating_curve_id": "00000000-0000-0000-0000-00000001443a",\n      "curve": {\n'
    '        "content": "{\\"created_at\\":\\"2026-01-09T00:00:00+00:00\\",\\"d'
    'elivery_id\\":null,\\"id\\":\\"00000000-0000-0000-0000-00000001443a\\",\\"in'
    'terpolation\\":\\"linear\\",\\"points\\":[{\\"discharge\\":4.0,\\"water_le'
    'vel\\":1.0},{\\"discharge\\":10000.0,\\"water_level\\":2.0}],\\"rating_type_'
    'label\\":null,\\"station_id\\":\\"00000000-0000-0000-0000-000000014050\\",\\'
    '"uploaded_by\\":null,\\"valid_from\\":\\"2025-01-10T00:00:00+00:00\\",\\"va'
    'lid_to\\":\\"2026-01-10T01:30:00+00:00\\",\\"version\\":1}"\n      },\n     '
    ' "level_unit": "m",\n      "curve_unit": "m",\n      "level_reference": '
    '"gauge_zero",\n      "curve_reference": "gauge_zero",\n      "offset_m": 0'
    '.0\n    }\n  ],\n  "feed_evidence": [\n    {\n      "tenant_id": "00000000-00'
    '00-0000-0000-000000000001",\n      "station_id": "00000000-0000-0000-0000-000'
    '000013c68",\n      "endpoint": "https://example.invalid/levels/",\n      "ap'
    'i_station_id": 81000,\n      "evidence_reference": "component-isolation-v1",'
    '\n      "verified_by": "fixture-reviewer",\n      "verified_at": "2026-01-1'
    '0T04:00:00+00:00",\n      "id": "00000000-0000-0000-0000-0000000153d9",\n    '
    '  "observation_id": "00000000-0000-0000-0000-000000014c09",\n      "measureme'
    'nt": {\n        "content": "{\\"created_at\\":\\"2026-01-10T04:00:00+00:00'
    '\\",\\"delivery_id\\":null,\\"id\\":\\"00000000-0000-0000-0000-000000014c09'
    '\\",\\"parameter\\":\\"water_level\\",\\"rating_curve_correction_version\\"'
    ':null,\\"rating_curve_id\\":null,\\"source\\":\\"measured\\",\\"station_id'
    '\\":\\"00000000-0000-0000-0000-000000013c68\\",\\"timestamp\\":\\"2026-01-09'
    'T23:30:00+00:00\\",\\"value\\":2.0}"\n      }\n    },\n    {\n      "tenant_i'
    'd": "00000000-0000-0000-0000-000000000001",\n      "station_id": "00000000-0'
    '000-0000-0000-000000013c68",\n      "endpoint": "https://example.invalid/leve'
    'ls/",\n      "api_station_id": 81000,\n      "evidence_reference": "componen'
    't-isolation-v1",\n      "verified_by": "fixture-reviewer",\n      "verified_'
    'at": "2026-01-10T04:00:00+00:00",\n      "id": "00000000-0000-0000-0000-0000'
    '000153da",\n      "observation_id": "00000000-0000-0000-0000-000000014c0a",\n'
    '      "measurement": {\n        "content": "{\\"created_at\\":\\"2026-01-1'
    '0T04:00:00+00:00\\",\\"delivery_id\\":null,\\"id\\":\\"00000000-0000-0000-00'
    '00-000000014c0a\\",\\"parameter\\":\\"water_level\\",\\"rating_curve_correct'
    'ion_version\\":null,\\"rating_curve_id\\":null,\\"source\\":\\"measured\\",'
    '\\"station_id\\":\\"00000000-0000-0000-0000-000000013c68\\",\\"timestamp\\":'
    '\\"2026-01-10T00:30:00+00:00\\",\\"value\\":2.0}"\n      }\n    },\n    {\n  '
    '    "tenant_id": "00000000-0000-0000-0000-000000000001",\n      "station_id"'
    ': "00000000-0000-0000-0000-000000013c68",\n      "endpoint": "https://example'
    '.invalid/levels/",\n      "api_station_id": 81000,\n      "evidence_reference'
    '": "component-isolation-v1",\n      "verified_by": "fixture-reviewer",\n   '
    '   "verified_at": "2026-01-10T04:00:00+00:00",\n      "id": "00000000-0000-'
    '0000-0000-0000000153db",\n      "observation_id": "00000000-0000-0000-0000-00'
    '0000014c0b",\n      "measurement": {\n        "content": "{\\"created_at\\'
    '":\\"2026-01-10T04:00:00+00:00\\",\\"delivery_id\\":null,\\"id\\":\\"00000'
    '000-0000-0000-0000-000000014c0b\\",\\"parameter\\":\\"water_level\\",\\"rati'
    'ng_curve_correction_version\\":null,\\"rating_curve_id\\":null,\\"source\\":'
    '\\"measured\\",\\"station_id\\":\\"00000000-0000-0000-0000-000000013c68\\",'
    '\\"timestamp\\":\\"2026-01-10T02:00:00+00:00\\",\\"value\\":2.0}"\n      }'
    '\n    },\n    {\n      "tenant_id": "00000000-0000-0000-0000-000000000001",\n '
    '     "station_id": "00000000-0000-0000-0000-000000014050",\n      "endpoint"'
    ': "https://example.invalid/levels/",\n      "api_station_id": 82000,\n      "'
    'evidence_reference": "component-isolation-v1",\n      "verified_by": "fixtur'
    'e-reviewer",\n      "verified_at": "2026-01-10T04:00:00+00:00",\n      "id"'
    ': "00000000-0000-0000-0000-0000000153dc",\n      "observation_id": "00000000-'
    '0000-0000-0000-000000014c0c",\n      "measurement": {\n        "content": "{'
    '\\"created_at\\":\\"2026-01-10T04:00:00+00:00\\",\\"delivery_id\\":null,\\"'
    'id\\":\\"00000000-0000-0000-0000-000000014c0c\\",\\"parameter\\":\\"water_le'
    'vel\\",\\"rating_curve_correction_version\\":null,\\"rating_curve_id\\":null,'
    '\\"source\\":\\"measured\\",\\"station_id\\":\\"00000000-0000-0000-0000-000'
    '000014050\\",\\"timestamp\\":\\"2026-01-09T23:30:00+00:00\\",\\"value\\":2.'
    '0}"\n      }\n    },\n    {\n      "tenant_id": "00000000-0000-0000-0000-00000'
    '0000001",\n      "station_id": "00000000-0000-0000-0000-000000014050",\n     '
    ' "endpoint": "https://example.invalid/levels/",\n      "api_station_id": 820'
    '00,\n      "evidence_reference": "component-isolation-v1",\n      "verified_b'
    'y": "fixture-reviewer",\n      "verified_at": "2026-01-10T04:00:00+00:00",'
    '\n      "id": "00000000-0000-0000-0000-0000000153dd",\n      "observation_id'
    '": "00000000-0000-0000-0000-000000014c0d",\n      "measurement": {\n        '
    '"content": "{\\"created_at\\":\\"2026-01-10T04:00:00+00:00\\",\\"delivery_'
    'id\\":null,\\"id\\":\\"00000000-0000-0000-0000-000000014c0d\\",\\"parameter'
    '\\":\\"water_level\\",\\"rating_curve_correction_version\\":null,\\"rating_c'
    'urve_id\\":null,\\"source\\":\\"measured\\",\\"station_id\\":\\"00000000-0'
    '000-0000-0000-000000014050\\",\\"timestamp\\":\\"2026-01-10T00:30:00+00:00\\"'
    ',\\"value\\":2.0}"\n      }\n    },\n    {\n      "tenant_id": "00000000-000'
    '0-0000-0000-000000000001",\n      "station_id": "00000000-0000-0000-0000-0000'
    '00014050",\n      "endpoint": "https://example.invalid/levels/",\n      "api'
    '_station_id": 82000,\n      "evidence_reference": "component-isolation-v1",\n'
    '      "verified_by": "fixture-reviewer",\n      "verified_at": "2026-01-10T'
    '04:00:00+00:00",\n      "id": "00000000-0000-0000-0000-0000000153de",\n      '
    '"observation_id": "00000000-0000-0000-0000-000000014c0e",\n      "measurement'
    '": {\n        "content": "{\\"created_at\\":\\"2026-01-10T04:00:00+00:00\\'
    '",\\"delivery_id\\":null,\\"id\\":\\"00000000-0000-0000-0000-000000014c0e\\'
    '",\\"parameter\\":\\"water_level\\",\\"rating_curve_correction_version\\":n'
    'ull,\\"rating_curve_id\\":null,\\"source\\":\\"measured\\",\\"station_id\\'
    '":\\"00000000-0000-0000-0000-000000014050\\",\\"timestamp\\":\\"2026-01-10T0'
    '2:00:00+00:00\\",\\"value\\":2.0}"\n      }\n    }\n  ],\n  "provisional_rows'
    '": [\n    {\n      "fingerprint": "e005693b6f6b53c95a39f8ea0a3f0c440bc6e7d7f93'
    'acbfd1ad273b19374dd57",\n      "tenant_id": "00000000-0000-0000-0000-00000000'
    '0001",\n      "station_id": "00000000-0000-0000-0000-000000013c68",\n      "'
    'observation_id": "00000000-0000-0000-0000-000000014c09",\n      "rating_curve'
    '_id": "00000000-0000-0000-0000-000000014439",\n      "feed_evidence_id": "00'
    '000000-0000-0000-0000-0000000153d9",\n      "reference_proof_id": "00000000-0'
    '000-0000-0000-000000014ff1",\n      "discharge": 10000.0,\n      "content": '
    '"{\\"conversion_version\\":\\"expired-rating-linear-reference-v1\\",\\"curve'
    '\\":{\\"created_at\\":\\"2026-01-09T00:00:00+00:00\\",\\"delivery_id\\":nul'
    'l,\\"id\\":\\"00000000-0000-0000-0000-000000014439\\",\\"interpolation\\":\\'
    '"linear\\",\\"points\\":[{\\"discharge\\":4.0,\\"water_level\\":1.0},{\\"'
    'discharge\\":10000.0,\\"water_level\\":2.0}],\\"rating_type_label\\":null,\\'
    '"station_id\\":\\"00000000-0000-0000-0000-000000013c68\\",\\"uploaded_by\\":'
    'null,\\"valid_from\\":\\"2025-01-10T00:00:00+00:00\\",\\"valid_to\\":\\"202'
    '6-01-10T01:30:00+00:00\\",\\"version\\":1},\\"discharge\\":10000.0,\\"feed_e'
    'vidence\\":{\\"api_station_id\\":81000,\\"endpoint\\":\\"https://example.inv'
    'alid/levels/\\",\\"evidence_reference\\":\\"component-isolation-v1\\",\\"id'
    '\\":\\"00000000-0000-0000-0000-0000000153d9\\",\\"measurement\\":{\\"content'
    '\\":\\"{\\\\\\"created_at\\\\\\":\\\\\\"2026-01-10T04:00:00+00:00\\\\\\",\\'
    '\\\\"delivery_id\\\\\\":null,\\\\\\"id\\\\\\":\\\\\\"00000000-0000-0000-0000-'
    '000000014c09\\\\\\",\\\\\\"parameter\\\\\\":\\\\\\"water_level\\\\\\",\\\\\\'
    '"rating_curve_correction_version\\\\\\":null,\\\\\\"rating_curve_id\\\\\\":nul'
    'l,\\\\\\"source\\\\\\":\\\\\\"measured\\\\\\",\\\\\\"station_id\\\\\\":\\\\'
    '\\"00000000-0000-0000-0000-000000013c68\\\\\\",\\\\\\"timestamp\\\\\\":\\\\\\'
    '"2026-01-09T23:30:00+00:00\\\\\\",\\\\\\"value\\\\\\":2.0}\\"},\\"observatio'
    'n_id\\":\\"00000000-0000-0000-0000-000000014c09\\",\\"station_id\\":\\"00000'
    '000-0000-0000-0000-000000013c68\\",\\"tenant_id\\":\\"00000000-0000-0000-0000-'
    '000000000001\\",\\"verified_at\\":\\"2026-01-10T04:00:00+00:00\\",\\"verifie'
    'd_by\\":\\"fixture-reviewer\\"},\\"measurement\\":{\\"created_at\\":\\"202'
    '6-01-10T04:00:00+00:00\\",\\"delivery_id\\":null,\\"id\\":\\"00000000-0000-0'
    '000-0000-000000014c09\\",\\"parameter\\":\\"water_level\\",\\"rating_curve_c'
    'orrection_version\\":null,\\"rating_curve_id\\":null,\\"source\\":\\"measure'
    'd\\",\\"station_id\\":\\"00000000-0000-0000-0000-000000013c68\\",\\"timestam'
    'p\\":\\"2026-01-09T23:30:00+00:00\\",\\"value\\":2.0},\\"qc\\":{\\"qc_flag'
    's\\":[{\\"detail\\":null,\\"rule_id\\":\\"range_check\\",\\"rule_version\\'
    '":\\"fixture-v1\\",\\"status\\":\\"qc_passed\\"}],\\"qc_rule_version\\":'
    '\\"fixture-v1\\",\\"qc_status\\":\\"qc_passed\\"},\\"reference_proof\\":{'
    '\\"api_station_id\\":81000,\\"curve\\":{\\"content\\":\\"{\\\\\\"created_a'
    't\\\\\\":\\\\\\"2026-01-09T00:00:00+00:00\\\\\\",\\\\\\"delivery_id\\\\\\":nu'
    'll,\\\\\\"id\\\\\\":\\\\\\"00000000-0000-0000-0000-000000014439\\\\\\",\\\\\\'
    '"interpolation\\\\\\":\\\\\\"linear\\\\\\",\\\\\\"points\\\\\\":[{\\\\\\"di'
    'scharge\\\\\\":4.0,\\\\\\"water_level\\\\\\":1.0},{\\\\\\"discharge\\\\\\":10'
    '000.0,\\\\\\"water_level\\\\\\":2.0}],\\\\\\"rating_type_label\\\\\\":null,\\'
    '\\\\"station_id\\\\\\":\\\\\\"00000000-0000-0000-0000-000000013c68\\\\\\",\\\\'
    '\\"uploaded_by\\\\\\":null,\\\\\\"valid_from\\\\\\":\\\\\\"2025-01-10T00:00:0'
    '0+00:00\\\\\\",\\\\\\"valid_to\\\\\\":\\\\\\"2026-01-10T01:30:00+00:00\\\\\\"'
    ',\\\\\\"version\\\\\\":1}\\"},\\"curve_reference\\":\\"gauge_zero\\",\\"cu'
    'rve_unit\\":\\"m\\",\\"endpoint\\":\\"https://example.invalid/levels/\\",\\'
    '"evidence_reference\\":\\"component-isolation-v1\\",\\"id\\":\\"00000000-00'
    '00-0000-0000-000000014ff1\\",\\"level_reference\\":\\"gauge_zero\\",\\"level'
    '_unit\\":\\"m\\",\\"offset_m\\":0.0,\\"rating_curve_id\\":\\"00000000-0000'
    '-0000-0000-000000014439\\",\\"station_id\\":\\"00000000-0000-0000-0000-0000000'
    '13c68\\",\\"tenant_id\\":\\"00000000-0000-0000-0000-000000000001\\",\\"verif'
    'ied_at\\":\\"2026-01-10T04:00:00+00:00\\",\\"verified_by\\":\\"fixture-revie'
    'wer\\"}}",\n      "captured_at": "2026-01-10T04:00:00+00:00"\n    },\n    {'
    '\n      "fingerprint": "e54ebc60fe773692d4c53b4fe707b6d730aea5c81f9856ca5a139'
    'f7bad375e5f",\n      "tenant_id": "00000000-0000-0000-0000-000000000001",\n  '
    '    "station_id": "00000000-0000-0000-0000-000000013c68",\n      "observation'
    '_id": "00000000-0000-0000-0000-000000014c0a",\n      "rating_curve_id": "000'
    '00000-0000-0000-0000-000000014439",\n      "feed_evidence_id": "00000000-0000'
    '-0000-0000-0000000153da",\n      "reference_proof_id": "00000000-0000-0000-00'
    '00-000000014ff1",\n      "discharge": 10000.0,\n      "content": "{\\"conve'
    'rsion_version\\":\\"expired-rating-linear-reference-v1\\",\\"curve\\":{\\"cr'
    'eated_at\\":\\"2026-01-09T00:00:00+00:00\\",\\"delivery_id\\":null,\\"id\\"'
    ':\\"00000000-0000-0000-0000-000000014439\\",\\"interpolation\\":\\"linear\\"'
    ',\\"points\\":[{\\"discharge\\":4.0,\\"water_level\\":1.0},{\\"discharge\\'
    '":10000.0,\\"water_level\\":2.0}],\\"rating_type_label\\":null,\\"station_id'
    '\\":\\"00000000-0000-0000-0000-000000013c68\\",\\"uploaded_by\\":null,\\"val'
    'id_from\\":\\"2025-01-10T00:00:00+00:00\\",\\"valid_to\\":\\"2026-01-10T01:3'
    '0:00+00:00\\",\\"version\\":1},\\"discharge\\":10000.0,\\"feed_evidence\\":'
    '{\\"api_station_id\\":81000,\\"endpoint\\":\\"https://example.invalid/levels/'
    '\\",\\"evidence_reference\\":\\"component-isolation-v1\\",\\"id\\":\\"0000'
    '0000-0000-0000-0000-0000000153da\\",\\"measurement\\":{\\"content\\":\\"{\\'
    '\\\\"created_at\\\\\\":\\\\\\"2026-01-10T04:00:00+00:00\\\\\\",\\\\\\"deliver'
    'y_id\\\\\\":null,\\\\\\"id\\\\\\":\\\\\\"00000000-0000-0000-0000-000000014c0a'
    '\\\\\\",\\\\\\"parameter\\\\\\":\\\\\\"water_level\\\\\\",\\\\\\"rating_curv'
    'e_correction_version\\\\\\":null,\\\\\\"rating_curve_id\\\\\\":null,\\\\\\"sou'
    'rce\\\\\\":\\\\\\"measured\\\\\\",\\\\\\"station_id\\\\\\":\\\\\\"00000000-0'
    '000-0000-0000-000000013c68\\\\\\",\\\\\\"timestamp\\\\\\":\\\\\\"2026-01-10T00'
    ':30:00+00:00\\\\\\",\\\\\\"value\\\\\\":2.0}\\"},\\"observation_id\\":\\"00'
    '000000-0000-0000-0000-000000014c0a\\",\\"station_id\\":\\"00000000-0000-0000-0'
    '000-000000013c68\\",\\"tenant_id\\":\\"00000000-0000-0000-0000-000000000001\\'
    '",\\"verified_at\\":\\"2026-01-10T04:00:00+00:00\\",\\"verified_by\\":\\"f'
    'ixture-reviewer\\"},\\"measurement\\":{\\"created_at\\":\\"2026-01-10T04:00:'
    '00+00:00\\",\\"delivery_id\\":null,\\"id\\":\\"00000000-0000-0000-0000-00000'
    '0014c0a\\",\\"parameter\\":\\"water_level\\",\\"rating_curve_correction_vers'
    'ion\\":null,\\"rating_curve_id\\":null,\\"source\\":\\"measured\\",\\"stat'
    'ion_id\\":\\"00000000-0000-0000-0000-000000013c68\\",\\"timestamp\\":\\"2026'
    '-01-10T00:30:00+00:00\\",\\"value\\":2.0},\\"qc\\":{\\"qc_flags\\":[{\\"de'
    'tail\\":null,\\"rule_id\\":\\"range_check\\",\\"rule_version\\":\\"fixture'
    '-v1\\",\\"status\\":\\"qc_passed\\"}],\\"qc_rule_version\\":\\"fixture-v1'
    '\\",\\"qc_status\\":\\"qc_passed\\"},\\"reference_proof\\":{\\"api_station'
    '_id\\":81000,\\"curve\\":{\\"content\\":\\"{\\\\\\"created_at\\\\\\":\\\\'
    '\\"2026-01-09T00:00:00+00:00\\\\\\",\\\\\\"delivery_id\\\\\\":null,\\\\\\"id'
    '\\\\\\":\\\\\\"00000000-0000-0000-0000-000000014439\\\\\\",\\\\\\"interpolatio'
    'n\\\\\\":\\\\\\"linear\\\\\\",\\\\\\"points\\\\\\":[{\\\\\\"discharge\\\\\\'
    '":4.0,\\\\\\"water_level\\\\\\":1.0},{\\\\\\"discharge\\\\\\":10000.0,\\\\\\'
    '"water_level\\\\\\":2.0}],\\\\\\"rating_type_label\\\\\\":null,\\\\\\"station'
    '_id\\\\\\":\\\\\\"00000000-0000-0000-0000-000000013c68\\\\\\",\\\\\\"uploaded_'
    'by\\\\\\":null,\\\\\\"valid_from\\\\\\":\\\\\\"2025-01-10T00:00:00+00:00\\\\\\'
    '",\\\\\\"valid_to\\\\\\":\\\\\\"2026-01-10T01:30:00+00:00\\\\\\",\\\\\\"vers'
    'ion\\\\\\":1}\\"},\\"curve_reference\\":\\"gauge_zero\\",\\"curve_unit\\":'
    '\\"m\\",\\"endpoint\\":\\"https://example.invalid/levels/\\",\\"evidence_re'
    'ference\\":\\"component-isolation-v1\\",\\"id\\":\\"00000000-0000-0000-0000-'
    '000000014ff1\\",\\"level_reference\\":\\"gauge_zero\\",\\"level_unit\\":\\'
    '"m\\",\\"offset_m\\":0.0,\\"rating_curve_id\\":\\"00000000-0000-0000-0000-0'
    '00000014439\\",\\"station_id\\":\\"00000000-0000-0000-0000-000000013c68\\",\\'
    '"tenant_id\\":\\"00000000-0000-0000-0000-000000000001\\",\\"verified_at\\":'
    '\\"2026-01-10T04:00:00+00:00\\",\\"verified_by\\":\\"fixture-reviewer\\"}}"'
    ',\n      "captured_at": "2026-01-10T04:00:00+00:00"\n    },\n    {\n      "fi'
    'ngerprint": "882de02602fcb3962a281a8254ed62feaca62d42149abbb3cb5344191e3277e'
    '5",\n      "tenant_id": "00000000-0000-0000-0000-000000000001",\n      "stat'
    'ion_id": "00000000-0000-0000-0000-000000013c68",\n      "observation_id": "0'
    '0000000-0000-0000-0000-000000014c0b",\n      "rating_curve_id": "00000000-000'
    '0-0000-0000-000000014439",\n      "feed_evidence_id": "00000000-0000-0000-000'
    '0-0000000153db",\n      "reference_proof_id": "00000000-0000-0000-0000-000000'
    '014ff1",\n      "discharge": 10000.0,\n      "content": "{\\"conversion_ver'
    'sion\\":\\"expired-rating-linear-reference-v1\\",\\"curve\\":{\\"created_at'
    '\\":\\"2026-01-09T00:00:00+00:00\\",\\"delivery_id\\":null,\\"id\\":\\"000'
    '00000-0000-0000-0000-000000014439\\",\\"interpolation\\":\\"linear\\",\\"poi'
    'nts\\":[{\\"discharge\\":4.0,\\"water_level\\":1.0},{\\"discharge\\":10000.'
    '0,\\"water_level\\":2.0}],\\"rating_type_label\\":null,\\"station_id\\":\\"'
    '00000000-0000-0000-0000-000000013c68\\",\\"uploaded_by\\":null,\\"valid_from\\'
    '":\\"2025-01-10T00:00:00+00:00\\",\\"valid_to\\":\\"2026-01-10T01:30:00+00:0'
    '0\\",\\"version\\":1},\\"discharge\\":10000.0,\\"feed_evidence\\":{\\"api_'
    'station_id\\":81000,\\"endpoint\\":\\"https://example.invalid/levels/\\",\\"'
    'evidence_reference\\":\\"component-isolation-v1\\",\\"id\\":\\"00000000-0000'
    '-0000-0000-0000000153db\\",\\"measurement\\":{\\"content\\":\\"{\\\\\\"crea'
    'ted_at\\\\\\":\\\\\\"2026-01-10T04:00:00+00:00\\\\\\",\\\\\\"delivery_id\\\\\\'
    '":null,\\\\\\"id\\\\\\":\\\\\\"00000000-0000-0000-0000-000000014c0b\\\\\\",\\'
    '\\\\"parameter\\\\\\":\\\\\\"water_level\\\\\\",\\\\\\"rating_curve_correctio'
    'n_version\\\\\\":null,\\\\\\"rating_curve_id\\\\\\":null,\\\\\\"source\\\\\\"'
    ':\\\\\\"measured\\\\\\",\\\\\\"station_id\\\\\\":\\\\\\"00000000-0000-0000-00'
    '00-000000013c68\\\\\\",\\\\\\"timestamp\\\\\\":\\\\\\"2026-01-10T02:00:00+00:0'
    '0\\\\\\",\\\\\\"value\\\\\\":2.0}\\"},\\"observation_id\\":\\"00000000-0000'
    '-0000-0000-000000014c0b\\",\\"station_id\\":\\"00000000-0000-0000-0000-0000000'
    '13c68\\",\\"tenant_id\\":\\"00000000-0000-0000-0000-000000000001\\",\\"verif'
    'ied_at\\":\\"2026-01-10T04:00:00+00:00\\",\\"verified_by\\":\\"fixture-revie'
    'wer\\"},\\"measurement\\":{\\"created_at\\":\\"2026-01-10T04:00:00+00:00\\"'
    ',\\"delivery_id\\":null,\\"id\\":\\"00000000-0000-0000-0000-000000014c0b\\",'
    '\\"parameter\\":\\"water_level\\",\\"rating_curve_correction_version\\":null'
    ',\\"rating_curve_id\\":null,\\"source\\":\\"measured\\",\\"station_id\\":'
    '\\"00000000-0000-0000-0000-000000013c68\\",\\"timestamp\\":\\"2026-01-10T02:0'
    '0:00+00:00\\",\\"value\\":2.0},\\"qc\\":{\\"qc_flags\\":[{\\"detail\\":nu'
    'll,\\"rule_id\\":\\"range_check\\",\\"rule_version\\":\\"fixture-v1\\",\\'
    '"status\\":\\"qc_passed\\"}],\\"qc_rule_version\\":\\"fixture-v1\\",\\"qc'
    '_status\\":\\"qc_passed\\"},\\"reference_proof\\":{\\"api_station_id\\":810'
    '00,\\"curve\\":{\\"content\\":\\"{\\\\\\"created_at\\\\\\":\\\\\\"2026-01-'
    '09T00:00:00+00:00\\\\\\",\\\\\\"delivery_id\\\\\\":null,\\\\\\"id\\\\\\":\\\\'
    '\\"00000000-0000-0000-0000-000000014439\\\\\\",\\\\\\"interpolation\\\\\\":\\'
    '\\\\"linear\\\\\\",\\\\\\"points\\\\\\":[{\\\\\\"discharge\\\\\\":4.0,\\\\\\'
    '"water_level\\\\\\":1.0},{\\\\\\"discharge\\\\\\":10000.0,\\\\\\"water_level'
    '\\\\\\":2.0}],\\\\\\"rating_type_label\\\\\\":null,\\\\\\"station_id\\\\\\":'
    '\\\\\\"00000000-0000-0000-0000-000000013c68\\\\\\",\\\\\\"uploaded_by\\\\\\":n'
    'ull,\\\\\\"valid_from\\\\\\":\\\\\\"2025-01-10T00:00:00+00:00\\\\\\",\\\\\\"v'
    'alid_to\\\\\\":\\\\\\"2026-01-10T01:30:00+00:00\\\\\\",\\\\\\"version\\\\\\":'
    '1}\\"},\\"curve_reference\\":\\"gauge_zero\\",\\"curve_unit\\":\\"m\\",\\'
    '"endpoint\\":\\"https://example.invalid/levels/\\",\\"evidence_reference\\":'
    '\\"component-isolation-v1\\",\\"id\\":\\"00000000-0000-0000-0000-000000014ff1'
    '\\",\\"level_reference\\":\\"gauge_zero\\",\\"level_unit\\":\\"m\\",\\"o'
    'ffset_m\\":0.0,\\"rating_curve_id\\":\\"00000000-0000-0000-0000-000000014439\\'
    '",\\"station_id\\":\\"00000000-0000-0000-0000-000000013c68\\",\\"tenant_id\\'
    '":\\"00000000-0000-0000-0000-000000000001\\",\\"verified_at\\":\\"2026-01-10'
    'T04:00:00+00:00\\",\\"verified_by\\":\\"fixture-reviewer\\"}}",\n      "cap'
    'tured_at": "2026-01-10T04:00:00+00:00"\n    },\n    {\n      "fingerprint": '
    '"e01954ec70cce1ecb758234f9eec9612677f8302b745c189b4af893f3308a69a",\n      "t'
    'enant_id": "00000000-0000-0000-0000-000000000001",\n      "station_id": "000'
    '00000-0000-0000-0000-000000014050",\n      "observation_id": "00000000-0000-0'
    '000-0000-000000014c0c",\n      "rating_curve_id": "00000000-0000-0000-0000-00'
    '000001443a",\n      "feed_evidence_id": "00000000-0000-0000-0000-0000000153dc'
    '",\n      "reference_proof_id": "00000000-0000-0000-0000-000000014ff2",\n    '
    '  "discharge": 10000.0,\n      "content": "{\\"conversion_version\\":\\"ex'
    'pired-rating-linear-reference-v1\\",\\"curve\\":{\\"created_at\\":\\"2026-01'
    '-09T00:00:00+00:00\\",\\"delivery_id\\":null,\\"id\\":\\"00000000-0000-0000-'
    '0000-00000001443a\\",\\"interpolation\\":\\"linear\\",\\"points\\":[{\\"di'
    'scharge\\":4.0,\\"water_level\\":1.0},{\\"discharge\\":10000.0,\\"water_leve'
    'l\\":2.0}],\\"rating_type_label\\":null,\\"station_id\\":\\"00000000-0000-00'
    '00-0000-000000014050\\",\\"uploaded_by\\":null,\\"valid_from\\":\\"2025-01-1'
    '0T00:00:00+00:00\\",\\"valid_to\\":\\"2026-01-10T01:30:00+00:00\\",\\"versio'
    'n\\":1},\\"discharge\\":10000.0,\\"feed_evidence\\":{\\"api_station_id\\":8'
    '2000,\\"endpoint\\":\\"https://example.invalid/levels/\\",\\"evidence_referen'
    'ce\\":\\"component-isolation-v1\\",\\"id\\":\\"00000000-0000-0000-0000-00000'
    '00153dc\\",\\"measurement\\":{\\"content\\":\\"{\\\\\\"created_at\\\\\\":'
    '\\\\\\"2026-01-10T04:00:00+00:00\\\\\\",\\\\\\"delivery_id\\\\\\":null,\\\\\\'
    '"id\\\\\\":\\\\\\"00000000-0000-0000-0000-000000014c0c\\\\\\",\\\\\\"paramete'
    'r\\\\\\":\\\\\\"water_level\\\\\\",\\\\\\"rating_curve_correction_version\\\\'
    '\\":null,\\\\\\"rating_curve_id\\\\\\":null,\\\\\\"source\\\\\\":\\\\\\"meas'
    'ured\\\\\\",\\\\\\"station_id\\\\\\":\\\\\\"00000000-0000-0000-0000-0000000140'
    '50\\\\\\",\\\\\\"timestamp\\\\\\":\\\\\\"2026-01-09T23:30:00+00:00\\\\\\",\\'
    '\\\\"value\\\\\\":2.0}\\"},\\"observation_id\\":\\"00000000-0000-0000-0000-0'
    '00000014c0c\\",\\"station_id\\":\\"00000000-0000-0000-0000-000000014050\\",\\'
    '"tenant_id\\":\\"00000000-0000-0000-0000-000000000001\\",\\"verified_at\\":'
    '\\"2026-01-10T04:00:00+00:00\\",\\"verified_by\\":\\"fixture-reviewer\\"},\\'
    '"measurement\\":{\\"created_at\\":\\"2026-01-10T04:00:00+00:00\\",\\"delive'
    'ry_id\\":null,\\"id\\":\\"00000000-0000-0000-0000-000000014c0c\\",\\"paramet'
    'er\\":\\"water_level\\",\\"rating_curve_correction_version\\":null,\\"rating'
    '_curve_id\\":null,\\"source\\":\\"measured\\",\\"station_id\\":\\"00000000'
    '-0000-0000-0000-000000014050\\",\\"timestamp\\":\\"2026-01-09T23:30:00+00:00\\'
    '",\\"value\\":2.0},\\"qc\\":{\\"qc_flags\\":[{\\"detail\\":null,\\"rule_'
    'id\\":\\"range_check\\",\\"rule_version\\":\\"fixture-v1\\",\\"status\\":'
    '\\"qc_passed\\"}],\\"qc_rule_version\\":\\"fixture-v1\\",\\"qc_status\\":'
    '\\"qc_passed\\"},\\"reference_proof\\":{\\"api_station_id\\":82000,\\"curve'
    '\\":{\\"content\\":\\"{\\\\\\"created_at\\\\\\":\\\\\\"2026-01-09T00:00:00+'
    '00:00\\\\\\",\\\\\\"delivery_id\\\\\\":null,\\\\\\"id\\\\\\":\\\\\\"00000000'
    '-0000-0000-0000-00000001443a\\\\\\",\\\\\\"interpolation\\\\\\":\\\\\\"linear'
    '\\\\\\",\\\\\\"points\\\\\\":[{\\\\\\"discharge\\\\\\":4.0,\\\\\\"water_leve'
    'l\\\\\\":1.0},{\\\\\\"discharge\\\\\\":10000.0,\\\\\\"water_level\\\\\\":2.0}'
    '],\\\\\\"rating_type_label\\\\\\":null,\\\\\\"station_id\\\\\\":\\\\\\"000000'
    '00-0000-0000-0000-000000014050\\\\\\",\\\\\\"uploaded_by\\\\\\":null,\\\\\\"va'
    'lid_from\\\\\\":\\\\\\"2025-01-10T00:00:00+00:00\\\\\\",\\\\\\"valid_to\\\\\\'
    '":\\\\\\"2026-01-10T01:30:00+00:00\\\\\\",\\\\\\"version\\\\\\":1}\\"},\\"c'
    'urve_reference\\":\\"gauge_zero\\",\\"curve_unit\\":\\"m\\",\\"endpoint\\'
    '":\\"https://example.invalid/levels/\\",\\"evidence_reference\\":\\"componen'
    't-isolation-v1\\",\\"id\\":\\"00000000-0000-0000-0000-000000014ff2\\",\\"lev'
    'el_reference\\":\\"gauge_zero\\",\\"level_unit\\":\\"m\\",\\"offset_m\\":'
    '0.0,\\"rating_curve_id\\":\\"00000000-0000-0000-0000-00000001443a\\",\\"stati'
    'on_id\\":\\"00000000-0000-0000-0000-000000014050\\",\\"tenant_id\\":\\"00000'
    '000-0000-0000-0000-000000000001\\",\\"verified_at\\":\\"2026-01-10T04:00:00+00'
    ':00\\",\\"verified_by\\":\\"fixture-reviewer\\"}}",\n      "captured_at": '
    '"2026-01-10T04:00:00+00:00"\n    },\n    {\n      "fingerprint": "a9879dfc554'
    'dc7f5579cf167c746e6ea84bfe105df68a8f433b723146bbce312",\n      "tenant_id": "'
    '00000000-0000-0000-0000-000000000001",\n      "station_id": "00000000-0000-00'
    '00-0000-000000014050",\n      "observation_id": "00000000-0000-0000-0000-0000'
    '00014c0d",\n      "rating_curve_id": "00000000-0000-0000-0000-00000001443a",'
    '\n      "feed_evidence_id": "00000000-0000-0000-0000-0000000153dd",\n      "r'
    'eference_proof_id": "00000000-0000-0000-0000-000000014ff2",\n      "discharge'
    '": 10000.0,\n      "content": "{\\"conversion_version\\":\\"expired-rating-'
    'linear-reference-v1\\",\\"curve\\":{\\"created_at\\":\\"2026-01-09T00:00:00+'
    '00:00\\",\\"delivery_id\\":null,\\"id\\":\\"00000000-0000-0000-0000-00000001'
    '443a\\",\\"interpolation\\":\\"linear\\",\\"points\\":[{\\"discharge\\":4'
    '.0,\\"water_level\\":1.0},{\\"discharge\\":10000.0,\\"water_level\\":2.0}],'
    '\\"rating_type_label\\":null,\\"station_id\\":\\"00000000-0000-0000-0000-0000'
    '00014050\\",\\"uploaded_by\\":null,\\"valid_from\\":\\"2025-01-10T00:00:00+0'
    '0:00\\",\\"valid_to\\":\\"2026-01-10T01:30:00+00:00\\",\\"version\\":1},\\'
    '"discharge\\":10000.0,\\"feed_evidence\\":{\\"api_station_id\\":82000,\\"en'
    'dpoint\\":\\"https://example.invalid/levels/\\",\\"evidence_reference\\":\\"'
    'component-isolation-v1\\",\\"id\\":\\"00000000-0000-0000-0000-0000000153dd\\"'
    ',\\"measurement\\":{\\"content\\":\\"{\\\\\\"created_at\\\\\\":\\\\\\"2026'
    '-01-10T04:00:00+00:00\\\\\\",\\\\\\"delivery_id\\\\\\":null,\\\\\\"id\\\\\\":'
    '\\\\\\"00000000-0000-0000-0000-000000014c0d\\\\\\",\\\\\\"parameter\\\\\\":\\'
    '\\\\"water_level\\\\\\",\\\\\\"rating_curve_correction_version\\\\\\":null,\\'
    '\\\\"rating_curve_id\\\\\\":null,\\\\\\"source\\\\\\":\\\\\\"measured\\\\\\"'
    ',\\\\\\"station_id\\\\\\":\\\\\\"00000000-0000-0000-0000-000000014050\\\\\\",'
    '\\\\\\"timestamp\\\\\\":\\\\\\"2026-01-10T00:30:00+00:00\\\\\\",\\\\\\"value'
    '\\\\\\":2.0}\\"},\\"observation_id\\":\\"00000000-0000-0000-0000-000000014c0d'
    '\\",\\"station_id\\":\\"00000000-0000-0000-0000-000000014050\\",\\"tenant_id'
    '\\":\\"00000000-0000-0000-0000-000000000001\\",\\"verified_at\\":\\"2026-01-'
    '10T04:00:00+00:00\\",\\"verified_by\\":\\"fixture-reviewer\\"},\\"measuremen'
    't\\":{\\"created_at\\":\\"2026-01-10T04:00:00+00:00\\",\\"delivery_id\\":nu'
    'll,\\"id\\":\\"00000000-0000-0000-0000-000000014c0d\\",\\"parameter\\":\\"w'
    'ater_level\\",\\"rating_curve_correction_version\\":null,\\"rating_curve_id\\'
    '":null,\\"source\\":\\"measured\\",\\"station_id\\":\\"00000000-0000-0000-'
    '0000-000000014050\\",\\"timestamp\\":\\"2026-01-10T00:30:00+00:00\\",\\"valu'
    'e\\":2.0},\\"qc\\":{\\"qc_flags\\":[{\\"detail\\":null,\\"rule_id\\":\\"'
    'range_check\\",\\"rule_version\\":\\"fixture-v1\\",\\"status\\":\\"qc_pass'
    'ed\\"}],\\"qc_rule_version\\":\\"fixture-v1\\",\\"qc_status\\":\\"qc_passe'
    'd\\"},\\"reference_proof\\":{\\"api_station_id\\":82000,\\"curve\\":{\\"co'
    'ntent\\":\\"{\\\\\\"created_at\\\\\\":\\\\\\"2026-01-09T00:00:00+00:00\\\\\\'
    '",\\\\\\"delivery_id\\\\\\":null,\\\\\\"id\\\\\\":\\\\\\"00000000-0000-0000-'
    '0000-00000001443a\\\\\\",\\\\\\"interpolation\\\\\\":\\\\\\"linear\\\\\\",\\'
    '\\\\"points\\\\\\":[{\\\\\\"discharge\\\\\\":4.0,\\\\\\"water_level\\\\\\":1'
    '.0},{\\\\\\"discharge\\\\\\":10000.0,\\\\\\"water_level\\\\\\":2.0}],\\\\\\"r'
    'ating_type_label\\\\\\":null,\\\\\\"station_id\\\\\\":\\\\\\"00000000-0000-000'
    '0-0000-000000014050\\\\\\",\\\\\\"uploaded_by\\\\\\":null,\\\\\\"valid_from\\'
    '\\\\":\\\\\\"2025-01-10T00:00:00+00:00\\\\\\",\\\\\\"valid_to\\\\\\":\\\\\\"'
    '2026-01-10T01:30:00+00:00\\\\\\",\\\\\\"version\\\\\\":1}\\"},\\"curve_refere'
    'nce\\":\\"gauge_zero\\",\\"curve_unit\\":\\"m\\",\\"endpoint\\":\\"https'
    '://example.invalid/levels/\\",\\"evidence_reference\\":\\"component-isolation-'
    'v1\\",\\"id\\":\\"00000000-0000-0000-0000-000000014ff2\\",\\"level_reference'
    '\\":\\"gauge_zero\\",\\"level_unit\\":\\"m\\",\\"offset_m\\":0.0,\\"rati'
    'ng_curve_id\\":\\"00000000-0000-0000-0000-00000001443a\\",\\"station_id\\":\\'
    '"00000000-0000-0000-0000-000000014050\\",\\"tenant_id\\":\\"00000000-0000-000'
    '0-0000-000000000001\\",\\"verified_at\\":\\"2026-01-10T04:00:00+00:00\\",\\"'
    'verified_by\\":\\"fixture-reviewer\\"}}",\n      "captured_at": "2026-01-10'
    'T04:00:00+00:00"\n    },\n    {\n      "fingerprint": "d976b02513f075bd88e593d'
    '1c1006f797a5c11a0b6bd8757918755426b799dbf",\n      "tenant_id": "00000000-000'
    '0-0000-0000-000000000001",\n      "station_id": "00000000-0000-0000-0000-0000'
    '00014050",\n      "observation_id": "00000000-0000-0000-0000-000000014c0e",\n'
    '      "rating_curve_id": "00000000-0000-0000-0000-00000001443a",\n      "feed'
    '_evidence_id": "00000000-0000-0000-0000-0000000153de",\n      "reference_proo'
    'f_id": "00000000-0000-0000-0000-000000014ff2",\n      "discharge": 10000.0,\n'
    '      "content": "{\\"conversion_version\\":\\"expired-rating-linear-referen'
    'ce-v1\\",\\"curve\\":{\\"created_at\\":\\"2026-01-09T00:00:00+00:00\\",\\"'
    'delivery_id\\":null,\\"id\\":\\"00000000-0000-0000-0000-00000001443a\\",\\"i'
    'nterpolation\\":\\"linear\\",\\"points\\":[{\\"discharge\\":4.0,\\"water_l'
    'evel\\":1.0},{\\"discharge\\":10000.0,\\"water_level\\":2.0}],\\"rating_type'
    '_label\\":null,\\"station_id\\":\\"00000000-0000-0000-0000-000000014050\\",\\'
    '"uploaded_by\\":null,\\"valid_from\\":\\"2025-01-10T00:00:00+00:00\\",\\"va'
    'lid_to\\":\\"2026-01-10T01:30:00+00:00\\",\\"version\\":1},\\"discharge\\":'
    '10000.0,\\"feed_evidence\\":{\\"api_station_id\\":82000,\\"endpoint\\":\\"h'
    'ttps://example.invalid/levels/\\",\\"evidence_reference\\":\\"component-isolat'
    'ion-v1\\",\\"id\\":\\"00000000-0000-0000-0000-0000000153de\\",\\"measurement'
    '\\":{\\"content\\":\\"{\\\\\\"created_at\\\\\\":\\\\\\"2026-01-10T04:00:00+'
    '00:00\\\\\\",\\\\\\"delivery_id\\\\\\":null,\\\\\\"id\\\\\\":\\\\\\"00000000'
    '-0000-0000-0000-000000014c0e\\\\\\",\\\\\\"parameter\\\\\\":\\\\\\"water_level'
    '\\\\\\",\\\\\\"rating_curve_correction_version\\\\\\":null,\\\\\\"rating_curve'
    '_id\\\\\\":null,\\\\\\"source\\\\\\":\\\\\\"measured\\\\\\",\\\\\\"station_i'
    'd\\\\\\":\\\\\\"00000000-0000-0000-0000-000000014050\\\\\\",\\\\\\"timestamp\\'
    '\\\\":\\\\\\"2026-01-10T02:00:00+00:00\\\\\\",\\\\\\"value\\\\\\":2.0}\\"},'
    '\\"observation_id\\":\\"00000000-0000-0000-0000-000000014c0e\\",\\"station_id'
    '\\":\\"00000000-0000-0000-0000-000000014050\\",\\"tenant_id\\":\\"00000000-0'
    '000-0000-0000-000000000001\\",\\"verified_at\\":\\"2026-01-10T04:00:00+00:00\\'
    '",\\"verified_by\\":\\"fixture-reviewer\\"},\\"measurement\\":{\\"created_'
    'at\\":\\"2026-01-10T04:00:00+00:00\\",\\"delivery_id\\":null,\\"id\\":\\"0'
    '0000000-0000-0000-0000-000000014c0e\\",\\"parameter\\":\\"water_level\\",\\"'
    'rating_curve_correction_version\\":null,\\"rating_curve_id\\":null,\\"source\\'
    '":\\"measured\\",\\"station_id\\":\\"00000000-0000-0000-0000-000000014050\\'
    '",\\"timestamp\\":\\"2026-01-10T02:00:00+00:00\\",\\"value\\":2.0},\\"qc\\'
    '":{\\"qc_flags\\":[{\\"detail\\":null,\\"rule_id\\":\\"range_check\\",\\'
    '"rule_version\\":\\"fixture-v1\\",\\"status\\":\\"qc_passed\\"}],\\"qc_ru'
    'le_version\\":\\"fixture-v1\\",\\"qc_status\\":\\"qc_passed\\"},\\"referen'
    'ce_proof\\":{\\"api_station_id\\":82000,\\"curve\\":{\\"content\\":\\"{\\'
    '\\\\"created_at\\\\\\":\\\\\\"2026-01-09T00:00:00+00:00\\\\\\",\\\\\\"deliver'
    'y_id\\\\\\":null,\\\\\\"id\\\\\\":\\\\\\"00000000-0000-0000-0000-00000001443a'
    '\\\\\\",\\\\\\"interpolation\\\\\\":\\\\\\"linear\\\\\\",\\\\\\"points\\\\\\'
    '":[{\\\\\\"discharge\\\\\\":4.0,\\\\\\"water_level\\\\\\":1.0},{\\\\\\"disch'
    'arge\\\\\\":10000.0,\\\\\\"water_level\\\\\\":2.0}],\\\\\\"rating_type_label\\'
    '\\\\":null,\\\\\\"station_id\\\\\\":\\\\\\"00000000-0000-0000-0000-00000001405'
    '0\\\\\\",\\\\\\"uploaded_by\\\\\\":null,\\\\\\"valid_from\\\\\\":\\\\\\"2025'
    '-01-10T00:00:00+00:00\\\\\\",\\\\\\"valid_to\\\\\\":\\\\\\"2026-01-10T01:30:00'
    '+00:00\\\\\\",\\\\\\"version\\\\\\":1}\\"},\\"curve_reference\\":\\"gauge_z'
    'ero\\",\\"curve_unit\\":\\"m\\",\\"endpoint\\":\\"https://example.invalid/'
    'levels/\\",\\"evidence_reference\\":\\"component-isolation-v1\\",\\"id\\":'
    '\\"00000000-0000-0000-0000-000000014ff2\\",\\"level_reference\\":\\"gauge_zer'
    'o\\",\\"level_unit\\":\\"m\\",\\"offset_m\\":0.0,\\"rating_curve_id\\":\\'
    '"00000000-0000-0000-0000-00000001443a\\",\\"station_id\\":\\"00000000-0000-00'
    '00-0000-000000014050\\",\\"tenant_id\\":\\"00000000-0000-0000-0000-00000000000'
    '1\\",\\"verified_at\\":\\"2026-01-10T04:00:00+00:00\\",\\"verified_by\\":\\'
    '"fixture-reviewer\\"}}",\n      "captured_at": "2026-01-10T04:00:00+00:00"'
    '\n    }\n  ],\n  "permission": {\n    "tenant_id": "00000000-0000-0000-0000-0'
    '00000000001",\n    "state": "enabled",\n    "permission_reference": "fixtu'
    're-only",\n    "inventory_digest": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
    'aaaaaaaaaaaaaaaaaaaaaaa"\n  },\n  "spec": {\n    "code": "component-result"'
    ',\n    "name": "Component result",\n    "network": "component-isolation",'
    '\n    "parameter": "discharge",\n    "lon": 8.25,\n    "lat": 47.25,\n    '
    '"components": [\n      {\n        "code": "component-a",\n        "weight"'
    ': 0.5\n      },\n      {\n        "code": "component-b",\n        "weight": '
    '0.25\n      }\n    ],\n    "timezone": "UTC",\n    "basin_code": null,\n    '
    '"effective_from": null,\n    "tenant_code": "sapphire"\n  },\n  "expected_s'
    'tation": {\n    "location": {\n      "lon": 8.25,\n      "lat": 47.25,\n   '
    '   "altitude_masl": null\n    },\n    "station_kind": "river",\n    "basin_'
    'id": null,\n    "timezone": "UTC",\n    "regulation_type": null,\n    "for'
    'ecast_targets": [\n      "discharge"\n    ],\n    "measured_parameters": [\n '
    '     "discharge"\n    ],\n    "station_status": "onboarding",\n    "created'
    '_at": "2026-01-10T04:00:00+00:00",\n    "updated_at": "2026-01-10T04:00:00+0'
    '0:00",\n    "network": "component-isolation",\n    "ownership": "own",\n '
    '   "wigos_id": null,\n    "gauging_status": "calculated",\n    "water_level'
    '_datum_masl": null,\n    "water_level_unit": null,\n    "tenant_id": "000000'
    '00-0000-0000-0000-000000000001",\n    "id": "$generated_station",\n    "code'
    '": "component-result",\n    "name": "Component result"\n  },\n  "expected_'
    'formula": [\n    {\n      "id": "$generated_formula_0",\n      "calculated_s'
    'tation_id": "$generated_station",\n      "component_station_id": "00000000-0'
    '000-0000-0000-000000013c68",\n      "parameter": "discharge",\n      "weight'
    '": 0.5,\n      "effective_from": "2026-01-10T00:00:00+00:00",\n      "effect'
    'ive_to": null,\n      "created_at": "2026-01-10T04:00:00+00:00"\n    },\n    '
    '{\n      "id": "$generated_formula_1",\n      "calculated_station_id": "$ge'
    'nerated_station",\n      "component_station_id": "00000000-0000-0000-0000-000'
    '000014050",\n      "parameter": "discharge",\n      "weight": 0.25,\n      '
    '"effective_from": "2026-01-10T00:00:00+00:00",\n      "effective_to": null,'
    '\n      "created_at": "2026-01-10T04:00:00+00:00"\n    }\n  ],\n  "expected_o'
    'utcome": {\n    "observations_derived": 2,\n    "observations_missing": 1,\n '
    '   "formula_configured": true,\n    "created": true\n  },\n  "calculation": '
    '[\n    "(1/2)*4+(1/4)*8=4",\n    "(1/2)*6+(1/4)*12=6",\n    "A@120=8, B@120 a'
    'bsent => None/MISSING, not partial 4",\n    "(1/2)*(12-4)=4, first output 8"\n'
    '  ],\n  "notes": [\n    "All arithmetic dyadic exact; == not approximate compa'
    'rator.",\n    "Source precedence selects measured4 over ordinary rating40; ra'
    'ting-only6 retained.",\n    "Curve is expired at C; two ordinary rating times'
    "tamps precede its valid_to. Synthetic history is not rating-conversion accur"
    'acy evidence.",\n    "Provisional early timestamp is in requested window; pro'
    "tected midpoint would add a fourth candidate if leaked; protected B@120 woul"
    'd cure missing result if leaked.",\n    "Generated placeholders use explicit '
    'bijection only; never substitute for literal input IDs.",\n    "Revision2 str'
    "engthens sensitivity signatures without claiming original value control was "
    'a production bug or adding cases.",\n    "Refusal still seeds/deletes origina'
    'l six Q rows, not the strengthened positive scenario."\n  ],\n  "representatio'
    'n_contract": {\n    "station_geometry": "Submitted ST_MakePoint longitude/lat'
    "itude and SRID=4326 are exact; raw PostGIS POINT coordinates, SRID and altit"
    'ude_masl checked explicitly; no geometry byte-order claim.",\n    "station_pa'
    'rameter_sets": "Only forecast_targets and measured_parameters represent froz'
    "ensets. Compare unique memberships and cardinality to listed literals; prese"
    "rve raw array order within each before/after input snapshot. Do not impose a"
    'rray order on an unordered domain field.",\n    "flags": "Separate frozen lay'
    "ers. Baseline/mixed MISSING row domain/public qc_flags=[]; _obs_to_values co"
    "mpiled/store-value qc_flags=None and raw Pg row mapping qc_flags=None. Nonem"
    "pty lists/details exact and ordered in all layers. No generic None/[] normal"
    'izer. No SQL-NULL vs JSON-null or cursor-driver wrapper inference.",\n    "in'
    'put_records": "All 13 observation fields literal. No generated input IDs.",\n'
    '    "target_columns": "Permissions/feed/proof/provisional tables have exactl'
    "y the frozen columns, no unlisted created timestamp; compare every column. U"
    'nrelated preexisting rows captured without normalization."\n  },\n  "reference'
    '_proof_rows": [\n    {\n      "id": "00000000-0000-0000-0000-000000014ff1",\n'
    '      "tenant_id": "00000000-0000-0000-0000-000000000001",\n      "station_id'
    '": "00000000-0000-0000-0000-000000013c68",\n      "rating_curve_id": "000000'
    '00-0000-0000-0000-000000014439",\n      "content": "{\\"api_station_id\\":81'
    '000,\\"curve\\":{\\"content\\":\\"{\\\\\\"created_at\\\\\\":\\\\\\"2026-01'
    '-09T00:00:00+00:00\\\\\\",\\\\\\"delivery_id\\\\\\":null,\\\\\\"id\\\\\\":\\'
    '\\\\"00000000-0000-0000-0000-000000014439\\\\\\",\\\\\\"interpolation\\\\\\":'
    '\\\\\\"linear\\\\\\",\\\\\\"points\\\\\\":[{\\\\\\"discharge\\\\\\":4.0,\\\\'
    '\\"water_level\\\\\\":1.0},{\\\\\\"discharge\\\\\\":10000.0,\\\\\\"water_leve'
    'l\\\\\\":2.0}],\\\\\\"rating_type_label\\\\\\":null,\\\\\\"station_id\\\\\\":'
    '\\\\\\"00000000-0000-0000-0000-000000013c68\\\\\\",\\\\\\"uploaded_by\\\\\\":n'
    'ull,\\\\\\"valid_from\\\\\\":\\\\\\"2025-01-10T00:00:00+00:00\\\\\\",\\\\\\"v'
    'alid_to\\\\\\":\\\\\\"2026-01-10T01:30:00+00:00\\\\\\",\\\\\\"version\\\\\\":'
    '1}\\"},\\"curve_reference\\":\\"gauge_zero\\",\\"curve_unit\\":\\"m\\",\\'
    '"endpoint\\":\\"https://example.invalid/levels/\\",\\"evidence_reference\\":'
    '\\"component-isolation-v1\\",\\"id\\":\\"00000000-0000-0000-0000-000000014ff1'
    '\\",\\"level_reference\\":\\"gauge_zero\\",\\"level_unit\\":\\"m\\",\\"o'
    'ffset_m\\":0.0,\\"rating_curve_id\\":\\"00000000-0000-0000-0000-000000014439\\'
    '",\\"station_id\\":\\"00000000-0000-0000-0000-000000013c68\\",\\"tenant_id\\'
    '":\\"00000000-0000-0000-0000-000000000001\\",\\"verified_at\\":\\"2026-01-10'
    'T04:00:00+00:00\\",\\"verified_by\\":\\"fixture-reviewer\\"}",\n      "fing'
    'erprint": "c44b3ef519c6872d0088c6406da8f09d1cd2f25f92a3fc18a78211b64db80ede"'
    '\n    },\n    {\n      "id": "00000000-0000-0000-0000-000000014ff2",\n      "'
    'tenant_id": "00000000-0000-0000-0000-000000000001",\n      "station_id": "00'
    '000000-0000-0000-0000-000000014050",\n      "rating_curve_id": "00000000-0000'
    '-0000-0000-00000001443a",\n      "content": "{\\"api_station_id\\":82000,\\'
    '"curve\\":{\\"content\\":\\"{\\\\\\"created_at\\\\\\":\\\\\\"2026-01-09T00'
    ':00:00+00:00\\\\\\",\\\\\\"delivery_id\\\\\\":null,\\\\\\"id\\\\\\":\\\\\\"0'
    '0000000-0000-0000-0000-00000001443a\\\\\\",\\\\\\"interpolation\\\\\\":\\\\\\"'
    'linear\\\\\\",\\\\\\"points\\\\\\":[{\\\\\\"discharge\\\\\\":4.0,\\\\\\"wate'
    'r_level\\\\\\":1.0},{\\\\\\"discharge\\\\\\":10000.0,\\\\\\"water_level\\\\\\'
    '":2.0}],\\\\\\"rating_type_label\\\\\\":null,\\\\\\"station_id\\\\\\":\\\\\\'
    '"00000000-0000-0000-0000-000000014050\\\\\\",\\\\\\"uploaded_by\\\\\\":null,\\'
    '\\\\"valid_from\\\\\\":\\\\\\"2025-01-10T00:00:00+00:00\\\\\\",\\\\\\"valid_t'
    'o\\\\\\":\\\\\\"2026-01-10T01:30:00+00:00\\\\\\",\\\\\\"version\\\\\\":1}\\"'
    '},\\"curve_reference\\":\\"gauge_zero\\",\\"curve_unit\\":\\"m\\",\\"endp'
    'oint\\":\\"https://example.invalid/levels/\\",\\"evidence_reference\\":\\"co'
    'mponent-isolation-v1\\",\\"id\\":\\"00000000-0000-0000-0000-000000014ff2\\",'
    '\\"level_reference\\":\\"gauge_zero\\",\\"level_unit\\":\\"m\\",\\"offset'
    '_m\\":0.0,\\"rating_curve_id\\":\\"00000000-0000-0000-0000-00000001443a\\",\\'
    '"station_id\\":\\"00000000-0000-0000-0000-000000014050\\",\\"tenant_id\\":\\'
    '"00000000-0000-0000-0000-000000000001\\",\\"verified_at\\":\\"2026-01-10T04:0'
    '0:00+00:00\\",\\"verified_by\\":\\"fixture-reviewer\\"}",\n      "fingerpri'
    'nt": "f1a6cf60480a4713af1e2406f5a36d09a9739cfb68cac9a61f2d93131ff244ac"\n    '
    '}\n  ],\n  "feed_evidence_rows": [\n    {\n      "id": "00000000-0000-0000-00'
    '00-0000000153d9",\n      "tenant_id": "00000000-0000-0000-0000-000000000001",'
    '\n      "station_id": "00000000-0000-0000-0000-000000013c68",\n      "observa'
    'tion_id": "00000000-0000-0000-0000-000000014c09",\n      "content": "{\\"ap'
    'i_station_id\\":81000,\\"endpoint\\":\\"https://example.invalid/levels/\\",\\'
    '"evidence_reference\\":\\"component-isolation-v1\\",\\"id\\":\\"00000000-00'
    '00-0000-0000-0000000153d9\\",\\"measurement\\":{\\"content\\":\\"{\\\\\\"cr'
    'eated_at\\\\\\":\\\\\\"2026-01-10T04:00:00+00:00\\\\\\",\\\\\\"delivery_id\\\\'
    '\\":null,\\\\\\"id\\\\\\":\\\\\\"00000000-0000-0000-0000-000000014c09\\\\\\",'
    '\\\\\\"parameter\\\\\\":\\\\\\"water_level\\\\\\",\\\\\\"rating_curve_correct'
    'ion_version\\\\\\":null,\\\\\\"rating_curve_id\\\\\\":null,\\\\\\"source\\\\\\'
    '":\\\\\\"measured\\\\\\",\\\\\\"station_id\\\\\\":\\\\\\"00000000-0000-0000-'
    '0000-000000013c68\\\\\\",\\\\\\"timestamp\\\\\\":\\\\\\"2026-01-09T23:30:00+00'
    ':00\\\\\\",\\\\\\"value\\\\\\":2.0}\\"},\\"observation_id\\":\\"00000000-00'
    '00-0000-0000-000000014c09\\",\\"station_id\\":\\"00000000-0000-0000-0000-00000'
    '0013c68\\",\\"tenant_id\\":\\"00000000-0000-0000-0000-000000000001\\",\\"ver'
    'ified_at\\":\\"2026-01-10T04:00:00+00:00\\",\\"verified_by\\":\\"fixture-rev'
    'iewer\\"}",\n      "fingerprint": "bc1c5e48aa3c2f22bc3dd6c2a5d9f63edfa20a390a'
    '0ad13ac4cd73d42ba6afcd"\n    },\n    {\n      "id": "00000000-0000-0000-0000-0'
    '000000153da",\n      "tenant_id": "00000000-0000-0000-0000-000000000001",\n  '
    '    "station_id": "00000000-0000-0000-0000-000000013c68",\n      "observation'
    '_id": "00000000-0000-0000-0000-000000014c0a",\n      "content": "{\\"api_st'
    'ation_id\\":81000,\\"endpoint\\":\\"https://example.invalid/levels/\\",\\"ev'
    'idence_reference\\":\\"component-isolation-v1\\",\\"id\\":\\"00000000-0000-0'
    '000-0000-0000000153da\\",\\"measurement\\":{\\"content\\":\\"{\\\\\\"create'
    'd_at\\\\\\":\\\\\\"2026-01-10T04:00:00+00:00\\\\\\",\\\\\\"delivery_id\\\\\\"'
    ':null,\\\\\\"id\\\\\\":\\\\\\"00000000-0000-0000-0000-000000014c0a\\\\\\",\\\\'
    '\\"parameter\\\\\\":\\\\\\"water_level\\\\\\",\\\\\\"rating_curve_correction_'
    'version\\\\\\":null,\\\\\\"rating_curve_id\\\\\\":null,\\\\\\"source\\\\\\":'
    '\\\\\\"measured\\\\\\",\\\\\\"station_id\\\\\\":\\\\\\"00000000-0000-0000-000'
    '0-000000013c68\\\\\\",\\\\\\"timestamp\\\\\\":\\\\\\"2026-01-10T00:30:00+00:00'
    '\\\\\\",\\\\\\"value\\\\\\":2.0}\\"},\\"observation_id\\":\\"00000000-0000-'
    '0000-0000-000000014c0a\\",\\"station_id\\":\\"00000000-0000-0000-0000-00000001'
    '3c68\\",\\"tenant_id\\":\\"00000000-0000-0000-0000-000000000001\\",\\"verifi'
    'ed_at\\":\\"2026-01-10T04:00:00+00:00\\",\\"verified_by\\":\\"fixture-review'
    'er\\"}",\n      "fingerprint": "4dbad91ed36b7fc40ca6d286c7263fd8bb5783f76ede1'
    'baf7eb1b7592c5d8288"\n    },\n    {\n      "id": "00000000-0000-0000-0000-0000'
    '000153db",\n      "tenant_id": "00000000-0000-0000-0000-000000000001",\n     '
    ' "station_id": "00000000-0000-0000-0000-000000013c68",\n      "observation_id'
    '": "00000000-0000-0000-0000-000000014c0b",\n      "content": "{\\"api_stati'
    'on_id\\":81000,\\"endpoint\\":\\"https://example.invalid/levels/\\",\\"evide'
    'nce_reference\\":\\"component-isolation-v1\\",\\"id\\":\\"00000000-0000-0000'
    '-0000-0000000153db\\",\\"measurement\\":{\\"content\\":\\"{\\\\\\"created_a'
    't\\\\\\":\\\\\\"2026-01-10T04:00:00+00:00\\\\\\",\\\\\\"delivery_id\\\\\\":nu'
    'll,\\\\\\"id\\\\\\":\\\\\\"00000000-0000-0000-0000-000000014c0b\\\\\\",\\\\\\'
    '"parameter\\\\\\":\\\\\\"water_level\\\\\\",\\\\\\"rating_curve_correction_ve'
    'rsion\\\\\\":null,\\\\\\"rating_curve_id\\\\\\":null,\\\\\\"source\\\\\\":\\'
    '\\\\"measured\\\\\\",\\\\\\"station_id\\\\\\":\\\\\\"00000000-0000-0000-0000-'
    '000000013c68\\\\\\",\\\\\\"timestamp\\\\\\":\\\\\\"2026-01-10T02:00:00+00:00\\'
    '\\\\",\\\\\\"value\\\\\\":2.0}\\"},\\"observation_id\\":\\"00000000-0000-00'
    '00-0000-000000014c0b\\",\\"station_id\\":\\"00000000-0000-0000-0000-000000013c'
    '68\\",\\"tenant_id\\":\\"00000000-0000-0000-0000-000000000001\\",\\"verified'
    '_at\\":\\"2026-01-10T04:00:00+00:00\\",\\"verified_by\\":\\"fixture-reviewer'
    '\\"}",\n      "fingerprint": "ab67d6552de174bfd40db96ebeb605d381fb9940789365f'
    '8c0f9e8e0063eb8cb"\n    },\n    {\n      "id": "00000000-0000-0000-0000-000000'
    '0153dc",\n      "tenant_id": "00000000-0000-0000-0000-000000000001",\n      '
    '"station_id": "00000000-0000-0000-0000-000000014050",\n      "observation_id'
    '": "00000000-0000-0000-0000-000000014c0c",\n      "content": "{\\"api_stati'
    'on_id\\":82000,\\"endpoint\\":\\"https://example.invalid/levels/\\",\\"evide'
    'nce_reference\\":\\"component-isolation-v1\\",\\"id\\":\\"00000000-0000-0000'
    '-0000-0000000153dc\\",\\"measurement\\":{\\"content\\":\\"{\\\\\\"created_a'
    't\\\\\\":\\\\\\"2026-01-10T04:00:00+00:00\\\\\\",\\\\\\"delivery_id\\\\\\":nu'
    'll,\\\\\\"id\\\\\\":\\\\\\"00000000-0000-0000-0000-000000014c0c\\\\\\",\\\\\\'
    '"parameter\\\\\\":\\\\\\"water_level\\\\\\",\\\\\\"rating_curve_correction_ve'
    'rsion\\\\\\":null,\\\\\\"rating_curve_id\\\\\\":null,\\\\\\"source\\\\\\":\\'
    '\\\\"measured\\\\\\",\\\\\\"station_id\\\\\\":\\\\\\"00000000-0000-0000-0000-'
    '000000014050\\\\\\",\\\\\\"timestamp\\\\\\":\\\\\\"2026-01-09T23:30:00+00:00\\'
    '\\\\",\\\\\\"value\\\\\\":2.0}\\"},\\"observation_id\\":\\"00000000-0000-00'
    '00-0000-000000014c0c\\",\\"station_id\\":\\"00000000-0000-0000-0000-0000000140'
    '50\\",\\"tenant_id\\":\\"00000000-0000-0000-0000-000000000001\\",\\"verified'
    '_at\\":\\"2026-01-10T04:00:00+00:00\\",\\"verified_by\\":\\"fixture-reviewer'
    '\\"}",\n      "fingerprint": "be26d4b10b0794dd0211c34db1bb6aff92a0d8f6e1cd4f3'
    '47b449c7a52f69344"\n    },\n    {\n      "id": "00000000-0000-0000-0000-000000'
    '0153dd",\n      "tenant_id": "00000000-0000-0000-0000-000000000001",\n      '
    '"station_id": "00000000-0000-0000-0000-000000014050",\n      "observation_id'
    '": "00000000-0000-0000-0000-000000014c0d",\n      "content": "{\\"api_stati'
    'on_id\\":82000,\\"endpoint\\":\\"https://example.invalid/levels/\\",\\"evide'
    'nce_reference\\":\\"component-isolation-v1\\",\\"id\\":\\"00000000-0000-0000'
    '-0000-0000000153dd\\",\\"measurement\\":{\\"content\\":\\"{\\\\\\"created_a'
    't\\\\\\":\\\\\\"2026-01-10T04:00:00+00:00\\\\\\",\\\\\\"delivery_id\\\\\\":nu'
    'll,\\\\\\"id\\\\\\":\\\\\\"00000000-0000-0000-0000-000000014c0d\\\\\\",\\\\\\'
    '"parameter\\\\\\":\\\\\\"water_level\\\\\\",\\\\\\"rating_curve_correction_ve'
    'rsion\\\\\\":null,\\\\\\"rating_curve_id\\\\\\":null,\\\\\\"source\\\\\\":\\'
    '\\\\"measured\\\\\\",\\\\\\"station_id\\\\\\":\\\\\\"00000000-0000-0000-0000-'
    '000000014050\\\\\\",\\\\\\"timestamp\\\\\\":\\\\\\"2026-01-10T00:30:00+00:00\\'
    '\\\\",\\\\\\"value\\\\\\":2.0}\\"},\\"observation_id\\":\\"00000000-0000-00'
    '00-0000-000000014c0d\\",\\"station_id\\":\\"00000000-0000-0000-0000-0000000140'
    '50\\",\\"tenant_id\\":\\"00000000-0000-0000-0000-000000000001\\",\\"verified'
    '_at\\":\\"2026-01-10T04:00:00+00:00\\",\\"verified_by\\":\\"fixture-reviewer'
    '\\"}",\n      "fingerprint": "b4c1c3a9398ee80a66b1a78a4cf8de09539106f07f3338b'
    '64df0f5b51c577541"\n    },\n    {\n      "id": "00000000-0000-0000-0000-000000'
    '0153de",\n      "tenant_id": "00000000-0000-0000-0000-000000000001",\n      '
    '"station_id": "00000000-0000-0000-0000-000000014050",\n      "observation_id'
    '": "00000000-0000-0000-0000-000000014c0e",\n      "content": "{\\"api_stati'
    'on_id\\":82000,\\"endpoint\\":\\"https://example.invalid/levels/\\",\\"evide'
    'nce_reference\\":\\"component-isolation-v1\\",\\"id\\":\\"00000000-0000-0000'
    '-0000-0000000153de\\",\\"measurement\\":{\\"content\\":\\"{\\\\\\"created_a'
    't\\\\\\":\\\\\\"2026-01-10T04:00:00+00:00\\\\\\",\\\\\\"delivery_id\\\\\\":nu'
    'll,\\\\\\"id\\\\\\":\\\\\\"00000000-0000-0000-0000-000000014c0e\\\\\\",\\\\\\'
    '"parameter\\\\\\":\\\\\\"water_level\\\\\\",\\\\\\"rating_curve_correction_ve'
    'rsion\\\\\\":null,\\\\\\"rating_curve_id\\\\\\":null,\\\\\\"source\\\\\\":\\'
    '\\\\"measured\\\\\\",\\\\\\"station_id\\\\\\":\\\\\\"00000000-0000-0000-0000-'
    '000000014050\\\\\\",\\\\\\"timestamp\\\\\\":\\\\\\"2026-01-10T02:00:00+00:00\\'
    '\\\\",\\\\\\"value\\\\\\":2.0}\\"},\\"observation_id\\":\\"00000000-0000-00'
    '00-0000-000000014c0e\\",\\"station_id\\":\\"00000000-0000-0000-0000-0000000140'
    '50\\",\\"tenant_id\\":\\"00000000-0000-0000-0000-000000000001\\",\\"verified'
    '_at\\":\\"2026-01-10T04:00:00+00:00\\",\\"verified_by\\":\\"fixture-reviewer'
    '\\"}",\n      "fingerprint": "7544ed07255bd9f7e215f3e8f8516cf852243f1090522f0'
    '9c1ff14de1f36c329"\n    }\n  ],\n  "expected_domain_public_observations": [\n  '
    '  {\n      "id": "$generated_observation_0",\n      "station_id": "$generat'
    'ed_station",\n      "timestamp": "2026-01-10T00:00:00+00:00",\n      "parame'
    'ter": "discharge",\n      "value": 4.0,\n      "source": "component_derive'
    'd",\n      "rating_curve_id": null,\n      "rating_curve_correction_version":'
    ' null,\n      "qc_status": "qc_passed",\n      "qc_flags": [\n        {\n   '
    '       "rule_id": "upstream_propagated",\n          "rule_version": "compon'
    'ent_derivation/v1",\n          "status": "qc_passed",\n          "detail": '
    '"{\\"component_station_id\\": \\"00000000-0000-0000-0000-000000013c68\\", \\'
    '"component_status\\": \\"qc_passed\\", \\"weight\\": 0.5}"\n        },\n   '
    '     {\n          "rule_id": "upstream_propagated",\n          "rule_version'
    '": "component_derivation/v1",\n          "status": "qc_passed",\n          '
    '"detail": "{\\"component_station_id\\": \\"00000000-0000-0000-0000-000000014'
    '050\\", \\"component_status\\": \\"qc_passed\\", \\"weight\\": 0.25}"\n   '
    '     }\n      ],\n      "qc_rule_version": "component_derivation/v1",\n      '
    '"created_at": "2026-01-10T04:00:00+00:00",\n      "delivery_id": null\n    }'
    ',\n    {\n      "id": "$generated_observation_1",\n      "station_id": "$ge'
    'nerated_station",\n      "timestamp": "2026-01-10T01:00:00+00:00",\n      "p'
    'arameter": "discharge",\n      "value": 6.0,\n      "source": "component_d'
    'erived",\n      "rating_curve_id": null,\n      "rating_curve_correction_versi'
    'on": null,\n      "qc_status": "qc_suspect",\n      "qc_flags": [\n        '
    '{\n          "rule_id": "upstream_propagated",\n          "rule_version": "'
    'component_derivation/v1",\n          "status": "qc_passed",\n          "deta'
    'il": "{\\"component_station_id\\": \\"00000000-0000-0000-0000-000000013c68\\'
    '", \\"component_status\\": \\"qc_passed\\", \\"weight\\": 0.5}"\n        }'
    ',\n        {\n          "rule_id": "upstream_propagated",\n          "rule_ve'
    'rsion": "component_derivation/v1",\n          "status": "qc_suspect",\n    '
    '      "detail": "{\\"component_station_id\\": \\"00000000-0000-0000-0000-000'
    '000014050\\", \\"component_status\\": \\"qc_suspect\\", \\"weight\\": 0.25}'
    '"\n        }\n      ],\n      "qc_rule_version": "component_derivation/v1",\n'
    '      "created_at": "2026-01-10T04:00:00+00:00",\n      "delivery_id": null'
    '\n    },\n    {\n      "id": "$generated_observation_2",\n      "station_id"'
    ': "$generated_station",\n      "timestamp": "2026-01-10T02:00:00+00:00",\n  '
    '    "parameter": "discharge",\n      "value": null,\n      "source": "com'
    'ponent_derived",\n      "rating_curve_id": null,\n      "rating_curve_correcti'
    'on_version": null,\n      "qc_status": "missing",\n      "qc_flags": [],\n '
    '     "qc_rule_version": null,\n      "created_at": "2026-01-10T04:00:00+00:00'
    '",\n      "delivery_id": null\n    }\n  ],\n  "expected_submitted_store_values'
    '_and_raw_rows": [\n    {\n      "id": "$generated_observation_0",\n      "st'
    'ation_id": "$generated_station",\n      "timestamp": "2026-01-10T00:00:00+00'
    ':00",\n      "parameter": "discharge",\n      "value": 4.0,\n      "source'
    '": "component_derived",\n      "rating_curve_id": null,\n      "rating_curve'
    '_correction_version": null,\n      "qc_status": "qc_passed",\n      "qc_flag'
    's": [\n        {\n          "rule_id": "upstream_propagated",\n          "ru'
    'le_version": "component_derivation/v1",\n          "status": "qc_passed",\n'
    '          "detail": "{\\"component_station_id\\": \\"00000000-0000-0000-0000'
    '-000000013c68\\", \\"component_status\\": \\"qc_passed\\", \\"weight\\": 0.'
    '5}"\n        },\n        {\n          "rule_id": "upstream_propagated",\n    '
    '      "rule_version": "component_derivation/v1",\n          "status": "qc_p'
    'assed",\n          "detail": "{\\"component_station_id\\": \\"00000000-0000'
    '-0000-0000-000000014050\\", \\"component_status\\": \\"qc_passed\\", \\"weig'
    'ht\\": 0.25}"\n        }\n      ],\n      "qc_rule_version": "component_deriv'
    'ation/v1",\n      "created_at": "2026-01-10T04:00:00+00:00",\n      "deliver'
    'y_id": null\n    },\n    {\n      "id": "$generated_observation_1",\n      "'
    'station_id": "$generated_station",\n      "timestamp": "2026-01-10T01:00:00+'
    '00:00",\n      "parameter": "discharge",\n      "value": 6.0,\n      "sour'
    'ce": "component_derived",\n      "rating_curve_id": null,\n      "rating_cur'
    've_correction_version": null,\n      "qc_status": "qc_suspect",\n      "qc_f'
    'lags": [\n        {\n          "rule_id": "upstream_propagated",\n          '
    '"rule_version": "component_derivation/v1",\n          "status": "qc_passed'
    '",\n          "detail": "{\\"component_station_id\\": \\"00000000-0000-0000'
    '-0000-000000013c68\\", \\"component_status\\": \\"qc_passed\\", \\"weight\\'
    '": 0.5}"\n        },\n        {\n          "rule_id": "upstream_propagated",'
    '\n          "rule_version": "component_derivation/v1",\n          "status": '
    '"qc_suspect",\n          "detail": "{\\"component_station_id\\": \\"000000'
    '00-0000-0000-0000-000000014050\\", \\"component_status\\": \\"qc_suspect\\", '
    '\\"weight\\": 0.25}"\n        }\n      ],\n      "qc_rule_version": "compone'
    'nt_derivation/v1",\n      "created_at": "2026-01-10T04:00:00+00:00",\n      '
    '"delivery_id": null\n    },\n    {\n      "id": "$generated_observation_2",'
    '\n      "station_id": "$generated_station",\n      "timestamp": "2026-01-10'
    'T02:00:00+00:00",\n      "parameter": "discharge",\n      "value": null,\n '
    '     "source": "component_derived",\n      "rating_curve_id": null,\n      '
    '"rating_curve_correction_version": null,\n      "qc_status": "missing",\n   '
    '   "qc_flags": null,\n      "qc_rule_version": null,\n      "created_at": "'
    '2026-01-10T04:00:00+00:00",\n      "delivery_id": null\n    }\n  ],\n  "change'
    'd_scenario": {\n    "ordinary_observations": [\n      {\n        "id": "0000'
    '0000-0000-0000-0000-000000014821",\n        "station_id": "00000000-0000-0000'
    '-0000-000000013c68",\n        "timestamp": "2026-01-10T00:00:00+00:00",\n    '
    '    "parameter": "discharge",\n        "value": 12.0,\n        "source": '
    '"measured",\n        "rating_curve_id": null,\n        "rating_curve_correcti'
    'on_version": null,\n        "qc_status": "qc_passed",\n        "qc_flags": '
    '[\n          {\n            "rule_id": "range_check",\n            "rule_vers'
    'ion": "fixture-v1",\n            "status": "qc_passed",\n            "deta'
    'il": null\n          }\n        ],\n        "qc_rule_version": "fixture-v1",'
    '\n        "created_at": "2026-01-10T04:00:00+00:00",\n        "delivery_id":'
    ' null\n      },\n      {\n        "id": "00000000-0000-0000-0000-000000014822"'
    ',\n        "station_id": "00000000-0000-0000-0000-000000013c68",\n        "ti'
    'mestamp": "2026-01-10T00:00:00+00:00",\n        "parameter": "discharge",\n'
    '        "value": 40.0,\n        "source": "rating_curve_derived",\n        '
    '"rating_curve_id": "00000000-0000-0000-0000-000000014439",\n        "rating_c'
    'urve_correction_version": "fixture-v1",\n        "qc_status": "qc_passed",'
    '\n        "qc_flags": [\n          {\n            "rule_id": "range_check",'
    '\n            "rule_version": "fixture-v1",\n            "status": "qc_pass'
    'ed",\n            "detail": null\n          }\n        ],\n        "qc_rule_ve'
    'rsion": "fixture-v1",\n        "created_at": "2026-01-10T04:00:00+00:00",\n'
    '        "delivery_id": null\n      },\n      {\n        "id": "00000000-0000-'
    '0000-0000-000000014823",\n        "station_id": "00000000-0000-0000-0000-0000'
    '00013c68",\n        "timestamp": "2026-01-10T01:00:00+00:00",\n        "para'
    'meter": "discharge",\n        "value": 6.0,\n        "source": "rating_cur'
    've_derived",\n        "rating_curve_id": "00000000-0000-0000-0000-00000001443'
    '9",\n        "rating_curve_correction_version": "fixture-v1",\n        "qc_s'
    'tatus": "qc_passed",\n        "qc_flags": [\n          {\n            "rule_'
    'id": "range_check",\n            "rule_version": "fixture-v1",\n           '
    ' "status": "qc_passed",\n            "detail": null\n          }\n        ],'
    '\n        "qc_rule_version": "fixture-v1",\n        "created_at": "2026-01-'
    '10T04:00:00+00:00",\n        "delivery_id": null\n      },\n      {\n        "'
    'id": "00000000-0000-0000-0000-000000014824",\n        "station_id": "0000000'
    '0-0000-0000-0000-000000013c68",\n        "timestamp": "2026-01-10T02:00:00+00'
    ':00",\n        "parameter": "discharge",\n        "value": 8.0,\n        "'
    'source": "measured",\n        "rating_curve_id": null,\n        "rating_curv'
    'e_correction_version": null,\n        "qc_status": "qc_passed",\n        "qc'
    '_flags": [\n          {\n            "rule_id": "range_check",\n            '
    '"rule_version": "fixture-v1",\n            "status": "qc_passed",\n       '
    '     "detail": null\n          }\n        ],\n        "qc_rule_version": "fix'
    'ture-v1",\n        "created_at": "2026-01-10T04:00:00+00:00",\n        "deli'
    'very_id": null\n      },\n      {\n        "id": "00000000-0000-0000-0000-0000'
    '00014825",\n        "station_id": "00000000-0000-0000-0000-000000014050",\n  '
    '      "timestamp": "2026-01-10T00:00:00+00:00",\n        "parameter": "disc'
    'harge",\n        "value": 8.0,\n        "source": "measured",\n        "ra'
    'ting_curve_id": null,\n        "rating_curve_correction_version": null,\n     '
    '   "qc_status": "qc_passed",\n        "qc_flags": [\n          {\n          '
    '  "rule_id": "range_check",\n            "rule_version": "fixture-v1",\n  '
    '          "status": "qc_passed",\n            "detail": null\n          }\n '
    '       ],\n        "qc_rule_version": "fixture-v1",\n        "created_at": '
    '"2026-01-10T04:00:00+00:00",\n        "delivery_id": null\n      },\n      {\n'
    '        "id": "00000000-0000-0000-0000-000000014826",\n        "station_id":'
    ' "00000000-0000-0000-0000-000000014050",\n        "timestamp": "2026-01-10T01'
    ':00:00+00:00",\n        "parameter": "discharge",\n        "value": 12.0,\n'
    '        "source": "measured",\n        "rating_curve_id": null,\n        "r'
    'ating_curve_correction_version": null,\n        "qc_status": "qc_suspect",\n '
    '       "qc_flags": [\n          {\n            "rule_id": "range_check",\n  '
    '          "rule_version": "fixture-v1",\n            "status": "qc_suspect'
    '",\n            "detail": null\n          }\n        ],\n        "qc_rule_vers'
    'ion": "fixture-v1",\n        "created_at": "2026-01-10T04:00:00+00:00",\n  '
    '      "delivery_id": null\n      },\n      {\n        "id": "00000000-0000-00'
    '00-0000-000000014827",\n        "station_id": "00000000-0000-0000-0000-000000'
    '013c68",\n        "timestamp": "2026-01-09T23:30:00+00:00",\n        "parame'
    'ter": "discharge",\n        "value": 2.0,\n        "source": "measured",'
    '\n        "rating_curve_id": null,\n        "rating_curve_correction_version":'
    ' null,\n        "qc_status": "qc_passed",\n        "qc_flags": [\n          '
    '{\n            "rule_id": "range_check",\n            "rule_version": "fixt'
    'ure-v1",\n            "status": "qc_passed",\n            "detail": null\n '
    '         }\n        ],\n        "qc_rule_version": "fixture-v1",\n        "cr'
    'eated_at": "2026-01-10T04:00:00+00:00",\n        "delivery_id": null\n      }'
    ',\n      {\n        "id": "00000000-0000-0000-0000-000000014828",\n        "s'
    'tation_id": "00000000-0000-0000-0000-000000014050",\n        "timestamp": "2'
    '026-01-09T23:30:00+00:00",\n        "parameter": "discharge",\n        "valu'
    'e": 4.0,\n        "source": "measured",\n        "rating_curve_id": null,\n'
    '        "rating_curve_correction_version": null,\n        "qc_status": "qc_pa'
    'ssed",\n        "qc_flags": [\n          {\n            "rule_id": "range_ch'
    'eck",\n            "rule_version": "fixture-v1",\n            "status": "q'
    'c_passed",\n            "detail": null\n          }\n        ],\n        "qc_r'
    'ule_version": "fixture-v1",\n        "created_at": "2026-01-10T04:00:00+00:0'
    '0",\n        "delivery_id": null\n      },\n      {\n        "id": "00000000'
    '-0000-0000-0000-000000014829",\n        "station_id": "00000000-0000-0000-000'
    '0-000000014050",\n        "timestamp": "2026-01-10T02:00:00+00:00",\n        '
    '"parameter": "discharge",\n        "value": 16.0,\n        "source": "mea'
    'sured",\n        "rating_curve_id": null,\n        "rating_curve_correction_ve'
    'rsion": null,\n        "qc_status": "qc_passed",\n        "qc_flags": [\n  '
    '        {\n            "rule_id": "range_check",\n            "rule_version"'
    ': "fixture-v1",\n            "status": "qc_passed",\n            "detail":'
    ' null\n          }\n        ],\n        "qc_rule_version": "fixture-v1",\n    '
    '    "created_at": "2026-01-10T04:00:00+00:00",\n        "delivery_id": null'
    '\n      }\n    ],\n    "added_ids": [\n      "00000000-0000-0000-0000-000000014'
    '827",\n      "00000000-0000-0000-0000-000000014828",\n      "00000000-0000-000'
    '0-0000-000000014829"\n    ],\n    "existing_changes": [\n      {\n        "id'
    '": "00000000-0000-0000-0000-000000014821",\n        "field": "value",\n    '
    '    "before": 4.0,\n        "after": 12.0\n      }\n    ],\n    "expected_dom'
    'ain_public_observations": [\n      {\n        "id": "$generated_observation_0'
    '",\n        "station_id": "$generated_station",\n        "timestamp": "202'
    '6-01-09T23:30:00+00:00",\n        "parameter": "discharge",\n        "value'
    '": 2.0,\n        "source": "component_derived",\n        "rating_curve_id":'
    ' null,\n        "rating_curve_correction_version": null,\n        "qc_status":'
    ' "qc_passed",\n        "qc_flags": [\n          {\n            "rule_id": "'
    'upstream_propagated",\n            "rule_version": "component_derivation/v1",'
    '\n            "status": "qc_passed",\n            "detail": "{\\"component'
    '_station_id\\": \\"00000000-0000-0000-0000-000000013c68\\", \\"component_statu'
    's\\": \\"qc_passed\\", \\"weight\\": 0.5}"\n          },\n          {\n     '
    '       "rule_id": "upstream_propagated",\n            "rule_version": "comp'
    'onent_derivation/v1",\n            "status": "qc_passed",\n            "deta'
    'il": "{\\"component_station_id\\": \\"00000000-0000-0000-0000-000000014050\\'
    '", \\"component_status\\": \\"qc_passed\\", \\"weight\\": 0.25}"\n        '
    '  }\n        ],\n        "qc_rule_version": "component_derivation/v1",\n      '
    '  "created_at": "2026-01-10T04:00:00+00:00",\n        "delivery_id": null\n '
    '     },\n      {\n        "id": "$generated_observation_1",\n        "station'
    '_id": "$generated_station",\n        "timestamp": "2026-01-10T00:00:00+00:00'
    '",\n        "parameter": "discharge",\n        "value": 8.0,\n        "sou'
    'rce": "component_derived",\n        "rating_curve_id": null,\n        "ratin'
    'g_curve_correction_version": null,\n        "qc_status": "qc_passed",\n      '
    '  "qc_flags": [\n          {\n            "rule_id": "upstream_propagated",'
    '\n            "rule_version": "component_derivation/v1",\n            "status'
    '": "qc_passed",\n            "detail": "{\\"component_station_id\\": \\"0'
    '0000000-0000-0000-0000-000000013c68\\", \\"component_status\\": \\"qc_passed\\'
    '", \\"weight\\": 0.5}"\n          },\n          {\n            "rule_id": "'
    'upstream_propagated",\n            "rule_version": "component_derivation/v1",'
    '\n            "status": "qc_passed",\n            "detail": "{\\"component'
    '_station_id\\": \\"00000000-0000-0000-0000-000000014050\\", \\"component_statu'
    's\\": \\"qc_passed\\", \\"weight\\": 0.25}"\n          }\n        ],\n      '
    '  "qc_rule_version": "component_derivation/v1",\n        "created_at": "202'
    '6-01-10T04:00:00+00:00",\n        "delivery_id": null\n      },\n      {\n     '
    '   "id": "$generated_observation_2",\n        "station_id": "$generated_sta'
    'tion",\n        "timestamp": "2026-01-10T01:00:00+00:00",\n        "paramete'
    'r": "discharge",\n        "value": 6.0,\n        "source": "component_deri'
    'ved",\n        "rating_curve_id": null,\n        "rating_curve_correction_vers'
    'ion": null,\n        "qc_status": "qc_suspect",\n        "qc_flags": [\n   '
    '       {\n            "rule_id": "upstream_propagated",\n            "rule_ve'
    'rsion": "component_derivation/v1",\n            "status": "qc_passed",\n   '
    '         "detail": "{\\"component_station_id\\": \\"00000000-0000-0000-0000-'
    '000000013c68\\", \\"component_status\\": \\"qc_passed\\", \\"weight\\": 0.5'
    '}"\n          },\n          {\n            "rule_id": "upstream_propagated",'
    '\n            "rule_version": "component_derivation/v1",\n            "status'
    '": "qc_suspect",\n            "detail": "{\\"component_station_id\\": \\"'
    '00000000-0000-0000-0000-000000014050\\", \\"component_status\\": \\"qc_suspect'
    '\\", \\"weight\\": 0.25}"\n          }\n        ],\n        "qc_rule_version'
    '": "component_derivation/v1",\n        "created_at": "2026-01-10T04:00:00+00'
    ':00",\n        "delivery_id": null\n      },\n      {\n        "id": "$gener'
    'ated_observation_3",\n        "station_id": "$generated_station",\n        "'
    'timestamp": "2026-01-10T02:00:00+00:00",\n        "parameter": "discharge",'
    '\n        "value": 8.0,\n        "source": "component_derived",\n        "r'
    'ating_curve_id": null,\n        "rating_curve_correction_version": null,\n    '
    '    "qc_status": "qc_passed",\n        "qc_flags": [\n          {\n         '
    '   "rule_id": "upstream_propagated",\n            "rule_version": "componen'
    't_derivation/v1",\n            "status": "qc_passed",\n            "detail"'
    ': "{\\"component_station_id\\": \\"00000000-0000-0000-0000-000000013c68\\", '
    '\\"component_status\\": \\"qc_passed\\", \\"weight\\": 0.5}"\n          },'
    '\n          {\n            "rule_id": "upstream_propagated",\n            "ru'
    'le_version": "component_derivation/v1",\n            "status": "qc_passed",'
    '\n            "detail": "{\\"component_station_id\\": \\"00000000-0000-0000-'
    '0000-000000014050\\", \\"component_status\\": \\"qc_passed\\", \\"weight\\"'
    ': 0.25}"\n          }\n        ],\n        "qc_rule_version": "component_deriv'
    'ation/v1",\n        "created_at": "2026-01-10T04:00:00+00:00",\n        "del'
    'ivery_id": null\n      }\n    ],\n    "expected_submitted_store_values_and_raw_'
    'rows": [\n      {\n        "id": "$generated_observation_0",\n        "stati'
    'on_id": "$generated_station",\n        "timestamp": "2026-01-09T23:30:00+00:'
    '00",\n        "parameter": "discharge",\n        "value": 2.0,\n        "s'
    'ource": "component_derived",\n        "rating_curve_id": null,\n        "rat'
    'ing_curve_correction_version": null,\n        "qc_status": "qc_passed",\n    '
    '    "qc_flags": [\n          {\n            "rule_id": "upstream_propagated"'
    ',\n            "rule_version": "component_derivation/v1",\n            "statu'
    's": "qc_passed",\n            "detail": "{\\"component_station_id\\": \\"'
    '00000000-0000-0000-0000-000000013c68\\", \\"component_status\\": \\"qc_passed'
    '\\", \\"weight\\": 0.5}"\n          },\n          {\n            "rule_id": '
    '"upstream_propagated",\n            "rule_version": "component_derivation/v1'
    '",\n            "status": "qc_passed",\n            "detail": "{\\"compon'
    'ent_station_id\\": \\"00000000-0000-0000-0000-000000014050\\", \\"component_st'
    'atus\\": \\"qc_passed\\", \\"weight\\": 0.25}"\n          }\n        ],\n   '
    '     "qc_rule_version": "component_derivation/v1",\n        "created_at": "'
    '2026-01-10T04:00:00+00:00",\n        "delivery_id": null\n      },\n      {\n  '
    '      "id": "$generated_observation_1",\n        "station_id": "$generated_'
    'station",\n        "timestamp": "2026-01-10T00:00:00+00:00",\n        "param'
    'eter": "discharge",\n        "value": 8.0,\n        "source": "component_d'
    'erived",\n        "rating_curve_id": null,\n        "rating_curve_correction_v'
    'ersion": null,\n        "qc_status": "qc_passed",\n        "qc_flags": [\n '
    '         {\n            "rule_id": "upstream_propagated",\n            "rule_'
    'version": "component_derivation/v1",\n            "status": "qc_passed",\n '
    '           "detail": "{\\"component_station_id\\": \\"00000000-0000-0000-000'
    '0-000000013c68\\", \\"component_status\\": \\"qc_passed\\", \\"weight\\": 0'
    '.5}"\n          },\n          {\n            "rule_id": "upstream_propagated"'
    ',\n            "rule_version": "component_derivation/v1",\n            "statu'
    's": "qc_passed",\n            "detail": "{\\"component_station_id\\": \\"'
    '00000000-0000-0000-0000-000000014050\\", \\"component_status\\": \\"qc_passed'
    '\\", \\"weight\\": 0.25}"\n          }\n        ],\n        "qc_rule_version'
    '": "component_derivation/v1",\n        "created_at": "2026-01-10T04:00:00+00'
    ':00",\n        "delivery_id": null\n      },\n      {\n        "id": "$gener'
    'ated_observation_2",\n        "station_id": "$generated_station",\n        "'
    'timestamp": "2026-01-10T01:00:00+00:00",\n        "parameter": "discharge",'
    '\n        "value": 6.0,\n        "source": "component_derived",\n        "r'
    'ating_curve_id": null,\n        "rating_curve_correction_version": null,\n    '
    '    "qc_status": "qc_suspect",\n        "qc_flags": [\n          {\n        '
    '    "rule_id": "upstream_propagated",\n            "rule_version": "compone'
    'nt_derivation/v1",\n            "status": "qc_passed",\n            "detail'
    '": "{\\"component_station_id\\": \\"00000000-0000-0000-0000-000000013c68\\",'
    ' \\"component_status\\": \\"qc_passed\\", \\"weight\\": 0.5}"\n          },'
    '\n          {\n            "rule_id": "upstream_propagated",\n            "ru'
    'le_version": "component_derivation/v1",\n            "status": "qc_suspect"'
    ',\n            "detail": "{\\"component_station_id\\": \\"00000000-0000-0000'
    '-0000-000000014050\\", \\"component_status\\": \\"qc_suspect\\", \\"weight\\'
    '": 0.25}"\n          }\n        ],\n        "qc_rule_version": "component_der'
    'ivation/v1",\n        "created_at": "2026-01-10T04:00:00+00:00",\n        "d'
    'elivery_id": null\n      },\n      {\n        "id": "$generated_observation_3'
    '",\n        "station_id": "$generated_station",\n        "timestamp": "202'
    '6-01-10T02:00:00+00:00",\n        "parameter": "discharge",\n        "value'
    '": 8.0,\n        "source": "component_derived",\n        "rating_curve_id":'
    ' null,\n        "rating_curve_correction_version": null,\n        "qc_status":'
    ' "qc_passed",\n        "qc_flags": [\n          {\n            "rule_id": "'
    'upstream_propagated",\n            "rule_version": "component_derivation/v1",'
    '\n            "status": "qc_passed",\n            "detail": "{\\"component'
    '_station_id\\": \\"00000000-0000-0000-0000-000000013c68\\", \\"component_statu'
    's\\": \\"qc_passed\\", \\"weight\\": 0.5}"\n          },\n          {\n     '
    '       "rule_id": "upstream_propagated",\n            "rule_version": "comp'
    'onent_derivation/v1",\n            "status": "qc_passed",\n            "deta'
    'il": "{\\"component_station_id\\": \\"00000000-0000-0000-0000-000000014050\\'
    '", \\"component_status\\": \\"qc_passed\\", \\"weight\\": 0.25}"\n        '
    '  }\n        ],\n        "qc_rule_version": "component_derivation/v1",\n      '
    '  "created_at": "2026-01-10T04:00:00+00:00",\n        "delivery_id": null\n '
    '     }\n    ],\n    "expected_formula": [\n      {\n        "id": "$generated'
    '_formula_0",\n        "calculated_station_id": "$generated_station",\n       '
    ' "component_station_id": "00000000-0000-0000-0000-000000013c68",\n        "pa'
    'rameter": "discharge",\n        "weight": 0.5,\n        "effective_from": '
    '"2026-01-09T23:30:00+00:00",\n        "effective_to": null,\n        "created'
    '_at": "2026-01-10T04:00:00+00:00"\n      },\n      {\n        "id": "$genera'
    'ted_formula_1",\n        "calculated_station_id": "$generated_station",\n    '
    '    "component_station_id": "00000000-0000-0000-0000-000000014050",\n        '
    '"parameter": "discharge",\n        "weight": 0.25,\n        "effective_from'
    '": "2026-01-09T23:30:00+00:00",\n        "effective_to": null,\n        "cre'
    'ated_at": "2026-01-10T04:00:00+00:00"\n      }\n    ],\n    "expected_station'
    '": {\n      "location": {\n        "lon": 8.25,\n        "lat": 47.25,\n   '
    '     "altitude_masl": null\n      },\n      "station_kind": "river",\n      '
    '"basin_id": null,\n      "timezone": "UTC",\n      "regulation_type": null'
    ',\n      "forecast_targets": [\n        "discharge"\n      ],\n      "measure'
    'd_parameters": [\n        "discharge"\n      ],\n      "station_status": "on'
    'boarding",\n      "created_at": "2026-01-10T04:00:00+00:00",\n      "updated'
    '_at": "2026-01-10T04:00:00+00:00",\n      "network": "component-isolation",'
    '\n      "ownership": "own",\n      "wigos_id": null,\n      "gauging_status'
    '": "calculated",\n      "water_level_datum_masl": null,\n      "water_level_'
    'unit": null,\n      "tenant_id": "00000000-0000-0000-0000-000000000001",\n   '
    '   "id": "$generated_station",\n      "code": "component-result",\n      '
    '"name": "Component result"\n    },\n    "expected_outcome": {\n      "obser'
    'vations_derived": 4,\n      "observations_missing": 0,\n      "formula_configu'
    'red": true,\n      "created": true\n    },\n    "independent_math": [\n      '
    '"(-30m) (1/2)*2+(1/4)*4=2",\n      "(0m) (1/2)*12+(1/4)*8=8; +4 vs baseline",'
    '\n      "(60m) (1/2)*6+(1/4)*12=6, unchanged QC_SUSPECT",\n      "(120m) (1/2)'
    '*8+(1/4)*16=8; formerly MISSING"\n    ],\n    "comparison": "Extra ordinary re'
    "cords at protected timestamps cause earlier effective_from/new candidate and"
    " missing-component repair. Existing selected value also changes, preserving "
    "original nonvacuous numeric control. Every protected record remains identica"
    'l. No cross-test dependency."\n  },\n  "retained_value_control": {\n    "id":'
    ' "00000000-0000-0000-0000-000000014821",\n    "only_field": "value",\n    "'
    'before": 4.0,\n    "after": 12.0,\n    "before_output": 4.0,\n    "after_out'
    'put": 8.0,\n    "delta": "4",\n    "scope": "Retained existing-value contr'
    "ol, now accompanied by three added ordinary rows in changed_scenario; this o"
    'bject alone is not the full changed-case oracle.",\n    "output_timestamp": "'
    '2026-01-10T00:00:00+00:00"\n  }\n}\n'
)

_ORACLE_SHA256 = "8401bcd1debe25ead1e726adec0e7dd25d0b1ad2d21acbb99b4de6edaf674dac"
_ID_FIELDS = frozenset(
    {
        "id",
        "station_id",
        "tenant_id",
        "basin_id",
        "uploaded_by",
        "observation_id",
        "rating_curve_id",
        "feed_evidence_id",
        "reference_proof_id",
        "calculated_station_id",
        "component_station_id",
    }
)
_TIME_FIELDS = frozenset(
    {
        "timestamp",
        "created_at",
        "updated_at",
        "valid_from",
        "valid_to",
        "verified_at",
        "effective_from",
        "effective_to",
        "captured_at",
    }
)
_INPUT_TABLES = (
    "stations",
    "observations",
    "rating_curves",
    "rating_reference_proofs",
    "measurement_feed_evidence",
    "provisional_discharge_permissions",
    "provisional_discharges",
    "calculated_station_formulas",
)
_SIDE_TABLES = (
    "model_states",
    "alerts",
    "forecasts",
    "forecast_values",
    "rejected_forecasts",
    "skill_scores",
    "skill_diagrams",
    "skill_generations",
    "observation_versions",
    "clim_baselines",
    "flow_regime_configs",
    "model_artifacts",
    "model_assignments",
)


def literal_oracle() -> dict[str, Any]:
    assert hashlib.sha256(_ORACLE_TEXT.encode()).hexdigest() == _ORACLE_SHA256
    return cast("dict[str, Any]", json.loads(_ORACLE_TEXT))


def expected_row(
    literal: dict[str, Any], ids: dict[str, UUID] | None = None
) -> dict[str, Any]:
    """Parse only frozen expected UUID/time fields; never normalize actual values."""
    mapped = ids or {}
    result = copy.deepcopy(literal)
    for key, value in result.items():
        if value is not None and key in _ID_FIELDS:
            result[key] = mapped[value] if value.startswith("$") else UUID(value)
        elif value is not None and key in _TIME_FIELDS:
            result[key] = ensure_utc(datetime.fromisoformat(value))
    return result


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


def observation(
    literal: dict[str, Any], ids: dict[str, UUID] | None = None
) -> Observation:
    values = expected_row(literal, ids)
    values["source"] = ObservationSource(values["source"])
    values["qc_status"] = QcStatus(values["qc_status"])
    values["qc_flags"] = [_flag(flag) for flag in _records_field(literal, "qc_flags")]
    return Observation(**values)


def station(
    literal: dict[str, Any], ids: dict[str, UUID] | None = None
) -> StationConfig:
    values = expected_row(literal, ids)
    values["location"] = GeoCoord(**values["location"])
    values["station_kind"] = StationKind(values["station_kind"])
    values["ownership"] = StationOwnership(values["ownership"])
    values["gauging_status"] = GaugingStatus(values["gauging_status"])
    values["station_status"] = StationStatus(values["station_status"])
    values["forecast_targets"] = frozenset(values["forecast_targets"])
    values["measured_parameters"] = frozenset(values["measured_parameters"])
    return StationConfig(**values)


def _rows(conn: sa.Connection, name: str) -> list[dict[str, Any]]:
    table = db.metadata.tables[name]
    return [dict(row) for row in conn.execute(sa.select(table)).mappings()]


def _inventory(conn: sa.Connection, names: tuple[str, ...]) -> dict[str, list[str]]:
    # Names are fixed metadata relations, never external SQL identifiers.
    return {
        name: sorted(
            conn.execute(sa.text(f"SELECT row_to_json(t)::text FROM public.{name} t"))
            .scalars()
            .all()
        )
        for name in names
    }


def _same_rows(actual: list[dict[str, Any]], expected: list[dict[str, Any]]) -> None:
    assert len(actual) == len(expected)
    unmatched = list(actual)
    for row in expected:
        assert row in unmatched, (row, unmatched)
        unmatched.remove(row)
    assert not unmatched


def _assert_identity(conn: sa.Connection, user: str) -> None:
    assert conn.scalar(sa.text("SELECT current_user")) == user
    assert conn.scalar(sa.text("SELECT session_user")) == "test"
    assert conn.scalar(sa.text("SHOW transaction_isolation")) == "read committed"


def _seed(
    conn: sa.Connection,
    stores: ComponentStores,
    oracle: dict[str, Any],
    scenario: ComponentScenario,
) -> None:
    _assert_identity(conn, "test")
    for row in oracle["stations"]:
        stores.station.store_station(station(row))
    curves = [_curve(row) for row in _records_field(oracle, "curves")]
    for curve in curves:
        PgRatingCurveStore(conn).store_rating_curve(curve)
    source = (
        oracle["changed_scenario"] if scenario is ComponentScenario.CHANGED else oracle
    )
    stores.obs.store_observations(
        [observation(row) for row in source["ordinary_observations"]]
    )
    proofs = [_proof(row) for row in _records_field(oracle, "reference_proofs")]
    for proof in proofs:
        seed_reference(conn, db.rating_reference_proofs, proof)
    permit_fixture(conn, TenantId(UUID(oracle["tenant_id"])))
    if scenario is ComponentScenario.CLEAN:
        return
    levels = [observation(row) for row in oracle["protected_levels"]]
    stores.obs.store_observations(levels)
    feeds = [_feed(row) for row in _records_field(oracle, "feed_evidence")]
    for level, feed, frozen in zip(
        levels, feeds, oracle["provisional_rows"], strict=True
    ):
        seed_reference(conn, db.measurement_feed_evidence, feed)
        proof = next(p for p in proofs if p.station_id == level.station_id)
        result = convert_provisional_discharge(
            observation=level,
            curves=[c for c in curves if c.station_id == level.station_id],
            feed_evidence=feed,
            reference_proof=proof,
            at=ensure_utc(datetime.fromisoformat(oracle["clock"])),
        )
        expected = expected_row(frozen)
        assert {
            key: getattr(result, key) for key in expected if key != "captured_at"
        } == {key: value for key, value in expected.items() if key != "captured_at"}
        assert result.measurement == feed.measurement
        assert result.curve == proof.curve
        assert (
            PgProvisionalDischargeStore(conn).store_provisional_discharge(
                result, captured_at=expected["captured_at"]
            )
            == result.fingerprint
        )


def _delete_ordinary(conn: sa.Connection, oracle: dict[str, Any]) -> None:
    ids = [UUID(row["id"]) for row in oracle["ordinary_observations"]]
    parent_ids = {UUID(row["id"]) for row in oracle["protected_levels"]}
    assert len(ids) == len(set(ids)) == 6
    assert not set(ids) & parent_ids
    references = conn.execute(
        sa.text(
            "SELECT n.nspname, c.relname, a.attname "
            "FROM pg_constraint k JOIN pg_class c ON c.oid=k.conrelid "
            "JOIN pg_namespace n ON n.oid=c.relnamespace "
            "JOIN LATERAL unnest(k.conkey,k.confkey) AS pair(child,parent) ON true "
            "JOIN pg_attribute a ON a.attrelid=k.conrelid AND a.attnum=pair.child "
            "JOIN pg_attribute p ON p.attrelid=k.confrelid AND p.attnum=pair.parent "
            "WHERE k.contype='f' AND k.confrelid='public.observations'::regclass "
            "AND p.attname='id'"
        )
    ).all()
    assert references
    preparer = conn.dialect.identifier_preparer
    for schema, table, column in references:
        qualified = f"{preparer.quote_schema(schema)}.{preparer.quote(table)}"
        query = sa.text(
            f"SELECT count(*) FROM {qualified} WHERE {preparer.quote(column)} IN :ids"
        ).bindparams(sa.bindparam("ids", expanding=True))
        assert conn.scalar(query, {"ids": ids}) == 0, (schema, table, column)
    deleted = (
        conn.execute(
            sa.delete(db.observations)
            .where(db.observations.c.id.in_(ids))
            .returning(db.observations.c.id)
        )
        .scalars()
        .all()
    )
    assert len(deleted) == 6 and set(deleted) == set(ids)


def _assert_seed(
    conn: sa.Connection,
    stores: ComponentStores,
    oracle: dict[str, Any],
    scenario: ComponentScenario,
) -> None:
    source = (
        oracle["changed_scenario"] if scenario is ComponentScenario.CHANGED else oracle
    )
    ordinary: list[dict[str, object]] = (
        []
        if scenario is ComponentScenario.PROTECTED_ONLY
        else _records_field(source, "ordinary_observations")
    )
    levels: list[dict[str, object]] = (
        []
        if scenario is ComponentScenario.CLEAN
        else _records_field(oracle, "protected_levels")
    )
    _same_rows(
        _rows(conn, "observations"),
        [expected_row(row) for row in ordinary + levels],
    )
    for literal in oracle["stations"]:
        sid = StationId(UUID(literal["id"]))
        assert stores.station.fetch_station(sid) == station(literal)
        actual = stores.obs.fetch_observations(
            sid,
            "discharge",
            ensure_utc(datetime.fromisoformat(oracle["window_start"])),
            ensure_utc(datetime.fromisoformat(oracle["window_end"])),
        )
        expected_observations = [
            observation(row) for row in ordinary if row["station_id"] == literal["id"]
        ]
        assert len(actual) == len(expected_observations)
        assert {row.id: row for row in actual} == {
            row.id: row for row in expected_observations
        }
    _same_rows(
        _rows(conn, "rating_curves"),
        [expected_row(row) for row in oracle["curves"]],
    )
    _same_rows(
        _rows(conn, "rating_reference_proofs"),
        [expected_row(row) for row in oracle["reference_proof_rows"]],
    )
    _same_rows(
        _rows(conn, "provisional_discharge_permissions"),
        [expected_row(oracle["permission"])],
    )
    for table, key in [
        ("measurement_feed_evidence", "feed_evidence_rows"),
        ("provisional_discharges", "provisional_rows"),
    ]:
        expected = (
            []
            if scenario is ComponentScenario.CLEAN
            else [expected_row(row) for row in oracle[key]]
        )
        _same_rows(_rows(conn, table), expected)
    assert not _rows(conn, "calculated_station_formulas")
    assert (
        stores.station.fetch_station_by_code("component-result", "component-isolation")
        is None
    )


@dataclass(frozen=True, kw_only=True, slots=True)
class WriteAttempt:
    relation: str
    operation: str
    sql: str
    parameters: tuple[dict[str, Any], ...]


class WriteObserver:
    def __init__(self, connection: sa.Connection) -> None:
        self.connection = connection
        self.attempts: list[WriteAttempt] = []
        self.same_connection = True

    def before_cursor_execute(
        self,
        conn: sa.Connection,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        executemany: bool,
    ) -> None:
        self.same_connection = self.same_connection and conn is self.connection
        compiled = context.compiled
        query = compiled.statement if compiled is not None else None
        operation = next(
            (
                name
                for name in ("insert", "update", "delete")
                if getattr(query, f"is_{name}", False)
            ),
            None,
        )
        if query is None or operation is None:
            if re.match(r"\s*(INSERT|UPDATE|DELETE)\b", statement, re.IGNORECASE):
                self.attempts.append(
                    WriteAttempt(
                        relation="uncompiled",
                        operation="unknown",
                        sql=statement,
                        parameters=(),
                    )
                )
            return
        self.attempts.append(
            WriteAttempt(
                relation=query.table.name,
                operation=operation,
                sql=statement,
                parameters=tuple(
                    copy.deepcopy(dict(row)) for row in context.compiled_parameters
                ),
            )
        )


def _station_values(literal: dict[str, Any], ids: dict[str, UUID]) -> dict[str, Any]:
    expected = expected_row(literal, ids)
    location = expected.pop("location")
    expected["altitude_masl"] = location["altitude_masl"]
    return expected


def _station_sets(actual: dict[str, Any], expected: dict[str, Any]) -> None:
    for key in ("forecast_targets", "measured_parameters"):
        assert len(actual[key]) == len(set(actual[key])) == len(expected[key])
        assert frozenset(actual[key]) == frozenset(expected[key])
    assert {
        k: v
        for k, v in actual.items()
        if k not in ("forecast_targets", "measured_parameters")
    } == {
        k: v
        for k, v in expected.items()
        if k not in ("forecast_targets", "measured_parameters")
    }


def _generated_ids(
    outcome: CalculatedOnboardingOutcome,
    formulas: list[dict[str, Any]],
    outputs: list[dict[str, Any]],
    expected: dict[str, Any],
    oracle: dict[str, Any],
) -> dict[str, UUID]:
    ids = {"$generated_station": UUID(str(outcome.station.id))}
    assert len(formulas) == 2
    by_component = {str(row["component_station_id"]): row for row in formulas}
    assert len(by_component) == 2
    for literal in expected["expected_formula"]:
        row = by_component[literal["component_station_id"]]
        assert row["calculated_station_id"] == outcome.station.id
        assert row["parameter"] == literal["parameter"]
        assert row["effective_from"] == datetime.fromisoformat(
            literal["effective_from"]
        )
        ids[literal["id"]] = row["id"]
    by_key = {
        (row["timestamp"], row["parameter"], row["source"]): row for row in outputs
    }
    assert (
        len(by_key)
        == len(outputs)
        == len(expected["expected_domain_public_observations"])
    )
    for literal in expected["expected_domain_public_observations"]:
        key = (
            datetime.fromisoformat(literal["timestamp"]),
            literal["parameter"],
            literal["source"],
        )
        row = by_key[key]
        assert row["station_id"] == outcome.station.id
        ids[literal["id"]] = row["id"]
    fixed = {
        UUID(row["id"])
        for key in (
            "stations",
            "curves",
            "ordinary_observations",
            "protected_levels",
            "reference_proofs",
            "feed_evidence",
        )
        for row in oracle[key]
    } | {UUID(row["id"]) for row in oracle["changed_scenario"]["ordinary_observations"]}
    assert len(ids) == len(set(ids.values()))
    assert all(isinstance(value, UUID) and value.version == 4 for value in ids.values())
    assert not set(ids.values()) & fixed
    return ids


def _submitted(
    observer: WriteObserver,
    expected: dict[str, Any],
    ids: dict[str, UUID],
) -> None:
    assert observer.same_connection
    assert {a.relation for a in observer.attempts} == {
        "stations",
        "calculated_station_formulas",
        "observations",
    }
    assert all(a.operation == "insert" for a in observer.attempts)
    submitted = {
        name: [
            row for a in observer.attempts if a.relation == name for row in a.parameters
        ]
        for name in ("stations", "calculated_station_formulas", "observations")
    }
    assert len(submitted["stations"]) == 1
    submitted_station = copy.deepcopy(submitted["stations"][0])
    assert (
        submitted_station.pop("ST_MakePoint_1")
        == (expected["expected_station"]["location"]["lon"])
    )
    assert (
        submitted_station.pop("ST_MakePoint_2")
        == (expected["expected_station"]["location"]["lat"])
    )
    assert submitted_station.pop("ST_SetSRID_1") == 4326
    _station_sets(submitted_station, _station_values(expected["expected_station"], ids))
    _same_rows(
        submitted["calculated_station_formulas"],
        [expected_row(row, ids) for row in expected["expected_formula"]],
    )
    # Fresh target has no conflict rows. Retain every compiled bind in evidence;
    # compare INSERT fields, not redundant ON CONFLICT update-bind naming/order.
    columns = set(db.observations.c.keys())
    records = [
        {key: value for key, value in row.items() if key in columns}
        for row in submitted["observations"]
    ]
    _same_rows(
        records,
        [
            expected_row(row, ids)
            for row in expected["expected_submitted_store_values_and_raw_rows"]
        ],
    )


def _check_success(
    conn: sa.Connection,
    stores: ComponentStores,
    outcome: CalculatedOnboardingOutcome,
    oracle: dict[str, Any],
    scenario: ComponentScenario,
    observer: WriteObserver,
) -> dict[str, Any]:
    expected = (
        oracle["changed_scenario"] if scenario is ComponentScenario.CHANGED else oracle
    )
    formulas = _rows(conn, "calculated_station_formulas")
    outputs = [
        row
        for row in _rows(conn, "observations")
        if row["station_id"] == outcome.station.id
    ]
    ids = _generated_ids(outcome, formulas, outputs, expected, oracle)
    assert {
        name: getattr(outcome, name) for name in expected["expected_outcome"]
    } == expected["expected_outcome"]
    assert outcome.station == station(expected["expected_station"], ids)
    assert stores.station.fetch_station(outcome.station.id) == outcome.station
    public_formula = stores.formula.fetch_current_formula(
        outcome.station.id, "discharge"
    )
    _same_rows(
        [asdict(row) for row in public_formula],
        [expected_row(row, ids) for row in expected["expected_formula"]],
    )
    _same_rows(
        formulas, [expected_row(row, ids) for row in expected["expected_formula"]]
    )
    _same_rows(
        outputs,
        [
            expected_row(row, ids)
            for row in expected["expected_submitted_store_values_and_raw_rows"]
        ],
    )
    public = stores.obs.fetch_observations(
        outcome.station.id,
        "discharge",
        ensure_utc(datetime.fromisoformat(oracle["window_start"])),
        ensure_utc(datetime.fromisoformat(oracle["window_end"])),
    )
    _same_rows(
        [asdict(row) for row in public],
        [
            asdict(observation(row, ids))
            for row in expected["expected_domain_public_observations"]
        ],
    )
    raw_station = dict(
        conn.execute(
            sa.select(
                db.stations,
                sa.func.ST_X(db.stations.c.location).label("lon"),
                sa.func.ST_Y(db.stations.c.location).label("lat"),
                sa.func.ST_SRID(db.stations.c.location).label("srid"),
                sa.func.ST_GeometryType(db.stations.c.location).label("geometry_type"),
            ).where(db.stations.c.id == outcome.station.id)
        )
        .mappings()
        .one()
    )
    assert raw_station.pop("lon") == expected["expected_station"]["location"]["lon"]
    assert raw_station.pop("lat") == expected["expected_station"]["location"]["lat"]
    assert raw_station.pop("srid") == 4326
    assert raw_station.pop("geometry_type") == "ST_Point"
    raw_station.pop("location")
    _station_sets(raw_station, _station_values(expected["expected_station"], ids))
    _submitted(observer, expected, ids)
    reverse = {value: key for key, value in ids.items()}

    def remap(row: dict[str, Any]) -> dict[str, Any]:
        return {
            key: reverse.get(value, value) if key in _ID_FIELDS else value
            for key, value in row.items()
        }

    return {
        "station": remap(asdict(outcome.station)),
        "formulas": [
            remap(row)
            for row in sorted(formulas, key=lambda row: row["component_station_id"])
        ],
        "observations": [
            remap(row) for row in sorted(outputs, key=lambda row: row["timestamp"])
        ],
    }


def _evidence_value(value: object) -> object:
    if isinstance(value, UUID):
        return {"uuid": str(value)}
    if isinstance(value, datetime):
        return {"datetime": value.isoformat()}
    if isinstance(value, Enum):
        return value.value
    if _is_frozenset(value):
        members: frozenset[object] = value
        return {
            "frozenset": sorted(_text(member, "frozenset member") for member in members)
        }
    raise TypeError(f"unsupported component evidence value: {type(value).__name__}")


def _record_evidence(
    scenario: ComponentScenario,
    flags: dict[str, Any],
    observer: WriteObserver | None,
    evidence: dict[str, Any],
    evidence_dir: Path,
) -> None:
    root = Path(os.environ.get("SKILL_PERSISTENCE_EVIDENCE_DIR", str(evidence_dir)))
    payload = {
        **evidence,
        "flags": flags,
        "write_attempts": (
            [asdict(row) for row in observer.attempts] if observer else []
        ),
        "boundary": (
            "compiled parameters, not driver wire; session cleanup is separate"
        ),
    }
    # The gate supplies a fresh, external evidence directory; never overwrite receipts.
    with (root / f"component-{scenario.value}.json").open("x") as output:
        output.write(
            json.dumps(payload, default=_evidence_value, allow_nan=False, indent=2)
        )
        output.write("\n")


def run_component_case(
    engine: sa.Engine,
    factory: Callable[[sa.Connection], ComponentStores],
    scenario: ComponentScenario,
    record_property: Callable[[str, object], None],
    *,
    evidence_dir: Path,
) -> dict[str, Any] | None:
    oracle = literal_oracle()
    flags: dict[str, Any] = {
        "calls_started": 0,
        "calls_completed": 0,
        "expected_refusal": False,
        "observer_removed": False,
        "role_reset": False,
        "rolled_back": False,
    }
    observer: WriteObserver | None = None
    evidence: dict[str, Any] = {"scenario": scenario.value}
    result: dict[str, Any] | None = None
    with engine.connect() as conn:
        transaction = conn.begin()
        try:
            stores = factory(conn)
            baseline = _inventory(conn, _SIDE_TABLES)
            _seed(conn, stores, oracle, scenario)
            if scenario is ComponentScenario.PROTECTED_ONLY:
                _delete_ordinary(conn, oracle)
            _assert_seed(conn, stores, oracle, scenario)
            before = _inventory(conn, _INPUT_TABLES)
            evidence["inputs_before"] = before
            evidence["side_effects_before"] = baseline
            assert _inventory(conn, _SIDE_TABLES) == baseline
            conn.execute(sa.text("SET LOCAL ROLE sapphire_worker"))
            _assert_identity(conn, "sapphire_worker")
            stores = factory(conn)
            with (
                pytest.raises(DBAPIError, match="permission denied"),
                conn.begin_nested(),
            ):
                conn.execute(sa.select(db.provisional_discharges))
            spec_values = copy.deepcopy(oracle["spec"])
            spec_values["components"] = tuple(
                ComponentSpec(**row) for row in spec_values["components"]
            )
            spec = CalculatedStationSpec(**spec_values)
            clock = ensure_utc(datetime.fromisoformat(oracle["clock"]))
            observer = WriteObserver(conn)
            listener = observer.before_cursor_execute
            event.listen(conn, "before_cursor_execute", listener)
            outcome = None
            try:
                _assert_identity(conn, "sapphire_worker")
                flags["worker_identity_before"] = True
                flags["calls_started"] = 1
                if scenario is ComponentScenario.PROTECTED_ONLY:
                    with pytest.raises(
                        ConfigurationError, match="no component observations found"
                    ):
                        onboard_calculated_station(
                            spec,
                            None,
                            stores.station,
                            stores.obs,
                            stores.formula,
                            lambda: clock,
                            ensure_utc(datetime.fromisoformat(oracle["window_start"])),
                            ensure_utc(datetime.fromisoformat(oracle["window_end"])),
                            tenant_id=TenantId(UUID(oracle["tenant_id"])),
                        )
                    flags["expected_refusal"] = True
                else:
                    outcome = onboard_calculated_station(
                        spec,
                        None,
                        stores.station,
                        stores.obs,
                        stores.formula,
                        lambda: clock,
                        ensure_utc(datetime.fromisoformat(oracle["window_start"])),
                        ensure_utc(datetime.fromisoformat(oracle["window_end"])),
                        tenant_id=TenantId(UUID(oracle["tenant_id"])),
                    )
                    flags["calls_completed"] = 1
                _assert_identity(conn, "sapphire_worker")
                flags["worker_identity_after"] = True
            finally:
                pending = sys.exception()
                try:
                    event.remove(conn, "before_cursor_execute", listener)
                    flags["observer_removed"] = not event.contains(
                        conn, "before_cursor_execute", listener
                    )
                except Exception as cleanup_error:
                    if pending is None:
                        raise
                    pending.add_note(
                        f"component observer cleanup failed: {cleanup_error}"
                    )
            assert flags["observer_removed"] and observer.same_connection
            conn.execute(sa.text("RESET ROLE"))
            flags["role_reset"] = True
            _assert_identity(conn, "test")
            if outcome is None:
                assert scenario is ComponentScenario.PROTECTED_ONLY
                assert observer.attempts == []
                assert _inventory(conn, _INPUT_TABLES) == before
            else:
                result = _check_success(
                    conn, stores, outcome, oracle, scenario, observer
                )
                after = _inventory(conn, _INPUT_TABLES)
                evidence["inputs_after"] = after
                evidence["mapped_result"] = result
                for name in _INPUT_TABLES:
                    if name not in (
                        "stations",
                        "observations",
                        "calculated_station_formulas",
                    ):
                        assert after[name] == before[name]
                    else:
                        assert all(row in after[name] for row in before[name])
                expected_added = {
                    "stations": 1,
                    "calculated_station_formulas": 2,
                    "observations": 4 if scenario is ComponentScenario.CHANGED else 3,
                }
                for name, added in expected_added.items():
                    assert len(after[name]) == len(before[name]) + added
            evidence["inputs_after"] = _inventory(conn, _INPUT_TABLES)
            evidence["side_effects_after"] = _inventory(conn, _SIDE_TABLES)
            assert evidence["side_effects_after"] == baseline
        finally:
            pending = sys.exception()
            cleanup_errors: list[Exception] = []
            try:
                # Rollback restores SET LOCAL even after an aborted statement.
                transaction.rollback()
                flags["rolled_back"] = not conn.in_transaction()
                conn.execute(sa.text("RESET ROLE"))
                flags["role_reset"] = (
                    conn.scalar(sa.text("SELECT current_user")) == "test"
                )
            except Exception as cleanup_error:
                cleanup_errors.append(cleanup_error)
            finally:
                try:
                    conn.rollback()
                except Exception as cleanup_error:
                    cleanup_errors.append(cleanup_error)
            if observer is not None:
                flags["same_connection"] = observer.same_connection
                flags["write_statements"] = len(observer.attempts)
                flags["submitted_rows"] = sum(
                    len(a.parameters) for a in observer.attempts
                )
            try:
                _record_evidence(scenario, flags, observer, evidence, evidence_dir)
                record_property(
                    f"component_{scenario.value}", json.dumps(flags, sort_keys=True)
                )
            except Exception as cleanup_error:
                cleanup_errors.append(cleanup_error)
            if cleanup_errors:
                if pending is None:
                    raise RuntimeError(
                        "component cleanup/evidence failed"
                    ) from cleanup_errors[0]
                for cleanup_error in cleanup_errors:
                    pending.add_note(
                        f"component cleanup/evidence failed: {cleanup_error}"
                    )
    assert flags["rolled_back"] and flags["role_reset"]
    return result
