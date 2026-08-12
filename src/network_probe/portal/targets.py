"""Portal targets for the Ins Test 3 payer list — the 11 sheet rows map to 9 distinct portals.

Each target is the *public, member-facing* find-a-doctor entry point (guest search, no login), which
is the surface a clinic staffer checks by hand today. `notes` records what we already know from the
Test 2 manual round and the 2026-06-28 policy sweep (docs/payer-sources/directory-urls.md) so the
probe either confirms or overturns it rather than re-deriving from scratch.

`fhir_fallback` names the payer's CMS-mandated public Provider Directory API (CMS-9115-F) to use
when the portal refuses automated access — the sanctioned automated path for the same question.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class PortalTarget:
    key: str
    portal_name: str
    entry_url: str
    payer_keys: tuple[str, ...]  # roster payer keys this portal answers for
    sheet_rows: tuple[str, ...]  # the Ins Test 3 "Insurance" values it covers
    platform: str | None = None  # shared directory platform, when it is one (HealthSparq, Centene hub…)
    fhir_fallback: str | None = None
    notes: str = ""
    search_hints: tuple[str, ...] = field(default=())  # selectors that indicate a live search control


# Ordered roughly by how much the demo depends on them (the three UHC rows share one portal, and
# UHC Find Care is the case where flex.optum FHIR was proven wrong on Naar — so it leads).
TARGETS: tuple[PortalTarget, ...] = (
    PortalTarget(
        key="uhc-findcare",
        portal_name="UHC Find Care (guest)",
        entry_url="https://findcare.guest.uhc.com/guest-plan-selection/browse",
        payer_keys=(
            "unitedhealthcare-az",
            "unitedhealthcare-co-denver",
            "unitedhealthcare-fl-south-florida",
            "unitedhealthcare-fl-tampa",
            "unitedhealthcare-ga-atlanta",
            "unitedhealthcare-il",
            "unitedhealthcare-nj-uvc",
            "unitedhealthcare-nj-vascular-health",
            "unitedhealthcare-ny",
            "unitedhealthcare-tx-dallas",
            "unitedhealthcare-tx-houston",
        ),
        sheet_rows=(
            "UHC Medicare Advantage GA",
            "UHC Commerical NHP Access HMO",
            "UHC AARP Medicare Advantage",
        ),
        fhir_fallback="flex.optum InsurancePlan/PractitionerRole (already wired)",
        notes=(
            "Reached by hand in Test 2 (Naar, NPI 1760457477 → absent → OON) while flex.optum FHIR said IN. "
            "uhc.com itself is Akamai-403 but the guest findcare host served a real search. Plan must be "
            "selected first via guest-plan-selection, so the driver is a two-step: pick product, then search."
        ),
        search_hints=("input[type=search]", "input[placeholder*='Search']", "[data-testid*='search']"),
    ),
    PortalTarget(
        key="azblue-healthsparq",
        portal_name="AZ Blue / HealthSparq",
        entry_url="https://azblue.healthsparq.com/",
        payer_keys=("bcbs-empire-anthem-elevance-az", "blue-shield-of-california-az"),
        sheet_rows=("BCBS Arizona AZ",),
        platform="HealthSparq",
        notes=(
            "Reached by hand in Test 2 (Maydell, NPI 1992078745 → IN on Statewide/National PPO). HealthSparq "
            "is a shared platform, so this driver should generalise to other HealthSparq payers by subdomain."
        ),
        search_hints=("input[name*='search']", "input[placeholder*='Search']", "#searchTerm"),
    ),
    PortalTarget(
        key="aetna-ahpublic",
        portal_name="Aetna Find Care (health.aetna.com guest)",
        entry_url="https://health.aetna.com/ahpublic/results?q=",
        # Every Aetna Commercial market on the roster. This portal is national: the launch URL carries
        # no state, and the network is pinned on-page by ZIP + the plan picker (which offers "Show all
        # plans (including those not in my area)"). So a market key never changes the walk — only the
        # ZIP and plan do, and both already come from the caller. Aetna Better Health (Medicaid) is a
        # DIFFERENT portal and is deliberately not listed here.
        payer_keys=(
            "aetna-az",
            "aetna-co-denver",
            "aetna-fl-south-florida",
            "aetna-fl-tampa",
            "aetna-ga-atlanta",
            "aetna-il",
            "aetna-nj-uvc",
            "aetna-nj-vascular-health",
            "aetna-ny",
            "aetna-tx-dallas",
            "aetna-tx-houston",
        ),
        sheet_rows=("Aetna Commercial ",),
        notes=(
            "Reached by hand in Test 2 (Desir, NPI 1346866332 → absent from 35 results → OON). NB Aetna's ToS "
            "expressly prohibits robots/scraping and robots.txt disallows /docfind/ — highest ToS exposure of "
            "the set; flagged for the client's counsel, not a technical blocker."
        ),
        search_hints=("input[type=search]", "input[placeholder*='doctor']"),
    ),
    PortalTarget(
        key="cigna-hcp",
        portal_name="Cigna Health Care Provider Directory",
        entry_url="https://hcpdirectory.cigna.com/",
        payer_keys=(
            "cigna-healthcare-az",
            "cigna-healthcare-co-denver",
            "cigna-healthcare-fl-south-florida",
            "cigna-healthcare-fl-tampa",
            "cigna-healthcare-ga-atlanta",
            "cigna-healthcare-il",
            "cigna-healthcare-nj-uvc",
            "cigna-healthcare-ny",
            "cigna-healthcare-tx-dallas",
            "cigna-healthcare-tx-houston",
        ),
        sheet_rows=("Cigna Commercial",),
        fhir_fallback="Cigna PDEX Plan-Net (pre-wired in fhir_pdex)",
        notes="2026-06-28 sweep: loaded, SPA, no WAF block. Commercial line → also TiC-eligible.",
        search_hints=("input[type=search]", "input[placeholder*='Search']", "[role=combobox]"),
    ),
    PortalTarget(
        key="oscar-care-options",
        portal_name="Oscar Care Options",
        entry_url="https://www.hioscar.com/care-options",
        payer_keys=(
            "oscar-az",
            "oscar-fl-south-florida",
            "oscar-fl-tampa",
            "oscar-ga-atlanta",
            "oscar-nj-vascular-health",
            "oscar-tx-houston",
        ),
        sheet_rows=("Oscar Health",),
        fhir_fallback="Oscar private JSON directory API (already an adapter)",
        notes=(
            "Public search, no login; robots.txt allows /care-options. We already have a working JSON adapter, "
            "so the portal driver exists mainly to produce the screenshot evidence."
        ),
        search_hints=("input[type=search]", "input[placeholder*='Search']"),
    ),
    PortalTarget(
        key="wellcare-hub",
        portal_name="Wellcare Find a Provider",
        entry_url="https://www.wellcarefindaprovider.com/",
        payer_keys=(
            "wellcare-allwell-centene-tx-houston",
            "wellcare-centene-az",
            "wellcare-centene-fidelis-nj-vascular-health",
            "wellcare-centene-ga-atlanta",
            "wellcare-centene-il",
            "wellcare-centene-tx-dallas",
            "wellcare-centene-tx-houston",
        ),
        sheet_rows=("Wellcare",),
        platform="Centene public hub",
        notes=(
            "301s to my.wellcare.com/x/hub/public/en/landing-page (SPA). No WAF in the 2026-06-28 sweep. "
            "Wellcare ToS prohibits systematic downloading — single per-provider lookups only. Same Centene "
            "platform as Ambetter, so the driver should generalise."
        ),
        search_hints=("input[type=search]", "input[placeholder*='Search']"),
    ),
    PortalTarget(
        key="humana-finder",
        portal_name="Humana Provider Finder",
        entry_url="https://finder.humana.com/",
        payer_keys=(
            "humana-az",
            "humana-co-denver",
            "humana-fl",
            "humana-ga-atlanta",
            "humana-il",
            "humana-nj-vascular-health",
            "humana-ny",
            "humana-tx-dallas",
            "humana-tx-houston",
        ),
        sheet_rows=("Humana Medicare FL",),
        fhir_fallback="Humana PDEX Plan-Net, no auth (verified live)",
        notes=(
            "www.humana.com/find-a-doctor times out at the WAF, but finder.humana.com is the actual tool host "
            "and was never probed. Humana's FHIR works with no auth, so this row has a sanctioned path either way."
        ),
        search_hints=("input[type=search]", "input[placeholder*='Search']"),
    ),
    PortalTarget(
        key="bcbsil-provider-finder",
        portal_name="BCBS Illinois Provider Finder",
        # The path in the 2026-06-28 sweep 404s. This is the live guest-search entry (zip + plan type),
        # confirmed 2026-07-28; www.bcbsil.com/find-care also serves and links to it.
        entry_url="https://www.bcbsil.com/find-care/find-a-doctor-or-hospital",
        payer_keys=(
            "bcbs-anthem-il",
            "bcbs-empire-anthem-elevance-hcsc-il",
        ),
        sheet_rows=("BCBS Illinois",),
        fhir_fallback="HCSC PDEX Plan-Net (static client_id header, already wired)",
        notes=(
            "HCSC; documented as Imperva-protected. HCSC's FHIR is wired and reachable, so the portal is the "
            "evidence layer rather than the only source. Commercial line → TiC-eligible too."
        ),
        search_hints=("input[type=search]", "input[placeholder*='Search']"),
    ),
    PortalTarget(
        key="bcbssc-sapphire",
        portal_name="BlueCross BlueShield of South Carolina (Zelis Sapphire, guest)",
        entry_url="https://shoppingforcare.sapphirethreesixtyfive.com/?ci=BCBSSC",
        # BCBS SC is its OWN Blue licensee — not HCSC, and nothing to do with bcbsil-provider-finder.
        # Its directory is Zelis Sapphire, the same product Molina runs on its own host.
        payer_keys=(
            "bcbs-south-carolina",
            "bcbs-south-carolina-ga-atlanta",
            "bcbs-south-carolina-fl-tampa",
            "bcbs-south-carolina-fl-south-florida",
        ),
        sheet_rows=("BCBS South Carolina",),
        platform="Zelis Sapphire",
        notes=(
            "Guest-searchable, 200, no WAF (2026-08-12). Answers for OUT-OF-STATE providers via "
            "BlueCard: with the network pinned to 'Preferred Blue', the Lake Worth FL clinic ZIP "
            "returned the sheet's own physician at the sheet's own street address, and the Atlanta "
            "clinic ZIP returned 10 GA providers. Result cards state the pinned network inline "
            "('In \"Preferred Blue\" Network'), so the attestation is in the screenshot."
        ),
        search_hints=("input[data-cy='autosuggest.input']",),
    ),
    PortalTarget(
        key="publix-sapphire",
        portal_name="Publix / BlueCross BlueShield of South Carolina (Zelis Sapphire, guest)",
        entry_url="https://shoppingforcare.sapphirethreesixtyfive.com/?ci=Publix",
        # Same Zelis instance as BCBSSC, one query parameter apart — but a DIFFERENT network book:
        # 3 allowable networks vs 41, and network id 10 renders as "PBB - Blue Choice PPO" here and
        # "Preferred Blue" there. Same id, different network: never carry one across tenants.
        payer_keys=(
            "bcbs-south-carolina-publix",
            "bcbs-south-carolina-publix-fl-south-florida",
        ),
        sheet_rows=("BCBS South Carolina Publix",),
        platform="Zelis Sapphire",
        notes=(
            "Publix's self-funded book on BCBS SC's Zelis tenant (ci=Publix). Three networks, each "
            "self-identifying by code: PBB - Blue Choice PPO, PWA - Blue Open Access POS, "
            "PXN - Network Blue. No alpha-prefix route (alpha_prefix.network_enabled = false)."
        ),
        search_hints=("input[data-cy='autosuggest.input']",),
    ),
    PortalTarget(
        key="molina-provider-search",
        portal_name="Molina Provider Search (TX Medicaid)",
        # The live tool is Zelis "Sapphire", found 2026-07-28 by driving candidates in a real browser.
        # providersearch.molinahealthcare.com is DEAD (retired host -> generic error page, NOT WAF-blocked)
        # and the molinahealthcare.com member hub serves 313 chars titled "Error Page". Imperva fronts the
        # Sapphire host but never challenged across 5 runs — fronted, not gated.
        entry_url="https://molina.sapphirecareselect.com/",
        payer_keys=(
            "molina-healthcare-tx-dallas",
            "molina-healthcare-tx-houston",
        ),
        sheet_rows=("Molina Medicaid TX",),
        fhir_fallback="Molina PDEX Plan-Net (public)",
        notes=(
            "molinahealthcare.com is 403 domain-wide (Akamai) but providersearch.molinahealthcare.com is a "
            "separate host that was never probed. Managed Medicaid → TiC-exempt, so portal/FHIR are the only "
            "network sources for this row; TX Medicaid enrollment is the decisive negative filter."
        ),
        # This SPA's box is a data-cy autosuggest; generic input[type=search] never matches it.
        search_hints=("input[data-cy='autosuggest.input']",),
    ),
)


def target_for_payer(payer_key: str) -> PortalTarget | None:
    """The portal that answers for a roster payer key, or None when we have no driver for it."""
    for t in TARGETS:
        if payer_key in t.payer_keys:
            return t
    return None


def by_key(portal_key: str) -> PortalTarget | None:
    for t in TARGETS:
        if t.key == portal_key:
            return t
    return None
