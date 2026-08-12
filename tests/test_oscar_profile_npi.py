"""Oscar: open the matched provider's profile and confirm the NPI there.

WHAT THE EVIDENCE USED TO BE. A screenshot of the autocomplete dropdown — ten people sharing a
surname, "Wendy M Stone, William Albert Stone, Kathryn E Stone, Charles Stone, David G Stone, …" —
with the driver's verdict resting on "surname as whole tokens + first-name agreement". The note said
so out loud: "identity here is name-only; NPI 1548202799 is confirmed by the Oscar JSON adapter, not
by this screenshot." So the picture filed as proof did not contain the provider's identifier, and a
reader had to take the name match on trust.

THE DRIVER'S DOCSTRING SAID THAT WAS UNAVOIDABLE — "no NPI appears anywhere on the profile page or
in its source" — and that is no longer true. Measured live 2026-08-12, driving the real flow:

    click the matched suggestion row  ->  /people/-TMmY4rti8NwL0/6U4v3CvNmJPAV2N7aniQ/
    scroll to "Provider information"  ->  click "Show all"

        Medical group affiliation   SRINIVAS RAO MD PA, TEXAS UVC MEDICAL PLLC
        NPI                         1548202799
        State license number        N1163

Two traps, both measured. A COLD deep-link to /search/people/<id> bounces to /care-options, so the
profile is reachable only by clicking the row from inside the flow. And "Show all" must be matched
EXACTLY: the same page carries "See all locations (5)", which navigates away and loses the profile.
"""

from __future__ import annotations

import pytest

from network_probe.portal.drivers.oscar_care_options import OscarCareOptionsDriver
from network_probe.portal.models import PortalQuery

Q = PortalQuery(payer_key="oscar-tx-houston", npi="1548202799", provider_first_name="David",
                provider_last_name="Stone", state="TX", city="Houston", zip_code="77079")

# Verbatim from the live profile after "Show all" (2026-08-12).
PROFILE = (
    "Provider information\nSpecialties\nPhlebologist, Emergency Medicine Specialist\n"
    "Gender\nMale\nMedical group affiliation\nSRINIVAS RAO MD PA, TEXAS UVC MEDICAL PLLC\n"
    "Residency\nPrisma Health Richland Memorial Hospital\nNPI\n1548202799\n"
    "State license number\nN1163\nBoard certifications\nAmerican Board of Family Medicine\n"
)
STRANGER = PROFILE.replace("1548202799", "1999999984")


class _Loc:
    def __init__(self, page, name, n=1):
        self.page, self.name, self.n = page, name, n

    def count(self):
        return self.n

    def is_visible(self):
        return self.n > 0

    @property
    def first(self):
        return self

    def click(self, timeout=None):  # noqa: ARG002
        self.page.clicked.append(self.name)
        if self.name == "Show all":
            self.page.expanded = True
        if self.name == "See all locations (5)":
            self.page.body = "somewhere else entirely"

    def scroll_into_view_if_needed(self, timeout=None):  # noqa: ARG002
        return None


class _Page:
    url = "https://www.hioscar.com/people/-TMmY4rti8NwL0/6U4v3CvNmJPAV2N7aniQ/?networkId=064"

    def __init__(self, profile=PROFILE, has_show_all=True):
        self.profile, self.has_show_all = profile, has_show_all
        self.expanded = False
        self.clicked: list[str] = []
        self.body = None

    def inner_text(self, sel):  # noqa: ARG002
        if self.body is not None:
            return self.body
        # Before "Show all" the identifiers are simply not in the DOM.
        return self.profile if self.expanded else self.profile.split("Medical group")[0]

    def wait_for_timeout(self, ms):  # noqa: ARG002
        return None

    def get_by_role(self, role, name=None, exact=False):  # noqa: ARG002
        return _Loc(self, name, 1 if (name == "Show all" and self.has_show_all) else 0)

    def get_by_text(self, text, exact=False):  # noqa: ARG002
        return _Loc(self, text, 1)

    def locator(self, sel):  # noqa: ARG002
        return _Loc(self, "row", 1)

    def evaluate(self, *a, **k):
        return None


@pytest.fixture
def d():
    return OscarCareOptionsDriver()


class TestTheProfileConfirmsTheNpi:
    def test_the_matching_npi_is_read_off_the_profile(self, d):
        page = _Page()
        ok, why, _ = d._confirm_on_profile(page, Q, "/people/x", lambda label: f"{label}.png")
        assert ok is True
        assert "1548202799" in why

    def test_show_all_must_be_clicked_or_the_npi_is_not_there(self, d):
        """Before the expander the identifiers are not in the DOM at all — the whole point."""
        page = _Page(has_show_all=False)
        ok, why, _ = d._confirm_on_profile(page, Q, "/people/x", lambda label: f"{label}.png")
        assert ok is None, "no NPI on the page is UNKNOWN, never a mismatch"
        assert "Show all" in why or "no NPI" in why.lower()

    def test_it_never_clicks_see_all_locations(self, d):
        """That control navigates away and loses the profile — measured."""
        page = _Page()
        d._confirm_on_profile(page, Q, "/people/x", lambda label: f"{label}.png")
        assert "See all locations (5)" not in page.clicked
        assert "Show all" in page.clicked

    def test_a_different_npi_on_the_profile_is_a_MISMATCH_not_a_confirmation(self, d):
        """The namesake case the dropdown could never rule out: right surname, wrong doctor."""
        page = _Page(profile=STRANGER)
        ok, why, _ = d._confirm_on_profile(page, Q, "/people/x", lambda label: f"{label}.png")
        assert ok is False
        assert "1999999984" in why and "1548202799" in why

    def test_the_profile_screenshot_is_returned_as_the_evidence(self, d):
        page = _Page()
        _, _, png = d._confirm_on_profile(page, Q, "/people/x", lambda label: f"{label}.png")
        assert png and "profile" in png, "the filed picture must be the profile, not the dropdown"
