"""provider_portal_facts: correct the Smith/Aetna note (it wrongly claimed "plain OON")

The seeded note said "plain OON — not distinguished as physician-OON", which contradicts the actual
determination: the portal (physician absent) combined with our Aetna TiC crosswalk (clinic TIN
843447602 IS contracted) correctly yields Physician OON — the client's oracle. A portal note should
describe only what the portal showed, not make the plain-vs-physician call. Syncs the note text from
portal_seed so the DB matches the file.

Idempotent: a single UPDATE of the global (tenant_id NULL) Aetna row.

Revision ID: 0032_portal_aetna_note
Revises: 0031_portal_facts_resync_aetna
"""

from collections.abc import Sequence

from sqlalchemy import text

from alembic import op
from network_probe.payers.portal_seed import portal_fact_rows

revision: str = "0032_portal_aetna_note"
down_revision: str | None = "0031_portal_facts_resync_aetna"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _aetna_note() -> str:
    return next(
        r["note"] for r in portal_fact_rows()
        if r["payer_key"] == "aetna-az" and str(r["npi"]) == "1346866332"
    )


def upgrade() -> None:
    op.get_bind().execute(
        text(
            "UPDATE provider_portal_facts SET note=:n "
            "WHERE tenant_id IS NULL AND payer_key='aetna-az' AND npi='1346866332'"
        ),
        {"n": _aetna_note()},
    )


def downgrade() -> None:
    # Display text only — nothing to roll back.
    pass
