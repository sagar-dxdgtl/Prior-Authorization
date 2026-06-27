"""Validation for the Devoted Health adapter.

Offline tests replay captured Algolia responses via httpx.MockTransport; live tests
(`-m live`) hit Devoted's real Algolia index.

Cross-payer ground truth (confirmed in DISCOVERY.md):
  Kyle A Herron, MD (NPI 1679766943) is OUT-of-network for Oscar's FL HMO plan but
  IN Devoted's "FL HMO" network — same provider, opposite verdicts across payers.
  Jessica Herron (NPI 1568741320) is not in Devoted's directory at all -> OUT_OF_NETWORK.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest

from network_probe.adapters.devoted import DevotedAdapter
from network_probe.core._http import CachedClient
from network_probe.domain.models import NetworkStatus, ProviderQuery

FIX = Path(__file__).parent / "fixtures"

KYLE_NPI = "1679766943"      # IN Devoted FL HMO
JESSICA_NPI = "1568741320"   # not in Devoted at all
YEAR = 2026
TODAY = date(2026, 6, 22)


def _load(name: str) -> dict:
    with (FIX / name).open() as fh:
        return json.load(fh)


def _algolia_handler(request: httpx.Request) -> httpx.Response:
    """Route by the Algolia query params embedded in the POST body."""
    body = json.loads(request.content.decode())
    params = parse_qs(body["requests"][0]["params"])
    q = (params.get("query") or [""])[0]
    filters = (params.get("filters") or [""])[0]
    facets = (params.get("facets") or [""])[0]

    if "NetworkNames" in facets:  # facet enumeration for resolve_network
        return httpx.Response(200, json=_load("devoted-facets.json"))

    has_network = "NetworkNames" in filters
    if q == KYLE_NPI and has_network:
        return httpx.Response(200, json=_load("devoted-kyle-flhmo.json"))
    if q == JESSICA_NPI and has_network:
        return httpx.Response(200, json=_load("devoted-jessica-flhmo.json"))
    if q == JESSICA_NPI and not has_network:
        return httpx.Response(200, json=_load("devoted-jessica-2026-anynet.json"))
    return httpx.Response(200, json={"results": [{"hits": [], "nbHits": 0, "facets": {}}]})


def _offline_adapter() -> DevotedAdapter:
    mock_client = httpx.Client(transport=httpx.MockTransport(_algolia_handler))
    cc = CachedClient(cache_dir=None, delay_seconds=0, client=mock_client)
    return DevotedAdapter(year=YEAR, today=TODAY, client=cc)


def _query(npi: str) -> ProviderQuery:
    return ProviderQuery(payer="devoted", plan_hint="HMO", npi=npi,
                         last_name="Herron", state="FL", zip_code="33409")


# ---- offline tests ----------------------------------------------------------

def test_resolve_network_fl_hmo():
    assert _offline_adapter().resolve_network("HMO", "FL") == "FL HMO"


def test_resolve_network_dsnp_variant():
    # "HMO D-SNP" should resolve to "FL HMO DSNP" (a real facet value)
    assert _offline_adapter().resolve_network("HMO D-SNP", "FL") == "FL HMO DSNP"


def test_resolve_network_unknown_returns_none():
    assert _offline_adapter().resolve_network("no plan type here", "FL") is None


def test_kyle_in_network():
    """Kyle Herron IS in Devoted FL HMO (opposite of his Oscar verdict)."""
    v = _offline_adapter().check_network(_query(KYLE_NPI))
    assert v.status == NetworkStatus.IN_NETWORK
    assert v.confidence == "high"
    assert v.matched_provider["npi"] == KYLE_NPI
    assert "FL HMO" in v.plan_or_network_checked


def test_jessica_out_of_network():
    """Jessica isn't in Devoted's directory -> OUT_OF_NETWORK, never IN."""
    v = _offline_adapter().check_network(_query(JESSICA_NPI))
    assert v.status == NetworkStatus.OUT_OF_NETWORK
    assert v.status != NetworkStatus.IN_NETWORK


def test_missing_npi_is_unknown():
    a = _offline_adapter()
    q = ProviderQuery(payer="devoted", plan_hint="HMO", last_name="Herron", state="FL")
    assert a.check_network(q).status == NetworkStatus.UNKNOWN


def test_unresolvable_plan_is_unknown_not_oon():
    a = _offline_adapter()
    q = ProviderQuery(payer="devoted", plan_hint="???", npi=KYLE_NPI, state="FL")
    assert a.check_network(q).status == NetworkStatus.UNKNOWN


# ---- live end-to-end --------------------------------------------------------

@pytest.mark.live
def test_kyle_in_network_live():
    a = DevotedAdapter(year=YEAR, today=TODAY, client=CachedClient(cache_dir=None, delay_seconds=0.3))
    try:
        v = a.check_network(_query(KYLE_NPI))
    except httpx.HTTPError as exc:
        pytest.skip(f"live Algolia unreachable: {exc}")
    assert v.status == NetworkStatus.IN_NETWORK, v.notes


@pytest.mark.live
def test_jessica_out_of_network_live():
    a = DevotedAdapter(year=YEAR, today=TODAY, client=CachedClient(cache_dir=None, delay_seconds=0.3))
    try:
        v = a.check_network(_query(JESSICA_NPI))
    except httpx.HTTPError as exc:
        pytest.skip(f"live Algolia unreachable: {exc}")
    assert v.status == NetworkStatus.OUT_OF_NETWORK, v.notes
