"""provider_network_facts: durable store for credentialing / TiC / directory NPI+TIN network facts

Adds one global-readable table (like `payers`: tenant_id NULL rows visible to all, tenant rows
layered on top) so provider-network facts pulled from the clinic's credentialing export and from
TiC MRF ingests (e.g. the Oscar FL pull) are persisted and reusable instead of thrown away. The
resolver and the group-contracted (physician-OON vs payer-OON) check read this; ingest writes it.

Idempotent + additive: CREATE TABLE/INDEX IF NOT EXISTS, policy dropped-then-created, grants
re-applied. No data seeded here — facts arrive via ingest.

Revision ID: 0025_provider_network_facts
Revises: 0024_validated_eligibility_ids
Create Date: 2026-07-18
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0025_provider_network_facts"
down_revision: str | None = "0024_validated_eligibility_ids"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS provider_network_facts (
            id              UUID PRIMARY KEY,
            tenant_id       UUID REFERENCES tenants(id),
            payer_key       VARCHAR(120) NOT NULL,
            npi             VARCHAR(10),
            tin             VARCHAR(20) NOT NULL,
            in_network      BOOLEAN NOT NULL,
            source          VARCHAR(20) NOT NULL,
            plan            VARCHAR(160),
            network_name    VARCHAR(160),
            effective_date  VARCHAR(40),
            retrieved_at    TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_pnf_identity ON provider_network_facts "
        "(tenant_id, payer_key, npi, tin, source)"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_pnf_payer_tin ON provider_network_facts (payer_key, tin)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_pnf_payer_npi_tin ON provider_network_facts (payer_key, npi, tin)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_pnf_tenant ON provider_network_facts (tenant_id)")

    # RLS: global rows (tenant_id NULL) visible to all; tenant rows isolated — mirrors `payers`.
    op.execute("ALTER TABLE provider_network_facts ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE provider_network_facts FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS provider_network_facts_isolation ON provider_network_facts")
    op.execute(
        "CREATE POLICY provider_network_facts_isolation ON provider_network_facts USING "
        "(tenant_id IS NULL OR tenant_id = current_setting('app.tenant_id', true)::uuid)"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON provider_network_facts TO preauth_app"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS provider_network_facts CASCADE")
