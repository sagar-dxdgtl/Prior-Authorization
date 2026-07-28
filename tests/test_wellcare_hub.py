"""Verdict, identity and badge regressions for the Wellcare (Centene hub) driver.

Every test here pins one defect an adversarial review proved in the first cut of the driver, and all
five are the same family as the ones already pinned for Cigna/BCBS-IL in
tests/test_cigna_bcbsil_identity.py: the capture note claimed something the code had not established,
and the claim was an OUT_OF_NETWORK or an IN_NETWORK about a real provider.

The verdict logic is exercised directly with synthetic evidence records (`_leg_a_blank` /
`_leg_b_blank`), which is the point of `_verdict` being pure — a false-OON branch must be reachable
in a unit test rather than only against a live payer portal.

The card texts are the ones the portal really returned on 2026-07-28 for the Ins Test 3 Wellcare row
(Manayan, Conrad, NPI 1902811656, Kennesaw GA 30144): the surname "Manayan" answered with MANAN B
SHAH and, in the hub typeahead, with REX C MANAYAN of Ontario CA — neither of them our provider.
"""

from __future__ import annotations

import pytest

from network_probe.portal.drivers.wellcare_hub import (
    WellcareHubDriver,
    _leg_a_blank,
    _leg_b_blank,
)
from network_probe.portal.models import PortalCapture, PortalQuery, PortalStatus

MANAYAN = PortalQuery(
    payer_key="wellcare-centene-ga-atlanta", npi="1902811656",
    provider_first_name="Conrad", provider_last_name="Manayan",
    plan="Wellcare Medicare Advantage (GA)", state="GA-Atlanta", city="Kennesaw", zip_code="30144",
)

# The one card Wellcare's plan-agnostic lookup returned for the surname control, verbatim.
SHAH_CARD = (
    "MANAN B SHAH, MD\nInternal Medicine, Board Certified\nNPI: 1457548513\n"
    "3280 HOWELL MILL RD, STE T100, ATLANTA, GA 30327\n16.40 miles away"
)
OUR_CARD = (
    "MANAYAN, CONRAD B, MD\nFamily Medicine\nNPI: 1902811656\n"
    "3525 GEORGE BUSBEE DR NW, STE 101, KENNESAW, GA 30144\n0.20 miles away"
)


@pytest.fixture
def d() -> WellcareHubDriver:
    return WellcareHubDriver()


def _verdict(d: WellcareHubDriver, q: PortalQuery, **kw) -> PortalCapture:
    """Run `_verdict` over an explicit evidence record, exactly as `capture()` does."""

    def result(status: PortalStatus, note: str, **extra) -> PortalCapture:
        return PortalCapture(payer_key=q.payer_key, npi=q.npi, plan=q.plan, status=status,
                             portal_name=d.portal_name, portal_url="https://portal", driver=d.key,
                             note=note, **extra)

    args: dict = {"pinned": None, "confirmed": False, "banner": None, "offered": [],
                  "a": _leg_a_blank(), "b": _leg_b_blank()}
    args.update(kw)
    return d._verdict(result, lambda name: f"{name}.png", q, **args)


def _leg_a(**over) -> dict:
    """A leg-A record for a *general* (searchAll) search that legitimately licenses an absence:
    populated, fully read, cards inspected, and the plan's own network shown to hold providers."""
    a = {**_leg_a_blank(), "reached": True, "kind": "searchAll", "term": "Manayan",
         "in_radius": 2, "in_network": 2, "cards": [SHAH_CARD, "OTHER, PERSON, MD"], "cards_read": 2,
         "identity": "no card matched", "npi_filter_zero": True}
    a.update(over)
    return a


def _leg_b(**over) -> dict:
    """A leg-B record where every gate for the decisive all-plans absence is satisfied."""
    b = {**_leg_b_blank(), "reached": True, "count": 0, "location_ok": True, "line_ok": True,
         "location_echo": "Kennesaw, GA, USA", "liveness": 1, "liveness_cards_read": 1,
         "liveness_complete": True, "control_npi": "1457548513", "npi_index_proven": True,
         "detail": "0 for the NPI, control NPI returned its provider"}
    b.update(over)
    return b


class TestIdentity:
    """`_identity` — the only thing standing between a same-surname stranger and a false IN."""

    def test_our_provider_matches_on_surname_and_first_name(self, d):
        card, why = d._identity([OUR_CARD], MANAYAN)
        assert card == OUR_CARD and why == "the NPI the portal printed on the card"

    def test_printed_npi_matches_even_when_the_name_is_formatted_differently(self, d):
        """The NPI is the portal's own identity assertion and outranks any name formatting."""
        card, why = d._identity(["CONRAD MANAYAN JR, MD\nNPI: 1902811656\nKENNESAW, GA"], MANAYAN)
        assert card is not None and why == "the NPI the portal printed on the card"

    def test_name_only_card_still_matches(self, d):
        card, why = d._identity(["Manayan, Conrad B, MD\nFamily Medicine\nKENNESAW, GA"], MANAYAN)
        assert card is not None and why == "surname + first name"

    def test_the_real_live_control_card_is_not_our_provider(self, d):
        """Verified live: the lookup answered "Manayan" with MANAN B SHAH. If that card counted as
        ours the driver would report IN_NETWORK for a stranger — and, worse, would treat the same
        card as proof its liveness control was valid while claiming our provider was absent."""
        assert d._identity([SHAH_CARD], MANAYAN) == (None, "no card matched")

    def test_a_different_first_name_with_a_compatible_middle_initial_is_not_ambiguous(self, d):
        """The hub typeahead offered REX C MANAYAN (Ontario CA) for this surname. The "C" is
        compatible with Conrad, but the card names Rex — that is someone else, not an undecidable
        card, and calling it ambiguous would throw away an otherwise decisive capture."""
        assert d._identity(["REX C MANAYAN, MD\nOntario, CA"], MANAYAN) == (None, "no card matched")

    def test_initial_only_is_ambiguous_and_decides_nothing(self, d):
        """"MANAYAN, C, MD" could be Conrad or Carlos. Returning it is a false IN; treating it as
        absent is a false OON while our provider may be the very card that was skipped."""
        assert d._identity(["MANAYAN, C, MD\nKennesaw, GA"], MANAYAN) == (None, "ambiguous")

    def test_substring_surname_is_rejected(self, d):
        """Token-wise, never substring: "AMANAYAN" contains "MANAYAN"."""
        assert d._identity(["AMANAYAN, CONRAD, MD"], MANAYAN) == (None, "no card matched")

    def test_only_the_name_line_is_matched_not_the_address(self, d):
        """The live card carries "3280 HOWELL MILL RD". Matching the whole card would report a
        provider named Mill as present because the *stranger's* street matched."""
        mill = PortalQuery(payer_key="wellcare-centene-ga-atlanta", npi="1999999999",
                           provider_first_name="Conrad", provider_last_name="Mill")
        assert d._identity([SHAH_CARD], mill) == (None, "no card matched")

    def test_a_real_match_still_wins_over_an_ambiguous_namesake(self, d):
        card, why = d._identity(["MANAYAN, C, MD", "Manayan, Conrad B, MD"], MANAYAN)
        assert card == "Manayan, Conrad B, MD" and why == "surname + first name"

    def test_missing_names_are_safe(self, d):
        assert d._identity([], MANAYAN)[0] is None
        assert d._identity([OUR_CARD], PortalQuery(payer_key="x", npi="0")) == (
            None, "no surname to match")


class TestBadge:
    """Network badges: a missed negative phrasing is a false IN asserted over the payer's own label."""

    @pytest.mark.parametrize("text", [
        "Not In Network", "not in network", "NOT IN-NETWORK", "not-in-network",
        "Out of Network", "out-of-network", "Non-Network", "non-participating",
        "MANAYAN, CONRAD B, MD — Not In Network — Kennesaw, GA 30144",
    ])
    def test_every_negative_phrasing_reads_out(self, d, text):
        """"not in network" CONTAINS "in network", so a positive-first substring test inverts the
        payer's answer. Two sibling drivers shipped that inversion."""
        assert d._badge(text) == "out", f"{text!r} must read as out-of-network"

    @pytest.mark.parametrize("text", ["In Network", "in-network", "Provider is in-network with 10 plans"])
    def test_positive_badges_still_read_in(self, d, text):
        assert d._badge(text) == "in"

    def test_no_badge_is_not_a_verdict(self, d):
        assert d._badge(SHAH_CARD) is None
        assert d._badge("") is None and d._badge(None) is None

    def test_a_negative_badge_beats_mere_presence(self, d):
        """The portal's exact NPI filter returned our provider, but its own card says not in network.
        Presence must not outvote the payer's label."""
        cap = _verdict(d, MANAYAN, pinned="Wellcare Giveback (HMO-POS) - H1112", confirmed=True,
                       banner="Wellcare Giveback (HMO-POS) - Kennesaw, GA (2026)",
                       a={**_leg_a_blank(), "reached": True, "kind": "npi", "in_radius": 1,
                          "in_network": 0, "cards_read": 1, "card": OUR_CARD + "\nNot In Network",
                          "identity": "the NPI the portal printed on the card", "badge": "out"})
        assert cap.status is PortalStatus.OUT_OF_NETWORK
        assert "NOT in-network" in cap.note


class TestNpiFilteredCountIsNotAGeneralResultSet:
    """DEFECT 3: `a_populated and confirmed` fired while `a` was still the exact-NPI-filter search, so
    ">0 results for this NPI" — the portal saying our provider IS listed — produced an
    OUT_OF_NETWORK whose note claimed the results were for the surname."""

    def test_a_populated_npi_filter_never_becomes_an_out_of_network(self, d):
        """The reachable false OON. The count came from `filters-npiFilter=<our npi>`; a hit there is
        evidence of presence, and it can never be evidence of absence."""
        cap = _verdict(d, MANAYAN, pinned="Wellcare Giveback (HMO-POS) - H1112", confirmed=True,
                       banner="Wellcare Giveback (HMO-POS) - Kennesaw, GA (2026)",
                       a={**_leg_a_blank(), "reached": True, "kind": "npi", "term": MANAYAN.npi,
                          "in_radius": 1, "in_network": 1, "cards": ["UNREADABLE CARD"],
                          "cards_read": 1, "identity": "no card matched"})
        assert cap.status is PortalStatus.UNKNOWN
        assert "exact-NPI filter returned 1 result(s)" in cap.note
        # And it must not describe that count as a name search, which is what the old note did.
        assert "result(s) for 'Manayan'" not in cap.note

    def test_a_general_search_absence_is_still_allowed_to_be_decisive(self, d):
        """The fix must not make the driver useless: a populated, fully-read, identity-checked
        surname set inside an identifier-confirmed plan is exactly what doctrine calls an OON."""
        cap = _verdict(d, MANAYAN, pinned="Wellcare Giveback (HMO-POS) - H1112", confirmed=True,
                       banner="Wellcare Giveback (HMO-POS) - Kennesaw, GA (2026)", a=_leg_a())
        assert cap.status is PortalStatus.OUT_OF_NETWORK
        assert "all 2 cards were read and none is NPI 1902811656" in cap.note
        # The "M providers in-network" figure is the page's own liveness line, not a property of the
        # returned cards (live: "1 Result(s) found" next to "60 providers in-network"), so the note
        # may only claim it as the plan's directory being populated here.
        assert "provider(s) in-network for this plan within the radius" in cap.note

    def test_a_truncated_result_set_is_unknown(self, d):
        """The portal counts results but shows a page of them and this layer never pages, so absence
        from page 1 of N is not absence."""
        cap = _verdict(d, MANAYAN, pinned="P", confirmed=True, banner="P - Kennesaw, GA",
                       a=_leg_a(in_radius=25, cards_read=2, truncated=True))
        assert cap.status is PortalStatus.UNKNOWN and "truncated" in cap.note

    def test_a_result_set_with_no_in_network_providers_is_unknown(self, d):
        """"0 providers in-network" means the set does not evidence that plan's network at all, so
        our absence from it evidences nothing either."""
        cap = _verdict(d, MANAYAN, pinned="P", confirmed=True, banner="P - Kennesaw, GA",
                       a=_leg_a(in_network=0))
        assert cap.status is PortalStatus.UNKNOWN

    def test_an_unconfirmed_plan_can_never_produce_an_out_of_network(self, d):
        """Doctrine: no confirmed plan -> UNKNOWN. A representative product of a line-level plan
        string is not the member's product."""
        cap = _verdict(d, MANAYAN, pinned="P", confirmed=False, banner="P - Kennesaw, GA", a=_leg_a())
        assert cap.status is PortalStatus.UNKNOWN
        assert "representative product" in cap.note

    def test_the_zero_of_an_empty_pinned_search_is_unknown(self, d):
        cap = _verdict(d, MANAYAN, pinned="P", confirmed=True, banner="P - Kennesaw, GA",
                       a={**_leg_a_blank(), "reached": True, "kind": "npi", "in_radius": 0,
                          "identity": "no card matched"})
        assert cap.status is PortalStatus.UNKNOWN and "empty set" in cap.note


class TestDecisiveLegNeedsAnNpiControl:
    """DEFECT 1: the leg-B absence reading rested on an NPI query returning 0, while the control that
    licensed believing that zero was a SURNAME query. A surname control cannot distinguish "this
    provider is absent" from "this field does not index NPIs at all" — and under the second reading
    every provider on earth is out-of-network.

    The gates are asserted on `_lookup_absence_is_real` directly, because leg B's zero is no longer
    allowed to be a verdict at all (see TestLegBZeroIsNeverAVerdict): the predicate is what decides
    whether the absence may even be *reported* as real, so it is where the control logic must be
    pinned."""

    def test_a_surname_control_alone_does_not_license_the_zero(self, d):
        """Exactly the old behaviour: count==0 for the NPI, surname control returned results, no NPI
        query was ever shown to work. The old code called this an absence and returned OON."""
        b = _leg_b(npi_index_proven=False, control_npi=None,
                   detail="no card printed an NPI, so no NPI query could be shown to work")
        assert d._lookup_absence_is_real(b) is False
        assert _verdict(d, MANAYAN, b=b).status is PortalStatus.UNKNOWN

    def test_a_control_npi_that_does_not_come_back_leaves_the_zero_meaningless(self, d):
        b = _leg_b(npi_index_proven=False, detail="the control NPI did not come back either")
        assert d._lookup_absence_is_real(b) is False
        assert _verdict(d, MANAYAN, b=b).status is PortalStatus.UNKNOWN

    def test_a_proven_npi_index_makes_the_absence_real_within_that_scope(self, d):
        """With the control being the same query type — an NPI the portal itself printed, returned by
        the same field at the same location and radius — the zero is a real absence from whatever
        that field searched. What it searched is a separate question, answered by the class below."""
        b = _leg_b()
        assert d._lookup_absence_is_real(b) is True
        cap = _verdict(d, MANAYAN, b=b)
        assert "1457548513" in cap.note and "live NPI index" in cap.note

    def test_a_plan_from_a_line_this_deployment_does_not_sell_is_not_a_real_absence(self, d):
        """Wellcare is Centene's MEDICARE brand; Ambetter and Fidelis are separate deployments. A
        Medicaid member's absence from the Medicare lookup would be a fabricated OON."""
        medicaid = PortalQuery(payer_key="wellcare-centene-nj", npi=MANAYAN.npi,
                               provider_first_name="Conrad", provider_last_name="Manayan",
                               plan="Fidelis Managed Medicaid (NJ)", state="NJ", city="Newark")
        cap = _verdict(d, medicaid, b=_leg_b(line_ok=False, detail="wrong line of business"))
        assert cap.status is PortalStatus.UNKNOWN

    def test_an_unconfirmed_location_is_unknown(self, d):
        """Google Places offered "30144 60th Ave S, Auburn, WA" first; a zero from Washington is not
        a zero at the clinic."""
        cap = _verdict(d, MANAYAN, b=_leg_b(location_ok=False, location_echo="Auburn, WA, USA",
                                            detail="searched the wrong place"))
        assert cap.status is PortalStatus.UNKNOWN


class TestLegBZeroIsNeverAVerdict:
    """MEASURED 2026-07-28, and it overturned this driver's original design. Leg B's UI says it checks
    whether a provider is "in-network" and its no-results copy says "may not be in-network with any of
    our plans", so an earlier cut treated a well-controlled zero there as decisive across all products.
    The request it actually issues is scoped:

        POST external-api.search.my.centene.com/pces/query?index=CEL
        {"values": {"field": "network.product.networkId",
                    "values": ["15294","14126","13191","13269","13193","13267"]}}

    Six networkIds of unmeasured extent — not the pinned product (leg B is never told one), but not
    provably every product either, and /productmapping/v2/v2/query answered {"networks":[]}. So the
    zero cannot be turned into a statement about the member's plan, and the only OON this driver
    produces is one scoped to an identifier-confirmed product."""

    def test_a_perfectly_controlled_lookup_zero_is_still_unknown(self, d):
        b = _leg_b()
        assert d._lookup_absence_is_real(b) is True  # the absence is real…
        cap = _verdict(d, MANAYAN, pinned="Wellcare Assist (HMO) - H1112", offered=["a", "b"], b=b)
        assert cap.status is PortalStatus.UNKNOWN  # …but not about the member's product
        assert "six network ids" in cap.note
        assert "representative product" in cap.note

    def test_the_note_still_reports_the_absence_it_did_establish(self, d):
        """Downgrading must not throw the finding away — a reviewer needs to see that the payer's own
        NPI index has no record of this provider at this location."""
        cap = _verdict(d, MANAYAN, b=_leg_b())
        assert "0 results for NPI 1902811656" in cap.note
        assert "live NPI index" in cap.note and "control NPI 1457548513 came back" not in cap.note

    def test_it_corroborates_a_confirmed_product_verdict_without_deciding_it(self, d):
        """Where the plan identifier DID confirm the product, leg A decides and leg B is cited as
        corroboration — described as its own six-network scope, never as "all plans"."""
        cap = _verdict(d, MANAYAN, pinned="Wellcare Giveback (HMO-POS) - H1112", confirmed=True,
                       banner="Wellcare Giveback (HMO-POS) - Kennesaw, GA (2026)",
                       a=_leg_a(), b=_leg_b())
        assert cap.status is PortalStatus.OUT_OF_NETWORK
        assert "corroborates rather than decides" in cap.note
        assert "all Wellcare products" not in cap.note

    def test_no_branch_anywhere_turns_a_lookup_zero_into_an_out_of_network(self, d):
        """A sweep, because this is the property the whole downgrade rests on: with leg A unable to
        speak (no product pinned, or pinned but unconfirmed), no combination of leg-B gates may
        produce an OON."""
        for pinned, confirmed in ((None, False), ("Wellcare Assist (HMO)", False)):
            for over in ({}, {"liveness": 9, "liveness_cards_read": 9},
                         {"npi_index_proven": True, "liveness_complete": True}):
                cap = _verdict(d, MANAYAN, pinned=pinned, confirmed=confirmed, b=_leg_b(**over))
                assert cap.status is not PortalStatus.OUT_OF_NETWORK, (pinned, confirmed, over)


class TestControlResultsMustBeInspected:
    """DEFECT 2: `_lb_research` returned only a count, so a control search that had returned OUR OWN
    provider still counted as proof the index was live — and the note then said our provider was
    absent from it. The two claims contradict each other."""

    # Asserted on the gate itself, not only on the resulting status: since leg B's zero can no longer
    # be a verdict, every one of these already answers UNKNOWN, so a status-only assertion would pass
    # even with the defect restored and would stop pinning anything.

    def test_a_control_that_returned_our_provider_cannot_prove_our_absence(self, d):
        b = _leg_b(liveness_has_us=True, liveness_match="MANAYAN, CONRAD B, MD",
                   detail="the control returned this very provider")
        assert d._lookup_absence_is_real(b) is False
        assert _verdict(d, MANAYAN, b=b).status is PortalStatus.UNKNOWN

    def test_an_ambiguous_control_card_cannot_prove_our_absence(self, d):
        b = _leg_b(liveness_ambiguous=True, detail="the control card carries only an initial")
        assert d._lookup_absence_is_real(b) is False
        assert _verdict(d, MANAYAN, b=b).status is PortalStatus.UNKNOWN

    def test_a_truncated_control_set_cannot_be_said_not_to_contain_us(self, d):
        b = _leg_b(liveness=40, liveness_cards_read=10, liveness_complete=False,
                   detail="control not read in full")
        assert d._lookup_absence_is_real(b) is False
        assert _verdict(d, MANAYAN, b=b).status is PortalStatus.UNKNOWN

    def test_an_empty_control_leaves_two_empty_sets(self, d):
        b = _leg_b(liveness=0, liveness_cards_read=0, liveness_complete=False, control_npi=None,
                   npi_index_proven=False, detail="two empty sets")
        assert d._lookup_absence_is_real(b) is False
        assert _verdict(d, MANAYAN, b=b).status is PortalStatus.UNKNOWN


class TestTheNoteClaimsOnlyWhatWasEstablished:
    """DEFECT 4: the second hub search overwrote leg A with `found=False, found_name=None` without
    reading a single card, and the note then asserted "N result(s) for <surname>, none this NPI" — a
    claim about cards nothing had inspected. This was the recurring failure across six drivers."""

    def test_the_pinned_leg_note_states_that_the_cards_were_read(self, d):
        cap = _verdict(d, MANAYAN, pinned="P", banner="P - Kennesaw, GA (2026)", a=_leg_a(), b=_leg_b())
        assert cap.status is PortalStatus.UNKNOWN  # unconfirmed product; leg B cannot decide either
        assert "all 2 of its cards were read and none is this provider" in cap.note
        assert "no card matched" in cap.note

    def test_a_truncated_pinned_set_is_reported_as_contributing_nothing(self, d):
        """The old note's "none this NPI" over an unread page becomes an explicit non-claim."""
        cap = _verdict(d, MANAYAN, pinned="P", banner="P - Kennesaw, GA (2026)",
                       a=_leg_a(in_radius=25, cards_read=2, truncated=True), b=_leg_b())
        assert "not read in full and contributes nothing" in cap.note

    def test_an_unidentifiable_npi_filter_hit_is_reported_not_relied_on(self, d):
        cap = _verdict(d, MANAYAN, pinned="P", banner="P - Kennesaw, GA (2026)",
                       a={**_leg_a_blank(), "reached": True, "kind": "npi", "in_radius": 1,
                          "in_network": 1, "cards_read": 1, "identity": "no card matched"},
                       b=_leg_b())
        assert "reported, not relied on" in cap.note

    def test_the_two_legs_disagreeing_is_reported_as_disagreement(self, d):
        """The exact NPI filter said 0 while the widened name search returned a card we identify as
        this provider. Neither an IN nor an OON survives that, and the note must say so."""
        cap = _verdict(d, MANAYAN, pinned="P", confirmed=True, banner="P - Kennesaw, GA (2026)",
                       a=_leg_a(card=OUR_CARD, identity="surname + first name"))
        assert cap.status is PortalStatus.UNKNOWN
        assert "disagrees with itself" in cap.note

    def test_a_walk_that_served_nothing_is_blocked_not_out_of_network(self, d):
        cap = _verdict(d, MANAYAN, b={**_leg_b_blank(), "detail": "lookup page unreachable: TimeoutError"})
        assert cap.status is PortalStatus.BLOCKED


class TestPlanListedIsNotAStringEquality:
    """DEFECT 5b: "is the pinned product among the provider's in-network plans" was a normalised
    string equality, and `_norm("… (HMO-POS)") == _norm("… (HMO-POS) - H1112")` is False — the
    portal's own trailing contract number made a covered product look uncovered, which the verdict
    then turned into an OUT_OF_NETWORK."""

    ROWS = ["Wellcare Giveback (HMO-POS) - H1112", "Wellcare Dual Access (HMO D-SNP) - H1112"]

    def test_the_same_product_with_a_contract_suffix_still_counts_as_listed(self, d):
        assert d._plan_listed(self.ROWS, "Wellcare Giveback (HMO-POS)") is True

    def test_an_identifier_match_counts_as_listed(self, d):
        assert d._plan_listed(["Wellcare Patriot (HMO) - H1112-018"], "H1112018000") is True

    def test_a_different_product_is_not_listed(self, d):
        assert d._plan_listed(self.ROWS, "Wellcare No Premium Open (PPO)") is False

    def test_a_covered_provider_is_in_network_not_out(self, d):
        cap = _verdict(d, MANAYAN, pinned="Wellcare Giveback (HMO-POS)", confirmed=True,
                       banner="Wellcare Giveback (HMO-POS) - Kennesaw, GA (2026)",
                       b=_leg_b(count=1, npi_found=True, matched_name="MANAYAN, CONRAD B, MD",
                                plans=self.ROWS, plan_count=2, plans_complete=True))
        assert cap.status is PortalStatus.IN_NETWORK

    def test_an_incompletely_read_plan_list_cannot_exclude_the_members_product(self, d):
        cap = _verdict(d, MANAYAN, pinned="Wellcare No Premium Open (PPO)", confirmed=True,
                       banner="Wellcare No Premium Open (PPO) - Kennesaw, GA (2026)",
                       b=_leg_b(count=1, npi_found=True, matched_name="MANAYAN, CONRAD B, MD",
                                plans=self.ROWS, plan_count=10, plans_complete=False))
        assert cap.status is PortalStatus.UNKNOWN and "not read in full" in cap.note

    def test_a_complete_plan_list_without_the_members_product_is_out_of_network(self, d):
        cap = _verdict(d, MANAYAN, pinned="Wellcare No Premium Open (PPO)", confirmed=True,
                       banner="Wellcare No Premium Open (PPO) - Kennesaw, GA (2026)",
                       b=_leg_b(count=1, npi_found=True, matched_name="MANAYAN, CONRAD B, MD",
                                plans=self.ROWS, plan_count=2, plans_complete=True))
        assert cap.status is PortalStatus.OUT_OF_NETWORK


class TestLegAWideningKeepsTheTwoSearchesApart:
    """The root of defect 3: leg A used to overwrite its record with the surname search while keeping
    the NPI search's meaning, and to hard-code `found=False` over cards it never read (defect 4)."""

    class _Stub(WellcareHubDriver):
        def __init__(self, by_kind: dict):
            self.by_kind, self.calls = by_kind, []

        def _hub_search(self, page, q, term, kind, trail):  # noqa: D102 - test double
            self.calls.append((term, kind))
            return {**_leg_a_blank(), **self.by_kind[kind], "term": term, "kind": kind,
                    "reached": True}

    def test_a_populated_npi_filter_is_not_widened_away(self):
        """Widening threw away the specific answer and left a count that meant "this NPI IS listed"
        labelled as a surname search."""
        stub = self._Stub({"npi": {"in_radius": 1, "in_network": 1, "cards": [OUR_CARD],
                                   "cards_read": 1}})
        a = stub._leg_a(None, MANAYAN, "Wellcare Giveback (HMO-POS)", [])
        assert stub.calls == [(MANAYAN.npi, "npi")]
        assert a["kind"] == "npi" and a["card"] == OUR_CARD

    def test_the_widened_surname_cards_are_actually_inspected(self):
        stub = self._Stub({
            "npi": {"in_radius": 0, "cards": [], "cards_read": 0},
            "searchAll": {"in_radius": 1, "in_network": 1, "cards": [SHAH_CARD], "cards_read": 1},
        })
        a = stub._leg_a(None, MANAYAN, "Wellcare Giveback (HMO-POS)", [])
        assert stub.calls == [(MANAYAN.npi, "npi"), ("Manayan", "searchAll")]
        assert a["kind"] == "searchAll" and a["npi_filter_zero"] is True
        assert a["card"] is None and a["identity"] == "no card matched"

    def test_the_widened_search_finding_us_is_carried_not_discarded(self):
        """`found=False, found_name=None` was hard-coded here. If the surname search does return our
        provider that is a contradiction with the NPI filter's zero, and the verdict layer needs to
        see it to answer UNKNOWN instead of OON."""
        stub = self._Stub({
            "npi": {"in_radius": 0, "cards": [], "cards_read": 0},
            "searchAll": {"in_radius": 2, "in_network": 2, "cards": [SHAH_CARD, OUR_CARD],
                          "cards_read": 2},
        })
        a = stub._leg_a(None, MANAYAN, "Wellcare Giveback (HMO-POS)", [])
        assert a["card"] == OUR_CARD and a["npi_filter_zero"] is True

    def test_no_pinned_product_means_no_leg_a_at_all(self):
        stub = self._Stub({})
        assert stub._leg_a(None, MANAYAN, None, []) == _leg_a_blank()
        assert stub.calls == []
