"""provider_network_facts: real contract dates + the source's own build date

Two gaps that a boolean network fact cannot express, both found live on 2026-07-29.

**1. The verdict is date-of-service scoped, and nothing recorded the dates.** Oscar's `network_infos`
gives start/end per network. For Ins Test 3 row 3 (Sanders, NPI 1700846789, net 065) it reads:

    2019-02-06 -> 2026-01-26   in_network = False
    2026-01-27 -> 2026-12-31   in_network = TRUE
    2027-01-01 -> (open)       in_network = False    <- terminates during the coverage year

So "is he in-network?" has no answer without "on what date". A bare IN reported today is already
WRONG for a January date of service — five months out. The existing `effective_date` is a free-text
VARCHAR(40) and cannot be compared; it is kept for provenance and `start_date`/`end_date` added
alongside it as the machine-comparable form.

**2. `retrieved_at` is when WE wrote the row, not when the SOURCE was built.** Telling a stale source
from a genuine conflict needs the latter: row 3's directory-vs-TiC disagreement resolved only because
the contract began 2026-01-27, *after* the MRF was built. `source_built_at` records an MRF's publish
date or a directory read time so `domain.fact_reconcile` can make that comparison.

All three columns are NULLable and most sources leave them empty — only Oscar dates its facts today.
A NULL means "undated", never "expired": the read path must return undated facts for any as-of date,
because absence of dating is not evidence a fact has lapsed.

Additive and idempotent (ADD COLUMN IF NOT EXISTS), matching 0025/0028/0036/0037. No backfill: the
existing rows genuinely have no dates, and inventing one would be worse than leaving it NULL.

Revision ID: 0038_pnf_effective_dates
Revises: 0037_pnf_network_identity
Create Date: 2026-07-29
"""

from __future__ import annotations

from alembic import op

revision = "0038_pnf_effective_dates"
down_revision = "0037_pnf_network_identity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE provider_network_facts ADD COLUMN IF NOT EXISTS start_date DATE")
    op.execute("ALTER TABLE provider_network_facts ADD COLUMN IF NOT EXISTS end_date DATE")
    op.execute(
        "ALTER TABLE provider_network_facts ADD COLUMN IF NOT EXISTS source_built_at TIMESTAMPTZ"
    )
    # The read path filters on the effective window for a given date of service.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_pnf_effective ON provider_network_facts "
        "(payer_key, npi, start_date, end_date)"
    )
    # A network has a TIMELINE, so the period is part of a fact's identity too. 0037 added the network
    # to the key; caught end-to-end on real Oscar data, net 065's three periods (OUT to 2026-01-26,
    # IN from 2026-01-27, OUT from 2027-01-01) still collapsed to one row — and the EXPIRED period won,
    # leaving the network with no fact effective today. Same defect as 0037, one level down.
    op.execute("DROP INDEX IF EXISTS uq_pnf_identity")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_pnf_identity ON provider_network_facts "
        "(tenant_id, payer_key, npi, tin, source, network_name, start_date)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_pnf_identity")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_pnf_identity ON provider_network_facts "
        "(tenant_id, payer_key, npi, tin, source, network_name)"
    )
    op.execute("DROP INDEX IF EXISTS ix_pnf_effective")
    op.execute("ALTER TABLE provider_network_facts DROP COLUMN IF EXISTS source_built_at")
    op.execute("ALTER TABLE provider_network_facts DROP COLUMN IF EXISTS end_date")
    op.execute("ALTER TABLE provider_network_facts DROP COLUMN IF EXISTS start_date")
