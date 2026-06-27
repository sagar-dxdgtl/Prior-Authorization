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
    r = httpx.post(s.stedi_eligibility_url,
                   headers={"Authorization": s.stedi_api_key, "Content-Type": "application/json"},
                   content=json.dumps(body), timeout=30)
    assert r.status_code == 200, r.text[:300]
    return r.json()


def test_live_error_path_parses_and_redacts():
    """A synthetic member returns a real AAA error; our parser must yield an honest UNKNOWN with the
    error CODE only (never the verbose payer text or the submitted member id)."""
    _require_key()
    data = _call("60054", {"firstName": "Jane", "lastName": "Doe",
                           "dateOfBirth": "19900101", "memberId": "NOSUCHMEMBER0"})
    res = parse_271_benefits(data)
    assert res.network_status == NetworkStatus.UNKNOWN
    assert res.coverage_active is None
    assert res.source_audit.get("error_codes"), "expected a captured AAA error code"
    blob = json.dumps(res.to_dict()) + json.dumps(res.source_audit)
    assert "possibleResolutions" not in blob, "verbose payer text must not be stored/returned"
    assert "NOSUCHMEMBER0" not in blob, "submitted member id must not leak into parsed output"


@pytest.mark.skipif(not os.environ.get("STEDI_LIVE_MEMBER_ID"),
                    reason="set STEDI_LIVE_MEMBER_ID + STEDI_LIVE_DOB (documented mock member) to assert full benefits")
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
