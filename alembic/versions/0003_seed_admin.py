"""seed demo tenant + admin"""
from sqlalchemy import text

from alembic import op
from network_probe.db.seed import ensure_demo_tenant_admin

revision = "0003_seed_admin"
down_revision = "0002_seed_payers"
branch_labels = None
depends_on = None

def upgrade():
    ensure_demo_tenant_admin(op.get_bind())

def downgrade():
    conn = op.get_bind()
    conn.execute(text("DELETE FROM users WHERE lower(username)='admin'"))
    conn.execute(text("DELETE FROM tenants WHERE slug='demo'"))
