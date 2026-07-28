"""provider_portal_facts: re-sync global seed rows from portal_seed (adds Smith/Aetna OON)

Adds the Aetna Medicare AZ portal check for Smith (Hedson Desir NPI 1346866332 absent from Aetna Find
Care → out-of-network). Re-syncs the global (tenant_id NULL) rows from portal_seed.portal_fact_rows()
so the DB matches the seed file — same DELETE-global + re-INSERT pattern the roster seed uses (0002).

Idempotent: DELETE the global rows then re-INSERT the current seed with deterministic UUIDs. No FK
points at these ids, so re-inserting is harmless.

Revision ID: 0031_portal_facts_resync_aetna
Revises: 0030_meridian_supported
"""

from collections.abc import Sequence

from sqlalchemy import text

from alembic import op
from network_probe.payers.portal_seed import portal_fact_rows

revision: str = "0031_portal_facts_resync_aetna"
down_revision: str | None = "0030_meridian_supported"
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


def _seed_uuid(i: int) -> str:
    return f"00000000-0000-4000-a000-0000000000{i:02d}"


def _sync(conn) -> None:
    conn.execute(text("DELETE FROM provider_portal_facts WHERE tenant_id IS NULL"))
    for i, r in enumerate(portal_fact_rows()):
        conn.execute(text(f"INSERT INTO provider_portal_facts ({_COLS}) VALUES ({_VALS})"), {
            "id": _seed_uuid(i), "payer_key": r["payer_key"], "npi": str(r["npi"]), "tin": r.get("tin"),
            "plan": r.get("plan"), "in_network": bool(r["in_network"]), "provider_name": r.get("provider_name"),
            "member": r.get("member"), "portal_name": r.get("portal_name") or "provider portal",
            "portal_url": r.get("portal_url"), "screenshot": r.get("screenshot"),
            "verified_by": r.get("verified_by"), "verified_at": r.get("verified_at"), "note": r.get("note"),
        })


def upgrade() -> None:
    _sync(op.get_bind())


def downgrade() -> None:
    # Drop the row this migration adds; the 0029 seed rows remain.
    op.get_bind().execute(
        text("DELETE FROM provider_portal_facts WHERE tenant_id IS NULL AND payer_key='aetna-az' AND npi='1346866332'")
    )
