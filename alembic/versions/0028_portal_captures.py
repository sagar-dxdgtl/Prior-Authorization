"""portal_captures: append-only evidence log of live payer-portal captures

One row per capture attempt against a payer's own find-a-doctor portal, including attempts that
produced no answer — a BLOCKED capture with a screenshot of the block is evidence too. There is
deliberately NO unique constraint: this is a history of what the payer's directory said and when, not
a current-fact table, so rows are never updated in place and never deduplicated. That history is the
artifact a No Surprises Act directory-accuracy dispute turns on (PHSA §2799B-9: 90-day verification
duty, one-business-day network-status response, member held harmless at in-network cost share when the
directory is wrong).

`status` holds the portal vocabulary (IN_NETWORK / OUT_OF_NETWORK / UNKNOWN / BLOCKED) instead of a
bare boolean, so "the portal refused automated access" can never collapse into out-of-network;
`in_network` is a nullable mirror, NULL for both non-answers. `plan_pinned` / `plan_match_basis`
record which plan was actually selected and why, because the verdict is only valid for that plan.

Contains no PHI — provider, clinic and plan data only. Global rows (tenant_id NULL) are readable by
every tenant, like `payers`. Idempotent + additive, matching 0025.

NOTE on the RLS predicate: this uses NULLIF(current_setting(...), '') so an empty-string GUC is
treated as "no tenant" rather than raising on the ::uuid cast. Existing tables predate that guard.

Revision ID: 0028_portal_captures
Revises: 0027_blue_shield_california_az
Create Date: 2026-07-28
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0028_portal_captures"
down_revision: str | None = "0027_blue_shield_california_az"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS portal_captures (
            id                UUID PRIMARY KEY,
            tenant_id         UUID REFERENCES tenants(id),
            payer_key         VARCHAR(120) NOT NULL,
            npi               VARCHAR(10) NOT NULL,
            tin               VARCHAR(20),
            status            VARCHAR(20) NOT NULL,
            in_network        BOOLEAN,
            plan              VARCHAR(200),
            plan_pinned       VARCHAR(200),
            plan_match_basis  VARCHAR(400),
            portal_name       VARCHAR(160) NOT NULL,
            portal_url        VARCHAR(400),
            driver            VARCHAR(60),
            screenshot        VARCHAR(200),
            result_count      INTEGER,
            matched_name      VARCHAR(200),
            reachability      VARCHAR(20),
            duration_ms       INTEGER,
            walk_trail        VARCHAR(700),
            note              VARCHAR(2000),
            captured_at       TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    # No unique index by design — see the module docstring. These support the read paths: "latest
    # capture for this provider at this payer" and "everything captured in a demo window".
    op.execute("CREATE INDEX IF NOT EXISTS ix_pc_payer_npi ON portal_captures (payer_key, npi)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_pc_npi ON portal_captures (npi)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_pc_captured_at ON portal_captures (captured_at DESC)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_pc_tenant ON portal_captures (tenant_id)")

    # RLS: global rows (tenant_id NULL) visible to all; tenant rows isolated — mirrors `payers`.
    op.execute("ALTER TABLE portal_captures ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE portal_captures FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS portal_captures_isolation ON portal_captures")
    op.execute(
        "CREATE POLICY portal_captures_isolation ON portal_captures USING "
        "(tenant_id IS NULL OR tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
    )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON portal_captures TO preauth_app")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS portal_captures CASCADE")
