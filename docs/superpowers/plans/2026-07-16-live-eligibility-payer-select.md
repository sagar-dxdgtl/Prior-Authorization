# Live Eligibility — Searchable Payer Select + 271-Grounded Plan — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the Eligibility page run live checks against the whole payer roster by picking a payer from a searchable select, then derive the member's plan from the 271 itself (no up-front guess) to scope the network verdict, with an override that re-checks the network only.

**Architecture:** Two payer-agnostic legs already exist in `check_eligibility` (Stedi 270/271 → benefits; directory → network verdict; then a merge). This plan (1) reads the member's real plan from `benefitsInformation[].planCoverage` and uses it to auto-scope the directory leg, (2) extracts the merge into a pure `reconcile()` reused by a new network-only re-check route, (3) adds a `GET /api/payers/search` (roster-first, Stedi-directory fallback) and a direct-`stedi_payer_id` path so any Stedi payer runs even without a roster row, and (4) reworks `Eligibility.tsx` into a searchable payer select + a post-271 plan control.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy (Postgres, RLS), httpx (`CachedClient` + `MockTransport` in tests), pytest (markers `not live`/`db`/`live`); React + TypeScript + Vite + Ant Design (`web/`).

## Global Constraints

- **PHI never hits disk:** Stedi/eligibility clients construct `CachedClient(cache_dir=None, ...)`. Never cache 270/271 traffic. (Existing rule in `stedi/client.py`.)
- **Correctness invariants (never weaken):** directory-vs-271 conflict → `REVIEW`; ambiguity → `UNKNOWN`, never `OUT_OF_NETWORK`; tenant golden-record override is the authoritative last word.
- **No fabrication:** if the 271 yields no usable plan string, `selected_plan` is `None` and the directory leg runs unscoped/`UNKNOWN` — never invent a network.
- **New dataclass fields are added with defaults** (`plan_candidates`, `selected_plan`, `stedi_network_status`) so existing `EligibilityResult(...)` constructors in `parse_271.py`, `stedi/client.py`, and `api/app.py` keep working unchanged.
- **Fast tests must stay green without a DB or network:** `pytest -m "not live and not db"`. DB-marked tests require local `preauth_test` (see README §Tests).
- **Plan string lives in `benefitsInformation[].planCoverage`** (a string), NOT `planInformation` (usually `{}` or the employer group). Verified against `.cache/stedi_271/`.

---

## File Structure

**Create:**
- `src/network_probe/domain/plan_candidates.py` — pure: extract + rank plan strings from a 271's `benefitsInformation`.
- `src/network_probe/payers/search.py` — pure/mockable payer search: `search_roster`, `search_stedi`, `load_roster_rows`.
- `web/src/services/payers.ts` — frontend API calls: `searchPayers`, `recheckNetwork`.
- `tests/test_plan_candidates.py`, `tests/test_reconcile.py`, `tests/test_payer_search.py`.

**Modify:**
- `src/network_probe/domain/benefits.py` — add 3 fields to `EligibilityResult` + `to_dict()`.
- `src/network_probe/stedi/parse_271.py` — populate `plan_candidates`/`selected_plan`; prefer derived plan for `plan_name`.
- `src/network_probe/domain/eligibility.py` — add `reconcile()`, `recheck_network()`; rework `check_eligibility` (use `reconcile`, `stedi_payer_id` bypass, auto-scope by `selected_plan`, capture `stedi_network_status`).
- `src/network_probe/api/app.py` — `CheckRequest.stedi_payer_id`; `GET /api/payers/search`; `POST /api/eligibility/recheck-network`; pass `stedi_payer_id` into `check_eligibility`.
- `web/src/pages/Eligibility.tsx` — payer searchable select; drop up-front plan; post-271 plan control + re-check.
- `tests/test_parse_271.py`, `tests/test_api_eligibility.py` — extend.

---

## Task 1: Plan-candidate extraction (pure)

**Files:**
- Create: `src/network_probe/domain/plan_candidates.py`
- Test: `tests/test_plan_candidates.py`

**Interfaces:**
- Produces: `derive_plan_candidates(benefits_information: list[dict]) -> tuple[list[dict], str | None]` — returns `(candidates, selected)` where each candidate is `{"plan": str, "is_product": bool, "rank": int}` (rank 0 = best) and `selected` is the top-ranked plan string or `None`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_plan_candidates.py
from network_probe.domain.plan_candidates import derive_plan_candidates


def _infos(*plan_strings):
    return [{"planCoverage": s} for s in plan_strings]


def test_dual_eligible_ranks_product_over_segment():
    # Devoted TX dual-eligible 271 (real values from .cache/stedi_271/1720209885.json)
    cands, selected = derive_plan_candidates(
        _infos("03 - SLMB ONLY (PARTIAL DUAL)", "DEVOTED GIVEBACK 006 TX (HMO)")
    )
    assert selected == "DEVOTED GIVEBACK 006 TX (HMO)"
    assert [c["plan"] for c in cands] == ["DEVOTED GIVEBACK 006 TX (HMO)", "03 - SLMB ONLY (PARTIAL DUAL)"]
    assert cands[0]["is_product"] is True and cands[0]["rank"] == 0


def test_oscar_first_of_two():
    cands, selected = derive_plan_candidates(_infos("BASE SILVER CSR 150", "SILVERSIMPLEPCPSAVER"))
    assert selected == "BASE SILVER CSR 150"
    assert len(cands) == 2


def test_generic_network_is_dropped():
    # Cigna returns only the useless string "Network" -> no usable candidate
    cands, selected = derive_plan_candidates(_infos("Network"))
    assert cands == [] and selected is None


def test_dedup_and_blank_skipped():
    cands, selected = derive_plan_candidates(_infos("UHC BRONZE ESSENTIAL", "", "UHC BRONZE ESSENTIAL"))
    assert [c["plan"] for c in cands] == ["UHC BRONZE ESSENTIAL"]
    assert selected == "UHC BRONZE ESSENTIAL"


def test_empty_input():
    assert derive_plan_candidates([]) == ([], None)
    assert derive_plan_candidates(None) == ([], None)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_plan_candidates.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'network_probe.domain.plan_candidates'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/network_probe/domain/plan_candidates.py
"""Derive the member's plan(s) from a 271's benefitsInformation[].planCoverage strings.

The plan the payer actually returns lives in benefitsInformation[].planCoverage (a string like
"DEVOTED CHOICE GIVEBACK 003 CO (PPO)" / "BASE SILVER CSR 150"), NOT planInformation (usually {}).
Most 271s carry 2+ distinct values (e.g. a dual-eligible member has an MA product line AND a
Medicaid segment); we rank a real product/network line above a coverage segment, and drop generic
junk ("Network") so we never scope a directory search on a meaningless string.
"""

from __future__ import annotations

import re

# Real product/network markers (metal tiers, network types, MA product words).
_PRODUCT = re.compile(r"\b(HMO|PPO|EPO|POS|SILVER|BRONZE|GOLD|PLATINUM|CHOICE|ESSENTIAL|ADVANTAGE)\b", re.I)
# Coverage-segment markers that are NOT a network to search (dual-eligible / affiliation lines).
_SEGMENT = re.compile(r"\b(SLMB|QMB|PARTIAL DUAL|DUAL|MEDICAID|AFFILIATION|CENTER)\b", re.I)
# Generic strings that carry no plan identity — never a usable directory scope.
_JUNK = {"", "network", "health benefit plan coverage", "medical", "coverage"}


def _is_product(s: str) -> bool:
    return bool(_PRODUCT.search(s))


def _is_segment(s: str) -> bool:
    return bool(_SEGMENT.search(s))


def _rank_bucket(s: str) -> int:
    if _is_segment(s):
        return 2
    if _is_product(s):
        return 0
    return 1


def derive_plan_candidates(benefits_information: list[dict] | None) -> tuple[list[dict], str | None]:
    order: list[str] = []
    seen: set[str] = set()
    for b in benefits_information or []:
        pc = (b.get("planCoverage") or "").strip()
        if pc.lower() in _JUNK or pc.lower() in seen:
            continue
        seen.add(pc.lower())
        order.append(pc)
    ranked = sorted(order, key=lambda s: (_rank_bucket(s), order.index(s)))
    candidates = [{"plan": s, "is_product": _is_product(s), "rank": i} for i, s in enumerate(ranked)]
    return candidates, (ranked[0] if ranked else None)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_plan_candidates.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add src/network_probe/domain/plan_candidates.py tests/test_plan_candidates.py
git commit -m "feat(eligibility): derive ranked plan candidates from 271 planCoverage"
```

---

## Task 2: Surface plan candidates on `EligibilityResult` + `parse_271`

**Files:**
- Modify: `src/network_probe/domain/benefits.py` (`EligibilityResult` dataclass + `to_dict`)
- Modify: `src/network_probe/stedi/parse_271.py` (`parse_271_benefits`)
- Test: `tests/test_parse_271.py`

**Interfaces:**
- Consumes: `derive_plan_candidates` (Task 1).
- Produces: `EligibilityResult.plan_candidates: list` (default `[]`), `EligibilityResult.selected_plan: str | None` (default `None`), both in `to_dict()`. `parse_271_benefits` sets them and prefers `selected_plan` for `plan_name`.

- [ ] **Step 1: Write the failing test** (append to `tests/test_parse_271.py`)

```python
def test_plan_candidates_from_plan_coverage():
    data = {
        "benefitsInformation": [
            {"code": "1", "planCoverage": "DEVOTED GIVEBACK 006 TX (HMO)"},
            {"code": "1", "planCoverage": "03 - SLMB ONLY (PARTIAL DUAL)"},
        ]
    }
    r = parse_271_benefits(data)
    assert r.selected_plan == "DEVOTED GIVEBACK 006 TX (HMO)"
    assert [c["plan"] for c in r.plan_candidates] == [
        "DEVOTED GIVEBACK 006 TX (HMO)",
        "03 - SLMB ONLY (PARTIAL DUAL)",
    ]
    # plan_name prefers the derived plan over the (empty) planInformation
    assert r.plan_name == "DEVOTED GIVEBACK 006 TX (HMO)"
    d = r.to_dict()
    assert d["selected_plan"] == "DEVOTED GIVEBACK 006 TX (HMO)"
    assert d["plan_candidates"][0]["plan"] == "DEVOTED GIVEBACK 006 TX (HMO)"


def test_no_usable_plan_leaves_selected_none():
    r = parse_271_benefits({"benefitsInformation": [{"code": "1", "planCoverage": "Network"}]})
    assert r.selected_plan is None and r.plan_candidates == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_parse_271.py::test_plan_candidates_from_plan_coverage -v`
Expected: FAIL — `AttributeError: 'EligibilityResult' object has no attribute 'selected_plan'`

- [ ] **Step 3: Write minimal implementation**

In `src/network_probe/domain/benefits.py`, add two fields at the END of the `EligibilityResult` dataclass (after `source_audit`), each with a default so existing constructors stay valid:

```python
    source_audit: dict
    plan_candidates: list = field(default_factory=list)
    selected_plan: str | None = None
```

And in its `to_dict()` return dict add:

```python
            "source_audit": self.source_audit,
            "plan_candidates": self.plan_candidates,
            "selected_plan": self.selected_plan,
```

In `src/network_probe/stedi/parse_271.py`, import the helper at the top:

```python
from network_probe.domain.plan_candidates import derive_plan_candidates
```

Then in `parse_271_benefits`, replace the plan/return block at the end (currently starting `plan = data.get("planInformation") or {}`) with:

```python
    candidates, selected = derive_plan_candidates(infos)
    plan = data.get("planInformation") or {}
    return EligibilityResult(
        coverage_active=coverage_active,
        plan_name=selected or plan.get("planName") or plan.get("groupDescription"),
        group=plan.get("groupNumber"),
        coverage_dates=data.get("planDateInformation") or {},
        network_status=status,
        benefits=lines,
        pcp_required=pcp,
        prior_auth_required=prior_auth,
        referral_required=referral,
        cob=_redact_cob(data.get("coordinationOfBenefits")),
        network_verdict=None,
        corroboration=[],
        source_audit={"source": "stedi-271"},
        plan_candidates=candidates,
        selected_plan=selected,
    )
```

(`field` is already imported in `benefits.py` via `from dataclasses import dataclass, field`.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_parse_271.py -v`
Expected: PASS (all, including the 2 new + the 4 existing — the existing `test_met_paired_and_cob_redacted` still passes because new fields default empty)

- [ ] **Step 5: Commit**

```bash
git add src/network_probe/domain/benefits.py src/network_probe/stedi/parse_271.py tests/test_parse_271.py
git commit -m "feat(eligibility): expose plan_candidates/selected_plan on EligibilityResult"
```

---

## Task 3: Extract the pure `reconcile()` merge

**Files:**
- Modify: `src/network_probe/domain/eligibility.py`
- Test: `tests/test_reconcile.py`

**Interfaces:**
- Produces: `reconcile(stedi_status: NetworkStatus, verdict: NetworkVerdict | None) -> tuple[NetworkStatus, list]` — returns `(final_status, corroboration)` applying the exact existing merge rules.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_reconcile.py
from network_probe.domain.eligibility import reconcile
from network_probe.domain.models import NetworkStatus, NetworkVerdict


def _verdict(status, corr=None):
    return NetworkVerdict(
        status=status, matched_provider=None, plan_or_network_checked="X",
        source_url="http://x", confidence="medium", notes="", corroboration=corr,
    )


def test_directory_in_vs_stedi_out_is_review():
    status, corr = reconcile(NetworkStatus.OUT_OF_NETWORK, _verdict(NetworkStatus.IN_NETWORK))
    assert status == NetworkStatus.REVIEW


def test_directory_out_vs_stedi_in_is_review():
    status, _ = reconcile(NetworkStatus.IN_NETWORK, _verdict(NetworkStatus.OUT_OF_NETWORK))
    assert status == NetworkStatus.REVIEW


def test_stedi_unknown_takes_directory():
    status, _ = reconcile(NetworkStatus.UNKNOWN, _verdict(NetworkStatus.IN_NETWORK))
    assert status == NetworkStatus.IN_NETWORK


def test_agreement_keeps_status_and_passes_corroboration():
    status, corr = reconcile(NetworkStatus.IN_NETWORK, _verdict(NetworkStatus.IN_NETWORK, corr=[{"source": "s"}]))
    assert status == NetworkStatus.IN_NETWORK and corr == [{"source": "s"}]


def test_no_verdict_keeps_stedi_status():
    status, corr = reconcile(NetworkStatus.OUT_OF_NETWORK, None)
    assert status == NetworkStatus.OUT_OF_NETWORK and corr == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_reconcile.py -v`
Expected: FAIL — `ImportError: cannot import name 'reconcile'`

- [ ] **Step 3: Write minimal implementation**

In `src/network_probe/domain/eligibility.py`, add near the top (after imports) a pure function, then make `check_eligibility` call it. Add:

```python
def reconcile(stedi_status: NetworkStatus, verdict) -> tuple[NetworkStatus, list]:
    """Merge the 271-derived network status with the directory verdict (the correctness core).

    Rules (unchanged): directory IN vs 271 OUT -> REVIEW; directory OUT vs 271 IN -> REVIEW;
    271 UNKNOWN + decisive directory -> take the directory; otherwise keep the 271 status.
    """
    if verdict is None:
        return stedi_status, []
    corr = verdict.corroboration or []
    status = stedi_status
    if verdict.status == NetworkStatus.IN_NETWORK and stedi_status == NetworkStatus.OUT_OF_NETWORK:
        status = NetworkStatus.REVIEW
    elif verdict.status == NetworkStatus.OUT_OF_NETWORK and stedi_status == NetworkStatus.IN_NETWORK:
        status = NetworkStatus.REVIEW
    elif stedi_status == NetworkStatus.UNKNOWN and verdict.status != NetworkStatus.UNKNOWN:
        status = verdict.status
    return status, corr
```

Then in `check_eligibility`, replace the block:

```python
    if verdict is not None:
        result.network_verdict = verdict
        result.corroboration = verdict.corroboration or []
        if verdict.status == NetworkStatus.IN_NETWORK and result.network_status == NetworkStatus.OUT_OF_NETWORK:
            result.network_status = NetworkStatus.REVIEW
        elif verdict.status == NetworkStatus.OUT_OF_NETWORK and result.network_status == NetworkStatus.IN_NETWORK:
            result.network_status = NetworkStatus.REVIEW
        elif result.network_status == NetworkStatus.UNKNOWN and verdict.status != NetworkStatus.UNKNOWN:
            result.network_status = verdict.status
```

with:

```python
    result.network_verdict = verdict
    result.network_status, result.corroboration = reconcile(result.network_status, verdict)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_reconcile.py tests/test_eligibility.py -v -m "not db and not live"`
Expected: PASS (new reconcile tests + existing eligibility tests unchanged in behavior)

- [ ] **Step 5: Commit**

```bash
git add src/network_probe/domain/eligibility.py tests/test_reconcile.py
git commit -m "refactor(eligibility): extract pure reconcile() from check_eligibility"
```

---

## Task 4: `check_eligibility` — direct Stedi id, auto-scope by plan, capture pre-merge status

**Files:**
- Modify: `src/network_probe/domain/benefits.py` (one more field)
- Modify: `src/network_probe/domain/eligibility.py` (`check_eligibility` signature + body)
- Test: `tests/test_eligibility.py`

**Interfaces:**
- Consumes: `reconcile` (Task 3), `EligibilityResult.selected_plan` (Task 2).
- Produces: `check_eligibility(..., stedi_payer_id: str | None = None)`; sets `result.stedi_network_status` (the 271-only status, pre-merge); when `q.plan_hint` is blank it is set to `result.selected_plan` before the directory leg.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_eligibility.py
from network_probe.domain.benefits import EligibilityResult
from network_probe.domain.models import NetworkStatus, ProviderQuery


class _FakeStedi:
    """Records the query it received and returns a canned result."""
    def __init__(self, result):
        self.result = result
        self.seen = None

    def check(self, q):
        self.seen = q
        return self.result


def _canned(selected="DEVOTED GIVEBACK 006 TX (HMO)"):
    return EligibilityResult(
        coverage_active=True, plan_name=selected, group=None, coverage_dates={},
        network_status=NetworkStatus.UNKNOWN, benefits=[], pcp_required=None,
        prior_auth_required=None, referral_required=None, cob=None, network_verdict=None,
        corroboration=[], source_audit={"source": "stedi-271"},
        plan_candidates=[{"plan": selected, "is_product": True, "rank": 0}], selected_plan=selected,
    )


def test_blank_plan_is_scoped_from_271(monkeypatch):
    import network_probe.domain.eligibility as elig
    monkeypatch.setattr(elig, "check_network", lambda q, **kw: (_ for _ in ()).throw(RuntimeError("no adapter")))
    fake = _FakeStedi(_canned())
    q = ProviderQuery(payer="devoted", plan_hint="", npi="1720209885")
    result = elig.check_eligibility(q, catalogue=_NoOpCatalogue(), stedi=fake)
    # the directory leg saw the plan the 271 returned, not a blank hint
    assert fake.seen.plan_hint == "DEVOTED GIVEBACK 006 TX (HMO)"
    assert result.stedi_network_status == NetworkStatus.UNKNOWN


class _NoOpCatalogue:
    def resolve(self, payer_key):
        return None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_eligibility.py::test_blank_plan_is_scoped_from_271 -v -m "not db and not live"`
Expected: FAIL — `AttributeError: 'EligibilityResult' object has no attribute 'stedi_network_status'`

- [ ] **Step 3: Write minimal implementation**

In `src/network_probe/domain/benefits.py`, add one more field at the end of `EligibilityResult` (after `selected_plan`):

```python
    selected_plan: str | None = None
    stedi_network_status: NetworkStatus | None = None
```

and in `to_dict()`:

```python
            "selected_plan": self.selected_plan,
            "stedi_network_status": self.stedi_network_status.value if self.stedi_network_status else None,
```

In `src/network_probe/domain/eligibility.py`, change `check_eligibility`'s signature to add `stedi_payer_id=None` and rework the top of the body:

```python
def check_eligibility(
    q: ProviderQuery,
    base_url: str | None = None,
    catalogue: PayerCatalogue | None = None,
    stedi: EligibilitySource | None = None,
    tenant_id=None,
    override_store=None,
    stedi_payer_id: str | None = None,
) -> EligibilityResult:
    cat = catalogue or DbPayerCatalogue()
    payer = cat.resolve(q.payer)
    effective_id = stedi_payer_id or (payer.stedi_payer_id if payer else None)
    source = stedi or StediEligibilityClient(payer_id=effective_id)
    result = source.check(q)
    # The 271 knows the member's real plan; scope the directory leg by it when the caller gave none.
    if not q.plan_hint and result.selected_plan:
        q.plan_hint = result.selected_plan
    result.stedi_network_status = result.network_status  # capture pre-merge (271-only) status
    kw: dict = {"catalogue": cat}
    if base_url:
        kw["base_url"] = base_url
    try:
        verdict = check_network(q, **kw)
    except Exception:
        verdict = None
    result.network_verdict = verdict
    result.network_status, result.corroboration = reconcile(result.network_status, verdict)
```

(Leave the override tail below unchanged.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_eligibility.py -v -m "not db and not live"`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/network_probe/domain/benefits.py src/network_probe/domain/eligibility.py tests/test_eligibility.py
git commit -m "feat(eligibility): auto-scope directory by 271 plan; accept direct stedi_payer_id"
```

---

## Task 5: Payer search module (pure/mockable)

**Files:**
- Create: `src/network_probe/payers/search.py`
- Test: `tests/test_payer_search.py`

**Interfaces:**
- Produces:
  - `search_roster(rows: list[dict], q: str, limit: int = 20) -> list[dict]` — rank roster dicts (keys: `label,key,benefit_type,state,stedi_payer_id,enrollment_status`) by exact→prefix→substring on label/key; returns option dicts `{value,label,market,benefit_type,stedi_payer_id,enrollment_status,source:"roster"}`.
  - `search_stedi(client, api_key: str, q: str, limit: int = 20) -> list[dict]` — map Stedi `items[]` to option dicts `{..., value:"stedi:<id>", source:"stedi"}`.
  - `load_roster_rows() -> list[dict]` — read global `payers` rows (tenant_id IS NULL) as dicts.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_payer_search.py
import httpx

from network_probe.core._http import CachedClient
from network_probe.payers.search import search_roster, search_stedi

ROWS = [
    {"label": "Aetna", "key": "aetna-az", "benefit_type": "Commercial", "state": "AZ",
     "stedi_payer_id": "60054", "enrollment_status": "needs_enrollment"},
    {"label": "Aetna Better Health", "key": "aetna-better-health-fl-south-florida",
     "benefit_type": "Managed Medicaid", "state": "FL-South Florida",
     "stedi_payer_id": "ABH01", "enrollment_status": "needs_enrollment"},
    {"label": "Oscar", "key": "oscar-az", "benefit_type": "ACA", "state": "AZ",
     "stedi_payer_id": "OSCAR", "enrollment_status": "supported"},
]


def test_roster_ranks_exact_then_prefix_then_substring():
    out = search_roster(ROWS, "aetna")
    assert out[0]["label"] == "Aetna" and out[0]["value"] == "aetna-az"  # exact before prefix
    assert out[0]["source"] == "roster" and out[0]["market"] == "AZ"
    assert {o["label"] for o in out} == {"Aetna", "Aetna Better Health"}


def test_roster_blank_query_returns_nothing():
    assert search_roster(ROWS, "   ") == []


def test_stedi_maps_items_with_prefix_value():
    payload = {"items": [
        {"primaryPayerId": "128KY", "displayName": "Aetna Better Health of Kentucky", "stediId": "AABKY"},
        {"stediId": "ONLYSTEDI", "conciseName": "Some Plan"},
        {"displayName": "No Id Plan"},  # dropped: no id
    ]}
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json=payload))
    client = CachedClient(cache_dir=None, delay_seconds=0, client=httpx.Client(transport=transport))
    out = search_stedi(client, "KEY", "aetna")
    assert out[0] == {
        "value": "stedi:128KY", "label": "Aetna Better Health of Kentucky", "market": None,
        "benefit_type": None, "stedi_payer_id": "128KY", "enrollment_status": None, "source": "stedi",
    }
    assert out[1]["value"] == "stedi:ONLYSTEDI"  # falls back to stediId
    assert len(out) == 2  # the id-less item is dropped
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_payer_search.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'network_probe.payers.search'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/network_probe/payers/search.py
"""Payer search for the UI select: curated roster first, Stedi live directory as fallback.

Pure functions (search_roster/search_stedi) so ranking + mapping are unit-tested without a DB or
network; load_roster_rows() is the thin DB read the endpoint uses.
"""

from __future__ import annotations

import os

from sqlalchemy import select

from network_probe.core._http import CachedClient
from network_probe.db.base import SessionLocal, app_engine
from network_probe.db.models import Payer

PAYERS_URL = os.environ.get("STEDI_PAYERS_URL", "https://healthcare.us.stedi.com/2024-04-01/payers")


def _option(*, value, label, market, benefit_type, stedi_payer_id, enrollment_status, source):
    return {
        "value": value, "label": label, "market": market, "benefit_type": benefit_type,
        "stedi_payer_id": stedi_payer_id, "enrollment_status": enrollment_status, "source": source,
    }


def search_roster(rows: list[dict], q: str, limit: int = 20) -> list[dict]:
    ql = (q or "").strip().lower()
    if not ql:
        return []
    scored: list[tuple[int, str, dict]] = []
    for r in rows:
        label = (r.get("label") or "")
        key = (r.get("key") or "")
        hay = label.lower()
        if ql not in hay and ql not in key.lower():
            continue
        rank = 0 if hay == ql else (1 if hay.startswith(ql) else 2)
        scored.append((rank, hay, r))
    scored.sort(key=lambda t: (t[0], t[1]))
    return [
        _option(
            value=r.get("key"), label=r.get("label"), market=r.get("state"),
            benefit_type=r.get("benefit_type"), stedi_payer_id=r.get("stedi_payer_id"),
            enrollment_status=r.get("enrollment_status"), source="roster",
        )
        for _, _, r in scored[:limit]
    ]


def search_stedi(client: CachedClient, api_key: str, q: str, limit: int = 20) -> list[dict]:
    try:
        data = client.get_json(f"{PAYERS_URL}?query={q}", headers={"Authorization": api_key})
    except Exception:
        return []
    out: list[dict] = []
    for it in (data.get("items") or []):
        pid = it.get("primaryPayerId") or it.get("stediId")
        if not pid:
            continue
        out.append(
            _option(
                value=f"stedi:{pid}", label=it.get("displayName") or it.get("conciseName") or "",
                market=None, benefit_type=None, stedi_payer_id=pid, enrollment_status=None, source="stedi",
            )
        )
        if len(out) >= limit:
            break
    return out


def load_roster_rows() -> list[dict]:
    with SessionLocal(bind=app_engine()) as s:
        payers = s.execute(select(Payer).where(Payer.tenant_id.is_(None))).scalars().all()
        return [
            {
                "label": p.label, "key": p.key, "benefit_type": p.benefit_type, "state": p.state,
                "stedi_payer_id": p.stedi_payer_id, "enrollment_status": p.enrollment_status,
            }
            for p in payers
        ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_payer_search.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/network_probe/payers/search.py tests/test_payer_search.py
git commit -m "feat(payers): add roster + Stedi-directory payer search functions"
```

---

## Task 6: `GET /api/payers/search` endpoint

**Files:**
- Modify: `src/network_probe/api/app.py`
- Test: `tests/test_api_eligibility.py`

**Interfaces:**
- Consumes: `search_roster`, `search_stedi`, `load_roster_rows` (Task 5).
- Produces: `GET /api/payers/search?q=<str>&limit=<int>` (auth required) → `list[option]`, roster first then de-duplicated Stedi results.

- [ ] **Step 1: Write the failing test** (append to `tests/test_api_eligibility.py`)

```python
@pytest.mark.db
def test_payers_search_roster_hits(auth_header):
    c = TestClient(app, raise_server_exceptions=False)
    r = c.get("/api/payers/search", params={"q": "aetna"}, headers=auth_header)
    assert r.status_code == 200
    body = r.json()
    assert body and any(o["label"] == "Aetna" for o in body)
    assert all(o["source"] in ("roster", "stedi") for o in body)


@pytest.mark.db
def test_payers_search_requires_auth():
    c = TestClient(app, raise_server_exceptions=False)
    assert c.get("/api/payers/search", params={"q": "aetna"}).status_code == 401
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_api_eligibility.py::test_payers_search_roster_hits -v -m db`
Expected: FAIL — 404 (route not defined) → assertion error on status_code

- [ ] **Step 3: Write minimal implementation**

In `src/network_probe/api/app.py`, add these imports near the existing ones:

```python
from network_probe.core._http import CachedClient
from network_probe.core.secrets_provider import get_secret
```

Add the route (place it after the existing `@app.get("/api/payers")`):

```python
@app.get("/api/payers/search")
def payers_search(q: str = "", limit: int = 20, ctx: RequestContext = Depends(get_context)) -> list[dict]:
    from network_probe.payers.search import load_roster_rows, search_roster, search_stedi

    roster = search_roster(load_roster_rows(), q, limit)
    if len(roster) >= limit:
        return roster
    api_key = get_settings().stedi_api_key or get_secret("STEDI_API_KEY")
    if not api_key:
        return roster
    seen = {o["stedi_payer_id"] for o in roster if o["stedi_payer_id"]}
    client = CachedClient(cache_dir=None, delay_seconds=0.3)
    extra = [o for o in search_stedi(client, api_key, q, limit) if o["stedi_payer_id"] not in seen]
    return roster + extra[: max(0, limit - len(roster))]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_api_eligibility.py -v -m db -k payers_search`
Expected: PASS (needs local `preauth_test` with seeded payers)

- [ ] **Step 5: Commit**

```bash
git add src/network_probe/api/app.py tests/test_api_eligibility.py
git commit -m "feat(api): add GET /api/payers/search (roster + Stedi fallback)"
```

---

## Task 7: `recheck_network()` + `POST /api/eligibility/recheck-network` + wire `stedi_payer_id`

**Files:**
- Modify: `src/network_probe/domain/eligibility.py` (`recheck_network`)
- Modify: `src/network_probe/api/app.py` (`CheckRequest.stedi_payer_id`, pass-through, new route)
- Test: `tests/test_eligibility.py`, `tests/test_api_eligibility.py`

**Interfaces:**
- Consumes: `reconcile` (Task 3), `check_network`, `DbOverrideStore`.
- Produces: `recheck_network(q, stedi_status: NetworkStatus, base_url=None, catalogue=None, tenant_id=None, override_store=None) -> dict` returning `{network_status, network_verdict, corroboration}`; `POST /api/eligibility/recheck-network`; `CheckRequest.stedi_payer_id` passed into `check_eligibility`.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_eligibility.py
def test_recheck_network_reconciles_new_plan(monkeypatch):
    import network_probe.domain.eligibility as elig
    from network_probe.domain.models import NetworkStatus, NetworkVerdict, ProviderQuery

    v = NetworkVerdict(status=NetworkStatus.OUT_OF_NETWORK, matched_provider=None,
                       plan_or_network_checked="P", source_url="http://x", confidence="high", notes="")
    monkeypatch.setattr(elig, "check_network", lambda q, **kw: v)
    q = ProviderQuery(payer="oscar", plan_hint="OTHER PLAN", npi="1679766943")
    out = elig.recheck_network(q, NetworkStatus.IN_NETWORK, catalogue=_NoOpCatalogue())
    # directory OUT vs 271 IN -> REVIEW
    assert out["network_status"] == "REVIEW"
    assert out["network_verdict"]["status"] == "OUT_OF_NETWORK"
```

```python
# add to tests/test_api_eligibility.py
@pytest.mark.db
def test_recheck_network_route(auth_header):
    c = TestClient(app, raise_server_exceptions=False)
    # 'mystery' has no adapter -> check_network raises -> verdict None -> keeps the given 271 status
    r = c.post("/api/eligibility/recheck-network",
               json={"payer": "mystery", "npi": "1679766943", "plan": "SILVER",
                     "stedi_network_status": "OUT_OF_NETWORK"},
               headers=auth_header)
    assert r.status_code == 200
    assert r.json()["network_status"] == "OUT_OF_NETWORK"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_eligibility.py::test_recheck_network_reconciles_new_plan -v -m "not db and not live"`
Expected: FAIL — `AttributeError: module ... has no attribute 'recheck_network'`

- [ ] **Step 3: Write minimal implementation**

In `src/network_probe/domain/eligibility.py`, add:

```python
def recheck_network(
    q: ProviderQuery,
    stedi_status: NetworkStatus,
    base_url: str | None = None,
    catalogue: PayerCatalogue | None = None,
    tenant_id=None,
    override_store=None,
) -> dict:
    """Re-run ONLY the directory leg for a newly chosen plan and re-merge against the prior 271
    status. No 270 is sent. Mirrors check_eligibility's merge + override tail."""
    cat = catalogue or DbPayerCatalogue()
    kw: dict = {"catalogue": cat}
    if base_url:
        kw["base_url"] = base_url
    try:
        verdict = check_network(q, **kw)
    except Exception:
        verdict = None
    status, corr = reconcile(stedi_status, verdict)
    store = override_store
    if store is None and tenant_id is not None:
        from network_probe.domain.overrides import DbOverrideStore

        store = DbOverrideStore(tenant_id)
    if store is not None:
        ov = store.lookup(q)
        if ov is not None:
            status = NetworkStatus(ov.status)
            corr = (corr or []) + [
                {"source": "override", "result": "authoritative",
                 "detail": f"{ov.status} confirmed by {ov.verified_by} ({ov.verified_at})"}
            ]
    return {
        "network_status": status.value,
        "network_verdict": verdict.to_dict() if verdict else None,
        "corroboration": corr,
    }
```

In `src/network_probe/api/app.py`:

Add `stedi_payer_id` to `CheckRequest` (after `member_id`/`dob`):

```python
    member_id: str | None = None
    dob: str | None = None
    stedi_payer_id: str | None = None
```

Pass it through in the `/api/eligibility` handler where it calls `check_eligibility`:

```python
    result = check_eligibility(
        q, base_url=(req.base_url or None), tenant_id=ctx.tenant_id,
        stedi_payer_id=(req.stedi_payer_id or None),
    )
```

Add a request model + route (near the other routes):

```python
class RecheckRequest(BaseModel):
    payer: str
    stedi_payer_id: str | None = None
    npi: str | None = None
    plan: str = ""
    state: str | None = None
    zip: str | None = None
    tin: str | None = None
    base_url: str | None = None
    stedi_network_status: str = "UNKNOWN"


@app.post("/api/eligibility/recheck-network")
def recheck_network_route(req: RecheckRequest, ctx: RequestContext = Depends(enforce_quota)):
    from network_probe.domain.eligibility import recheck_network

    if req.base_url:
        try:
            assert_safe_url(req.base_url)
        except ValueError as e:
            raise HTTPException(status_code=400, detail={"message": str(e)})
    if req.npi and not valid_npi(req.npi):
        raise HTTPException(status_code=400, detail={"message": "invalid NPI"})
    q = ProviderQuery(
        payer=req.payer, plan_hint=req.plan or "", npi=req.npi or None,
        state=req.state or None, zip_code=req.zip or None, tin=req.tin or None,
    )
    try:
        stedi_status = NetworkStatus(req.stedi_network_status)
    except ValueError:
        stedi_status = NetworkStatus.UNKNOWN
    return recheck_network(q, stedi_status, base_url=(req.base_url or None), tenant_id=ctx.tenant_id)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_eligibility.py -v -m "not db and not live"` then `pytest tests/test_api_eligibility.py -v -m db -k recheck`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/network_probe/domain/eligibility.py src/network_probe/api/app.py tests/test_eligibility.py tests/test_api_eligibility.py
git commit -m "feat(api): add network-only recheck route and stedi_payer_id passthrough"
```

---

## Task 8: Frontend — searchable payer select

**Files:**
- Create: `web/src/services/payers.ts`
- Modify: `web/src/pages/Eligibility.tsx`

**Interfaces:**
- Consumes: `GET /api/payers/search` (Task 6).
- Produces: `searchPayers(q): Promise<PayerOption[]>`; a payer `Select` whose selected option is held in state so `payer` + `stedi_payer_id` are sent on submit.

- [ ] **Step 1: Create the service**

```typescript
// web/src/services/payers.ts
import { apiFetch } from './auth';

export interface PayerOption {
  value: string;
  label: string;
  market: string | null;
  benefit_type: string | null;
  stedi_payer_id: string | null;
  enrollment_status: string | null;
  source: 'roster' | 'stedi';
}

export async function searchPayers(q: string): Promise<PayerOption[]> {
  const res = await apiFetch(`/payers/search?q=${encodeURIComponent(q)}`);
  if (!res.ok) return [];
  return (await res.json()) as PayerOption[];
}

export interface RecheckResult {
  network_status: string;
  network_verdict: Record<string, unknown> | null;
  corroboration: { source: string; result: string; detail: string }[] | null;
}

export async function recheckNetwork(body: {
  payer: string;
  stedi_payer_id?: string;
  npi?: string;
  plan: string;
  state?: string;
  zip?: string;
  tin?: string;
  stedi_network_status: string;
}): Promise<RecheckResult> {
  const res = await apiFetch('/eligibility/recheck-network', { method: 'POST', body: JSON.stringify(body) });
  if (!res.ok) throw new Error('Re-check failed');
  return (await res.json()) as RecheckResult;
}
```

- [ ] **Step 2: Replace the payer input with a searchable Select in `Eligibility.tsx`**

Add to imports:

```typescript
import { Form, Input, Button, Table, Card, Typography, Divider, Select, Tag } from 'antd';
import { searchPayers, recheckNetwork, type PayerOption } from '../services/payers';
```

Add state + a debounced search handler inside the `Eligibility` component (near the existing `useState`s):

```typescript
  const [payerOptions, setPayerOptions] = useState<PayerOption[]>([]);
  const [selectedPayer, setSelectedPayer] = useState<PayerOption | null>(null);
  const searchTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const onPayerSearch = (q: string) => {
    if (searchTimer.current) clearTimeout(searchTimer.current);
    if (!q.trim()) { setPayerOptions([]); return; }
    searchTimer.current = setTimeout(() => {
      searchPayers(q).then(setPayerOptions).catch(() => setPayerOptions([]));
    }, 250);
  };
```

Add `useRef` to the React import at the top of the file:

```typescript
import { useState, useRef } from 'react';
```

Replace the payer `Form.Item`/`Input` (the one labelled "Payer ID") with:

```tsx
              <Form.Item name="payer" label="Payer" rules={[{ required: true, message: 'Payer is required' }]}>
                <Select
                  showSearch
                  filterOption={false}
                  placeholder="Search payers (e.g. Aetna, UnitedHealthcare)"
                  onSearch={onPayerSearch}
                  onChange={(value) =>
                    setSelectedPayer(payerOptions.find((o) => o.value === value) ?? null)
                  }
                  notFoundContent={null}
                  options={payerOptions.map((o) => ({
                    value: o.value,
                    label: (
                      <span>
                        {o.label}
                        {o.market ? <span style={{ color: palette.slate400 }}> · {o.market}</span> : null}
                        {o.enrollment_status === 'supported' ? (
                          <Tag color="green" style={{ marginLeft: 6 }}>supported</Tag>
                        ) : o.enrollment_status === 'needs_enrollment' ? (
                          <Tag color="gold" style={{ marginLeft: 6 }}>needs enrollment</Tag>
                        ) : o.source === 'stedi' ? (
                          <Tag style={{ marginLeft: 6 }}>Stedi</Tag>
                        ) : null}
                      </span>
                    ),
                  }))}
                />
              </Form.Item>
```

In `handleSubmit`, build the request so a Stedi-sourced payer also sends its id:

```typescript
  const handleSubmit = async (values: EligibilityRequest) => {
    setLoading(true);
    setResult(null);
    const payload = {
      ...values,
      stedi_payer_id: selectedPayer?.source === 'stedi' ? selectedPayer.stedi_payer_id ?? undefined : undefined,
    };
    try {
      const res = await apiFetch('/eligibility', { method: 'POST', body: JSON.stringify(payload) });
      // ...rest unchanged
```

- [ ] **Step 3: Typecheck / build**

Run: `cd web && npm run build`
Expected: build succeeds (no TS errors). Then `npx oxlint` — no new errors.

- [ ] **Step 4: Commit**

```bash
git add web/src/services/payers.ts web/src/pages/Eligibility.tsx
git commit -m "feat(web): searchable payer select backed by /api/payers/search"
```

---

## Task 9: Frontend — plan control from the 271 + network re-check

**Files:**
- Modify: `web/src/pages/Eligibility.tsx`

**Interfaces:**
- Consumes: `result.plan_candidates`, `result.selected_plan`, `result.stedi_network_status` (Tasks 2/4); `recheckNetwork` (Task 8).
- Produces: after a check, a plan `Select` (seeded from the 271, editable) whose change re-runs the network leg and updates the verdict in place.

- [ ] **Step 1: Extend the response type + drop the required up-front plan field**

In the `EligibilityResponse` interface, add:

```typescript
  plan_candidates: { plan: string; is_product: boolean; rank: number }[];
  selected_plan: string | null;
  stedi_network_status: string | null;
```

Remove the up-front **Plan** `Form.Item` (the one under "Plan & Location" with `name="plan"` and `required`). Leave State/ZIP. The 270/271 does not need a plan; it now comes from the 271.

- [ ] **Step 2: Add a plan-control card rendered after `result`**

Add state near the other `useState`s:

```typescript
  const [rechecking, setRechecking] = useState(false);
```

Insert this block just above the "Cost-Share Matrix" card (inside `{result && (...)}`):

```tsx
          {result.plan_candidates?.length > 0 && (
            <Card style={{ marginBottom: 16 }} styles={{ body: { padding: '14px 18px' } }}>
              <div style={styles.cardHeaderTitle}>Plan used for network check</div>
              <div style={{ marginTop: 8, maxWidth: 460 }}>
                <Select
                  style={{ width: '100%' }}
                  value={result.selected_plan ?? undefined}
                  loading={rechecking}
                  options={result.plan_candidates.map((c) => ({
                    value: c.plan,
                    label: c.is_product ? c.plan : `${c.plan} (segment)`,
                  }))}
                  onChange={async (plan) => {
                    setRechecking(true);
                    try {
                      const upd = await recheckNetwork({
                        payer: form.getFieldValue('payer'),
                        stedi_payer_id:
                          selectedPayer?.source === 'stedi' ? selectedPayer.stedi_payer_id ?? undefined : undefined,
                        npi: form.getFieldValue('npi'),
                        plan,
                        state: form.getFieldValue('state'),
                        zip: form.getFieldValue('zip'),
                        tin: form.getFieldValue('tin'),
                        stedi_network_status: result.stedi_network_status ?? 'UNKNOWN',
                      });
                      setResult({
                        ...result,
                        selected_plan: plan,
                        network_status: upd.network_status as EligibilityResponse['network_status'],
                        network_verdict: upd.network_verdict as unknown as NetworkVerdict | null,
                        corroboration: upd.corroboration,
                      });
                    } catch {
                      toast.error('Network re-check failed');
                    } finally {
                      setRechecking(false);
                    }
                  }}
                />
                <Text type="secondary" style={{ fontSize: 12, display: 'block', marginTop: 6 }}>
                  Derived from the payer's 271. Change it to re-check the network for another of this
                  member's coverages.
                </Text>
              </div>
            </Card>
          )}
```

- [ ] **Step 3: Typecheck / build**

Run: `cd web && npm run build`
Expected: build succeeds. `npx oxlint` — no new errors.

- [ ] **Step 4: Manual end-to-end verification**

Start API (`uvicorn network_probe.api:app`) + web (`cd web && npm run dev`), log in, then:
1. In **Payer**, type `Devoted` → the select shows roster options with badges; pick **Devoted Health**.
2. Enter NPI `1720209885`, member id/DOB/name from the demo, submit.
3. Expect: coverage ACTIVE; the **Plan used for network check** card shows `DEVOTED GIVEBACK 006 TX (HMO)` selected, with `03 - SLMB ONLY (PARTIAL DUAL)` also selectable.
4. Switch the plan → the network verdict banner + status tile update without a full re-submit.

- [ ] **Step 5: Commit**

```bash
git add web/src/pages/Eligibility.tsx
git commit -m "feat(web): plan-from-271 control with in-place network re-check"
```

---

## Final verification

- [ ] Run the fast suite: `pytest -m "not live and not db"` → all green.
- [ ] Run the DB suite (needs `preauth_test`): `pytest -m db` → all green.
- [ ] `cd web && npm run build` → succeeds.

## Self-Review

**Spec coverage:**
- Searchable payer select (roster + Stedi fallback) → Tasks 5, 6, 8. ✓
- Stedi-only payer wiring (`stedi_payer_id`) → Tasks 4, 7 (request field + passthrough), 8 (client sends it). ✓
- 271-first, plan auto-derived, directory auto-scoped → Tasks 1, 2, 4. ✓
- `plan_candidates`/`selected_plan` on result + parse fix (`planCoverage`) → Tasks 1, 2. ✓
- Extract `reconcile()` → Task 3. ✓
- Network-only re-check route → Task 7 (backend), 9 (UI). ✓
- UI rework (payer select, plan control, re-check) → Tasks 8, 9. ✓
- Guardrails (junk→None, dual-eligible ranking, REVIEW/UNKNOWN invariants) → Tasks 1 (junk/ranking), 3 (reconcile). ✓
- Deferred full plan-catalog typeahead → not built (per spec Scope). ✓

**Placeholder scan:** No TBD/TODO; every code step shows real code and exact test commands. ✓

**Type consistency:** `derive_plan_candidates -> (list[dict], str|None)` used consistently in Tasks 1/2; `reconcile(NetworkStatus, NetworkVerdict|None) -> (NetworkStatus, list)` in Tasks 3/4/7; option dict shape `{value,label,market,benefit_type,stedi_payer_id,enrollment_status,source}` identical across Tasks 5/6/8; `EligibilityResult` gains `plan_candidates` (T2), `selected_plan` (T2), `stedi_network_status` (T4), all defaulted so prior constructors hold. `PayerOption` TS interface matches the backend option dict. ✓
