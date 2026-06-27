# DISCOVERY-fhir.md — Compliant FHIR PDEX Plan-Net Provider Directory API

**Discovered/verified:** 2026-06-22. The *compliant* answer to "how do we automate Humana
(and other gated payers)." Built into `network_probe/adapters/fhir_pdex.py`.

---

## Why this exists

Humana (`DISCOVERY-humana.md`) and BCBS-TX (`DISCOVERY-bcbstx.md`) gate their **web** "Find a
Doctor" tools behind bot protection (Akamai-style sensor headers; Imperva). Fighting that is
off-limits. But under the **CMS Interoperability & Patient Access rule** (CMS-9115-F, extended by
CMS-0057-F), those same payers are *required* to publish a **public Provider Directory API** that:

- requires **no user authentication** (a payer may ask devs to register for an API key, but cannot
  put the data behind a login/WAF challenge),
- is **HL7 FHIR R4**, following the **Da Vinci PDEX Plan-Net** implementation guide,
- exposes `InsurancePlan`, `Organization`, `Location`, `Practitioner`, `PractitionerRole`,
  `HealthcareService`.

This API is *designed* for programmatic access — so it's the correct, durable, bot-protection-free
way to do network-status enrichment. One adapter works for **any** PDEX Plan-Net server.

## Verified endpoints (no auth, no login, reachable headless)

| Payer key | Base URL | Notes |
|---|---|---|
| `humana-fhir` | `https://fhir.humana.com/api` | FHIR 4.0.1, `HumanaFHIR`, 923k practitioners; identifier search + inline network display |
| `cigna-fhir` | `https://p-hi2.digitaledge.cigna.com/ProviderDirectory/v1` | **name search only** (no NPI identifier); network names via Organization read |
| `uhc` | `https://flex.optum.com/fhirpublic/R4` | UnitedHealthcare via **Optum FHIR Layer Exchange — public, no login**; identifier search; network names via Organization read |

All answer plain `curl`/`httpx` with `accept: application/fhir+json` → 200, **no cookies, key, or bot
challenge.** (Opala/Premera sits behind an Azure WAF 403.)

**UHC "no login" note:** UHC's own developer portal gates *authenticated* member APIs behind
OneHealthcare registration, which obscures the public directory. The CMS-mandated **provider
directory** is auth-free and hosted on Optum's flex server: base `https://flex.optum.com/fhirpublic`
(and `/fhirpublic2025`), metadata at `/fhirpublic/R4/metadata`. No registration required.

### Two server "modes" the adapter handles
PDEX servers vary, so `FhirPdexAdapter` is capability-tolerant:
1. **Practitioner lookup:** try `Practitioner?identifier=<NPI>` (Humana, UHC). If the server returns
   `400 not-supported` (Cigna), fall back to `Practitioner?family=&given=` and match the NPI from
   results' `identifier`.
2. **Network name:** read the `network-reference` extension's `valueReference.display` when present
   (Humana); when only an `Organization/<id>` reference is given (Cigna, UHC), resolve it with a
   bounded set of `GET Organization/<id>` reads to get `.name`.

## The query contract (what the adapter does)

1. **Provider in the directory?** `GET /Practitioner?identifier=<NPI>`
   → Bundle; `entry[].resource.id` is the practitioner key. `identifier.system` is
   `http://hl7.org/fhir/sid/us-npi`. `total: 0` ⇒ not a listed provider ⇒ OUT_OF_NETWORK.
   *(Note: chained `PractitionerRole?practitioner.identifier=<NPI>` timed out on Humana — resolve the
   id first, then query by reference.)*
2. **Which networks?** `GET /PractitionerRole?practitioner=<id>&_count=50` (follow `Bundle.link[next]`).
   The network is in a PDEX extension, **not** `PractitionerRole.network`:
   ```json
   "extension":[{"url":"http://hl7.org/fhir/us/davinci-pdex-plan-net/StructureDefinition/network-reference",
                 "valueReference":{"reference":"…/Organization/…","display":"Medicare PPO"}}]
   ```
   Collect `valueReference.display` across all roles = the provider's networks. Specialty is in
   `PractitionerRole.specialty[].coding[].display`.
3. **Match `plan_hint` to a network name** (fuzzy: exact > substring(closest length) > token recall):
   - confident match → **IN_NETWORK** (high), naming the matched network.
   - provider present, no confident match → **UNKNOWN** (medium) **listing the real networks** —
     never a wrong OON from a naming mismatch.
   - no plan hint → **IN_NETWORK** (medium): contracted with the payer; networks listed.
   - practitioner absent → **OUT_OF_NETWORK**.

## Ground truth reproduced (the cross-payer payoff)

**Kyle A Herron, MD (NPI 1679766943)** via Humana's FHIR API participates in **10 networks** incl.
`Medicare PPO`, `Natl Medicare HMO/SNP-Travel`, `HumanaGoldChoice Ntwk PFFS` — but **not `FL Medicare
HMO`**, exactly why the bot-protected web search returned 0 for that single network. So across all
four payers, the same NPI yields:

| Payer | Plan / network | Verdict |
|---|---|---|
| Oscar | FL HMO Standard "Silver Simple PCP Saver CSR 150" | **OUT_OF_NETWORK** |
| Devoted | FL HMO | **IN_NETWORK** |
| Humana (FHIR) | "Medicare PPO" | **IN_NETWORK** |
| Humana (FHIR) | "FL Medicare HMO" | **UNKNOWN** (in 10 other networks, not this one) |

## Usage

```bash
python -m network_probe.cli --payer humana-fhir --npi 1679766943 --plan "Medicare PPO"
# any PDEX server:
python -m network_probe.cli --payer fhir --base-url https://fhir.humana.com/api --npi <NPI> --plan "<network>"
```

Add a payer = one line in `KNOWN_ENDPOINTS` (its public FHIR base URL). No per-site scraping,
no bot walls.

## Sources
- HL7 Da Vinci PDEX Plan-Net IG — http://hl7.org/fhir/us/davinci-pdex-plan-net/
- CMS Provider Directory API — https://www.cms.gov/priorities/burden-reduction/overview/interoperability/frequently-asked-questions/provider-directory-api
- Humana Provider Directory API — https://developers.humana.com/provider-directory-api/doc
