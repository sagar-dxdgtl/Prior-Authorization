# Multi-source expansion — all 67 roster payers, all lanes

**Goal (user, no shortcuts):** for every roster payer, broaden the data sources beyond Stedi: map a
real Stedi payer id, register a **public FHIR PDEX Plan-Net** directory where one exists (preferred over
bespoke web portals, which are bot-protected/fragile — we do NOT bypass bot protection), and a
**TiC** index URL for NPI→TIN. Honestly document payers with no compliant public directory/TiC
(Stedi 271 still covers eligibility for those).

**Three lanes:**
- **Stedi ids** — tighten the resolver matcher (token-overlap on name fields; drop alias short-codes
  that caused `AZ/SD/ID` false matches), produce a *reviewed* proposal, bake confident ids into
  `payers/roster_seed.py` + a reseed migration (reproducible).
- **FHIR PDEX directories** — research + **verify** (fetch CapabilityStatement/`Practitioner`) the public
  Plan-Net base URL per payer; add a `fhir_base_url` to the catalogue; wire the engine to run the
  directory check for those payers (the `fhir` adapter already takes `base_url`).
- **TiC** — research + verify each payer's public TiC index URL; add `tic_url` to the catalogue; the
  streaming ingester (Slice C) consumes a chosen in-network file (bulk run needs the real file/disk).

**Engineering:** migration adds `fhir_base_url`, `tic_url`, `directory_url`, `directory_access` columns
to `payers`; seed from the research matrix; `check_eligibility` uses the payer's `fhir_base_url` for the
directory leg when present; per-payer **source-matrix doc** (`docs/payer-sources/`). Tests; no real PHI.

**Bot-protection — firm boundary (decided 2026-06-28):** we do **NOT** bypass payer-site CAPTCHA/WAF/
bot-protection, including via CAPTCHA-solving services (2captcha/capsolver). Circumventing a third
party's access controls is a ToS/CFAA/anti-circumvention problem and reverses this project's own ethics
("never bypass … blocked → document, don't circumvent"). For a payer with **no public FHIR + a
bot-walled site**, the path is an **authorized B2B channel** — Availity (Coverages/Provider APIs) or the
payer's provider API/SFTP using the **practice's own credentials** (the practice is contracted) — which
is recorded per-payer as `directory_access = "needs-availity-or-official-api"`. FHIR PDEX (CMS-mandated,
public) covers most roster plan types (MA, Medicaid/CHIP MCO, QHP/ACA), so this is rarely needed.

**Process:** research sweep (parallel, writes `docs/payer-sources/*.json`; finds public FHIR/TiC and
*records* — never bypasses — each site's access status) → I review → catalogue migration + seed +
engine wiring (implementer) → self-review → PR(s) → merge. Each PR green + reviewed. No real PHI.
EOF
