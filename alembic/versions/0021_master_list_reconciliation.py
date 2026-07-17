"""seed payer catalogue: master-list reconciliation (FL-Tampa / NJ markets + NY/FL back-fill)

Reconciles the global payer catalogue to the client's full market master list. Source of truth is
``network_probe.payers.roster_seed.payer_rows`` (expanded 2026-07-17). This migration inserts every
roster row that isn't already present, which covers two groups:

  1. **New rows from the master list** — the two entirely-new markets FL-Tampa / NJ-UVC /
     NJ-Vascular Health, plus back-fill for NY (previously only EmblemHealth + UMR), FL-South
     Florida, and CO-Denver (Clear Spring). ~106 rows.
  2. **File-ahead stragglers** — 11 rows that were added to ``roster_seed`` after their market's
     original insert migration ran and so were never seeded into the DB: all UMR rows, the HCSC-IL
     Managed Medicaid row (G00621), and ``UnitedHealthcare Medicare Advantage / FL-South Florida``.
     That last gap is why a state=FL payer search returned a Texas UHC row instead of the FL one.

The insert is guarded by ``WHERE NOT EXISTS`` on (tenant_id IS NULL, key, benefit_type, state) so
it is idempotent and safe to run against a partially-populated catalogue — it never duplicates an
existing row. Known national Stedi ids are reused verbatim; state-specific government/regional ids
are NOT reused across states (they stay needs_payer_id), matching this file's honesty policy.

Revision ID: 0021_master_list_reconciliation
Revises: 0020_meridian_public_fhir
Create Date: 2026-07-17
"""

import uuid
from collections.abc import Sequence

from sqlalchemy import text

from alembic import op
from network_probe.payers.roster_seed import payer_rows

revision: str = "0021_master_list_reconciliation"
down_revision: str | None = "0020_meridian_public_fhir"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLS = (
    "id, tenant_id, key, label, benefit_type, state, stedi_payer_id, enrollment_status, "
    "network_indicator_supported, fhir_base_url, tic_url, directory_url, directory_access"
)
_VALS = (
    ":id, :tenant_id, :key, :label, :benefit_type, :state, :stedi_payer_id, :enrollment_status, "
    ":network_indicator_supported, :fhir_base_url, :tic_url, :directory_url, :directory_access"
)
_INSERT = text(f"INSERT INTO payers ({_COLS}) VALUES ({_VALS})")
# Idempotency guard: skip a row that already exists for (key, benefit_type, state) among the global
# (tenant_id IS NULL) catalogue. Done as a separate SELECT rather than INSERT...SELECT so the bind
# params coerce to the varchar columns (Postgres types projected bind params as `text`, which the
# INSERT...SELECT form then rejects against varchar columns).
_EXISTS = text(
    "SELECT 1 FROM payers WHERE tenant_id IS NULL AND key = :key "
    "AND benefit_type = :benefit_type AND state = :state LIMIT 1"
)

# The two brand-new markets this migration introduces. Downgrade removes them cleanly (mirrors the
# by-state downgrade in 0015). The rows back-filled into pre-existing markets (NY / FL-South Florida
# / CO-Denver) and the file-ahead stragglers are intentionally NOT reverted — they close genuine
# catalogue gaps (including data that should have been seeded all along), so un-seeding them on a
# downgrade would only re-introduce known-missing rows.
_NEW_STATES = ["FL-Tampa", "NJ-UVC", "NJ-Vascular Health"]


def upgrade() -> None:
    conn = op.get_bind()
    for r in payer_rows():
        already = conn.execute(
            _EXISTS, {"key": r["key"], "benefit_type": r["benefit_type"], "state": r["state"]}
        ).first()
        if already:
            continue
        conn.execute(_INSERT, {"id": str(uuid.uuid4()), **r})


def downgrade() -> None:
    op.get_bind().execute(
        text("DELETE FROM payers WHERE tenant_id IS NULL AND state = ANY(:states)"),
        {"states": _NEW_STATES},
    )
