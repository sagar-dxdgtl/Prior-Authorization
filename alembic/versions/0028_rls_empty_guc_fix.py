"""Harden RLS policies against an empty-string app.tenant_id GUC.

Migration 0002 fixed this for `payers`: after a `tenant_session` transaction ends, the LOCAL
`app.tenant_id` GUC resets to '' (empty), and a policy that casts it directly with
`current_setting('app.tenant_id', true)::uuid` THROWS `invalid input syntax for type uuid: ""`.
That aborts the query — so a read of a *global-readable* table (tenant_id NULL rows) from a session
that isn't inside a tenant_session (e.g. ProviderNetworkStore / PlanBenefits) fails intermittently.

`provider_network_facts` (0025) and `plan_benefits` (0026) shipped the un-hardened form and inherited
the bug. Symptom: the TiC-derived physician-OON signal (group_contracted) silently returned empty in
the API request context, so Munar/Oscar showed UNKNOWN instead of PHYSICIAN_OON. Fix = the same
`NULLIF(current_setting('app.tenant_id', true), '')::uuid` coercion payers/payer_directory_entries use
('' -> NULL -> global rows stay visible, tenant rows still isolated).

Idempotent + additive: only DROP/CREATE POLICY (no data/schema change).
"""

from __future__ import annotations

from alembic import op

revision: str = "0028_rls_empty_guc_fix"
down_revision: str | None = "0027_blue_shield_california_az"
branch_labels = None
depends_on = None

_HARDENED = "(tenant_id IS NULL OR tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
_VULNERABLE = "(tenant_id IS NULL OR tenant_id = current_setting('app.tenant_id', true)::uuid)"

_TABLES = [
    ("provider_network_facts", "provider_network_facts_isolation"),
    ("plan_benefits", "plan_benefits_isolation"),
]


def upgrade() -> None:
    for table, policy in _TABLES:
        op.execute(f"DROP POLICY IF EXISTS {policy} ON {table}")
        op.execute(f"CREATE POLICY {policy} ON {table} USING {_HARDENED}")


def downgrade() -> None:
    for table, policy in _TABLES:
        op.execute(f"DROP POLICY IF EXISTS {policy} ON {table}")
        op.execute(f"CREATE POLICY {policy} ON {table} USING {_VULNERABLE}")
