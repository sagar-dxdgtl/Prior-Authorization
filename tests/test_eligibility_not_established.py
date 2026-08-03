"""A network verdict presupposes a member. When the 271 never answered, say THAT.

Caught on the demo path 2026-08-03. A Molina row (NPI 1437131901, expected INN) rendered
**Out-of-Network · low confidence** on literally zero evidence:

    Coverage N/A · PCP N/A · Prior Auth N/A · Referral N/A
    plan asked : None
    walk: … → no network matched plan None (no plan string was supplied) → header still on
          'AZ - Molina Complete Care'

The 271 returned nothing, so there was no plan, so no provider search was ever issued. The tile
still committed to a direction — and picked the wrong one for that row.

Two different failures were wearing the same label, and only the first deserves a lean:

  * "we know the member, we cannot pin their network"  -> lean, OON is the safe direction
  * "the 271 did not come back at all"                 -> there is no member; a NETWORK status is
                                                          not a thing that can be true or false yet

Saying "Out-of-Network" in the second case is not a cautious lean, it is a false statement about a
check that never ran. This is NOT a return of the blank UNKNOWN — it names a different and fixable
problem ("this member/payer combination returned nothing") and points at the real next step.

`coverage_established` rides in the evidence dict rather than as a new parameter, so it round-trips
through `prior_evidence` to the async portal capture for free.
"""

from network_probe.domain.determination import final_determination
from network_probe.domain.models import NetworkStatus


def test_a_silent_271_does_not_produce_a_network_direction():
    d = final_determination(
        NetworkStatus.UNKNOWN, None, evidence={"coverage_established": False},
    )
    assert d.display_code == "NOT_ESTABLISHED"
    assert "not established" in d.display_label.lower()
    assert d.display_code != "OUT_OF_NETWORK"


def test_it_carries_no_confidence_meter():
    """There is no confidence in a non-answer — the meter must not render at all."""
    d = final_determination(
        NetworkStatus.UNKNOWN, None, evidence={"coverage_established": False},
    )
    assert d.confidence == "none"


def test_the_reason_names_the_actual_problem():
    d = final_determination(
        NetworkStatus.UNKNOWN, None, evidence={"coverage_established": False},
    )
    blob = f"{d.basis} {d.next_step}".lower()
    assert "271" in blob or "eligibility" in blob
    assert "member" in blob


def test_established_coverage_still_leans_as_before():
    """The ordinary unsettled case is untouched: we know the member, we cannot pin the network."""
    d = final_determination(
        NetworkStatus.UNKNOWN, True, evidence={"coverage_established": True, "in_directory": False},
    )
    assert d.display_code == "OUT_OF_NETWORK_WITH_BENEFITS"
    assert d.confidence == "low"


def test_absent_flag_is_treated_as_established():
    """Callers that never set it (tests, CLI, older payloads) keep the previous behaviour."""
    d = final_determination(NetworkStatus.UNKNOWN, True, evidence={})
    assert d.display_code == "OUT_OF_NETWORK_WITH_BENEFITS"


def test_a_decisive_verdict_is_never_suppressed():
    """Credentialing or TiC can answer for the PROVIDER without a 271 — that finding stands on its
    own and must not be hidden just because the member lookup failed."""
    for status, expected in (
        (NetworkStatus.IN_NETWORK, "IN_NETWORK"),
        (NetworkStatus.OUT_OF_NETWORK, "OUT_OF_NETWORK"),
    ):
        d = final_determination(status, False, evidence={"coverage_established": False})
        assert d.display_code == expected
        assert d.confidence == "high"


def test_directory_evidence_does_not_rescue_a_missing_member():
    """"In 11 of this payer's networks" is about the PROVIDER. Without a member there is no plan for
    them to be in-network FOR, so it cannot stand in for one."""
    d = final_determination(
        NetworkStatus.UNKNOWN, None,
        evidence={"coverage_established": False, "directory_networks": 11, "plan_given": False},
    )
    assert d.display_code == "NOT_ESTABLISHED"


def test_it_serializes():
    d = final_determination(
        NetworkStatus.UNKNOWN, None, evidence={"coverage_established": False},
    )
    js = d.to_dict()
    assert js["display_code"] == "NOT_ESTABLISHED" and js["confidence"] == "none"
    assert js["code"] == "UNKNOWN"  # doctrine unchanged
