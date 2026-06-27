"""usage counters: per-tenant daily/monthly request quota (RLS isolated)

Revision ID: 0006_usage_counters
Revises: 0005_review_workflow
Create Date: 2026-06-28

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006_usage_counters"
down_revision: str | None = "0005_review_workflow"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "usage_counters",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("period_type", sa.String(length=8), nullable=False),
        sa.Column("period_key", sa.String(length=16), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "period_type", "period_key", name="uq_usage_tenant_period"),
    )
    op.create_index(op.f("ix_usage_counters_tenant_id"), "usage_counters", ["tenant_id"], unique=False)

    _post(op)


def _post(op):
    # Enable AND force RLS so even the table owner is subject to the policy (mirrors init schema).
    op.execute("ALTER TABLE usage_counters ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE usage_counters FORCE ROW LEVEL SECURITY")
    # Strict tenant isolation. tenant_id is also part of the unique key, so an ON CONFLICT upsert can
    # only ever collide with a row of the *current* tenant (which is always visible under this policy).
    op.execute(
        "CREATE POLICY usage_counters_isolation ON usage_counters "
        "USING (tenant_id = current_setting('app.tenant_id', true)::uuid)"
    )
    # Least privilege: full DML for the app role on the tenant-isolated counter table.
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON usage_counters TO preauth_app")


def downgrade() -> None:
    op.drop_index(op.f("ix_usage_counters_tenant_id"), table_name="usage_counters")
    op.drop_table("usage_counters")
