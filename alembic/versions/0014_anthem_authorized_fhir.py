"""directory catalogue: Anthem/Elevance (Colorado) → authorized-fhir

Flips the Colorado "BCBS / Empire (Anthem / Elevance)" rows to the live OAuth2-gated PDEX
directory after wiring + live verification:
  - Elevance's CMS-mandate Provider Directory API (FHIR R4, PDEX Plan-Net) at
    totalview.healthos.elevancehealth.com/.../fhir/cms_mandate/mcd is confirmed reachable with
    OAuth2 client-credentials (ANTHEM_FHIR_* in .env). `/metadata` is a live CapabilityStatement
    (fhirVersion 4.0.1) and Practitioner?identifier=<NPI> / PractitionerRole?practitioner=<id>
    return data with inline PDEX network-reference displays. The engine routes the directory leg
    to the generic FhirPdexAdapter through an OAuth2 bearer-token client.

Scope: ONLY Colorado. Anthem Blue Cross and Blue Shield Colorado IS Elevance, so this is its
authoritative directory. The same roster label covers AZ (BCBSAZ) and FL (Florida Blue), which are
INDEPENDENT Blue licensees NOT present in this Elevance directory — those rows stay
needs-authorized-api so the engine never false-OONs a real local provider against the wrong payer.

Mirrors the per-(label, state) override in payers/roster_seed.py for already-seeded databases.

Revision ID: 0014_anthem_authorized_fhir
Revises: 0013_payer_directory_entries
Create Date: 2026-06-30
"""

from collections.abc import Sequence

from sqlalchemy import text

from alembic import op

revision: str = "0014_anthem_authorized_fhir"
down_revision: str | None = "0013_payer_directory_entries"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ANTHEM_FHIR = "https://totalview.healthos.elevancehealth.com/resources/unregistered/api/v1/fhir/cms_mandate/mcd"

_UP = text(
    "UPDATE payers SET "
    "fhir_base_url = :url, "
    "directory_access = 'authorized-fhir' "
    "WHERE tenant_id IS NULL "
    "AND label = 'BCBS / Empire (Anthem / Elevance)' "
    "AND state = 'CO-Denver'"
)
# downgrade: restore the pre-wiring (auth-gated, unrouted) state
_DOWN = text(
    "UPDATE payers SET fhir_base_url = NULL, directory_access = 'needs-authorized-api' "
    "WHERE tenant_id IS NULL "
    "AND label = 'BCBS / Empire (Anthem / Elevance)' "
    "AND state = 'CO-Denver'"
)


def upgrade() -> None:
    op.get_bind().execute(_UP, {"url": _ANTHEM_FHIR})


def downgrade() -> None:
    op.get_bind().execute(_DOWN)
