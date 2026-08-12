"""Seed BlueCross BlueShield of South Carolina and its Publix book into the payer roster.

Four rows of the 2026-08-12 sheet are BCBS SC or BCBS SC Publix, and both were reachable via
`/api/portal/capture` but INVISIBLE in the UI: the payer picker is fed by `/api/payers/search`, which
reads this table — registering a `PortalTarget` does not put a payer in the dropdown. Verified against
the live DB on 2026-08-12: 306 payer rows, `label ILIKE '%South Carolina%'` -> 0, `'%Publix%'` -> 0.

WHY THEY ARE TWO PAYERS AND NOT ONE. They share one Zelis Sapphire instance and differ only by the
`ci` query parameter, but they are different network books: BCBSSC allows 41 of the platform's 96
networks and Publix allows 3. Network id 10 renders as "Preferred Blue" under BCBSSC and
"PBB - Blue Choice PPO" under Publix — the same id, two different networks by name. Collapsing them
into one payer would let a walk answer a Publix member from the BCBSSC book, which is exactly the
confidently-wrong-network failure this layer exists to prevent.

WHY THE MARKETS ARE THE CLINIC'S AND NOT THE MEMBER'S. All four rows are South Carolina members
treated in Georgia and Florida — ordinary BlueCard. Measured live 2026-08-12: with the network pinned
to the member's own "Preferred Blue", the SC directory returned the sheet's own physician at the
sheet's own street address in Lake Worth FL, and ten Georgia providers at the Atlanta clinic ZIP. So
the payer is scoped by where care is delivered, matching every other market row here.

`stedi_payer_id` is deliberately NULL / `needs_payer_id`. roster_seed.py's own rule is that an
unverified id is not baked in, and no eligibility id for these was confirmed. That is honest rather
than convenient: a wrong payer id produces a failed 270 that reads like a coverage problem.

Additive and idempotent — inserts only the four keys, skips any that already exist, so a re-run and a
later full re-seed from roster_seed.py both converge on the same rows.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0042_seed_bcbs_south_carolina"
down_revision = "0041_enrollment_status_truth"
branch_labels = None
depends_on = None

_SAPPHIRE = "https://shoppingforcare.sapphirethreesixtyfive.com/?ci="
# The BlueCard control-plane index. Bare requests 403; it needs a Referer of provider.bcbssc.com and
# a US exit. Recorded so the TiC leg does not have to rediscover it.
_TIC = "https://d2vbl1kcu4hfid.cloudfront.net/bcbssc_index.json"

ROWS = [
    # key, label, benefit_type, state, directory_url
    ("bcbs-south-carolina-ga-atlanta", "BCBS South Carolina", "Commercial", "GA-Atlanta",
     f"{_SAPPHIRE}BCBSSC"),
    ("bcbs-south-carolina-fl-tampa", "BCBS South Carolina", "Commercial", "FL-Tampa",
     f"{_SAPPHIRE}BCBSSC"),
    ("bcbs-south-carolina-fl-south-florida", "BCBS South Carolina", "Commercial", "FL-South Florida",
     f"{_SAPPHIRE}BCBSSC"),
    ("bcbs-south-carolina-publix-fl-south-florida", "BCBS South Carolina Publix", "Commercial",
     "FL-South Florida", f"{_SAPPHIRE}Publix"),
]


def upgrade() -> None:
    conn = op.get_bind()
    for key, label, btype, state, directory_url in ROWS:
        conn.execute(
            sa.text(
                """
                INSERT INTO payers (
                    id, tenant_id, key, label, benefit_type, state, stedi_payer_id,
                    enrollment_status, network_indicator_supported, fhir_base_url, tic_url,
                    directory_url, directory_access
                )
                -- Casts are load-bearing: :key is both a SELECTed value and a comparison against a
                -- varchar column, and without them Postgres raises AmbiguousParameter ("text versus
                -- character varying") rather than inserting.
                SELECT gen_random_uuid(), NULL, CAST(:key AS varchar), CAST(:label AS varchar),
                       CAST(:btype AS varchar), CAST(:state AS varchar), NULL,
                       'needs_payer_id', FALSE, NULL, CAST(:tic AS varchar),
                       CAST(:directory_url AS varchar), 'public-guest'
                WHERE NOT EXISTS (
                    SELECT 1 FROM payers WHERE key = CAST(:key AS varchar) AND tenant_id IS NULL
                )
                """
            ),
            {"key": key, "label": label, "btype": btype, "state": state,
             "tic": _TIC, "directory_url": directory_url},
        )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text("DELETE FROM payers WHERE tenant_id IS NULL AND key = ANY(:keys)"),
        {"keys": [r[0] for r in ROWS]},
    )
