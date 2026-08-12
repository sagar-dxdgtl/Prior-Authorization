"""A CMS contract-PBP-segment typed into the identifier field IS a plan pin.

Sheet row 9 (Jose Chavez, Health Spring MCR, Arthur Maydell NPI 1992078745, Phoenix 85006) failed
with:

    the plan string None carries no CMS contract-PBP-segment, and this portal pins a network by
    that identifier alone.

The identifier was sitting in the request the whole time. The 8-12-26 sheet's only identifier column
is "Ins Group Number", and for that row it holds **H0354027000** — CMS contract H0354, PBP 027,
segment 000. That is not a member id; it is the exact plan pin `healthspring_phynd.cms_plan_id()`
already parses, and the portal's own URL takes it as `healthPlan=H0354-027-000`.

WHY THIS IS NARROW AND NOT "SWEEP THE GROUP FIELD IN". `portal/from_271.py` deliberately excludes
`EligibilityResult.group` because in this client's workbook that column also holds MEMBER
identifiers (`SRG########`, bare 12-digit runs), and the plan string is typed into a public payer
search box and persisted in the audit note — so sweeping the field would leak a member id into both.
Its docstring says: "If a genuine group number is ever needed for plan pinning, pass it explicitly
rather than sweeping the field in." This is that explicit path, and the CMS shape test is what makes
it safe: a value that is not a contract-PBP-segment yields nothing at all.
"""

from __future__ import annotations

import pytest

from network_probe.portal.from_271 import plan_pin_from_identifier


class TestOnlyACmsPlanIdEverEscapes:
    @pytest.mark.parametrize("value,expected", [
        ("H0354027000", "H0354-027-000"),          # sheet row 9, verbatim
        ("H0354-027-000", "H0354-027-000"),        # already punctuated
        ("h0354027000", "H0354-027-000"),          # normalised
        ("  H0354027000  ", "H0354-027-000"),
        ("R1234001000", "R1234-001-000"),          # regional PPO contracts
        ("S5601030000", "S5601-030-000"),          # PDP contracts
    ])
    def test_a_cms_contract_pbp_segment_is_recognised(self, value, expected):
        assert plan_pin_from_identifier(value) == expected

    @pytest.mark.parametrize("value", [
        "716365N00",        # BCBS SC group number (rows 2-5)
        "715940401",        # ditto
        "IG1301",           # BCBS IL group
        "78800511",         # Surest, 8 digits
        "SRG12345678",      # the workbook's alpha-prefixed MEMBER id shape
        "123456789012",     # the workbook's 12-digit MEMBER id shape
        "ZCS716365N00",     # a Blues member id WITH an alpha prefix
        "H0354",            # a contract alone pins no plan
        "",
        None,
    ])
    def test_everything_that_is_not_a_cms_plan_id_yields_nothing(self, value):
        """The shape test is the whole safety argument: a member id must never come back out."""
        assert plan_pin_from_identifier(value) is None

    def test_a_member_id_embedded_in_a_longer_string_is_not_mined_out(self):
        """Only a value that IS a plan id counts — this must not scan free text for one, or a note
        mentioning a contract would start pinning networks."""
        assert plan_pin_from_identifier("member H0354027000 of something") is None


class TestTheHealthspringDriverAcceptsIt:
    def test_the_pin_feeds_the_driver_that_asked_for_it(self):
        from network_probe.portal.drivers.healthspring_phynd import cms_plan_id

        assert cms_plan_id(plan_pin_from_identifier("H0354027000")) == "H0354-027-000"


class TestItReachesTheResultTheFrontendSends:
    """The frontend posts `plan: selected_plan ?? plan_name ?? null` to /api/portal/capture, so the
    pin has to land on `selected_plan` — and it must be labelled, because a plan an operator typed
    and a plan the payer named do not deserve the same confidence."""

    def _result(self, **kw):
        from network_probe.domain.benefits import EligibilityResult
        from network_probe.domain.models import NetworkStatus

        base = dict(
            coverage_active=None, plan_name=None, group=None, coverage_dates={},
            network_status=NetworkStatus.UNKNOWN, benefits=[], pcp_required=None,
            prior_auth_required=None, referral_required=None, cob=None,
            network_verdict=None, corroboration=[], source_audit={},
        )
        base.update(kw)
        return EligibilityResult(**base)

    def _run(self, member_id, **result_kw):
        from network_probe.domain import eligibility as el
        from network_probe.domain.models import ProviderQuery

        class _Src:
            def check(_self, q):
                return self._result(**result_kw)

        q = ProviderQuery(payer="healthspring", npi="1992078745", member_id=member_id,
                          first_name="Jose", last_name="Chavez", dob="2/12/1961",
                          plan_hint=None)
        return el.check_eligibility(q, stedi=_Src(), catalogue=_FakeCat())

    def test_row_9s_contract_id_becomes_the_plan_the_portal_pins(self):
        r = self._run("H0354027000")
        assert r.selected_plan == "H0354-027-000"
        assert r.plan_pin_source and "not the payer's 271" in r.plan_pin_source
        assert r.to_dict()["plan_pin_source"]

    def test_a_group_number_in_the_same_field_pins_nothing(self):
        """Rows 2-5 put a BCBS SC group number in this field. It must stay unpinnable."""
        assert self._run("716365N00").selected_plan is None

    def test_a_plan_the_payer_named_is_never_overwritten_or_relabelled(self):
        r = self._run("H0354027000", selected_plan="HealthSpring Preferred (HMO) H0354-001-000")
        assert r.selected_plan == "HealthSpring Preferred (HMO) H0354-001-000"
        assert r.plan_pin_source is None, "the payer named it; do not label it as typed"


class _FakeCat:
    def resolve(self, payer):
        return None
