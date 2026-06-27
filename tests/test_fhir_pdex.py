"""Validation for the generic FHIR PDEX Plan-Net adapter.

Offline tests replay captured Humana FHIR responses via httpx.MockTransport; live tests
(`-m live`) hit Humana's real public CMS Provider Directory API at fhir.humana.com/api.

Cross-payer ground truth (all confirmed live, see DISCOVERY-fhir.md):
  Kyle A Herron, NPI 1679766943 — in Humana's FHIR directory under 10 networks incl.
  "Medicare PPO" and "Natl Medicare HMO/SNP-Travel", but NOT "FL Medicare HMO" (which is
  why the bot-protected web search returned 0 for that one network).
"""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from network_probe.adapters.fhir_pdex import FhirPdexAdapter
from network_probe.core._http import CachedClient
from network_probe.domain.models import NetworkStatus, ProviderQuery
from network_probe.domain.plan_aliases import network_aliases

FIX = Path(__file__).parent / "fixtures"
KYLE_NPI = "1679766943"
KYLE_PID = "121019814f05e529b5361d8f73ad779dc73f8ac1a56b30a86461234a543db35a"
HUMANA = "https://fhir.humana.com/api"


def _load(name: str) -> dict:
    with (FIX / name).open() as fh:
        return json.load(fh)


def _fhir_handler(request: httpx.Request) -> httpx.Response:
    u = urlsplit(str(request.url))
    qs = parse_qs(u.query)
    if u.path.endswith("/Practitioner"):
        ident = (qs.get("identifier") or [""])[0]
        if ident == KYLE_NPI:
            return httpx.Response(200, json=_load("fhir-kyle-by-npi.json"))
        return httpx.Response(200, json=_load("fhir-practitioner-notfound.json"))
    if u.path.endswith("/PractitionerRole"):
        prac = (qs.get("practitioner") or [""])[0]
        if prac == KYLE_PID:
            return httpx.Response(200, json=_load("fhir-kyle-practitionerrole.json"))
        return httpx.Response(200, json={"resourceType": "Bundle", "total": 0, "entry": []})
    return httpx.Response(404, json={})


def _offline() -> FhirPdexAdapter:
    mock = httpx.Client(transport=httpx.MockTransport(_fhir_handler))
    cc = CachedClient(cache_dir=None, delay_seconds=0, client=mock)
    return FhirPdexAdapter(base_url=HUMANA, payer_name="humana-fhir", client=cc)


def _q(npi, plan):
    return ProviderQuery(payer="humana-fhir", plan_hint=plan, npi=npi, last_name="Herron")


# ---- offline ----------------------------------------------------------------

def test_kyle_in_network_medicare_ppo():
    v = _offline().check_network(_q(KYLE_NPI, "Medicare PPO"))
    assert v.status == NetworkStatus.IN_NETWORK
    assert v.confidence == "high"
    assert v.matched_provider["matched_network"] == "Medicare PPO"


def test_kyle_fl_medicare_hmo_is_unknown_not_oon():
    """He's genuinely not in FL Medicare HMO, but name-matching can't prove that —
    so we return UNKNOWN with his real networks, never a wrong OON."""
    v = _offline().check_network(_q(KYLE_NPI, "FL Medicare HMO"))
    assert v.status == NetworkStatus.UNKNOWN
    assert "Medicare PPO" in (v.matched_provider["networks"])


def test_kyle_no_hint_lists_networks_in_network():
    v = _offline().check_network(_q(KYLE_NPI, ""))
    assert v.status == NetworkStatus.IN_NETWORK
    assert v.confidence == "medium"
    assert len(v.matched_provider["networks"]) >= 5


def test_unknown_npi_is_out_of_network():
    v = _offline().check_network(_q("1000000004", "Medicare PPO"))
    assert v.status == NetworkStatus.OUT_OF_NETWORK


def test_missing_npi_is_unknown():
    v = _offline().check_network(ProviderQuery(payer="humana-fhir", plan_hint="Medicare PPO"))
    assert v.status == NetworkStatus.UNKNOWN


# --- Cigna/UHC-style server: no identifier search; network names via Organization ---
NET_EXT = "http://hl7.org/fhir/us/davinci-pdex-plan-net/StructureDefinition/network-reference"


def _refserver_handler(request: httpx.Request) -> httpx.Response:
    u = urlsplit(str(request.url))
    qs = parse_qs(u.query)
    if u.path.endswith("/Practitioner"):
        if "identifier" in qs:  # this server doesn't support NPI identifier search
            return httpx.Response(400, json={"resourceType": "OperationOutcome",
                                             "issue": [{"severity": "error", "code": "not-supported"}]})
        if (qs.get("family") or [""])[0].lower() == "smith":
            return httpx.Response(200, json={"resourceType": "Bundle", "total": 1, "entry": [
                {"resource": {"resourceType": "Practitioner", "id": "p1", "name": [{"text": "Jane Smith"}],
                              "identifier": [{"system": "http://hl7.org/fhir/sid/us-npi", "value": "1234567893"}]}}]})
        return httpx.Response(200, json={"resourceType": "Bundle", "total": 0, "entry": []})
    if u.path.endswith("/PractitionerRole"):
        return httpx.Response(200, json={"resourceType": "Bundle", "total": 1, "entry": [
            {"resource": {"resourceType": "PractitionerRole", "id": "r1",
                          "extension": [{"url": NET_EXT, "valueReference": {"reference": "Organization/o1"}}],
                          "specialty": [{"coding": [{"display": "Cardiology"}]}]}}]})
    if "/Organization/" in u.path:
        return httpx.Response(200, json={"resourceType": "Organization", "id": "o1", "name": "Open Access Plus"})
    return httpx.Response(404, json={})


def _refserver() -> FhirPdexAdapter:
    mock = httpx.Client(transport=httpx.MockTransport(_refserver_handler))
    return FhirPdexAdapter(base_url="https://example.org/fhir", payer_name="cigna-fhir",
                           client=CachedClient(cache_dir=None, delay_seconds=0, client=mock))


def test_name_fallback_with_org_resolution_in_network():
    """identifier search 400s → name fallback → NPI match → network name via Organization read."""
    v = _refserver().check_network(ProviderQuery(
        payer="cigna-fhir", plan_hint="Open Access Plus", npi="1234567893", last_name="Smith"))
    assert v.status == NetworkStatus.IN_NETWORK
    assert v.matched_provider["matched_network"] == "Open Access Plus"


def test_name_fallback_npi_mismatch_is_oon():
    v = _refserver().check_network(ProviderQuery(
        payer="cigna-fhir", plan_hint="X", npi="9999999999", last_name="Smith"))
    assert v.status == NetworkStatus.OUT_OF_NETWORK


# --- plan-name -> network-name alias map ---
def test_plan_alias_lookup():
    assert network_aliases("uhc", "UHC Bronze Essential", "TX") == ["TX Individual Exchange Benefit Plan"]
    assert network_aliases("uhc", "Bronze Essential", "FL") == []          # state gate
    assert network_aliases("humana-fhir", "HUM FULL AC GIVEBACK") == ["Medicare PPO"]


def _uhc_handler(request: httpx.Request) -> httpx.Response:
    u = urlsplit(str(request.url))
    qs = parse_qs(u.query)
    if u.path.endswith("/Practitioner"):
        if (qs.get("identifier") or [""])[0] == "1972603934":
            return httpx.Response(200, json={"resourceType": "Bundle", "total": 1, "entry": [
                {"resource": {"resourceType": "Practitioner", "id": "u1", "name": [{"text": "Kevin D Fradkin"}],
                              "identifier": [{"system": "http://hl7.org/fhir/sid/us-npi", "value": "1972603934"}]}}]})
        return httpx.Response(200, json={"resourceType": "Bundle", "total": 0, "entry": []})
    if u.path.endswith("/PractitionerRole"):
        return httpx.Response(200, json={"resourceType": "Bundle", "total": 1, "entry": [
            {"resource": {"resourceType": "PractitionerRole", "id": "ur1", "extension": [
                {"url": NET_EXT, "valueReference": {"display": "TX Individual Exchange Benefit Plan"}}]}}]})
    return httpx.Response(404, json={})


def test_uhc_bronze_essential_resolves_in_network_via_alias():
    """The exact case from the report: plan 'Bronze Essential' (TX) → IN via the alias map."""
    mock = httpx.Client(transport=httpx.MockTransport(_uhc_handler))
    a = FhirPdexAdapter(base_url="https://example.org/fhir", payer_name="uhc",
                        client=CachedClient(cache_dir=None, delay_seconds=0, client=mock))
    v = a.check_network(ProviderQuery(payer="uhc", plan_hint="Bronze Essential",
                                      npi="1972603934", last_name="Fradkin", state="TX"))
    assert v.status == NetworkStatus.IN_NETWORK
    assert v.matched_provider["matched_network"] == "TX Individual Exchange Benefit Plan"
    assert "alias" in v.notes.lower()


# ---- live (real Humana CMS Provider Directory API) --------------------------

@pytest.mark.live
def test_kyle_in_network_live():
    a = FhirPdexAdapter(payer_name="humana-fhir", client=CachedClient(cache_dir=None, delay_seconds=0.3))
    try:
        v = a.check_network(_q(KYLE_NPI, "Medicare PPO"))
    except httpx.HTTPError as exc:
        pytest.skip(f"live FHIR unreachable: {exc}")
    assert v.status == NetworkStatus.IN_NETWORK, v.notes


@pytest.mark.live
def test_unknown_npi_oon_live():
    a = FhirPdexAdapter(payer_name="humana-fhir", client=CachedClient(cache_dir=None, delay_seconds=0.3))
    try:
        v = a.check_network(_q("1000000004", "Medicare PPO"))
    except httpx.HTTPError as exc:
        pytest.skip(f"live FHIR unreachable: {exc}")
    assert v.status == NetworkStatus.OUT_OF_NETWORK, v.notes


@pytest.mark.live
def test_uhc_optum_public_fhir_live():
    """UnitedHealthcare via its public Optum FHIR endpoint (no login). Org-resolved networks."""
    a = FhirPdexAdapter(payer_name="uhc", client=CachedClient(cache_dir=None, delay_seconds=0.3))
    try:
        v = a.check_network(ProviderQuery(payer="uhc", plan_hint="", npi="1972603934", last_name="Fradkin"))
    except httpx.HTTPError as exc:
        pytest.skip(f"live UHC FHIR unreachable: {exc}")
    assert v.status == NetworkStatus.IN_NETWORK, v.notes
    assert v.matched_provider["networks"], "expected resolved network names"
