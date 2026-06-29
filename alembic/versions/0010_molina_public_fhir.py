"""directory catalogue fix: Molina Healthcare → public-fhir

Corrects the global Molina Healthcare rows after live verification:
  - Molina's interoperability Provider Directory API at
    api.interop.molinahealthcare.com/ProviderDirectory is confirmed PUBLIC — no OAuth2,
    no registration. `/metadata` is a live CapabilityStatement (FHIR R4 4.0.1) and
    Practitioner?identifier=<NPI> / PractitionerRole?practitioner=<id> return data
    (605k practitioners, 16.5M roles). Network names are inline on the PDEX
    network-reference extension (e.g. 'MHMS CHIP', 'Molina Marketplace').
    Set fhir_base_url and flip directory_access to public-fhir so the engine routes the
    directory leg to the generic FhirPdexAdapter.

Revision ID: 0010_molina_public_fhir
Revises: 0009_kaiser_public_fhir
Create Date: 2026-06-29
"""

from collections.abc import Sequence

from sqlalchemy import text

from alembic import op

revision: str = "0010_molina_public_fhir"
down_revision: str | None = "0009_kaiser_public_fhir"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MOLINA_UP = text(
    "UPDATE payers SET "
    "fhir_base_url = 'https://api.interop.molinahealthcare.com/ProviderDirectory', "
    "directory_access = 'public-fhir' "
    "WHERE tenant_id IS NULL AND label = 'Molina Healthcare'"
)
# downgrade: restore the pre-fix (auth-gated) state
_MOLINA_DOWN = text(
    "UPDATE payers SET fhir_base_url = NULL, directory_access = 'needs-authorized-api' "
    "WHERE tenant_id IS NULL AND label = 'Molina Healthcare'"
)


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(_MOLINA_UP)


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(_MOLINA_DOWN)
