"""directory catalogue: Centene-family plans → public-fhir (PDEX, allowlist caveat)

Sets the shared Centene PDEX Plan-Net Provider Directory on all Centene-family rows:
  - Ambetter (Centene)
  - Arizona Complete Health - Complete Care Plan (Centene)
  - Wellcare (Centene)

Endpoint: https://iopc-pd.api.centene.com/iopc/pd/fhir/providerdirectory
(FHIR R4 4.0.1, Da Vinci PDEX Plan-Net 1.2.0, Authentication Type: None per the Centene
partner portal). Verified live — `/metadata` 200 + unauthenticated `Practitioner?family=SMITH`
returned a 200-record Bundle with NPIs.

CAVEAT (important): the endpoint is fronted by CloudFront with an AWS-WAF managed rule that
blocks datacenter/cloud and non-US IPs (HTTP 403 "Request blocked"). The API needs NO
credentials, but the production egress IP must be **allowlisted by Centene** (email the API
owner) or every query 403s. Until then the directory leg will error for these payers. This is
intentionally seeded as public-fhir (the API is genuinely no-auth) with the allowlist tracked
in docs/payer-sources/SIGNUP-CHECKLIST.md.

Revision ID: 0011_centene_public_fhir
Revises: 0010_molina_public_fhir
Create Date: 2026-06-29
"""

from collections.abc import Sequence

from sqlalchemy import text

from alembic import op

revision: str = "0011_centene_public_fhir"
down_revision: str | None = "0010_molina_public_fhir"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Centene-family labels (none contain single quotes, so inline literal SQL is safe).
_LABELS = (
    "'Ambetter (Centene)', "
    "'Arizona Complete Health - Complete Care Plan (Centene)', "
    "'Wellcare (Centene)'"
)
_CENTENE_UP = text(
    "UPDATE payers SET "
    "fhir_base_url = 'https://iopc-pd.api.centene.com/iopc/pd/fhir/providerdirectory', "
    "directory_access = 'public-fhir' "
    f"WHERE tenant_id IS NULL AND label IN ({_LABELS})"
)
# downgrade: restore the pre-fix (auth-gated) state
_CENTENE_DOWN = text(
    "UPDATE payers SET fhir_base_url = NULL, directory_access = 'needs-authorized-api' "
    f"WHERE tenant_id IS NULL AND label IN ({_LABELS})"
)


def upgrade() -> None:
    op.get_bind().execute(_CENTENE_UP)


def downgrade() -> None:
    op.get_bind().execute(_CENTENE_DOWN)
