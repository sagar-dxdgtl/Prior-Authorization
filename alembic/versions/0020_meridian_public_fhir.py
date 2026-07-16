"""directory catalogue: Meridian Health (IL Medicaid, Centene) → public-fhir

Meridian Health Plan of Illinois is a wholly-owned Centene subsidiary served by the shared
Centene PDEX Plan-Net Provider Directory — the same endpoint migration 0011 already set for the
other Centene-family rows (Ambetter / Wellcare / Arizona Complete Health). Meridian's roster
SOURCES entry was added *after* 0011 ran, so no migration ever populated its fhir_base_url: the
row stayed directory_access='none' / fhir_base_url=NULL, and the network-status engine fell
through to `ValueError: No adapter for payer 'meridian-health-il'`. This wires it like the rest
of the Centene family so it routes to the generic FHIR PDEX adapter.

Endpoint: https://iopc-pd.api.centene.com/iopc/pd/fhir/providerdirectory
(FHIR R4, Da Vinci PDEX Plan-Net, Authentication Type: None — same AWS-WAF egress-allowlist
caveat documented in 0011). Verified live against this client's Meridian-labeled provider
Kevin Petermann (NPI 1588744650), which returns real Illinois-specific network affiliations.

Revision ID: 0020_meridian_public_fhir
Revises: 0019_hcsc_authorized_fhir
Create Date: 2026-07-16
"""

from collections.abc import Sequence

from sqlalchemy import text

from alembic import op

revision: str = "0020_meridian_public_fhir"
down_revision: str | None = "0019_hcsc_authorized_fhir"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UP = text(
    "UPDATE payers SET "
    "fhir_base_url = 'https://iopc-pd.api.centene.com/iopc/pd/fhir/providerdirectory', "
    "directory_access = 'public-fhir' "
    "WHERE tenant_id IS NULL AND label = 'Meridian Health'"
)
# downgrade: restore the pre-fix state (the row was directory_access='none' with no base_url).
_DOWN = text(
    "UPDATE payers SET fhir_base_url = NULL, directory_access = 'none' "
    "WHERE tenant_id IS NULL AND label = 'Meridian Health'"
)


def upgrade() -> None:
    op.get_bind().execute(_UP)


def downgrade() -> None:
    op.get_bind().execute(_DOWN)
