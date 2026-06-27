# Evidence-by-source UI + accuracy view — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Determination panel show each evidence source (271 intake / payer directory / Stedi / TIN-scope) as its own lane, add a "real vs what we gave (how we caught it)" ground-truth banner + benchmark scorecard, and correct the Rodriguez case from a missed `IN_NETWORK` to a caught `OUT_OF_NETWORK` via a golden-record override.

**Architecture:** Keep verdict logic where it is. Add an additive `evidence` block (raw pre-finalize payer-directory snapshot + the per-source signals) onto `NetworkVerdict`, surfaced through `/api/check`. The API also returns `ground_truth` for known cases and exposes `GET /api/benchmark`. A seeded override fixes Rodriguez. The UI renders four source-lane cards, a ground-truth banner, and a scorecard.

**Tech Stack:** Python 3.12, FastAPI, pytest, httpx (`MockTransport` for offline tests), vanilla HTML/CSS/JS (single self-contained `static/index.html`).

## Global Constraints

- **Additive only:** existing `/api/check` response fields and `NetworkVerdict.to_dict()` keys stay unchanged; new keys are added. The 47 existing tests must still pass.
- **Stedi lane badge is `LIVE`** in the UI (per product decision) even though it is backed by a canned 271 fixture when `STEDI_API_KEY` is unset. Real API is used when the key is set.
- **TIN-scope badge is dynamic:** `LIVE` when the TIN signal is `corroborates`/`contradicts`; otherwise `NEEDS INTEGRATION` with a reason. Sub-text: "needs NPI→TIN crosswalk / Availity TIN portal".
- **Rodriguez label is honest:** "caught via golden-record override (Availity)", not "automatic". The directory's stale `IN` stays visible in the payer-directory lane.
- **No live re-run** of payer directories for the scorecard — it is seeded from documented results.
- **Stedi fixture must be correct:** Rodriguez (NPI `1629339312`) → only OON benefits → `contradicts`; all other NPIs → `inconclusive` ("no provider-specific in-network signal").
- Run tests with: `.venv/bin/pytest -q` (offline tests; `-m live` is excluded by default).

---

### Task 1: Add `evidence` field to `NetworkVerdict`

**Files:**
- Modify: `network_probe/models.py`
- Test: `tests/test_models_evidence.py` (create)

**Interfaces:**
- Produces: `NetworkVerdict(..., evidence: Optional[dict] = None)`; `to_dict()` includes `"evidence"` key.

- [ ] **Step 1: Write the failing test**

Create `tests/test_models_evidence.py`:

```python
"""NetworkVerdict carries an optional additive `evidence` block."""
from network_probe.models import NetworkStatus, NetworkVerdict


def _v(**kw):
    base = dict(status=NetworkStatus.IN_NETWORK, matched_provider={"npi": "1"},
                plan_or_network_checked="x", source_url="u", confidence="high", notes="n")
    base.update(kw)
    return NetworkVerdict(**base)


def test_evidence_defaults_to_none_and_serializes():
    v = _v()
    assert v.evidence is None
    assert "evidence" in v.to_dict() and v.to_dict()["evidence"] is None


def test_evidence_roundtrips_in_to_dict():
    v = _v(evidence={"payer_directory": {"status": "IN_NETWORK"}, "signals": []})
    d = v.to_dict()
    assert d["evidence"]["payer_directory"]["status"] == "IN_NETWORK"
    assert d["evidence"]["signals"] == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_models_evidence.py -v`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'evidence'`.

- [ ] **Step 3: Add the field and serialize it**

In `network_probe/models.py`, in the `NetworkVerdict` dataclass, add the field after `corroboration`:

```python
    corroboration: Optional[list] = None  # cross-source signals [{source, result, detail}]
    evidence: Optional[dict] = None  # additive: {payer_directory: {...}, signals: [...]}
```

And in `to_dict`, add the key (after `"corroboration"`):

```python
            "corroboration": self.corroboration,
            "evidence": self.evidence,
        }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_models_evidence.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add network_probe/models.py tests/test_models_evidence.py
git commit -m "feat(models): add additive evidence block to NetworkVerdict"
```

---

### Task 2: Corroboration — extract signal runner, Stedi fixture source, always-on Stedi

**Files:**
- Modify: `network_probe/corroboration.py`
- Test: `tests/test_corroboration.py` (append cases)

**Interfaces:**
- Consumes: `Signal`, `default_sources`, `finalize` (existing).
- Produces:
  - `run_display_signals(verdict, q, sources) -> list[Signal]`
  - `finalize(verdict, q, sources=None, override_store=None, signals=None)` — new optional `signals` param (when provided, used as-is; when `None`, computed as before).
  - `StediMockSource` with `.name == "Stedi"`; returns `contradicts` for NPI `1629339312`, else `inconclusive`.
  - `default_sources(client=None)` always includes a Stedi source: real `StediSource` if `STEDI_API_KEY` set, else `StediMockSource`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_corroboration.py`:

```python
# ---- Stedi fixture / mock source -------------------------------------------

from network_probe.corroboration import (StediMockSource, run_display_signals,  # noqa: E402
                                          default_sources)


def test_stedi_mock_contradicts_for_rodriguez():
    s = StediMockSource().check(_q(npi="1629339312"), _verdict(NetworkStatus.IN_NETWORK))
    assert s.source == "Stedi" and s.result == "contradicts"


def test_stedi_mock_inconclusive_for_unknown_npi():
    s = StediMockSource().check(_q(npi="9999999999"), _verdict(NetworkStatus.IN_NETWORK))
    assert s.source == "Stedi" and s.result == "inconclusive"


def test_default_sources_always_includes_stedi(monkeypatch):
    monkeypatch.delenv("STEDI_API_KEY", raising=False)
    names = {getattr(s, "name", "") for s in default_sources()}
    assert "Stedi" in names  # mock stands in when no key


def test_run_display_signals_collects_from_each_source():
    sigs = run_display_signals(_verdict(NetworkStatus.IN_NETWORK), _q(),
                               [_FakeSource(Signal("FAKE", "corroborates", "ok")), StediMockSource()])
    results = {s.source: s.result for s in sigs}
    assert results["FAKE"] == "corroborates" and results["Stedi"] == "contradicts"


def test_finalize_accepts_precomputed_signals_without_rerunning():
    # passing signals= must not call source.check again (would raise here)
    class _Boom:
        name = "BOOM"
        def check(self, q, v):
            raise AssertionError("should not be called")
    pre = [Signal("FAKE", "corroborates", "ok")]
    v = finalize(_verdict(NetworkStatus.IN_NETWORK, "high"), _q(),
                 sources=[_Boom()], override_store=_NO, signals=pre)
    assert v.confidence == "medium" and v.corroboration[0]["source"] == "FAKE"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_corroboration.py -k "stedi_mock or default_sources_always or run_display or precomputed" -v`
Expected: FAIL — `ImportError: cannot import name 'StediMockSource'` (and friends).

- [ ] **Step 3: Implement in `network_probe/corroboration.py`**

Add the fixture + mock source (place after the `StediSource` class, before `default_sources`):

```python
# Canned-but-correct 271 fixtures keyed by NPI. The interpretation path is the real
# StediSource._interpret, so only the payload is mocked. Most payers don't return a
# provider-specific network indicator in a 271, so unknown NPIs are honestly inconclusive.
_STEDI_FIXTURE_271: dict[str, dict] = {
    # Dr Jing Li — Devoted CO PPO: the payer's 271 returns out-of-network benefits,
    # independently agreeing with the Availity-confirmed OON truth (directory says IN, stale).
    "1629339312": {"benefitsInformation": [{"inPlanNetworkIndicatorCode": "N"}]},
}


class StediMockSource:
    """Fixture-backed Stedi 270/271 cross-check used when no STEDI_API_KEY is configured.

    Presented in the UI with a LIVE badge (product decision); the payload is canned but flows
    through the real StediSource._interpret, so the verdict semantics are genuine.
    """
    name = "Stedi"

    def check(self, q: ProviderQuery, verdict: NetworkVerdict) -> Optional[Signal]:
        data = _STEDI_FIXTURE_271.get(q.npi or "")
        if data is None:
            return Signal(self.name, "inconclusive",
                          "271 carried no provider-specific in-network signal (payer-dependent).")
        return StediSource._interpret(data)
```

Replace `default_sources` with the always-on-Stedi version:

```python
def default_sources(client: Optional[CachedClient] = None) -> list:
    sources: list = [NppesSource(client), TinScopeSource(), FreshnessSource()]
    if os.environ.get("STEDI_API_KEY"):
        sources.append(StediSource(client=client))   # real clearinghouse
    else:
        sources.append(StediMockSource())            # fixture stand-in (LIVE badge in UI)
    return sources
```

Extract the signal runner and thread the optional `signals` param into `finalize`. Add this helper above `finalize`:

```python
def run_display_signals(verdict: NetworkVerdict, q: ProviderQuery, sources: list) -> list[Signal]:
    """Run each source defensively; a source that errors degrades to an inconclusive signal."""
    out: list[Signal] = []
    for src in sources:
        try:
            s = src.check(q, verdict)
        except Exception:
            s = Signal(getattr(src, "name", "source"), "inconclusive", "source error")
        if s:
            out.append(s)
    return out
```

In `finalize`, change the signature and replace the inline collection loop. The signature becomes:

```python
def finalize(verdict: NetworkVerdict, q: ProviderQuery, sources: Optional[list] = None,
             override_store=None, signals: Optional[list] = None) -> NetworkVerdict:
```

Keep the override early-return exactly as-is (it stays before any source work). Then replace:

```python
    sources = default_sources() if sources is None else sources
    signals: list[Signal] = []
    for src in sources:
        try:
            s = src.check(q, verdict)
        except Exception:
            s = Signal(getattr(src, "name", "source"), "inconclusive", "source error")
        if s:
            signals.append(s)
    verdict.corroboration = [s.as_dict() for s in signals]
```

with:

```python
    sources = default_sources() if sources is None else sources
    if signals is None:
        signals = run_display_signals(verdict, q, sources)
    verdict.corroboration = [s.as_dict() for s in signals]
```

(The rest of `finalize` — `contradictions`/`stale`/asymmetry — is unchanged and keeps using `signals`.)

- [ ] **Step 4: Run the new tests, then the full corroboration suite**

Run: `.venv/bin/pytest tests/test_corroboration.py -v`
Expected: PASS — new cases pass and all pre-existing corroboration tests still pass (override early-return and explicit-`sources` paths are unchanged).

- [ ] **Step 5: Commit**

```bash
git add network_probe/corroboration.py tests/test_corroboration.py
git commit -m "feat(corroboration): Stedi fixture source + run_display_signals + precomputed signals"
```

---

### Task 3: Service — snapshot raw payer verdict and attach evidence

**Files:**
- Modify: `network_probe/service.py`
- Test: `tests/test_service_evidence.py` (create)

**Interfaces:**
- Consumes: `run_display_signals`, `finalize`, `default_sources` (Task 2); `NetworkVerdict.evidence` (Task 1).
- Produces: `check_network(q, corroborate=True, **adapter_kwargs)` returns a verdict whose `.evidence == {"payer_directory": {...}, "signals": [ {source,result,detail}, ... ]}`. The `payer_directory` snapshot is the adapter's verdict **before** finalize/override.

- [ ] **Step 1: Write the failing test**

Create `tests/test_service_evidence.py`:

```python
"""service.check_network attaches an evidence block: the raw payer-directory snapshot
(pre-finalize) plus the per-source display signals."""
from __future__ import annotations

from network_probe import service as svc
from network_probe.corroboration import Signal
from network_probe.models import NetworkStatus, NetworkVerdict, ProviderQuery


class _FakeAdapter:
    client = None
    def check_network(self, q):
        return NetworkVerdict(status=NetworkStatus.IN_NETWORK,
                              matched_provider={"npi": q.npi, "name": "Kyle A Herron"},
                              plan_or_network_checked="oscar / net 066", source_url="http://dir",
                              confidence="high", notes="found in directory.")


class _FakeStedi:
    name = "Stedi"
    def check(self, q, v):
        return Signal("Stedi", "inconclusive", "no provider-specific signal")


def _patch(monkeypatch):
    monkeypatch.setattr(svc, "get_adapter", lambda payer, **kw: _FakeAdapter())
    # keep it offline: replace the source set used inside check_network
    monkeypatch.setattr("network_probe.corroboration.default_sources", lambda client=None: [_FakeStedi()])


def test_evidence_has_raw_directory_snapshot(monkeypatch):
    _patch(monkeypatch)
    q = ProviderQuery(payer="oscar", plan_hint="x", npi="1679766943", last_name="Herron")
    v = svc.check_network(q)
    assert v.evidence["payer_directory"]["status"] == "IN_NETWORK"
    assert v.evidence["payer_directory"]["matched_provider"]["npi"] == "1679766943"


def test_evidence_has_signals(monkeypatch):
    _patch(monkeypatch)
    q = ProviderQuery(payer="oscar", plan_hint="x", npi="1679766943", last_name="Herron")
    v = svc.check_network(q)
    sources = {s["source"] for s in v.evidence["signals"]}
    assert "Stedi" in sources
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_service_evidence.py -v`
Expected: FAIL — `TypeError: 'NoneType' object is not subscriptable` (`v.evidence` is `None`).

- [ ] **Step 3: Implement in `network_probe/service.py`**

Replace the body of `check_network` with:

```python
def check_network(q: ProviderQuery, corroborate: bool = True, **adapter_kwargs) -> NetworkVerdict:
    adapter = get_adapter(q.payer, **adapter_kwargs)
    raw = adapter.check_network(q)
    snapshot = {
        "status": raw.status.value,
        "confidence": raw.confidence,
        "matched_provider": raw.matched_provider,
        "plan_or_network_checked": raw.plan_or_network_checked,
        "source_url": raw.source_url,
        "notes": raw.notes,
    }
    final = raw
    signals: list = []
    if corroborate:
        from .corroboration import finalize, default_sources, run_display_signals
        client = getattr(adapter, "client", None)
        sources = default_sources(client)
        # signals are computed once against the raw directory verdict so they are available for
        # display even when an override decides the final verdict; finalize reuses them.
        sig_objs = run_display_signals(raw, q, sources)
        signals = [s.as_dict() for s in sig_objs]
        final = finalize(raw, q, sources, signals=sig_objs)
    final.evidence = {"payer_directory": snapshot, "signals": signals}
    return final
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_service_evidence.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add network_probe/service.py tests/test_service_evidence.py
git commit -m "feat(service): attach raw payer-directory snapshot + signals as evidence"
```

---

### Task 4: API — ground-truth map and benchmark endpoint

**Files:**
- Modify: `network_probe/api.py`
- Test: `tests/test_api.py` (append cases)

**Interfaces:**
- Consumes: `check_network` returning a verdict with `evidence` (Task 3).
- Produces:
  - `GROUND_TRUTH: dict[tuple[str, str], dict]` keyed by `(payer, npi)` → `{truth, source, note}`.
  - `/api/check` response additionally includes `"ground_truth"` (the matched entry or `None`).
  - `GET /api/benchmark` → `list[dict]` with keys `case, truth, our_status, our_confidence, caught, how`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_api.py`:

```python
def test_check_includes_ground_truth(monkeypatch):
    fake = NetworkVerdict(status=NetworkStatus.OUT_OF_NETWORK, matched_provider=None,
                          plan_or_network_checked="devoted PPO", source_url="u",
                          confidence="high", notes="override")
    monkeypatch.setattr(api_mod, "check_network", lambda q, **kw: fake)
    r = client.post("/api/check", json={"payer": "devoted", "plan": "PPO", "npi": "1629339312"})
    assert r.status_code == 200
    gt = r.json()["ground_truth"]
    assert gt and gt["truth"] == "OUT_OF_NETWORK"


def test_check_ground_truth_none_for_unknown(monkeypatch):
    fake = NetworkVerdict(status=NetworkStatus.IN_NETWORK, matched_provider=None,
                          plan_or_network_checked="x", source_url="u", confidence="high", notes="n")
    monkeypatch.setattr(api_mod, "check_network", lambda q, **kw: fake)
    r = client.post("/api/check", json={"payer": "oscar", "plan": "x", "npi": "0000000000"})
    assert r.json()["ground_truth"] is None


def test_benchmark_lists_four_cases_all_caught():
    r = client.get("/api/benchmark")
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 4
    assert all(row["caught"] for row in rows)
    rod = next(row for row in rows if "Rodriguez" in row["case"])
    assert rod["our_status"] == "OUT_OF_NETWORK" and "override" in rod["how"].lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_api.py -k "ground_truth or benchmark" -v`
Expected: FAIL — `KeyError: 'ground_truth'` and `404` for `/api/benchmark`.

- [ ] **Step 3: Implement in `network_probe/api.py`**

Add the constants after the `SAMPLES` list:

```python
# Independently-confirmed truth (Availity / payer portal / phone) for the demo cases, keyed by
# (payer, npi). Surfaced as `ground_truth` so the UI can show "real vs what we gave".
GROUND_TRUTH: dict[tuple[str, str], dict] = {
    ("oscar", "1679766943"): {"truth": "OUT_OF_NETWORK", "source": "Availity / payer portal",
                              "note": "Absent from Oscar network 066."},
    ("devoted", "1629339312"): {"truth": "OUT_OF_NETWORK", "source": "Availity / payer portal",
                                "note": "Devoted directory lists Dr Li as IN for CO PPO — stale."},
    ("humana-fhir", "1336160274"): {"truth": "OUT_OF_NETWORK", "source": "Availity / payer portal",
                                    "note": "Not in the queried Medicare PPO network."},
    ("cigna-fhir", "1184610453"): {"truth": "OUT_OF_NETWORK", "source": "Cigna portal (TIN-level)",
                                   "note": "Out-of-network for this patient's TIN."},
}

# Seeded accuracy scorecard for the 4 pVerify OON examples (see TODO-network-accuracy.md).
# Not a live re-run — documented results, with Rodriguez corrected by the golden-record override.
BENCHMARK = [
    {"case": "Ochoa · Oscar · Herron", "truth": "OUT_OF_NETWORK",
     "our_status": "OUT_OF_NETWORK", "our_confidence": "high", "caught": True,
     "how": "directory absence (primary signal)"},
    {"case": "Benschneider · Cigna · Kiang", "truth": "OUT_OF_NETWORK",
     "our_status": "OUT_OF_NETWORK", "our_confidence": "medium", "caught": True,
     "how": "directory absence (primary signal)"},
    {"case": "Franz · Humana · Friedman", "truth": "OUT_OF_NETWORK",
     "our_status": "OUT_OF_NETWORK", "our_confidence": "medium", "caught": True,
     "how": "directory absence (primary signal)"},
    {"case": "Rodriguez · Devoted CO PPO · Li", "truth": "OUT_OF_NETWORK",
     "our_status": "OUT_OF_NETWORK", "our_confidence": "high", "caught": True,
     "how": "golden-record override (Availity); directory still lists Li as IN — stale"},
]
```

Add the benchmark route (next to the other routes, e.g. after `samples`):

```python
@app.get("/api/benchmark")
def benchmark() -> list[dict]:
    return BENCHMARK
```

In the `check` route, attach ground truth to the returned dict. Replace the final `return`:

```python
    gt = GROUND_TRUTH.get((req.payer, req.npi or ""))
    return {"payer": req.payer, "ground_truth": gt, **verdict.to_dict()}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_api.py -v`
Expected: PASS — new cases pass; pre-existing API tests still pass (response is a superset).

- [ ] **Step 5: Commit**

```bash
git add network_probe/api.py tests/test_api.py
git commit -m "feat(api): ground_truth on /api/check + /api/benchmark scorecard endpoint"
```

---

### Task 5: Seed the Rodriguez golden-record override

**Files:**
- Create: `.overrides/overrides.json`
- Test: `tests/test_override_seed.py` (create)

**Interfaces:**
- Consumes: `OverrideStore` (existing), `finalize` (existing), default override path `.overrides/overrides.json`.
- Produces: a persisted override so `check_network` for `(devoted, 1629339312, PPO)` resolves to `OUT_OF_NETWORK (high)`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_override_seed.py`:

```python
"""The seeded golden-record override corrects Rodriguez (Devoted CO PPO · Dr Li) to OON."""
from pathlib import Path

from network_probe.corroboration import finalize
from network_probe.models import NetworkStatus, NetworkVerdict, ProviderQuery
from network_probe.overrides import OverrideStore

SEED = Path(".overrides/overrides.json")


def _devoted_in_verdict():
    return NetworkVerdict(status=NetworkStatus.IN_NETWORK,
                          matched_provider={"npi": "1629339312", "name": "Jing Li, MD"},
                          plan_or_network_checked="devoted CO PPO", source_url="http://dir",
                          confidence="high", notes="listed in directory.")


def test_seed_file_exists_and_has_rodriguez():
    assert SEED.exists(), "seed override file missing"
    store = OverrideStore(path=SEED)
    q = ProviderQuery(payer="devoted", plan_hint="PPO", npi="1629339312", last_name="Li")
    assert store.lookup(q) is not None


def test_seed_override_flips_rodriguez_to_oon():
    store = OverrideStore(path=SEED)
    q = ProviderQuery(payer="devoted", plan_hint="PPO", npi="1629339312", last_name="Li")
    out = finalize(_devoted_in_verdict(), q, override_store=store)
    assert out.status == NetworkStatus.OUT_OF_NETWORK and out.confidence == "high"
    assert "availity" in out.notes.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_override_seed.py -v`
Expected: FAIL — `assert SEED.exists()` is False.

- [ ] **Step 3: Create `.overrides/overrides.json`**

```json
[
  {
    "payer": "devoted",
    "npi": "1629339312",
    "status": "OUT_OF_NETWORK",
    "verified_by": "Availity",
    "verified_at": "2026-06-01",
    "network": null,
    "plan": "PPO",
    "tin": null,
    "note": "Directory lists Dr Jing Li as IN for CO PPO; Availity + payer portal confirm OON. Stale directory entry."
  }
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_override_seed.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Confirm `.overrides/` is not gitignored away, then commit**

Run: `git check-ignore .overrides/overrides.json || echo "tracked"`
Expected: prints `tracked` (file is committable). If it prints the path, add a negation to `.gitignore`: `!.overrides/overrides.json`.

```bash
git add -f .overrides/overrides.json tests/test_override_seed.py
git commit -m "feat(overrides): seed Availity golden-record override correcting Rodriguez to OON"
```

---

### Task 6: Frontend — source-lane grid, ground-truth banner, benchmark scorecard

**Files:**
- Modify: `network_probe/static/index.html`
- Test: `tests/test_api.py` (append one smoke case)

**Interfaces:**
- Consumes: `/api/check` response with `evidence` + `ground_truth` (Tasks 3–4); `GET /api/benchmark` (Task 4).
- Produces: rendered DOM. Verified by a smoke test (markers present in served HTML) plus manual check.

- [ ] **Step 1: Write the failing smoke test**

Append to `tests/test_api.py`:

```python
def test_index_has_evidence_and_scorecard_markers():
    r = client.get("/")
    assert r.status_code == 200
    # the new UI building blocks are present in the served page
    assert "Evidence by source" in r.text
    assert 'id="scorecard"' in r.text
    assert "renderLanes" in r.text and "groundTruth" in r.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_api.py::test_index_has_evidence_and_scorecard_markers -v`
Expected: FAIL — markers absent.

- [ ] **Step 3: Add CSS** (in `network_probe/static/index.html`, before the closing `</style>` on line ~119)

```css
  /* evidence lanes */
  .lanes{display:grid;grid-template-columns:1fr 1fr;gap:11px;margin-top:18px}
  @media(max-width:520px){.lanes{grid-template-columns:1fr}}
  .lane{border:1px solid var(--line);border-radius:10px;padding:12px 13px;background:#fbfcfe}
  .lane h4{margin:0 0 6px;font-size:10.5px;font-weight:600;text-transform:uppercase;
           letter-spacing:.07em;color:var(--soft);display:flex;align-items:center;justify-content:space-between;gap:8px}
  .lane .badge{font-family:var(--mono);font-size:9.5px;font-weight:600;letter-spacing:.04em;
               padding:2px 6px;border-radius:5px;white-space:nowrap}
  .badge.live{background:var(--in-wash);color:var(--in);border:1px solid #bfe3cd}
  .badge.needs{background:var(--unk-wash);color:var(--unk);border:1px solid #eed9b3}
  .lane .ld{font-size:12.5px;color:var(--ink);line-height:1.4}
  .lane .ld .res{font-family:var(--mono);font-size:10.5px;text-transform:uppercase;letter-spacing:.04em;
                 padding:1px 5px;border-radius:4px;margin-right:5px}
  .res.corroborates{background:var(--in-wash);color:var(--in)}
  .res.contradicts{background:var(--oon-wash);color:var(--oon)}
  .res.inconclusive{background:#eef1f5;color:var(--muted)}
  .lane .sub{margin-top:5px;font-size:11px;color:var(--soft);font-family:var(--mono)}
  /* ground-truth banner */
  .gt{margin-top:18px;border:1px solid var(--line-strong);border-radius:10px;padding:13px 15px;background:#fff}
  .gt.caught{border-color:#bfe3cd;background:var(--in-wash)}
  .gt.missed{border-color:#f3c9c4;background:var(--oon-wash)}
  .gt .row{display:flex;gap:10px;font-size:13px;margin:2px 0}
  .gt .row b{font-family:var(--mono);font-size:11px;text-transform:uppercase;letter-spacing:.05em;
             color:var(--muted);min-width:54px}
  .gt .how{margin-top:6px;font-size:12px;color:var(--ink)}
  /* scorecard */
  #scorecard .panel-b{padding:0}
  .sc{width:100%;border-collapse:collapse;font-size:12.5px}
  .sc th,.sc td{text-align:left;padding:9px 14px;border-bottom:1px solid var(--line)}
  .sc th{font-size:10px;text-transform:uppercase;letter-spacing:.07em;color:var(--soft);font-weight:600}
  .sc td.st{font-family:var(--mono);font-size:11.5px}
  .sc .ok{color:var(--in);font-weight:600} .sc .miss{color:var(--oon);font-weight:600}
  .sc .how{color:var(--muted);font-size:11.5px}
  .sctag{font-size:11px;color:var(--muted);font-family:var(--mono)}
```

- [ ] **Step 4: Add the scorecard panel** — in `index.html`, immediately after the closing `</section>` of the Determination panel (right before the `</div>` that closes `.shell`, line ~183), insert:

```html
  <!-- SCORECARD -->
  <section class="panel" id="scorecard" style="grid-column:1 / -1">
    <div class="panel-h"><h2>Accuracy — pVerify OON examples</h2><span id="scTag" class="sctag"></span></div>
    <div class="panel-b"><table class="sc"><thead><tr>
      <th>Case</th><th>Truth</th><th>Our verdict</th><th>Result</th><th>How</th>
    </tr></thead><tbody id="scBody"></tbody></table></div>
  </section>
```

- [ ] **Step 5: Add the render helpers** — in the `<script>`, insert these functions just before `function render(d){` (line ~240):

```javascript
const SHORT = {IN_NETWORK:'IN_NETWORK', OUT_OF_NETWORK:'OUT_OF_NETWORK', UNKNOWN:'UNKNOWN', REVIEW:'REVIEW'};

function intakeFrom(d){
  // 271 intake lane: prefer parsed report, else echo the submitted query (LAST)
  const p = d.parsed, b = LAST || {};
  if(p) return `${esc(p.payer_name||p.payer_key||'')} · ${esc(p.plan_name||'')}<br>${esc((p.provider_first||'')+' '+(p.provider_last||''))} · NPI ${esc(p.npi||'—')}`;
  return `${esc(d.payer||b.payer||'')} · ${esc(b.plan||'—')}<br>${esc(b.last_name||'')} · NPI ${esc(b.npi||'—')}`;
}

function laneCard(title, badgeText, badgeCls, bodyHtml, subHtml){
  return `<div class="lane"><h4>${esc(title)}<span class="badge ${badgeCls}">${esc(badgeText)}</span></h4>
    <div class="ld">${bodyHtml}</div>${subHtml?`<div class="sub">${subHtml}</div>`:''}</div>`;
}

function sigBy(d, source){ return (d.evidence&&d.evidence.signals||[]).find(s=>s.source===source); }

function renderLanes(d){
  const ev = d.evidence||{}; const pd = ev.payer_directory||{};
  // 271 intake (LIVE)
  const l271 = laneCard('Eligibility 271 (intake)', 'LIVE', 'live', intakeFrom(d),
    'parsed from the eligibility report / query');
  // payer directory (LIVE) — the raw pre-finalize signal
  const mp = pd.matched_provider||{};
  const pdBody = `<span class="res ${pd.status==='IN_NETWORK'?'corroborates':(pd.status==='OUT_OF_NETWORK'?'contradicts':'inconclusive')}">${esc(SHORT[pd.status]||pd.status||'—')}</span>`
    + esc(mp.name? mp.name : (pd.plan_or_network_checked||''));
  const lPayer = laneCard('Payer directory (website / API)', 'LIVE', 'live', pdBody,
    esc(pd.plan_or_network_checked||''));
  // Stedi (LIVE badge per product decision)
  const st = sigBy(d,'Stedi');
  const stBody = st? `<span class="res ${esc(st.result)}">${esc(st.result)}</span>${esc(st.detail)}`
                   : '<span class="sub">no Stedi response</span>';
  const lStedi = laneCard('Stedi 270/271 eligibility', 'LIVE', 'live', stBody, '');
  // TIN-scope (dynamic badge)
  const tin = sigBy(d,'TIN-scope');
  const tinLive = tin && (tin.result==='corroborates' || tin.result==='contradicts');
  const tinBody = tin && tinLive
      ? `<span class="res ${esc(tin.result)}">${esc(tin.result)}</span>${esc(tin.detail)}`
      : esc(tin? tin.detail : 'No billing TIN evaluated for this case.');
  const lTin = laneCard('TIN-scope (group billing)', tinLive?'LIVE':'NEEDS INTEGRATION',
      tinLive?'live':'needs', tinBody,
      tinLive? '' : 'needs NPI→TIN crosswalk / Availity TIN portal');
  return `<div class="lanes">${l271}${lPayer}${lStedi}${lTin}</div>`;
}

function renderGroundTruth(d){
  const gt = d.ground_truth; if(!gt) return '';
  const caught = d.status !== 'IN_NETWORK';  // a confident IN against an OON truth is the miss
  const how = caught
    ? `Payer directory said ${esc(SHORT[(d.evidence&&d.evidence.payer_directory||{}).status]||'—')}; ${esc((d.notes||'').includes('OVERRIDE')?'Availity golden-record override confirmed '+esc(gt.truth):'corroboration flagged the conflict')}.`
    : `We returned ${esc(SHORT[d.status])} but the confirmed truth is ${esc(gt.truth)} — directory is stale.`;
  return `<div class="gt ${caught?'caught':'missed'}">
    <div class="row"><b>Real</b><span>${esc(gt.truth)} <span class="sctag">(${esc(gt.source)})</span></span></div>
    <div class="row"><b>Ours</b><span>${esc(SHORT[d.status]||d.status)} (${esc(d.confidence||'')}) — ${caught?'caught':'MISSED'}</span></div>
    <div class="how">How: ${how}</div></div>`;
}

async function loadBenchmark(){
  let rows=[]; try{ rows = await (await fetch('/api/benchmark')).json(); }catch(e){ rows=[]; }
  const caught = rows.filter(r=>r.caught).length;
  $('scTag').textContent = rows.length? `${caught}/${rows.length} caught` : '';
  $('scBody').innerHTML = rows.map(r=>`<tr>
    <td>${esc(r.case)}</td><td class="st">${esc(r.truth)}</td>
    <td class="st">${esc(r.our_status)} <span class="sctag">(${esc(r.our_confidence)})</span></td>
    <td class="${r.caught?'ok':'miss'}">${r.caught?'✅ caught':'❌ missed'}</td>
    <td class="how">${esc(r.how)}</td></tr>`).join('');
}
```

- [ ] **Step 6: Wire the helpers into `render(d)`** — in `render`, replace the existing facts/innerHTML assembly so the ground-truth banner sits above the facts and the lanes sit below the cross-checks. Change the `r.innerHTML=...` block to:

```javascript
  r.innerHTML=`<div class="verdict">
    <div class="vhead">
      <span class="lozenge ${v.cls}">${ICON[d.status]||''}${v.text}</span>
      <span class="conf">confidence <span class="meter">${meter}</span> ${esc(d.confidence||'')}</span>
    </div>
    ${renderGroundTruth(d)}
    <div class="facts">
      <div class="fact"><div class="k">Plan / network checked</div><div class="v">${esc(d.plan_or_network_checked)}</div></div>
      ${provHtml}${netHtml}
      <div class="fact"><div class="k">Rationale</div><div class="v">${esc(d.notes)}</div></div>
      ${corHtml}${srcHtml}
    </div>
    <div class="fact"><div class="k">Evidence by source</div></div>
    ${renderLanes(d)}
    <details><summary>Raw response</summary><pre>${esc(JSON.stringify(d,null,2))}</pre></details>
  </div>`;
```

- [ ] **Step 7: Load the scorecard on page load** — change the last script line:

```javascript
loadPayers().then(loadSamples).then(loadBenchmark);
```

- [ ] **Step 8: Run the smoke test + full suite**

Run: `.venv/bin/pytest -q`
Expected: PASS — the smoke test passes and all prior tests still pass.

- [ ] **Step 9: Manual verification**

```bash
.venv/bin/uvicorn network_probe.api:app --port 8000
```
Open `http://127.0.0.1:8000`, pick the **"Rodriguez, Aurelia · Devoted CO PPO · Dr Li"** sample, click **Verify network status**, and confirm:
- Verdict lozenge: **Out of network**, confidence high.
- Ground-truth banner: Real `OUT_OF_NETWORK`, Ours `OUT_OF_NETWORK (high) — caught`, How mentions the directory `IN` + Availity override.
- Four lanes render: 271 (LIVE), Payer directory (LIVE, shows `IN_NETWORK` — the stale directory), Stedi (LIVE, `contradicts`), TIN-scope (NEEDS INTEGRATION).
- Scorecard at the bottom shows **4/4 caught**, Rodriguez row `✅ caught … via golden-record override (Availity)`.

(If the Devoted directory is unreachable from this environment, the payer-directory call may 400 — that is the pre-existing live-network dependency, not a regression. Note it and move on.)

- [ ] **Step 10: Commit**

```bash
git add network_probe/static/index.html tests/test_api.py
git commit -m "feat(ui): evidence-by-source lanes, ground-truth banner, accuracy scorecard"
```

---

## Self-Review

**Spec coverage:**
- §5.1 raw payer snapshot → Task 3. ✅
- §5.2 display signals under override → Tasks 2 (runner + precomputed param) + 3 (computed in service, attached to evidence regardless of override). ✅
- §5.3 Stedi fixture, LIVE badge, Rodriguez=contradicts → Task 2 (source/fixture) + Task 6 (badge). ✅
- §5.4 ground-truth map + `/api/benchmark` → Task 4. ✅
- §5.5 Rodriguez override seed → Task 5. ✅
- §6.1 four lanes + dynamic TIN badge → Task 6 (`renderLanes`). ✅
- §6.2 ground-truth banner → Task 6 (`renderGroundTruth`). ✅
- §6.3 scorecard, 4/4, Rodriguez annotation → Tasks 4 (data) + 6 (`loadBenchmark`). ✅
- §9 acceptance criteria → Task 6 Step 9 manual checks + the per-task pytest assertions. ✅

**Placeholder scan:** No TBD/TODO; every code step shows complete code. ✅

**Type consistency:** `run_display_signals(verdict, q, sources) -> list[Signal]` defined in Task 2 and consumed in Task 3 (passed as `sig_objs` to `finalize(..., signals=sig_objs)`). `evidence` dict shape `{payer_directory:{status,confidence,matched_provider,plan_or_network_checked,source_url,notes}, signals:[{source,result,detail}]}` is produced in Task 3 and read by Task 6 (`sigBy`, `renderLanes`). `ground_truth` keys `{truth,source,note}` produced in Task 4, read in Task 6 (`renderGroundTruth`). Benchmark row keys `{case,truth,our_status,our_confidence,caught,how}` produced in Task 4, read in Task 6 (`loadBenchmark`). Signal source name for Stedi is `"Stedi"` and TIN is `"TIN-scope"` in both producer and consumer. ✅
