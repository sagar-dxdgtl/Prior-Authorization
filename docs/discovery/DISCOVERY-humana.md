# DISCOVERY-humana.md — Humana "Find Care" endpoint contract **+ BLOCKER**

**Discovered:** 2026-06-22, by live inspection of `https://findcare.humana.com` (guest access).
Third payer attempt. See `DISCOVERY.md` (Oscar) and `DISCOVERY-devoted.md` (Devoted).
**Payer:** Humana (Medicare Advantage / Medicaid / Employer) · **Coverage year:** "Current" (2026)

---

## ⛔ TL;DR — provider-search endpoints are BOT-PROTECTED; STOPPED per CLAUDE.md

The provider-search API works **in a real browser** but is **blocked for any headless client**. The
search POSTs require a set of dynamically-generated anti-bot **sensor headers** (`yignxymxbx-a/-b/-c/
-d/-f/-z`, ~6 KB of obfuscated client-JS output — the signature of an Akamai/Imperva-style bot
manager). A clean headless request with the correct `ocp-apim-subscription-key` but **without** those
sensor headers is rejected at the nginx edge:

| Endpoint (headless `curl`, no sensor headers) | Result |
|---|---|
| `POST …/providersearch/predict/allwithfolders` (name typeahead) | **502 Bad Gateway** (×3, not transient) |
| `POST …/providersearch/allwithfolders` (results) | **blocked** (same edge) |
| `GET …/session/v1/config`, `…/featureflags`, `…/token/validate/guest`, `…/session/v1/guest` | 200 |

So protection is **targeted specifically at the provider-search endpoints** (the valuable data), not
the whole gateway.

**Per CLAUDE.md ("Do not attempt to defeat bot protection… document and report — that's a valid,
useful finding, not a failure to brute-force past") and Claude Code's security policy, discovery
STOPS here.** We did **not** reverse-engineer the sensor JS, forge/replay sensor tokens, run a
headless browser to mint them, or otherwise evade the WAF. A production Humana integration should use
a **compliant access path** — e.g. Humana's CMS-mandated Provider Directory API / interoperability
APIs, or a contracted data feed — not the bot-protected web endpoint.

---

## What was learned (legitimately, via the real browser)

Documented so a future compliant integration has the shape; **not** built into a live adapter.

### Funnel
`findcare.humana.com` → guest "Search as a guest" (enter location) → `/type-of-care` (Medical) →
`/medical?view=networks` (choose network) → `/medical?view=search` (name/specialty search) →
`/medical?view=results`.

### Network model
Network is an integer `networkId`. The FL network picker offered (2026):
- Medicaid: *FL Healthy Horizons LTC*, *Humana Healthy Horizons in FL*
- Medicare: *Medicare PPO/Employer PPO Plus*, *Medicare PPO*, *Humana Honor PPO*,
  *Natl Medicare HMO/SNP-Travel*, *National Employer HMO – Travel*, *HumanaGoldChoice Ntwk PFFS*,
  **FL Medicare HMO → `networkId 3920`** (only id confirmed, from the request body), *CarePlus*,
  *Employer HMO*, *CarePlus CareComplete/Breeze*

### Endpoints (host `findcare.humana.com`, all POST `application/json`)
1. **Name typeahead** — `/apim-gateway/api/v1/providersearch/predict/allwithfolders`
   body: `{customerId,coverageTypeId,networkId,value:"<name>",distance,count,location:{lat,lng,zipCode},type:"medical",coverageYear:"Current",…}`
   → `{"predictions":[{"value":"Danay Herrera","type":"name",…}]}` (name strings only, no NPI)
2. **Results (verdict-bearing)** — `/apim-gateway/api/v1/providersearch/allwithfolders`
   body: `{customerId,networkId:3920,value:"Herron",distance,offset,limit,sortBy,location:{…},filters:[],coverageYear:"Current",…}`
   → `{resultCount, results:[…], filters, attributes}`. For **Herron in FL Medicare HMO → `resultCount: 0`**
   (in-browser), i.e. no Herron in that network. (No populated result captured — declined to keep
   driving the protected flow.)

### Auth / headers observed on the protected POSTs
- `ocp-apim-subscription-key: eb9163d9e6f14d318da1f8ac34a14757` (Azure APIM key, embedded in page)
- `ocp-apim-trace: true`
- **`yignxymxbx-a/-b/-c/-d/-f/-z`** — anti-bot sensor payload (dynamic, client-JS-generated) ← the blocker
- `x-dtpc` (Dynatrace), plus Quantum Metric / Qualtrics instrumentation

---

## Recommendation

- **Do not** build a scraping adapter against the protected web endpoint. It cannot run headless
  without defeating bot protection, which is out of scope by policy.
- For a real third payer with a *working live probe*, prefer a **softer** directory (Oscar and
  Devoted both proved open). Among the CLAUDE.md list, Cigna/BCBS-TX may be similarly protected and
  should be probed the same cautious way (try headless; if a WAF/sensor challenge appears, stop).
- For Humana specifically, pursue the **official/compliant API** route.

## Evidence (`./.discovery/`)
- `humana-predict-herron.json` — typeahead response (name suggestions only).
- `humana-results-herron.json` — results response, `resultCount: 0` for Herron / FL Medicare HMO.
- `humana-headless-resp.txt` — the 502 Bad Gateway returned to a headless request (the blocker).
