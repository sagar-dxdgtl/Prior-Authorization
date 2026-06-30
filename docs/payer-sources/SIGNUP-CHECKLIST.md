# Payer FHIR directory — signup checklist

Goal: get **free developer-portal credentials** for each payer's **Provider Directory FHIR API** (the
CMS-mandated, sanctioned alternative to the bot-walled find-a-doctor pages). Eligibility (270/271) is
separate — handled via Stedi keys already in `.env` (not in this doc).

**How to use:** register at the portal, register your **practice/org (NPI)** with intended use =
*provider directory / eligibility verification for treatment & operations*, then drop the credentials
into `.env` using the **exact env-var names** below and tell me the payer. I wire + test (mock → one
non-PHI live directory lookup) and flip that payer to live. Never commit `.env`.

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
| Cigna | `https://fhir.cigna.com/ProviderDirectory/v1` | public, verified, wired |
| Humana | `https://fhir.humana.com/api` | public, verified, wired |
| Devoted Health | `https://fhir.devoted.com/fhir` | public, verified, wired |
| Healthspring (Cigna Medicare) | `https://p-hi2.digitaledge.cigna.com/ProviderDirectory/v1` | public, verified (re-verified live 2026-06-29 — CapabilityStatement + data; no key) |
| AmeriHealth Caritas | `https://api-ext.amerihealthcaritas.com/NCEX/provider-api` | public, verified, wired |
| Kaiser Permanente | `https://kpx-service-bus.kp.org/service/hp/mhpo/healthplanproviderv1rc` | public, verified, wired (no OAuth2 — registration "Approval Not Required"; national incl. CO) |
| Molina Healthcare | `https://api.interop.molinahealthcare.com/ProviderDirectory` | public, verified, wired (no OAuth2; 605k practitioners, inline network names) |
| SCAN Health Plan | `https://providerdirectory.scanhealthplan.com` | public, verified, wired (no auth; **presence-based** — directory lists in-network providers but exposes no network linkage, so present = in-network for SCAN; rate-limits hard) |
| UnitedHealthcare | (existing Optum public adapter) | wired; portal optional for fuller API |
| Oscar | (existing public adapter) | wired |

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
| **HCSC Provider Finder** ⚠️ *(the `providerfinder-1.1.5` OAS / keys you applied for — NOT Cigna/Healthspring)* | `HCSC` | `api.hcsc.net` (MuleSoft) — Sapphire PDEX FHIR R4 (`/providerfinder/sapphire/fhir/Practitioner\|PractitionerRole\|Organization\|Location\|OrganizationAffiliation`) | App registration | API key / subscription (returns **401 "Authentication denied"** without it) | you provide once issued | **Out of current markets** — HCSC = BCBS of IL/TX/MT/NM/OK, not AZ/CO/FL/NY. Confirm intent before wiring. |
| **Health Choice AZ** | `HEALTHCHOICE` | BCBSAZ / Innovaccer (`azblue.innovaccer.com`) | Practice/NPI | API key | you provide | `HEALTHCHOICE_FHIR_{API_KEY,BASE_URL}` |

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

---

## After you register (per payer)
1. Put the creds in `.env` with the names above; set `<P>_FHIR_BASE_URL` (+ `TOKEN_URL` for OAuth2).
2. Tell me the payer. I'll: wire the generic authenticated-FHIR adapter to read `<P>_FHIR_*`, add the
   token/refresh flow, test it (mock first, then **one non-PHI** live `Practitioner` lookup), and flip
   that payer's catalogue `directory_access` from `needs-authorized-api` → live.
3. Repeat per payer — they're independent.

Eligibility (270/271): Stedi key already in `.env`; remaining step is **per-payer enrollment** in the
Stedi dashboard for the payers you want live coverage from.
