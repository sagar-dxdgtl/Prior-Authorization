# Data intake — United Vein & Vascular Centers

What to collect from the client to light up the platform. Forward the three CSV templates in this folder.
**No patient data here** — providers, entities, payers, and contract status only (NPIs are public; treat
TINs/EINs as business-sensitive → keep filled files out of email if possible, share via a secure channel).

## What we already have (no need to re-collect)
- **Payer roster** for **AZ / CO / FL / NY** (in the catalogue).
- **10 billing entities** (legal name, EIN/TIN, **Group NPI**, state) — already provided.
  - ⚠️ **Verify:** Group NPI **`1053977801`** is listed for **both** *NJ UVC Medical PLLC* (TIN 93-1867629)
    and *Vascular Health LLC* (TIN 83-4407175). Confirm whether one Group NPI really covers both entities.

## Files to collect (priority order)

### 1. `providers.csv` — individual rendering providers ★ highest priority
The Group NPIs we have are **Type-2 (org)**; provider **directories (FHIR) and network status are keyed
on the individual Type-1 NPI**. One row **per provider per location** they practice at.

| column | meaning / example |
|---|---|
| `individual_npi` | Type-1 NPI, 10 digits (e.g. `1972603934`) |
| `first_name`, `last_name` | provider name (e.g. `Kevin`, `Fradkin`) |
| `credential` | MD / DO / PA / NP |
| `specialty` | e.g. `Vascular Surgery`, `Phlebology` |
| `taxonomy_code` | NUCC taxonomy if known (e.g. `2086S0129X`) — optional |
| `entity_name` | which billing entity they render under (matches the entity list) |
| `group_npi` | that entity's Type-2 NPI (e.g. `1447023528`) |
| `tax_id` | that entity's EIN/TIN (e.g. `93-3510922`) |
| `city`, `state`, `zip` | practice location (helps directory matching) |
| `status` | `active` / `inactive` |

### 2. `provider_payer_contracts.csv` — credentialing / par status ★ the "golden record"
The client's credentialing roster: which provider/entity is **contracted (in-network)** with which payer,
per state. This is the highest-trust source — it seeds the system's golden-record overrides directly, so a
verified contract beats any scraped/inferred status. One row per (provider-or-entity × payer × state).

| column | meaning / example |
|---|---|
| `entity_name` | billing entity (e.g. `Texas UVC Medical PLLC`) |
| `group_npi`, `tax_id` | as above |
| `individual_npi` | the rendering provider, or blank for an entity/group-level contract |
| `payer_name` | e.g. `UnitedHealthcare` |
| `line_of_business` | `Commercial` / `Medicare Advantage` / `ACA` / `Medicaid` |
| `state` | e.g. `TX` |
| `par_status` | `PAR` (in-network) / `NON-PAR` / `PENDING` |
| `effective_date`, `term_date` | YYYY-MM-DD (`term_date` blank if active) |
| `plan_names` | specific plans/networks if known (e.g. `Choice Plus`) — optional |
| `notes` | anything (e.g. "credentialing in progress") |

### 3. `payer_roster_<STATE>.csv` — payer lists for the new states
Your entities operate in **GA, IL, NJ, TX** too, but the catalogue only covers AZ/CO/FL/NY. Same format
as the roster you first gave me — one per new state (or one combined file with a `state_market` column).

| column | meaning / example |
|---|---|
| `payer_name` | e.g. `Aetna` |
| `benefit_type` | `Commercial` / `Medicare Advantage` / `ACA` / `Managed Medicaid` / `Traditional Medicare` |
| `state_market` | e.g. `TX - Dallas`, `GA`, `IL`, `NJ` |
| `network_status` | `INN` (in-network) / `OON` |
| `effective` | `EFF` or a date |
| `notes` | optional |

(If collecting #3 is slow, I can **research** the major payers per state instead — just say so.)

## How each file is used
- `providers.csv` → directory (FHIR `Practitioner`) lookups + per-provider TiC matching + the NPI used in 270/271.
- `provider_payer_contracts.csv` → **golden-record overrides** (authoritative network status) + corroboration ground truth.
- `payer_roster_<STATE>.csv` → extends the payer **catalogue** to GA/IL/NJ/TX so there's something to check against there.
