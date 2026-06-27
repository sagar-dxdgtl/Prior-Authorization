"""payer source matrix: FHIR/TiC/directory catalogue columns

Adds four nullable text columns to ``payers`` (fhir_base_url, tic_url, directory_url,
directory_access) and back-fills every global roster row from
``network_probe.payers.roster_seed.payer_rows`` — the single source of truth. The same
UPDATE also re-syncs stedi_payer_id / enrollment_status / network_indicator_supported,
because the multi-source expansion confirmed new Stedi ids for several payers (a freshly
seeded DB gets these straight from 0002; an already-seeded DB is corrected here).

Revision ID: 0007_payer_sources
Revises: 0006_usage_counters
Create Date: 2026-06-28

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy import text

from alembic import op
from network_probe.payers.roster_seed import payer_rows

revision: str = "0007_payer_sources"
down_revision: str | None = "0006_usage_counters"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UPDATE = text(
    "UPDATE payers SET "
    "fhir_base_url=:fhir_base_url, tic_url=:tic_url, directory_url=:directory_url, "
    "directory_access=:directory_access, stedi_payer_id=:stedi_payer_id, "
    "enrollment_status=:enrollment_status, network_indicator_supported=:network_indicator_supported "
    "WHERE tenant_id IS NULL AND key=:key"
)


def upgrade() -> None:
    op.add_column("payers", sa.Column("fhir_base_url", sa.String(), nullable=True))
    op.add_column("payers", sa.Column("tic_url", sa.String(), nullable=True))
    op.add_column("payers", sa.Column("directory_url", sa.String(), nullable=True))
    op.add_column("payers", sa.Column("directory_access", sa.String(length=40), nullable=True))

    conn = op.get_bind()
    for r in payer_rows():
        conn.execute(_UPDATE, r)


def downgrade() -> None:
    op.drop_column("payers", "directory_access")
    op.drop_column("payers", "directory_url")
    op.drop_column("payers", "tic_url")
    op.drop_column("payers", "fhir_base_url")
