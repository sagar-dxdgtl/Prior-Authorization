"""directory catalogue: SCAN Health Plan → public-fhir (presence-based)

Sets SCAN's public PDEX directory after live verification:
  - https://providerdirectory.scanhealthplan.com (InterSystems FHIR R4, no auth) lists SCAN's
    in-network providers. It exposes NO traversable network linkage (PractitionerRole has no
    network-reference extension; OrganizationAffiliation.network and InsurancePlan.network are
    unpopulated — verified: 76 InsurancePlans, 0 network refs). So the engine routes it to the
    presence-based ScanDirectoryAdapter (service._fhir_class_for), which treats "present in the
    directory" as IN_NETWORK for SCAN and best-effort confirms the queried state.
    Set fhir_base_url and flip directory_access to public-fhir.

Revision ID: 0012_scan_public_fhir
Revises: 0011_centene_public_fhir
Create Date: 2026-06-29
"""

from collections.abc import Sequence

from sqlalchemy import text

from alembic import op

revision: str = "0012_scan_public_fhir"
down_revision: str | None = "0011_centene_public_fhir"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SCAN_UP = text(
    "UPDATE payers SET "
    "fhir_base_url = 'https://providerdirectory.scanhealthplan.com', "
    "directory_access = 'public-fhir' "
    "WHERE tenant_id IS NULL AND label = 'Scan'"
)
# downgrade: restore the pre-fix (auth-gated) state
_SCAN_DOWN = text(
    "UPDATE payers SET fhir_base_url = NULL, directory_access = 'needs-authorized-api' "
    "WHERE tenant_id IS NULL AND label = 'Scan'"
)


def upgrade() -> None:
    op.get_bind().execute(_SCAN_UP)


def downgrade() -> None:
    op.get_bind().execute(_SCAN_DOWN)
