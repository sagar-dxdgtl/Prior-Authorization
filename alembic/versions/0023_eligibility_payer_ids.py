"""seed payer catalogue: eligibility payer ids for BCBS-AZ / BCBS-GA / Kaiser-CO / Mercy Care

Wires Stedi eligibility payer ids (all eligibilityCheck=SUPPORTED, found via the Stedi payer
directory and validated live 2026-07-18 against the client's own members) for rows that previously
had none, so their 270/271 eligibility runs:

  - AZ "BCBS / Empire (Anthem / Elevance)"  -> 53589  (Blue Cross Blue Shield of Arizona / "AZ Blue")
  - GA "BCBS / Empire (Anthem / Elevance)"  -> 00601  (Anthem Blue Cross Blue Shield of Georgia)
  - CO-Denver "Kaiser Permanente"           -> 100173 (Kaiser Foundation Health Plan Colorado)
  - AZ "Mercy Care"                         -> 86052  (Mercy Care Plan / AHCCCS; replaces 33628 =
                                                        the ACC-RBHA sub-product, which returned AAA-75
                                                        for the client's real member)

Directory routing is unchanged (BCBS-AZ stays needs-authorized-api; BCBS-GA keeps its Anthem
authorized-FHIR override) — this only sets the eligibility id + flips needs_payer_id ->
needs_enrollment.

Revision ID: 0023_eligibility_payer_ids
Revises: 0022_devoted_co_il_txdallas
Create Date: 2026-07-18
"""

from collections.abc import Sequence

from sqlalchemy import text

from alembic import op

revision: str = "0023_eligibility_payer_ids"
down_revision: str | None = "0022_devoted_co_il_txdallas"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (label, state, new_stedi_id, old_stedi_id_for_downgrade, old_enrollment_for_downgrade)
_UPDATES = [
    ("BCBS / Empire (Anthem / Elevance)", "AZ", "53589", None, "needs_payer_id"),
    ("BCBS / Empire (Anthem / Elevance)", "GA-Atlanta", "00601", None, "needs_payer_id"),
    ("Kaiser Permanente", "CO-Denver", "100173", None, "needs_payer_id"),
    ("Mercy Care", "AZ", "86052", "33628", "needs_enrollment"),
]

_SET = text(
    "UPDATE payers SET stedi_payer_id = :sid, enrollment_status = 'needs_enrollment' "
    "WHERE tenant_id IS NULL AND label = :label AND state = :state"
)
_UNSET = text(
    "UPDATE payers SET stedi_payer_id = :sid, enrollment_status = :enroll "
    "WHERE tenant_id IS NULL AND label = :label AND state = :state"
)


def upgrade() -> None:
    conn = op.get_bind()
    for label, state, sid, _old_sid, _old_enroll in _UPDATES:
        conn.execute(_SET, {"sid": sid, "label": label, "state": state})


def downgrade() -> None:
    conn = op.get_bind()
    for label, state, _sid, old_sid, old_enroll in _UPDATES:
        conn.execute(_UNSET, {"sid": old_sid, "enroll": old_enroll, "label": label, "state": state})
