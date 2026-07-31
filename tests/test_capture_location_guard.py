"""A walk with no clinic location must be refused before a browser is ever launched.

Every one of the nine portals gates provider search behind a committed location, so a query with no
ZIP, city or state cannot produce an answer from any of them. Three drivers (Aetna, Cigna,
HealthSparq) already return UNKNOWN for it — but only after navigating, and UHC Find Care had no
guard at all: a live Dual Complete walk for NPI 1245461292 with no ZIP spent ~120s typing a surname
into a portal whose plan list never populates without a location, then screenshotted an empty box.

Refusing in run_capture instead makes the outcome identical across drivers, costs no browser, and
never files a screenshot that looks like a searched-and-absent result.
"""

import pytest

from network_probe.portal import capture as cap
from network_probe.portal.models import PortalQuery, PortalStatus

AZ_UHC = "unitedhealthcare-az"
AZ_BLUE = "bcbs-empire-anthem-elevance-az"  # HealthSparq: searches on ZIP *or* city *or* state
NPI = "1245461292"


def _no_browser(monkeypatch):
    """Fail loudly if anything tries to open a browser."""

    def boom(*a, **k):
        raise AssertionError("a browser was launched for a query that carries no location")

    monkeypatch.setattr(cap.pb, "browser_session", boom)


def _q(**kw):
    return PortalQuery(payer_key=AZ_UHC, npi=NPI, provider_last_name="Bui",
                       plan="UHC Medicare Dual Complete AZMCARE", **kw)


def test_no_location_is_refused_without_launching_a_browser(monkeypatch):
    _no_browser(monkeypatch)
    c = cap.run_capture(_q())
    assert c.status is PortalStatus.UNKNOWN
    assert c.screenshot is None, "an unsearched query must not file a screenshot as evidence"
    assert "location" in (c.note or "").lower()
    assert c.driver == "uhc-findcare", "the driver is still named, so the UI can say which portal"


@pytest.mark.parametrize("loc", [{"state": "AZ"}, {"city": "Phoenix"}])
def test_uhc_refuses_a_query_with_no_zip_even_when_it_has_a_state(monkeypatch, loc):
    """The hole a live run found: UHC's guard used to accept ZIP *or* city *or* state, but the driver
    only ever types the ZIP. A state-only query therefore sailed past and spent ~120s on a portal it
    could not search. Drivers declare which fields they can actually use."""
    _no_browser(monkeypatch)
    c = cap.run_capture(_q(**loc))
    assert c.status is PortalStatus.UNKNOWN
    assert "ZIP" in (c.note or "")


@pytest.mark.parametrize("loc", [{"zip_code": "85382"}, {"city": "Phoenix"}, {"state": "AZ"}])
def test_healthsparq_accepts_any_one_location_field(monkeypatch, loc):
    """HealthSparq genuinely searches on ZIP *or* city *or* state, so the guard must not demand a ZIP
    of every portal — that would refuse checks the driver could have answered."""
    launched = {}

    class Sentinel(Exception):
        """Raised in place of opening a browser, so the test never touches a real portal."""

    def fake_session(*a, **k):
        launched["yes"] = True
        raise Sentinel

    monkeypatch.setattr(cap.pb, "browser_session", fake_session)
    # run_capture does not wrap the browser session, so the sentinel escapes; the job store is what
    # turns an infrastructure failure into a failed job. All this asserts is that the guard let it by.
    with pytest.raises(Sentinel):
        cap.run_capture(PortalQuery(payer_key=AZ_BLUE, npi=NPI, provider_last_name="Maydell",
                                    plan="Statewide / National PPO", **loc))
    assert launched.get("yes"), f"guard wrongly refused a query carrying {loc}"
