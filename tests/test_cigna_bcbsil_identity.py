"""Identity and badge regressions for the Cigna and BCBS-IL drivers.

Both defects here are the same two classes that the adversarial pass found in the other drivers, which
is the point of this file: they are systemic, not per-driver accidents.

  1. A same-surname stranger accepted as our provider -> false IN. Cigna's `_match` kept a
     `surname_only` fallback and returned it when nothing better appeared: verified, a query for
     Randall Orem matched "Orem, Jessica L, DO" and the caller turned that into IN_NETWORK.
  2. A network badge read positively because the positive phrase is a SUBSTRING of the negative one.
     BCBS-IL tested "out of network" but never "not in network", so `_badge("Not In Network")`
     returned "in" — and the caller treats any badge that is not "out" as in-network.

Neither portal exposes an NPI on a card, so the name is the only identity handle there is.
"""

from __future__ import annotations

import pytest

from network_probe.portal.drivers.bcbsil_provider_finder import BcbsilProviderFinderDriver
from network_probe.portal.drivers.cigna_hcp import CignaHcpDriver
from network_probe.portal.models import PortalQuery

# The two Ins Test 3 rows these drivers answer for.
OREM = PortalQuery(payer_key="cigna-healthcare-fl-south-florida", npi="1497741409",
                   provider_first_name="Randall", provider_last_name="Orem", zip_code="34986")
RAIKAR = PortalQuery(payer_key="bcbs-empire-anthem-elevance-hcsc-il", npi="1841308830",
                     provider_first_name="Bao Lan", provider_last_name="Raikar", zip_code="60031")


class TestCignaIdentity:
    @pytest.fixture
    def d(self):
        return CignaHcpDriver()

    def test_our_provider_matches(self, d):
        name, why = d._match(["Orem, Randall C, MD - Port St. Lucie, FL"], OREM)
        assert name is not None and why == "surname + first name"

    def test_same_surname_stranger_is_not_a_match(self, d):
        """The verified false IN. Jessica Orem is a different person."""
        assert d._match(["Orem, Jessica L, DO - Stuart, FL"], OREM) == (None, "no card matched")

    def test_initial_only_is_ambiguous_not_a_verdict(self, d):
        """"Orem, R C" could be Randall or Robert. Returning it is a false IN; calling it absent is a
        false OON while our provider may be the very row we skipped. It must decide nothing."""
        assert d._match(["Orem, R C, MD"], OREM) == (None, "ambiguous")

    def test_substring_surname_is_rejected(self, d):
        """Cigna's own typeahead answered "Orem" with "Shoaf, Noremi D" — "noremi" contains "orem"."""
        assert d._match(["Shoaf, Noremi D"], OREM) == (None, "no card matched")

    def test_ambiguity_does_not_mask_a_real_match(self, d):
        """A genuine match among namesakes still wins over the ambiguous one."""
        name, why = d._match(["Orem, R C, MD", "Orem, Randall C, MD"], OREM)
        assert name == "Orem, Randall C, MD" and why == "surname + first name"

    def test_missing_names_are_safe(self, d):
        assert d._match([], OREM)[0] is None
        assert d._match(["Orem, Randall C, MD"], PortalQuery(payer_key="x", npi="0"))[0] is None


class TestBcbsilBadge:
    @pytest.fixture
    def d(self):
        return BcbsilProviderFinderDriver()

    @pytest.mark.parametrize("text", [
        "Not In Network", "not in network", "NOT IN NETWORK", "not in-network",
        "Out of Network", "out-of-network", "Non-Network", "non-participating",
        "Raikar, Bao Lan, MD - Not In Network - Gurnee, IL 60031",
    ])
    def test_every_negative_phrasing_reads_out(self, d, text):
        """The caller treats any badge that is not "out" as in-network, so a missed negative is a
        false IN asserted over the payer's own contrary label."""
        assert d._badge(text) == "out", f"{text!r} must read as out-of-network"

    @pytest.mark.parametrize("text", ["In Network", "in-network", "IN NETWORK",
                                      "Raikar, Bao Lan, MD - In-network - Gurnee, IL"])
    def test_positive_badges_still_read_in(self, d, text):
        assert d._badge(text) == "in"

    def test_no_badge_is_not_a_verdict(self, d):
        assert d._badge("Raikar, Bao Lan, MD - Gurnee, IL 60031") is None
        assert d._badge("") is None


class TestBcbsilIdentity:
    @pytest.fixture
    def d(self):
        return BcbsilProviderFinderDriver()

    def test_the_real_namesake_from_the_verified_run(self, d):
        """The live run's wide search returned Sanjay V. Raikar for Bao-Lan Raikar."""
        assert d._match([("Sanjay V. Raikar, MD", "card")], RAIKAR)[0] is None

    def test_our_provider_matches(self, d):
        got, why = d._match([("Raikar, Bao Lan, MD", "In-network · Gurnee, IL")], RAIKAR)
        assert got is not None and why == "surname + first name"

    def test_initial_only_is_ambiguous(self, d):
        assert d._match([("Raikar, B, MD", "card")], RAIKAR) == (None, "ambiguous")
