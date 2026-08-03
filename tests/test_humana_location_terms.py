"""Humana's location box rejected the only form the driver ever typed.

Caught on the demo path 2026-08-03. The field showed, in Humana's own words:

    Your location: FL 33618
    "Enter a valid address, city and state, or ZIP code."

`_commit_location` built exactly one term — state and ZIP joined by a space — and Google Places does
not accept that shape. It wants a ZIP on its own, or a comma-separated "City, ST". Worse, when no
suggestion was trusted the driver pressed Enter on the rejected text, which left the field in its
error state, so Continue never advanced and the walk died two steps later at a misleading
"type of care 'Medical' not clickable".

The bare ZIP is safe to try first even though Places reads a lone ZIP as a HOUSE NUMBER
("33618" -> "33618 Samuel Ivy Drive, Tampa, FL 33619"), because `_is_our_place` already refuses a
suggestion whose ZIP is not the one asked for. That guard is what "ST ZIP" was working around, and
it was never needed.

After the fix the same query reaches network selection: "portal scoped to: Carrollwood, FL 33618,
USA → network selection → 14 networks offered near 33618".
"""

from network_probe.portal.drivers.humana_finder import HumanaFinderDriver
from network_probe.portal.models import PortalQuery


def _terms(**kw):
    return HumanaFinderDriver()._location_terms(PortalQuery(payer_key="humana-fl", npi="1", **kw))


def test_the_rejected_state_zip_form_is_no_longer_tried_first():
    """"FL 33618" is what Places refused; it must not lead."""
    assert _terms(state="FL", zip_code="33618")[0] != "FL 33618"


def test_a_bare_zip_leads_when_there_is_no_city():
    assert _terms(state="FL", zip_code="33618")[0] == "33618"


def test_city_state_zip_leads_when_a_city_is_known():
    """The most precise form Places accepts, and the Clinic City field now supplies it."""
    assert _terms(state="FL", zip_code="33618", city="Tampa")[0] == "Tampa, FL 33618"


def test_the_old_form_is_kept_as_a_last_resort():
    """It did work sometimes — keep it, just never first."""
    assert "FL 33618" in _terms(state="FL", zip_code="33618")


def test_terms_are_unique_and_non_empty():
    t = _terms(state="FL", zip_code="33618", city="Tampa")
    assert len(t) == len(set(t))
    assert all(x and x.strip(", ") == x for x in t)


def test_a_market_suffixed_roster_state_is_reduced_to_two_letters():
    """Roster states carry a market suffix ("FL-Tampa"); typing that into Places matches nothing."""
    assert _terms(state="FL-Tampa", zip_code="33618")[0] == "33618"
    assert all("Tampa" not in x or x.startswith("Tampa") for x in _terms(state="FL-Tampa", zip_code="33618"))


def test_no_location_yields_no_terms():
    assert _terms() == []
