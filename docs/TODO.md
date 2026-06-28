# TODO — Prior-Authorization platform

Living tracker of pending / blocked work. `☑` done · `☐` open · ⛔ = blocked on (who/what).
Keep this updated as items land. (UVVC = tenant #1; the platform is multi-client.)

## TiC — network / NPI→TIN crosswalk
- ☑ Ingester: streaming `ijson`, `--tin-file`, `--npi-file`, **external `provider_reference.location` (Pass-3)** for Cigna/Aetna-style MRFs.
- ☑ **UHC-AZ exchange** validated end-to-end — 11 AZ + 2 CO contracts, NPPES-confirmed.
- ☐ ⛔ **US IP** — **Cigna-AZ pull**: run `scripts/pull_tic_index.py --index-url <signed url> --state AZ --tin-file …` from a **US** host (AWS us-east-1 / US VPN). Cigna's MRF CloudFront is US-geo-restricted; this sandbox is India-pinned. *(AWS CLI available — test later.)*
- ☐ ⛔ **US compute** — **UHC commercial HMO files** (Choice/Navigate/Core/Doctors-Plus, 8–9 GB each): run on US AWS per `docs/payer-sources/TIC-BATCH-RUNBOOK.md` → captures the 8 non-exchange entity TINs.
- ☑ **Aetna TiC index found** — current public index = CVS proxy GCS (`transparency-proxy.aetna.com`, bucket `cost-transparency-prod-cmerittin-toc`); **not geo-blocked**, no token for public state files (old HealthSparq dead). ☑ **Aetna-CO pulled** — 6 CO contracts (TIN 475181686), NPPES-confirmed. ⚠️ Aetna publishes **mandate states only → AZ NOT in TiC** → get Aetna-AZ via the directory API / Stedi. (CVS guest-token endpoint needs an extra header for gated products — not needed for the public CO files.)
- ☐ **Humana + other AZ payers** TiC — repeat per payer (Humana index page was bot-walled; revisit via the MRF CDN).
- ☐ Persist crosswalks **per-tenant** + wire `TIN_CROSSWALK_PATH` / a DB store (multi-client).
- ☐ Verify flagged NPI **1285652651** (Gonzalez) — possible TIN-share, excluded pending a check.

## FHIR provider directories
- ☑ Public + wired: Cigna, Humana, Devoted, Wellpoint/Amerigroup, AmeriHealth Caritas, UHC (Optum), Oscar.
- ☐ ⛔ **user signup** — register dev creds per `docs/payer-sources/SIGNUP-CHECKLIST.md` (Aetna, UHC, Anthem/Elevance, Kaiser, Molina, Centene, + smaller MA plans) → put in `.env` as `<P>_FHIR_*` → I wire each.
- ☐ Build the generic **authenticated-FHIR adapter** (OAuth2 client-creds + API-key) when the first creds land.

## Eligibility — Stedi 270/271
- ☐ ⛔ **user** — confirm the `.env` key is **prod** (32-char; live test still uses Stedi mock) + do **per-payer enrollment** in the Stedi dashboard.
- ☐ ⛔ **user** — provide the mock-member **DOB** to flip `test_live_full_benefits_parse` skip→assert.
- ☐ Review `MATRIX.md` Stedi review-queue → `scripts/resolve_payer_ids.py --apply` the accepted ids.

## Client onboarding / multi-tenant
- ☐ ⛔ **client** — intake CSVs: `providers.csv`, `provider_payer_contracts.csv` (golden record), payer rosters for **GA/IL/NJ/TX**.
- ☐ Build **tenant-scoped loaders** for entities/TINs/NPIs/contracts/crosswalks (repeatable per-client onboarding); move the one hardcoded TIN seed to tenant data.
- ☐ Confirm shared Group NPI **1053977801** (NJ UVC Medical vs Vascular Health).
- ☐ Extend payer catalogue to **GA/IL/NJ/TX** (client rosters or research).

## Compliance / prod
- ☐ BAAs (Stedi + cloud); KMS in prod (`FERNET_KEYS_KMS`); remaining gaps in `docs/compliance/controls.md`.
