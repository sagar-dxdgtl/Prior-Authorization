# Re-point the Rodriguez/Li "Payer directory" lane to Devoted's compliant FHIR directory — demo design

**Date:** 2026-07-02
**Branch:** demo (main is left untouched — it already documents the URL)
**Status:** approved, ready to implement

## Discovery (what prompted this)

The demo's Rodriguez / Devoted CO PPO / Dr Jing Li (NPI `1629339312`) case was the one case
where the **payer directory was wrong**: Devoted's public **Algolia "Find a Doctor"** widget
(`https://en2fbm9o9o-dsn.algolia.net/1/indexes/*/queries`) lists Li as **IN-network** for CO PPO
(6 records, 37 counties), so the demo relied on a seeded **Availity golden-record override** to
flip the verdict to OON.

Live investigation found a **second, independent directory published by Devoted** — the
CMS-mandated public **FHIR PDEX Plan-Net Provider Directory** at `https://fhir.devoted.com/fhir`
(HAPI FHIR R4, **no auth**). Verified live:

- Directory is populated: 9,904 practitioners, 133,500 roles, 21 states incl. Colorado.
- The member's exact network — **"CO PPO"** — exists as an `Organization` (type `ntwk`) and is
  populated (49 of 200 sampled CO roles reference it).
- Control (NPI `1003010133` → Jon Lepley) resolves via identifier search + direct read.
- **Jing Li (NPI 1629339312) is absent from the entire directory:**
  `Practitioner?identifier=1629339312` → total 0; `Practitioner/practitioner-1629339312` → 404;
  name search "Jing Li" → 0.

So Devoted's **compliant** directory returns **OON directly** (provider not listed), independently
agreeing with Availity + the phone verification and contradicting Devoted's own Algolia marketing
widget. This is the source that `TODO-network-accuracy.md §2` marked "base URL still TBD".

## Goal

For the Rodriguez/Li case in the demo, make the **"Payer directory (website / API)"** lane use
Devoted's compliant FHIR directory instead of the stale Algolia widget — so the lane's URL becomes
`https://fhir.devoted.com/fhir/...` and it returns **OON directly**, no override crutch. Refresh the
local cache so it serves offline.

## Non-goals

- **Main is untouched.** It already lists `https://fhir.devoted.com/fhir` as public/verified/wired.
- No new corroboration source / verdict-pipeline change (that was the alternative "auto cross-check"
  approach; not chosen). This is a re-point of the existing lane.
- No change to the other demo cases. The Algolia `DevotedAdapter` and its `devoted` payer key stay
  as-is (still exercised by the Craig/TX-HMO sample and the manual "Devoted (Algolia)" payer option).
- No committed PHI / no committed cache — `.cache/` stays gitignored; the cache refresh is local.

## Changes

1. **`network_probe/adapters/fhir_pdex.py`** — add `"devoted-fhir":
   "https://fhir.devoted.com/fhir"` to `KNOWN_ENDPOINTS`. `service.py` auto-registers it as an
   adapter key (same mechanism as `humana-fhir` / `cigna-fhir` / `uhc`).

2. **`network_probe/api.py`**
   - `SAMPLES`: the Rodriguez sample `payer` `"devoted"` → `"devoted-fhir"` (keep npi/name/plan).
   - `PAYERS`: add a `devoted-fhir` catalogue entry ("Devoted Health — FHIR Provider Directory
     (compliant CMS API)").
   - `GROUND_TRUTH`: add `("devoted-fhir", "1629339312")` → OON, source "Availity + phone",
     note that Devoted's Algolia widget still lists Li as IN (stale) — the two-directories-disagree
     insight is preserved as context.
   - `BENCHMARK`: Rodriguez row `how` → "compliant FHIR directory absence (provider not listed)"
     and confidence to match the FHIR verdict; it is still `caught`.

3. **Cache (local, gitignored):** prewarm by running the Devoted/Li check once so the FHIR
   Practitioner responses cache into `.cache/`. The now-moot Availity override may stay (it is keyed
   to `payer="devoted"`, so it will not match `devoted-fhir`) or be removed; leaving it is harmless.

## Verdict path after the change

`/api/check {payer: "devoted-fhir", npi: "1629339312", plan: "PPO", last_name: "Li"}`
→ `FhirPdexAdapter(payer_name="devoted-fhir")` → `Practitioner?identifier=1629339312` absent →
`OUT_OF_NETWORK` (medium), `source_url = https://fhir.devoted.com/fhir/Practitioner?identifier=1629339312`.
The UI's `renderGroundTruth` already emits the correct "how" line when the directory returns OON
directly (index.html:376) — no UI code change required.

## Tests

- `tests/test_api.py`: the existing Rodriguez assertion (currently `payer="devoted"`) updates to
  `devoted-fhir`; assert the verdict is OON and `source_url` contains `fhir.devoted.com`.
- Add a focused offline test that `FhirPdexAdapter(payer_name="devoted-fhir")` resolves the base URL
  and returns OON for an absent NPI (replay an empty-bundle response via `httpx.MockTransport`).
- A `-m live` check that `GET https://fhir.devoted.com/fhir/metadata` is a CapabilityStatement and
  NPI 1629339312 is absent (skips if the host is unreachable).
