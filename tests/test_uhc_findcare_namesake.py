"""UHC Find Care must not read a same-surname stranger as our provider.

Caught live against the Test 2 answer key, with the plan correctly pinned:

    our provider Stephanie Bui  -> matched "Tony BUIPain Management"          (staff: OON)
    our provider David Naar     -> matched "Benjamin L NAARChiropractic Med"  (staff: OON w/ Benefits)

Both returned a confident IN_NETWORK. The match was `surname in normalized(text)` — a substring
test that ignores the first name entirely, so any Bui matches any other Bui. It is the same defect
class removed from the Oscar adapter (P1) and the same one HealthSparq was hardened against.

Three outcomes, and the middle one is what keeps this honest:
  * first AND last name present      -> ours
  * same surname, DIFFERENT first    -> a namesake. Not ours, and not evidence of absence either:
                                        the portal may list ours under a form we did not match.
  * surname only, no first name      -> ambiguous, same treatment
"""

from network_probe.portal.drivers.uhc_findcare import UhcFindCareDriver
from network_probe.portal.models import PortalQuery


def _q(first, last):
    return PortalQuery(payer_key="unitedhealthcare-az", npi="1245461292",
                       provider_first_name=first, provider_last_name=last)


def test_a_same_surname_stranger_is_not_our_provider():
    """THE live failure: Stephanie Bui is not Tony Bui."""
    ours, amb = UhcFindCareDriver().identify(["Tony BUIPain Management"], _q("Stephanie", "Bui"))
    assert ours is None
    assert amb is True  # a namesake was seen — absence here is not evidence of out-of-network


def test_the_second_live_failure_too():
    ours, amb = UhcFindCareDriver().identify(
        ["Benjamin L NAARChiropractic Medicine"], _q("David", "Naar"))
    assert ours is None
    assert amb is True


def test_our_provider_is_matched_when_both_names_are_present():
    ours, amb = UhcFindCareDriver().identify(
        ["Stephanie BUIInternal Medicine"], _q("Stephanie", "Bui"))
    assert ours == "Stephanie BUIInternal Medicine"
    assert amb is False


def test_our_provider_is_found_among_namesakes():
    pool = ["Tony BUIPain Management", "Stephanie BUIInternal Medicine"]
    ours, _ = UhcFindCareDriver().identify(pool, _q("Stephanie", "Bui"))
    assert ours == "Stephanie BUIInternal Medicine"


def test_a_surname_only_listing_is_ambiguous_not_a_match():
    ours, amb = UhcFindCareDriver().identify(["BUI, S. — Family Medicine"], _q("Stephanie", "Bui"))
    assert ours is None
    assert amb is True


def test_an_unrelated_surname_is_neither_ours_nor_ambiguous():
    """Real absence: no one of that surname is listed, so the set genuinely lacks our provider."""
    ours, amb = UhcFindCareDriver().identify(["Conrad MANAYANGeneral Surgery"], _q("Stephanie", "Bui"))
    assert ours is None
    assert amb is False


def test_with_no_first_name_supplied_a_surname_match_stays_ambiguous():
    ours, amb = UhcFindCareDriver().identify(["Tony BUIPain Management"], _q(None, "Bui"))
    assert ours is None
    assert amb is True
