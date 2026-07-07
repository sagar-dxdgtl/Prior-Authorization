"""seed payer catalogue: Illinois / Georgia-Atlanta / Texas-Houston / Texas-Dallas markets

Adds 113 new global payer rows (client benefit-list reconciliation, 2026-07-06) for four markets
that had zero roster coverage before this migration: IL, GA-Atlanta, TX-Houston, TX-Dallas. Source
of truth is ``network_probe.payers.roster_seed.payer_rows`` — this migration inserts only the rows
for those four states (the original 67 rows from 0002 are untouched).

Includes the Georgia Anthem/BCBS `authorized-fhir` override (rides the same `ANTHEM_FHIR_*` creds
already wired for Colorado in 0014 — confirmed live against real GA data, no new registration
needed) and several `public-fhir` rows that ride existing adapters/endpoints with zero new code
(Cigna, Humana, Devoted, Oscar, Molina, UnitedHealthcare, the Centene family). See
docs/payer-sources/MATRIX.md and SIGNUP-CHECKLIST.md for full per-payer sourcing notes.

Revision ID: 0015_il_ga_tx_markets
Revises: 0014_anthem_authorized_fhir
Create Date: 2026-07-06
"""

import uuid
from collections.abc import Sequence

from sqlalchemy import text

from alembic import op
from network_probe.payers.roster_seed import payer_rows

revision: str = "0015_il_ga_tx_markets"
down_revision: str | None = "0014_anthem_authorized_fhir"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NEW_STATES = {"IL", "GA-Atlanta", "TX-Houston", "TX-Dallas"}

_COLS = (
    "id, tenant_id, key, label, benefit_type, state, stedi_payer_id, enrollment_status, "
    "network_indicator_supported, fhir_base_url, tic_url, directory_url, directory_access"
)
_VALS = (
    ":id, :tenant_id, :key, :label, :benefit_type, :state, :stedi_payer_id, :enrollment_status, "
    ":network_indicator_supported, :fhir_base_url, :tic_url, :directory_url, :directory_access"
)


def upgrade() -> None:
    conn = op.get_bind()
    rows = [r for r in payer_rows() if r["state"] in _NEW_STATES]
    for r in rows:
        conn.execute(text(f"INSERT INTO payers ({_COLS}) VALUES ({_VALS})"), {"id": str(uuid.uuid4()), **r})


def downgrade() -> None:
    op.get_bind().execute(
        text("DELETE FROM payers WHERE tenant_id IS NULL AND state = ANY(:states)"),
        {"states": list(_NEW_STATES)},
    )
