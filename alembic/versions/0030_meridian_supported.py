"""meridian-health-il: flip enrollment_status needs_enrollment -> supported

Meridian Health (IL Managed Medicaid) is adapter-supported: it resolves to the shared Centene
national PDEX public-FHIR directory (verified live against the client's own Kevin Petermann,
NPI 1588744650). Per roster_seed's own definition, `supported` = a public-FHIR/adapter-supported
payer — so the seeded `needs_enrollment` was stale (set before the Centene FHIR wiring landed) and
mislabels a working payer in the UI. This flips the already-seeded global row.

Additive + idempotent: a single conditional UPDATE of the global (tenant_id NULL) row.

Revision ID: 0030_meridian_supported
Revises: 0029_provider_portal_facts
"""

from collections.abc import Sequence

from sqlalchemy import text

from alembic import op

revision: str = "0030_meridian_supported"
down_revision: str | None = "0029_provider_portal_facts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.get_bind().execute(
        text(
            "UPDATE payers SET enrollment_status='supported' "
            "WHERE key='meridian-health-il' AND tenant_id IS NULL AND enrollment_status='needs_enrollment'"
        )
    )


def downgrade() -> None:
    op.get_bind().execute(
        text(
            "UPDATE payers SET enrollment_status='needs_enrollment' "
            "WHERE key='meridian-health-il' AND tenant_id IS NULL AND enrollment_status='supported'"
        )
    )
