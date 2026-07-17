"""seed payer catalogue: Devoted Health added to CO-Denver / IL / TX-Dallas

Devoted Health already covered AZ / GA-Atlanta / FL-South Florida / FL-Tampa / TX-Houston. This adds
the three remaining client markets where Devoted genuinely operates a Medicare Advantage plan
(Colorado, Illinois, Dallas-Fort Worth). It is deliberately NOT added to NJ-UVC / NJ-Vascular Health
or NY — Devoted does not sell in those states, and a fabricated market row would false-resolve in a
live demo. Same national Stedi id (DEVOT) and public fhir.devoted.com PDEX adapter as the existing
Devoted rows, so these route to the live directory with no new code or creds.

Idempotent insert (guard SELECT + INSERT VALUES), same shape as 0021.

Revision ID: 0022_devoted_co_il_txdallas
Revises: 0021_master_list_reconciliation
Create Date: 2026-07-17
"""

import uuid
from collections.abc import Sequence

from sqlalchemy import text

from alembic import op
from network_probe.payers.roster_seed import payer_rows

revision: str = "0022_devoted_co_il_txdallas"
down_revision: str | None = "0021_master_list_reconciliation"
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
_DEVOTED_NEW = {("Devoted Health", "CO-Denver"), ("Devoted Health", "IL"), ("Devoted Health", "TX-Dallas")}


def upgrade() -> None:
    conn = op.get_bind()
    for r in payer_rows():
        if (r["label"], r["state"]) not in _DEVOTED_NEW:
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
            "DELETE FROM payers WHERE tenant_id IS NULL AND label = 'Devoted Health' "
            "AND state = ANY(:states)"
        ),
        {"states": ["CO-Denver", "IL", "TX-Dallas"]},
    )
