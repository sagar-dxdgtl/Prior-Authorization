# Payer FHIR directory — signup checklist

Goal: get **free developer-portal credentials** for each payer's **Provider Directory FHIR API** (the
CMS-mandated, sanctioned alternative to the bot-walled find-a-doctor pages). Eligibility (270/271) is
separate — handled via Stedi keys already in `.env` (not in this doc).

**How to use:** register at the portal, register your **practice/org (NPI)** with intended use =
*provider directory / eligibility verification for treatment & operations*, then drop the credentials
into `.env` using the **exact env-var names** below and tell me the payer. I wire + test (mock → one
non-PHI live directory lookup) and flip that payer to live. Never commit `.env`.

---

## Quick wins found 2026-07-06 (skip the queue for these)

A pass looking specifically for shortcuts past the standard business-registration process turned up
four real leads, in order of how actionable they are right now:

1. **Gold Kidney Health Plan — already usable, no signup needed.** Its AaNeel Connect **sandbox** is
   confirmed live: `curl -H "payer-id: f24482f7e98e49f7a141bf503e0b3b20" https://api-sandbox.aaneelconnect.com/cms/r4/providerdirectory/Practitioner`
   returns a real FHIR Bundle today, no auth at all. Production is still pending its subscription key,
   but the sandbox itself needs nothing from you — pull real Gold Kidney data now instead of waiting.
2. **Provider Partners — genuine open self-serve signup.** `pphpfhirapp.prod.healthaxis.net/register`
   is a real, no-approval-gate signup form (email/password/phone/ToS only). Worth registering to see
   what credentials/base URL land — narrow SNP network though, so low priority unless needed.
3. **Superior HealthPlan (Centene) — may not need separate registration at all.** Centene's own
   developer reference doc lists Superior in the same national brand table as Ambetter/Wellcare/Peach
   State — all on the one shared `iopc-pd.api.centene.com` endpoint. Once our prod IP is
   Centene-allowlisted (already required for Ambetter/Wellcare — see the Centene row below), try
   `Organization?name=Superior HealthPlan` there before pursuing the separate Partner Portal login.
4. **Wellpoint/Amerigroup — tested live 2026-07-06, doesn't work as-is, but points to a faster fix.**
   Reused the existing `ANTHEM_FHIR_CLIENT_ID`/`SECRET` against Wellpoint's registered path:
   `/metadata` is public either way, but `/Practitioner` returns `401 "Unable to find scope
   associated with the operation"` — tried this against both the standard Anthem token URL and a
   second `client.oauth2/registered/api/v1/token` endpoint that also accepted our credentials.
   Decoding the returned token confirms it genuinely is our existing approved Elevance app
   (`entity_name: "Quickflows AI"`, `entityType: "Third Party App"`) — so the OAuth backend really
   is shared, but Wellpoint access is a separate per-resource entitlement on top of the same app
   registration, not a different credential set. **Next step: email Elevance/Anthem support asking
   them to add the Wellpoint/Amerigroup provider-directory scope to our existing "Quickflows AI"
   app registration** — likely faster than filing Wellpoint's own from-scratch registration
   (`wellpoint.com/developers`, "several weeks" per their site).

**Confirmed dead ends this pass** (don't re-search): Aetna, Aetna Better Health, Alignment Health Plan,
HCSC, Health Choice AZ, EmblemHealth, and Curative all confirmed manual-approval-only or no path at
all — no sandbox/instant-key tier exists for any of them. **AvMed got worse**: its old endpoint is now
fully TLS-dead (not just expired), and the newer Sentara-hosted replacement is Patient-Access-only
(no directory search) — there is no live directory path for AvMed at all, Stedi 59274 is eligibility-
only. **Memorial Hermann HP's own documented test endpoint is confirmed DNS-dead** — the infrastructure
itself is decommissioned, not just hard to find.

---

## Env-var naming convention (so one adapter reads them all)

**OAuth2 client-credentials payers:**
```
<P>_FHIR_BASE_URL      = https://…              # the FHIR base (you get this at/after registration)
<P>_FHIR_TOKEN_URL     = https://…/oauth2/token # OAuth2 token endpoint
<P>_FHIR_CLIENT_ID     = …
<P>_FHIR_CLIENT_SECRET = …
<P>_FHIR_SCOPE         = …                       # optional, only if the API requires a scope
```
**API-key payers:**
```
<P>_FHIR_BASE_URL      = https://…
<P>_FHIR_API_KEY       = …
<P>_FHIR_API_KEY_HEADER= apikey                  # optional; header name if not the default
```
`<P>` = the payer prefix in the table below (AETNA, UHC, ANTHEM, KAISER, MOLINA, CENTENE, …).

**Concrete example — HCSC** (static header, no token exchange, base URL is fixed/public so it's
wired in code rather than `.env`): just `HCSC_FHIR_CLIENT_ID=…`, sent as request header
`client_id: <value>` on every call (see `payers/adapters/fhir_auth.py:build_apikey_fhir_adapter`).

---

## Who registers + what to provide (you do NOT need a personal NPI)

You register as a **developer / software application**, not as a healthcare provider. A personal NPI is
**not** required. (And many directory APIs need **no registration at all** — Cigna, Humana, Devoted are
fully open; just call the endpoint.)

Where a portal does require a developer account, you'll typically provide:
- **Developer/company account** — your name, work email, company/legal entity (your dev shop *or* the
  practice).
- **App registration** — an app name (e.g. `UVVC Network Verification`), a short **intended-use**
  description (be honest: *"provider-directory lookups + eligibility verification for United Vein &
  Vascular Centers, a contracted provider group — treatment & operations"*), an **OAuth2 redirect URI**
  (a placeholder like `https://localhost/callback` is fine; you can change it later), optional website.
- **Accept** the developer/API terms of use.

**If a portal asks for an NPI / Tax ID to verify a healthcare relationship:** use the **practice's**
identifiers — the 10 **Group NPIs + TINs** you already have (e.g. AZ `1548800980` / `84-3447602`) —
and register as the practice's **technology vendor / business associate**. That's the correct framing:
you're building the tool *for* a contracted practice, not impersonating a provider.

**Pick the right API product:** the **Provider Directory / Plan-Net API** (public, treatment/operations).
Do **NOT** pick the *Patient Access API* — that one requires an individual member's login/consent and is
not for directory lookups.

**Paperwork:** have a **BAA with the practice** in place (you'll handle their data downstream). Directory
data itself isn't PHI, but eligibility (270/271) will be. Some portals approve instantly; others take a
few business days.

---

## A. No signup needed — already public / wired ✅

| Payer | FHIR base | Status |
|---|---|---|
| Cigna | `https://fhir.cigna.com/ProviderDirectory/v1` | public, verified, wired — **confirmed same national endpoint for IL/GA-Atlanta/TX-Houston/TX-Dallas, 2026-07-06** |
| Humana | `https://fhir.humana.com/api` | public, verified, wired — **confirmed same national endpoint for IL/GA-Atlanta/TX-Houston/TX-Dallas, 2026-07-06** |
| Devoted Health | `https://fhir.devoted.com/fhir` | public, verified, wired — **confirmed same national endpoint for GA-Atlanta/TX-Houston, 2026-07-06** |
| Healthspring (Cigna Medicare) | `https://p-hi2.digitaledge.cigna.com/ProviderDirectory/v1` | public, verified (re-verified live 2026-06-29 — CapabilityStatement + data; no key). GA/TX rows added 2026-07-06 presumed same endpoint, not individually re-verified. |
| AmeriHealth Caritas | `https://api-ext.amerihealthcaritas.com/NCEX/provider-api` | public, verified, wired |
| Kaiser Permanente | `https://kpx-service-bus.kp.org/service/hp/mhpo/healthplanproviderv1rc` | public, verified, wired (no OAuth2 — registration "Approval Not Required"; national incl. CO). "Kaiser Foundation Health Plan of Georgia" row added 2026-07-06 **presumed** same national endpoint — not individually re-verified for GA. |
| Molina Healthcare | `https://api.interop.molinahealthcare.com/ProviderDirectory` | public, verified, wired (no OAuth2; 605k practitioners, inline network names) — **re-verified live for Texas 2026-07-06** (`/metadata` 200 + a real Houston 77042 clinic returned); TX-Houston/TX-Dallas rows added. |
| SCAN Health Plan | `https://providerdirectory.scanhealthplan.com` | public, verified, wired (no auth; **presence-based** — directory lists in-network providers but exposes no network linkage, so present = in-network for SCAN; rate-limits hard) |
| UnitedHealthcare | (existing Optum public adapter) | wired; portal optional for fuller API — **confirmed same adapter for IL/GA-Atlanta/TX-Houston/TX-Dallas** incl. the "UnitedHealthcare Community Plan" Medicaid brand (same technical product, not separate) |
| Oscar | (existing public adapter) | wired — **confirmed same adapter for GA-Atlanta/TX-Houston, 2026-07-06** |
| **Anthem / Elevance BCBS (Georgia)** | (existing `ANTHEM_FHIR_*` creds, same as CO) | **wired + confirmed live 2026-07-06** — Georgia's Anthem BCBS is directly Elevance-owned (SEC 10-K Exhibit 21); queried the existing CO credentials directly and got back real GA networks. **No new registration needed** — catalogue row added (`docs/payer-sources/MATRIX.md`), DB insert migration written (`alembic/versions/0015_il_ga_tx_markets.py`, not yet applied). |
| **HCSC Provider Finder (BCBS IL/TX/MT/NM/OK)** | `https://api.hcsc.net/providerfinder/sapphire/fhir` | **wired + confirmed live 2026-07-14** — client_id credential issued (previously 401 even on `/metadata`, "tighter-gated than Aetna"); routes via a dedicated client_id-header adapter (`HCSC_FHIR_CLIENT_ID` in `.env`), NOT the Anthem OAuth2 path — HCSC is an independent Blue licensee, not Elevance. Covers all `BCBS / Empire (Anthem / Elevance)(HCSC)` rows (IL/TX-Houston/TX-Dallas, every benefit type) — catalogue rows updated (`MATRIX.md`), DB migration applied (`alembic/versions/0019_hcsc_authorized_fhir.py`). |

> **Georgia's Anthem BCBS (and Amerigroup Community Care of Georgia) — no new signup needed.**
> Georgia's Anthem BCBS is a direct Elevance subsidiary (SEC 10-K Exhibit 21: "Blue Cross Blue Shield
> Healthcare Plan of Georgia, Inc." dba Anthem BCBS), same as Colorado. The existing `ANTHEM_FHIR_*`
> creds already in `.env` were queried directly for GA data 2026-07-06 and confirmed live GA networks
> across Commercial/ACA/Medicare Advantage/Medicaid — this only needed a new catalogue row (done, see
> `MATRIX.md`), not new registration.
>
> **Molina Healthcare — re-verified live for Texas 2026-07-06** (`/metadata` 200 + a real Houston 77042
> clinic returned) — same national endpoint already public/wired for AZ, now confirmed covering TX too.
>
> **Kaiser Foundation Health Plan of Georgia** — presumed to ride the same national Kaiser PDEX endpoint
> already public/wired (see Kaiser row above, "national incl. CO") — NOT individually re-verified for
> GA this pass; confirm with a live GA query before fully trusting.

> **AvMed:** The only known FHIR URL for AvMed has an expired TLS certificate — the endpoint is unusable as a public directory source. Stedi 270/271 (id `59274`) remains the only machine-queryable path for AvMed.

## B. Free developer-portal signup → creds (the work list)

| Payer | `<P>` | Register at | Register as | Credential | FHIR base | Env vars |
|---|---|---|---|---|---|---|
| **Aetna** (**Commercial + Medicare** — one product covers both) | `AETNA` | `developerportal.aetna.com` → product **`public-medicare-providerdirectory-fhir`** | Third-Party App, directory-only | OAuth2 client | `https://apif1.aetna.com/fhir/v1/providerdirectorydata/` (PDEX 1.2.0, R4; `/metadata` public, queries need OAuth2) | `AETNA_FHIR_{CLIENT_ID,CLIENT_SECRET,BASE_URL}`, `AETNA_FHIR_TOKEN_URL=https://apif1.aetna.com/fhir/v1/fhirserver_auth/oauth2/token` |
| **Aetna Better Health** (**Medicaid — SEPARATE**, *not* in the commercial endpoint) | `ABH` | **separate product** — likely `public-providerdirectory-fhir` (unqualified) or a state-specific ABH endpoint; **confirm via `interoperabilitydevelopersupport@aetna.com`** | Medicaid plan / NPI | OAuth2 client | you provide (ABH-specific) | `ABH_FHIR_{CLIENT_ID,CLIENT_SECRET,TOKEN_URL,BASE_URL}` |
| **UnitedHealthcare** | `UHC` | UHC API Marketplace / OneHealthcare ID | Practice/NPI | API key (URL disclosed after reg) | you provide | `UHC_FHIR_{API_KEY,BASE_URL}` |
| **Anthem / Elevance BCBS** ✅ **WIRED + LIVE (2026-06-30)** | `ANTHEM` | Elevance developer portal — **creds obtained** | Practice/NPI (Third-Party App) | OAuth2 **client-credentials** — creds in the **form body**, **no scope** (`token_type=bearer`, `expires_in=3600`) | `https://totalview.healthos.elevancehealth.com/resources/unregistered/api/v1/fhir/cms_mandate/mcd` — verified live: national, **multi-LoB** PDEX Plan-Net R4 (Commercial/ACA/Medicare/Medicaid; networks across all Anthem Blue states). NPI `identifier` search + inline `network-reference` displays. | `ANTHEM_FHIR_{BASE_URL,TOKEN_URL,CLIENT_ID,CLIENT_SECRET}` set in `.env`; token URL = `…/client.oauth2/unregistered/api/v1/token`. **Scope caveat:** this is *Elevance's* directory → authoritative only for Blues that ARE Elevance. **CO-Denver routed live** (`authorized-fhir`); AZ (BCBSAZ) & FL (Florida Blue) are independent licensees → left `needs-authorized-api` (not in this endpoint). |
| **Wellpoint / Amerigroup (Elevance)** | `WELLPOINT` | `wellpoint.com/developers` (Elevance Health developer portal) | Practice/NPI | OAuth2 client | `https://totalview.healthos.elevancehealth.com/resources/registered/Wellpoint/api/v1/fhir` (registered path; returns 403 without OAuth2 creds) | `WELLPOINT_FHIR_{CLIENT_ID,CLIENT_SECRET,TOKEN_URL,BASE_URL}` |
| **Centene** (Ambetter / Wellcare / AZ Complete) | `CENTENE` | `partners.centene.com` (portal documents the endpoint; **Authentication Type: None**, PDEX Plan-Net 1.2.0 / FHIR R4) | Practice/NPI | **none — no API key/registration exists per portal** | National: `https://iopc-pd.api.centene.com/iopc/pd/fhir/providerdirectory` · California: `https://iopc-provider.api.centene.com/iopc/provider/ca/fhir/providerdirectory` — **blocked by AWS-WAF, not auth.** CloudFront fences to **US + non-datacenter IPs**: non-US (India Jio residential) → 403 geo rule; US datacenter/VPN/**EC2/AWS** → 403 hosting-provider/anonymous-IP rule. No key to request. Endpoint + data **confirmed live & no-auth** via out-of-band US-residential check (2026-06-29): `/metadata` 200, `Practitioner?family=SMITH` → 200-record Bundle with NPIs → PDEX Plan-Net, adapter-ready. **Seeded as public-fhir** (migration `0011`, all 3 Centene-family labels) — but the engine's own egress (datacenter/AWS) is WAF-blocked, so prod will **403 every query until your prod Elastic IP `/32` is allowlisted by Centene** (email the API owner; allow rules outrank the managed-rule block). Verify from the *real prod egress* (not a proxy) once allowlisted. | — (public once IP-allowlisted; no creds) |
| **EmblemHealth** | `EMBLEM` | HealthTranzform portal (`prodtzinterop.developer.healthtranzformdev.com`) — endpoint is open/no-auth | Practice/NPI | **none, but DON'T wire** | `https://prodtzinterop.healthtranzformdev.com/providerdirectory` — reachable + real practitioners, but **network data is broken** (ParticipatingNetwork Orgs → person names; no network-type orgs), so in-network can't be determined. Not wireable until clean data. | — |
| **SelectHealth** | `SELECTHEALTH` | **Registered** at `api-selecthealth.my.site.com/s/apis` (MuleSoft) | Practice/NPI | OAuth2 client-credentials (client_id/secret) | `https://api.selecthealth.org/provider-directory/v1/fhir` (401 without creds) — STANDARD PDEX `network-reference` model → wires via FhirPdexAdapter + auth layer once creds land | `SELECTHEALTH_FHIR_{CLIENT_ID,CLIENT_SECRET,TOKEN_URL,BASE_URL}` |
| **Alignment Health** | `ALIGNMENT` | Alignment interoperability portal | Practice/NPI | OAuth2 / API key | you provide | `ALIGNMENT_FHIR_{…,BASE_URL}` |
| **Align Senior Care** | `ALIGNSENIOR` | **No API** — monthly PDF directory (AllyAlign) | n/a | **none** (public PDF download) | Parsed into `payer_directory_entries` (PyMuPDF, ~53.8k providers) and matched by name+state+zip — the PDF has no NPIs, so NPI stays on our side. App-scheduled monthly refresh (`ENABLE_DIRECTORY_REFRESH=1`); manual: `python -m network_probe.cli.load_directory`. | — |
| **EternalHealth** | `ETERNAL` | **Now:** monthly PDF (no key). **Upgrade pending:** AaNeel Connect FHIR (see Gold Kidney row — registered, awaiting key) | n/a (PDF) → AaNeel key | PDF: none. AaNeel FHIR: shared `AANEEL_SUBSCRIPTION_KEY` + `AANEEL_PAYER_ID_ETERNAL` | **Wired** via PDF today (`payer_directory_entries`, name+state+zip; refresh `ENABLE_DIRECTORY_REFRESH=1` or `python -m network_probe.cli.load_directory eternalhealth-az`). Switch to AaNeel FHIR (real network data) once the AaNeel key lands; keep PDF as fallback. | — / AaNeel |
| **Gold Kidney + EternalHealth** (AaNeel Connect — one registration covers both, + Freedom & ~9 other AaNeel payers) | `AANEEL` | `developers.aaneelconnect.com` (free) — **registered 2026-06-29, awaiting keys** | App registration | **Azure-APIM `Ocp-Apim-Subscription-Key`** (one key for all AaNeel payers) **+ `payer-id`** per payer (query or header) | Base `https://api.aaneelconnect.com/cms/r4/providerdirectory` (sandbox `https://api-sandbox.aaneelconnect.com/…`) — standard PDEX `network-reference` model → FhirPdexAdapter + key/payer-id. Sandbox payer-ids: Gold Kidney `f24482f7e98e49f7a141bf503e0b3b20`, Freedom `f7451303d8c1458e8625a057588116a9`; **get EternalHealth's** from the portal dropdown. | `AANEEL_SUBSCRIPTION_KEY`, `AANEEL_FHIR_BASE_URL`, `AANEEL_PAYER_ID_GOLDKIDNEY`, `AANEEL_PAYER_ID_ETERNAL` |
| **Healthspring (Cigna Medicare)** | `HEALTHSPRING` | none — public | none | none | `https://p-hi2.digitaledge.cigna.com/ProviderDirectory/v1` — **re-verified live 2026-06-29** (CapabilityStatement + Practitioner data); public, no key. Already wired (public-fhir). | — |
| **Community Health Choice (CHC)** | `CHC` | `developers.communityhealthchoice.org` | Practice/NPI | Sign-up gated (portal live, no public base URL disclosed pre-registration) | you provide once issued | Houston/Harris-only nonprofit HMO (Medicaid STAR/STAR+PLUS/CHIP + ACA Marketplace + MA D-SNP) — confirmed NOT serving Dallas. `CHC_FHIR_{...,BASE_URL}` |
| **Superior HealthPlan (Centene)** | `SUPERIOR` | Centene Partner Portal (TX Medicaid brand) | Practice/NPI | Portal login (unlike the shared public Centene PDEX used for Ambetter/Wellcare/Peach State — this one is gated) | you provide once issued | Confirmed Dallas SDA, **excludes Tarrant County** and has zero Harris/Houston presence — verify the clinic's exact service area before relying on this. |
| **Health Choice AZ** | `HEALTHCHOICE` | BCBSAZ / Innovaccer (`azblue.innovaccer.com`) | Practice/NPI | API key | you provide | `HEALTHCHOICE_FHIR_{API_KEY,BASE_URL}` |
| **Baylor Scott &amp; White Health Plan** | `BSW` | Inovalon DataStream — `datastream.inovalonone.com` (sandbox self-register at `datastream-sandbox.inovalonone.com/login/self-register`), documented from `bswhealthplan.com/api` | Practice/NPI | OAuth2/OIDC (Inovalon DataStream) | JS-rendered portal, no literal FHIR base URL disclosed pre-registration — not live-verified | `BSW_FHIR_{CLIENT_ID,CLIENT_SECRET,TOKEN_URL,BASE_URL}`. Stedi id **88030** already confirmed live — separate track from this directory signup. Confirmed serving Dallas/Tarrant/Collin/Denton/Ellis/Johnson/Rockwall (Mansfield-area coverage confirmed) for Commercial + MA (CMS H8142). |
| **Memorial Hermann Health Plan** | `MEMHERM` | No confirmed developer portal. Site references a "Third-Party App Developer Application Form" (CMS interoperability docs pattern) but no direct link/email found. | Practice/NPI | Unknown — not yet identified | Documented base (`apigateway.memorialhermann.org:7443/infor/CustomerApi/public`) does **NOT resolve** (NXDOMAIN) — not usable as-is | Only confirmed fallback contact: (855) 645-8448 or `healthplan.memorialhermann.org/contact-us`. Stedi id **PGRAJ** confirmed live, but Stedi's own record shows `eligibilityCheck: NOT_SUPPORTED` (837 claims only) — **registering with Stedi will NOT unlock eligibility checks for this payer**, only claims. |

> Portal URLs move — confirm the exact current URL at signup. Where the base URL is "you provide," the
> payer discloses it only after registration; paste it into `<P>_FHIR_BASE_URL`.

> **OAuth2 authed-FHIR adapter is built** (`payers/adapters/fhir_auth.py`: client-credentials token
> fetch + caching + 401-refresh in front of the generic `FhirPdexAdapter`). **Anthem is the live
> template** — the remaining OAuth2 payers (SelectHealth, Aetna, ABH, Wellpoint) now just need their
> `<P>_FHIR_*` creds in `.env` + a few wiring lines + a state-scoped catalogue flip to `authorized-fhir`.

## C. No public API — Stedi 270/271 only (no directory creds to get)

AHCCCS (AZ Medicaid, Stedi `BEUZA`), DES/DDD (routes via AHCCCS), Colorado Medicaid (`SKCO0`),
Noridian & Novitas (Traditional Medicare — CMS NPPES/Care Compare is the directory), Mercy Care &
Community Care Plan (no public PDEX found). Network = the government program; eligibility via Stedi.

**Added 2026-07-06 (IL / GA-Atlanta / TX-Houston / TX-Dallas):**
- **Illinois Dept. of Healthcare & Family Services (HFS)** — Traditional Medicaid; no Stedi id
  confirmed on Stedi's own site (a candidate `IL621` from a third-party clearinghouse is unconfirmed).
- **National Government Services (NGS)** — Traditional Medicare MAC, Jurisdiction 6 (IL/MN/WI).
- **Palmetto GBA, LLC** — Traditional Medicare MAC, Jurisdiction J (AL/GA/TN) — supersedes Cahaba GBA
  (contract 75FCMC24C0023, eff. 2024-09-01).
- **Novitas Solutions, Inc.** — Traditional Medicare MAC, Jurisdiction H (AR/CO/LA/MS/NM/OK/TX) — same
  contractor already listed for CO-Denver, now confirmed for TX-Houston/TX-Dallas too.
- **Texas Health and Human Services Commission (HHSC)** — TX Medicaid is ~100% managed-care, so HHSC
  itself isn't the network; TMHP's Online Provider Lookup (`opl.tmhp.com`) is public but not
  machine-queryable. The practical verification target is the contracted MCO (Superior/Molina/UHC
  Community Plan/Amerigroup/etc.), not HHSC directly.
- **Curative** — no developer/interoperability portal or FHIR API found (checked all provider-facing
  pages + the TiC page, 2026-07-06). Provider search stays login-gated. Only published contact:
  Provider Services (855) 414-1083 — a member-services line, not confirmed as an API/technical
  contact. Stays `needs-authorized-api` desk-research-only; re-check periodically since Curative is a
  newer entrant that may stand up a CMS-mandated Provider Directory API later.

**No signup path found at all, 2026-07-06** (real, CMS-regulated companies confirmed to exist, but no
developer portal, interoperability page, or API contact could be located despite targeted searching —
re-check periodically rather than re-searching from scratch):
- **Essence Healthcare** (IL, MA) — only general contact via essencehealthcare.com.
- **Longevity Health Plan** (IL, MA — Stedi id **LIL01** confirmed) — only `Compliance@longevityhealthplan.com`.
- **Zing Health** (IL, MA) — only `provider.services@myzinghealth.com` (untested for API access).
- **Provider Partners** (IL, MA) — possible HealthAxis-hosted portal `pphpfhirapp.prod.healthaxis.net/Login`, not confirmed reachable pre-registration. Narrow SNP network (nursing-facility residents only) — low relevance to this clinic.
- **Alliant Health Plans** (GA-Atlanta, Commercial) — only Provider Relations `ProviderRelations@AlliantPlans.com` / (800) 811-4793. **Also: confirmed service area is north Georgia only — Cobb County/Kennesaw doesn't appear in any county list, so this payer may not even serve this clinic's market.**

**Data errors found, not integration gaps — flag back to whoever sourced the client benefit list rather than pursuing registration:**
- **MCC Health** (TX-Houston + TX-Dallas, Commercial) — no matching Texas company could be identified after real searching (the closest name match is a Dallas direct-contracting platform with no member benefits/claims/directory at all, not an insurer).
- **Abilis Health Plan** (TX-Houston + TX-Dallas, MA) — confirmed real company (2026 rebrand of "Signature Advantage," CMS H2400) but operates **only in Kentucky/Tennessee** — zero Texas presence in CMS filings, TDI, or its own service-area page.
- **"BCBS (Anthem)"** (IL, MA) — almost certainly a duplicate of the HCSC-owned "BCBS / Empire (Anthem / Elevance)(HCSC)" MA row; Elevance holds no Blue license in Illinois at all.
- **Clear Spring Health** (IL + GA-Atlanta, MA) — **already exited Medicare Advantage entirely as of 2026-06-01** — this plan no longer exists as an active MA offering; deprioritize rather than register.

---

## After you register (per payer)
1. Put the creds in `.env` with the names above; set `<P>_FHIR_BASE_URL` (+ `TOKEN_URL` for OAuth2).
2. Tell me the payer. I'll: wire the generic authenticated-FHIR adapter to read `<P>_FHIR_*`, add the
   token/refresh flow, test it (mock first, then **one non-PHI** live `Practitioner` lookup), and flip
   that payer's catalogue `directory_access` from `needs-authorized-api` → live.
3. Repeat per payer — they're independent.

Eligibility (270/271): Stedi key already in `.env`; remaining step is **per-payer enrollment** in the
Stedi dashboard for the payers you want live coverage from.
