"""seed payer catalogue: Blue Shield of California (AZ market, BlueCard home plan)

Adds Blue Shield of California as a queryable payer for this client's member SRGB10057830 — a CA
home-plan member (BlueCard alpha prefix SRG) receiving services in Arizona. Stedi 100935 (stediId
JDGWJ), eligibilityCheck=SUPPORTED; validated live 2026-07-18 (active). Distinct company from AZ
Blue (53589) and Anthem. Commercial line, so it is TiC-eligible (unlike the Medicare cases).

Idempotent insert (guard SELECT + INSERT VALUES), same shape as 0022.

Revision ID: 0027_blue_shield_california_az
Revises: 0026_plan_benefits
Create Date: 2026-07-18
"""

import uuid
from collections.abc import Sequence

from sqlalchemy import text

from alembic import op
from network_probe.payers.roster_seed import payer_rows

revision: str = "0027_blue_shield_california_az"
down_revision: str | None = "0026_plan_benefits"
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
_NEW = {("Blue Shield of California", "AZ")}


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
        text("DELETE FROM payers WHERE tenant_id IS NULL AND label = 'Blue Shield of California' AND state = 'AZ'")
    )
