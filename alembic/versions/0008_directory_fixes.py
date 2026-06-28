"""directory catalogue fixes: Wellpoint→needs-auth, Healthspring→public-fhir

Corrects two global payer rows after live verification:
  - Wellpoint/Amerigroup: the registered-path FHIR URL returns 403 without OAuth2 creds;
    clear fhir_base_url and flip directory_access to needs-authorized-api.
  - Healthspring (Cigna Medicare): p-hi2.digitaledge.cigna.com/ProviderDirectory/v1 is
    confirmed public (no auth required); set fhir_base_url and flip to public-fhir.

Revision ID: 0008_directory_fixes
Revises: 0007_payer_sources
Create Date: 2026-06-28
"""

from collections.abc import Sequence

from sqlalchemy import text

from alembic import op

revision: str = "0008_directory_fixes"
down_revision: str | None = "0007_payer_sources"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_WELLPOINT_UP = text(
    "UPDATE payers SET fhir_base_url = NULL, directory_access = 'needs-authorized-api' "
    "WHERE tenant_id IS NULL AND label = 'Wellpoint / Amerigroup (Elevance)'"
)
_HEALTHSPRING_UP = text(
    "UPDATE payers SET "
    "fhir_base_url = 'https://p-hi2.digitaledge.cigna.com/ProviderDirectory/v1', "
    "directory_access = 'public-fhir' "
    "WHERE tenant_id IS NULL AND label = 'Healthspring'"
)
# downgrade: restore the incorrect pre-fix state
_WELLPOINT_DOWN = text(
    "UPDATE payers SET "
    "fhir_base_url = 'https://totalview.healthos.elevancehealth.com/resources/registered/Wellpoint/api/v1/fhir', "
    "directory_access = 'public-fhir' "
    "WHERE tenant_id IS NULL AND label = 'Wellpoint / Amerigroup (Elevance)'"
)
_HEALTHSPRING_DOWN = text(
    "UPDATE payers SET fhir_base_url = NULL, directory_access = 'needs-authorized-api' "
    "WHERE tenant_id IS NULL AND label = 'Healthspring'"
)


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(_WELLPOINT_UP)
    conn.execute(_HEALTHSPRING_UP)


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(_WELLPOINT_DOWN)
    conn.execute(_HEALTHSPRING_DOWN)
