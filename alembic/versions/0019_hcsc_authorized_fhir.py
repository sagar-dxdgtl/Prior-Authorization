"""directory catalogue: HCSC (BCBS IL/TX/MT/NM/OK) → authorized-fhir

Flips every "BCBS / Empire (Anthem / Elevance)(HCSC)" row (IL, TX-Houston, TX-Dallas — all benefit
types) to the live client_id-header-gated PDEX directory after wiring + live verification:
  - HCSC's Sapphire Provider Directory API (FHIR R4, PDEX Plan-Net) at
    api.hcsc.net/providerfinder/sapphire/fhir was previously confirmed gated (401 even on
    /metadata — see docs/payer-sources/MATRIX.md, SIGNUP-CHECKLIST.md "applied-for keys"). HCSC
    has now issued a client_id credential (HCSC_FHIR_CLIENT_ID in .env). `/metadata` is a live FHIR
    4.0.1 CapabilityStatement and Practitioner?identifier=<NPI> / PractitionerRole?practitioner=<id>
    return data with inline PDEX network-reference displays — same shape as Humana. The engine
    routes the directory leg to the generic FhirPdexAdapter through a static-header client
    (build_apikey_fhir_adapter), NOT the Anthem OAuth2 path — HCSC is an independent Blue licensee,
    not Elevance, despite the shared roster label.

Scope: all 3 markets this label currently spans (IL, TX-Houston, TX-Dallas) — HCSC's directory is
one national API, not per-state, so every benefit type/state combination routes the same way.

Mirrors the label-level update in payers/roster_seed.py (SOURCES) for already-seeded databases.

Revision ID: 0019_hcsc_authorized_fhir
Revises: 0018_uhc_fhir_base_url
Create Date: 2026-07-14
"""

from collections.abc import Sequence

from sqlalchemy import text

from alembic import op

revision: str = "0019_hcsc_authorized_fhir"
down_revision: str | None = "0018_uhc_fhir_base_url"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_HCSC_FHIR = "https://api.hcsc.net/providerfinder/sapphire/fhir"
_HCSC_LABEL = "BCBS / Empire (Anthem / Elevance)(HCSC)"

_UP = text(
    "UPDATE payers SET "
    "fhir_base_url = :url, "
    "directory_access = 'authorized-fhir' "
    "WHERE tenant_id IS NULL "
    "AND label = :label"
)
# downgrade: restore the pre-wiring (auth-gated, unrouted) state
_DOWN = text(
    "UPDATE payers SET fhir_base_url = NULL, directory_access = 'needs-authorized-api' "
    "WHERE tenant_id IS NULL "
    "AND label = :label"
)


def upgrade() -> None:
    op.get_bind().execute(_UP, {"url": _HCSC_FHIR, "label": _HCSC_LABEL})


def downgrade() -> None:
    op.get_bind().execute(_DOWN, {"label": _HCSC_LABEL})
