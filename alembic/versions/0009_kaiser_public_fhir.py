"""directory catalogue fix: Kaiser Permanente → public-fhir

Corrects the global Kaiser Permanente rows after live verification:
  - Kaiser's CMS Provider Directory & Formulary API (FHIR R4, PDEX Plan-Net) at
    kpx-service-bus.kp.org/service/hp/mhpo/healthplanproviderv1rc is confirmed PUBLIC —
    no OAuth2, no registration. `/metadata` is a live CapabilityStatement (Smile CDR,
    fhirVersion 4.0.1) and Practitioner?identifier=<NPI> / PractitionerRole?practitioner=<id>
    return data nationwide (incl. Colorado). Network names resolve from the PDEX
    network-reference Organization refs (Commercial / Medicaid / Medicare).
    Set fhir_base_url and flip directory_access to public-fhir so the engine routes the
    directory leg to the generic FhirPdexAdapter.

Revision ID: 0009_kaiser_public_fhir
Revises: 0008_directory_fixes
Create Date: 2026-06-29
"""

from collections.abc import Sequence

from sqlalchemy import text

from alembic import op

revision: str = "0009_kaiser_public_fhir"
down_revision: str | None = "0008_directory_fixes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_KAISER_UP = text(
    "UPDATE payers SET "
    "fhir_base_url = 'https://kpx-service-bus.kp.org/service/hp/mhpo/healthplanproviderv1rc', "
    "directory_access = 'public-fhir' "
    "WHERE tenant_id IS NULL AND label = 'Kaiser Permanente'"
)
# downgrade: restore the pre-fix (auth-gated) state
_KAISER_DOWN = text(
    "UPDATE payers SET fhir_base_url = NULL, directory_access = 'needs-authorized-api' "
    "WHERE tenant_id IS NULL AND label = 'Kaiser Permanente'"
)


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(_KAISER_UP)


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(_KAISER_DOWN)
