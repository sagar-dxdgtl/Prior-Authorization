# Payer Source Matrix — FHIR / TiC / Stedi catalogue

Machine-generated from `src/network_probe/payers/roster_seed.py` (the catalogue source of truth) by `scripts`-style generation. All 67 global roster rows. Only **verified** data is seeded; uncertain Stedi ids are deliberately left blank and flagged `review` for human `--apply` against `docs/payer-sources/stedi-proposals.txt`.

## Legend

- **Stedi ID** — primary EDI payer id used for 270/271. Blank = `needs_payer_id` (no confident id; see note).
- **FHIR base URL** — verified-public PDEX Plan-Net server that the engine routes the directory leg to. `(verified)` = research confirmed a live CapabilityStatement + unauthenticated query. `via existing adapter` = UHC (public Optum FHIR) / Oscar (web) are wired by adapter key, not a catalogue URL. `—` = none public.
- **TiC URL** — Transparency-in-Coverage machine-readable-file index, only where research verified it (incl. corporate-family shares, e.g. Centene/Cigna).
- **Dir access** — `public-fhir` (open PDEX or existing public adapter) · `needs-authorized-api` (OAuth2 / portal registration) · `none` (govt/Medicaid/MAC: the program is the network; no PDEX/TiC).
- **review** in a note = an unresolved Stedi id left for a human; the matrix records why the fuzzy resolver proposal was *not* baked.

## Matrix

| Payer | State | Type | Stedi ID | FHIR base URL (verified?) | TiC URL | Dir access | Note |
|---|---|---|---|---|---|---|---|
| Aetna | AZ | Commercial | 60054 | — | `https://health1.aetna.com/app/public/` | needs-authorized-api | Stedi 60054; provider-directory FHIR is OAuth2-gated (needs-authorized-api). |
| Aetna | AZ | Medicare Advantage | 60054 | — | `https://health1.aetna.com/app/public/` | needs-authorized-api | Stedi 60054; provider-directory FHIR is OAuth2-gated (needs-authorized-api). |
| Alignment Health Plan | AZ | Medicare Advantage | CCHPC | — | — | needs-authorized-api | CCHPC — resolver + research agree. |
| Ambetter (Centene) | AZ | ACA | 68069 | `https://iopc-pd.api.centene.com/iopc/pd/fhir/providerdirectory` (verified) | `https://www.centene.com/price-transparency-files.html` | public-fhir | Verified public PDEX Plan-Net (FHIR R4, auth: None). Centene umbrella 68069. **Prod egress must be WAF-allowlisted by Centene** or queries 403 — see SIGNUP-CHECKLIST. |
| Arizona Complete Health - Complete Care Plan (Centene) | AZ | Managed Medicaid | 68069 | `https://iopc-pd.api.centene.com/iopc/pd/fhir/providerdirectory` (verified) | `https://www.centene.com/price-transparency-files.html` | public-fhir | Verified public PDEX Plan-Net (FHIR R4, auth: None). Centene umbrella 68069. **Prod egress must be WAF-allowlisted by Centene** or queries 403 — see SIGNUP-CHECKLIST. |
| Arizona Health Care Cost Containment System (AHCCCS) | AZ | Traditional Medicaid | BEUZA | — | — | none | BEUZA — authoritative AZ Medicaid id. Govt program: no PDEX/TiC (the program IS the network). |
| BCBS / Empire (Anthem / Elevance) | AZ | ACA | — | — | `https://www.anthem.com/machine-readable-file` | needs-authorized-api | review: Stedi id varies by state BCBS affiliate (CA 040); resolver's 81508 (Empire/SOMOS) unconfirmed — left for review. |
| BCBS / Empire (Anthem / Elevance) | AZ | Commercial | — | — | `https://www.anthem.com/machine-readable-file` | needs-authorized-api | review: Stedi id varies by state BCBS affiliate (CA 040); resolver's 81508 (Empire/SOMOS) unconfirmed — left for review. |
| BCBS / Empire (Anthem / Elevance) | AZ | Medicare Advantage | — | — | `https://www.anthem.com/machine-readable-file` | needs-authorized-api | review: Stedi id varies by state BCBS affiliate (CA 040); resolver's 81508 (Empire/SOMOS) unconfirmed — left for review. |
| Cigna Healthcare | AZ | ACA | 62308 | `https://fhir.cigna.com/ProviderDirectory/v1` (verified) | `https://www.cigna.com/legal/compliance/machine-readable-files` | public-fhir | Verified public FHIR + existing cigna-fhir adapter. Stedi 62308 (enrollment required before live 270/271). |
| Cigna Healthcare | AZ | Commercial | 62308 | `https://fhir.cigna.com/ProviderDirectory/v1` (verified) | `https://www.cigna.com/legal/compliance/machine-readable-files` | public-fhir | Verified public FHIR + existing cigna-fhir adapter. Stedi 62308 (enrollment required before live 270/271). |
| DES/Division of Developmental Disabilities | AZ | Managed Medicaid | — | — | — | none | review: no standalone EDI payer id; ALTCS eligibility routes through AHCCCS (BEUZA). |
| Devoted Health | AZ | Medicare Advantage | DEVOT | `https://fhir.devoted.com/fhir` (verified) | — | public-fhir | Verified public FHIR + existing Algolia adapter. Stedi DEVOT — supported. |
| EternalHealth | AZ | Medicare Advantage | — | — | — | needs-authorized-api | review: research candidate RP037; resolver found no confident match — left for review. |
| Gold Kidney Health Plan | AZ | Medicare Advantage | A6865 | — | — | needs-authorized-api | A6865 — resolver + research agree. |
| Health Choice / BCBS / (Anthem / Elevance) | AZ | Managed Medicaid | — | — | — | needs-authorized-api | review: BCBSAZ-administered; resolver's 130 (Anthem Indiana) is wrong-state — left for review. |
| Healthspring | AZ | Medicare Advantage | — | `https://p-hi2.digitaledge.cigna.com/ProviderDirectory/v1` (verified) | `https://www.cigna.com/legal/compliance/machine-readable-files` | public-fhir | Verified public FHIR (Cigna Medicare brand). review: Stedi id — research 52192 vs resolver 63092 disagree — left for review. |
| Humana | AZ | Medicare Advantage | 61101 | `https://fhir.humana.com/api` (verified) | `https://developers.humana.com/syntheticdata/Resource/PCTFilesList?fileType=innetwork` | public-fhir | Verified public FHIR + existing humana-fhir adapter. Stedi 61101 — supported. |
| Mercy Care | AZ | Managed Medicaid | — | — | — | none | review: resolver's 22248 (AmeriHealth Caritas PA) is wrong; no confirmed AZ id. Govt/Medicaid MCO. |
| Mercy Care | AZ | Medicare Advantage | — | — | — | none | review: resolver's 22248 (AmeriHealth Caritas PA) is wrong; no confirmed AZ id. Govt/Medicaid MCO. |
| Molina Healthcare | AZ | Dual Eligible (FIDE SNP) | — | `https://api.interop.molinahealthcare.com/ProviderDirectory` (verified) | — | public-fhir | Verified public PDEX Plan-Net (FHIR 4.0.1) — no auth/registration; 605k practitioners. Inline network names (MHMS CHIP, Molina Marketplace, …). review: Stedi id still open — resolver's 20934 (Molina Illinois) wrong-state; AZ medical routes via Availity/Emdeon. |
| Molina Healthcare | AZ | Managed Medicaid | — | `https://api.interop.molinahealthcare.com/ProviderDirectory` (verified) | — | public-fhir | Verified public PDEX Plan-Net (FHIR 4.0.1) — no auth/registration; 605k practitioners. Inline network names (MHMS CHIP, Molina Marketplace, …). review: Stedi id still open — resolver's 20934 (Molina Illinois) wrong-state; AZ medical routes via Availity/Emdeon. |
| Noridian Healthcare Solutions, LLC | AZ | Traditional Medicare | — | — | — | none | review: Traditional Medicare uses per-state ids; resolver's 03302 (Medicare North Dakota) is wrong. CMS-owned data (NPPES). |
| Oscar | AZ | ACA | OSCAR | via existing adapter | — | public-fhir | Existing OscarAdapter (public). Stedi OSCAR — supported. |
| Scan | AZ | Medicare Advantage | SPSCN | `https://providerdirectory.scanhealthplan.com` (verified) | — | public-fhir | Verified public PDEX (InterSystems FHIR R4, no auth) — **presence-based**: SCAN exposes no network linkage (no PractitionerRole network-ref; OrganizationAffiliation.network + InsurancePlan.network unpopulated — 76 plans, 0 networks). Routes to ScanDirectoryAdapter (present in directory = in-network for SCAN; best-effort state check). Rate-limits hard. Stedi SPSCN. |
| UnitedHealthcare | AZ | ACA | 87726 | via existing adapter | `https://transparency-in-coverage.uhc.com/` | public-fhir | Existing public Optum FHIR adapter (uhc). Stedi 87726 — supported. |
| UnitedHealthcare | AZ | Commercial | 87726 | via existing adapter | `https://transparency-in-coverage.uhc.com/` | public-fhir | Existing public Optum FHIR adapter (uhc). Stedi 87726 — supported. |
| UnitedHealthcare | AZ | Medicare Advantage | 87726 | via existing adapter | `https://transparency-in-coverage.uhc.com/` | public-fhir | Existing public Optum FHIR adapter (uhc). Stedi 87726 — supported. |
| Wellcare (Centene) | AZ | Medicare Advantage | — | `https://iopc-pd.api.centene.com/iopc/pd/fhir/providerdirectory` (verified) | `https://www.centene.com/price-transparency-files.html` | public-fhir | Verified public PDEX Plan-Net (FHIR R4, auth: None). **Prod egress must be WAF-allowlisted by Centene** or queries 403 — see SIGNUP-CHECKLIST. review: Stedi id still open — umbrella 68069 vs WellCare 4032 (KFNLV); resolver's 68068 is behavioral-health. |
| Wellpoint / Amerigroup (Elevance) | AZ | Medicare Advantage | — | — | — | needs-authorized-api | Auth-gated: metadata is public but data queries on the registered path return 403 without OAuth2 creds. Register at wellpoint.com/developers. review: WLPNT/RUWTL vs resolver 26375 (Amerigroup) unreconciled — Stedi id left for review. |
| Aetna | CO-Denver | Commercial | 60054 | — | `https://health1.aetna.com/app/public/` | needs-authorized-api | Stedi 60054; provider-directory FHIR is OAuth2-gated (needs-authorized-api). |
| Aetna | CO-Denver | Medicare Advantage | 60054 | — | `https://health1.aetna.com/app/public/` | needs-authorized-api | Stedi 60054; provider-directory FHIR is OAuth2-gated (needs-authorized-api). |
| BCBS / Empire (Anthem / Elevance) | CO-Denver | ACA | — | — | `https://www.anthem.com/machine-readable-file` | needs-authorized-api | review: Stedi id varies by state BCBS affiliate (CA 040); resolver's 81508 (Empire/SOMOS) unconfirmed — left for review. |
| BCBS / Empire (Anthem / Elevance) | CO-Denver | Medicare Advantage | — | — | `https://www.anthem.com/machine-readable-file` | needs-authorized-api | review: Stedi id varies by state BCBS affiliate (CA 040); resolver's 81508 (Empire/SOMOS) unconfirmed — left for review. |
| BCBS / Empire (Anthem / Elevance) | CO-Denver | Commercial | — | — | `https://www.anthem.com/machine-readable-file` | needs-authorized-api | review: Stedi id varies by state BCBS affiliate (CA 040); resolver's 81508 (Empire/SOMOS) unconfirmed — left for review. |
| Cigna Healthcare | CO-Denver | ACA | 62308 | `https://fhir.cigna.com/ProviderDirectory/v1` (verified) | `https://www.cigna.com/legal/compliance/machine-readable-files` | public-fhir | Verified public FHIR + existing cigna-fhir adapter. Stedi 62308 (enrollment required before live 270/271). |
| Cigna Healthcare | CO-Denver | Commercial | 62308 | `https://fhir.cigna.com/ProviderDirectory/v1` (verified) | `https://www.cigna.com/legal/compliance/machine-readable-files` | public-fhir | Verified public FHIR + existing cigna-fhir adapter. Stedi 62308 (enrollment required before live 270/271). |
| Colorado Department of Health Care Policy & Financing | CO-Denver | Traditional Medicaid | SKCO0 | — | — | none | SKCO0 — authoritative CO Medicaid id. Govt program: no PDEX/TiC. |
| Healthspring | CO-Denver | Medicare Advantage | — | `https://p-hi2.digitaledge.cigna.com/ProviderDirectory/v1` (verified) | `https://www.cigna.com/legal/compliance/machine-readable-files` | public-fhir | Verified public FHIR (Cigna Medicare brand). review: Stedi id — research 52192 vs resolver 63092 disagree — left for review. |
| Humana | CO-Denver | Medicare Advantage | 61101 | `https://fhir.humana.com/api` (verified) | `https://developers.humana.com/syntheticdata/Resource/PCTFilesList?fileType=innetwork` | public-fhir | Verified public FHIR + existing humana-fhir adapter. Stedi 61101 — supported. |
| Kaiser Permanente | CO-Denver | Commercial | — | `https://kpx-service-bus.kp.org/service/hp/mhpo/healthplanproviderv1rc` (verified) | `https://healthy.kaiserpermanente.org/support/transparency-coverage` | public-fhir | Verified public PDEX Plan-Net (Smile CDR, FHIR 4.0.1) — no auth/registration; national incl. CO (24k locations). Networks resolve to Commercial/Medicaid/Medicare orgs. review: Stedi id still open — regional ids (NorCal 94135, SoCal 94285); CO id unconfirmed. |
| Kaiser Permanente | CO-Denver | Medicare Advantage | — | `https://kpx-service-bus.kp.org/service/hp/mhpo/healthplanproviderv1rc` (verified) | `https://healthy.kaiserpermanente.org/support/transparency-coverage` | public-fhir | Verified public PDEX Plan-Net (Smile CDR, FHIR 4.0.1) — no auth/registration; national incl. CO (24k locations). Networks resolve to Commercial/Medicaid/Medicare orgs. review: Stedi id still open — regional ids (NorCal 94135, SoCal 94285); CO id unconfirmed. |
| Novitas Solutions, Inc. | CO-Denver | Traditional Medicare | — | — | — | none | review: Traditional Medicare per-state ids; resolver's 04312 (Medicare Oklahoma) wrong for CO. CMS-owned data (NPPES). |
| SelectHealth | CO-Denver | ACA | — | — | `https://selecthealth.org/disclaimers/machine-readable-data` | needs-authorized-api | **Registered** (MuleSoft portal). Base `https://api.selecthealth.org/provider-directory/v1/fhir` → 401 without creds. Uses STANDARD PDEX `network-reference` model → wires via FhirPdexAdapter + client-credentials auth once creds land. review: Stedi id SX107/TLTBQ unconfirmed. |
| SelectHealth | CO-Denver | Medicare Advantage | — | — | `https://selecthealth.org/disclaimers/machine-readable-data` | needs-authorized-api | **Registered** (MuleSoft portal). Base `https://api.selecthealth.org/provider-directory/v1/fhir` → 401 without creds. Uses STANDARD PDEX `network-reference` model → wires via FhirPdexAdapter + client-credentials auth once creds land. review: Stedi id SX107/TLTBQ unconfirmed. |
| UnitedHealthcare | CO-Denver | Commercial | 87726 | via existing adapter | `https://transparency-in-coverage.uhc.com/` | public-fhir | Existing public Optum FHIR adapter (uhc). Stedi 87726 — supported. |
| UnitedHealthcare | CO-Denver | Dual Eligible (FIDE SNP) | 87726 | via existing adapter | `https://transparency-in-coverage.uhc.com/` | public-fhir | Existing public Optum FHIR adapter (uhc). Stedi 87726 — supported. |
| UnitedHealthcare | CO-Denver | Medicare Advantage | 87726 | via existing adapter | `https://transparency-in-coverage.uhc.com/` | public-fhir | Existing public Optum FHIR adapter (uhc). Stedi 87726 — supported. |
| EmblemHealth | NY | Commercial | 13551 | — | `https://transparency.emblemhealth.com/` | needs-authorized-api | 13551 — resolver + research agree. HealthTranzform endpoint (`prodtzinterop.healthtranzformdev.com/providerdirectory`) is open/no-auth with real practitioners, but **network data is broken** — `plannet-ParticipatingNetwork` Orgs resolve to *person names* and `Organization?type=network` is empty, so in-network can't be determined. NOT wireable until clean network data. Eligibility via Stedi 13551. |
| Aetna | FL-South Florida | Commercial | 60054 | — | `https://health1.aetna.com/app/public/` | needs-authorized-api | Stedi 60054; provider-directory FHIR is OAuth2-gated (needs-authorized-api). |
| Aetna | FL-South Florida | Medicare Advantage | 60054 | — | `https://health1.aetna.com/app/public/` | needs-authorized-api | Stedi 60054; provider-directory FHIR is OAuth2-gated (needs-authorized-api). |
| Aetna Better Health | FL-South Florida | Managed Medicaid | ABH01 | — | — | needs-authorized-api | ABH01 — resolver + research agree. |
| Align Senior Health Plan | FL-South Florida | Medicare Advantage | — | — | — | needs-authorized-api | review: per-state ids; FL=ASFL1 (research); resolver gave ASCA1 (CA), wrong-state. |
| Ambetter (Centene) | FL-South Florida | ACA | 68069 | `https://iopc-pd.api.centene.com/iopc/pd/fhir/providerdirectory` (verified) | `https://www.centene.com/price-transparency-files.html` | public-fhir | Verified public PDEX Plan-Net (FHIR R4, auth: None). Centene umbrella 68069. **Prod egress must be WAF-allowlisted by Centene** or queries 403 — see SIGNUP-CHECKLIST. |
| AmeriHealth Caritas | FL-South Florida | ACA | — | `https://api-ext.amerihealthcaritas.com/NCEX/provider-api` (verified) | — | public-fhir | Verified public FHIR (NCEX/NC path). review: per-state ids (NC 81671/NANCR, PA CRQTA); resolver's 83148 differs — id left for review. |
| AmeriHealth Caritas | FL-South Florida | Medicare Advantage | — | `https://api-ext.amerihealthcaritas.com/NCEX/provider-api` (verified) | — | public-fhir | Verified public FHIR (NCEX/NC path). review: per-state ids (NC 81671/NANCR, PA CRQTA); resolver's 83148 differs — id left for review. |
| AvMed | FL-South Florida | ACA | 59274 | — | — | needs-authorized-api | 59274 — resolver + research agree. FHIR endpoint stale (TLS expired) → not seeded. |
| AvMed | FL-South Florida | Commercial | 59274 | — | — | needs-authorized-api | 59274 — resolver + research agree. FHIR endpoint stale (TLS expired) → not seeded. |
| AvMed | FL-South Florida | Medicare Advantage | 59274 | — | — | needs-authorized-api | 59274 — resolver + research agree. FHIR endpoint stale (TLS expired) → not seeded. |
| BCBS / Empire (Anthem / Elevance) | FL-South Florida | ACA | — | — | `https://www.anthem.com/machine-readable-file` | needs-authorized-api | review: Stedi id varies by state BCBS affiliate (CA 040); resolver's 81508 (Empire/SOMOS) unconfirmed — left for review. |
| BCBS / Empire (Anthem / Elevance) | FL-South Florida | Commercial | — | — | `https://www.anthem.com/machine-readable-file` | needs-authorized-api | review: Stedi id varies by state BCBS affiliate (CA 040); resolver's 81508 (Empire/SOMOS) unconfirmed — left for review. |
| BCBS / Empire (Anthem / Elevance) | FL-South Florida | Managed Medicaid | — | — | `https://www.anthem.com/machine-readable-file` | needs-authorized-api | review: Stedi id varies by state BCBS affiliate (CA 040); resolver's 81508 (Empire/SOMOS) unconfirmed — left for review. |
| BCBS / Empire (Anthem / Elevance) | FL-South Florida | Medicare Advantage | — | — | `https://www.anthem.com/machine-readable-file` | needs-authorized-api | review: Stedi id varies by state BCBS affiliate (CA 040); resolver's 81508 (Empire/SOMOS) unconfirmed — left for review. |
| Cigna Healthcare | FL-South Florida | ACA | 62308 | `https://fhir.cigna.com/ProviderDirectory/v1` (verified) | `https://www.cigna.com/legal/compliance/machine-readable-files` | public-fhir | Verified public FHIR + existing cigna-fhir adapter. Stedi 62308 (enrollment required before live 270/271). |
| Cigna Healthcare | FL-South Florida | Commercial | 62308 | `https://fhir.cigna.com/ProviderDirectory/v1` (verified) | `https://www.cigna.com/legal/compliance/machine-readable-files` | public-fhir | Verified public FHIR + existing cigna-fhir adapter. Stedi 62308 (enrollment required before live 270/271). |
| Community Care Plan | FL-South Florida | Managed Medicaid | — | — | — | none | review: FL Medicaid MCO; resolver candidate 59064 unconfirmed; PDF-only directory. |
| Curative | FL-South Florida | Commercial | CURTV | — | `https://curative.com/transparency-in-coverage-rates` | needs-authorized-api | CURTV — resolver + research agree. Provider search is login-gated. |

## Counts

- Total roster rows: **67**
- Rows with `fhir_base_url`: **22** (Cigna 6, Humana 2, Devoted 1, Healthspring 2, AmeriHealth Caritas 2, Kaiser 2, Molina 2, Centene 4, Scan 1).
- Rows with `tic_url`: **42**
- Rows with a Stedi id: **36**
- By `directory_access`: `needs-authorized-api` 30 · `none` 8 · `public-fhir` 29

## Needs authorized API / no public source

Payers with **no machine-queryable public directory** today (the engine cannot route an automated network-status leg; eligibility still works via Stedi 270/271 where an id exists):

- **Aetna** (needs-authorized-api) — Stedi 60054; provider-directory FHIR is OAuth2-gated (needs-authorized-api).
- **Alignment Health Plan** (needs-authorized-api) — CCHPC — resolver + research agree.
- **Arizona Health Care Cost Containment System (AHCCCS)** (none) — BEUZA — authoritative AZ Medicaid id. Govt program: no PDEX/TiC (the program IS the network).
- **BCBS / Empire (Anthem / Elevance)** (needs-authorized-api) — review: Stedi id varies by state BCBS affiliate (CA 040); resolver's 81508 (Empire/SOMOS) unconfirmed — left for review.
- **DES/Division of Developmental Disabilities** (none) — review: no standalone EDI payer id; ALTCS eligibility routes through AHCCCS (BEUZA).
- **EternalHealth** (needs-authorized-api) — review: research candidate RP037; resolver found no confident match — left for review.
- **Gold Kidney Health Plan** (needs-authorized-api) — A6865 — resolver + research agree.
- **Health Choice / BCBS / (Anthem / Elevance)** (needs-authorized-api) — review: BCBSAZ-administered; resolver's 130 (Anthem Indiana) is wrong-state — left for review.
- **Wellpoint / Amerigroup (Elevance)** (needs-authorized-api) — auth-gated: registered-path FHIR returns 403 without OAuth2 creds. Register at wellpoint.com/developers.
- **Mercy Care** (none) — review: resolver's 22248 (AmeriHealth Caritas PA) is wrong; no confirmed AZ id. Govt/Medicaid MCO.
- **Noridian Healthcare Solutions, LLC** (none) — review: Traditional Medicare uses per-state ids; resolver's 03302 (Medicare North Dakota) is wrong. CMS-owned data (NPPES).
- **Colorado Department of Health Care Policy & Financing** (none) — SKCO0 — authoritative CO Medicaid id. Govt program: no PDEX/TiC.
- **Novitas Solutions, Inc.** (none) — review: Traditional Medicare per-state ids; resolver's 04312 (Medicare Oklahoma) wrong for CO. CMS-owned data (NPPES).
- **SelectHealth** (needs-authorized-api) — **registered**; base `api.selecthealth.org/provider-directory/v1/fhir` (401 without creds); STANDARD PDEX `network-reference` model → wires via FhirPdexAdapter + client-credentials once creds land. Stedi id SX107/TLTBQ unconfirmed.
- **EmblemHealth** (needs-authorized-api) — 13551. HealthTranzform endpoint open/no-auth but **network data broken** (ParticipatingNetwork Orgs → person names; no network-type orgs) → not wireable until clean data.
- **Aetna Better Health** (needs-authorized-api) — ABH01 — resolver + research agree.
- **Align Senior Health Plan** (needs-authorized-api) — review: per-state ids; FL=ASFL1 (research); resolver gave ASCA1 (CA), wrong-state.
- **AvMed** (needs-authorized-api) — 59274 — resolver + research agree. FHIR endpoint stale (TLS expired) → not seeded.
- **Community Care Plan** (none) — review: FL Medicaid MCO; resolver candidate 59064 unconfirmed; PDF-only directory.
- **Curative** (needs-authorized-api) — CURTV — resolver + research agree. Provider search is login-gated.

## Review queue (Stedi ids deliberately not baked)

These rows kept `needs_payer_id` because the resolver proposal was wrong-state / wrong-line-of-business or disagreed with research. Re-run the resolver and human-`--apply` from `stedi-proposals.txt` after confirming:

- **BCBS / Empire (Anthem / Elevance)** — review: Stedi id varies by state BCBS affiliate (CA 040); resolver's 81508 (Empire/SOMOS) unconfirmed — left for review.
- **DES/Division of Developmental Disabilities** — review: no standalone EDI payer id; ALTCS eligibility routes through AHCCCS (BEUZA).
- **EternalHealth** — review: research candidate RP037; resolver found no confident match — left for review.
- **Health Choice / BCBS / (Anthem / Elevance)** — review: BCBSAZ-administered; resolver's 130 (Anthem Indiana) is wrong-state — left for review.
- **Healthspring** — review: Cigna Medicare brand; research 52192 vs resolver 63092 disagree — left for review.
- **Mercy Care** — review: resolver's 22248 (AmeriHealth Caritas PA) is wrong; no confirmed AZ id. Govt/Medicaid MCO.
- **Molina Healthcare** — directory now public-fhir (verified PDEX); only the **Stedi id** stays in review: resolver's 20934 (Molina Illinois) wrong-state; AZ medical routes via Availity/Emdeon.
- **Noridian Healthcare Solutions, LLC** — review: Traditional Medicare uses per-state ids; resolver's 03302 (Medicare North Dakota) is wrong. CMS-owned data (NPPES).
- **Wellcare (Centene)** — directory now public-fhir (shared Centene PDEX); only the **Stedi id** stays in review: umbrella 68069 vs WellCare 4032 (KFNLV); resolver's 68068 is behavioral-health.
- **Kaiser Permanente** — directory now public-fhir (verified PDEX); only the **Stedi id** stays in review: regional ids (NorCal 94135, SoCal 94285); CO id unconfirmed; resolver's 91051 (WA) wrong-region.
- **Novitas Solutions, Inc.** — review: Traditional Medicare per-state ids; resolver's 04312 (Medicare Oklahoma) wrong for CO. CMS-owned data (NPPES).
- **SelectHealth** — review: research candidate SX107/TLTBQ; resolver found no confident match — left for review.
- **Align Senior Health Plan** — review: per-state ids; FL=ASFL1 (research); resolver gave ASCA1 (CA), wrong-state.
- **Community Care Plan** — review: FL Medicaid MCO; resolver candidate 59064 unconfirmed; PDF-only directory.
