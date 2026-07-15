# Centene PractitionerRole Reference-Format Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix `FhirPdexAdapter._networks_for()` so it correctly resolves network roles from Centene's FHIR server (which requires the full `Practitioner/<id>` reference form) without breaking any already-working server (which use a bare id).

**Architecture:** Extract the existing page-walking query logic into a small private helper that fetches one attempt's worth of `PractitionerRole` results for a given `practitioner=` query value. `_networks_for()` calls it with a bare id first (today's default, unchanged for every already-verified server), and only retries once with the full `Practitioner/<id>` form if the first attempt found zero roles. Pure internal refactor — no public signature changes, no caller changes.

**Tech Stack:** Python 3.12, pytest, httpx.MockTransport (existing offline-test pattern in `tests/test_fhir_pdex.py`).

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-15-centene-practitioner-ref-fix-design.md` — read it first; this plan implements it exactly.
- No signature change to `_networks_for()`. No change to `check_network()`, `KNOWN_ENDPOINTS`, `ScanDirectoryAdapter`, or any other adapter/caller.
- Bare id must remain the first attempt (every already-verified server — UHC, HCSC, Humana, Cigna, Kaiser, Molina — must stay on its current, unmodified working path; verified via a request-count assertion, not just a final-result assertion).
- The full-reference retry fires only when the bare-id attempt found zero roles — never both attempts unconditionally, never a hardcoded per-payer format table.
- Community Care Plan (FL) and the separate UHC/Optum Organization-dereference quirk are out of scope — do not touch.

---

## File Structure

- **Modify** `src/network_probe/payers/adapters/fhir_pdex.py` — extract `_fetch_practitioner_roles()`, update `_networks_for()` to try-then-fallback.
- **Modify** `tests/test_fhir_pdex.py` — add a Centene-shaped mock handler and 3 new offline tests.

No new files.

---

### Task 1: Fix `_networks_for()` with a bare-id-then-full-reference fallback

**Files:**
- Modify: `src/network_probe/payers/adapters/fhir_pdex.py:163-213` (`_networks_for()`)
- Test: `tests/test_fhir_pdex.py`

**Interfaces:**
- Produces: new private method `FhirPdexAdapter._fetch_practitioner_roles(self, practitioner_ref: str) -> tuple[list[str], list[str], set[str], int]` — returns `(inline_names, org_refs, specialties, role_count)` for one query attempt against `PractitionerRole?practitioner=<practitioner_ref>`. `practitioner_ref` is either a bare id (e.g. `"3287471"`) or a full reference (e.g. `"Practitioner/3287471"`) — the caller decides which to pass.
- `_networks_for(self, practitioner_id: str) -> tuple[list[str], list[str], int]` keeps its existing public signature and return shape (`network_names, specialties, role_count`) — unchanged for every caller.

- [ ] **Step 1: Write the failing tests**

Open `tests/test_fhir_pdex.py`. After `test_uhc_bronze_essential_resolves_in_network_via_alias` (currently ending around line 245) and before the `# ---- live (real Humana CMS Provider Directory API) ----` comment (currently line 248), add:

```python
# --- Centene-shaped server: bare id returns zero roles, full "Practitioner/<id>" reference
# form is required. Regression for the bug found in the Meridian Health Medicaid sub-project
# (docs/superpowers/specs/2026-07-15-centene-practitioner-ref-fix-design.md). ------------------

CENTENE_PID = "3287471"
CENTENE_NPI = "1588744650"


def _centene_handler(request: httpx.Request) -> httpx.Response:
    u = urlsplit(str(request.url))
    qs = parse_qs(u.query)
    if u.path.endswith("/Practitioner"):
        if "identifier" in qs:  # Centene rejects identifier search (matches production: HTTP 400)
            return httpx.Response(
                400,
                json={"resourceType": "OperationOutcome", "issue": [{"severity": "error", "code": "not-supported"}]},
            )
        if (qs.get("family") or [""])[0].lower() == "petermann":
            return httpx.Response(
                200,
                json={
                    "resourceType": "Bundle",
                    "total": 1,
                    "entry": [
                        {
                            "resource": {
                                "resourceType": "Practitioner",
                                "id": CENTENE_PID,
                                "name": [{"text": "Dr. Kevin Louis Petermann"}],
                                "identifier": [{"system": "http://hl7.org/fhir/sid/us-npi", "value": CENTENE_NPI}],
                            }
                        }
                    ],
                },
            )
        if (qs.get("family") or [""])[0].lower() == "noroles":
            return httpx.Response(
                200,
                json={
                    "resourceType": "Bundle",
                    "total": 1,
                    "entry": [
                        {
                            "resource": {
                                "resourceType": "Practitioner",
                                "id": "NOROLE1",
                                "name": [{"text": "Dr. No Roles"}],
                                "identifier": [{"system": "http://hl7.org/fhir/sid/us-npi", "value": "1000000004"}],
                            }
                        }
                    ],
                },
            )
        return httpx.Response(200, json={"resourceType": "Bundle", "total": 0, "entry": []})
    if u.path.endswith("/PractitionerRole"):
        prac = (qs.get("practitioner") or [""])[0]
        if prac == f"Practitioner/{CENTENE_PID}":  # the form Centene actually requires
            return httpx.Response(
                200,
                json={
                    "resourceType": "Bundle",
                    "total": 1,
                    "entry": [
                        {
                            "resource": {
                                "resourceType": "PractitionerRole",
                                "id": "cr1",
                                "extension": [{"url": NET_EXT, "valueReference": {"display": "IL SNP"}}],
                            }
                        }
                    ],
                },
            )
        # bare id (or any other reference) -> Centene's real behavior: zero roles
        return httpx.Response(200, json={"resourceType": "Bundle", "total": 0, "entry": []})
    return httpx.Response(404, json={})


def _centene_adapter() -> FhirPdexAdapter:
    mock = httpx.Client(transport=httpx.MockTransport(_centene_handler))
    return FhirPdexAdapter(
        base_url="https://example.org/fhir",
        payer_name="meridian",
        client=CachedClient(cache_dir=None, delay_seconds=0, client=mock),
    )


def test_centene_shaped_server_falls_back_to_full_reference():
    """Bare id returns zero roles (Centene's real behavior) -> retry with Practitioner/<id> ->
    resolves the real network."""
    v = _centene_adapter().check_network(
        ProviderQuery(payer="meridian", plan_hint="", npi=CENTENE_NPI, provider_last_name="Petermann")
    )
    assert v.status == NetworkStatus.IN_NETWORK
    assert "IL SNP" in v.matched_provider["networks"]


def test_centene_shaped_server_genuinely_zero_roles_reports_no_active_roles():
    """Both the bare-id and Practitioner/<id> attempts legitimately return zero -> still an
    honest 'no active network roles' result, not an error and not a false IN_NETWORK."""
    v = _centene_adapter().check_network(
        ProviderQuery(payer="meridian", plan_hint="", npi="1000000004", provider_last_name="Noroles")
    )
    assert v.status == NetworkStatus.OUT_OF_NETWORK
    assert "no active network roles" in v.notes


def test_bare_id_success_makes_no_fallback_request():
    """Existing bare-id-only servers (Humana) must not pay for a retry they don't need."""
    role_requests = []

    def counting_handler(request: httpx.Request) -> httpx.Response:
        u = urlsplit(str(request.url))
        if u.path.endswith("/PractitionerRole"):
            role_requests.append(str(request.url))
        return _fhir_handler(request)

    mock = httpx.Client(transport=httpx.MockTransport(counting_handler))
    cc = CachedClient(cache_dir=None, delay_seconds=0, client=mock)
    a = FhirPdexAdapter(base_url=HUMANA, payer_name="humana-fhir", client=cc)
    v = a.check_network(_q(KYLE_NPI, "Medicare PPO"))
    assert v.status == NetworkStatus.IN_NETWORK
    assert len(role_requests) == 1, role_requests
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `source .venv/bin/activate && python3 -m pytest tests/test_fhir_pdex.py::test_centene_shaped_server_falls_back_to_full_reference tests/test_fhir_pdex.py::test_centene_shaped_server_genuinely_zero_roles_reports_no_active_roles tests/test_fhir_pdex.py::test_bare_id_success_makes_no_fallback_request -v`
Expected: `test_centene_shaped_server_falls_back_to_full_reference` FAILS (`assert v.status == NetworkStatus.IN_NETWORK` — actual is `OUT_OF_NETWORK`, since today's code only tries the bare id and Centene's mock returns zero for that). `test_centene_shaped_server_genuinely_zero_roles_reports_no_active_roles` and `test_bare_id_success_makes_no_fallback_request` PASS already (today's code happens to satisfy them, since it never retries at all) — that's fine, they're written now so they stay green through the fix and catch any future regression in either direction.

- [ ] **Step 3: Extract `_fetch_practitioner_roles()` and update `_networks_for()`**

In `src/network_probe/payers/adapters/fhir_pdex.py`, find this block (currently lines 163-213):

```python
    def _networks_for(self, practitioner_id: str) -> tuple[list[str], list[str], int]:
        """Return (network_names, specialties, role_count) across all PractitionerRole pages.

        Network name comes from the PDEX network-reference extension's display when present
        (Humana); when only an Organization reference is given (Cigna), resolve it to a name.
        """
        url = f"{self.base_url}/PractitionerRole?practitioner={quote(practitioner_id)}&_count=50"
        names: list[str] = []
        refs: list[str] = []
        specialties: set[str] = set()
        roles = 0
        pages = 0
        while url and pages < MAX_ROLE_PAGES:
            bundle = self._get(url)
            for e in bundle.get("entry") or []:
                r = e.get("resource") or {}
                if r.get("resourceType") != "PractitionerRole":
                    continue
                roles += 1
                for ext in r.get("extension") or []:
                    if ext.get("url") == NETWORK_EXT_URL:
                        vr = ext.get("valueReference") or {}
                        if vr.get("display"):
                            names.append(vr["display"])
                        elif vr.get("reference"):
                            refs.append(vr["reference"])
                for c in r.get("specialty") or []:
                    for cd in c.get("coding") or []:
                        if cd.get("display"):
                            specialties.add(cd["display"])
            url = self._next_link(bundle)
            pages += 1
        # resolve Organization references that lacked an inline display name
        uniq_refs, seen_ref = [], set()
        for ref in refs:
            if ref not in seen_ref:
                seen_ref.add(ref)
                uniq_refs.append(ref)
        for ref in uniq_refs[:MAX_ORG_RESOLVE]:
            nm = self._org_name(ref)
            if nm:
                names.append(nm)
        if len(uniq_refs) > MAX_ORG_RESOLVE:
            names.append(f"(+{len(uniq_refs) - MAX_ORG_RESOLVE} more network organizations)")
        # de-dup network names, preserve order
        seen, uniq = set(), []
        for n in names:
            if n not in seen:
                seen.add(n)
                uniq.append(n)
        return uniq, sorted(specialties), roles
```

Replace with:

```python
    def _fetch_practitioner_roles(
        self, practitioner_ref: str
    ) -> tuple[list[str], list[str], set[str], int]:
        """Fetch one query attempt's worth of PractitionerRole pages for `practitioner_ref`
        (either a bare id or a full "Practitioner/<id>" reference -- the caller decides which).
        Returns (inline_names, org_refs, specialties, role_count) -- no Organization-reference
        resolution yet; that happens once in _networks_for(), after the winning attempt is
        chosen, so a retry never resolves the same Organization twice."""
        url = f"{self.base_url}/PractitionerRole?practitioner={quote(practitioner_ref)}&_count=50"
        names: list[str] = []
        refs: list[str] = []
        specialties: set[str] = set()
        roles = 0
        pages = 0
        while url and pages < MAX_ROLE_PAGES:
            bundle = self._get(url)
            for e in bundle.get("entry") or []:
                r = e.get("resource") or {}
                if r.get("resourceType") != "PractitionerRole":
                    continue
                roles += 1
                for ext in r.get("extension") or []:
                    if ext.get("url") == NETWORK_EXT_URL:
                        vr = ext.get("valueReference") or {}
                        if vr.get("display"):
                            names.append(vr["display"])
                        elif vr.get("reference"):
                            refs.append(vr["reference"])
                for c in r.get("specialty") or []:
                    for cd in c.get("coding") or []:
                        if cd.get("display"):
                            specialties.add(cd["display"])
            url = self._next_link(bundle)
            pages += 1
        return names, refs, specialties, roles

    def _networks_for(self, practitioner_id: str) -> tuple[list[str], list[str], int]:
        """Return (network_names, specialties, role_count) across all PractitionerRole pages.

        Network name comes from the PDEX network-reference extension's display when present
        (Humana); when only an Organization reference is given (Cigna), resolve it to a name.

        Tries a bare practitioner id first (the default -- UHC/HCSC/Humana/Cigna/Kaiser/Molina
        are all verified working with this form). Some servers (confirmed: Centene's HAPI FHIR)
        reject a bare id for the `practitioner=` search param and only return roles for the full
        `Practitioner/<id>` reference form -- HCSC's Sapphire server is the reverse (bare id
        works, full reference returns nothing), so neither form is safe to hardcode. Retrying
        once with the full reference form only when the bare id found zero roles keeps every
        already-working server on its current path. See docs/superpowers/specs/
        2026-07-15-centene-practitioner-ref-fix-design.md.
        """
        names, refs, specialties, roles = self._fetch_practitioner_roles(practitioner_id)
        if roles == 0:
            names, refs, specialties, roles = self._fetch_practitioner_roles(f"Practitioner/{practitioner_id}")
        # resolve Organization references that lacked an inline display name
        uniq_refs, seen_ref = [], set()
        for ref in refs:
            if ref not in seen_ref:
                seen_ref.add(ref)
                uniq_refs.append(ref)
        for ref in uniq_refs[:MAX_ORG_RESOLVE]:
            nm = self._org_name(ref)
            if nm:
                names.append(nm)
        if len(uniq_refs) > MAX_ORG_RESOLVE:
            names.append(f"(+{len(uniq_refs) - MAX_ORG_RESOLVE} more network organizations)")
        # de-dup network names, preserve order
        seen, uniq = set(), []
        for n in names:
            if n not in seen:
                seen.add(n)
                uniq.append(n)
        return uniq, sorted(specialties), roles
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `source .venv/bin/activate && python3 -m pytest tests/test_fhir_pdex.py::test_centene_shaped_server_falls_back_to_full_reference tests/test_fhir_pdex.py::test_centene_shaped_server_genuinely_zero_roles_reports_no_active_roles tests/test_fhir_pdex.py::test_bare_id_success_makes_no_fallback_request -v`
Expected: all 3 PASS.

- [ ] **Step 5: Run the full non-live test file to check for regressions**

Run: `source .venv/bin/activate && python3 -m pytest tests/test_fhir_pdex.py -k "not live" -v`
Expected: all pass (12 passed, 3 deselected — the pre-existing 9 plus this task's 3 new tests). This exercises every existing offline fixture (Humana bare-id, Cigna/UHC-style Organization-reference resolution, UHC plan-alias matching) unmodified, proving the refactor didn't change behavior for any of them.

- [ ] **Step 6: Run `tests/test_payer_sources.py` as an unrelated-regression check**

Run: `source .venv/bin/activate && python3 -m pytest tests/test_payer_sources.py -k "not db" -q`
Expected: `24 passed, 2 deselected` — unchanged from before this task (this file doesn't touch the roster/catalogue, only the adapter's internal query logic).

- [ ] **Step 7: Commit**

```bash
git add src/network_probe/payers/adapters/fhir_pdex.py tests/test_fhir_pdex.py
git commit -m "$(cat <<'EOF'
fix(fhir): resolve Centene PractitionerRole with the correct reference form

FhirPdexAdapter._networks_for() queried PractitionerRole?practitioner=
with a bare id, which silently returns zero roles on Centene's HAPI FHIR
server (it requires the full "Practitioner/<id>" reference form). HCSC's
Sapphire server is the reverse -- confirmed live, neither form is safe
to hardcode. Now tries the bare id first (every already-verified server
stays on its current path) and retries once with the full reference
form only if that found zero roles, mirroring the existing
identifier-then-name-search fallback already in this file.

This was silently affecting every Centene-family payer already in
production (Ambetter, Wellcare, AZ Complete Health, Peach State,
Superior HealthPlan, Meridian Health) -- found during live verification
of the Meridian Health Medicaid sub-project.
EOF
)"
```

---

### Task 2: Full verification

**Files:** none modified — verification only.

- [ ] **Step 1: Run the full fhir_pdex test file (excluding live tests)**

Run: `source .venv/bin/activate && python3 -m pytest tests/test_fhir_pdex.py -k "not live" -v`
Expected: all pass, 0 failures (12 passed, 3 deselected).

- [ ] **Step 2: Run the broader test suite to check for unrelated regressions**

Run: `source .venv/bin/activate && python3 -m pytest tests/ -k "not db" -q`
Expected: no new failures compared to the pre-task-1 baseline. (If you see 2 failures in `tests/test_override_seed.py` about a missing `.overrides/overrides.json` file, that's a pre-existing, worktree-environment-only gap unrelated to this change — confirmed in the prior Medicaid sub-project's verification; it passes in the main repo checkout, just not in a fresh worktree that never had that local, gitignored file. Don't treat it as a regression, but do record it in your report.)

- [ ] **Step 3: Manual live smoke test — confirm the fix against the real Centene and HCSC servers**

This test hits two real external APIs using this repo's existing `.env` credentials — expected and intentional, matching the equivalent step in the prior two plans this session.

Run:
```bash
source .venv/bin/activate && python3 -c "
import sys
sys.path.insert(0, 'src')
from network_probe.payers.roster_seed import SOURCES
from network_probe.payers.adapters.fhir_pdex import FhirPdexAdapter
from network_probe.payers.adapters.fhir_auth import build_apikey_fhir_adapter
from network_probe.domain.models import ProviderQuery
from network_probe.core.config import get_settings

# --- Meridian Health / Centene: this is the exact call that surfaced the bug ---
meridian_adapter = FhirPdexAdapter(base_url=SOURCES['Meridian Health'][0], payer_name='meridian')
q = ProviderQuery(payer='meridian', plan_hint='', npi='1588744650', provider_first_name='Kevin', provider_last_name='Petermann', state='IL')
v = meridian_adapter.check_network(q)
print('Meridian (Centene) status:', v.status)
print('Meridian networks:', v.matched_provider['networks'] if v.matched_provider else None)
assert str(v.status).endswith('IN_NETWORK'), v.status
assert v.matched_provider['networks'], 'expected real network names, got none'

# --- HCSC: confirm the already-working path is genuinely untouched, not passing by coincidence ---
s = get_settings()
hcsc_adapter = build_apikey_fhir_adapter(
    payer_key='hcsc', base_url=SOURCES['BCBS / Empire (Anthem / Elevance)(HCSC)'][0],
    header_name='client_id', header_value=s.hcsc_fhir_client_id,
)
hq = ProviderQuery(payer='hcsc', plan_hint='', npi='1336160274', provider_first_name=None, provider_last_name='Friedman', state='IL')
hv = hcsc_adapter.check_network(hq)
print('HCSC status:', hv.status)
print('HCSC networks:', hv.matched_provider['networks'] if hv.matched_provider else None)
assert str(hv.status).endswith('IN_NETWORK'), hv.status
assert hv.matched_provider['networks'], 'expected real network names, got none'
print('ALL CHECKS PASSED')
"
```
Expected: prints `Meridian (Centene) status: NetworkStatus.IN_NETWORK` with a non-empty networks list (should include `'IL SNP'` among others, matching the real data found during this sub-project's research), `HCSC status: NetworkStatus.IN_NETWORK` with a non-empty networks list (should include HCSC network names like the ones found during the Medicaid sub-project's research), and `ALL CHECKS PASSED`.

If either assertion fails, do not treat it as flaky and move on — report BLOCKED with the exact output. A Meridian failure here would mean the fix itself is wrong; an HCSC failure would mean the fix broke the already-working path (the one thing this plan's Global Constraints explicitly forbid).

- [ ] **Step 4: Review the full diff before wrap-up**

Run: `git log --oneline -2` and `git diff <task-1-base>..HEAD --stat` (use the actual base commit you recorded before Task 1).
Expected: 1 commit from this plan (Task 1), touching `src/network_probe/payers/adapters/fhir_pdex.py` and `tests/test_fhir_pdex.py` only.

No further commit needed for this task — it's verification-only. If Step 1, 2, or 3 fails, stop and fix the responsible task before proceeding to close out this plan.

---

## After this plan

Community Care Plan (FL) is next — separate spec/plan cycle, needs a new PDF-parser `format` in
`src/network_probe/domain/directory_pdf.py`. The separate UHC/Optum Organization-dereference
quirk (different server, different code path — `_org_name()`, not touched by this plan) remains
open as a lower-priority follow-up.
