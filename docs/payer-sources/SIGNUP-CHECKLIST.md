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
| Wellpoint / Amerigroup | `https://totalview.healthos.elevancehealth.com/resources/registered/Wellpoint/api/v1/fhir` | public, verified, wired |
| AmeriHealth Caritas | `https://api-ext.amerihealthcaritas.com/NCEX/provider-api` | public, verified, wired |
| UnitedHealthcare | (existing Optum public adapter) | wired; portal optional for fuller API |
| Oscar | (existing public adapter) | wired |

## B. Free developer-portal signup → creds (the work list)

| Payer | `<P>` | Register at | Register as | Credential | FHIR base | Env vars |
|---|---|---|---|---|---|---|
| **Aetna** (**Commercial + Medicare** — one product covers both) | `AETNA` | `developerportal.aetna.com` → product **`public-medicare-providerdirectory-fhir`** | Third-Party App, directory-only | OAuth2 client | `https://apif1.aetna.com/fhir/v1/providerdirectorydata/` (PDEX 1.2.0, R4; `/metadata` public, queries need OAuth2) | `AETNA_FHIR_{CLIENT_ID,CLIENT_SECRET,BASE_URL}`, `AETNA_FHIR_TOKEN_URL=https://apif1.aetna.com/fhir/v1/fhirserver_auth/oauth2/token` |
| **Aetna Better Health** (**Medicaid — SEPARATE**, *not* in the commercial endpoint) | `ABH` | **separate product** — likely `public-providerdirectory-fhir` (unqualified) or a state-specific ABH endpoint; **confirm via `interoperabilitydevelopersupport@aetna.com`** | Medicaid plan / NPI | OAuth2 client | you provide (ABH-specific) | `ABH_FHIR_{CLIENT_ID,CLIENT_SECRET,TOKEN_URL,BASE_URL}` |
| **UnitedHealthcare** | `UHC` | UHC API Marketplace / OneHealthcare ID | Practice/NPI | API key (URL disclosed after reg) | you provide | `UHC_FHIR_{API_KEY,BASE_URL}` |
| **Anthem / Elevance BCBS** | `ANTHEM` | Elevance / Wellpoint developer portal | Practice/NPI | OAuth2 client | `https://totalview.healthos.elevancehealth.com/resources/registered/…` | `ANTHEM_FHIR_{CLIENT_ID,CLIENT_SECRET,TOKEN_URL,BASE_URL}` |
| **Kaiser Permanente** | `KAISER` | `developer.kp.org` | Practice/NPI (note: region-specific) | OAuth2 client | you provide (per region) | `KAISER_FHIR_{CLIENT_ID,CLIENT_SECRET,TOKEN_URL,BASE_URL}` |
| **Molina** | `MOLINA` | `developer.interop.molinahealthcare.com` | Practice/NPI, per state | API key / OAuth2 | you provide | `MOLINA_FHIR_{API_KEY or CLIENT_ID/SECRET,BASE_URL}` |
| **Centene** (Ambetter / Wellcare / AZ Complete) | `CENTENE` | `partners.centene.com` | Practice/NPI | portal → API creds | you provide | `CENTENE_FHIR_{CLIENT_ID,CLIENT_SECRET,TOKEN_URL,BASE_URL}` |
| **EmblemHealth** | `EMBLEM` | HealthTranzform developer portal | Practice/NPI | OAuth2 / API key | you provide | `EMBLEM_FHIR_{…,BASE_URL}` |
| **SCAN Health Plan** | `SCAN` | SCAN developer/interop portal | Practice/NPI | OAuth2 / API key | you provide | `SCAN_FHIR_{…,BASE_URL}` |
| **SelectHealth** | `SELECTHEALTH` | SelectHealth developer portal | Practice/NPI | OAuth2 / API key | you provide | `SELECTHEALTH_FHIR_{…,BASE_URL}` |
| **Alignment Health** | `ALIGNMENT` | Alignment interoperability portal | Practice/NPI | OAuth2 / API key | you provide | `ALIGNMENT_FHIR_{…,BASE_URL}` |
| **Align Senior Care** | `ALIGNSENIOR` | AllyAlign / portal | Practice/NPI | OAuth2 / API key | you provide | `ALIGNSENIOR_FHIR_{…,BASE_URL}` |
| **EternalHealth** | `ETERNAL` | AaNeel-hosted portal | Practice/NPI | OAuth2 / API key | you provide | `ETERNAL_FHIR_{…,BASE_URL}` |
| **Gold Kidney** | `GOLDKIDNEY` | AaNeel-hosted portal | Practice/NPI | OAuth2 / API key | you provide | `GOLDKIDNEY_FHIR_{…,BASE_URL}` |
| **Healthspring (Cigna Medicare)** | `HEALTHSPRING` | Cigna developer portal | Practice/NPI | OAuth2 client | you provide | `HEALTHSPRING_FHIR_{…,BASE_URL}` |
| **Health Choice AZ** | `HEALTHCHOICE` | BCBSAZ / Innovaccer (`azblue.innovaccer.com`) | Practice/NPI | API key | you provide | `HEALTHCHOICE_FHIR_{API_KEY,BASE_URL}` |

> Portal URLs move — confirm the exact current URL at signup. Where the base URL is "you provide," the
> payer discloses it only after registration; paste it into `<P>_FHIR_BASE_URL`.

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
