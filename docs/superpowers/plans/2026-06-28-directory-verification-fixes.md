# Directory Verification Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Apply live-verified corrections to the payer provider-directory catalogue — Wellpoint demoted from public to auth-gated, Healthspring promoted to public FHIR, Cigna prod endpoint, docs and DB migration updated.

**Architecture:** Single-source-of-truth is `roster_seed.py`; migration 0008 syncs the already-seeded DB rows; TDD ensures no routing test regresses.

**Tech Stack:** Python, SQLAlchemy, Alembic, pytest, ruff; PostgreSQL (`preauth` / `preauth_test`).

## Global Constraints

- Branch: `fix/directory-verification`
- `pytest -m "not live and not db" -q` must stay green at every commit
- `pytest -m db -q` must stay green
- `ruff check src tests scripts` must be clean
- No live network calls in tests
- Never stage `.env`
- Migration `down_revision = "0007_payer_sources"` → new `revision = "0008_directory_fixes"`
- Apply migration to BOTH `preauth` and `preauth_test`; psql-verify row values

---

### Task 1: Update tests (TDD — write failing tests first)

**Files:**
- Modify: `tests/test_payer_sources.py`

**Interfaces:**
- Consumes: `network_probe.payers.roster_seed.SOURCES`, `payer_rows`
- Produces: failing test suite that the seed fix (Task 2) will green

- [ ] **Step 1: Update constants and `_FHIR_PAYERS` map**

Replace the Wellpoint-centric constants and add Healthspring:

```python
WELLPOINT = "Wellpoint / Amerigroup (Elevance)"
HEALTHSPRING = "Healthspring"
HEALTHSPRING_FHIR = "https://p-hi2.digitaledge.cigna.com/ProviderDirectory/v1"

_FHIR_PAYERS = {
    "Cigna Healthcare": "https://fhir.cigna.com/ProviderDirectory/v1",
    "Humana": "https://fhir.humana.com/api",
    "Devoted Health": "https://fhir.devoted.com/fhir",
    "Healthspring": HEALTHSPRING_FHIR,
    "AmeriHealth Caritas": "https://api-ext.amerihealthcaritas.com/NCEX/provider-api",
}
```

Remove the old `WELLPOINT_FHIR` constant.

- [ ] **Step 2: Update `test_fhir_base_url_routes_directory_leg_to_pdex`**

Change to use Healthspring (which now has a `fhir_base_url`):

```python
def test_fhir_base_url_routes_directory_leg_to_pdex():
    adapter = svc.get_adapter(HEALTHSPRING, catalogue=_FakeCatalogue(HEALTHSPRING_FHIR), client=_offline_client())
    assert isinstance(adapter, FhirPdexAdapter)
    assert adapter.base_url == HEALTHSPRING_FHIR
```

- [ ] **Step 3: Update `test_seeded_fhir_base_urls_present` to assert Wellpoint has NO fhir_base_url**

```python
def test_seeded_fhir_base_urls_present():
    by_label = {r["label"]: r["fhir_base_url"] for r in payer_rows()}
    for label, url in _FHIR_PAYERS.items():
        assert by_label.get(label) == url, label
    # honest: only verified-public servers get a baked URL
    assert by_label["Aetna"] is None
    assert by_label["BCBS / Empire (Anthem / Elevance)"] is None
    # Wellpoint is auth-gated (registered path 403) — must NOT carry a public fhir_base_url
    assert by_label[WELLPOINT] is None, "Wellpoint must not have a public fhir_base_url"
```

- [ ] **Step 4: Add `test_wellpoint_is_auth_gated`**

```python
def test_wellpoint_is_auth_gated():
    rows_by_label = {r["label"]: r for r in payer_rows()}
    wp = rows_by_label[WELLPOINT]
    assert wp["fhir_base_url"] is None
    assert wp["directory_access"] == "needs-authorized-api"
```

- [ ] **Step 5: Update the db test `test_db_catalogue_surfaces_source_columns`**

Change Wellpoint assertions and add Healthspring assertions:

```python
@pytest.mark.db
def test_db_catalogue_surfaces_source_columns():
    from sqlalchemy.orm import Session

    from network_probe.db.base import owner_engine
    from network_probe.db.models import Payer
    from network_probe.payers.catalogue import DbPayerCatalogue

    with Session(owner_engine()) as s:
        for r in payer_rows():
            s.add(Payer(**r))
        s.commit()

    cat = DbPayerCatalogue()

    # Wellpoint is auth-gated: no public fhir_base_url
    wp = cat.resolve(WELLPOINT)
    assert wp is not None
    assert wp.fhir_base_url is None
    assert wp.directory_access == "needs-authorized-api"

    # Wellpoint no longer routes to a FHIR adapter via the catalogue
    with pytest.raises(ValueError, match="No adapter"):
        svc.get_adapter(WELLPOINT, catalogue=cat, client=_offline_client())

    # Healthspring has a verified-public FHIR URL → routes to FhirPdexAdapter
    hs = cat.resolve(HEALTHSPRING)
    assert hs is not None
    assert hs.fhir_base_url == HEALTHSPRING_FHIR
    assert hs.directory_access == "public-fhir"
    hs_adapter = svc.get_adapter(HEALTHSPRING, catalogue=cat, client=_offline_client())
    assert isinstance(hs_adapter, FhirPdexAdapter)
    assert hs_adapter.base_url == HEALTHSPRING_FHIR

    # a govt/Medicaid program is honestly recorded as having no public directory source
    ahcccs = cat.resolve("Arizona Health Care Cost Containment System (AHCCCS)")
    assert ahcccs.directory_access == "none"
    assert ahcccs.fhir_base_url is None
```

- [ ] **Step 6: Run tests — verify failures are the expected ones**

```
pytest tests/test_payer_sources.py -q
```

Expected: `test_seeded_fhir_base_urls_present`, `test_wellpoint_is_auth_gated`, `test_fhir_base_url_routes_directory_leg_to_pdex` FAIL (seed not yet fixed).

---

### Task 2: Fix `roster_seed.py` — Wellpoint + Healthspring

**Files:**
- Modify: `src/network_probe/payers/roster_seed.py`

**Interfaces:**
- Produces: `SOURCES["Wellpoint / Amerigroup (Elevance)"]` with `fhir_base_url=None, directory_access="needs-authorized-api"` and `SOURCES["Healthspring"]` with `fhir_base_url="https://p-hi2.digitaledge.cigna.com/ProviderDirectory/v1", directory_access="public-fhir"`

- [ ] **Step 1: Fix Wellpoint entry in SOURCES**

Change from:
```python
"Wellpoint / Amerigroup (Elevance)": (
    "https://totalview.healthos.elevancehealth.com/resources/registered/Wellpoint/api/v1/fhir",
    None,
    "https://findcaresecure.wellpoint.com/",
    "public-fhir",
),
```

To:
```python
"Wellpoint / Amerigroup (Elevance)": (
    None,
    None,
    "https://findcaresecure.wellpoint.com/",
    "needs-authorized-api",
),
```

- [ ] **Step 2: Fix Healthspring entry in SOURCES**

Change from:
```python
"Healthspring": (
    None,
    _CIGNA_TIC,
    "https://www.healthspring.com/providers/network-participation",
    "needs-authorized-api",
),
```

To:
```python
"Healthspring": (
    "https://p-hi2.digitaledge.cigna.com/ProviderDirectory/v1",
    _CIGNA_TIC,
    "https://www.healthspring.com/providers/network-participation",
    "public-fhir",
),
```

- [ ] **Step 3: Run pure tests — all should pass now**

```
pytest tests/test_payer_sources.py -m "not live and not db" -q
pytest tests/test_catalogue.py -m "not live and not db" -q
```

Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add src/network_probe/payers/roster_seed.py tests/test_payer_sources.py
git commit -m "fix(seed): Wellpoint→needs-authorized-api+no-fhir-url; Healthspring→public-fhir"
```

---

### Task 3: Fix `fhir_pdex.py` — Cigna prod endpoint

**Files:**
- Modify: `src/network_probe/payers/adapters/fhir_pdex.py`

**Interfaces:**
- `KNOWN_ENDPOINTS["cigna-fhir"]` changes from staging `p-hi2.digitaledge.cigna.com` URL to production `fhir.cigna.com/ProviderDirectory/v1`
- Key `"cigna-fhir"` stays unchanged (referenced in catalogue.py, corroboration.py, tests)

- [ ] **Step 1: Update KNOWN_ENDPOINTS cigna-fhir value**

Change:
```python
KNOWN_ENDPOINTS = {
    "humana-fhir": "https://fhir.humana.com/api",
    "cigna-fhir": "https://p-hi2.digitaledge.cigna.com/ProviderDirectory/v1",
    "uhc": "https://flex.optum.com/fhirpublic/R4",
}
```

To:
```python
KNOWN_ENDPOINTS = {
    "humana-fhir": "https://fhir.humana.com/api",
    "cigna-fhir": "https://fhir.cigna.com/ProviderDirectory/v1",
    # UnitedHealthcare's public PDEX directory (Optum FHIR Layer Exchange) — no auth/login.
    "uhc": "https://flex.optum.com/fhirpublic/R4",
}
```

- [ ] **Step 2: Verify existing fhir_pdex tests still pass (they use explicit base_url)**

```
pytest tests/test_fhir_pdex.py -m "not live" -q
```

Expected: all pass (tests construct FhirPdexAdapter with explicit `base_url`, not via KNOWN_ENDPOINTS).

- [ ] **Step 3: Verify MATRIX.md Cigna prod URL is already correct**

The roster_seed already has `https://fhir.cigna.com/ProviderDirectory/v1` (confirmed). No change needed there.

- [ ] **Step 4: Commit**

```bash
git add src/network_probe/payers/adapters/fhir_pdex.py
git commit -m "fix(fhir-pdex): cigna-fhir → production endpoint fhir.cigna.com"
```

---

### Task 4: Create migration 0008_directory_fixes

**Files:**
- Create: `alembic/versions/0008_directory_fixes.py`

**Interfaces:**
- `down_revision = "0007_payer_sources"`
- Corrects global payer rows (tenant_id IS NULL) for Wellpoint and Healthspring

- [ ] **Step 1: Write the migration**

```python
"""directory catalogue fixes: Wellpoint→needs-auth, Healthspring→public-fhir

Revision ID: 0008_directory_fixes
Revises: 0007_payer_sources
Create Date: 2026-06-28
"""

from collections.abc import Sequence

from sqlalchemy import text

from alembic import op

revision: str = "0008_directory_fixes"
down_revision: str | None = "0007_payer_sources"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_WELLPOINT_UP = text(
    "UPDATE payers SET fhir_base_url = NULL, directory_access = 'needs-authorized-api' "
    "WHERE tenant_id IS NULL AND label = 'Wellpoint / Amerigroup (Elevance)'"
)
_HEALTHSPRING_UP = text(
    "UPDATE payers SET "
    "fhir_base_url = 'https://p-hi2.digitaledge.cigna.com/ProviderDirectory/v1', "
    "directory_access = 'public-fhir' "
    "WHERE tenant_id IS NULL AND label = 'Healthspring'"
)
_WELLPOINT_DOWN = text(
    "UPDATE payers SET "
    "fhir_base_url = 'https://totalview.healthos.elevancehealth.com/resources/registered/Wellpoint/api/v1/fhir', "
    "directory_access = 'public-fhir' "
    "WHERE tenant_id IS NULL AND label = 'Wellpoint / Amerigroup (Elevance)'"
)
_HEALTHSPRING_DOWN = text(
    "UPDATE payers SET fhir_base_url = NULL, directory_access = 'needs-authorized-api' "
    "WHERE tenant_id IS NULL AND label = 'Healthspring'"
)


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(_WELLPOINT_UP)
    conn.execute(_HEALTHSPRING_UP)


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(_WELLPOINT_DOWN)
    conn.execute(_HEALTHSPRING_DOWN)
```

- [ ] **Step 2: Apply to preauth_test**

```bash
DATABASE_URL="postgresql+psycopg://postgres:sagar@localhost:5432/preauth_test" alembic upgrade 0008_directory_fixes
```

- [ ] **Step 3: Apply to preauth**

```bash
DATABASE_URL="postgresql+psycopg://postgres:sagar@localhost:5432/preauth" alembic upgrade 0008_directory_fixes
```

- [ ] **Step 4: psql-verify both DBs**

```sql
-- preauth_test
psql -U postgres -d preauth_test -c "SELECT label, state, fhir_base_url, directory_access FROM payers WHERE tenant_id IS NULL AND label IN ('Wellpoint / Amerigroup (Elevance)', 'Healthspring') ORDER BY label, state;"

-- preauth
psql -U postgres -d preauth -c "SELECT label, state, fhir_base_url, directory_access FROM payers WHERE tenant_id IS NULL AND label IN ('Wellpoint / Amerigroup (Elevance)', 'Healthspring') ORDER BY label, state;"
```

Expected (both DBs):
- Wellpoint rows: `fhir_base_url = NULL`, `directory_access = needs-authorized-api`
- Healthspring rows: `fhir_base_url = https://p-hi2.digitaledge.cigna.com/ProviderDirectory/v1`, `directory_access = public-fhir`

- [ ] **Step 5: Run db tests**

```bash
pytest -m db -q
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add alembic/versions/0008_directory_fixes.py
git commit -m "fix(migration): 0008 — correct Wellpoint (auth-gated) + Healthspring (public-fhir)"
```

---

### Task 5: Update docs

**Files:**
- Modify: `docs/payer-sources/SIGNUP-CHECKLIST.md`
- Modify: `docs/payer-sources/MATRIX.md`
- Modify: `docs/TODO.md`

**Interfaces:** none (documentation only)

- [ ] **Step 1: Update SIGNUP-CHECKLIST.md Section A**

Remove Wellpoint from Section A table. Add Healthspring row:
```
| Healthspring (Cigna Medicare) | `https://p-hi2.digitaledge.cigna.com/ProviderDirectory/v1` | public, verified |
```

Add AvMed note after the Section A table:
```
> **AvMed:** Only known FHIR URL has an expired TLS certificate — unusable as a public endpoint. Stedi 270/271 (id 59274) remains the only machine path.
```

- [ ] **Step 2: Move Wellpoint to Section B**

In Section B table, add:
```
| **Wellpoint / Amerigroup (Elevance)** | `WELLPOINT` | `wellpoint.com/developers` (Elevance Health developer portal) | Practice/NPI | OAuth2 client | `https://totalview.healthos.elevancehealth.com/resources/registered/Wellpoint/api/v1/fhir` (registered path, 403 without creds) | `WELLPOINT_FHIR_{CLIENT_ID,CLIENT_SECRET,TOKEN_URL,BASE_URL}` |
```

Remove Healthspring from Section B (it was there as "Healthspring (Cigna Medicare)").

- [ ] **Step 3: Update MATRIX.md Wellpoint row (AZ)**

Change:
```
| Wellpoint / Amerigroup (Elevance) | AZ | Medicare Advantage | — | `https://totalview.healthos.elevancehealth.com/resources/registered/Wellpoint/api/v1/fhir` (verified) | — | public-fhir | Verified public FHIR (routes the directory leg). review: ... |
```

To:
```
| Wellpoint / Amerigroup (Elevance) | AZ | Medicare Advantage | — | — | — | needs-authorized-api | Auth-gated: metadata public but data queries return 403 on registered path. Register at wellpoint.com/developers. review: WLPNT/RUWTL vs resolver 26375 unreconciled — Stedi id left for review. |
```

- [ ] **Step 4: Update MATRIX.md Healthspring rows (AZ + CO-Denver)**

AZ row, change:
```
| Healthspring | AZ | Medicare Advantage | — | — | `https://www.cigna.com/legal/compliance/machine-readable-files` | needs-authorized-api | review: Cigna Medicare brand; research 52192 vs resolver 63092 disagree — left for review. |
```

To:
```
| Healthspring | AZ | Medicare Advantage | — | `https://p-hi2.digitaledge.cigna.com/ProviderDirectory/v1` (verified) | `https://www.cigna.com/legal/compliance/machine-readable-files` | public-fhir | Verified public FHIR (Cigna Medicare brand). review: Stedi id 52192 vs 63092 disagree — left for review. |
```

CO-Denver row similarly.

- [ ] **Step 5: Update MATRIX.md Counts**

Change:
```
- Rows with `fhir_base_url`: **12** (Cigna 6, Humana 2, Devoted 1, Wellpoint 1, AmeriHealth Caritas 2).
- By `directory_access`: `needs-authorized-api` 40 · `none` 8 · `public-fhir` 19
```

To:
```
- Rows with `fhir_base_url`: **13** (Cigna 6, Humana 2, Devoted 1, Healthspring 2, AmeriHealth Caritas 2).
- By `directory_access`: `needs-authorized-api` 39 · `none` 8 · `public-fhir` 20
```

- [ ] **Step 6: Update docs/TODO.md FHIR directories section**

Change:
```
- ☑ Public + wired: Cigna, Humana, Devoted, Wellpoint/Amerigroup, AmeriHealth Caritas, UHC (Optum), Oscar.
```

To:
```
- ☑ Public + wired (verified): Cigna, Humana, Devoted, Healthspring (Cigna Medicare `p-hi2.digitaledge.cigna.com`), AmeriHealth Caritas, UHC (Optum), Oscar. Wellpoint/Amerigroup moved to needs-creds (registered path is auth-gated; register at wellpoint.com/developers).
```

- [ ] **Step 7: Commit**

```bash
git add docs/payer-sources/SIGNUP-CHECKLIST.md docs/payer-sources/MATRIX.md docs/TODO.md
git commit -m "docs: update directory matrix + checklist — Wellpoint auth-gated, Healthspring public, AvMed dead"
```

---

### Task 6: Final verification

- [ ] **Step 1: ruff check**

```bash
ruff check src tests scripts
```

Expected: no issues.

- [ ] **Step 2: Full pure test run**

```bash
pytest -m "not live and not db" -q
```

Expected: all pass, print count.

- [ ] **Step 3: DB test run**

```bash
pytest -m db -q
```

Expected: all pass, print count.

- [ ] **Step 4: Final commit and summary**

Report base SHA + new HEAD SHA, test counts, psql verification results.
