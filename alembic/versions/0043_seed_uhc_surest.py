"""Seed UHC Surest as its own payer so it is selectable and cannot be confused with plain UHC.

Row 8 of the 2026-08-12 sheet is UHC Surest. Measured against the live roster before this migration:
`label ILIKE '%surest%'` returned 0 rows, so typing "Surest" in the payer picker found NOTHING, and
"UHC Surest" returned 15 generic `unitedhealthcare-*` markets. The second failure is the dangerous
one — it looks like a match while naming a different network book, and a walk launched from it would
answer about UnitedHealthcare's plan list rather than Surest's.

WHY A SEPARATE PAYER ON A SHARED PORTAL. Surest is a UnitedHealthcare company and does not run its
own directory: benefits.surest.com links its provider directories straight into
findcare.guest.uhc.com with a base64 `deeplink` carrying UHC's own guest flag and, crucially, a
`reciprocityId` that pins the network by IDENTIFIER — 52 Choice Plus, 03 Select Plus POS, 01 Options
PPO. Same portal, same driver (`uhc-findcare`), different network book. So the payer key is separate
while the PortalTarget is shared.

`stedi_payer_id` stays NULL / `needs_payer_id`: no eligibility id was verified, and roster_seed.py's
own rule is that unverified ids are not baked in.

Additive and idempotent, matching 0042.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0043_seed_uhc_surest"
down_revision = "0042_seed_bcbs_south_carolina"
branch_labels = None
depends_on = None

_DIRECTORY = "https://benefits.surest.com/"

ROWS = [
    ("uhc-surest-tx-houston", "TX-Houston"),
    ("uhc-surest-tx-dallas", "TX-Dallas"),
    ("uhc-surest-ga-atlanta", "GA-Atlanta"),
    ("uhc-surest-il", "IL"),
]


def upgrade() -> None:
    conn = op.get_bind()
    for key, state in ROWS:
        conn.execute(
            sa.text(
                """
                INSERT INTO payers (
                    id, tenant_id, key, label, benefit_type, state, stedi_payer_id,
                    enrollment_status, network_indicator_supported, fhir_base_url, tic_url,
                    directory_url, directory_access
                )
                -- Casts are load-bearing: :key is both a SELECTed value and compared against a
                -- varchar column, and Postgres otherwise raises AmbiguousParameter (see 0042).
                SELECT gen_random_uuid(), NULL, CAST(:key AS varchar), 'UHC Surest',
                       'Commercial', CAST(:state AS varchar), NULL,
                       'needs_payer_id', FALSE, NULL, NULL,
                       CAST(:directory AS varchar), 'public-guest'
                WHERE NOT EXISTS (
                    SELECT 1 FROM payers WHERE key = CAST(:key AS varchar) AND tenant_id IS NULL
                )
                """
            ),
            {"key": key, "state": state, "directory": _DIRECTORY},
        )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text("DELETE FROM payers WHERE tenant_id IS NULL AND key = ANY(:keys)"),
        {"keys": [r[0] for r in ROWS]},
    )
