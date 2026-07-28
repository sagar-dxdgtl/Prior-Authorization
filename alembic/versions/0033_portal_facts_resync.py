"""provider_portal_facts: re-sync global rows from portal_seed (Smith/Aetna note → plain OON)

group_contracted is now line-of-business-aware: the commercial-only TiC crosswalk is not applied to
Medicare/Medicaid/Dual lines, so Smith/Aetna MA resolves to plain OON (not physician-OON). The seed
note is updated to match. Full re-sync of the global (tenant_id NULL) rows from portal_seed so the DB
matches the file — same DELETE-global + re-INSERT pattern as the roster seed and 0031.

Idempotent; deterministic UUIDs; no FK points at these ids.

Revision ID: 0033_portal_facts_resync
Revises: 0032_portal_aetna_note
"""

from collections.abc import Sequence

from sqlalchemy import text

from alembic import op
from network_probe.payers.portal_seed import portal_fact_rows

revision: str = "0033_portal_facts_resync"
down_revision: str | None = "0032_portal_aetna_note"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLS = (
    "id, tenant_id, payer_key, npi, tin, plan, in_network, provider_name, member, "
    "portal_name, portal_url, screenshot, verified_by, verified_at, note"
)
_VALS = (
    ":id, NULL, :payer_key, :npi, :tin, :plan, :in_network, :provider_name, :member, "
    ":portal_name, :portal_url, :screenshot, :verified_by, :verified_at, :note"
)


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(text("DELETE FROM provider_portal_facts WHERE tenant_id IS NULL"))
    for i, r in enumerate(portal_fact_rows()):
        conn.execute(text(f"INSERT INTO provider_portal_facts ({_COLS}) VALUES ({_VALS})"), {
            "id": f"00000000-0000-4000-a000-0000000000{i:02d}", "payer_key": r["payer_key"],
            "npi": str(r["npi"]), "tin": r.get("tin"), "plan": r.get("plan"),
            "in_network": bool(r["in_network"]), "provider_name": r.get("provider_name"),
            "member": r.get("member"), "portal_name": r.get("portal_name") or "provider portal",
            "portal_url": r.get("portal_url"), "screenshot": r.get("screenshot"),
            "verified_by": r.get("verified_by"), "verified_at": r.get("verified_at"), "note": r.get("note"),
        })


def downgrade() -> None:
    # Display/reference data — re-sync is not meaningfully reversible; leave as-is.
    pass
