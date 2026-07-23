"""basin_static_packages canonical fingerprint (Plan 120 Phase 2 fixer round)

Revision ID: 0040
Revises: 0039
Create Date: 2026-07-23

Adds an additive nullable `basin_static_packages.fingerprint` column — a
deterministic canonical fingerprint of the validated manifest metadata
(network, contract_version, extractor name/version, source_datasets,
climatology_window, declared manifest file set) PLUS the computed payload
checksums (`types/basin_package.py::compute_package_fingerprint`).

The importer persists this fingerprint with the package provenance and, on a
re-import of the same `package_id`, compares the stored fingerprint against the
freshly-computed one: identical → idempotent no-op; any difference (including a
manifest-only mutation with unchanged payload checksums) → immutability
violation reject (contract §11, `04:676`). Additive; no existing column is
redefined.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0040"
down_revision: str | None = "0039"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "basin_static_packages",
        sa.Column("fingerprint", sa.Text, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("basin_static_packages", "fingerprint")
