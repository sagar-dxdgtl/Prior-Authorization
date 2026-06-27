from decimal import Decimal
from network_probe.benefits import BenefitLine, EligibilityResult, Network, BenefitCategory, CoverageLevel
from network_probe.models import NetworkStatus

def test_eligibility_result_to_dict_is_json_safe_and_phi_free():
    bl = BenefitLine(service_type="30", service_type_label="General", network=Network.OON,
                     category=BenefitCategory.COPAY, level=CoverageLevel.INDIVIDUAL,
                     amount=Decimal("50"), percent=None, time_period="calendar year",
                     met=None, remaining=None, raw_codes={"EB01": "B"})
    r = EligibilityResult(coverage_active=True, plan_name="Silver", group="GRP1", coverage_dates={},
                          network_status=NetworkStatus.OUT_OF_NETWORK, benefits=[bl],
                          pcp_required=False, prior_auth_required=True, referral_required=False,
                          cob=None, network_verdict=None, corroboration=[], source_audit={"endpoint": "stedi"})
    d = r.to_dict()
    assert d["network_status"] == "OUT_OF_NETWORK"
    assert d["benefits"][0]["network"] == "OON"
    assert d["benefits"][0]["amount"] == "50"            # Decimal serialized to str
    assert d["benefits"][0]["percent"] is None
    assert d["prior_auth_required"] is True
    assert "member_id" not in str(d) and "dob" not in str(d)

def test_json_dumps_round_trips():
    import json
    bl = BenefitLine("30", "General", Network.IN, BenefitCategory.DEDUCTIBLE, CoverageLevel.FAMILY,
                     Decimal("4000"), None, "calendar year", Decimal("2500"), Decimal("1500"), {})
    s = json.dumps(bl.to_dict())   # must not raise (json-safe)
    assert '"met": "2500"' in s and '"remaining": "1500"' in s
