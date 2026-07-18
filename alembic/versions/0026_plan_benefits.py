"""plan_benefits: public CMS Medicare-Advantage PBP benefit-design (plan type + MOOP) by contract year

One global-readable table (tenant_id NULL rows visible to all, like `payers`) holding the OON *tier*
signal for MA plans — plan type (HMO/HMO-POS/PPO/PFFS), SNP flag, and in-network / combined / OON
MOOP — keyed by contract (H/R/E number) + PBP id + segment. Benefit design only; no provider data.
Fed by the CMS PBP Benefits ingest (`pbp_ingest` → `PlanBenefitStore.upsert`), read by the plan-type
resolver to fill a silent 271's OON tier and to enrich the evidence panel.

Idempotent + additive: CREATE TABLE/INDEX IF NOT EXISTS, policy dropped-then-created, grants
re-applied. No data seeded here — plans arrive via ingest.

Revision ID: 0026_plan_benefits
Revises: 0025_provider_network_facts
Create Date: 2026-07-18
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0026_plan_benefits"
down_revision: str | None = "0025_provider_network_facts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS plan_benefits (
            id                  UUID PRIMARY KEY,
            tenant_id           UUID REFERENCES tenants(id),
            contract_number     VARCHAR(10) NOT NULL,
            pbp_id              VARCHAR(3) NOT NULL,
            segment_id          VARCHAR(3) NOT NULL DEFAULT '0',
            year                INTEGER NOT NULL,
            plan_type           VARCHAR(12) NOT NULL,
            plan_type_code      VARCHAR(4),
            plan_name           VARCHAR(200),
            org_marketing_name  VARCHAR(200),
            snp_type_code       VARCHAR(2),
            dsnp                BOOLEAN NOT NULL DEFAULT FALSE,
            network_flag        VARCHAR(2),
            inn_moop            VARCHAR(16),
            comb_moop_yn        VARCHAR(2),
            comb_moop           VARCHAR(16),
            oon_moop_yn         VARCHAR(2),
            oon_moop            VARCHAR(16),
            retrieved_at        TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_planben_identity ON plan_benefits "
        "(tenant_id, contract_number, pbp_id, segment_id, year)"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_planben_contract ON plan_benefits (contract_number)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_planben_year ON plan_benefits (year)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_planben_name ON plan_benefits (plan_name)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_planben_tenant ON plan_benefits (tenant_id)")

    # RLS: global rows (tenant_id NULL) visible to all; tenant rows isolated — mirrors `payers`.
    op.execute("ALTER TABLE plan_benefits ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE plan_benefits FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS plan_benefits_isolation ON plan_benefits")
    op.execute(
        "CREATE POLICY plan_benefits_isolation ON plan_benefits USING "
        "(tenant_id IS NULL OR tenant_id = current_setting('app.tenant_id', true)::uuid)"
    )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON plan_benefits TO preauth_app")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS plan_benefits CASCADE")
