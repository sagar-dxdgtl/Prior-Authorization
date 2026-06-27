"""drop actor_id FK from eligibility_checks - actor may be a service account or external caller

Revision ID: 0004_drop_actor_fk
Revises: 0003_seed_admin
Create Date: 2026-06-27

"""
from typing import Sequence, Union
from alembic import op

revision: str = "0004_drop_actor_fk"
down_revision: Union[str, None] = "0003_seed_admin"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint(
        "eligibility_checks_actor_id_fkey",
        "eligibility_checks",
        type_="foreignkey",
    )


def downgrade() -> None:
    op.create_foreign_key(
        "eligibility_checks_actor_id_fkey",
        "eligibility_checks",
        "users",
        ["actor_id"],
        ["id"],
    )
