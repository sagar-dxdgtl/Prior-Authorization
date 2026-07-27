# Payer-portal reachability probe — 2026-07-28

What a real Chromium met at each portal's public entry URL. Supersedes the 2026-06-28 fetch-tool sweep in `docs/payer-sources/directory-urls.md` for reachability only (that document remains authoritative on robots.txt and Terms of Use).

Method: one navigation per portal, sequential, real desktop Chrome UA, 45s budget, full-page screenshot of every outcome. **No challenge was solved or bypassed** — challenges are detected, screenshotted and reported. Where a portal refuses automated access, the payer's CMS-mandated public FHIR Provider Directory API (CMS-9115-F) is the sanctioned path for the same question.

Screenshots: `.cache/portal-probe/2026-07-28/`

| Portal | Sheet row(s) | Result | HTTP | Challenge shown | Bot-protection present | Next step |
|---|---|---|---|---|---|---|
| `uhc-findcare`<br>UHC Find Care (guest) | UHC Medicare Advantage GA<br>UHC Commerical NHP Access HMO<br>UHC AARP Medicare Advantage | **SEARCHABLE** | 200 | none | none detected | write the driver |
| `azblue-healthsparq`<br>AZ Blue / HealthSparq | BCBS Arizona AZ | **LOADED** | 200 | none | Imperva / Incapsula | write the driver (navigate deeper first) |
| `aetna-ahpublic`<br>Aetna Find Care (health.aetna.com guest) | Aetna Commercial  | **LOADED** | 200 | none | none detected | write the driver (navigate deeper first) |
| `cigna-hcp`<br>Cigna Health Care Provider Directory | Cigna Commercial | **SEARCHABLE** | 200 | none | none detected | write the driver |
| `oscar-care-options`<br>Oscar Care Options | Oscar Health | **LOADED** | 200 | none | none detected | write the driver (navigate deeper first) |
| `wellcare-hub`<br>Wellcare Find a Provider | Wellcare | **LOADED** | 200 | none | none detected | write the driver (navigate deeper first) |
| `humana-finder`<br>Humana Provider Finder | Humana Medicare FL | **LOADED** | 200 | none | none detected | write the driver (navigate deeper first) |
| `bcbsil-provider-finder`<br>BCBS Illinois Provider Finder | BCBS Illinois | **LOADED** | 200 | none | none detected | write the driver (navigate deeper first) |
| `molina-provider-search`<br>Molina Provider Search (TX Medicaid) | Molina Medicaid TX | **LOADED** | 200 | none | none detected | write the driver (navigate deeper first) |

**Challenges encountered: 0 of 9.** No portal in this set presented a CAPTCHA or human-verification challenge to a real browser at its entry URL.

Note the distinction in the two right-hand columns: several portals *run* bot-protection scripts while serving content normally. A script being present is not a gate; only a challenge actually shown to the user is.

## Per-portal detail

### UHC Find Care (guest) (`uhc-findcare`)

- **Entry URL:** https://findcare.guest.uhc.com/guest-plan-selection/browse
- **Final URL:** https://findcare.guest.uhc.com/guest-plan-selection/browse
- **Result:** SEARCHABLE (HTTP 200, 25217ms)
- **Page title:** All | Find Care
- **Search control:** input[placeholder*='Search']
- **Screenshot:** `uhc-findcare-searchable.png`
- **Detail:** loaded with a usable provider-search control (input[placeholder*='Search']).
- **FHIR fallback:** flex.optum InsurancePlan/PractitionerRole (already wired)
- **Prior knowledge:** Reached by hand in Test 2 (Naar, NPI 1760457477 → absent → OON) while flex.optum FHIR said IN. uhc.com itself is Akamai-403 but the guest findcare host served a real search. Plan must be selected first via guest-plan-selection, so the driver is a two-step: pick product, then search.

### AZ Blue / HealthSparq (`azblue-healthsparq`)

- **Entry URL:** https://azblue.healthsparq.com/
- **Final URL:** https://azblue.healthsparq.com/
- **Result:** LOADED (HTTP 200, 16489ms)
- **Page title:** —
- **Search control:** not found at entry URL
- **Screenshot:** `azblue-healthsparq-loaded.png`
- **Detail:** loaded 101 chars of content but no search control matched at the entry URL — the driver needs to navigate deeper (plan selection / hub link) before searching (bot protection present: Imperva / Incapsula, served transparently).
- **Prior knowledge:** Reached by hand in Test 2 (Maydell, NPI 1992078745 → IN on Statewide/National PPO). HealthSparq is a shared platform, so this driver should generalise to other HealthSparq payers by subdomain.

### Aetna Find Care (health.aetna.com guest) (`aetna-ahpublic`)

- **Entry URL:** https://health.aetna.com/ahpublic/results?q=
- **Final URL:** https://health.aetna.com/ahpublic/results?q=
- **Result:** LOADED (HTTP 200, 20292ms)
- **Page title:** Aetna
- **Search control:** not found at entry URL
- **Screenshot:** `aetna-ahpublic-loaded.png`
- **Detail:** loaded 466 chars of content but no search control matched at the entry URL — the driver needs to navigate deeper (plan selection / hub link) before searching.
- **Prior knowledge:** Reached by hand in Test 2 (Desir, NPI 1346866332 → absent from 35 results → OON). NB Aetna's ToS expressly prohibits robots/scraping and robots.txt disallows /docfind/ — highest ToS exposure of the set; flagged for the client's counsel, not a technical blocker.

### Cigna Health Care Provider Directory (`cigna-hcp`)

- **Entry URL:** https://hcpdirectory.cigna.com/
- **Final URL:** https://hcpdirectory.cigna.com/web/public/consumer/directory/search
- **Result:** SEARCHABLE (HTTP 200, 12338ms)
- **Page title:** Cigna Health Care Provider Directory
- **Search control:** input[type=search]
- **Screenshot:** `cigna-hcp-searchable.png`
- **Detail:** loaded with a usable provider-search control (input[type=search]).
- **FHIR fallback:** Cigna PDEX Plan-Net (pre-wired in fhir_pdex)
- **Prior knowledge:** 2026-06-28 sweep: loaded, SPA, no WAF block. Commercial line → also TiC-eligible.

### Oscar Care Options (`oscar-care-options`)

- **Entry URL:** https://www.hioscar.com/care-options
- **Final URL:** https://www.hioscar.com/care-options
- **Result:** LOADED (HTTP 200, 21453ms)
- **Page title:** Search our network | Oscar
- **Search control:** not found at entry URL
- **Screenshot:** `oscar-care-options-loaded.png`
- **Detail:** loaded 5232 chars of content but no search control matched at the entry URL — the driver needs to navigate deeper (plan selection / hub link) before searching.
- **FHIR fallback:** Oscar private JSON directory API (already an adapter)
- **Prior knowledge:** Public search, no login; robots.txt allows /care-options. We already have a working JSON adapter, so the portal driver exists mainly to produce the screenshot evidence.

### Wellcare Find a Provider (`wellcare-hub`)

- **Entry URL:** https://www.wellcarefindaprovider.com/
- **Final URL:** https://my.wellcare.com/x/hub/public/en/landing-page
- **Result:** LOADED (HTTP 200, 20974ms)
- **Page title:** Landing Page - Wellcare
- **Search control:** not found at entry URL
- **Screenshot:** `wellcare-hub-loaded.png`
- **Detail:** loaded 1294 chars of content but no search control matched at the entry URL — the driver needs to navigate deeper (plan selection / hub link) before searching.
- **Prior knowledge:** 301s to my.wellcare.com/x/hub/public/en/landing-page (SPA). No WAF in the 2026-06-28 sweep. Wellcare ToS prohibits systematic downloading — single per-provider lookups only. Same Centene platform as Ambetter, so the driver should generalise.

### Humana Provider Finder (`humana-finder`)

- **Entry URL:** https://finder.humana.com/
- **Final URL:** https://findcare.humana.com/
- **Result:** LOADED (HTTP 200, 19779ms)
- **Page title:** Find Care - Humana
- **Search control:** not found at entry URL
- **Screenshot:** `humana-finder-loaded.png`
- **Detail:** loaded 806 chars of content but no search control matched at the entry URL — the driver needs to navigate deeper (plan selection / hub link) before searching.
- **FHIR fallback:** Humana PDEX Plan-Net, no auth (verified live)
- **Prior knowledge:** www.humana.com/find-a-doctor times out at the WAF, but finder.humana.com is the actual tool host and was never probed. Humana's FHIR works with no auth, so this row has a sanctioned path either way.

### BCBS Illinois Provider Finder (`bcbsil-provider-finder`)

- **Entry URL:** https://www.bcbsil.com/find-care/find-a-doctor-or-hospital
- **Final URL:** https://www.bcbsil.com/find-care/find-a-doctor-or-hospital
- **Result:** LOADED (HTTP 200, 21232ms)
- **Page title:** Find an In-Network Doctor, Specialist or Hospital | Blue Cross and Blue Shield of Illinois
- **Search control:** not found at entry URL
- **Screenshot:** `bcbsil-provider-finder-loaded.png`
- **Detail:** loaded 4760 chars of content but no search control matched at the entry URL — the driver needs to navigate deeper (plan selection / hub link) before searching.
- **FHIR fallback:** HCSC PDEX Plan-Net (static client_id header, already wired)
- **Prior knowledge:** HCSC; documented as Imperva-protected. HCSC's FHIR is wired and reachable, so the portal is the evidence layer rather than the only source. Commercial line → TiC-eligible too.

### Molina Provider Search (TX Medicaid) (`molina-provider-search`)

- **Entry URL:** https://www.molinahealthcare.com/members/tx/en-US/mem/provider-directories.aspx
- **Final URL:** https://www.molinahealthcare.com/members/tx/en-US/mem/provider-directories.aspx
- **Result:** LOADED (HTTP 200, 10782ms)
- **Page title:** Error Page
- **Search control:** not found at entry URL
- **Screenshot:** `molina-provider-search-loaded.png`
- **Detail:** loaded 313 chars of content but no search control matched at the entry URL — the driver needs to navigate deeper (plan selection / hub link) before searching.
- **FHIR fallback:** Molina PDEX Plan-Net (public)
- **Prior knowledge:** molinahealthcare.com is 403 domain-wide (Akamai) but providersearch.molinahealthcare.com is a separate host that was never probed. Managed Medicaid → TiC-exempt, so portal/FHIR are the only network sources for this row; TX Medicaid enrollment is the decisive negative filter.
