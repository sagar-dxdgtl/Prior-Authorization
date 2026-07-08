"""directory catalogue: real Stedi trading-partner ids for the 9 UVC demo provider-search payers

Resolves real GET /2024-04-01/payers matches (Stedi's live payer-search API) for the 9 payers the
UVC demo-cases roster previously fell back to manual provider-search research for, because no
Stedi payer id was known: Meridian Health (IL), First Coast Service Options/Traditional Medicare
(FL), Humana (FL — new market, reuses the existing national Humana public-fhir adapter), Mercy
Care (AZ), Community Health Choice Marketplace/ACA (TX-Houston), National Government Services/
Traditional Medicare (IL), Noridian Healthcare Solutions/Traditional Medicare (AZ), and Tricare for
Life (AZ). UnitedHealthcare (AZ) already had a working stedi_payer_id + public-fhir adapter and
needed no catalogue change.

Every id was looked up directly against Stedi's live payer network and verified by exact
state/plan-name match, not accepted from the bare fuzzy-name resolver (which proposes false
positives for these exact payers — e.g. "Noridian" alone resolves to a North Dakota entry; see
scripts/resolve_payer_ids.py's own docstring warning). Four labels are brand new to the roster
(Meridian Health, First Coast Service Options, Humana-FL, Tricare for Life); the rest are updates
to existing needs_payer_id rows. Source of truth is
``network_probe.payers.roster_seed.payer_rows`` — this migration inserts a row if none exists for
(label, state, benefit_type), else updates it in place.

Revision ID: 0017_uvc_demo_stedi_ids
Revises: 0016_long_tail_research
Create Date: 2026-07-08
"""

import uuid
from collections.abc import Sequence

from sqlalchemy import text

from alembic import op
from network_probe.payers.roster_seed import payer_rows

revision: str = "0017_uvc_demo_stedi_ids"
down_revision: str | None = "0016_long_tail_research"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_LABELS = {
    "Meridian Health",
    "First Coast Service Options, Inc.",
    "Mercy Care",
    "Noridian Healthcare Solutions, LLC",
    "National Government Services, Inc. (NGS)",
    "Community Health Choice (CHC)",
    "Tricare for Life",
}
_STATES = {"AZ", "IL", "FL", "TX-Houston"}

_COLS = (
    "id, tenant_id, key, label, benefit_type, state, stedi_payer_id, enrollment_status, "
    "network_indicator_supported, fhir_base_url, tic_url, directory_url, directory_access"
)
_VALS = (
    ":id, :tenant_id, :key, :label, :benefit_type, :state, :stedi_payer_id, :enrollment_status, "
    ":network_indicator_supported, :fhir_base_url, :tic_url, :directory_url, :directory_access"
)
_UPDATE = text(
    "UPDATE payers SET "
    "fhir_base_url=:fhir_base_url, tic_url=:tic_url, directory_url=:directory_url, "
    "directory_access=:directory_access, stedi_payer_id=:stedi_payer_id, "
    "enrollment_status=:enrollment_status, network_indicator_supported=:network_indicator_supported "
    "WHERE tenant_id IS NULL AND label=:label AND state=:state "
    "AND benefit_type IS NOT DISTINCT FROM :benefit_type"
)
_EXISTS = text(
    "SELECT 1 FROM payers WHERE tenant_id IS NULL AND label=:label AND state=:state "
    "AND benefit_type IS NOT DISTINCT FROM :benefit_type"
)
# Humana-FL is a new market row for an already-seeded label — inserted alongside the other new rows.
_NEW_ROWS = {("Humana", "FL")}


def upgrade() -> None:
    conn = op.get_bind()
    rows = [
        r
        for r in payer_rows()
        if r["state"] in _STATES and (r["label"] in _LABELS or (r["label"], r["state"]) in _NEW_ROWS)
    ]
    for r in rows:
        exists = conn.execute(_EXISTS, r).first()
        if exists:
            conn.execute(_UPDATE, r)
        else:
            conn.execute(text(f"INSERT INTO payers ({_COLS}) VALUES ({_VALS})"), {"id": str(uuid.uuid4()), **r})


def downgrade() -> None:
    # The 4 brand-new labels are removable outright; the rest revert to needs_payer_id/None (their
    # pre-migration state) rather than being deleted, since 0015/0016 already created those rows.
    conn = op.get_bind()
    conn.execute(
        text("DELETE FROM payers WHERE tenant_id IS NULL AND label = ANY(:labels)"),
        {"labels": ["Meridian Health", "First Coast Service Options, Inc.", "Tricare for Life"]},
    )
    conn.execute(
        text("DELETE FROM payers WHERE tenant_id IS NULL AND label='Humana' AND state='FL'"),
    )
    conn.execute(
        text(
            "UPDATE payers SET stedi_payer_id=NULL, enrollment_status='needs_payer_id', "
            "network_indicator_supported=false "
            "WHERE tenant_id IS NULL AND label = ANY(:labels)"
        ),
        {
            "labels": [
                "Mercy Care",
                "Noridian Healthcare Solutions, LLC",
                "National Government Services, Inc. (NGS)",
                "Community Health Choice (CHC)",
            ]
        },
    )
