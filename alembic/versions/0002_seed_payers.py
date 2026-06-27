"""seed payer catalogue"""
import uuid

from sqlalchemy import text

from alembic import op
from network_probe.payers.roster_seed import payer_rows

revision = "0002_seed_payers"
down_revision = "6976ba126a06"
branch_labels = None
depends_on = None

_COLS = "id, tenant_id, key, label, benefit_type, state, stedi_payer_id, enrollment_status, network_indicator_supported"
_VALS = ":id, :tenant_id, :key, :label, :benefit_type, :state, :stedi_payer_id, :enrollment_status, :network_indicator_supported"

def upgrade():
    conn = op.get_bind()
    # Fix payers_isolation RLS policy: empty-string GUC ('') after a tenant_session ends would
    # cause  ''::uuid  to throw.  NULLIF coerces ''→NULL so global rows remain visible with no
    # tenant context, while tenant-scoped rows still filter correctly.
    conn.execute(text("DROP POLICY IF EXISTS payers_isolation ON payers"))
    conn.execute(text(
        "CREATE POLICY payers_isolation ON payers USING "
        "(tenant_id IS NULL OR tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
    ))
    conn.execute(text("DELETE FROM payers WHERE tenant_id IS NULL"))
    for r in payer_rows():
        conn.execute(text(f"INSERT INTO payers ({_COLS}) VALUES ({_VALS})"), {"id": str(uuid.uuid4()), **r})

def downgrade():
    op.get_bind().execute(text("DELETE FROM payers WHERE tenant_id IS NULL"))
