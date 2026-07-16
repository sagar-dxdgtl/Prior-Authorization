import json
import pathlib
from decimal import Decimal

from network_probe.domain.benefits import BenefitCategory, CoverageLevel, Network
from network_probe.domain.models import NetworkStatus
from network_probe.stedi.parse_271 import parse_271_benefits

DATA = json.loads((pathlib.Path(__file__).parent / "fixtures/stedi-271-inn-oon.json").read_text())


def test_active_and_inn_oon_copays():
    r = parse_271_benefits(DATA)
    assert r.coverage_active is True
    copays = [b for b in r.benefits if b.category == BenefitCategory.COPAY]
    nets = {b.network: b.amount for b in copays}
    assert nets[Network.IN] == Decimal("30") and nets[Network.OON] == Decimal("60")


def test_met_paired_and_cob_redacted():
    r = parse_271_benefits(DATA)
    ded = next(b for b in r.benefits if b.category == BenefitCategory.DEDUCTIBLE and b.time_period == "calendar year")
    assert ded.network == Network.OON and ded.level == CoverageLevel.FAMILY
    assert str(ded.met) == "2500" and str(ded.remaining) == "1500"  # 4000 total - 1500 remaining
    # the standalone "remaining" line was consumed into the total
    assert not any(b.category == BenefitCategory.DEDUCTIBLE and b.time_period == "remaining" for b in r.benefits)
    coins = next(b for b in r.benefits if b.category == BenefitCategory.COINSURANCE)
    assert coins.percent == Decimal("0.4") and coins.amount is None
    assert r.prior_auth_required is True
    assert r.cob and "primaryPayer" in r.cob and "subscriberMemberId" not in r.cob
    assert "SHOULD-NOT-LEAK" not in json.dumps(r.to_dict())


def test_aaa_reject_is_unknown_and_redacted():
    r = parse_271_benefits({"errors": [{"code": "42", "description": "member id ABC123 not found"}]})
    assert r.coverage_active is None and r.network_status == NetworkStatus.UNKNOWN
    assert r.source_audit["error_codes"] == ["42"]
    assert "ABC123" not in json.dumps(r.source_audit)


def test_inactive_coverage():
    r = parse_271_benefits({"benefitsInformation": [{"code": "6", "name": "Inactive"}]})
    assert r.coverage_active is False


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
