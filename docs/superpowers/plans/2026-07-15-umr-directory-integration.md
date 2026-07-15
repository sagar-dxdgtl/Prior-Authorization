# UMR Provider-Directory Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add UMR (UnitedHealth Group's third-party-administrator brand for self-funded employer plans) to the payer catalogue as a directory-integrated payer, aliased to the already-wired UnitedHealthcare/Optum FHIR endpoint — no new adapter code — and fix a live "No adapter" bug in the identical existing pattern found along the way.

**Architecture:** This is a pure data/config + test + docs change in `src/network_probe/payers/roster_seed.py`. No new adapter classes, no new dispatch logic in `src/network_probe/domain/service.py` — UMR routes through the exact same `FhirPdexAdapter` + `flex.optum.com` endpoint that `"UnitedHealthcare"` already uses, via the catalogue-driven `fhir_base_url` fallback path in `get_adapter()` (`src/network_probe/domain/service.py:142-157`).

**Tech Stack:** Python 3.12, pytest, SQLAlchemy (unused by this change — no DB/migration work needed, `payer_rows()` is pure Python).

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-15-umr-directory-integration-design.md` — read it first; this plan implements it exactly.
- No new adapter code. UMR must resolve via the existing `FhirPdexAdapter` / `_UHC_FHIR` endpoint only.
- `stedi_payer_id` for UMR stays `None` / `enrollment_status="needs_payer_id"` — do not guess an id from Stedi's 19+ UMR-associated candidates (matches this codebase's existing "don't guess ambiguous ids" policy, e.g. Molina's AZ rows).
- Exclude Oxford Freedom and UnitedHealthcare Dental PPO — not in scope.
- Do not touch `FhirPdexAdapter._org_name` (the Organization-dereference quirk) — separate follow-up, out of scope for this change.
- `docs/payer-sources/MATRIX.md` is hand-maintained (confirmed: no generator script exists under `scripts/`) — edit it directly, keep the `## Counts` section arithmetically consistent with the table.

---

## File Structure

- **Modify** `src/network_probe/payers/roster_seed.py` — add `_UHC_FHIR` constant; fix `SOURCES["UnitedHealthcare Community Plan"]`; add `SOURCES["UMR"]`; add 9 `ROSTER` tuples.
- **Modify** `tests/test_payer_sources.py` — add a shared `UHC_FHIR` test constant, extend `_FHIR_PAYERS` (covers the Community Plan fix and UMR's `fhir_base_url`), add one new `test_umr_seeded_in_every_market` test (covers market coverage, `stedi_payer_id`, `enrollment_status`).
- **Modify** `docs/payer-sources/MATRIX.md` — add 9 UMR table rows, a note on the Community Plan fix, and update `## Counts`.

No new files.

---

### Task 1: Fix the `"UnitedHealthcare Community Plan"` no-adapter bug

**Files:**
- Modify: `src/network_probe/payers/roster_seed.py:260-275` (constants block), `:498-508` (`SOURCES["UnitedHealthcare"]`), `:711-716` (`SOURCES["UnitedHealthcare Community Plan"]`)
- Test: `tests/test_payer_sources.py`

**Interfaces:**
- Produces: module-level constant `_UHC_FHIR: str` in `roster_seed.py`, value `"https://flex.optum.com/fhirpublic/R4"`. Later tasks (Task 2) import/reuse this same constant.
- Consumes: nothing new.

- [ ] **Step 1: Extend the existing regression test (don't write a new one — reuse the pattern already in the file)**

Open `tests/test_payer_sources.py`. Near the top, after `HCSC_FHIR = "..."` (currently line 45), add a new shared constant:

```python
UHC_FHIR = "https://flex.optum.com/fhirpublic/R4"
```

Then find the `_FHIR_PAYERS` dict (currently lines 27-38):

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
}
```

Replace with:

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
}
```

This dict already drives a loop in the existing `test_seeded_fhir_base_urls_present` (`for label, url in _FHIR_PAYERS.items(): assert by_label.get(label) == url, label`) — no new test function needed, this one entry is enough to turn it into the regression test for this bug.

- [ ] **Step 2: Run the test to verify it fails**

Run: `source .venv/bin/activate && python3 -m pytest tests/test_payer_sources.py::test_seeded_fhir_base_urls_present -v`
Expected: FAIL — `AssertionError: UnitedHealthcare Community Plan` (from `assert by_label.get(label) == url, label`; actual is `None`).

- [ ] **Step 3: Add the shared constant**

In `src/network_probe/payers/roster_seed.py`, find this block (currently lines 268-269):

```python
_HCSC_FHIR = "https://api.hcsc.net/providerfinder/sapphire/fhir"

# (fhir_base_url, tic_url, directory_url, directory_access)
```

Replace it with:

```python
_HCSC_FHIR = "https://api.hcsc.net/providerfinder/sapphire/fhir"
# UnitedHealthcare's public Optum FHIR Layer Exchange (no auth/login) -- the same endpoint the
# "uhc" adapter key uses (fhir_pdex.KNOWN_ENDPOINTS["uhc"]). Shared by every UHC-family catalogue
# label that needs a populated fhir_base_url to reach the catalogue-driven FHIR dispatch (see the
# "UnitedHealthcare" SOURCES note below): UnitedHealthcare itself, UnitedHealthcare Community Plan,
# and UMR (UHG's self-funded-plan TPA -- rides UHC's own Choice Plus/Options PPO/Core/NexusACO/
# Select Plus networks, not a network of its own; see docs/superpowers/specs/
# 2026-07-15-umr-directory-integration-design.md).
_UHC_FHIR = "https://flex.optum.com/fhirpublic/R4"

# (fhir_base_url, tic_url, directory_url, directory_access)
```

- [ ] **Step 4: Fix the `"UnitedHealthcare"` and `"UnitedHealthcare Community Plan"` tuples to use the constant**

Find this block (currently lines 498-508):

```python
    "UnitedHealthcare": (
        # get_adapter() only reaches the pre-built "uhc" adapter-key shortcut when q.payer is
        # literally "uhc" -- callers that resolve a payer via its full catalogue key (e.g.
        # "unitedhealthcare-az", as check_eligibility() does) need fhir_base_url populated here
        # to fall through to the catalogue-driven FHIR dispatch instead of hitting "no adapter".
        # Same endpoint as the "uhc" adapter-key shortcut (fhir_pdex.KNOWN_ENDPOINTS["uhc"]).
        "https://flex.optum.com/fhirpublic/R4",
        "https://transparency-in-coverage.uhc.com/",
        "https://www.uhc.com/find-a-doctor",
        "public-fhir",
    ),
```

Replace with:

```python
    "UnitedHealthcare": (
        # get_adapter() only reaches the pre-built "uhc" adapter-key shortcut when q.payer is
        # literally "uhc" -- callers that resolve a payer via its full catalogue key (e.g.
        # "unitedhealthcare-az", as check_eligibility() does) need fhir_base_url populated here
        # to fall through to the catalogue-driven FHIR dispatch instead of hitting "no adapter".
        # Same endpoint as the "uhc" adapter-key shortcut (fhir_pdex.KNOWN_ENDPOINTS["uhc"]).
        _UHC_FHIR,
        "https://transparency-in-coverage.uhc.com/",
        "https://www.uhc.com/find-a-doctor",
        "public-fhir",
    ),
```

Then find this block (currently lines 711-716):

```python
    "UnitedHealthcare Community Plan": (
        # Same underlying UHC/Optum adapter as other UnitedHealthcare rows — not a separate
        # technical product, just a distinct Medicaid brand name.
        None, "https://transparency-in-coverage.uhc.com/", "https://www.uhc.com/communityplan/find-a-doctor", "public-fhir",
    ),
```

Replace with:

```python
    "UnitedHealthcare Community Plan": (
        # Same underlying UHC/Optum adapter as other UnitedHealthcare rows — not a separate
        # technical product, just a distinct Medicaid brand name. BUG FIX 2026-07-15: this was
        # `None` here, which meant get_adapter() had no fhir_base_url to fall through to and no
        # adapter-key shortcut matches this multi-word label either -- every call raised
        # `ValueError: No adapter for payer 'UnitedHealthcare Community Plan'`. Populating it with
        # the same _UHC_FHIR constant the "UnitedHealthcare" row uses fixes it (see
        # docs/superpowers/specs/2026-07-15-umr-directory-integration-design.md).
        _UHC_FHIR, "https://transparency-in-coverage.uhc.com/", "https://www.uhc.com/communityplan/find-a-doctor", "public-fhir",
    ),
```

- [ ] **Step 5: Run the test to verify it passes, and confirm the previously-failing test now passes too**

Run: `source .venv/bin/activate && python3 -m pytest tests/test_payer_sources.py::test_seeded_fhir_base_urls_present tests/test_payer_sources.py::test_public_fhir_rows_have_fhir_or_existing_adapter -v`
Expected: both PASS (`test_public_fhir_rows_have_fhir_or_existing_adapter` was the one failing before this task — see spec finding #6).

- [ ] **Step 6: Run the full non-db test file to check for regressions**

Run: `source .venv/bin/activate && python3 -m pytest tests/test_payer_sources.py -k "not db" -v`
Expected: all pass (22 passed, 2 deselected — one more than the pre-existing 21-passed/1-failed baseline).

- [ ] **Step 7: Commit**

```bash
git add src/network_probe/payers/roster_seed.py tests/test_payer_sources.py
git commit -m "$(cat <<'EOF'
fix(payers): populate fhir_base_url for UnitedHealthcare Community Plan

get_adapter("UnitedHealthcare Community Plan", ...) raised "No adapter"
at runtime -- its SOURCES tuple had fhir_base_url=None and its multi-word
label never matches the "uhc" adapter-key shortcut. Introduces a shared
_UHC_FHIR constant (also used by "UnitedHealthcare" and, next, UMR) and
populates it here, the same fix already applied to "UnitedHealthcare".

Found while verifying the UMR integration design
(docs/superpowers/specs/2026-07-15-umr-directory-integration-design.md).
EOF
)"
```

---

### Task 2: Add UMR to the catalogue

**Files:**
- Modify: `src/network_probe/payers/roster_seed.py` (`SOURCES` dict, `ROSTER` list)
- Test: `tests/test_payer_sources.py`

**Interfaces:**
- Consumes: `_UHC_FHIR` constant from Task 1.
- Produces: `SOURCES["UMR"]` tuple; 9 new `ROSTER` tuples with label `"UMR"`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_payer_sources.py`, extend the `_FHIR_PAYERS` dict again — Task 1 already added `"UnitedHealthcare Community Plan": UHC_FHIR,`; add one more line right after it:

```python
    "UnitedHealthcare Community Plan": UHC_FHIR,
    "UMR": UHC_FHIR,
```

This alone makes `test_seeded_fhir_base_urls_present` assert `SOURCES["UMR"]["fhir_base_url"] == UHC_FHIR` once the loop reaches it — but it doesn't check market coverage, `stedi_payer_id`, `enrollment_status`, or that `get_adapter()` actually routes UMR correctly, so also add a new test near `test_scan_seeded_public_fhir` (after it, before `test_align_routes_to_db_directory_adapter`):

```python
UMR_MARKETS = {
    "AZ", "CO-Denver", "NY", "FL-South Florida", "FL", "IL", "GA-Atlanta", "TX-Houston", "TX-Dallas",
}


def test_umr_seeded_in_every_market():
    umr_rows = [r for r in payer_rows() if r["label"] == "UMR"]
    assert {r["state"] for r in umr_rows} == UMR_MARKETS
    for r in umr_rows:
        assert r["benefit_type"] == "Commercial", r["state"]
        assert r["fhir_base_url"] == UHC_FHIR, r["state"]
        assert r["directory_access"] == "public-fhir", r["state"]
        assert r["stedi_payer_id"] is None, r["state"]
        assert r["enrollment_status"] == "needs_payer_id", r["state"]
        assert r["network_indicator_supported"] is False, r["state"]
```

Note: do NOT also add a `test_umr_routes_to_pdex_via_catalogue`-style test using `_FakeCatalogue` here (the same pattern `test_fhir_base_url_routes_directory_leg_to_pdex` uses for Healthspring) — `_FakeCatalogue` hands `get_adapter()` a mocked row with whatever `fhir_base_url` you pass it, so a test built that way would pass regardless of whether `SOURCES["UMR"]` exists in the real roster yet. It would prove the generic routing mechanism works with the string `"UMR"` (already proven, payer-agnostic, by the existing Healthspring/Kaiser/Molina/Centene tests) but not anything specific to this task's data change — not a genuine red/green step here, so it's left out.

- [ ] **Step 2: Run the test to verify it fails**

Run: `source .venv/bin/activate && python3 -m pytest tests/test_payer_sources.py::test_umr_seeded_in_every_market -v`
Expected: FAIL — `AssertionError: assert set() == {'AZ', 'CO-Denver', ...}` (no `ROSTER` rows with label `"UMR"` exist yet, so `umr_rows` is empty).

- [ ] **Step 3: Add `SOURCES["UMR"]`**

In `src/network_probe/payers/roster_seed.py`, find the end of the `"UnitedHealthcare Community Plan"` tuple (as fixed in Task 1) and the start of the next entry:

```python
        _UHC_FHIR, "https://transparency-in-coverage.uhc.com/", "https://www.uhc.com/communityplan/find-a-doctor", "public-fhir",
    ),
    "WellCare / AllWell (Centene)": (
```

Replace with:

```python
        _UHC_FHIR, "https://transparency-in-coverage.uhc.com/", "https://www.uhc.com/communityplan/find-a-doctor", "public-fhir",
    ),
    "UMR": (
        # UMR (UnitedHealth Group's third-party-administrator brand for self-funded employer
        # plans) is not an insurer and has no network of its own -- verified 2026-07-15: its own
        # "find a provider" tool (umr.com/find-a-provider) lists ~15 named networks (Choice Plus,
        # Core, Options PPO, Select Plus, NexusACO, state-tiered variants), every one a
        # UnitedHealthcare-branded national commercial network product, not a UMR-specific
        # network. Live-tested against 3 of this client's own roster NPIs on flex.optum.com
        # (Manayan/GA, Naar/FL, Bui/AZ) -- all resolved IN_NETWORK. TiC MRFs are published on the
        # same UHC portal ("UnitedHealthcare, UMR, and HealthSCOPE Benefits create and publish
        # Machine-Readable Files on behalf of group health plans... posted at
        # transparency-in-coverage.uhc.com"). Excluded: Oxford Freedom (separate legacy UHG
        # brand, external Rally-platform tool) and UnitedHealthcare Dental PPO (different product
        # line). No single Stedi/EDI id: Stedi lists 19+ distinct UMR-associated payer ids
        # (UMR01/UMRWAU/XXUMR/10394/GEHA/...) because UMR's eligibility routing is per
        # self-funded employer group, not one umbrella id -- left `needs_payer_id` for human
        # review rather than guessed (same honesty policy as every other ambiguous id in this
        # file). See docs/superpowers/specs/2026-07-15-umr-directory-integration-design.md.
        _UHC_FHIR,
        "https://transparency-in-coverage.uhc.com/",
        "https://www.umr.com/find-a-provider",
        "public-fhir",
    ),
    "WellCare / AllWell (Centene)": (
```

- [ ] **Step 4: Add the 9 `ROSTER` rows, one per existing market**

Each insertion below is anchored on the exact last UHC-family row (or, where none exists, the last row) of that market's block, so the new UMR row lands next to its UHC siblings. Apply each of the following 9 edits in `src/network_probe/payers/roster_seed.py`.

**AZ** — find:
```python
    ("UnitedHealthcare", "ACA", "AZ", "87726", "supported"),
    ("UnitedHealthcare", "Commercial", "AZ", "87726", "supported"),
    ("UnitedHealthcare", "Medicare Advantage", "AZ", "87726", "supported"),
    ("Wellcare (Centene)", "Medicare Advantage", "AZ", None, "needs_payer_id"),
```
Replace with:
```python
    ("UnitedHealthcare", "ACA", "AZ", "87726", "supported"),
    ("UnitedHealthcare", "Commercial", "AZ", "87726", "supported"),
    ("UnitedHealthcare", "Medicare Advantage", "AZ", "87726", "supported"),
    ("UMR", "Commercial", "AZ", None, "needs_payer_id"),
    ("Wellcare (Centene)", "Medicare Advantage", "AZ", None, "needs_payer_id"),
```

**CO-Denver** — find:
```python
    ("UnitedHealthcare", "Commercial", "CO-Denver", "87726", "supported"),
    ("UnitedHealthcare", "Dual Eligible (FIDE SNP)", "CO-Denver", "87726", "supported"),
    ("UnitedHealthcare", "Medicare Advantage", "CO-Denver", "87726", "supported"),
    # --- New York ---
```
Replace with:
```python
    ("UnitedHealthcare", "Commercial", "CO-Denver", "87726", "supported"),
    ("UnitedHealthcare", "Dual Eligible (FIDE SNP)", "CO-Denver", "87726", "supported"),
    ("UnitedHealthcare", "Medicare Advantage", "CO-Denver", "87726", "supported"),
    ("UMR", "Commercial", "CO-Denver", None, "needs_payer_id"),
    # --- New York ---
```

**NY** — find:
```python
    ("EmblemHealth", "Commercial", "NY", "13551", "needs_enrollment"),
    # --- Florida (South Florida) ---
```
Replace with:
```python
    ("EmblemHealth", "Commercial", "NY", "13551", "needs_enrollment"),
    ("UMR", "Commercial", "NY", None, "needs_payer_id"),
    # --- Florida (South Florida) ---
```

**FL-South Florida** — find:
```python
    ("Curative", "Commercial", "FL-South Florida", "CURTV", "needs_enrollment"),
    # --- Illinois --- (added from client benefit list; researched via 3 parallel agent passes,
```
Replace with:
```python
    ("Curative", "Commercial", "FL-South Florida", "CURTV", "needs_enrollment"),
    ("UMR", "Commercial", "FL-South Florida", None, "needs_payer_id"),
    # --- Illinois --- (added from client benefit list; researched via 3 parallel agent passes,
```

**IL** — find:
```python
    ("UnitedHealthcare", "Commercial", "IL", "87726", "supported"),
    ("UnitedHealthcare", "Medicare Advantage", "IL", "87726", "supported"),
    ("Zing Health", "Medicare Advantage", "IL", None, "needs_payer_id"),
```
Replace with:
```python
    ("UnitedHealthcare", "Commercial", "IL", "87726", "supported"),
    ("UnitedHealthcare", "Medicare Advantage", "IL", "87726", "supported"),
    ("UMR", "Commercial", "IL", None, "needs_payer_id"),
    ("Zing Health", "Medicare Advantage", "IL", None, "needs_payer_id"),
```

**GA-Atlanta** — find:
```python
    ("UnitedHealthcare", "Commercial", "GA-Atlanta", "87726", "supported"),
    ("UnitedHealthcare", "Dual Eligible (FIDE SNP)", "GA-Atlanta", "87726", "supported"),
    ("UnitedHealthcare", "Medicare Advantage", "GA-Atlanta", "87726", "supported"),
    ("Clear Spring Health", "Medicare Advantage", "GA-Atlanta", None, "needs_payer_id"),
```
Replace with:
```python
    ("UnitedHealthcare", "Commercial", "GA-Atlanta", "87726", "supported"),
    ("UnitedHealthcare", "Dual Eligible (FIDE SNP)", "GA-Atlanta", "87726", "supported"),
    ("UnitedHealthcare", "Medicare Advantage", "GA-Atlanta", "87726", "supported"),
    ("UMR", "Commercial", "GA-Atlanta", None, "needs_payer_id"),
    ("Clear Spring Health", "Medicare Advantage", "GA-Atlanta", None, "needs_payer_id"),
```

**TX-Houston** — find:
```python
    ("UnitedHealthcare", "Commercial", "TX-Houston", "87726", "supported"),
    ("UnitedHealthcare", "Dual Eligible (FIDE SNP)", "TX-Houston", "87726", "supported"),
    ("UnitedHealthcare", "Medicare Advantage", "TX-Houston", "87726", "supported"),
    ("UnitedHealthcare Community Plan", "Managed Medicaid", "TX-Houston", None, "needs_payer_id"),
    ("Wellcare (Centene)", "Medicare Advantage", "TX-Houston", None, "needs_payer_id"),
```
Replace with:
```python
    ("UnitedHealthcare", "Commercial", "TX-Houston", "87726", "supported"),
    ("UnitedHealthcare", "Dual Eligible (FIDE SNP)", "TX-Houston", "87726", "supported"),
    ("UnitedHealthcare", "Medicare Advantage", "TX-Houston", "87726", "supported"),
    ("UnitedHealthcare Community Plan", "Managed Medicaid", "TX-Houston", None, "needs_payer_id"),
    ("UMR", "Commercial", "TX-Houston", None, "needs_payer_id"),
    ("Wellcare (Centene)", "Medicare Advantage", "TX-Houston", None, "needs_payer_id"),
```

**TX-Dallas** — find:
```python
    ("UnitedHealthcare Community Plan", "Managed Medicaid", "TX-Dallas", None, "needs_payer_id"),
    ("Wellcare (Centene)", "Medicare Advantage", "TX-Dallas", None, "needs_payer_id"),
```
Replace with:
```python
    ("UnitedHealthcare Community Plan", "Managed Medicaid", "TX-Dallas", None, "needs_payer_id"),
    ("UMR", "Commercial", "TX-Dallas", None, "needs_payer_id"),
    ("Wellcare (Centene)", "Medicare Advantage", "TX-Dallas", None, "needs_payer_id"),
```

**FL** (plain, added 2026-07-08) — find:
```python
    ("First Coast Service Options, Inc.", "Traditional Medicare", "FL", "09102", "needs_enrollment"),
    ("Humana", "Medicare Advantage", "FL", "61101", "supported"),
]
```
Replace with:
```python
    ("First Coast Service Options, Inc.", "Traditional Medicare", "FL", "09102", "needs_enrollment"),
    ("Humana", "Medicare Advantage", "FL", "61101", "supported"),
    ("UMR", "Commercial", "FL", None, "needs_payer_id"),
]
```

- [ ] **Step 5: Run the new tests to verify they pass**

Run: `source .venv/bin/activate && python3 -m pytest tests/test_payer_sources.py::test_umr_seeded_in_every_market tests/test_payer_sources.py::test_seeded_fhir_base_urls_present -v`
Expected: both PASS.

- [ ] **Step 6: Run the full non-db test file to check for regressions**

Run: `source .venv/bin/activate && python3 -m pytest tests/test_payer_sources.py -k "not db" -v`
Expected: all pass (23 passed, 2 deselected — one more than Task 1's 22-passed checkpoint).

- [ ] **Step 7: Commit**

```bash
git add src/network_probe/payers/roster_seed.py tests/test_payer_sources.py
git commit -m "$(cat <<'EOF'
feat(payers): add UMR, aliased to the existing UHC/Optum FHIR directory

Research (docs/superpowers/specs/2026-07-15-umr-directory-integration-design.md)
confirms UMR (UHG's self-funded-plan TPA) rides UnitedHealthcare's own
commercial networks -- Choice Plus, Options PPO, Core, NexusACO, Select
Plus -- rather than a UMR-specific one, so this reuses the existing
flex.optum.com FHIR endpoint with no new adapter code. Seeded across all
9 markets the catalogue currently covers. stedi_payer_id stays unset
(19+ ambiguous group-specific Stedi ids, no single confident one).
EOF
)"
```

---

### Task 3: Update `docs/payer-sources/MATRIX.md`

**Files:**
- Modify: `docs/payer-sources/MATRIX.md`

**Interfaces:**
- Consumes: the 9 UMR roster rows and the Community Plan fix from Tasks 1-2 (documents them; does not affect their behavior).
- Produces: nothing consumed by later tasks.

- [ ] **Step 1: Add the UMR table row after the last UnitedHealthcare-family row in each of the 8 existing market sections**

Each row uses this template (columns: Payer | State | Type | Stedi ID | FHIR base URL | TiC URL | Dir access | Note), with `<STATE>` substituted:

```
| UMR | <STATE> | Commercial |  | via existing adapter | `https://transparency-in-coverage.uhc.com/` | public-fhir | UHG's self-funded-plan TPA — rides UnitedHealthcare's own networks (Choice Plus/Options PPO/Core/NexusACO/Select Plus), not a network of its own; live-tested against 3 in-roster NPIs on the same flex.optum.com endpoint (Manayan/GA, Naar/FL, Bui/AZ — all IN_NETWORK). No single Stedi id (19+ group-specific ids on Stedi, e.g. UMR01/UMRWAU/XXUMR) — left `needs_payer_id` for human review. Excludes Oxford Freedom and UnitedHealthcare Dental PPO. See docs/superpowers/specs/2026-07-15-umr-directory-integration-design.md. |
```

Apply at these 8 anchors (find the exact line, insert the UMR row directly after it):

1. After (line 44): `| UnitedHealthcare | AZ | Medicare Advantage | 87726 | via existing adapter | ... |` → insert UMR/AZ row.
2. After (line 64): `| UnitedHealthcare | CO-Denver | Medicare Advantage | 87726 | via existing adapter | ... |` → insert UMR/CO-Denver row.
3. After (line 65): `| EmblemHealth | NY | Commercial | 13551 | ... |` → insert UMR/NY row.
4. After (line 83): `| Curative | FL-South Florida | Commercial | CURTV | ... |` → insert UMR/FL-South Florida row.
5. After (line 101): `| UnitedHealthcare | IL | Medicare Advantage | 87726 | ... |` → insert UMR/IL row.
6. After (line 127): `| UnitedHealthcare | GA-Atlanta | Medicare Advantage | 87726 | ... |` → insert UMR/GA-Atlanta row.
7. After (line 160): `| UnitedHealthcare Community Plan | TX-Houston | Managed Medicaid |  | ... |` → insert UMR/TX-Houston row.
8. After (line 192): `| UnitedHealthcare Community Plan | TX-Dallas | Managed Medicaid |  | ... |` → insert UMR/TX-Dallas row.

(Line numbers are from the pre-Task-3 file; re-read the file with `Read` before editing since each insertion shifts subsequent line numbers — insert top-to-bottom, or search each anchor's literal text fresh before each edit.)

- [ ] **Step 2: Add a new "FL" mini-section for the 9th market, plus a note that this market is otherwise undocumented in this file**

`docs/payer-sources/MATRIX.md` currently has no rows at all for the plain `FL` market (First Coast Service Options / Humana were added to `roster_seed.py` on 2026-07-08, after this file's last full regeneration — a pre-existing gap, not something this task fixes). Find the end of the table, immediately before the blank line that precedes `## Counts`:

```
| Wellpoint / Amerigroup (Elevance) | TX-Dallas | Medicare Advantage |  | — | — | needs-authorized-api | Same as Houston. |

## Counts
```

Replace with:

```
| Wellpoint / Amerigroup (Elevance) | TX-Dallas | Medicare Advantage |  | — | — | needs-authorized-api | Same as Houston. |
| UMR | FL | Commercial |  | via existing adapter | `https://transparency-in-coverage.uhc.com/` | public-fhir | UHG's self-funded-plan TPA — rides UnitedHealthcare's own networks (Choice Plus/Options PPO/Core/NexusACO/Select Plus), not a network of its own; live-tested against 3 in-roster NPIs on the same flex.optum.com endpoint (Manayan/GA, Naar/FL, Bui/AZ — all IN_NETWORK). No single Stedi id (19+ group-specific ids on Stedi, e.g. UMR01/UMRWAU/XXUMR) — left `needs_payer_id` for human review. Excludes Oxford Freedom and UnitedHealthcare Dental PPO. See docs/superpowers/specs/2026-07-15-umr-directory-integration-design.md. **Note:** the plain `FL` market (First Coast Service Options, Humana Medicare Advantage) has no other rows documented in this file — added to `roster_seed.py` 2026-07-08, after this file's last full pass; pre-existing gap, not addressed here. |

## Counts
```

- [ ] **Step 3: Update the `## Counts` section**

Find (currently lines 198-213):

```
## Counts

- Total roster rows: **184** (grown past the original 180 as later passes — UHC/UnitedHealthcare
  Community Plan wiring, etc. — added rows; see git history for the incremental deltas rather than
  a single point-in-time total).
- Rows with `fhir_base_url`: **91** (UnitedHealthcare 18, Cigna 13, **BCBS / Empire (Anthem /
  Elevance)(HCSC) 10** — new 2026-07-14, Humana 8, Molina 8, Ambetter/Centene 6, Anthem/Elevance 6,
  Healthspring 5, Wellcare/Centene 5, Devoted 3, Kaiser Permanente 2, AmeriHealth Caritas 2, AZ
  Complete Health 1, Scan 1, Kaiser Foundation Health Plan of Georgia 1, Peach State 1,
  WellCare/AllWell 1).
- Rows with `tic_url`: **100**
- Rows with a Stedi id: **96**
- By `directory_access`: `needs-authorized-api` 66 · `authorized-fhir` 16 (+10 HCSC, 2026-07-14) ·
  `none` 20 · `public-fhir` 80 · `pdf-directory` 2
```

Replace with:

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

- [ ] **Step 4: Verify the row count and arithmetic**

Run: `grep -c "^| UMR |" docs/payer-sources/MATRIX.md`
Expected: `9`

Run: `grep -c "^|" docs/payer-sources/MATRIX.md`
Expected: table header + separator + 193 data rows = 195 (this counts every `|`-prefixed line in the table, including the 2 header lines — confirm it's `193 + 2`).

- [ ] **Step 5: Commit**

```bash
git add docs/payer-sources/MATRIX.md
git commit -m "$(cat <<'EOF'
docs(payer-sources): document UMR rows and the Community Plan fix

Adds the 9 new UMR matrix rows (one per market, including a new FL
mini-section — that market has no other documented rows in this file,
a pre-existing gap), a note on the UnitedHealthcare Community Plan
fhir_base_url bug fix, and updates the Counts section to match.
EOF
)"
```

---

### Task 4: Full verification

**Files:** none modified — verification only.

- [ ] **Step 1: Run the full payer-sources test suite (excluding db tests, which need a live database)**

Run: `source .venv/bin/activate && python3 -m pytest tests/test_payer_sources.py -k "not db" -v`
Expected: all pass, 0 failures (was 21 passed / 1 failed at the start of this plan).

- [ ] **Step 2: Run the broader test suite to check for unrelated regressions**

Run: `source .venv/bin/activate && python3 -m pytest tests/ -k "not db" -q`
Expected: no new failures introduced by this change (pre-existing db-only tests are deselected, same as Step 1).

- [ ] **Step 3: Manual live smoke test — confirm UMR and the fixed Community Plan row both resolve through the real catalogue-driven path**

Run:
```bash
source .venv/bin/activate && python3 -c "
import sys
sys.path.insert(0, 'src')
from network_probe.payers.roster_seed import SOURCES, payer_rows
from network_probe.payers.adapters.fhir_pdex import FhirPdexAdapter
from network_probe.domain.models import ProviderQuery

adapter = FhirPdexAdapter(payer_name='uhc')  # same live endpoint UMR/Community Plan now route to
q = ProviderQuery(payer='umr', plan_hint='', npi='1902811656', first_name='Conrad', last_name='Manayan', state='GA')
v = adapter.check_network(q)
print('UMR live smoke test status:', v.status)
assert str(v.status).endswith('IN_NETWORK'), v.status

umr_rows = [r for r in payer_rows() if r['label'] == 'UMR']
print('UMR roster rows:', len(umr_rows))
assert len(umr_rows) == 9

cp = SOURCES['UnitedHealthcare Community Plan']
print('Community Plan fhir_base_url:', cp[0])
assert cp[0] == 'https://flex.optum.com/fhirpublic/R4'
print('ALL CHECKS PASSED')
"
```
Expected: prints `UMR live smoke test status: NetworkStatus.IN_NETWORK`, `UMR roster rows: 9`, `Community Plan fhir_base_url: https://flex.optum.com/fhirpublic/R4`, `ALL CHECKS PASSED`.

- [ ] **Step 4: Review the full diff before wrap-up**

Run: `git log --oneline -4 && git diff main~3 --stat` (adjust range if other commits landed in between).
Expected: 3 commits from this plan (Tasks 1-3), touching `src/network_probe/payers/roster_seed.py`, `tests/test_payer_sources.py`, `docs/payer-sources/MATRIX.md`.

No further commit needed for this task — it's verification-only. If Step 1, 2, or 3 fails, stop and fix the responsible task before proceeding to close out this plan.

---

## After this plan

Medicaid is the next sub-project (separate spec/plan cycle, not part of this one): Aetna Better Health, Community Care Plan (FL), Meridian Health (IL), and HCSC's "Blue Cross Community Health Plans" (IL Medicaid, Stedi id `G00621` already known). Also tracked as a follow-up, not blocking: the `FhirPdexAdapter._org_name` Organization-dereference quirk found during UMR research (spec finding #3), which limits network-name precision for every UHC-family row today.
