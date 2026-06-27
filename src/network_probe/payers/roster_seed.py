from __future__ import annotations

import re

# (label, benefit_type, state, stedi_payer_id | None, enrollment_status)
# Known Stedi ids seeded for verified payers; best-guess for Aetna/Ambetter/Cigna (resolver confirms);
# everything else None/needs_payer_id (Task 20 resolver fills from Stedi's payer network).
ROSTER = [
    # --- Arizona ---
    ("Aetna", "Commercial", "AZ", "60054", "needs_enrollment"),
    ("Aetna", "Medicare Advantage", "AZ", "60054", "needs_enrollment"),
    ("Alignment Health Plan", "Medicare Advantage", "AZ", None, "needs_payer_id"),
    ("Ambetter (Centene)", "ACA", "AZ", "68069", "needs_enrollment"),
    ("Arizona Complete Health - Complete Care Plan (Centene)", "Managed Medicaid", "AZ", None, "needs_payer_id"),
    ("Arizona Health Care Cost Containment System (AHCCCS)", "Traditional Medicaid", "AZ", None, "needs_payer_id"),
    ("BCBS / Empire (Anthem / Elevance)", "ACA", "AZ", None, "needs_payer_id"),
    ("BCBS / Empire (Anthem / Elevance)", "Commercial", "AZ", None, "needs_payer_id"),
    ("BCBS / Empire (Anthem / Elevance)", "Medicare Advantage", "AZ", None, "needs_payer_id"),
    ("Cigna Healthcare", "ACA", "AZ", "62308", "needs_enrollment"),
    ("Cigna Healthcare", "Commercial", "AZ", "62308", "needs_enrollment"),
    ("DES/Division of Developmental Disabilities", "Managed Medicaid", "AZ", None, "needs_payer_id"),
    ("Devoted Health", "Medicare Advantage", "AZ", "DEVOT", "supported"),
    ("EternalHealth", "Medicare Advantage", "AZ", None, "needs_payer_id"),
    ("Gold Kidney Health Plan", "Medicare Advantage", "AZ", None, "needs_payer_id"),
    ("Health Choice / BCBS / (Anthem / Elevance)", "Managed Medicaid", "AZ", None, "needs_payer_id"),
    ("Healthspring", "Medicare Advantage", "AZ", None, "needs_payer_id"),
    ("Humana", "Medicare Advantage", "AZ", "61101", "supported"),
    ("Mercy Care", "Managed Medicaid", "AZ", None, "needs_payer_id"),
    ("Mercy Care", "Medicare Advantage", "AZ", None, "needs_payer_id"),
    ("Molina Healthcare", "Dual Eligible (FIDE SNP)", "AZ", None, "needs_payer_id"),
    ("Molina Healthcare", "Managed Medicaid", "AZ", None, "needs_payer_id"),
    ("Noridian Healthcare Solutions, LLC", "Traditional Medicare", "AZ", None, "needs_payer_id"),
    ("Oscar", "ACA", "AZ", "OSCAR", "supported"),
    ("Scan", "Medicare Advantage", "AZ", None, "needs_payer_id"),
    ("UnitedHealthcare", "ACA", "AZ", "87726", "supported"),
    ("UnitedHealthcare", "Commercial", "AZ", "87726", "supported"),
    ("UnitedHealthcare", "Medicare Advantage", "AZ", "87726", "supported"),
    ("Wellcare (Centene)", "Medicare Advantage", "AZ", None, "needs_payer_id"),
    ("Wellpoint / Amerigroup (Elevance)", "Medicare Advantage", "AZ", None, "needs_payer_id"),
    # --- Colorado (Denver) ---
    ("Aetna", "Commercial", "CO-Denver", "60054", "needs_enrollment"),
    ("Aetna", "Medicare Advantage", "CO-Denver", "60054", "needs_enrollment"),
    ("BCBS / Empire (Anthem / Elevance)", "ACA", "CO-Denver", None, "needs_payer_id"),
    ("BCBS / Empire (Anthem / Elevance)", "Medicare Advantage", "CO-Denver", None, "needs_payer_id"),
    ("BCBS / Empire (Anthem / Elevance)", "Commercial", "CO-Denver", None, "needs_payer_id"),
    ("Cigna Healthcare", "ACA", "CO-Denver", "62308", "needs_enrollment"),
    ("Cigna Healthcare", "Commercial", "CO-Denver", "62308", "needs_enrollment"),
    (
        "Colorado Department of Health Care Policy & Financing",
        "Traditional Medicaid",
        "CO-Denver",
        None,
        "needs_payer_id",
    ),
    ("Healthspring", "Medicare Advantage", "CO-Denver", None, "needs_payer_id"),
    ("Humana", "Medicare Advantage", "CO-Denver", "61101", "supported"),
    ("Kaiser Permanente", "Commercial", "CO-Denver", None, "needs_payer_id"),
    ("Kaiser Permanente", "Medicare Advantage", "CO-Denver", None, "needs_payer_id"),
    ("Novitas Solutions, Inc.", "Traditional Medicare", "CO-Denver", None, "needs_payer_id"),
    ("SelectHealth", "ACA", "CO-Denver", None, "needs_payer_id"),
    ("SelectHealth", "Medicare Advantage", "CO-Denver", None, "needs_payer_id"),
    ("UnitedHealthcare", "Commercial", "CO-Denver", "87726", "supported"),
    ("UnitedHealthcare", "Dual Eligible (FIDE SNP)", "CO-Denver", "87726", "supported"),
    ("UnitedHealthcare", "Medicare Advantage", "CO-Denver", "87726", "supported"),
    # --- New York ---
    ("EmblemHealth", "Commercial", "NY", None, "needs_payer_id"),
    # --- Florida (South Florida) ---
    ("Aetna", "Commercial", "FL-South Florida", "60054", "needs_enrollment"),
    ("Aetna", "Medicare Advantage", "FL-South Florida", "60054", "needs_enrollment"),
    ("Aetna Better Health", "Managed Medicaid", "FL-South Florida", None, "needs_payer_id"),
    ("Align Senior Health Plan", "Medicare Advantage", "FL-South Florida", None, "needs_payer_id"),
    ("Ambetter (Centene)", "ACA", "FL-South Florida", "68069", "needs_enrollment"),
    ("AmeriHealth Caritas", "ACA", "FL-South Florida", None, "needs_payer_id"),
    ("AmeriHealth Caritas", "Medicare Advantage", "FL-South Florida", None, "needs_payer_id"),
    ("AvMed", "ACA", "FL-South Florida", None, "needs_payer_id"),
    ("AvMed", "Commercial", "FL-South Florida", None, "needs_payer_id"),
    ("AvMed", "Medicare Advantage", "FL-South Florida", None, "needs_payer_id"),
    ("BCBS / Empire (Anthem / Elevance)", "ACA", "FL-South Florida", None, "needs_payer_id"),
    ("BCBS / Empire (Anthem / Elevance)", "Commercial", "FL-South Florida", None, "needs_payer_id"),
    ("BCBS / Empire (Anthem / Elevance)", "Managed Medicaid", "FL-South Florida", None, "needs_payer_id"),
    ("BCBS / Empire (Anthem / Elevance)", "Medicare Advantage", "FL-South Florida", None, "needs_payer_id"),
    ("Cigna Healthcare", "ACA", "FL-South Florida", "62308", "needs_enrollment"),
    ("Cigna Healthcare", "Commercial", "FL-South Florida", "62308", "needs_enrollment"),
    ("Community Care Plan", "Managed Medicaid", "FL-South Florida", None, "needs_payer_id"),
    ("Curative", "Commercial", "FL-South Florida", None, "needs_payer_id"),
]


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")


def payer_rows():
    """Yield dict kwargs for one GLOBAL Payer row per ROSTER entry."""
    for label, btype, state, sid, enroll in ROSTER:
        yield {
            "tenant_id": None,
            "key": f"{slug(label)}-{slug(state)}",
            "label": label,
            "benefit_type": btype,
            "state": state,
            "stedi_payer_id": sid,
            "enrollment_status": enroll,
            "network_indicator_supported": enroll == "supported",
        }
