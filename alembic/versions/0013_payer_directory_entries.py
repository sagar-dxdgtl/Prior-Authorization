"""payer_directory_entries: parsed PDF provider-directory rows (e.g. Align Senior Care)

Stores provider-location rows parsed from a payer's monthly PDF directory (name + location,
no NPI). Global reference data (tenant_id NULL) like payers — we match our own providers
(name + state + zip) against it rather than resolving the directory to NPIs.

RLS mirrors payers: global rows (tenant_id IS NULL) are readable by every tenant's app role.

Revision ID: 0013_payer_directory_entries
Revises: 0012_scan_public_fhir
Create Date: 2026-06-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision: str = "0013_payer_directory_entries"
down_revision: str | None = "0012_scan_public_fhir"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "payer_directory_entries",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=True, index=True),
        sa.Column("payer_key", sa.String(120), nullable=False, index=True),
        sa.Column("last_name", sa.String(120), nullable=False, index=True),
        sa.Column("first_name", sa.String(120), nullable=False, server_default=""),
        sa.Column("full_name", sa.String(240), nullable=False),
        sa.Column("specialty", sa.String(120), nullable=True),
        sa.Column("address", sa.String(240), nullable=True),
        sa.Column("city", sa.String(120), nullable=True),
        sa.Column("state", sa.String(2), nullable=True, index=True),
        sa.Column("zip", sa.String(10), nullable=True),
        sa.Column("accepting_new", sa.Boolean(), nullable=True),
        sa.Column("source_version", sa.String(40), nullable=True),
        sa.Column("loaded_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    # the hot lookup path: payer + surname (+ state)
    op.create_index(
        "ix_pde_lookup", "payer_directory_entries", ["payer_key", "last_name", "state"]
    )
    # RLS: global rows readable by every tenant's app role (same policy shape as payers)
    op.execute("ALTER TABLE payer_directory_entries ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE payer_directory_entries FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY payer_directory_entries_isolation ON payer_directory_entries USING "
        "(tenant_id IS NULL OR tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
    )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON payer_directory_entries TO preauth_app")

    # Flip the PDF-only plans to pdf-directory so the engine routes them to DbDirectoryAdapter
    # (a fresh DB seeds this from roster_seed; an already-migrated DB is corrected here).
    op.execute(
        "UPDATE payers SET directory_access = 'pdf-directory' WHERE tenant_id IS NULL "
        "AND label IN ('Align Senior Health Plan', 'EternalHealth')"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE payers SET directory_access = 'needs-authorized-api' WHERE tenant_id IS NULL "
        "AND label IN ('Align Senior Health Plan', 'EternalHealth')"
    )
    op.execute("DROP POLICY IF EXISTS payer_directory_entries_isolation ON payer_directory_entries")
    op.drop_index("ix_pde_lookup", table_name="payer_directory_entries")
    op.drop_table("payer_directory_entries")
