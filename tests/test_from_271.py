"""The 271 -> portal handoff: does a live eligibility answer make a portal verdict decisive?

This is the link that was missing. `plan_match` only licenses an OUT_OF_NETWORK when it can pin the
plan by identifier, so a human typing "AARP Medicare Advantage" can never produce a decisive verdict —
verified on 2026-07-28, when that exact string pinned "AARP Medicare Advantage CareFlex FL-35" out of
three equally-matching AARP products. A real 271 carries the contract/PBP, which is what fixes it.

The other half of these tests is the PHI boundary: the string built here is typed into a payer's public
search box and stored in the capture's audit note, so it must contain plan descriptors and nothing else.
"""

from __future__ import annotations

from types import SimpleNamespace

from network_probe.portal.from_271 import (
    ProviderTarget,
    plan_is_pinnable,
    plan_string_from_271,
    portal_query_from_271,
)
from network_probe.portal.plan_match import match_plan

OREM = ProviderTarget(npi="1497741409", first_name="Randall", last_name="Orem",
                      zip_code="34986", state="FL", tin="463812940")

UHC_FL_OPTIONS = [
    "AARP Medicare Advantage CareFlex from UHC FL-35 (HMO-POS)",
    "AARP Medicare Advantage Patriot from UHC FL-0026 (PPO)",
    "AARP Medicare Advantage Choice from UHC FL-12 (PPO)",
]


def _r271(**kw):
    base = dict(selected_plan=None, plan_name=None, plan_candidates=[], group=None)
    return SimpleNamespace(**{**base, **kw})


class TestPlanString:
    def test_prefers_the_selected_plan(self):
        r = _r271(selected_plan="AARP Medicare Advantage FL-0026 (PPO)", plan_name="Medicare Advantage")
        assert plan_string_from_271(r).startswith("AARP Medicare Advantage FL-0026")

    def test_harvests_an_identifier_from_a_sibling_candidate(self):
        """The chosen label often names the product without its contract number while a candidate
        carries it. Without this the 271 would pin no better than a human typing the product name."""
        r = _r271(selected_plan="AARP Medicare Advantage (PPO)",
                  plan_candidates=[{"planName": "AARP MA", "contract": "H2406018000"}])
        out = plan_string_from_271(r)
        assert "H2406018000" in out

    def test_does_not_append_a_less_specific_prefix(self):
        """H2406 adds no precision once H2406018000 is present."""
        r = _r271(selected_plan="AARP MA H2406018000", plan_candidates=["Other plan H2406"])
        assert plan_string_from_271(r).count("H2406") == 1

    def test_no_plan_named_returns_none(self):
        """A driver handed plan=None must answer UNKNOWN rather than guess a network."""
        assert plan_string_from_271(_r271()) is None


class TestPhiBoundary:
    def test_group_field_is_never_included(self):
        """In this client's own data the "Ins Group Number" column holds member identifiers such as
        AAAA00000000. This string is typed into a public payer search box and stored in the audit note,
        so sweeping that field in would leak a member id into both."""
        r = _r271(selected_plan="BCBS AZ Statewide PPO", group="AAAA00000000")
        out = plan_string_from_271(r)
        assert "AAAA00000000" not in out
        q = portal_query_from_271(r, OREM, payer_key="bcbs-empire-anthem-elevance-az")
        assert "AAAA00000000" not in (q.plan or "")

    def test_query_carries_no_member_identifier_at_all(self):
        r = _r271(selected_plan="AARP Medicare Advantage FL-0026 (PPO)", group="999888777")
        q = portal_query_from_271(r, OREM, payer_key="unitedhealthcare-fl-south-florida")
        blob = " ".join(str(v) for v in vars(q).values() if v is not None)
        # Synthetic stand-ins for the member id / DOB / MRN shapes this must never carry.
        for member_datum in ("999888777", "1900-01-01", "111222"):
            assert member_datum not in blob
        # The provider/clinic data that SHOULD be there still is.
        assert q.npi == "1497741409" and q.zip_code == "34986" and q.tin == "463812940"


class TestDecisiveness:
    def test_a_human_typed_plan_cannot_pin_and_so_cannot_license_an_oon(self):
        """The regression that motivates this module."""
        assert match_plan("AARP Medicare Advantage", UHC_FL_OPTIONS) is None
        assert plan_is_pinnable(_r271(selected_plan="AARP Medicare Advantage")) is False

    def test_a_real_271_pins_decisively_and_licenses_an_oon(self):
        r = _r271(selected_plan="AARP Medicare Advantage from UHC FL-0026 (PPO)",
                  plan_candidates=[{"contract": "H2406018000"}])
        assert plan_is_pinnable(r) is True
        m = match_plan(plan_string_from_271(r), UHC_FL_OPTIONS)
        assert m is not None
        assert m.label == "AARP Medicare Advantage Patriot from UHC FL-0026 (PPO)"
        assert m.confirms_network is True

    def test_pinnable_is_cheap_enough_to_gate_a_live_lookup(self):
        """Callers should be able to skip a ~60-120s live capture that provably cannot settle the row."""
        assert plan_is_pinnable(_r271()) is False
        assert plan_is_pinnable(_r271(plan_name="Cigna Commercial")) is False
        assert plan_is_pinnable(_r271(plan_name="Cigna Open Access Plus OAP/OA001 H1234")) is True
