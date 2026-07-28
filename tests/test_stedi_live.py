"""Live Stedi verification (Task 27).

Gated: marked ``live`` and skipped unless ``STEDI_API_KEY`` is set. Run with::

    pytest -m live

Uses a Stedi MOCK payer with a TEST key + a synthetic member, so it sends **no real PHI** and
needs **no payer enrollment**. The default test exercises the real AAA-error path (the member
won't match), which validates auth + endpoint + response JSON + our error parsing + PHI redaction
against the live API. To additionally assert a full benefits parse, set the documented mock-member
env vars (Stedi's mock-requests doc lists exact values per payer)::

    STEDI_LIVE_PAYER=60054 STEDI_LIVE_MEMBER_ID=AETNA9wcSu \
    STEDI_LIVE_FIRST=John STEDI_LIVE_LAST=Doe STEDI_LIVE_DOB=<exact-yyyymmdd> pytest -m live

Live-verified field names (matching ``parse_271_benefits``): ``benefitsInformation`` entries use
``code, name, serviceTypeCodes, serviceTypes, coverageLevelCode, inPlanNetworkIndicatorCode,
benefitAmount, benefitPercent, timeQualifierCode, additionalInformation``; errors arrive in a
top-level ``errors`` array (AAA codes, e.g. 71 = DOB mismatch, 72 = invalid member id).
"""

from __future__ import annotations

import json
import os

import httpx
import pytest

from network_probe.core.config import get_settings
from network_probe.domain.models import NetworkStatus
from network_probe.stedi.parse_271 import parse_271_benefits

pytestmark = pytest.mark.live


def _require_key() -> str:
    key = get_settings().stedi_api_key
    if not key:
        pytest.skip("STEDI_API_KEY not set — skipping live Stedi test")
    return key


def _call(payer: str, subscriber: dict) -> dict:
    s = get_settings()
    body = {
        "tradingPartnerServiceId": payer,
        "provider": {"organizationName": "Test Clinic", "npi": "1679766943"},
        "subscriber": subscriber,
        "encounter": {"serviceTypeCodes": ["30"]},
    }
    r = httpx.post(
        s.stedi_eligibility_url,
        headers={"Authorization": s.stedi_api_key, "Content-Type": "application/json"},
        content=json.dumps(body),
        timeout=30,
    )
    assert r.status_code == 200, r.text[:300]
    return r.json()


def test_live_unknown_member_is_honest_and_leaks_nothing():
    """A synthetic member against a real payer: the verdict must stay UNKNOWN and nothing
    identifying may survive parsing.

    This used to assert an AAA error code, and it broke because Aetna stopped sending one for this
    input — it now returns `errors: []` and a definite `planStatus: Inactive`. That assertion never
    belonged in a live test: AAA parsing is deterministic and is already covered offline in
    `test_parse_271.py`, so pinning it here only bought a dependency on one payer's current
    behaviour. What a live call uniquely proves is the redaction contract, so that is what is left.

    The payer echoes the submitted member id back, plus a placeholder subscriber of its own
    ("HUMAN RESOURCES HELP DESK", a PO box in Honolulu). Both are checked: an identifier we sent and
    a name/address we did not ask for must be equally absent from anything we keep.
    """
    _require_key()
    data = _call(
        "60054", {"firstName": "Jane", "lastName": "Doe", "dateOfBirth": "19900101", "memberId": "NOSUCHMEMBER0"}
    )
    res = parse_271_benefits(data)
    # Never a confident answer about a member the payer could not identify.
    assert res.network_status == NetworkStatus.UNKNOWN
    assert res.coverage_active is not True

    blob = json.dumps(res.to_dict()) + json.dumps(res.source_audit)
    assert "possibleResolutions" not in blob, "verbose payer text must not be stored/returned"
    assert "NOSUCHMEMBER0" not in blob, "submitted member id must not leak into parsed output"
    echoed = data.get("subscriber") or {}
    for field in ("memberId", "firstName", "lastName"):
        value = str(echoed.get(field) or "").strip()
        if value:
            assert value not in blob, f"payer-echoed subscriber {field} must not survive parsing"
    for line in (echoed.get("address") or {}).values():
        if str(line or "").strip():
            assert str(line) not in blob, "payer-echoed subscriber address must not survive parsing"


@pytest.mark.skipif(
    not os.environ.get("STEDI_LIVE_MEMBER_ID"),
    reason="set STEDI_LIVE_MEMBER_ID + STEDI_LIVE_DOB (documented mock member) to assert full benefits",
)
def test_live_full_benefits_parse():
    """With a documented mock member, assert a full benefits-bearing 271 parses and carries no PHI."""
    _require_key()
    subscriber = {
        "firstName": os.environ.get("STEDI_LIVE_FIRST", "John"),
        "lastName": os.environ.get("STEDI_LIVE_LAST", "Doe"),
        "dateOfBirth": os.environ["STEDI_LIVE_DOB"],
        "memberId": os.environ["STEDI_LIVE_MEMBER_ID"],
    }
    data = _call(os.environ.get("STEDI_LIVE_PAYER", "60054"), subscriber)
    res = parse_271_benefits(data)
    assert res.coverage_active is not None
    assert len(res.benefits) > 0
    assert os.environ["STEDI_LIVE_MEMBER_ID"] not in json.dumps(res.to_dict())
