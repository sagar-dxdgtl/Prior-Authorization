"""Publix eligibility routes through BCBS South Carolina, not the Publix employer entity.

0044 set the Publix row to Stedi payer `J1897` ("Publix Super Markets Incorporated",
eligibilityCheck NOT_SUPPORTED) on the reasoning that the name matched and the 270 routing was
unconfirmed. That was the wrong entity, and the badge it produced ("not supported") would have told a
user no eligibility check was possible for a member whose check is in fact routable.

Evidence, 2026-08-12: Publix is a BRAND ON BCBS SOUTH CAROLINA'S OWN SYSTEMS, not a standalone payer.
Its provider pages are served from BCBS SC's provider site —
`provider.bcbssc.com/web/public/brands/publix/` (BlueCard, Forms, Education Center) — and its member
portal is BCBS SC's My Health Toolkit at `unit-4.myhealthtoolkit.com/.../brands/publix`. Stedi's own
network page for NYNYD (BlueCross BlueShield of South Carolina, payer id 00401) documents real-time
and batch 270/271 eligibility as supported. `J1897` is the employer record, which is why Stedi cannot
run an eligibility check against it.

So the row is repointed at 00401 / supported, matching the other three BCBS SC markets.

⚠ ONE RESIDUAL UNCERTAINTY, DELIBERATELY LEFT VISIBLE RATHER THAN RESOLVED BY GUESSWORK. The Publix
book is co-branded: the Zelis directory tenant renders "Florida Blue + Publix", the same brand pages
are also served from Florida Blue's `myhealthplanner.com`, and the network files behind it are BCBS
Florida's (Blue Choice PPO / NetworkBlue). Under BlueCard the 270 answers at the member's HOME plan,
which the member ID's alpha prefix decides — so a Publix member whose home plan is Florida Blue may
need Florida Blue's payer id instead. 00401 is the better-evidenced default, not a certainty; if a
270 to it returns a member-not-found AAA, check the alpha prefix before assuming the member is
inactive.

Idempotent: only touches the Publix row, and only while it still carries J1897.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0045_publix_bcbssc"
down_revision = "0044_stedi_payer_ids"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().execute(
        sa.text(
            """
            UPDATE payers
               SET stedi_payer_id = '00401',
                   enrollment_status = 'supported',
                   network_indicator_supported = TRUE
             WHERE tenant_id IS NULL
               AND key LIKE 'bcbs-south-carolina-publix%'
               AND stedi_payer_id = 'J1897'
            """
        )
    )


def downgrade() -> None:
    op.get_bind().execute(
        sa.text(
            """
            UPDATE payers
               SET stedi_payer_id = 'J1897',
                   enrollment_status = 'not_supported',
                   network_indicator_supported = FALSE
             WHERE tenant_id IS NULL
               AND key LIKE 'bcbs-south-carolina-publix%'
            """
        )
    )
