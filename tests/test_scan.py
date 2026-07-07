"""Validation for the presence-based SCAN directory adapter.

Offline tests replay SCAN's real responses (captured from providerdirectory.scanhealthplan.com:
Bridget Smith, NPI 1205982105 / id 8694298, role 8701043 → org 971354, location "Unknown")
via httpx.MockTransport. SCAN rate-limits hard, so there is no live test here.

Ground truth (verified live, see docs/payer-sources/MATRIX.md): SCAN's directory lists in-network
providers but exposes NO network linkage, so the only signal is presence = in-network for SCAN.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import httpx

from network_probe.core._http import CachedClient
from network_probe.domain.models import NetworkStatus, ProviderQuery
from network_probe.payers.adapters.scan import ScanDirectoryAdapter

# --- real captured SCAN resources -------------------------------------------------
BRIDGET = {
    "resourceType": "Bundle",
    "total": 1,
    "entry": [
        {
            "resource": {
                "resourceType": "Practitioner",
                "id": "8694298",
                "identifier": [{"system": "http://hl7.org/fhir/sid/us-npi", "value": "1205982105"}],
                "name": [{"text": "SMITH, BRIDGET, NP", "family": "SMITH", "given": ["BRIDGET, J"]}],
                # note: real SCAN Practitioner read carries no address
            }
        }
    ],
}
BRIDGET_ROLE = {
    "resourceType": "Bundle",
    "total": 1,
    "entry": [
        {
            "resource": {
                "resourceType": "PractitionerRole",
                "id": "8701043",
                "practitioner": {"reference": "Practitioner/8694298"},
                "organization": {"reference": "Organization/971354"},
                # real SCAN role: location has no reference, only display "Unknown" → no state
                "location": [{"type": "Location", "display": "Unknown"}],
            }
        }
    ],
}
# a provider WITH a resolvable state, to exercise the state qualifier
CARLA = {
    "resourceType": "Bundle",
    "total": 1,
    "entry": [
        {
            "resource": {
                "resourceType": "Practitioner",
                "id": "5000",
                "identifier": [{"system": "http://hl7.org/fhir/sid/us-npi", "value": "2222222222"}],
                "name": [{"family": "JONES", "given": ["CARLA"]}],
                "address": [{"state": "CA"}],
            }
        }
    ],
}
CARLA_ROLE = {
    "resourceType": "Bundle",
    "total": 1,
    "entry": [
        {
            "resource": {
                "resourceType": "PractitionerRole",
                "id": "5001",
                "practitioner": {"reference": "Practitioner/5000"},
                "location": [{"reference": "Location/600"}],
            }
        }
    ],
}
EMPTY = {"resourceType": "Bundle", "total": 0, "entry": []}


def _handler(request: httpx.Request) -> httpx.Response:
    u = urlsplit(str(request.url))
    qs = parse_qs(u.query)
    if u.path.endswith("/Practitioner"):
        ident = (qs.get("identifier") or [""])[0]
        fam = (qs.get("family") or [""])[0].upper()
        if ident == "1205982105" or fam == "SMITH":
            return httpx.Response(200, json=BRIDGET)
        if ident == "2222222222":
            return httpx.Response(200, json=CARLA)
        return httpx.Response(200, json=EMPTY)
    if u.path.endswith("/PractitionerRole"):
        prac = (qs.get("practitioner") or [""])[0]
        if prac == "8694298":
            return httpx.Response(200, json=BRIDGET_ROLE)
        if prac == "5000":
            return httpx.Response(200, json=CARLA_ROLE)
        return httpx.Response(200, json=EMPTY)
    if "/Location/600" in u.path:
        return httpx.Response(200, json={"resourceType": "Location", "id": "600", "address": {"state": "CA"}})
    return httpx.Response(404, json={})


def _adapter(handler=_handler) -> ScanDirectoryAdapter:
    mock = httpx.Client(transport=httpx.MockTransport(handler))
    return ScanDirectoryAdapter(
        client=CachedClient(cache_dir=None, delay_seconds=0, client=mock),
    )


def _q(npi=None, state=None, last=None):
    return ProviderQuery(payer="scan", plan_hint="", npi=npi, provider_last_name=last, state=state)


def test_present_provider_is_in_network():
    v = _adapter().check_network(_q(npi="1205982105"))
    assert v.status == NetworkStatus.IN_NETWORK
    assert v.confidence == "medium"
    assert v.matched_provider["scan_id"] == "8694298"
    assert "in-network provider directory" in v.notes


def test_absent_provider_is_out_of_network():
    v = _adapter().check_network(_q(npi="9999999999"))
    assert v.status == NetworkStatus.OUT_OF_NETWORK


def test_missing_npi_and_name_is_unknown():
    v = _adapter().check_network(_q())
    assert v.status == NetworkStatus.UNKNOWN


def test_state_unconfirmed_stays_presence_in_network():
    # Bridget has no address and her role location is "Unknown" → state can't be confirmed,
    # but presence still means in-network for SCAN (never a false OON).
    v = _adapter().check_network(_q(npi="1205982105", state="AZ"))
    assert v.status == NetworkStatus.IN_NETWORK
    assert v.confidence == "medium"
    assert "could not be confirmed against AZ" in v.notes


def test_state_match_is_high_confidence():
    v = _adapter().check_network(_q(npi="2222222222", state="CA"))
    assert v.status == NetworkStatus.IN_NETWORK
    assert v.confidence == "high"
    assert v.matched_provider["service_states"] == ["CA"]


def test_state_mismatch_downgrades_to_unknown():
    # Carla services CA; an AZ query can't be confirmed → UNKNOWN (present in SCAN, not in AZ).
    v = _adapter().check_network(_q(npi="2222222222", state="AZ"))
    assert v.status == NetworkStatus.UNKNOWN
    assert "CA" in v.notes


def test_identifier_403_falls_back_to_name_search():
    def throttle_ident(request: httpx.Request) -> httpx.Response:
        u = urlsplit(str(request.url))
        qs = parse_qs(u.query)
        if u.path.endswith("/Practitioner") and "identifier" in qs:
            return httpx.Response(403, json={"resourceType": "OperationOutcome"})
        return _handler(request)

    v = _adapter(throttle_ident).check_network(_q(npi="1205982105", last="SMITH"))
    assert v.status == NetworkStatus.IN_NETWORK
    assert v.matched_provider["scan_id"] == "8694298"
