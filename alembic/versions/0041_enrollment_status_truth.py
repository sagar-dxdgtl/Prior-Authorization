"""payers.enrollment_status — correct it from Stedi's own transactionSupport

The payer picker showed an amber "needs enrollment" badge on **163 of 306 roster rows**, including
every Cigna market. It was wrong on all of them. `roster_seed.py` says so itself — "best-guess for
Aetna/Ambetter/Cigna (resolver confirms)" — and the guess was never reconciled.

Stedi publishes the answer per payer at `GET /2024-04-01/payers` as
`transactionSupport.eligibilityCheck`, one of SUPPORTED | ENROLLMENT_REQUIRED | NOT_SUPPORTED.
Measured 2026-08-03 against the live payer list (3661 payers): Cigna (62308) is **SUPPORTED**. Its
`enrollment` block does carry a requirement — but for `claimPayment`, a transaction this product does
not perform. Reading a claim-payment enrollment requirement as if it gated an eligibility check is
what produced the false badge.

Audited across the whole roster:

    our status        stedi eligibilityCheck    rows
    needs_enrollment  SUPPORTED                  163   <- wrong, 51 distinct payer brands
    needs_payer_id    (no stedi id)               79   <- untouched, genuinely unresolved
    supported         SUPPORTED                   54   <- already right
    needs_enrollment  ENROLLMENT_REQUIRED          5   <- right, kept
    needs_enrollment  NOT_SUPPORTED                2   <- wrong LABEL: not supported at all
    needs_enrollment  (no stedi match)             3   <- untouched

`not_supported` is a new value, and a distinct fact from `needs_enrollment`: enrolling would not help,
because Stedi cannot run an eligibility check against that payer at all. The UI renders it as its own
badge rather than collapsing it back into the amber one.

The mapping is INLINED rather than fetched. A migration must not depend on the network or on a live
API key, and a re-run months from now must produce the same rows it produced today. Regenerate with
`scripts/refresh_enrollment_status.py` when the roster grows.

Scoped by `stedi_payer_id` and only ever writes a row whose status actually differs, so it is
idempotent and re-runnable. Tenant-scoped payer rows are updated too — they share the payer id.

`enrollment_status` is DISPLAY-ONLY (`web/src/pages/Eligibility.tsx` badge); nothing gates on it, so
this changes no verdict.

Additive and idempotent, matching 0025/0028/0036/0037/0038/0039/0040.

Revision ID: 0041_enrollment_status_truth
Revises: 0040_portal_capture_networks
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0041_enrollment_status_truth"
down_revision = "0040_portal_capture_networks"
branch_labels = None
depends_on = None

STATUS_BY_PAYER_ID = {
    "00601": "supported",
    "03102": "needs_enrollment",
    "06102": "needs_enrollment",
    "09102": "needs_enrollment",
    "100173": "supported",
    "100229": "supported",
    "100935": "supported",
    "13189": "supported",
    "13551": "supported",
    "20818": "supported",
    "21313": "supported",
    "22099": "supported",
    "31401": "supported",
    "51062": "supported",
    "53589": "supported",
    "58234": "supported",
    "59274": "supported",
    "60054": "supported",
    "60495": "supported",
    "61101": "supported",
    "62308": "supported",
    "63092": "supported",
    "68069": "supported",
    "77027": "supported",
    "803": "supported",
    "81079": "supported",
    "83248": "supported",
    "84367": "supported",
    "86047": "supported",
    "86052": "supported",
    "86916": "supported",
    "87726": "supported",
    "88030": "supported",
    "88232": "supported",
    "95044": "supported",
    "A4353": "supported",
    "A6865": "not_supported",
    "ABH01": "supported",
    "ASFL1": "supported",
    "CCHPC": "supported",
    "CRPHP": "supported",
    "CRSCK": "supported",
    "CURTV": "supported",
    "DEVOT": "supported",
    "G00621": "supported",
    "IL621": "supported",
    "LIL01": "not_supported",
    "MCDNY": "supported",
    "MLNTX": "supported",
    "OSCAR": "supported",
    "RP105": "supported",
    "SKCO0": "supported",
    "SMPLY": "supported",
    "SPSCN": "supported",
    "TDFIC": "supported",
    "TMDSA": "supported",
    "WLPNT": "supported",
}


def upgrade() -> None:
    conn = op.get_bind()
    for payer_id, status in STATUS_BY_PAYER_ID.items():
        conn.execute(
            sa.text(
                "UPDATE payers SET enrollment_status = :s "
                "WHERE stedi_payer_id = :p AND enrollment_status IS DISTINCT FROM :s"
            ),
            {"s": status, "p": payer_id},
        )


def downgrade() -> None:
    # The prior values were a guess that was wrong on 163 rows; restoring them would reintroduce the
    # false badge. Rows keep the measured status.
    pass
