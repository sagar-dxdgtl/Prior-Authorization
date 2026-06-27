"""Validation for the Oscar adapter.

Two layers:
  * Offline, deterministic tests driven by captured fixtures via httpx.MockTransport.
    These always run and pin the verdict logic against the real recorded responses.
  * A live end-to-end test (marked `live`) that hits hioscar.com for the actual
    ground-truth Herron case. Run with:  pytest -m live
    It skips gracefully if the network/site is unreachable.

Ground truth (from CLAUDE.md, confirmed in DISCOVERY.md):
  Kyle Herron, NPI 1679766943, Oscar FL "Silver Simple PCP Saver CSR 150" (net 066)
  => OUT_OF_NETWORK.
Positive control:
  Jessica L Herron, NPI 1568741320 => IN_NETWORK for net 066, 2026.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from network_probe.adapters.oscar import OscarAdapter
from network_probe.core._http import CachedClient
from network_probe.models import NetworkStatus, ProviderQuery

FIX = Path(__file__).parent / "fixtures"

# ---- test constants ---------------------------------------------------------
KYLE_NPI = "1679766943"          # ground-truth OON provider (absent from net 066)
JESSICA_NPI = "1568741320"       # positive control (in net 066)
JESSICA_EID = "8oHqRKWUYDqgNR"
PLAN_HINT = "BASE SILVER CSR 150 / SILVERSIMPLEPCPSAVER"
TODAY = date(2026, 6, 22)
YEAR = 2026


def _load(name: str) -> dict:
    with (FIX / name).open() as fh:
        return json.load(fh)


def _mock_handler(request: httpx.Request) -> httpx.Response:
    """Route recorded fixtures by path + query, mimicking hioscar.com."""
    u = urlsplit(str(request.url))
    path, qs = u.path, parse_qs(u.query)

    if path == "/search/api/v2/networks":
        return httpx.Response(200, json=_load("networks.json"))

    if path == "/api/get-network-plans":
        nid = qs.get("networkId", [""])[0]
        return httpx.Response(200, json=_load(f"plans-{nid}.json"))

    if path == "/search/autocomplete/multientity/":
        # only the Herron/066 query is fixtured (all our tests search Herron in 066)
        return httpx.Response(200, json=_load("autocomplete-herron-066.json"))

    if path.startswith("/api/provider-profile/legacy-initial-data-api/"):
        eid = path.rsplit("/", 1)[-1]
        if eid == JESSICA_EID:
            return httpx.Response(200, json=_load("provider-profile-jessica.json"))
        return httpx.Response(404, json={})

    return httpx.Response(404, json={"unmatched": path})


def _offline_adapter() -> OscarAdapter:
    mock_client = httpx.Client(transport=httpx.MockTransport(_mock_handler))
    cc = CachedClient(cache_dir=None, delay_seconds=0, client=mock_client)
    return OscarAdapter(year=YEAR, today=TODAY, client=cc)


def _query(npi: str) -> ProviderQuery:
    return ProviderQuery(
        payer="oscar",
        plan_hint=PLAN_HINT,
        npi=npi,
        last_name="Herron",
        state="FL",
        zip_code="33409",
    )


# ---- offline tests ----------------------------------------------------------

def test_resolve_network_maps_test_plan_to_066():
    a = _offline_adapter()
    resolved = a.resolve_network(PLAN_HINT, "FL")
    assert resolved is not None
    assert resolved["network_id"] == "066"
    assert resolved["matched_plan"] == "Silver Simple PCP Saver CSR 150"
    assert resolved["policy_id"] == "e9d56277-eae0-46f9-865a-e90c7573a0e8"


def test_resolve_network_unknown_plan_returns_none():
    a = _offline_adapter()
    assert a.resolve_network("Totally Made Up Plan Name XYZ", "FL") is None


def test_herron_is_not_in_network():
    """THE ground-truth assertion: Kyle Herron must NOT be in-network."""
    verdict = _offline_adapter().check_network(_query(KYLE_NPI))
    assert verdict.status != NetworkStatus.IN_NETWORK
    assert verdict.status == NetworkStatus.OUT_OF_NETWORK
    assert verdict.confidence == "high"
    assert "066" in verdict.plan_or_network_checked
    assert verdict.source_url  # audit trail populated


def test_jessica_is_in_network():
    """Positive control: a provider actually in net 066 reads IN_NETWORK."""
    verdict = _offline_adapter().check_network(_query(JESSICA_NPI))
    assert verdict.status == NetworkStatus.IN_NETWORK
    assert verdict.confidence == "high"
    assert verdict.matched_provider["npi"] == JESSICA_NPI


def test_source_url_always_present():
    for npi in (KYLE_NPI, JESSICA_NPI):
        assert _offline_adapter().check_network(_query(npi)).source_url


def test_missing_last_name_is_unknown_not_oon():
    a = _offline_adapter()
    q = ProviderQuery(payer="oscar", plan_hint=PLAN_HINT, npi=KYLE_NPI, state="FL")
    assert a.check_network(q).status == NetworkStatus.UNKNOWN


# ---- live end-to-end (the real validation; run with `pytest -m live`) --------

@pytest.mark.live
def test_herron_oon_live():
    adapter = OscarAdapter(year=YEAR, today=TODAY, client=CachedClient(cache_dir=None, delay_seconds=0.4))
    try:
        verdict = adapter.check_network(_query(KYLE_NPI))
    except httpx.HTTPError as exc:
        pytest.skip(f"live site unreachable: {exc}")
    assert verdict.status == NetworkStatus.OUT_OF_NETWORK, verdict.notes


@pytest.mark.live
def test_jessica_in_network_live():
    adapter = OscarAdapter(year=YEAR, today=TODAY, client=CachedClient(cache_dir=None, delay_seconds=0.4))
    try:
        verdict = adapter.check_network(_query(JESSICA_NPI))
    except httpx.HTTPError as exc:
        pytest.skip(f"live site unreachable: {exc}")
    assert verdict.status == NetworkStatus.IN_NETWORK, verdict.notes
