# DISCOVERY-bcbstx.md — BCBS Texas "Provider Finder" contract **+ BLOCKER**

**Discovered:** 2026-06-22, live inspection of `bcbstx.com` → `my.providerfinderonline.com`
(guest access). Fourth payer attempt (pivot after Humana). See `DISCOVERY.md`,
`DISCOVERY-devoted.md`, `DISCOVERY-humana.md`.
**Payer:** Blue Cross Blue Shield of Texas (HCSC) · vendor: **Zelis "Sapphire365"** · year: 2026

> **UPDATE 2026-07-14 — the recommended compliant path below is now LIVE, not just planned.**
> HCSC issued a `client_id` credential for its Sapphire PDEX Plan-Net FHIR API
> (`api.hcsc.net/providerfinder/sapphire/fhir` — previously 401 even on `/metadata`, see
> `docs/payer-sources/MATRIX.md` / `SIGNUP-CHECKLIST.md`). Wired via
> `payers/adapters/fhir_auth.py:build_apikey_fhir_adapter` (a static `client_id` request header,
> no OAuth2 token exchange) + the generic `FhirPdexAdapter` — same compliant CMS Provider
> Directory pattern already used for Humana, just with a different auth mechanism. Covers every
> `BCBS / Empire (Anthem / Elevance)(HCSC)` catalogue row (IL/TX-Houston/TX-Dallas). The Imperva-
> gated `my.providerfinderonline.com` web tool documented below remains untouched/unscraped, as
> planned — this is the CMS-mandated alternative that made scraping unnecessary.

---

## ⛔ TL;DR — directory API is behind IMPERVA bot protection; STOPPED per CLAUDE.md

BCBS-TX's "Search as a Guest" hands off to the Zelis Sapphire platform at
`my.providerfinderonline.com`. That platform has a clean JSON API — but it is behind **Imperva
(Incapsula)** bot management. Any headless request is rejected:

```
GET /api/configuration_profile/signature.json … → HTTP 403  "Not Allowed"
GET /api/providers/autocomplete.json?name=Smith… → HTTP 403  "Not Allowed"
response headers:  x-cdn: Imperva   x-iinfo: …   set-cookie: visid_incap_…, incap_ses_…
```

Signature of the protection: `x-cdn: Imperva`, `visid_incap_*` / `incap_ses_*` cookies, and a JS
challenge that POSTs to a random-word path (e.g. `/g-Ayre-our-haue-know-…`) to mint the clearance
cookie. A real browser passes the challenge; a plain `httpx`/`curl` client does not.

**Per CLAUDE.md ("Do not attempt to bypass CAPTCHAs, WAFs, or auth… document and report") and Claude
Code's security policy, discovery STOPS here.** We did **not** solve the Imperva JS challenge, harvest
or replay `incap_*` clearance cookies, or run a stealth browser to mint them. Like Humana, the
compliant path is BCBS-TX/HCSC's **CMS Provider Directory API (FHIR PDEX Plan-Net)**, which is
required to be public and auth-free — not the Imperva-gated web tool.

---

## What was learned (legitimately, via the real browser)

Documented for a future compliant integration; **not** built into a live adapter.

### Funnel
`bcbstx.com/find-care/find-a-doctor-or-hospital` → "Search as a Guest" →
`my.providerfinderonline.com/?ci=TX-UUX&corp_code=TX` (Sapphire365) → onboarding wizard
(location → plan-type → plan) → home with `network_id` set → name search.

### Network / plan model
- Integer `network_id` (default `240002020`; **Blue Advantage HMO [BAV]** resolved to `1000128`).
- Individual & Family plans → networks: **Blue Advantage HMO/Plus [BAV]** (most common),
  **MyBlue Health [BFT]** (narrow), **Blue Choice PPO [BCA]**, **Traditional [TRA]**.
- `ci` (config identifier) changes per plan, e.g. `tx-blueadvantage-retail`.
- `GET /api/marketing_plans.json?corp_code=TX&market_segment=Retail` maps plans → networks;
  `GET /api/networks.json` enumerates networks.

### Endpoints (host `my.providerfinderonline.com`, all GET JSON; **all 403 headless**)
1. `…/api/configuration_profile/signature.json?network_id=&geo_location=&locale=en&identifier=<ci>`
   → mints the `config_signature` token threaded through the other calls.
2. **Provider name search** — `…/api/providers/autocomplete.json?network_id=<id>&name=<name>&geo_location=<lat,lng>&radius=50&page=1&limit=10&ci=<ci>&sort=score desc, distance asc&config_signature=<sig>`
3. `…/api/search_specialties.json`, `…/api/networks.json`, `…/api/marketing_plans.json`.

### Response shape (`providers/autocomplete.json`) — observed in-browser
```
{ "_meta": { "counts": {"total": {"providers": 495}}, "pages": {"total": 50, "current": 1, "next": 2} },
  "providers": [ { "name": "Megan Smith", "npi_identifier": "1457803553", "out_of_network": null,
                   "provider_id": "...", "primary_field_specialty": "...", "locations": [...] }, … ] }
```
- **Identity:** `npi_identifier`, `name`, `provider_id`.
- **Participation:** results are **network-scoped** (presence ⇒ in that network); plus an explicit
  per-provider **`out_of_network`** boolean.
- **Pagination:** `page`/`limit` with `_meta.pages.total` — **no hard result cap** (good; unlike Oscar).
  A clean adapter would page by NPI/name and match `npi_identifier` exactly.

---

## Recommendation
- Do **not** build a scraper against the Imperva-gated Sapphire endpoint.
- Use the **compliant FHIR Provider Directory API** for BCBS-TX/HCSC (see `DISCOVERY-humana.md`
  recommendation; same CMS mandate applies to all impacted payers).
- Working live adapters in this repo remain **Oscar** and **Devoted** (both open). Pattern observed:
  larger/incumbent payers (Humana, BCBS-TX) gate directories behind bot management; newer/digital
  payers (Oscar, Devoted) leave them open.

## Evidence (`./.discovery/`)
- `bcbstx-autocomplete-smith.json` — in-browser provider search result (shape reference).
- `bcbstx-sig.json` — the `403 Not Allowed` body returned to a headless request (the blocker).
