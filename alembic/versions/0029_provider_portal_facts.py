"""provider_portal_facts: authoritative member-facing find-a-doctor result (overrides FHIR) + screenshot

The payer provider portals (Meridian Find-a-Provider, UHC Find Care, AZ Blue / HealthSparq,
Medicare.gov Care Compare) are the accurate network source — the CMS machine-readable FHIR we query
over-includes (false INs) and the TiC we ingest has gaps. This global-readable table (like `payers`:
tenant_id NULL rows visible to all) persists a portal-verified fact per (payer, NPI) that OVERRIDES
the FHIR/directory-derived status, with the portal `screenshot` (a file under api/static/portal/) as
proof. Seeded here with the Test2 portal checks; the live-portal-API pivot writes the same rows.

Idempotent + additive: CREATE TABLE/INDEX IF NOT EXISTS, policy dropped-then-created (hardened NULLIF
form), grants re-applied, global seed rows DELETE-then-INSERT. RLS mirrors `payers`.

Revision ID: 0029_provider_portal_facts
Revises: 0028_rls_empty_guc_fix
"""

from collections.abc import Sequence

from sqlalchemy import text

from alembic import op
from network_probe.payers.portal_seed import portal_fact_rows

revision: str = "0029_provider_portal_facts"
down_revision: str | None = "0028_rls_empty_guc_fix"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLS = (
    "id, tenant_id, payer_key, npi, tin, plan, in_network, provider_name, member, "
    "portal_name, portal_url, screenshot, verified_by, verified_at, note"
)
_VALS = (
    ":id, NULL, :payer_key, :npi, :tin, :plan, :in_network, :provider_name, :member, "
    ":portal_name, :portal_url, :screenshot, :verified_by, :verified_at, :note"
)


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS provider_portal_facts (
            id             UUID PRIMARY KEY,
            tenant_id      UUID REFERENCES tenants(id),
            payer_key      VARCHAR(120) NOT NULL,
            npi            VARCHAR(10)  NOT NULL,
            tin            VARCHAR(20),
            plan           VARCHAR(200),
            in_network     BOOLEAN NOT NULL,
            provider_name  VARCHAR(160),
            member         VARCHAR(160),
            portal_name    VARCHAR(160) NOT NULL,
            portal_url     VARCHAR(400),
            screenshot     VARCHAR(200),
            verified_by    VARCHAR(120),
            verified_at    VARCHAR(40),
            note           VARCHAR(700),
            retrieved_at   TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_ppf_identity ON provider_portal_facts "
        "(tenant_id, payer_key, npi, plan)"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_ppf_npi ON provider_portal_facts (npi)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_ppf_payer_npi ON provider_portal_facts (payer_key, npi)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_ppf_tenant ON provider_portal_facts (tenant_id)")

    # RLS: global rows (tenant_id NULL) visible to all; tenant rows isolated. Hardened NULLIF form so
    # an empty-string app.tenant_id GUC ('' after a tenant_session ends) coerces to NULL, not a cast error.
    op.execute("ALTER TABLE provider_portal_facts ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE provider_portal_facts FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS provider_portal_facts_isolation ON provider_portal_facts")
    op.execute(
        "CREATE POLICY provider_portal_facts_isolation ON provider_portal_facts USING "
        "(tenant_id IS NULL OR tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
    )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON provider_portal_facts TO preauth_app")

    # Seed the global (tenant_id NULL) portal-verified rows, idempotently.
    conn = op.get_bind()
    conn.execute(text("DELETE FROM provider_portal_facts WHERE tenant_id IS NULL"))
    for i, r in enumerate(portal_fact_rows()):
        params = {
            "id": _seed_uuid(i),
            "payer_key": r["payer_key"],
            "npi": str(r["npi"]),
            "tin": r.get("tin"),
            "plan": r.get("plan"),
            "in_network": bool(r["in_network"]),
            "provider_name": r.get("provider_name"),
            "member": r.get("member"),
            "portal_name": r.get("portal_name") or "provider portal",
            "portal_url": r.get("portal_url"),
            "screenshot": r.get("screenshot"),
            "verified_by": r.get("verified_by"),
            "verified_at": r.get("verified_at"),
            "note": r.get("note"),
        }
        conn.execute(text(f"INSERT INTO provider_portal_facts ({_COLS}) VALUES ({_VALS})"), params)


def _seed_uuid(i: int) -> str:
    """Deterministic UUID per seed row (migrations must be reproducible — no random uuid4)."""
    return f"00000000-0000-4000-a000-0000000000{i:02d}"


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS provider_portal_facts CASCADE")
