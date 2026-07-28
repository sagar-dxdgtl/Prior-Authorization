"""seed payer catalogue: Wellcare (Centene), Georgia — Atlanta market

The Ins Test 3 sheet has a "Wellcare / GA" row (Manayan, Conrad, NPI 1902811656, Kennesaw GA 30144)
and GA was the one market with no Wellcare row in the catalogue — AZ, IL, TX-Houston, TX-Dallas and
NJ all had one. Nothing here is a new determination: same label ("Wellcare (Centene)"), same line
(Medicare Advantage) and the same Stedi payer id (68069, Centene) as every other Wellcare row, so the
only new fact is the market. `enrollment_status` stays `needs_enrollment` like its siblings — it has an
id but is not yet a public-FHIR/adapter-supported payer.

Idempotent insert (guard SELECT + INSERT VALUES), same shape as 0027.

NOTE on numbering: this branch is based on `main`, whose head is 0027, so this is 0029 after this
branch's own 0028_portal_captures. The demo branch (`demo-portal-verified-network`) independently has
0028_rls_empty_guc_fix … 0033_portal_facts_resync applied to both the dev and test databases, so
`alembic upgrade` cannot walk the chain from either side until the two are re-chained at rebase. The
seed below was therefore applied directly as well; re-running this migration is a no-op by design.

Revision ID: 0029_wellcare_ga_atlanta
Revises: 0028_portal_captures
Create Date: 2026-07-28
"""

import uuid
from collections.abc import Sequence

from sqlalchemy import text

from alembic import op
from network_probe.payers.roster_seed import payer_rows

revision: str = "0029_wellcare_ga_atlanta"
down_revision: str | None = "0028_portal_captures"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLS = (
    "id, tenant_id, key, label, benefit_type, state, stedi_payer_id, enrollment_status, "
    "network_indicator_supported, fhir_base_url, tic_url, directory_url, directory_access"
)
_VALS = (
    ":id, :tenant_id, :key, :label, :benefit_type, :state, :stedi_payer_id, :enrollment_status, "
    ":network_indicator_supported, :fhir_base_url, :tic_url, :directory_url, :directory_access"
)
_INSERT = text(f"INSERT INTO payers ({_COLS}) VALUES ({_VALS})")
_EXISTS = text(
    "SELECT 1 FROM payers WHERE tenant_id IS NULL AND key = :key "
    "AND benefit_type = :benefit_type AND state = :state LIMIT 1"
)
_NEW = {("Wellcare (Centene)", "GA-Atlanta")}


def upgrade() -> None:
    conn = op.get_bind()
    for r in payer_rows():
        if (r["label"], r["state"]) not in _NEW:
            continue
        already = conn.execute(
            _EXISTS, {"key": r["key"], "benefit_type": r["benefit_type"], "state": r["state"]}
        ).first()
        if already:
            continue
        conn.execute(_INSERT, {"id": str(uuid.uuid4()), **r})


def downgrade() -> None:
    op.get_bind().execute(
        text(
            "DELETE FROM payers WHERE tenant_id IS NULL AND label = 'Wellcare (Centene)' "
            "AND state = 'GA-Atlanta'"
        )
    )
