"""Seed rows for `provider_portal_facts` — the AUTHORITATIVE member-facing find-a-doctor result.

These are manual/authorized checks against each payer's own provider portal (Meridian Find-a-Provider,
UHC Find Care, AZ Blue / HealthSparq, Medicare.gov Care Compare). The portal is the accurate network
source: the CMS machine-readable FHIR we query over-includes (false INs, e.g. Naar) and the TiC we
ingest has gaps (e.g. Maydell). A row here OVERRIDES the FHIR/directory-derived status and carries the
portal screenshot as proof (`screenshot` names a file under api/static/portal/).

Seeded like the roster catalogue (global rows, tenant_id NULL) via the 0029 migration. Later we pivot
from these manual rows to live portal APIs — same table, same override semantics; only the writer changes.
"""

from __future__ import annotations


def portal_fact_rows() -> list[dict]:
    """Test2 portal-verified provider-network facts. Keyed by (payer_key, npi); `plan` is for display."""
    return [
        # Perry — Meridian IL Managed Medicaid; Kevin Petermann → OON.
        {
            "payer_key": "meridian-health-il",
            "npi": "1588744650",
            "tin": "843012976",
            "plan": "MeridianHealth: Medicaid",
            "in_network": False,
            "provider_name": "Kevin Petermann",
            "member": "Jennifer Perry",
            "portal_name": "Meridian Find-a-Provider",
            "portal_url": "https://findaprovider.ilmeridian.com/search-results",
            "screenshot": "perry-meridian-1588744650.png",
            "verified_by": "provider portal (manual)",
            "verified_at": "2026-07-20",
            "note": (
                "0 results for NPI 1588744650 in 'MeridianHealth: Medicaid' (entire network), searching "
                "from Oak Lawn IL 60453 — the provider is not in the Medicaid MCO network → out-of-network."
            ),
        },
        # Desormeaux + Stephen — UHC AARP Medicare Advantage FL-0026 (PPO); David Naar → OON (w/ benefits).
        {
            "payer_key": "unitedhealthcare-fl-south-florida",
            "npi": "1760457477",
            "tin": "463812940",
            "plan": "AARP Medicare Advantage from UHC FL-0026 (PPO) / H2406018000",
            "in_network": False,
            "provider_name": "David Naar",
            "member": "Lisa Desormeaux / Norma Stephen",
            "portal_name": "UHC Find Care (findcare.guest.uhc.com)",
            "portal_url": "https://findcare.guest.uhc.com/guest-plan-selection/browse",
            "screenshot": "naar-uhc-findcare-1760457477.png",
            "verified_by": "provider portal (manual)",
            "verified_at": "2026-07-20",
            "note": (
                "For plan 'AARP Medicare Advantage from UHC FL-0026 (PPO)' in Boca Raton FL 33431, David "
                "Naar (NPI 1760457477) does not appear — the directory surfaces a different Benjamin L Naar "
                "(chiropractic) and FMC of Naranja (dialysis). Physician out-of-network; the PPO pays OON benefits."
            ),
        },
        # Vasquez — BlueCard: Blue Shield of California member, AZ host (BCBS Arizona); Arthur Maydell → INN.
        {
            "payer_key": "blue-shield-of-california-az",
            "npi": "1992078745",
            "tin": "843447602",
            "plan": "Statewide/National PPO (BlueCard host: BCBS Arizona)",
            "in_network": True,
            "provider_name": "Arthur T. Maydell",
            "member": "Linda Vasquez-Villegas",
            "portal_name": "AZ Blue / HealthSparq",
            "portal_url": "https://azblue.healthsparq.com/",
            "screenshot": "maydell-azblue-1992078745.png",
            "verified_by": "provider portal (manual)",
            "verified_at": "2026-07-20",
            "note": (
                "Arthur T. Maydell (NPI 1992078745, Diagnostic Radiology) shows in-network in the "
                "Statewide/National PPO (the BlueCard network) at United Vein Centers, 3805 E Bell Rd, "
                "Phoenix AZ 85032 — '14 in network' → in-network."
            ),
        },
        # Rodriguez — Original Medicare (FFS) + Humana Medicare Supplement (Medigap); Maurice Roulhac → INN.
        {
            "payer_key": "humana-co-denver",
            "npi": "1801837109",
            "tin": "475181686",
            "plan": "Original Medicare (FFS) + Humana Medicare Supplement (Medigap)",
            "in_network": True,
            "provider_name": "Maurice Roulhac, MD",
            "member": "Jose Rodriguez",
            "portal_name": "Medicare.gov Care Compare",
            "portal_url": "https://www.medicare.gov/care-compare/",
            "screenshot": "roulhac-medicare-1801837109.png",
            "verified_by": "provider portal (manual)",
            "verified_at": "2026-07-20",
            "note": (
                "Maurice Roulhac, MD (NPI 1801837109, Vascular Surgery) is Medicare-enrolled and 'Charges the "
                "Medicare-approved amount (so you pay less out-of-pocket)' — i.e. accepts assignment. For "
                "Original Medicare + Humana Medigap there is no provider network; a participating Medicare "
                "provider is in-network."
            ),
        },
        # Smith — Aetna Medicare AZ; Hedson Desir → OON (plain, not physician-OON: no plan selected so no
        # clinic-in-network signal to distinguish it).
        {
            "payer_key": "aetna-az",
            "npi": "1346866332",
            "tin": "843447602",
            "plan": "Aetna Medicare AZ (HMO/PPO Medicare Advantage)",
            "in_network": False,
            "provider_name": "Hedson Desir",
            "member": "Susan Smith",
            "portal_name": "Aetna Find Care (health.aetna.com)",
            "portal_url": "https://health.aetna.com/ahpublic/results?q=Desir%20Hedson",
            "screenshot": "desir-aetna-1346866332.png",
            "verified_by": "provider portal (manual)",
            "verified_at": "2026-07-20",
            "note": (
                "Searched 'Desir Hedson' near Phoenix AZ 85032 in Aetna Find Care — 35 results, but none is "
                "Hedson Desir (NPI 1346866332); the matches are different people (Fallon Desir PSYD, Katherine "
                "Desio-Scott MSW, Cullen Hudson MD). The physician is absent from Aetna's directory → "
                "out-of-network. This is a Medicare Advantage line, and TiC/MRF network data is commercial-only, "
                "so we do not refine it to physician-OON — reported as plain out-of-network."
            ),
        },
    ]
