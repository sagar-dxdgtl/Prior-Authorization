# Next steps — turning the 3 lanes on

Legend: 👤 = your action · 🤖 = mine (once your input lands). Nothing here needs a bot-bypass.

---

## Lane 1 — FHIR provider directories (network status)
The compliant replacement for the bot-walled find-a-doctor sites. See
`docs/payer-sources/SIGNUP-CHECKLIST.md`.
1. 👤 Register the practice (NPI) at each payer's developer portal (free) → get OAuth2 `client_id/secret`
   or an API key. Already-public (no signup): Cigna, Humana, Devoted, Wellpoint/Amerigroup, AmeriHealth
   Caritas, UHC (Optum), Oscar.
2. 👤 Drop creds in `.env` using the `<P>_FHIR_*` names from the checklist; paste the base URL the portal
   gives you into `<P>_FHIR_BASE_URL`.
3. 🤖 I wire the authenticated-FHIR adapter for that payer (token caching/refresh, reuses the SSRF guard),
   test it (mock → one non-PHI `Practitioner` lookup), and flip its catalogue `directory_access` to live.
   One payer at a time, independent.

## Lane 2 — TiC (Transparency-in-Coverage) → NPI→TIN + contracted network
**The code is done** (Slice C: `scripts/ingest_tic.py`, streaming `ijson` ingester) and `tic_url` is
recorded for ~42 roster rows. The blocker is **data volume + your provider list**, not credentials.

How a TiC pull actually works:
- A payer's **index ("table of contents") file** lists, per plan/region, the URLs of the **in-network
  rate files**.
- Each in-network file is **huge** (GB–TB, gzipped JSON) and contains negotiated rates referencing
  `provider_groups` = `NPI[] + TIN`.
- Presence of an `(NPI, TIN)` in a plan's in-network file = that provider is **contracted** for that plan.

Steps:
1. 👤 **Give me the practice's provider NPI list** (one NPI per line). This is the key input — it's the
   `--npi-file` filter that turns a multi-GB file into a tiny, relevant crosswalk (only your providers).
2. 👤 **Pick priority payers/plans** (e.g. the AZ commercial/MA networks you bill most). TiC is
   commercial/group + MA only — govt Medicaid/Traditional Medicare have none.
3. 🤖 For each: fetch the payer's index → locate the right in-network file for that plan/region → stream-
   ingest it with your NPI filter → write the `npi,tin` crosswalk. (Large files run as a background job;
   needs disk/bandwidth — I'll report size/time per payer.)
4. 🤖 Point `TIN_CROSSWALK_PATH` at the combined crosswalk → `TinScopeSource` now corroborates the
   billing TIN against the contracted in-network TIN on every check.
5. 👤 Re-run monthly (TiC files are monthly snapshots) — I can script this once the first few work.

Caveats: a few **index pages are bot-walled** (e.g. `developers.humana.com`); the in-network *blob*
URLs are usually direct CDN/Azure/S3 downloads once you have them from the index — if the index itself
is walled, that payer's index needs an authorized/manual fetch (same B2B principle as Lane 1).

## Lane 3 — Stedi eligibility (270/271)
Key already in `.env`.
1. 👤 Confirm the `.env` key is **prod** (current one is 32 chars; the live test still uses Stedi's mock)
   and do **per-payer enrollment** in the Stedi dashboard for the payers you want live coverage from.
2. 👤 Provide the documented mock-member **DOB** to flip `test_live_full_benefits_parse` from skip→assert
   (member `AETNA9wcSu` is valid; the exact DOB is in Stedi's mock-requests doc).
3. ⚠️ Real-member prod calls only in a **BAA'd + KMS** environment, never local dev (see
   `docs/compliance/controls.md`).

## Lane 4 — Stedi payer-id review queue
1. 👤 Review the `review` rows in `docs/payer-sources/MATRIX.md` (each says why the fuzzy id was withheld).
2. 🤖 For the ids you accept, I run `scripts/resolve_payer_ids.py --apply` (or bake them into the seed)
   so those rows flip `needs_payer_id` → mapped.

---

## What unblocks the most, fastest
1. 👤 **Provider NPI list** → unlocks Lane 2 (TiC) immediately — I can pull the first payer that day.
2. 👤 **First FHIR creds** (Aetna is the cleanest) → I wire Lane 1 payer #1 end-to-end as the template.
3. 👤 **Confirm prod Stedi key + enroll 2–3 payers** → live eligibility for those.

Tell me which you want first; each lane is independent.
