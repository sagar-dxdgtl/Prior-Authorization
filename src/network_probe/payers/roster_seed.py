from __future__ import annotations

import re

# (label, benefit_type, state, stedi_payer_id | None, enrollment_status)
# Known Stedi ids seeded for verified payers; best-guess for Aetna/Ambetter/Cigna (resolver confirms);
# everything else None/needs_payer_id (Task 20 resolver fills from Stedi's payer network).
#
# Stedi-id precedence (multi-source expansion): keep the original verified ids; add a research
# stedi_hint ONLY where it AGREES with the resolver proposal (docs/payer-sources/stedi-proposals.txt)
# or is an authoritative government id. Clearly-wrong fuzzy proposals (e.g. Noridian->03302 North
# Dakota, Molina->Illinois) are NOT baked here — those payers stay `needs_payer_id` for human review
# (see docs/payer-sources/MATRIX.md). Newly-confirmed ids flip a row from `needs_payer_id` to
# `needs_enrollment` (it has an id but is not yet a public-FHIR/adapter-supported payer).
ROSTER = [
    # --- Arizona ---
    ("Aetna", "Commercial", "AZ", "60054", "needs_enrollment"),
    ("Aetna", "Medicare Advantage", "AZ", "60054", "needs_enrollment"),
    ("Alignment Health Plan", "Medicare Advantage", "AZ", "CCHPC", "needs_enrollment"),
    ("Ambetter (Centene)", "ACA", "AZ", "68069", "needs_enrollment"),
    ("Arizona Complete Health - Complete Care Plan (Centene)", "Managed Medicaid", "AZ", "68069", "needs_enrollment"),
    ("Arizona Health Care Cost Containment System (AHCCCS)", "Traditional Medicaid", "AZ", "BEUZA", "needs_enrollment"),
    ("BCBS / Empire (Anthem / Elevance)", "ACA", "AZ", None, "needs_payer_id"),
    ("BCBS / Empire (Anthem / Elevance)", "Commercial", "AZ", None, "needs_payer_id"),
    ("BCBS / Empire (Anthem / Elevance)", "Medicare Advantage", "AZ", None, "needs_payer_id"),
    ("Cigna Healthcare", "ACA", "AZ", "62308", "needs_enrollment"),
    ("Cigna Healthcare", "Commercial", "AZ", "62308", "needs_enrollment"),
    ("DES/Division of Developmental Disabilities", "Managed Medicaid", "AZ", None, "needs_payer_id"),
    ("Devoted Health", "Medicare Advantage", "AZ", "DEVOT", "supported"),
    ("EternalHealth", "Medicare Advantage", "AZ", None, "needs_payer_id"),
    ("Gold Kidney Health Plan", "Medicare Advantage", "AZ", "A6865", "needs_enrollment"),
    ("Health Choice / BCBS / (Anthem / Elevance)", "Managed Medicaid", "AZ", None, "needs_payer_id"),
    ("Healthspring", "Medicare Advantage", "AZ", None, "needs_payer_id"),
    ("Humana", "Medicare Advantage", "AZ", "61101", "supported"),
    ("Mercy Care", "Managed Medicaid", "AZ", None, "needs_payer_id"),
    ("Mercy Care", "Medicare Advantage", "AZ", None, "needs_payer_id"),
    ("Molina Healthcare", "Dual Eligible (FIDE SNP)", "AZ", None, "needs_payer_id"),
    ("Molina Healthcare", "Managed Medicaid", "AZ", None, "needs_payer_id"),
    ("Noridian Healthcare Solutions, LLC", "Traditional Medicare", "AZ", None, "needs_payer_id"),
    ("Oscar", "ACA", "AZ", "OSCAR", "supported"),
    ("Scan", "Medicare Advantage", "AZ", "SPSCN", "needs_enrollment"),
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
        "SKCO0",
        "needs_enrollment",
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
    ("EmblemHealth", "Commercial", "NY", "13551", "needs_enrollment"),
    # --- Florida (South Florida) ---
    ("Aetna", "Commercial", "FL-South Florida", "60054", "needs_enrollment"),
    ("Aetna", "Medicare Advantage", "FL-South Florida", "60054", "needs_enrollment"),
    ("Aetna Better Health", "Managed Medicaid", "FL-South Florida", "ABH01", "needs_enrollment"),
    ("Align Senior Health Plan", "Medicare Advantage", "FL-South Florida", None, "needs_payer_id"),
    ("Ambetter (Centene)", "ACA", "FL-South Florida", "68069", "needs_enrollment"),
    ("AmeriHealth Caritas", "ACA", "FL-South Florida", None, "needs_payer_id"),
    ("AmeriHealth Caritas", "Medicare Advantage", "FL-South Florida", None, "needs_payer_id"),
    ("AvMed", "ACA", "FL-South Florida", "59274", "needs_enrollment"),
    ("AvMed", "Commercial", "FL-South Florida", "59274", "needs_enrollment"),
    ("AvMed", "Medicare Advantage", "FL-South Florida", "59274", "needs_enrollment"),
    ("BCBS / Empire (Anthem / Elevance)", "ACA", "FL-South Florida", None, "needs_payer_id"),
    ("BCBS / Empire (Anthem / Elevance)", "Commercial", "FL-South Florida", None, "needs_payer_id"),
    ("BCBS / Empire (Anthem / Elevance)", "Managed Medicaid", "FL-South Florida", None, "needs_payer_id"),
    ("BCBS / Empire (Anthem / Elevance)", "Medicare Advantage", "FL-South Florida", None, "needs_payer_id"),
    ("Cigna Healthcare", "ACA", "FL-South Florida", "62308", "needs_enrollment"),
    ("Cigna Healthcare", "Commercial", "FL-South Florida", "62308", "needs_enrollment"),
    ("Community Care Plan", "Managed Medicaid", "FL-South Florida", None, "needs_payer_id"),
    ("Curative", "Commercial", "FL-South Florida", "CURTV", "needs_enrollment"),
]


# Per-payer source attributes, keyed by roster label. Only honest/verified data is seeded here:
#   fhir_base_url    — set ONLY for verified-public PDEX Plan-Net servers (research fhir_verified=true).
#                      UHC/Oscar keep their existing adapters (routed by adapter key), so they stay None.
#   tic_url          — set ONLY where research actually verified the CMS machine-readable-file index
#                      (incl. corporate-family shares, e.g. Centene plans share the Centene index).
#   directory_url    — the human-facing "find a doctor" page (informational; not machine-queried).
#   directory_access — "public-fhir" (verified open PDEX or an existing public adapter),
#                      "needs-authorized-api" (OAuth2/portal registration required), or
#                      "none" (govt/Medicaid/MAC — the program IS the network; no PDEX/TiC).
# Anything absent from this map gets all-None columns (see _BLANK_SOURCE).
_CENTENE_TIC = "https://www.centene.com/price-transparency-files.html"
_CIGNA_TIC = "https://www.cigna.com/legal/compliance/machine-readable-files"
# Centene's public PDEX Plan-Net directory (FHIR R4, Authentication Type: None per the partner
# portal) — shared by all Centene-family plans (Ambetter, WellCare, AZ Complete Health). Verified
# live (CapabilityStatement 200 + unauthenticated Practitioner search returning NPIs). NOTE: the
# endpoint sits behind a CloudFront AWS-WAF that blocks datacenter/non-US IPs, so the prod egress
# IP must be allowlisted by Centene (email the API owner) or queries 403. See docs SIGNUP-CHECKLIST.
_CENTENE_FHIR = "https://iopc-pd.api.centene.com/iopc/pd/fhir/providerdirectory"

# (fhir_base_url, tic_url, directory_url, directory_access)
SOURCES: dict[str, tuple[str | None, str | None, str | None, str]] = {
    "Aetna": (
        None,
        "https://health1.aetna.com/app/public/",
        "https://www.aetna.com/individuals-families/find-a-doctor.html",
        "needs-authorized-api",
    ),
    "Aetna Better Health": (
        None,
        None,
        "https://www.aetnabetterhealth.com/find-provider.html",
        "needs-authorized-api",
    ),
    "Alignment Health Plan": (
        None,
        None,
        "https://providersearch.alignmenthealthplan.com/",
        "needs-authorized-api",
    ),
    "Align Senior Health Plan": (
        None,
        None,
        "https://alignseniorcare.com/providers/provider-documents/",
        "needs-authorized-api",
    ),
    "Ambetter (Centene)": (
        _CENTENE_FHIR,
        _CENTENE_TIC,
        "https://www.ambetterhealth.com/find-a-doctor.html",
        "public-fhir",
    ),
    "AmeriHealth Caritas": (
        "https://api-ext.amerihealthcaritas.com/NCEX/provider-api",
        None,
        "https://www.amerihealthcaritas.com",
        "public-fhir",
    ),
    "Arizona Complete Health - Complete Care Plan (Centene)": (
        _CENTENE_FHIR,
        _CENTENE_TIC,
        "https://www.azcompletehealth.com/find-a-provider",
        "public-fhir",
    ),
    "Arizona Health Care Cost Containment System (AHCCCS)": (
        None,
        None,
        "https://www.azahcccs.gov/Members/ProgramsAndCoveredServices/ProviderListings/",
        "none",
    ),
    "AvMed": (
        None,
        None,
        "https://www.avmed.org/find-doctors-facilities/",
        "needs-authorized-api",
    ),
    "BCBS / Empire (Anthem / Elevance)": (
        None,
        "https://www.anthem.com/machine-readable-file",
        "https://www.anthem.com/find-a-doctor",
        "needs-authorized-api",
    ),
    "Cigna Healthcare": (
        "https://fhir.cigna.com/ProviderDirectory/v1",
        _CIGNA_TIC,
        "https://hcpdirectory.cigna.com/",
        "public-fhir",
    ),
    "Colorado Department of Health Care Policy & Financing": (
        None,
        None,
        "https://hcpf.colorado.gov/provider-enrollment",
        "none",
    ),
    "Community Care Plan": (
        None,
        None,
        "https://providerdirectory.ccpcares.org/mma",
        "none",
    ),
    "Curative": (
        None,
        "https://curative.com/transparency-in-coverage-rates",
        "https://curative.com/providers",
        "needs-authorized-api",
    ),
    "DES/Division of Developmental Disabilities": (
        None,
        None,
        "https://des.az.gov/services/disabilities/developmental-disabilities/vendors-providers/current",
        "none",
    ),
    "Devoted Health": (
        "https://fhir.devoted.com/fhir",
        None,
        "https://www.devoted.com/search-providers/",
        "public-fhir",
    ),
    "EmblemHealth": (
        None,
        "https://transparency.emblemhealth.com/",
        "https://www.emblemhealth.com/find-a-doctor/find-the-right-care",
        "needs-authorized-api",
    ),
    "EternalHealth": (
        None,
        None,
        "https://nhconnect.eternalhealth.com/Provider/PublicPcpSearch",
        "needs-authorized-api",
    ),
    "Gold Kidney Health Plan": (
        None,
        None,
        "https://www.goldkidney.com/provider-search/",
        "needs-authorized-api",
    ),
    "Health Choice / BCBS / (Anthem / Elevance)": (
        None,
        None,
        "https://providerdirectory.healthchoiceaz.com/",
        "needs-authorized-api",
    ),
    "Healthspring": (
        "https://p-hi2.digitaledge.cigna.com/ProviderDirectory/v1",
        _CIGNA_TIC,
        "https://www.healthspring.com/providers/network-participation",
        "public-fhir",
    ),
    "Humana": (
        "https://fhir.humana.com/api",
        "https://developers.humana.com/syntheticdata/Resource/PCTFilesList?fileType=innetwork",
        "https://www.humana.com/find-a-doctor",
        "public-fhir",
    ),
    "Kaiser Permanente": (
        "https://kpx-service-bus.kp.org/service/hp/mhpo/healthplanproviderv1rc",
        "https://healthy.kaiserpermanente.org/support/transparency-coverage",
        "https://healthy.kaiserpermanente.org/find-a-doctor",
        "public-fhir",
    ),
    "Mercy Care": (
        None,
        None,
        "https://www.mercycareaz.org/find-a-provider",
        "none",
    ),
    "Molina Healthcare": (
        "https://api.interop.molinahealthcare.com/ProviderDirectory",
        None,
        "https://www.molinahealthcare.com/members/az/en-us/mem/medicaid/helpful-resources/provider.aspx",
        "public-fhir",
    ),
    "Noridian Healthcare Solutions, LLC": (
        None,
        None,
        "https://npiregistry.cms.hhs.gov",
        "none",
    ),
    "Novitas Solutions, Inc.": (
        None,
        None,
        "https://npiregistry.cms.hhs.gov",
        "none",
    ),
    "Oscar": (
        None,
        None,
        "https://www.hioscar.com/care-options",
        "public-fhir",
    ),
    "Scan": (
        # Public PDEX directory (InterSystems FHIR R4, no auth). Presence-based only: SCAN
        # exposes no network linkage (no PractitionerRole network-reference; OrganizationAffiliation
        # .network + InsurancePlan.network unpopulated), so the engine routes it to the
        # presence-based ScanDirectoryAdapter (see service._fhir_class_for).
        "https://providerdirectory.scanhealthplan.com",
        None,
        "https://www.scanhealthplan.com/helpful-tools/provider-search",
        "public-fhir",
    ),
    "SelectHealth": (
        None,
        "https://selecthealth.org/disclaimers/machine-readable-data",
        "https://selecthealth.org/find-care",
        "needs-authorized-api",
    ),
    "UnitedHealthcare": (
        None,
        "https://transparency-in-coverage.uhc.com/",
        "https://www.uhc.com/find-a-doctor",
        "public-fhir",
    ),
    "Wellcare (Centene)": (
        _CENTENE_FHIR,
        _CENTENE_TIC,
        "https://www.wellcare.com/en/find-a-doctor",
        "public-fhir",
    ),
    "Wellpoint / Amerigroup (Elevance)": (
        None,
        None,
        "https://findcaresecure.wellpoint.com/",
        "needs-authorized-api",
    ),
}

_BLANK_SOURCE: tuple[None, None, None, None] = (None, None, None, None)


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")


def payer_rows():
    """Yield dict kwargs for one GLOBAL Payer row per ROSTER entry, with source columns merged."""
    for label, btype, state, sid, enroll in ROSTER:
        fhir_base_url, tic_url, directory_url, directory_access = SOURCES.get(label, _BLANK_SOURCE)
        yield {
            "tenant_id": None,
            "key": f"{slug(label)}-{slug(state)}",
            "label": label,
            "benefit_type": btype,
            "state": state,
            "stedi_payer_id": sid,
            "enrollment_status": enroll,
            "network_indicator_supported": enroll == "supported",
            "fhir_base_url": fhir_base_url,
            "tic_url": tic_url,
            "directory_url": directory_url,
            "directory_access": directory_access,
        }
