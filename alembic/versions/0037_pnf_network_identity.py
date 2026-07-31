"""provider_network_facts: network_name becomes part of the row identity

The provider-first reverse lookup (HANDOFF-2026-07-29 §2) reads a provider ONCE and gets their
participation across EVERY network the payer sells. Oscar's profile for NPI 1700846789 (Sanders,
Ins Test 3 row 3) returns, from a single fetch:

    net 065  TIN 921600050  in_network = True
    net 082  TIN 921600050  in_network = False
    net 083  TIN 921600050  in_network = False

Same payer, same NPI, same TIN, same source — three different networks. `uq_pnf_identity` was
(tenant_id, payer_key, npi, tin, source), so those are ONE row by the old identity and only the first
could be stored. Two consequences, both bad:

  * the provider's network SET collapsed to a single fact, and
  * which fact survived depended on iteration order — reversed, the store keeps "082 False" and
    silently drops "065 True", turning an in-network provider into an out-of-network answer.

The OUT rows are not noise either: "in no network of this payer" is what makes an absence decisive,
and that conclusion needs every network's fact, not one of them.

`uq_pnf_identity` is a UNIQUE INDEX, not a table constraint — 0025 creates it with raw
`CREATE UNIQUE INDEX`, so it lives in pg_indexes and `op.drop_constraint` cannot see it. This
migration uses the same idiom, which is idempotent by construction (DROP ... IF EXISTS /
CREATE ... IF NOT EXISTS), matching 0025/0028/0036.

Note `network_name` is nullable and Postgres treats NULLs as distinct in a unique index, so this does
NOT de-duplicate the network-less TiC crosswalk rows. That is unchanged behaviour: de-duplication has
always been done in `ProviderNetworkStore.upsert`, which now keys on the network too. The index is the
backstop, not the mechanism.

Revision ID: 0037_pnf_network_identity
Revises: 0036_portal_url_widen
Create Date: 2026-07-29
"""

from __future__ import annotations

from alembic import op

revision = "0037_pnf_network_identity"
down_revision = "0036_portal_url_widen"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_pnf_identity")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_pnf_identity ON provider_network_facts "
        "(tenant_id, payer_key, npi, tin, source, network_name)"
    )


def downgrade() -> None:
    # Reverting can violate the narrower index once a provider's full network set has been stored, so
    # collapse to one row per old identity first — keeping the in-network fact, which is the one a
    # caller would have seen under the old behaviour.
    op.execute("DROP INDEX IF EXISTS uq_pnf_identity")
    op.execute(
        """
        DELETE FROM provider_network_facts a
        USING provider_network_facts b
        WHERE a.id <> b.id
          AND a.tenant_id IS NOT DISTINCT FROM b.tenant_id
          AND a.payer_key = b.payer_key
          AND a.npi IS NOT DISTINCT FROM b.npi
          AND a.tin = b.tin
          AND a.source = b.source
          AND (b.in_network, b.retrieved_at) > (a.in_network, a.retrieved_at)
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_pnf_identity ON provider_network_facts "
        "(tenant_id, payer_key, npi, tin, source)"
    )
