"""Resolve Stedi payer ids for BCBS South Carolina, Surest and Publix.

0042 and 0043 seeded these three with `stedi_payer_id` NULL / `needs_payer_id`, because no id had
been verified and roster_seed.py's rule is that an unverified id is never baked in. The payer picker
therefore showed "payer id unresolved" and no 270 could be routed for them.

Resolved 2026-08-12 against Stedi's live directory (3,660 payers), filtered LOCALLY. This matters:
`search_stedi` returned the SAME 20 payers for every query tried ("south carolina", "publix",
"surest", "bind"), i.e. it was not filtering at all, and an id lifted from that list would have been
an unrelated payer. The full list was fetched and matched by name instead.

    00401  (stediId NYNYD)  BlueCross BlueShield of South Carolina        eligibilityCheck SUPPORTED
    25463  (stediId QSOCK)  Surest                                        eligibilityCheck SUPPORTED
    J1897  (stediId XSZDP)  Publix Super Markets Incorporated             eligibilityCheck NOT_SUPPORTED

`enrollment_status` is taken from Stedi's own `transactionSupport.eligibilityCheck`, the same source
0041 used to correct 163 wrong badges — not guessed from whether an id exists.

PUBLIX IS DELIBERATELY `not_supported`, NOT `supported`. Stedi lists the employer entity but cannot
run an eligibility check against it, so enrolling would not help; that is a distinct fact from
"needs enrollment" and 0041 already added the value and its own badge for exactly this case. It is
also NOT quietly repointed at BCBS SC's 00401: the Publix book is administered on a co-branded
Florida Blue tenant, so which entity answers a 270 for that member is an open question, and pointing
it at the wrong payer would fail in a way that reads like a coverage problem. It stays honest until
someone confirms the routing.

Idempotent: matches on key, and only fills rows that still carry the seeded placeholder.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# NB: alembic_version.version_num is VARCHAR(32) -- a longer id fails the whole migration
# at the very last statement, after its updates have already run.
revision = "0044_stedi_payer_ids"
down_revision = "0043_seed_uhc_surest"
branch_labels = None
depends_on = None

# key-prefix -> (stedi_payer_id, enrollment_status, network_indicator_supported)
UPDATES = [
    ("bcbs-south-carolina-publix%", "J1897", "not_supported", False),
    ("bcbs-south-carolina-%", "00401", "supported", True),
    ("uhc-surest-%", "25463", "supported", True),
]


def upgrade() -> None:
    conn = op.get_bind()
    # Publix first: its key also matches the broader bcbs-south-carolina-% pattern, and the guard
    # below only fills rows still holding the placeholder, so ordering keeps it on its own id.
    for pattern, payer_id, status, net in UPDATES:
        conn.execute(
            sa.text(
                """
                UPDATE payers
                   SET stedi_payer_id = CAST(:pid AS varchar),
                       enrollment_status = CAST(:status AS varchar),
                       network_indicator_supported = :net
                 WHERE tenant_id IS NULL
                   AND key LIKE CAST(:pattern AS varchar)
                   AND stedi_payer_id IS NULL
                """
            ),
            {"pid": payer_id, "status": status, "net": net, "pattern": pattern},
        )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text(
            """
            UPDATE payers
               SET stedi_payer_id = NULL,
                   enrollment_status = 'needs_payer_id',
                   network_indicator_supported = FALSE
             WHERE tenant_id IS NULL
               AND (key LIKE 'bcbs-south-carolina-%' OR key LIKE 'uhc-surest-%')
            """
        )
    )
