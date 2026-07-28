"""Picking the RIGHT catalogue row when a payer key sells several lines of business.

Six roster keys carry four `benefit_type` rows each — `bcbs-empire-anthem-elevance-hcsc-il` is
ACA + Commercial + Managed Medicaid + Medicare Advantage under one key. `DbPayerCatalogue.resolve`
returns the FIRST match, which is correct for `stedi_payer_id` (consistent per payer) and wrong for
`benefit_type` (differs per row).

The consequence is silent and demo-visible: that key resolved to **Managed Medicaid**, so
`line_of_business` marked TiC federally exempt and never consulted it — and Ins Test 3 row 5, whose
NPI *and* TIN are both in BCBS IL's in-network MRF, rendered UNKNOWN instead of IN_NETWORK. The
same arbitrary pick could equally have gone the other way and fired TiC on a genuine Medicaid
member, which is the over-claiming direction.

The member's plan decides the line. These tests pin that.
"""

from types import SimpleNamespace

import pytest

from network_probe.domain.benefit_type import benefit_type_for


class _Cat:
    """Catalogue double: one payer key, several benefit_type rows, in DB order."""

    def __init__(self, key, benefit_types):
        self._key = key
        self._rows = [SimpleNamespace(key=key, label=key, benefit_type=b) for b in benefit_types]

    def rows_for(self, payer_key):
        return list(self._rows) if payer_key == self._key else []


IL = _Cat("bcbs-il", ["ACA", "Commercial", "Managed Medicaid", "Medicare Advantage"])


def test_a_commercial_plan_selects_the_commercial_row():
    """Row 5. Without this the key resolves to Managed Medicaid and TiC is skipped as exempt."""
    assert benefit_type_for("bcbs-il", "BCBS IL Blue Choice PPO", catalogue=IL) == "Commercial"


def test_a_medicare_advantage_plan_selects_the_medicare_row():
    assert benefit_type_for("bcbs-il", "Blue Cross Medicare Advantage PPO H1234",
                            catalogue=IL) == "Medicare Advantage"


def test_a_medicaid_plan_selects_the_medicaid_row():
    assert benefit_type_for("bcbs-il", "Blue Cross Community Health Plans Medicaid",
                            catalogue=IL) == "Managed Medicaid"


def test_an_aca_plan_selects_an_aca_or_commercial_row():
    """ACA and Commercial are both TiC-eligible, so either is a correct answer here."""
    assert benefit_type_for("bcbs-il", "Blue Choice Preferred Silver marketplace plan",
                            catalogue=IL) in ("ACA", "Commercial")


def test_no_plan_keeps_the_catalogue_order_and_does_not_invent_a_line():
    """With nothing to disambiguate on, behave exactly as before — first row wins. Guessing a line
    here is what produces either a silently suppressed TiC or a false IN."""
    assert benefit_type_for("bcbs-il", None, catalogue=IL) == "ACA"


def test_a_single_row_key_is_returned_unchanged():
    one = _Cat("oscar-ga-atlanta", ["ACA"])
    assert benefit_type_for("oscar-ga-atlanta", "anything at all", catalogue=one) == "ACA"


def test_an_unknown_payer_is_none_not_an_error():
    assert benefit_type_for("no-such-payer", "some plan", catalogue=IL) is None


@pytest.mark.parametrize("plan", ["", "   ", None])
def test_blank_plans_are_treated_as_no_plan(plan):
    assert benefit_type_for("bcbs-il", plan, catalogue=IL) == "ACA"
