"""Tests for cross-source corroboration (#2) and confidence/asymmetry (#1)."""

from __future__ import annotations

import httpx
import pytest

from network_probe.core._http import CachedClient
from network_probe.domain.corroboration import (
    FreshnessSource,
    NppesSource,
    Signal,
    StediSource,
    TinScopeSource,
    finalize,
)
from network_probe.domain.models import NetworkStatus, NetworkVerdict, ProviderQuery
from network_probe.domain.overrides import Override, OverrideStore


class _NoOverrides:
    def lookup(self, q):
        return None


_NO = _NoOverrides()


def _verdict(status, conf="high", name="Jing Li, MD", npi="1629339312"):
    return NetworkVerdict(status=status, matched_provider={"npi": npi, "name": name},
                          plan_or_network_checked="devoted CO PPO", source_url="http://x",
                          confidence=conf, notes="found in directory.")


def _q(npi="1629339312", last="Li", state="CO"):
    return ProviderQuery(payer="devoted", plan_hint="PPO", npi=npi, last_name=last, state=state)


class _FakeSource:
    def __init__(self, signal):
        self.name = "FAKE"
        self._s = signal

    def check(self, q, v):
        return self._s


# ---- #1 confidence / asymmetry --------------------------------------------

def test_single_source_in_demoted_to_medium():
    v = finalize(_verdict(NetworkStatus.IN_NETWORK, "high"), _q(),
                 sources=[_FakeSource(Signal("FAKE", "corroborates", "ok"))], override_store=_NO)
    assert v.status == NetworkStatus.IN_NETWORK
    assert v.confidence == "medium"
    assert "verify before billing" in v.notes.lower()
    assert v.corroboration and v.corroboration[0]["result"] == "corroborates"


def test_oon_is_left_strong():
    v = finalize(_verdict(NetworkStatus.OUT_OF_NETWORK, "high"), _q(),
                 sources=[_FakeSource(Signal("FAKE", "corroborates", "ok"))], override_store=_NO)
    assert v.status == NetworkStatus.OUT_OF_NETWORK
    assert v.confidence == "high"  # absence-based OON not demoted


# ---- #2 corroboration -> REVIEW on conflict --------------------------------

def test_contradiction_flips_in_to_review():
    v = finalize(_verdict(NetworkStatus.IN_NETWORK, "high"), _q(),
                 sources=[_FakeSource(Signal("NPPES", "contradicts", "NPI deactivated"))], override_store=_NO)
    assert v.status == NetworkStatus.REVIEW
    assert v.confidence == "conflict"
    assert "deactivated" in v.notes.lower()


def test_unreachable_source_does_not_flip():
    v = finalize(_verdict(NetworkStatus.IN_NETWORK, "high"), _q(),
                 sources=[_FakeSource(Signal("NPPES", "inconclusive", "unreachable"))], override_store=_NO)
    assert v.status == NetworkStatus.IN_NETWORK
    assert v.confidence == "medium"  # still demoted, but not flipped


# ---- NPPES source parsing (mocked registry) -------------------------------

def _nppes(json_body):
    def handler(request):
        return httpx.Response(200, json=json_body)
    cc = CachedClient(cache_dir=None, delay_seconds=0,
                      client=httpx.Client(transport=httpx.MockTransport(handler)))
    return NppesSource(client=cc)


def test_nppes_active_corroborates():
    body = {"basic": {"firstName": "Jing", "lastName": "Li", "status": "A"},
            "addresses": [{"state": "CO"}], "enumerationType": "NPI-1"}
    s = _nppes(body).check(_q(), _verdict(NetworkStatus.IN_NETWORK))
    assert s.result == "corroborates"


def test_nppes_deactivated_contradicts():
    body = {"basic": {"firstName": "Jing", "lastName": "Li", "status": "D"},
            "addresses": [{"state": "CO"}]}
    s = _nppes(body).check(_q(), _verdict(NetworkStatus.IN_NETWORK))
    assert s.result == "contradicts"


def test_nppes_not_found_contradicts():
    s = _nppes({}).check(_q(), _verdict(NetworkStatus.IN_NETWORK))  # npiDetails returns {} when unknown
    assert s.result == "contradicts"


def test_nppes_identity_mismatch_contradicts():
    body = {"basic": {"firstName": "Robert", "lastName": "Smith", "status": "A"},
            "addresses": [{"state": "CO"}]}
    s = _nppes(body).check(_q(last="Li"), _verdict(NetworkStatus.IN_NETWORK, name="Jing Li, MD"))
    assert s.result == "contradicts"


def test_nppes_unreachable_is_inconclusive():
    def handler(request):
        raise httpx.ConnectError("no dns")
    cc = CachedClient(cache_dir=None, delay_seconds=0,
                      client=httpx.Client(transport=httpx.MockTransport(handler)))
    s = NppesSource(client=cc).check(_q(), _verdict(NetworkStatus.IN_NETWORK))
    assert s.result == "inconclusive"


# ---- #3 TIN-level source ---------------------------------------------------

def _verdict_with_tins(tins):
    v = _verdict(NetworkStatus.IN_NETWORK)
    v.matched_provider["in_network_tins"] = tins
    return v


def test_tin_matches_corroborates():
    s = TinScopeSource().check(_q(), _verdict_with_tins(["274322240"]))  # q has no tin -> None
    assert s is None
    q = ProviderQuery(payer="oscar", plan_hint="x", npi="1", tin="274322240")
    assert TinScopeSource().check(q, _verdict_with_tins(["274322240"])).result == "corroborates"


def test_tin_shown_when_provider_not_in_directory():
    # OON provider + a billing TIN with NO verified record + no crosswalk -> honestly inconclusive
    q = ProviderQuery(payer="humana-fhir", plan_hint="", npi="1336160274", last_name="Friedman", tin="999000111")
    s = TinScopeSource().check(q, _verdict(NetworkStatus.OUT_OF_NETWORK))
    assert s is not None and s.result == "inconclusive" and "999000111" in s.detail


def test_verified_tin_status_corroborates_oon_for_cigna_kiang():
    # Cigna's TIN portal confirms NPI 1184610453 under TIN 463812940 (Wazni PLLC) is OON;
    # with the directory also OON this is a real corroborating group-level check, not a guess.
    q = ProviderQuery(payer="cigna-fhir", plan_hint="", npi="1184610453", last_name="Kiang", tin="463812940")
    s = TinScopeSource().check(q, _verdict(NetworkStatus.OUT_OF_NETWORK))
    assert s.result == "corroborates"
    assert "463812940" in s.detail and "Wazni" in s.detail and "OUT-OF-NETWORK" in s.detail


def test_verified_tin_status_contradicts_when_directory_says_in():
    # If the directory had listed the provider as IN but the billing TIN is verified OON,
    # that's the "individual listed, billing TIN OON" catch -> contradiction -> finalize REVIEW.
    q = ProviderQuery(payer="cigna-fhir", plan_hint="", npi="1184610453", last_name="Kiang", tin="463812940")
    s = TinScopeSource().check(q, _verdict(NetworkStatus.IN_NETWORK, name="William Kiang", npi="1184610453"))
    assert s.result == "contradicts"
    out = finalize(_verdict(NetworkStatus.IN_NETWORK, name="William Kiang", npi="1184610453"), q,
                   sources=[TinScopeSource()], override_store=_NO)
    assert out.status == NetworkStatus.REVIEW


def test_tin_mismatch_contradicts_and_finalize_reviews():
    q = ProviderQuery(payer="oscar", plan_hint="x", npi="1", tin="463812940")
    s = TinScopeSource().check(q, _verdict_with_tins(["274322240"]))
    assert s.result == "contradicts"
    v = finalize(_verdict_with_tins(["274322240"]), q, sources=[TinScopeSource()], override_store=_NO)
    assert v.status == NetworkStatus.REVIEW  # billing TIN not in-network -> review


# ---- #4 freshness ----------------------------------------------------------

def test_going_oon_soon_drags_confidence_low():
    v = _verdict(NetworkStatus.IN_NETWORK)
    v.matched_provider["going_oon_soon"] = True
    out = finalize(v, _q(), sources=[FreshnessSource()], override_store=_NO)
    assert out.status == NetworkStatus.IN_NETWORK and out.confidence == "low"
    assert "out-of-network soon" in out.notes.lower()


# ---- #5 override / golden record -------------------------------------------

def test_override_wins_over_directory(tmp_path):
    store = OverrideStore(path=tmp_path / "ov.json")
    store.add(Override(payer="devoted", npi="1629339312", status="OUT_OF_NETWORK",
                       verified_by="Availity 2026-05-21", verified_at="2026-05-21",
                       plan="PPO", note="confirmed OON by payer rep"))
    # directory says IN, but the confirmed override flips it to OON authoritatively
    out = finalize(_verdict(NetworkStatus.IN_NETWORK), _q(), override_store=store)
    assert out.status == NetworkStatus.OUT_OF_NETWORK
    assert out.confidence == "high"
    assert "verified override" in out.notes.lower()
    assert out.corroboration[0]["source"] == "override"


def test_override_lookup_specificity(tmp_path):
    store = OverrideStore(path=tmp_path / "ov.json")
    store.add(Override(payer="devoted", npi="1629339312", status="OUT_OF_NETWORK",
                       verified_by="x", verified_at="2026-05-21"))
    assert store.lookup(_q()) is not None
    assert store.lookup(ProviderQuery(payer="devoted", plan_hint="PPO", npi="0000000000")) is None


# ---- Stedi eligibility source (Phase 2) ------------------------------------

def test_stedi_disabled_without_key(monkeypatch):
    monkeypatch.delenv("STEDI_API_KEY", raising=False)
    assert StediSource().check(_q(), _verdict(NetworkStatus.IN_NETWORK)) is None


def test_stedi_dob_conversion():
    assert StediSource._to_stedi_dob("04/11/1970") == "19700411"


def test_stedi_interpret_network_codes():
    f = StediSource._interpret
    assert f({"benefitsInformation": [{"inPlanNetworkIndicatorCode": "Y"}]}).result == "corroborates"
    assert f({"benefitsInformation": [{"inPlanNetworkIndicatorCode": "N"}]}).result == "contradicts"
    assert f({"benefitsInformation": [{"inPlanNetworkIndicatorCode": "Y"},
                                       {"inPlanNetworkIndicatorCode": "N"}]}).result == "inconclusive"
    assert f({"benefitsInformation": []}).result == "inconclusive"


def test_stedi_check_corroborates(monkeypatch):
    monkeypatch.setattr(StediSource, "PAYER_IDS", {"oscar": "00007"})
    def handler(request):
        return httpx.Response(200, json={"benefitsInformation": [{"inPlanNetworkIndicatorCode": "Y"}]})
    cc = CachedClient(cache_dir=None, delay_seconds=0, client=httpx.Client(transport=httpx.MockTransport(handler)))
    s = StediSource(api_key="test_x", client=cc)
    q = ProviderQuery(payer="oscar", plan_hint="x", npi="1", last_name="Herron", member_id="M1", dob="01/02/1970")
    assert s.check(q, _verdict(NetworkStatus.IN_NETWORK)).result == "corroborates"


# ---- Stedi fixture / mock source -------------------------------------------

from network_probe.domain.corroboration import StediMockSource, default_sources, run_display_signals  # noqa: E402


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


# ---- live NPPES (real registry) --------------------------------------------

@pytest.mark.live
def test_nppes_live_active_provider():
    s = NppesSource(client=CachedClient(cache_dir=None, delay_seconds=0.3)).check(
        ProviderQuery(payer="oscar", plan_hint="x", npi="1679766943", last_name="Herron"),
        _verdict(NetworkStatus.IN_NETWORK, name="Kyle A Herron, MD", npi="1679766943"))
    if s.result == "inconclusive":
        pytest.skip("NPPES not reachable from this environment")
    assert s.result == "corroborates"
    assert "herron" in s.detail.lower()
