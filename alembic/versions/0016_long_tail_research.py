"""directory catalogue: long-tail IL/GA/TX payer research findings (2026-07-06)

Re-syncs the 12 previously-unresearched payers from the IL/GA-Atlanta/TX-Houston/TX-Dallas
markets (0015) with their actual research findings: confirmed Stedi ids (Longevity Health Plan,
Memorial Hermann HP, Baylor Scott & White Health Plan), corrected notes/URLs, and flags for two
rows that turned out not to correspond to a real payer in that state (MCC Health, Abilis Health
Plan) or a plan that has since wound down (Clear Spring Health). See docs/payer-sources/MATRIX.md
and SIGNUP-CHECKLIST.md for full sourcing.

Revision ID: 0016_long_tail_research
Revises: 0015_il_ga_tx_markets
Create Date: 2026-07-06
"""

from collections.abc import Sequence

from sqlalchemy import text

from alembic import op
from network_probe.payers.roster_seed import payer_rows

revision: str = "0016_long_tail_research"
down_revision: str | None = "0015_il_ga_tx_markets"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_RESEARCHED_LABELS = {
    "Essence Healthcare",
    "Longevity Health Plan",
    "Clear Spring Health",
    "Zing Health",
    "Provider Partners",
    "Alliant Health Plans",
    "Memorial Hermann HP",
    "Baylor Scott & White Health Plan",
    "MCC Health",
    "Abilis Health Plan",
    "CareSource",
    "BCBS (Anthem)",
}

_UPDATE = text(
    "UPDATE payers SET "
    "fhir_base_url=:fhir_base_url, tic_url=:tic_url, directory_url=:directory_url, "
    "directory_access=:directory_access, stedi_payer_id=:stedi_payer_id, "
    "enrollment_status=:enrollment_status, network_indicator_supported=:network_indicator_supported "
    "WHERE tenant_id IS NULL AND label=:label AND state=:state "
    "AND benefit_type IS NOT DISTINCT FROM :benefit_type"
)


def upgrade() -> None:
    conn = op.get_bind()
    for r in payer_rows():
        if r["label"] in _RESEARCHED_LABELS:
            conn.execute(_UPDATE, r)


def downgrade() -> None:
    # No historical values to restore to (0015 already had placeholder Nones for these labels) —
    # this migration is additive-only (research findings), not reversible to a prior state.
    pass
