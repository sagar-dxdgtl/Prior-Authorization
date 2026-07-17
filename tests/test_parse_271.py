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


def test_out_of_network_benefits_true_when_plan_has_oon_tiers():
    # The fixture returns both IN ($30) and OON ($60) cost-share tiers -> the plan pays out-of-network
    # ("OON w/ benefits"). network_status stays UNKNOWN (mixed tiers are never a provider verdict), but
    # the plan-level OON-coverage fact must be surfaced so the app can label OON vs OON-w-benefits.
    r = parse_271_benefits(DATA)
    assert r.out_of_network_benefits is True
    assert r.network_status == NetworkStatus.UNKNOWN


def test_out_of_network_benefits_false_for_in_network_only_plan():
    data = {"benefitsInformation": [
        {"code": "B", "inPlanNetworkIndicatorCode": "Y", "benefitAmount": "20", "serviceTypeCodes": ["98"]},
    ]}
    r = parse_271_benefits(data)
    assert r.out_of_network_benefits is False
    assert r.network_status == NetworkStatus.IN_NETWORK


def test_out_of_network_benefits_none_when_no_cost_share_tiers():
    data = {"benefitsInformation": [{"code": "1", "serviceTypeCodes": ["30"]}]}  # active coverage, no tiers
    r = parse_271_benefits(data)
    assert r.out_of_network_benefits is None


def test_out_of_network_benefits_included_in_dict():
    assert parse_271_benefits(DATA).to_dict()["out_of_network_benefits"] is True


def test_out_of_network_benefits_false_when_oon_is_structural_only():
    # HMOPOS / D-SNP shape (the live Dual Complete case): OON lines exist, but only as
    # deductible / OOP-max on general coverage — NO OON copay/coinsurance for a physician service.
    # The plan does not actually pay out-of-network, so this is "OON", not "OON w/ benefits".
    data = {"benefitsInformation": [
        {"code": "B", "inPlanNetworkIndicatorCode": "Y", "benefitAmount": "20", "serviceTypeCodes": ["98"]},
        {"code": "C", "inPlanNetworkIndicatorCode": "N", "benefitAmount": "0", "serviceTypeCodes": ["30"]},
        {"code": "G", "inPlanNetworkIndicatorCode": "N", "benefitAmount": "0", "serviceTypeCodes": ["30"]},
    ]}
    assert parse_271_benefits(data).out_of_network_benefits is False


def test_out_of_network_benefits_true_for_oon_coinsurance_on_physician():
    data = {"benefitsInformation": [
        {"code": "A", "inPlanNetworkIndicatorCode": "N", "benefitPercent": "0.4", "serviceTypeCodes": ["96"]},
    ]}
    assert parse_271_benefits(data).out_of_network_benefits is True


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
