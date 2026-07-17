"""seed payer catalogue: validated Stedi eligibility ids for the long-tail payers

Fills in eligibility payer ids for roster payers that had none, each validated against Stedi's
authoritative payer directory (2026-07-18): required an exact/strong name match, the roster's state
in the Stedi payer's operatingStates, and eligibilityCheck=SUPPORTED. The Centene family id (68069)
is additionally web-confirmed as Ambetter/Wellcare/AllWell/Superior's payer id (incl. via Availity).

These are directory-validated but NOT yet member-verified, so they land as needs_enrollment. Only
rows still lacking an id are touched (WHERE stedi_payer_id IS NULL); Molina and UHC Community Plan
are per-state. Genuinely unresolved payers (no supported Stedi match, or no eligibility support:
Abilis, Claritev, Clear Spring, Community Care Plan, DES/DDD, EternalHealth, First Health, Gold
Kidney/Multiplan, MCC Health, Partners Direct, SECUR, SelectHealth, Solis, Ultimate) are left as-is.

Revision ID: 0024_validated_eligibility_ids
Revises: 0023_eligibility_payer_ids
Create Date: 2026-07-18
"""

from collections.abc import Sequence

from sqlalchemy import text

from alembic import op

revision: str = "0024_validated_eligibility_ids"
down_revision: str | None = "0023_eligibility_payer_ids"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NJ_DMAHS = (
    "New Jersey Department of Human Services - Division of Medical Assistance and Health Services (DMAHS)"
)

# (label, state_prefix_or_None, stedi_id) — state None updates all of the label's null-id rows.
_UPDATES = [
    ("Healthspring", None, "63092"),
    # UMR intentionally omitted: it has no single Stedi id (per-self-funded-employer routing) and is
    # deliberately kept needs_payer_id (see roster_seed SOURCES note + test_umr_seeded_in_every_market).
    ("Wellpoint / Amerigroup (Elevance)", None, "WLPNT"),
    ("Wellcare (Centene)", None, "68069"),
    ("Superior HealthPlan (Centene)", None, "68069"),
    ("Sunshine State Health Plan (Centene)", None, "68069"),
    ("WellCare / AllWell (Centene)", None, "68069"),
    ("Wellcare (Centene) / Fidelis", None, "68069"),
    ("Ambetter (Centene) / Fidelis", None, "68069"),
    ("Align Senior Health Plan", None, "ASFL1"),
    ("Alliant Health Plans", None, "58234"),
    ("AmeriHealth Caritas", None, "88232"),
    ("AmeriHealth NJ", None, "95044"),
    ("Anthem / Empire BCBS (HealthPlus)", None, "803"),
    ("BCBS / Braven (Anthem / Elevance)(HCSC)", None, "84367"),
    ("BCBS / Horizon (Anthem / Elevance)(HCSC)", None, "22099"),
    ("Baycare", None, "81079"),
    ("CareSource", None, "CRSCK"),
    ("Clover Health", None, "CRPHP"),
    ("Elevance Health - Simply", None, "SMPLY"),
    ("Simply", None, "SMPLY"),
    ("Essence Healthcare", None, "20818"),
    ("Florida Agency for Health Care Administration (AHCA)", None, "77027"),
    ("Health Choice / BCBS / (Anthem / Elevance)", None, "RP105"),
    ("Illinois Department of Healthcare and Family Services (HFS)", None, "IL621"),
    ("Kaiser Foundation Health Plan of Georgia", None, "21313"),
    ("New York State Department of Health (NYSDOH)", None, "MCDNY"),
    (_NJ_DMAHS, None, "100229"),
    ("Provider Partners", None, "31401"),
    ("Texas Health and Human Services Commission (HHSC)", None, "86916"),
    ("Zing Health", None, "83248"),
    ("Molina Healthcare", "AZ", "A4353"),
    ("Molina Healthcare", "FL", "51062"),
    ("Molina Healthcare", "TX", "MLNTX"),
    ("UnitedHealthcare Community Plan", "NJ", "86047"),
]

_ALL = text(
    "UPDATE payers SET stedi_payer_id = :sid, enrollment_status = 'needs_enrollment' "
    "WHERE tenant_id IS NULL AND label = :label AND stedi_payer_id IS NULL"
)
_BY_STATE = text(
    "UPDATE payers SET stedi_payer_id = :sid, enrollment_status = 'needs_enrollment' "
    "WHERE tenant_id IS NULL AND label = :label AND state LIKE :st AND stedi_payer_id IS NULL"
)


def upgrade() -> None:
    conn = op.get_bind()
    for label, st, sid in _UPDATES:
        if st is None:
            conn.execute(_ALL, {"sid": sid, "label": label})
        else:
            conn.execute(_BY_STATE, {"sid": sid, "label": label, "st": f"{st}%"})


def downgrade() -> None:
    conn = op.get_bind()
    for label, st, sid in _UPDATES:
        clause = "AND state LIKE :st " if st else ""
        params = {"sid": sid, "label": label}
        if st:
            params["st"] = f"{st}%"
        conn.execute(
            text(
                "UPDATE payers SET stedi_payer_id = NULL, enrollment_status = 'needs_payer_id' "
                f"WHERE tenant_id IS NULL AND label = :label {clause}AND stedi_payer_id = :sid"
            ),
            params,
        )
