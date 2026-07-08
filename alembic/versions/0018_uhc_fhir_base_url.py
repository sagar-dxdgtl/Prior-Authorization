"""directory catalogue: populate UnitedHealthcare's fhir_base_url

get_adapter() only reaches the pre-built "uhc" adapter-key shortcut when a caller passes q.payer
literally as "uhc". check_eligibility() (and this app's uvc_demo pipeline) resolve payers by their
full catalogue key instead (e.g. "unitedhealthcare-az"), which falls through to the catalogue-
driven FHIR dispatch -- and that path requires fhir_base_url to be set on the row. UHC's rows had
it deliberately left None (per roster_seed.py's original comment: "UHC/Oscar keep their existing
adapters, routed by adapter key, so they stay None"), which meant every UnitedHealthcare catalogue
row silently had NO WORKING LIVE-DIRECTORY CHECK at all when reached via its catalogue key --
raising "No adapter for payer" instead of running a real check. This affects the live production
/api/eligibility path too, not just the demo, since it goes through the same check_eligibility() ->
check_network() -> get_adapter() call chain.

Fix: populate fhir_base_url with the same endpoint the "uhc" adapter-key shortcut already uses
(fhir_pdex.KNOWN_ENDPOINTS["uhc"] = https://flex.optum.com/fhirpublic/R4), matching how Cigna/
Kaiser/Humana/Anthem rows are already seeded. Source of truth updated in roster_seed.py; this
migration re-syncs the existing UnitedHealthcare rows in the DB.

Revision ID: 0018_uhc_fhir_base_url
Revises: 0017_uvc_demo_stedi_ids
Create Date: 2026-07-08
"""

from collections.abc import Sequence

from sqlalchemy import text

from alembic import op
from network_probe.payers.roster_seed import payer_rows

revision: str = "0018_uhc_fhir_base_url"
down_revision: str | None = "0017_uvc_demo_stedi_ids"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UPDATE = text(
    "UPDATE payers SET fhir_base_url=:fhir_base_url "
    "WHERE tenant_id IS NULL AND label=:label AND state=:state "
    "AND benefit_type IS NOT DISTINCT FROM :benefit_type AND fhir_base_url IS NULL"
)


def upgrade() -> None:
    conn = op.get_bind()
    for r in payer_rows():
        if r["label"] == "UnitedHealthcare":
            conn.execute(_UPDATE, r)


def downgrade() -> None:
    op.get_bind().execute(
        text(
            "UPDATE payers SET fhir_base_url=NULL WHERE tenant_id IS NULL AND label='UnitedHealthcare' "
            "AND fhir_base_url='https://flex.optum.com/fhirpublic/R4'"
        )
    )
