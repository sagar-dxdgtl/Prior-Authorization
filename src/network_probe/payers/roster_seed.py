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
    # Stedi id 33628 = "Mercy Care ACC-RBHA" (verified via GET /2024-04-01/payers, exact plan-name
    # match to Mercy Care's own AHCCCS Complete Care/RBHA Medicaid product), 2026-07-08.
    ("Mercy Care", "Managed Medicaid", "AZ", "33628", "needs_enrollment"),
    ("Mercy Care", "Medicare Advantage", "AZ", "33628", "needs_enrollment"),
    ("Molina Healthcare", "Dual Eligible (FIDE SNP)", "AZ", None, "needs_payer_id"),
    ("Molina Healthcare", "Managed Medicaid", "AZ", None, "needs_payer_id"),
    # Stedi id 03102 = "Medicare Arizona Part B" (verified via GET /2024-04-01/payers, matched by
    # STATE not by MAC-contractor name -- confirms this codebase's own prior warning that a fuzzy
    # name match on "Noridian" alone proposes the wrong state, e.g. North Dakota), 2026-07-08.
    ("Noridian Healthcare Solutions, LLC", "Traditional Medicare", "AZ", "03102", "needs_enrollment"),
    ("Oscar", "ACA", "AZ", "OSCAR", "supported"),
    ("Scan", "Medicare Advantage", "AZ", "SPSCN", "needs_enrollment"),
    # Added for the UVC demo-cases roster (2026-07-08). Stedi id TDFIC = "TRICARE for Life", exact
    # name match verified via GET /2024-04-01/payers. Benefit type is its own category (Medicare-
    # secondary wraparound coverage for military retirees/dependents), not Medicare Advantage.
    ("Tricare for Life", "TRICARE Secondary", "AZ", "TDFIC", "needs_enrollment"),
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
    # --- Illinois --- (added from client benefit list; researched via 3 parallel agent passes,
    # 2026-07-06 — see docs/payer-sources/MATRIX.md "Illinois" section for full sourcing notes)
    ("Aetna", "Commercial", "IL", "60054", "needs_enrollment"),
    ("Aetna", "Medicare Advantage", "IL", "60054", "needs_enrollment"),
    ("Aetna Better Health", "Managed Medicaid", "IL", None, "needs_payer_id"),
    ("Ambetter (Centene)", "ACA", "IL", "68069", "needs_enrollment"),
    ("BCBS (Anthem)", "Medicare Advantage", "IL", None, "needs_payer_id"),
    ("BCBS / Empire (Anthem / Elevance)(HCSC)", "ACA", "IL", None, "needs_payer_id"),
    ("BCBS / Empire (Anthem / Elevance)(HCSC)", "Commercial", "IL", None, "needs_payer_id"),
    ("BCBS / Empire (Anthem / Elevance)(HCSC)", "Medicare Advantage", "IL", None, "needs_payer_id"),
    ("Cigna Healthcare", "Commercial", "IL", "62308", "needs_enrollment"),
    ("Essence Healthcare", "Medicare Advantage", "IL", None, "needs_payer_id"),
    ("Humana", "Dual Eligible (FIDE SNP)", "IL", "61101", "supported"),
    ("Humana", "Medicare Advantage", "IL", "61101", "supported"),
    ("Illinois Department of Healthcare and Family Services (HFS)", "Traditional Medicaid", "IL", None, "needs_payer_id"),
    ("Longevity Health Plan", "Medicare Advantage", "IL", "LIL01", "needs_enrollment"),
    # Stedi id 13189 = "Meridian (Illinois)" (verified via GET /2024-04-01/payers, exact
    # state-scoped match -- the earlier fuzzy-name resolver proposal, "TRISTAR Insurance
    # Group", was a false positive from bare token overlap on "Meridian"), 2026-07-08.
    ("Meridian Health", "Managed Medicaid", "IL", "13189", "needs_enrollment"),
    # Stedi id 06102 = "Medicare Illinois Part B" (verified via GET /2024-04-01/payers; Stedi
    # organizes traditional-Medicare by STATE+part, not by MAC-contractor name -- "National
    # Government Services" alone resolves to an umbrella entity, not the state-specific
    # trading-partner id eligibility checks need), 2026-07-08.
    ("National Government Services, Inc. (NGS)", "Traditional Medicare", "IL", "06102", "needs_enrollment"),
    ("Provider Partners", "Medicare Advantage", "IL", None, "needs_payer_id"),
    ("UnitedHealthcare", "Commercial", "IL", "87726", "supported"),
    ("UnitedHealthcare", "Medicare Advantage", "IL", "87726", "supported"),
    ("Zing Health", "Medicare Advantage", "IL", None, "needs_payer_id"),
    ("Wellcare (Centene)", "Dual Eligible (FIDE SNP)", "IL", None, "needs_payer_id"),
    ("Wellcare (Centene)", "Medicare Advantage", "IL", None, "needs_payer_id"),
    ("Clear Spring Health", "Medicare Advantage", "IL", None, "needs_payer_id"),
    # --- Georgia (Atlanta) ---
    ("Aetna", "Commercial", "GA-Atlanta", "60054", "needs_enrollment"),
    ("Aetna", "Medicare Advantage", "GA-Atlanta", "60054", "needs_enrollment"),
    ("Alliant Health Plans", "Commercial", "GA-Atlanta", None, "needs_payer_id"),
    ("Ambetter (Centene)", "ACA", "GA-Atlanta", "68069", "needs_enrollment"),
    ("BCBS / Empire (Anthem / Elevance)", "ACA", "GA-Atlanta", None, "needs_payer_id"),
    ("BCBS / Empire (Anthem / Elevance)", "Commercial", "GA-Atlanta", None, "needs_payer_id"),
    ("BCBS / Empire (Anthem / Elevance)", "Medicare Advantage", "GA-Atlanta", None, "needs_payer_id"),
    ("CareSource", "Medicare Advantage", "GA-Atlanta", None, "needs_payer_id"),
    ("Cigna Healthcare", "ACA", "GA-Atlanta", "62308", "needs_enrollment"),
    ("Cigna Healthcare", "Commercial", "GA-Atlanta", "62308", "needs_enrollment"),
    ("Curative", "Commercial", "GA-Atlanta", "CURTV", "needs_enrollment"),
    ("Devoted Health", "Medicare Advantage", "GA-Atlanta", "DEVOT", "supported"),
    ("Healthspring", "Medicare Advantage", "GA-Atlanta", None, "needs_payer_id"),
    ("Humana", "Medicare Advantage", "GA-Atlanta", "61101", "supported"),
    ("Kaiser Foundation Health Plan of Georgia", "Medicare Advantage", "GA-Atlanta", None, "needs_payer_id"),
    ("Novitas Solutions, Inc.", "Traditional Medicare", "GA-Atlanta", None, "needs_payer_id"),
    ("Oscar", "ACA", "GA-Atlanta", "OSCAR", "supported"),
    # NOTE: client list tagged this "Traditional Medicaid" — corrected to Traditional Medicare;
    # Palmetto GBA is the CMS Medicare Administrative Contractor for GA (Jurisdiction J), never a
    # Medicaid entity. Flagged for client confirmation, see MATRIX.md.
    ("Palmetto GBA, LLC", "Traditional Medicare", "GA-Atlanta", None, "needs_payer_id"),
    ("Peach State Health Plan (Centene)", "Managed Medicaid", "GA-Atlanta", "68069", "needs_enrollment"),
    ("UnitedHealthcare", "Commercial", "GA-Atlanta", "87726", "supported"),
    ("UnitedHealthcare", "Dual Eligible (FIDE SNP)", "GA-Atlanta", "87726", "supported"),
    ("UnitedHealthcare", "Medicare Advantage", "GA-Atlanta", "87726", "supported"),
    ("Clear Spring Health", "Medicare Advantage", "GA-Atlanta", None, "needs_payer_id"),
    # --- Texas (Houston) ---
    ("Abilis Health Plan", "Medicare Advantage", "TX-Houston", None, "needs_payer_id"),
    ("MCC Health", "Commercial", "TX-Houston", None, "needs_payer_id"),
    ("Aetna", "Commercial", "TX-Houston", "60054", "needs_enrollment"),
    ("Aetna", "Medicare Advantage", "TX-Houston", "60054", "needs_enrollment"),
    ("Aetna Better Health", "Managed Medicaid", "TX-Houston", "TMDSA", "needs_enrollment"),
    ("Ambetter (Centene)", "ACA", "TX-Houston", "68069", "needs_enrollment"),
    ("BCBS / Empire (Anthem / Elevance)(HCSC)", "ACA", "TX-Houston", None, "needs_payer_id"),
    ("BCBS / Empire (Anthem / Elevance)(HCSC)", "Commercial", "TX-Houston", None, "needs_payer_id"),
    ("BCBS / Empire (Anthem / Elevance)(HCSC)", "Managed Medicaid", "TX-Houston", None, "needs_payer_id"),
    ("BCBS / Empire (Anthem / Elevance)(HCSC)", "Medicare Advantage", "TX-Houston", None, "needs_payer_id"),
    ("Cigna Healthcare", "ACA", "TX-Houston", "62308", "needs_enrollment"),
    ("Cigna Healthcare", "Commercial", "TX-Houston", "62308", "needs_enrollment"),
    # Stedi id 60495 = "Community Health Choice (Marketplace)" (verified via GET /2024-04-01/payers
    # -- disambiguated from a second, unrelated "Community Health Choice" entry, id 48145, whose
    # own `names` field is the Medicaid/CHIP/D-SNP side, not Marketplace/ACA), 2026-07-08.
    ("Community Health Choice (CHC)", "ACA", "TX-Houston", "60495", "needs_enrollment"),
    ("Community Health Choice (CHC)", "Dual Eligible (FIDE SNP)", "TX-Houston", None, "needs_payer_id"),
    ("Community Health Choice (CHC)", "Managed Medicaid", "TX-Houston", None, "needs_payer_id"),
    ("Curative", "Commercial", "TX-Houston", "CURTV", "needs_enrollment"),
    ("Devoted Health", "Medicare Advantage", "TX-Houston", "DEVOT", "supported"),
    ("Healthspring", "Medicare Advantage", "TX-Houston", None, "needs_payer_id"),
    ("Humana", "Medicare Advantage", "TX-Houston", "61101", "supported"),
    ("Memorial Hermann HP", "Commercial", "TX-Houston", "PGRAJ", "needs_enrollment"),
    ("Memorial Hermann HP", "Medicare Advantage", "TX-Houston", "PGRAJ", "needs_enrollment"),
    ("Molina Healthcare", "ACA", "TX-Houston", None, "needs_payer_id"),
    ("Molina Healthcare", "Managed Medicaid", "TX-Houston", None, "needs_payer_id"),
    ("Molina Healthcare", "Medicare Advantage", "TX-Houston", None, "needs_payer_id"),
    ("Novitas Solutions, Inc.", "Traditional Medicare", "TX-Houston", None, "needs_payer_id"),
    ("Oscar", "ACA", "TX-Houston", "OSCAR", "supported"),
    ("Superior HealthPlan (Centene)", "Managed Medicaid", "TX-Houston", None, "needs_payer_id"),
    ("Texas Health and Human Services Commission (HHSC)", "Traditional Medicaid", "TX-Houston", None, "needs_payer_id"),
    ("UnitedHealthcare", "Commercial", "TX-Houston", "87726", "supported"),
    ("UnitedHealthcare", "Dual Eligible (FIDE SNP)", "TX-Houston", "87726", "supported"),
    ("UnitedHealthcare", "Medicare Advantage", "TX-Houston", "87726", "supported"),
    ("UnitedHealthcare Community Plan", "Managed Medicaid", "TX-Houston", None, "needs_payer_id"),
    ("Wellcare (Centene)", "Medicare Advantage", "TX-Houston", None, "needs_payer_id"),
    ("WellCare / AllWell (Centene)", "Dual Eligible (FIDE SNP)", "TX-Houston", None, "needs_payer_id"),
    ("Wellpoint / Amerigroup (Elevance)", "ACA", "TX-Houston", None, "needs_payer_id"),
    ("Wellpoint / Amerigroup (Elevance)", "Managed Medicaid", "TX-Houston", None, "needs_payer_id"),
    ("Wellpoint / Amerigroup (Elevance)", "Medicare Advantage", "TX-Houston", None, "needs_payer_id"),
    # --- Texas (Dallas) --- NOTE: HHSC service-delivery-area caveat — Mansfield sits mostly in
    # Tarrant County, which is a SEPARATE SDA from "Dallas" SDA for STAR/STAR+PLUS/CHIP. Superior
    # HealthPlan's Medicaid book covers Dallas SDA but explicitly EXCLUDES Tarrant; other Medicaid
    # MCOs below were confirmed for "Dallas" SDA but not individually re-checked for Tarrant. Verify
    # against the clinic's exact service area before treating any Dallas-market Medicaid row as live.
    ("Aetna", "Commercial", "TX-Dallas", "60054", "needs_enrollment"),
    ("Aetna", "Medicare Advantage", "TX-Dallas", "60054", "needs_enrollment"),
    ("Aetna Better Health", "Managed Medicaid", "TX-Dallas", "TMDSA", "needs_enrollment"),
    ("Ambetter (Centene)", "ACA", "TX-Dallas", "68069", "needs_enrollment"),
    ("Baylor Scott & White Health Plan", "Commercial", "TX-Dallas", "88030", "needs_enrollment"),
    ("Baylor Scott & White Health Plan", "Medicare Advantage", "TX-Dallas", "88030", "needs_enrollment"),
    ("BCBS / Empire (Anthem / Elevance)(HCSC)", "ACA", "TX-Dallas", None, "needs_payer_id"),
    ("BCBS / Empire (Anthem / Elevance)(HCSC)", "Commercial", "TX-Dallas", None, "needs_payer_id"),
    ("Abilis Health Plan", "Medicare Advantage", "TX-Dallas", None, "needs_payer_id"),
    ("BCBS / Empire (Anthem / Elevance)(HCSC)", "Medicare Advantage", "TX-Dallas", None, "needs_payer_id"),
    ("MCC Health", "Commercial", "TX-Dallas", None, "needs_payer_id"),
    ("Cigna Healthcare", "ACA", "TX-Dallas", "62308", "needs_enrollment"),
    ("Cigna Healthcare", "Commercial", "TX-Dallas", "62308", "needs_enrollment"),
    ("Curative", "Commercial", "TX-Dallas", "CURTV", "needs_enrollment"),
    ("Healthspring", "Medicare Advantage", "TX-Dallas", None, "needs_payer_id"),
    ("Humana", "Medicare Advantage", "TX-Dallas", "61101", "supported"),
    ("Molina Healthcare", "ACA", "TX-Dallas", None, "needs_payer_id"),
    ("Molina Healthcare", "Managed Medicaid", "TX-Dallas", None, "needs_payer_id"),
    ("Molina Healthcare", "Medicare Advantage", "TX-Dallas", None, "needs_payer_id"),
    ("Novitas Solutions, Inc.", "Traditional Medicare", "TX-Dallas", None, "needs_payer_id"),
    ("Superior HealthPlan (Centene)", "Managed Medicaid", "TX-Dallas", None, "needs_payer_id"),
    ("Texas Health and Human Services Commission (HHSC)", "Traditional Medicaid", "TX-Dallas", None, "needs_payer_id"),
    ("UnitedHealthcare", "ACA", "TX-Dallas", "87726", "supported"),
    ("UnitedHealthcare", "Commercial", "TX-Dallas", "87726", "supported"),
    ("UnitedHealthcare", "Dual Eligible (FIDE SNP)", "TX-Dallas", "87726", "supported"),
    ("UnitedHealthcare", "Medicare Advantage", "TX-Dallas", "87726", "supported"),
    ("UnitedHealthcare Community Plan", "Managed Medicaid", "TX-Dallas", None, "needs_payer_id"),
    ("Wellcare (Centene)", "Medicare Advantage", "TX-Dallas", None, "needs_payer_id"),
    ("Wellpoint / Amerigroup (Elevance)", "ACA", "TX-Dallas", None, "needs_payer_id"),
    ("Wellpoint / Amerigroup (Elevance)", "Managed Medicaid", "TX-Dallas", None, "needs_payer_id"),
    ("Wellpoint / Amerigroup (Elevance)", "Medicare Advantage", "TX-Dallas", None, "needs_payer_id"),
    # --- Florida --- Added for the UVC demo-cases roster (2026-07-08): Traditional Medicare (First
    # Coast Service Options is the FL Part A/B MAC) and Humana Medicare Advantage. Stedi ids verified
    # via GET /2024-04-01/payers.
    ("First Coast Service Options, Inc.", "Traditional Medicare", "FL", "09102", "needs_enrollment"),
    ("Humana", "Medicare Advantage", "FL", "61101", "supported"),
]


# Per-payer source attributes, keyed by roster label. Only honest/verified data is seeded here:
#   fhir_base_url    — set ONLY for verified-public PDEX Plan-Net servers (research fhir_verified=true).
#                      UHC/Oscar keep their existing adapters (routed by adapter key), so they stay None.
#   tic_url          — set ONLY where research actually verified the CMS machine-readable-file index
#                      (incl. corporate-family shares, e.g. Centene plans share the Centene index).
#   directory_url    — the human-facing "find a doctor" page (informational; not machine-queried).
#   directory_access — "public-fhir" (verified open PDEX or an existing public adapter),
#                      "authorized-fhir" (verified PDEX behind OAuth2 — creds in .env; routed to the
#                      FhirPdexAdapter through an OAuth2 bearer-token client, e.g. Anthem/Elevance),
#                      "needs-authorized-api" (OAuth2/portal registration required, not yet wired),
#                      "pdf-directory" (network published only as a monthly PDF → parsed into
#                      payer_directory_entries, routed to DbDirectoryAdapter), or
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
        # PDF-only network (AllyAlign) — no FHIR, no NPIs. Monthly directory PDF is parsed into
        # payer_directory_entries and matched by name+state+zip (routes to DbDirectoryAdapter).
        None,
        None,
        "https://alignseniorcare.com/providers/provider-documents/",
        "pdf-directory",
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
        # RE-CHECKED 2026-07-06: the old `myfhir.avmed.org` endpoint has regressed from expired-TLS
        # to a full TLS-handshake failure (dead). A newer endpoint was found —
        # avmp.interop.avmed.com/api/v1/avmp/metadata (Sentara Health-hosted, reflecting AvMed's
        # 2023 acquisition by Sentara; cert valid) — but it's Patient-Access-only (read-by-id, no
        # search, no PractitionerRole/Location/OrganizationAffiliation) and cannot answer network-
        # participation queries. No live directory path exists; Stedi 59274 (eligibility only)
        # remains the only usable channel.
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
        # PDF-only network (AaNeel) — no FHIR, no NPIs. Date-stamped monthly PDF (AZ) is
        # discovered from the find-a-provider page, parsed into payer_directory_entries, and
        # matched by name+state+zip (DbDirectoryAdapter).
        None,
        None,
        "https://www.eternalhealth.com/for-members/find-a-provider-or-pharmacy/",
        "pdf-directory",
    ),
    "Gold Kidney Health Plan": (
        # SANDBOX CONFIRMED LIVE 2026-07-06 — independently re-verified: `curl -H "payer-id:
        # f24482f7e98e49f7a141bf503e0b3b20" https://api-sandbox.aaneelconnect.com/cms/r4/
        # providerdirectory/Practitioner` returns a real HTTP 200 FHIR Bundle (no subscription key,
        # login, or approval needed — the sandbox `payer-id` header is all it takes). AaNeel's own
        # portal states Provider Directory sandbox APIs need no authentication at all. This runs on
        # a separate Azure APIM instance from production, which is still pending its subscription
        # key — pull real Gold Kidney network data from the sandbox NOW rather than waiting.
        # Production stays needs-authorized-api until the prod key lands.
        None,
        None,
        "https://www.goldkidney.com/provider-search/",
        "needs-authorized-api",
    ),
    "First Coast Service Options, Inc.": (
        # Traditional Medicare MAC (FL Part A/B), same treatment as Noridian(AZ)/NGS(IL) --
        # no network concept, checked via CMS's own tools per-NPI.
        None,
        None,
        "https://npiregistry.cms.hhs.gov",
        "none",
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
    "Meridian Health": (
        # Illinois Medicaid MCO. Its own "Find a Provider" tool is a JS SPA (no public FHIR/API
        # found) -- same treatment as other directory-access=none payers.
        None,
        None,
        "https://findaprovider.ilmeridian.com",
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
        # get_adapter() only reaches the pre-built "uhc" adapter-key shortcut when q.payer is
        # literally "uhc" -- callers that resolve a payer via its full catalogue key (e.g.
        # "unitedhealthcare-az", as check_eligibility() does) need fhir_base_url populated here
        # to fall through to the catalogue-driven FHIR dispatch instead of hitting "no adapter".
        # Same endpoint as the "uhc" adapter-key shortcut (fhir_pdex.KNOWN_ENDPOINTS["uhc"]).
        "https://flex.optum.com/fhirpublic/R4",
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
        # TESTED LIVE 2026-07-06 — hypothesis DISPROVEN for data access, but useful finding.
        # wellpoint.com/developers requires a full legal/compliance intake ("several weeks" per
        # their own site) — confirmed not self-serve. Tried reusing the existing ANTHEM_FHIR_*
        # credentials (same Elevance backend, same "hos-fhir-server v1.0.0" fingerprint) directly
        # against Wellpoint's registered path: `/metadata` is public either way (200), but
        # `/Practitioner` returns 401 "Unable to find scope associated with the operation" no
        # matter which Elevance token endpoint issues the bearer token (tried the standard Anthem
        # token URL AND a second "registered/api/v1/token" endpoint that also accepts our creds).
        # Decoding the token confirms it's genuinely OUR existing approved Elevance app
        # ("entity_name":"Quickflows AI", "entityType":"Third Party App", "sor_cd":"Provider
        # Directory") — so the OAuth backend is shared, but Wellpoint access is a SEPARATE
        # per-resource entitlement layered on top of the same app registration, not a different
        # credential set. ACTIONABLE: email Elevance asking them to add the Wellpoint/Amerigroup
        # provider-directory scope to our EXISTING "Quickflows AI" app registration — likely faster
        # than filing a brand-new registration from scratch via wellpoint.com/developers.
        None,
        None,
        "https://findcaresecure.wellpoint.com/",
        "needs-authorized-api",
    ),
    # --- Added for IL / GA-Atlanta / TX-Houston / TX-Dallas, 2026-07-06 ---
    "BCBS (Anthem)": (
        # RESEARCHED, high-confidence determination: almost certainly a data-entry duplicate of
        # "BCBS / Empire (Anthem / Elevance)(HCSC)" Medicare Advantage for IL, NOT a genuine
        # Anthem/Elevance product. BCBSA Blue licenses are exclusive per state; HCSC holds the
        # exclusive IL license and directly sells "Blue Cross Medicare Advantage" HMO/HMO-POS/PPO
        # there. Elevance's own newsroom list of its affiliated 2025 MA brands (Anthem, Wellpoint,
        # HealthSun, Simply Healthcare, Freedom Health, Optimum HealthCare, MMM) never mentions
        # Illinois, and Elevance holds no Blue license in IL at all (its Blue states are CA/CO/CT/
        # GA/IN/KY/ME/MO/NV/NH/NY/OH/VA/WI). Recommend merging this row into the HCSC row rather
        # than pursuing it as separate registration work.
        None, None, None, "needs-authorized-api",
    ),
    "BCBS / Empire (Anthem / Elevance)(HCSC)": (
        # HCSC (Health Care Service Corp) owns BCBS in IL/TX/MT/NM/OK — an independent licensee, NOT
        # Elevance, same pattern as BCBSAZ/Florida Blue (do NOT route through _ANTHEM_FHIR). Medicaid
        # product "Blue Cross Community Health Plans" has a confirmed Stedi id (G00621, IL) but its
        # FHIR endpoint (api.hcsc.net/providerfinder/sapphire/fhir) returned 401 even on /metadata —
        # tighter-gated than Aetna. Dev portal: interoperability.hcsc.com.
        None, None, None, "needs-authorized-api",
    ),
    "Essence Healthcare": (
        # Lumeris-owned, St. Louis MO. Confirmed CMS-regulated (H2610/H6200/H3189/H4620), sells in
        # IL. `essencehealthcare.healthlx.com/{metadata,fhir/metadata}` returns HTTP 200 but it's a
        # React SPA page, NOT a real CapabilityStatement — confirmed false positive, do not reuse.
        # No developer/interoperability portal found despite targeted search.
        None, None, "https://essencehealthcare.com/find-a-doctor/", "needs-authorized-api",
    ),
    "Illinois Department of Healthcare and Family Services (HFS)": (
        None, None, "https://ext2.hfs.illinois.gov/hfsindprovdirectory", "none",
    ),
    "Longevity Health Plan": (
        # Longevity Health Plan Inc. (IL entity; per-state sister entities elsewhere). No FHIR
        # endpoint found (fhir./api.longevityhealthplan.com don't resolve). No dev portal — only
        # Compliance@longevityhealthplan.com. IL Stedi id LIL01 confirmed live on Stedi's own site.
        # NOTE: Longevity has NO direct Georgia plan of its own — GA is served via a separate
        # "National Carriers" partner brand, so the GA-Atlanta row for this label is likely
        # mislabeled; flag for client confirmation before treating it as real.
        None, None, "https://longevityhealthplan.com/find-a-provider/", "needs-authorized-api",
    ),
    "National Government Services, Inc. (NGS)": (
        # Traditional Medicare MAC, Jurisdiction 6 (IL/MN/WI) — same treatment as Noridian(AZ)/
        # Novitas(CO): govt program IS the network, no PDEX/TiC/Stedi id.
        None, None, "https://npiregistry.cms.hhs.gov", "none",
    ),
    "Provider Partners": (
        # "Provider Partners Health Plan(s)" — institutional SNP (nursing-facility/assisted-living
        # network only), CMS H3800-001-0 for IL. No FHIR endpoint found (guessed metadata paths all
        # 404). RE-CHECKED 2026-07-06: `pphpfhirapp.prod.healthaxis.net/Login` has a confirmed real
        # "Sign Up" link -> /register — genuine OPEN self-service signup (email/password/phone/ToS,
        # no invite code, no approval gate to create an account). What credentials/base URL appear
        # post-signup is unverified since that requires completing registration — worth actually
        # doing. Narrow SNP network, limited relevance to an outpatient clinic either way.
        None, None, "https://pphealthplan.com/provider-directory/", "needs-authorized-api",
    ),
    "Zing Health": (
        # Zing Health Plan, Inc. (dba Zing Health), Chicago IL, founded 2019, no parent co.
        # Multi-state (IL/IN/MI/MS/OH/TN), not IL-only. `myzinghealth.com/metadata` and
        # `zinghealthdev.azurewebsites.net/metadata` return HTTP 200 but are marketing-site HTML,
        # NOT CapabilityStatements — confirmed false positives. No dev portal found; only a general
        # provider-relations inbox (provider.services@myzinghealth.com), untested for API access.
        None, None, "https://myzinghealth.com/search-provider", "needs-authorized-api",
    ),
    "Clear Spring Health": (
        # MA brand of Group One Thousand One, LLC ("Group 1001"). **Already exited Medicare
        # Advantage entirely as of 2026-06-01** (benefits ran through 2026-05-31, ~12k enrollees
        # affected) — this plan no longer exists as an active MA offering as of this research
        # (2026-07-06). Deprioritize rather than pursue integration. Hosted by AaNeel Infotech (same
        # vendor as this repo's existing Gold Kidney/EternalHealth rows) — dev portal reachable at
        # developers.aaneelconnect.com/home?payerCode=aead93d7-3b6f-467a-8376-c30119c0503a, but its
        # own providerdirectory/metadata endpoint 404s (alive, not live-verified).
        None, None, "https://clearspringhealthcare.com/find-a-provider/", "needs-authorized-api",
    ),
    "Alliant Health Plans": (
        # Regional NW Georgia insurer (Dalton, GA / Whitfield County). Confirmed service area is
        # ~24k-provider "4Corners Alliant Network" across north GA counties (Whitfield, Murray,
        # Catoosa, Walker, Gordon, Floyd, Polk, Pickens, Gilmer, Fannin, Union, Lumpkin, Dawson,
        # Towns, White, Hall, Rabun, Habersham, Banks, Stephens, Franklin, Hart, Barrow) — **Cobb
        # County/Kennesaw does NOT appear in any confirmed county list**; likely does not actually
        # serve this clinic's market — flag for client confirmation before pursuing. No FHIR/API
        # found. Not to be confused with "Alliant Health Solutions" (formerly Alliant-Georgia
        # Medical Care Foundation), an unrelated CMS Quality Improvement Organization.
        None, "https://alliantplans.com/machine-readable-data/",
        "https://alliantplans.com/providers/", "needs-authorized-api",
    ),
    "CareSource": (
        # Georgia Medicare Advantage CONFIRMED — sells "CareSource Dual Advantage (HMO D-SNP)" +
        # "Dual Advantage PLUS", CMS contract H8390-015-0, statewide (159 GA counties), active
        # 2024-2026. It's specifically the D-SNP line, not a general/standalone MA plan (unlike
        # CareSource's broader non-D-SNP MA business in OH/IN/KY/WV/NC). No FHIR endpoint found.
        None, None, "https://findadoctor.caresource.com/", "none",
    ),
    "Kaiser Foundation Health Plan of Georgia": (
        # PRESUMED same national Kaiser PDEX endpoint already verified public for CO ("national
        # incl. CO" per that SOURCES note) — GA-specific data NOT individually re-verified this
        # pass. Confirm with a live GA query before fully trusting.
        "https://kpx-service-bus.kp.org/service/hp/mhpo/healthplanproviderv1rc",
        "https://healthy.kaiserpermanente.org/support/transparency-coverage",
        "https://healthy.kaiserpermanente.org/find-a-doctor",
        "public-fhir",
    ),
    "Palmetto GBA, LLC": (
        # Traditional Medicare MAC, Jurisdiction J (AL/GA/TN) — same treatment as Noridian/Novitas/NGS.
        None, None, "https://npiregistry.cms.hhs.gov", "none",
    ),
    "Peach State Health Plan (Centene)": (
        _CENTENE_FHIR, _CENTENE_TIC, "https://findaprovider.pshpgeorgia.com/", "public-fhir",
    ),
    "Abilis Health Plan": (
        # CONFIRMED REAL COMPANY, but likely WRONG STATE for these rows: Abilis Health Plan is the
        # 2026 rebrand of "Signature Advantage" (HMO SNP, CMS H2400, BrightSpring Health Services)
        # — confirmed operating ONLY in Kentucky (120 counties) and Tennessee (34 counties), an
        # Institutional SNP (nursing-facility/assisted-living residents only). No TX presence found
        # in CMS filings, TDI, or the company's own service-area page. The TX-Houston/TX-Dallas
        # rows for this label appear to be a client-list data error, not an unresearched gap —
        # flag for client confirmation before pursuing registration at all.
        None, None, "https://signatureadvantageplan.com/interactive-provider-directory-2/", "needs-authorized-api",
    ),
    "MCC Health": (
        # COULD NOT CONFIRM A MATCHING COMPANY. Closest name match ("MCC Health, PBC" dba Cost Plus
        # Wellness, Dallas TX, Mark Cuban-backed) is a direct-contracting platform with no member
        # benefits/claims/provider directory — not an insurer. No "MCC Health" entry in TDI's HMO
        # listing. Other "MCC" candidates found are brokers/consulting firms, not TX payers.
        # Recommend flagging this roster row back to whoever sourced the client list to ask what
        # plan/EOB/member-card actually shows "MCC Health" — no registration path can exist until
        # the real entity is identified.
        None, None, None, "needs-authorized-api",
    ),
    "Community Health Choice (CHC)": (
        # Houston/Harris-only nonprofit HMO (Medicaid STAR/STAR+PLUS/CHIP + ACA Marketplace + MA
        # D-SNP). Dev portal (developers.communityhealthchoice.org) live but sign-up gated; no
        # reachable /metadata this pass.
        None, None, "https://providersearch.communityhealthchoice.org", "needs-authorized-api",
    ),
    "Memorial Hermann HP": (
        # Memorial Hermann Health Plan, Inc. (MA, CMS H7115) + Memorial Hermann Commercial Health
        # Plan, Inc. — TDI-licensed, Greater Houston only (Harris/Brazoria/Ft.Bend/Montgomery/
        # Galveston/Waller/Walker/Wharton). RE-CHECKED 2026-07-06: found their OWN developer docs
        # page (healthplan.memorialhermann.org/about-us/cmstpadev-documentation), which documents
        # BOTH prod (apigateway.memorialhermann.org:7443/.../public) AND test
        # (apigatewaytest.memorialhermann.org:7443/.../public) endpoints + OAuth2 token URLs — but
        # `dig` confirms BOTH subdomains are NXDOMAIN (no A/CNAME at all), while the parent domain
        # resolves fine. This isn't a resolver fluke — the documented API infrastructure itself is
        # dead/decommissioned. Registration still requires a paper "Third Party App Developer
        # Application Form" targeting infrastructure that doesn't exist. Stedi id PGRAJ confirmed
        # live, BUT shows eligibilityCheck: NOT_SUPPORTED (837 claims only, no 270/271).
        None, None, "https://healthplan.memorialhermann.org/find-a-doctor", "needs-authorized-api",
    ),
    "Superior HealthPlan (Centene)": (
        # Centene's TX Medicaid brand — unlike Ambetter/Wellcare/Peach State, this product's FHIR
        # is gated behind the Centene Partner Portal login, NOT the shared public national endpoint.
        # RE-CHECKED 2026-07-06: Centene's own official developer reference doc ("Centene Health
        # Plan and Brands for Third Party Application Developers," content.centene.com, linked from
        # partners.centene.com) lists Superior HealthPlan (Medicaid) and Ambetter from Superior
        # HealthPlan (Marketplace) in the SAME unified national table as Ambetter/Wellcare/Peach
        # State/Home State/Buckeye — already-confirmed brands on the shared public
        # iopc-pd.api.centene.com endpoint. No separate Superior-only document, no note of
        # different infrastructure. Strongly suggests Superior is just a plan-name value in the
        # SAME shared national FHIR API, not a separate technical system — once the prod egress IP
        # is Centene-allowlisted (already required for Ambetter/Wellcare), try querying
        # `Organization?name=Superior HealthPlan` there BEFORE pursuing a separate Partner Portal
        # login.
        None, None, "https://findaprovider.superiorhealthplan.com", "needs-authorized-api",
    ),
    "Texas Health and Human Services Commission (HHSC)": (
        None, None, "https://opl.tmhp.com", "none",
    ),
    "Tricare for Life": (
        # Medicare-secondary coverage — no separate TRICARE network contract required as long as the
        # provider is Medicare-enrolled and not opted out (per TRICARE's own published guidance).
        None, None, "https://tricare.mil/tfl", "none",
    ),
    "UnitedHealthcare Community Plan": (
        # Same underlying UHC/Optum adapter as other UnitedHealthcare rows — not a separate
        # technical product, just a distinct Medicaid brand name.
        None, "https://transparency-in-coverage.uhc.com/", "https://www.uhc.com/communityplan/find-a-doctor", "public-fhir",
    ),
    "WellCare / AllWell (Centene)": (
        _CENTENE_FHIR, _CENTENE_TIC, "https://www.wellcare.com/en/find-a-doctor", "public-fhir",
    ),
    "Baylor Scott & White Health Plan": (
        # Scott and White Health Plan (dba BSW Health Plan) + subsidiaries. Confirmed Dallas/Tarrant/
        # Collin/Denton/Ellis/Johnson/Rockwall coverage for both Commercial and MA (CMS H8142) —
        # Mansfield-area coverage confirmed. Stedi id 88030 confirmed live. Interoperability page
        # points to an Inovalon DataStream OAuth2 portal (JS-rendered, no disclosed base URL
        # pre-registration — not live-verified). NOTE: exiting TX Medicaid MCO (Aug 2026) and ACA
        # marketplace (Dec 2026) per news reports — does not affect Commercial-group/MA lines.
        None, "https://bswhealthplan.com/transparency", "https://bswhealthplan.com/care", "needs-authorized-api",
    ),
}

_BLANK_SOURCE: tuple[None, None, None, None] = (None, None, None, None)

# Anthem / Elevance OAuth2-gated PDEX directory (CMS-mandate path). Verified live: token endpoint
# takes form client_credentials, FHIR base is a national multi-LoB PDEX R4 server (creds in .env as
# ANTHEM_FHIR_*). It's Elevance's directory, so it's authoritative ONLY for Blues that ARE Elevance.
_ANTHEM_FHIR = "https://totalview.healthos.elevancehealth.com/resources/unregistered/api/v1/fhir/cms_mandate/mcd"
_ANTHEM_TIC = "https://www.anthem.com/machine-readable-file"
_ANTHEM_DIR = "https://www.anthem.com/find-a-doctor"

# Per-(label, state) source overrides — for payers whose directory source differs by market. The
# roster labels several independent Blues uniformly as "BCBS / Empire (Anthem / Elevance)", but only
# some states' Blue IS Elevance. Anthem BCBS Colorado is genuinely Elevance, so its CO rows route to
# the live OAuth2 endpoint; AZ ("BCBS"=BCBSAZ) and FL ("BCBS"=Florida Blue) are independent licensees
# NOT in this directory, so they keep needs-authorized-api (routing them here would false-OON every
# real local provider). Each value fully replaces the label-level SOURCES tuple for that one row.
SOURCE_OVERRIDES: dict[tuple[str, str], tuple[str | None, str | None, str | None, str]] = {
    ("BCBS / Empire (Anthem / Elevance)", "CO-Denver"): (_ANTHEM_FHIR, _ANTHEM_TIC, _ANTHEM_DIR, "authorized-fhir"),
    # Georgia's Anthem BCBS is also a direct Elevance subsidiary (SEC 10-K Exhibit 21: "Blue Cross
    # Blue Shield Healthcare Plan of Georgia, Inc." dba Anthem BCBS) — confirmed live 2026-07-06 by
    # querying the existing ANTHEM_FHIR_* creds directly and finding real GA networks (GA_HMO,
    # GA_PPO, GA Blue Value HIX, Medicare IND - GA PPO/HMO/SNP, Georgia Medicaid, etc). Same
    # treatment as CO — no new registration needed, just this catalogue row.
    ("BCBS / Empire (Anthem / Elevance)", "GA-Atlanta"): (_ANTHEM_FHIR, _ANTHEM_TIC, _ANTHEM_DIR, "authorized-fhir"),
}


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")


def payer_rows():
    """Yield dict kwargs for one GLOBAL Payer row per ROSTER entry, with source columns merged.
    A per-(label, state) SOURCE_OVERRIDES entry takes precedence over the label-level SOURCES tuple."""
    for label, btype, state, sid, enroll in ROSTER:
        fhir_base_url, tic_url, directory_url, directory_access = SOURCE_OVERRIDES.get(
            (label, state)
        ) or SOURCES.get(label, _BLANK_SOURCE)
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
