"""The no-network branch must grade on ASSIGNMENT, not just enrollment.

`no_network_verdict` settles members with no provider network (Original Medicare FFS, Medicare
Supplement/Medigap) — the Test 2 Roulhac row. Its docstring always stated the correct rule, *"a
provider who accepts Medicare assignment is in-network by definition"*, but the code underneath
checked PECOS **enrollment** and then returned `confidence="high"`. Enrolled is necessary, not
sufficient: it does not separate the participating provider the member owes $0 to from the
non-participating one who can balance-bill, or from the opted-out one Medicare will not pay at all.

These tests pin the graded outcome. The direction that matters most is that neither new source can
manufacture a confident answer on its own — an unreachable opt-out file downgrades an IN rather
than being read as "not opted out", and two CMS files that disagree go to REVIEW rather than
letting one of them win silently.
"""

import pytest

from network_probe.domain.enrollment import (
    ACCEPTS,
    MAY_EXCEED,
    OPTED_OUT,
    UNKNOWN_ASSIGNMENT,
    AssignmentResult,
    EnrollmentResult,
)
from network_probe.domain.models import NetworkStatus, ProviderQuery
from network_probe.domain.provider_network import no_network_verdict

_ROULHAC = "1801837109"


def _q(npi=_ROULHAC, tin="475181686", plan="Humana Medicare Supplement Plan G"):
    return ProviderQuery(payer="humana-co-denver", plan_hint=plan, npi=npi, tin=tin)


def _pecos(enrolled):
    return lambda npi: EnrollmentResult(enrolled, "medicare-pecos", f"NPI {npi}: stub lookup")


def _assign(status, optout_checked=True):
    return lambda npi: AssignmentResult(status, f"stub assignment for {npi}", optout_checked=optout_checked)


# ---- the graded answer ---------------------------------------------------------------------

def test_participating_provider_is_in_network_with_high_confidence():
    """The Roulhac case. NOW the 'high' is earned: it is the same fact the Care Compare screenshot
    shows, not merely 'is enrolled somewhere'."""
    v = no_network_verdict(_q(), pecos_fn=_pecos(True), assignment_fn=_assign(ACCEPTS))
    assert v.status == NetworkStatus.IN_NETWORK
    assert v.confidence == "high"
    assert "assignment" in v.notes.lower()


def test_non_participating_provider_is_in_network_but_flagged_for_excess_charges():
    """Still in-network — Medicare covers the service — but the member can be billed the Part B
    excess, and a verdict that hides that is the one that produces a surprise bill."""
    v = no_network_verdict(_q(), pecos_fn=_pecos(True), assignment_fn=_assign(MAY_EXCEED))
    assert v.status == NetworkStatus.IN_NETWORK
    assert v.confidence == "medium"
    assert "excess" in v.notes.lower()


def test_opted_out_provider_is_out_of_network_even_though_enrolled():
    """A private contract is the one case where the answer really is 'out of network' for a member
    with no network: Medicare pays nothing, and the supplement pays nothing on top of nothing."""
    v = no_network_verdict(_q(), pecos_fn=_pecos(True), assignment_fn=_assign(OPTED_OUT))
    assert v.status == NetworkStatus.OUT_OF_NETWORK
    assert v.confidence == "high"
    assert "opt" in v.notes.lower()


def test_unknown_assignment_still_resolves_in_network_but_only_at_medium():
    """Today's behaviour, honestly graded. Most providers are simply absent from the Care Compare
    file, and that must not block the verdict — it just can't claim 'high' any more."""
    v = no_network_verdict(_q(), pecos_fn=_pecos(True), assignment_fn=_assign(UNKNOWN_ASSIGNMENT))
    assert v.status == NetworkStatus.IN_NETWORK
    assert v.confidence == "medium"


# ---- an unchecked negative source cannot be treated as a clear one -------------------------------

def test_an_unreachable_optout_file_downgrades_a_yes_rather_than_being_read_as_no_optout():
    v = no_network_verdict(
        _q(), pecos_fn=_pecos(True), assignment_fn=_assign(ACCEPTS, optout_checked=False)
    )
    assert v.status == NetworkStatus.IN_NETWORK
    assert v.confidence == "medium"


# ---- opt-out outranks enrollment in BOTH directions ---------------------------------------------

def test_opt_out_settles_it_even_when_enrollment_could_not_be_determined():
    v = no_network_verdict(_q(), pecos_fn=_pecos(None), assignment_fn=_assign(OPTED_OUT))
    assert v.status == NetworkStatus.OUT_OF_NETWORK


def test_assignment_can_settle_a_member_whose_pecos_lookup_failed():
    """PECOS being down is not a reason to punt when the Care Compare file answered."""
    v = no_network_verdict(_q(), pecos_fn=_pecos(None), assignment_fn=_assign(ACCEPTS))
    assert v.status == NetworkStatus.IN_NETWORK
    assert v.confidence == "medium"


def test_both_sources_silent_is_still_unknown_and_never_a_directory_fallthrough():
    v = no_network_verdict(_q(), pecos_fn=_pecos(None), assignment_fn=_assign(UNKNOWN_ASSIGNMENT))
    assert v is not None
    assert v.status == NetworkStatus.UNKNOWN
    assert v.confidence == "low"


# ---- contradictions go to a human ---------------------------------------------------------------

def test_two_cms_files_that_disagree_go_to_review_not_to_a_confident_oon():
    """PECOS says he cannot bill Medicare; the Care Compare file says he accepts assignment. One of
    them is wrong and we cannot tell which, so asserting either answer is over-claiming."""
    v = no_network_verdict(_q(), pecos_fn=_pecos(False), assignment_fn=_assign(ACCEPTS))
    assert v.status == NetworkStatus.REVIEW
    assert v.confidence == "conflict"


def test_not_enrolled_and_no_assignment_evidence_stays_a_decisive_oon():
    """Regression guard: the existing decisive negative must survive. Nothing contradicts it here."""
    v = no_network_verdict(_q(), pecos_fn=_pecos(False), assignment_fn=_assign(UNKNOWN_ASSIGNMENT))
    assert v.status == NetworkStatus.OUT_OF_NETWORK
    assert v.confidence == "high"


def test_not_enrolled_and_opted_out_agree_on_out_of_network():
    v = no_network_verdict(_q(), pecos_fn=_pecos(False), assignment_fn=_assign(OPTED_OUT))
    assert v.status == NetworkStatus.OUT_OF_NETWORK


# ---- shape and plumbing --------------------------------------------------------------------------

def test_no_npi_is_unknown_and_calls_neither_source():
    calls = []
    v = no_network_verdict(
        _q(npi=None),
        pecos_fn=lambda n: calls.append("pecos"),
        assignment_fn=lambda n: calls.append("assign"),
    )
    assert v.status == NetworkStatus.UNKNOWN
    assert calls == []


def test_the_assignment_evidence_is_carried_in_the_corroboration_trail():
    v = no_network_verdict(_q(), pecos_fn=_pecos(True), assignment_fn=_assign(ACCEPTS))
    sources = [c["source"] for c in (v.corroboration or [])]
    assert "Medicare assignment" in sources


@pytest.mark.parametrize("status", [ACCEPTS, MAY_EXCEED, UNKNOWN_ASSIGNMENT])
def test_a_broken_assignment_lookup_never_blocks_the_verdict(status):
    """An exception from the assignment source must degrade to today's enrollment-only answer."""

    def boom(npi):
        raise RuntimeError("CMS down")

    v = no_network_verdict(_q(), pecos_fn=_pecos(True), assignment_fn=boom)
    assert v.status == NetworkStatus.IN_NETWORK
    assert v.confidence == "medium"
