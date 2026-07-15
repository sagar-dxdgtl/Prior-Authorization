# Medicaid (Meridian + HCSC) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire directory access for Meridian Health (IL) and HCSC's "Blue Cross Community Health Plans" (IL Medicaid), reusing already-live infrastructure with no new adapter code.

**Architecture:** Pure data/config change in `src/network_probe/payers/roster_seed.py`. Meridian Health gets an existing-constant `SOURCES` fix (`_CENTENE_FHIR`, same pattern as the shared Centene-family endpoint already used by Ambetter/Wellcare/Peach State/Superior HealthPlan). The HCSC Medicaid product is added as a 4th `benefit_type` row under the *existing* `"BCBS / Empire (Anthem / Elevance)(HCSC)"` label — deliberately not a new label — so it inherits that label's existing `SOURCES` tuple and keeps routing through `_authed_builder_for()`'s `"hcsc"` string match in `src/network_probe/domain/service.py`.

**Tech Stack:** Python 3.12, pytest, no DB/migration work (`payer_rows()` is pure Python, same as the prior UMR plan).

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-15-medicaid-meridian-hcsc-design.md` — read it first; this plan implements it exactly.
- No new adapter code, no new `SOURCES` entry for the HCSC Medicaid product — it must inherit the existing `"BCBS / Empire (Anthem / Elevance)(HCSC)"` `SOURCES` tuple via the label match.
- The HCSC Medicaid row's `key` (derived as `slug(label)-slug(state)`) must equal `"bcbs-empire-anthem-elevance-hcsc-il"` — identical to the existing IL HCSC rows — proving it did not become a separate routable identity.
- Meridian Health's existing `ROSTER` tuple (`stedi_payer_id="13189"`, `enrollment_status="needs_enrollment"`) does not change — only its `SOURCES` tuple.
- Aetna Better Health: out of scope, not touched. Community Care Plan (FL): out of scope, separate plan.
- `docs/payer-sources/MATRIX.md` is hand-maintained (no generator script) — edit directly, keep `## Counts` arithmetically consistent.

---

## File Structure

- **Modify** `src/network_probe/payers/roster_seed.py` — fix `SOURCES["Meridian Health"]`; add one new `ROSTER` tuple under the existing HCSC label; extend that label's `SOURCES` comment by one sentence.
- **Modify** `tests/test_payer_sources.py` — extend `_FHIR_PAYERS` with Meridian; add one new test for the HCSC Medicaid row.
- **Modify** `docs/payer-sources/MATRIX.md` — add a new Meridian Health table row (it currently has none at all — a pre-existing gap, not something earlier work addressed), add a new HCSC Medicaid table row, fix two stale "no roster row of its own yet" mentions, update `## Counts`.

No new files.

---

### Task 1: Fix Meridian Health's `SOURCES` entry

**Files:**
- Modify: `src/network_probe/payers/roster_seed.py:467-474` (`SOURCES["Meridian Health"]`)
- Test: `tests/test_payer_sources.py`

**Interfaces:**
- Consumes: the existing `_CENTENE_FHIR` constant (already defined in `roster_seed.py`, used by `SOURCES["Ambetter (Centene)"]` and others — do not redefine it).
- Produces: nothing new consumed by later tasks (Task 2 is independent of this one).

- [ ] **Step 1: Write the failing test**

In `tests/test_payer_sources.py`, find the `_FHIR_PAYERS` dict (currently lines 28-41):

```python
_FHIR_PAYERS = {
    "Cigna Healthcare": "https://fhir.cigna.com/ProviderDirectory/v1",
    "Humana": "https://fhir.humana.com/api",
    "Devoted Health": "https://fhir.devoted.com/fhir",
    "Healthspring": HEALTHSPRING_FHIR,
    "AmeriHealth Caritas": "https://api-ext.amerihealthcaritas.com/NCEX/provider-api",
    "Kaiser Permanente": KAISER_FHIR,
    "Molina Healthcare": MOLINA_FHIR,
    "Ambetter (Centene)": CENTENE_FHIR,
    "Arizona Complete Health - Complete Care Plan (Centene)": CENTENE_FHIR,
    "Wellcare (Centene)": CENTENE_FHIR,
    "UnitedHealthcare Community Plan": UHC_FHIR,
    "UMR": UHC_FHIR,
}
```

Replace with (one new line, `"Meridian Health": CENTENE_FHIR`, added to the Centene-family group):

```python
_FHIR_PAYERS = {
    "Cigna Healthcare": "https://fhir.cigna.com/ProviderDirectory/v1",
    "Humana": "https://fhir.humana.com/api",
    "Devoted Health": "https://fhir.devoted.com/fhir",
    "Healthspring": HEALTHSPRING_FHIR,
    "AmeriHealth Caritas": "https://api-ext.amerihealthcaritas.com/NCEX/provider-api",
    "Kaiser Permanente": KAISER_FHIR,
    "Molina Healthcare": MOLINA_FHIR,
    "Ambetter (Centene)": CENTENE_FHIR,
    "Arizona Complete Health - Complete Care Plan (Centene)": CENTENE_FHIR,
    "Wellcare (Centene)": CENTENE_FHIR,
    "Meridian Health": CENTENE_FHIR,
    "UnitedHealthcare Community Plan": UHC_FHIR,
    "UMR": UHC_FHIR,
}
```

This dict already drives a loop in the existing `test_seeded_fhir_base_urls_present` (`for label, url in _FHIR_PAYERS.items(): assert by_label.get(label) == url, label`) — no new test function needed, this one entry is enough to turn it into the test for this fix.

- [ ] **Step 2: Run the test to verify it fails**

Run: `source .venv/bin/activate && python3 -m pytest tests/test_payer_sources.py::test_seeded_fhir_base_urls_present -v`
Expected: FAIL — `AssertionError: Meridian Health` (from `assert by_label.get(label) == url, label`; actual is `None` since `SOURCES["Meridian Health"]` still has `fhir_base_url=None`).

- [ ] **Step 3: Fix `SOURCES["Meridian Health"]`**

In `src/network_probe/payers/roster_seed.py`, find this block:

```python
    "Meridian Health": (
        # Illinois Medicaid MCO. Its own "Find a Provider" tool is a JS SPA (no public FHIR/API
        # found) -- same treatment as other directory-access=none payers.
        None,
        None,
        "https://findaprovider.ilmeridian.com",
        "none",
    ),
```

Replace with:

```python
    "Meridian Health": (
        # Illinois Medicaid MCO. Its own "Find a Provider" tool is a JS SPA (no public FHIR/API
        # found) -- BUT Meridian Health Plan of Illinois has been a wholly-owned Centene
        # subsidiary since 2018 (grouped with WellCare/Sunshine Health/Buckeye), and the shared
        # Centene national PDEX directory (_CENTENE_FHIR) already serves it: verified live
        # 2026-07-15 against this client's own Meridian-labeled provider (Kevin Petermann, NPI
        # 1588744650) -- a real hit, with real Illinois-specific network affiliations (IL SNP,
        # Exchange IL, etc.). Those network names don't literally say "Meridian" -- a
        # Centene-platform-wide pattern already accepted for the existing Superior HealthPlan
        # (Centene) row, not a Meridian-specific gap. See docs/superpowers/specs/
        # 2026-07-15-medicaid-meridian-hcsc-design.md.
        _CENTENE_FHIR,
        None,
        "https://findaprovider.ilmeridian.com",
        "public-fhir",
    ),
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `source .venv/bin/activate && python3 -m pytest tests/test_payer_sources.py::test_seeded_fhir_base_urls_present -v`
Expected: PASS.

- [ ] **Step 5: Run the full non-db test file to check for regressions**

Run: `source .venv/bin/activate && python3 -m pytest tests/test_payer_sources.py -k "not db" -v`
Expected: all pass, same count as before this task started (23 passed, 2 deselected — confirm this matches your actual starting point; if it doesn't, note the discrepancy in your report rather than assuming it's fine).

- [ ] **Step 6: Commit**

```bash
git add src/network_probe/payers/roster_seed.py tests/test_payer_sources.py
git commit -m "$(cat <<'EOF'
fix(payers): wire Meridian Health (IL) to the shared Centene directory

Meridian Health Plan of Illinois has been a Centene subsidiary since
2018. Verified live against this client's own Meridian-labeled provider
(Kevin Petermann, NPI 1588744650) on the shared _CENTENE_FHIR endpoint --
a real hit with real IL-specific network data, even though the network
names don't literally say "Meridian" (a Centene-platform-wide pattern,
already accepted for the existing Superior HealthPlan row).
EOF
)"
```

---

### Task 2: Add the HCSC Medicaid row

**Files:**
- Modify: `src/network_probe/payers/roster_seed.py` (`ROSTER` list, IL section; `SOURCES["BCBS / Empire (Anthem / Elevance)(HCSC)"]` comment)
- Test: `tests/test_payer_sources.py`

**Interfaces:**
- Consumes: the existing `_HCSC_FHIR` constant and the existing `SOURCES["BCBS / Empire (Anthem / Elevance)(HCSC)"]` tuple — neither is modified except adding one sentence to the comment.
- Produces: one new `ROSTER` tuple: `("BCBS / Empire (Anthem / Elevance)(HCSC)", "Managed Medicaid", "IL", "G00621", "needs_enrollment")`.

- [ ] **Step 1: Write the failing test**

In `tests/test_payer_sources.py`, add a new test after `test_hcsc_without_creds_refuses_unauthenticated` (which ends the `# ---- authorized-FHIR (static client_id header, e.g. HCSC) routing ----` section, currently around line 276):

```python
def test_hcsc_medicaid_row_seeded():
    row = next(
        r
        for r in payer_rows()
        if r["label"] == HCSC_LABEL and r["state"] == "IL" and r["benefit_type"] == "Managed Medicaid"
    )
    assert row["stedi_payer_id"] == "G00621"
    assert row["enrollment_status"] == "needs_enrollment"
    assert row["network_indicator_supported"] is False
    # Inherited from the label-level SOURCES entry, not a new one:
    assert row["fhir_base_url"] == HCSC_FHIR
    assert row["directory_access"] == "authorized-fhir"
    # Regression (design finding #5): this MUST reuse the existing HCSC label/key so it keeps
    # routing through _build_hcsc_adapter via the "hcsc" substring match in _authed_builder_for().
    # A brand-new label like "Blue Cross Community Health Plans" would silently bypass that match
    # and misroute to a "credentials not configured" error despite real, working creds.
    assert row["key"] == "bcbs-empire-anthem-elevance-hcsc-il"
    assert "Blue Cross Community Health Plans" not in SOURCES
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `source .venv/bin/activate && python3 -m pytest tests/test_payer_sources.py::test_hcsc_medicaid_row_seeded -v`
Expected: FAIL — `StopIteration` (the `next(...)` call finds no matching row yet, since the `ROSTER` tuple doesn't exist).

- [ ] **Step 3: Add the `ROSTER` row**

In `src/network_probe/payers/roster_seed.py`, find this block in the IL section:

```python
    ("BCBS / Empire (Anthem / Elevance)(HCSC)", "ACA", "IL", None, "needs_payer_id"),
    ("BCBS / Empire (Anthem / Elevance)(HCSC)", "Commercial", "IL", None, "needs_payer_id"),
    ("BCBS / Empire (Anthem / Elevance)(HCSC)", "Medicare Advantage", "IL", None, "needs_payer_id"),
    ("Cigna Healthcare", "Commercial", "IL", "62308", "needs_enrollment"),
```

Replace with:

```python
    ("BCBS / Empire (Anthem / Elevance)(HCSC)", "ACA", "IL", None, "needs_payer_id"),
    ("BCBS / Empire (Anthem / Elevance)(HCSC)", "Commercial", "IL", None, "needs_payer_id"),
    ("BCBS / Empire (Anthem / Elevance)(HCSC)", "Medicare Advantage", "IL", None, "needs_payer_id"),
    # Retail brand "Blue Cross Community Health Plans (SM)" -- HCSC's IL Medicaid product, same
    # underlying entity/technical integration as the other 3 IL rows above (same SOURCES entry,
    # same _HCSC_FHIR endpoint, same HCSC_FHIR_CLIENT_ID). Verified live 2026-07-15: Organization
    # "Blue Cross Community Health Plans (SM)" (id network-11152019) is active on the Sapphire
    # FHIR endpoint with 637,497 PractitionerRole entries. Stedi id G00621 confirmed live on
    # Stedi's own site. Deliberately NOT a separate roster label -- see
    # docs/superpowers/specs/2026-07-15-medicaid-meridian-hcsc-design.md finding #5 for why a new
    # label here would silently break routing in _authed_builder_for().
    ("BCBS / Empire (Anthem / Elevance)(HCSC)", "Managed Medicaid", "IL", "G00621", "needs_enrollment"),
    ("Cigna Healthcare", "Commercial", "IL", "62308", "needs_enrollment"),
```

(Note: `℠` in source comments — use the plain-ASCII `(SM)` in the Python comment above to avoid any encoding surprises in the `.py` file; the `℠` character is fine in the Markdown docs touched in Task 3.)

- [ ] **Step 4: Extend the label's `SOURCES` comment by one sentence**

Find this block:

```python
    "BCBS / Empire (Anthem / Elevance)(HCSC)": (
        # HCSC (Health Care Service Corp) owns BCBS in IL/TX/MT/NM/OK — an independent licensee, NOT
        # Elevance, same pattern as BCBSAZ/Florida Blue (routes to _HCSC_FHIR, never _ANTHEM_FHIR).
        # **Directory LIVE 2026-07-14** — HCSC issued a client_id credential (previously 401'd even
        # on /metadata, "tighter-gated than Aetna"); routes to the client_id-header-authed adapter
        # (HCSC_FHIR_CLIENT_ID in .env), same one FHIR base covering all 3 markets this label
        # spans (IL/TX-Houston/TX-Dallas) and every benefit type. Medicaid product "Blue Cross
        # Community Health Plans" has a separately confirmed Stedi id (G00621, IL) but no roster
        # row of its own yet. Dev portal: interoperability.hcsc.com.
        _HCSC_FHIR, None, None, "authorized-fhir",
    ),
```

Replace with:

```python
    "BCBS / Empire (Anthem / Elevance)(HCSC)": (
        # HCSC (Health Care Service Corp) owns BCBS in IL/TX/MT/NM/OK — an independent licensee, NOT
        # Elevance, same pattern as BCBSAZ/Florida Blue (routes to _HCSC_FHIR, never _ANTHEM_FHIR).
        # **Directory LIVE 2026-07-14** — HCSC issued a client_id credential (previously 401'd even
        # on /metadata, "tighter-gated than Aetna"); routes to the client_id-header-authed adapter
        # (HCSC_FHIR_CLIENT_ID in .env), same one FHIR base covering all 3 markets this label
        # spans (IL/TX-Houston/TX-Dallas) and every benefit type. Medicaid product "Blue Cross
        # Community Health Plans" has a separately confirmed Stedi id (G00621, IL) and, as of
        # 2026-07-15, its own IL Managed Medicaid ROSTER row below (still this same label/SOURCES
        # entry, not a new one). Dev portal: interoperability.hcsc.com.
        _HCSC_FHIR, None, None, "authorized-fhir",
    ),
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `source .venv/bin/activate && python3 -m pytest tests/test_payer_sources.py::test_hcsc_medicaid_row_seeded -v`
Expected: PASS.

- [ ] **Step 6: Run the full non-db test file to check for regressions**

Run: `source .venv/bin/activate && python3 -m pytest tests/test_payer_sources.py -k "not db" -v`
Expected: all pass (24 passed, 2 deselected — Task 1 doesn't add a new test function, only a dict entry, so its checkpoint stays at 23; this task adds exactly one new test function, `test_hcsc_medicaid_row_seeded`, making 24. If Task 1's checkpoint count differed from 23, adjust this expectation to that count + 1).

- [ ] **Step 7: Commit**

```bash
git add src/network_probe/payers/roster_seed.py tests/test_payer_sources.py
git commit -m "$(cat <<'EOF'
feat(payers): add HCSC Medicaid (Blue Cross Community Health Plans, IL)

Adds this as a 4th benefit_type row under the EXISTING HCSC label rather
than a new roster label, so it inherits the already-live SOURCES entry
(_HCSC_FHIR, authorized-fhir) and keeps routing through
_authed_builder_for()'s "hcsc" substring match -- a new label here would
have silently bypassed that match and misrouted to a "credentials not
configured" error despite real, working creds (HCSC_FHIR_CLIENT_ID
already in .env). Verified live: Organization "Blue Cross Community
Health Plans" is active with 637,497 PractitionerRole entries. Stedi id
G00621 already confirmed on Stedi's own site.
EOF
)"
```

---

### Task 3: Update `docs/payer-sources/MATRIX.md`

**Files:**
- Modify: `docs/payer-sources/MATRIX.md`

**Interfaces:**
- Consumes: the Meridian fix and HCSC Medicaid row from Tasks 1-2 (documents them; does not affect their behavior).
- Produces: nothing consumed by later tasks.

- [ ] **Step 1: Add a new Meridian Health table row**

`docs/payer-sources/MATRIX.md` currently has **no row at all** for Meridian Health — a pre-existing gap in this hand-maintained file, not something earlier passes addressed (confirm with `grep -n "Meridian" docs/payer-sources/MATRIX.md` before you start — it should print nothing). Find this anchor in the IL section of the table:

```
| Longevity Health Plan | IL | Medicare Advantage | LIL01 | — | — | needs-authorized-api | **RESEARCHED.** Longevity Health Plan Inc. (IL entity). Stedi id **LIL01** confirmed live on Stedi's own site — flipped `needs_payer_id`→`needs_enrollment`. No FHIR endpoint found (`fhir./api.longevityhealthplan.com` don't resolve). No dev portal — only `Compliance@longevityhealthplan.com`. **Georgia caveat: Longevity has no direct GA plan of its own** (GA served via a separate "National Carriers" partner brand) — the GA-Atlanta row for this label below is likely mislabeled; flag for client confirmation. |
| National Government Services, Inc. (NGS) | IL | Traditional Medicare |  | — | — | none | Confirmed: MAC **Jurisdiction 6** (IL/MN/WI) for Traditional Medicare. Same `none` treatment as Noridian(AZ)/Novitas(CO) — CMS-owned data (NPPES), no Stedi id. |
```

Insert the new row alphabetically between them (Meridian sorts between Longevity and National Government Services):

```
| Longevity Health Plan | IL | Medicare Advantage | LIL01 | — | — | needs-authorized-api | **RESEARCHED.** Longevity Health Plan Inc. (IL entity). Stedi id **LIL01** confirmed live on Stedi's own site — flipped `needs_payer_id`→`needs_enrollment`. No FHIR endpoint found (`fhir./api.longevityhealthplan.com` don't resolve). No dev portal — only `Compliance@longevityhealthplan.com`. **Georgia caveat: Longevity has no direct GA plan of its own** (GA served via a separate "National Carriers" partner brand) — the GA-Atlanta row for this label below is likely mislabeled; flag for client confirmation. |
| Meridian Health | IL | Managed Medicaid | 13189 | `https://iopc-pd.api.centene.com/iopc/pd/fhir/providerdirectory` (verified) | — | public-fhir | **Directory LIVE 2026-07-15** — Meridian Health Plan of Illinois has been a Centene subsidiary since 2018 (grouped with WellCare/Sunshine Health/Buckeye), and the shared Centene national PDEX directory already serves it. Verified against this client's own Meridian-labeled provider (Kevin Petermann, NPI 1588744650): a real hit, with real Illinois-specific network affiliations (IL SNP, Exchange IL, Exchange Solutions, CC National Medicare HMO, Exchange Solutions Marathon). Network names don't literally say "Meridian" — a Centene-platform-wide pattern, already accepted for the existing Superior HealthPlan (Centene) row, not Meridian-specific. Stedi 13189 — confirmed, not yet enrolled. |
| National Government Services, Inc. (NGS) | IL | Traditional Medicare |  | — | — | none | Confirmed: MAC **Jurisdiction 6** (IL/MN/WI) for Traditional Medicare. Same `none` treatment as Noridian(AZ)/Novitas(CO) — CMS-owned data (NPPES), no Stedi id. |
```

- [ ] **Step 2: Add the HCSC Medicaid table row**

Find this anchor (the 3 existing HCSC IL rows):

```
| BCBS / Empire (Anthem / Elevance)(HCSC) | IL | ACA |  | `https://api.hcsc.net/providerfinder/sapphire/fhir` (authorized, verified) | — | authorized-fhir | **Directory LIVE 2026-07-14** — HCSC issued a `client_id` credential (`HCSC_FHIR_CLIENT_ID` in .env; previously 401 even on `/metadata`, "tighter-gated than Aetna"). HCSC (Health Care Service Corp) owns BCBS IL — an independent licensee, NOT Elevance (same pattern as BCBSAZ/Florida Blue) — routes to a dedicated client_id-header adapter, never the Anthem OAuth2 one. Verified live: standard FHIR 4.0.1 CapabilityStatement, Practitioner `identifier` search + inline `network-reference` (e.g. real hit: Jeffery E Friedman, NPI 1336160274, in "Blue Cross Medicare Advantage (PPO)℠" among 9 networks). Medicaid product "Blue Cross Community Health Plans" has confirmed Stedi id **G00621** but no roster row of its own yet. Dev portal: `interoperability.hcsc.com`. |
| BCBS / Empire (Anthem / Elevance)(HCSC) | IL | Commercial |  | `https://api.hcsc.net/providerfinder/sapphire/fhir` (authorized, verified) | — | authorized-fhir | **Directory LIVE 2026-07-14** — same HCSC client_id-header adapter as the ACA row above (one FHIR base covers every benefit type for this label). |
| BCBS / Empire (Anthem / Elevance)(HCSC) | IL | Medicare Advantage |  | `https://api.hcsc.net/providerfinder/sapphire/fhir` (authorized, verified) | — | authorized-fhir | **Directory LIVE 2026-07-14** — same HCSC client_id-header adapter as the ACA row above (one FHIR base covers every benefit type for this label). |
```

Replace with (the ACA row's note is also updated to drop the stale "no roster row of its own yet" claim, and a new Managed Medicaid row is appended):

```
| BCBS / Empire (Anthem / Elevance)(HCSC) | IL | ACA |  | `https://api.hcsc.net/providerfinder/sapphire/fhir` (authorized, verified) | — | authorized-fhir | **Directory LIVE 2026-07-14** — HCSC issued a `client_id` credential (`HCSC_FHIR_CLIENT_ID` in .env; previously 401 even on `/metadata`, "tighter-gated than Aetna"). HCSC (Health Care Service Corp) owns BCBS IL — an independent licensee, NOT Elevance (same pattern as BCBSAZ/Florida Blue) — routes to a dedicated client_id-header adapter, never the Anthem OAuth2 one. Verified live: standard FHIR 4.0.1 CapabilityStatement, Practitioner `identifier` search + inline `network-reference` (e.g. real hit: Jeffery E Friedman, NPI 1336160274, in "Blue Cross Medicare Advantage (PPO)℠" among 9 networks). Medicaid product "Blue Cross Community Health Plans" has confirmed Stedi id **G00621** — see the Managed Medicaid row below. Dev portal: `interoperability.hcsc.com`. |
| BCBS / Empire (Anthem / Elevance)(HCSC) | IL | Commercial |  | `https://api.hcsc.net/providerfinder/sapphire/fhir` (authorized, verified) | — | authorized-fhir | **Directory LIVE 2026-07-14** — same HCSC client_id-header adapter as the ACA row above (one FHIR base covers every benefit type for this label). |
| BCBS / Empire (Anthem / Elevance)(HCSC) | IL | Managed Medicaid | G00621 | `https://api.hcsc.net/providerfinder/sapphire/fhir` (authorized, verified) | — | authorized-fhir | **Directory LIVE 2026-07-15** — retail brand "Blue Cross Community Health Plans℠", same underlying HCSC technical integration as the other 3 IL rows here (deliberately kept as the same roster label, not split into a new one — a new label would have bypassed the `"hcsc"` substring match in `_authed_builder_for()` and silently misrouted). Verified live: `Organization` "Blue Cross Community Health Plans℠" (id `network-11152019`) is active with 637,497 `PractitionerRole` entries. Stedi **G00621** confirmed live on Stedi's own site. |
| BCBS / Empire (Anthem / Elevance)(HCSC) | IL | Medicare Advantage |  | `https://api.hcsc.net/providerfinder/sapphire/fhir` (authorized, verified) | — | authorized-fhir | **Directory LIVE 2026-07-14** — same HCSC client_id-header adapter as the ACA row above (one FHIR base covers every benefit type for this label). |
```

- [ ] **Step 3: Fix the second stale "no roster row of its own yet" mention**

Find this block in the "Needs authorized API / no public source" narrative section:

```
- **BCBS / Empire (Anthem / Elevance)(HCSC)** — **now LIVE 2026-07-14** (`authorized-fhir`: all IL/
  TX-Houston/TX-Dallas rows, every benefit type — HCSC issued a `client_id` credential, previously
  401 even on `/metadata`). HCSC (Health Care Service Corp) owns BCBS in IL/TX/MT/NM/OK — an
  independent licensee, NOT Elevance (same as BCBSAZ/Florida Blue), so it routes to its own
  client_id-header adapter, never the Anthem OAuth2 one. Medicaid product "Blue Cross Community
  Health Plans" has confirmed Stedi id **G00621** (IL) but no roster row of its own yet. Dev
  portal: `interoperability.hcsc.com`.
```

Replace with:

```
- **BCBS / Empire (Anthem / Elevance)(HCSC)** — **now LIVE 2026-07-14** (`authorized-fhir`: all IL/
  TX-Houston/TX-Dallas rows, every benefit type — HCSC issued a `client_id` credential, previously
  401 even on `/metadata`). HCSC (Health Care Service Corp) owns BCBS in IL/TX/MT/NM/OK — an
  independent licensee, NOT Elevance (same as BCBSAZ/Florida Blue), so it routes to its own
  client_id-header adapter, never the Anthem OAuth2 one. Medicaid product "Blue Cross Community
  Health Plans" has confirmed Stedi id **G00621** (IL) and, as of 2026-07-15, its own IL Managed
  Medicaid roster row (same label/SOURCES entry — see the Matrix above). Dev
  portal: `interoperability.hcsc.com`.
```

- [ ] **Step 4: Update the `## Counts` section**

Find:

```
## Counts

- Total roster rows: **193** (grown past the original 180 as later passes — UHC/UnitedHealthcare
  Community Plan wiring, UMR, etc. — added rows; see git history for the incremental deltas rather
  than a single point-in-time total).
- Rows with `fhir_base_url`: **102** (UnitedHealthcare 18, Cigna 13, **BCBS / Empire (Anthem /
  Elevance)(HCSC) 10** — new 2026-07-14, Humana 8, Molina 8, Ambetter/Centene 6, Anthem/Elevance 6,
  Healthspring 5, Wellcare/Centene 5, **UMR 9** — new 2026-07-15, Devoted 3, Kaiser Permanente 2,
  AmeriHealth Caritas 2, **UnitedHealthcare Community Plan 2** — bug fix 2026-07-15 (was
  incorrectly `None`), AZ Complete Health 1, Scan 1, Kaiser Foundation Health Plan of Georgia 1,
  Peach State 1, WellCare/AllWell 1).
- Rows with `tic_url`: **109**
- Rows with a Stedi id: **96**
- By `directory_access`: `needs-authorized-api` 66 · `authorized-fhir` 16 (+10 HCSC, 2026-07-14) ·
  `none` 20 · `public-fhir` 89 (+9 UMR, 2026-07-15) · `pdf-directory` 2
```

Replace with:

```
## Counts

- Total roster rows: **194** (grown past the original 180 as later passes — UHC/UnitedHealthcare
  Community Plan wiring, UMR, HCSC Medicaid, etc. — added rows; see git history for the
  incremental deltas rather than a single point-in-time total).
- Rows with `fhir_base_url`: **104** (UnitedHealthcare 18, Cigna 13, **BCBS / Empire (Anthem /
  Elevance)(HCSC) 11** — +1 for the new IL Managed Medicaid row, 2026-07-15, Humana 8, Molina 8,
  Ambetter/Centene 6, Anthem/Elevance 6, Healthspring 5, Wellcare/Centene 5, **UMR 9** — new
  2026-07-15, Devoted 3, Kaiser Permanente 2, AmeriHealth Caritas 2, **UnitedHealthcare Community
  Plan 2** — bug fix 2026-07-15 (was incorrectly `None`), AZ Complete Health 1, **Meridian Health
  1** — new 2026-07-15 (was incorrectly `none`), Scan 1, Kaiser Foundation Health Plan of Georgia
  1, Peach State 1, WellCare/AllWell 1).
- Rows with `tic_url`: **109**
- Rows with a Stedi id: **97** (+1 for the new HCSC Medicaid row's confirmed **G00621**).
- By `directory_access`: `needs-authorized-api` 66 · `authorized-fhir` 17 (+11 HCSC total,
  2026-07-15) · `none` 19 (−1: Meridian Health moved to `public-fhir`) · `public-fhir` 90 (+9 UMR
  and +1 Meridian Health, 2026-07-15) · `pdf-directory` 2
```

- [ ] **Step 5: Verify the row counts and arithmetic**

Run: `grep -c "^| Meridian Health |" docs/payer-sources/MATRIX.md`
Expected: `1`

Run: `grep -c "^| BCBS / Empire (Anthem / Elevance)(HCSC) |" docs/payer-sources/MATRIX.md`
Expected: `11` (verified pre-task baseline is `10`; this task adds exactly 1 new IL Managed Medicaid row).

Run: `grep -c "^|" docs/payer-sources/MATRIX.md`
Expected: `193` (verified pre-task baseline is `191`; this task adds exactly 2 new table rows — Meridian Health + HCSC Medicaid — nothing else touches the `|`-prefixed line count). Note: this file's `## Counts` prose total (`194` after Step 4, since it also counts non-table-row facts) is intentionally a running total, not derived from this raw pipe-count — the two numbers are expected to differ; that's normal, not a bug (see the prior UMR plan's Task 3 for the same distinction).

- [ ] **Step 6: Commit**

```bash
git add docs/payer-sources/MATRIX.md
git commit -m "$(cat <<'EOF'
docs(payer-sources): document Meridian Health fix and HCSC Medicaid row

Adds the first-ever Meridian Health matrix row (previously entirely
undocumented in this file) and the new HCSC IL Managed Medicaid row,
fixes two stale "no roster row of its own yet" mentions, and updates
the Counts section to match.
EOF
)"
```

---

### Task 4: Full verification

**Files:** none modified — verification only.

- [ ] **Step 1: Run the full payer-sources test suite**

Run: `source .venv/bin/activate && python3 -m pytest tests/test_payer_sources.py -k "not db" -v`
Expected: all pass, 0 failures.

- [ ] **Step 2: Run the broader test suite to check for unrelated regressions**

Run: `source .venv/bin/activate && python3 -m pytest tests/ -k "not db" -q`
Expected: no new failures.

- [ ] **Step 3: Manual live smoke test — confirm both fixes resolve through the real, live endpoints**

This test hits two real external APIs (Centene's and HCSC's) using this repo's existing `.env` credentials — expected and intentional, matching the equivalent step in the prior UMR plan.

Run:
```bash
source .venv/bin/activate && python3 -c "
import sys
sys.path.insert(0, 'src')
from network_probe.payers.roster_seed import SOURCES, payer_rows
from network_probe.payers.adapters.fhir_pdex import FhirPdexAdapter
from network_probe.payers.adapters.fhir_auth import build_apikey_fhir_adapter
from network_probe.domain.models import ProviderQuery
from network_probe.core.config import get_settings

# --- Meridian Health, via the shared Centene endpoint ---
meridian_adapter = FhirPdexAdapter(base_url=SOURCES['Meridian Health'][0], payer_name='meridian')
q = ProviderQuery(payer='meridian', plan_hint='', npi='1588744650', first_name='Kevin', last_name='Petermann', state='IL')
v = meridian_adapter.check_network(q)
print('Meridian live smoke test status:', v.status)
assert str(v.status).endswith('IN_NETWORK'), v.status

meridian_rows = [r for r in payer_rows() if r['label'] == 'Meridian Health']
print('Meridian roster rows:', len(meridian_rows))
assert len(meridian_rows) == 1
assert meridian_rows[0]['directory_access'] == 'public-fhir'

# --- HCSC Medicaid: confirm the real Organization is still live and populated ---
s = get_settings()
hcsc_adapter = build_apikey_fhir_adapter(
    payer_key='hcsc', base_url=SOURCES['BCBS / Empire (Anthem / Elevance)(HCSC)'][0],
    header_name='client_id', header_value=s.hcsc_fhir_client_id,
)
org_bundle = hcsc_adapter.client.get_json(
    f\"{hcsc_adapter.base_url}/Organization?name=Blue%20Cross%20Community%20Health%20Plans&_count=5\",
    headers={'accept': 'application/fhir+json'},
)
names = [e['resource'].get('name') for e in org_bundle.get('entry', [])]
print('HCSC Organization search results:', names)
assert any('Blue Cross Community Health Plans' in (n or '') for n in names)

medicaid_rows = [
    r for r in payer_rows()
    if r['label'] == 'BCBS / Empire (Anthem / Elevance)(HCSC)' and r['benefit_type'] == 'Managed Medicaid'
]
print('HCSC Medicaid roster rows:', len(medicaid_rows))
assert len(medicaid_rows) == 1
assert medicaid_rows[0]['stedi_payer_id'] == 'G00621'
print('ALL CHECKS PASSED')
"
```
Expected: prints `Meridian live smoke test status: NetworkStatus.IN_NETWORK`, `Meridian roster rows: 1`, HCSC organization results containing `Blue Cross Community Health Plans...`, `HCSC Medicaid roster rows: 1`, `ALL CHECKS PASSED`.

If any assertion fails, do not treat it as a flaky/transient issue and move on — report BLOCKED with the exact output; a failure here means either a real external API change or a bug in Tasks 1-2 that the unit tests didn't catch (unit tests use fakes; this step is the only one touching the real endpoints).

- [ ] **Step 4: Review the full diff before wrap-up**

Run: `git log --oneline -4` and `git diff <task-1-base>..HEAD --stat` (use the actual base commit you recorded before Task 1).
Expected: 3 commits from this plan (Tasks 1-3), touching `src/network_probe/payers/roster_seed.py`, `tests/test_payer_sources.py`, `docs/payer-sources/MATRIX.md`.

No further commit needed for this task — it's verification-only. If Step 1, 2, or 3 fails, stop and fix the responsible task before proceeding to close out this plan.

---

## After this plan

Community Care Plan (FL) is next — separate spec/plan cycle. It needs a new PDF-parser `format`
in `src/network_probe/domain/directory_pdf.py` (distinct from the existing `allyalign`/`aaneel`
formats) and a `directory_load.py` `PDF_DIRECTORIES` entry, or entries, covering the 3 relevant
South Florida county files (Broward, Miami-Dade, Palm Beach) rather than the loader's current
single-PDF-per-payer assumption. Aetna Better Health stays on hold.
