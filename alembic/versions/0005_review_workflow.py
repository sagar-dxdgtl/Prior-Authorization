"""review workflow: tenant-scoped review_cases + review_notes (RLS isolated)

Revision ID: 0005_review_workflow
Revises: 0004_drop_actor_fk
Create Date: 2026-06-28

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005_review_workflow"
down_revision: str | None = "0004_drop_actor_fk"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "review_cases",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("eligibility_check_id", sa.UUID(), nullable=True),
        sa.Column("payer_key", sa.String(length=120), nullable=False),
        sa.Column("npi", sa.String(length=10), nullable=True),
        sa.Column("member_id_hash", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("assignee_id", sa.UUID(), nullable=True),
        sa.Column("resolution", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_review_cases_tenant_id"), "review_cases", ["tenant_id"], unique=False)
    op.create_index(op.f("ix_review_cases_member_id_hash"), "review_cases", ["member_id_hash"], unique=False)
    op.create_index(op.f("ix_review_cases_status"), "review_cases", ["status"], unique=False)
    op.create_index(op.f("ix_review_cases_created_at"), "review_cases", ["created_at"], unique=False)

    op.create_table(
        "review_notes",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("case_id", sa.UUID(), nullable=False),
        sa.Column("author_id", sa.UUID(), nullable=False),
        sa.Column("text", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["case_id"], ["review_cases.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_review_notes_tenant_id"), "review_notes", ["tenant_id"], unique=False)
    op.create_index(op.f("ix_review_notes_case_id"), "review_notes", ["case_id"], unique=False)
    op.create_index(op.f("ix_review_notes_created_at"), "review_notes", ["created_at"], unique=False)

    _post(op)


def _post(op):
    # Enable AND force RLS so even the table owner is subject to policies (mirrors init schema).
    for t in ("review_cases", "review_notes"):
        op.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {t}_isolation ON {t} USING (tenant_id = current_setting('app.tenant_id', true)::uuid)"
        )

    # Least privilege: full DML for the app role on the new tenant-isolated tables.
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON review_cases, review_notes TO preauth_app")


def downgrade() -> None:
    op.drop_index(op.f("ix_review_notes_created_at"), table_name="review_notes")
    op.drop_index(op.f("ix_review_notes_case_id"), table_name="review_notes")
    op.drop_index(op.f("ix_review_notes_tenant_id"), table_name="review_notes")
    op.drop_table("review_notes")
    op.drop_index(op.f("ix_review_cases_created_at"), table_name="review_cases")
    op.drop_index(op.f("ix_review_cases_status"), table_name="review_cases")
    op.drop_index(op.f("ix_review_cases_member_id_hash"), table_name="review_cases")
    op.drop_index(op.f("ix_review_cases_tenant_id"), table_name="review_cases")
    op.drop_table("review_cases")
