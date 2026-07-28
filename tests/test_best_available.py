"""A row must never render as a blank "UNKNOWN".

Every unsettled Ins Test 3 row has real evidence behind it — the provider is in the payer's own
directory under N networks, or the clinic's TIN is in the payer's MRF under other NPIs, or the
provider is Medicare-enrolled. Showing "UNKNOWN" throws all of that away and reads as "the system
does not work", which is a presentation failure rather than a doctrine one.

So UNKNOWN keeps its `code` — nothing downstream may treat a provisional reading as a verdict —
but gains three display fields: the best available reading, the evidence behind it, and the single
thing that would settle it. `provisional` is DISPLAY ONLY. The moment it is allowed to drive
billing or a client-facing determination it becomes the false IN this codebase keeps removing.
"""

from network_probe.domain.determination import final_determination
from network_probe.domain.models import NetworkStatus


def test_a_confirmed_verdict_carries_no_provisional():
    """When we actually know, there is nothing provisional to say."""
    d = final_determination(NetworkStatus.IN_NETWORK, True)
    assert d.code == "IN_NETWORK"
    assert d.provisional is None


def test_directory_presence_becomes_a_likely_in_network_reading():
    """Row 1 / 7 / 10: the provider IS contracted with this payer — we just cannot pin which
    network without the member's plan. That is a far better thing to show than 'UNKNOWN'."""
    d = final_determination(
        NetworkStatus.UNKNOWN, None,
        evidence={"directory_networks": 3, "payer_label": "UnitedHealthcare"},
    )
    assert d.code == "UNKNOWN"                     # the verdict is unchanged
    assert d.provisional == "LIKELY_IN_NETWORK"    # but the row is not blank
    assert "3 network" in d.basis
    assert "plan" in d.next_step.lower()


def test_group_contracted_with_our_npi_absent_leans_physician_oon():
    d = final_determination(
        NetworkStatus.UNKNOWN, None,
        evidence={"group_contracted": True, "roster_other_npis": 16},
    )
    assert d.provisional == "LIKELY_PHYSICIAN_OUT_OF_NETWORK"
    assert "16" in d.basis


def test_absence_from_the_directory_never_leans_out_of_network():
    """Row 6 (Humana). Absence is not evidence of OON — that is the rule the whole system turns
    on, and a demo-facing label must not quietly break it."""
    d = final_determination(
        NetworkStatus.UNKNOWN, None,
        evidence={"directory_networks": 0, "in_directory": False},
    )
    assert d.provisional != "LIKELY_OUT_OF_NETWORK"
    assert d.provisional is None
    assert "credentialing" in d.next_step.lower()


def test_medicare_enrollment_alone_is_not_a_network_reading():
    """Enrolled in Medicare is necessary, never sufficient — it clears a gate, it does not pin a
    network."""
    d = final_determination(NetworkStatus.UNKNOWN, None, evidence={"medicare_enrolled": True})
    assert d.provisional is None
    assert "medicare" in (d.basis or "").lower()


def test_with_no_evidence_at_all_it_says_so_plainly():
    d = final_determination(NetworkStatus.UNKNOWN, None, evidence={})
    assert d.code == "UNKNOWN"
    assert d.provisional is None
    assert d.label and d.next_step  # still never blank


def test_every_unknown_row_renders_something():
    """The demo requirement, as a test: no unsettled row may come back with an empty label,
    empty reason, or no stated next step."""
    for ev in ({"directory_networks": 4}, {"group_contracted": True, "roster_other_npis": 2},
               {"in_directory": False}, {"medicare_enrolled": True}, {}):
        d = final_determination(NetworkStatus.UNKNOWN, None, evidence=ev)
        assert d.label.strip() and d.reason.strip() and (d.next_step or "").strip()
        assert d.to_dict()["provisional"] == d.provisional
