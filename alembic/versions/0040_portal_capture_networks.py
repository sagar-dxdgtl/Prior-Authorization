"""portal_captures.networks_accepted — persist the provider-first network list

A capture that reads EVERY network a provider participates in was computing the most reusable thing
this layer produces and then throwing it away: it survived only as prose inside `note`, where nothing
can query it. Adding it here means one walk answers the question durably.

Discovered live 2026-07-31 on AZ Blue / HealthSparq, which names them behind the card's "N in network"
link (Arthur Maydell NPI 1992078745 -> 14 networks; Hedson Desir NPI 1346866332 -> 1, `ACA Health
Choice`). Because the payer also publishes its full network list — 24 for AZ Blue — the stored list
answers both directions: in the 14, and out of the other 10.

JSONB rather than TEXT[], matching `result_jsonb`/`source_audit` on this schema, and it stays directly
queryable:

    SELECT npi FROM portal_captures WHERE networks_accepted @> '["Statewide PPO"]'

NULL means "not asked, or the portal does not name them" and must never be read as "no networks" —
only a non-empty list licenses treating absence from it as evidence. That is why the column is
nullable with no default rather than defaulting to an empty array, which would assert something
false about every capture written before today.

Additive and idempotent, matching 0025/0028/0036/0037/0038/0039.

Revision ID: 0040_portal_capture_networks
Revises: 0039_portal_capture_note_text
Create Date: 2026-07-31
"""

from __future__ import annotations

from alembic import op

revision = "0040_portal_capture_networks"
down_revision = "0039_portal_capture_note_text"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE portal_captures ADD COLUMN IF NOT EXISTS networks_accepted JSONB")
    # Containment queries ("which captured providers are in network X?") are the whole point of
    # storing it, and JSONB containment can use GIN.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_portal_captures_networks "
        "ON portal_captures USING GIN (networks_accepted)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_portal_captures_networks")
    op.execute("ALTER TABLE portal_captures DROP COLUMN IF EXISTS networks_accepted")
