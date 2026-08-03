"""Directory presence in networks that are NOT the member's is not evidence of in-network.

Caught on the demo path 2026-08-03. Randall C Orem (NPI 1497741409) is in UnitedHealthcare's FL
directory under 11 networks — Choice Plus, NexusACO RP, Select HMO, Charter HMO, … — and the member
is on "LPPO-AARP MEDICARE ADVANTAGE FROM UHC FL-0026 (PPO". **None of the 11 matched.** The live
portal walk then found no listing for him at all. The tile still read "IN NETWORK · low confidence".

The lean was reading `directory_networks > 0` as "contracted with this payer, so probably in". That
inference is only sound when the network could not be pinned because NO PLAN WAS GIVEN. Once a plan
IS given and every network fails to match it, the same fact points the other way: he is contracted
with UnitedHealthcare for *other* products, which is exactly the false IN this codebase exists to
remove — the FhirPdex "IN_NETWORK with no plan" defect resolved Georgia members onto New Mexico and
Medicaid networks.

Two independent suppressors, because either alone is enough to make an IN lean unsafe:
  * a plan was given and no network matched it, and
  * the payer's own member-facing portal searched and did not list the provider.

Neither becomes a doctrinal OUT_OF_NETWORK — `code` stays UNKNOWN and absence is still not proof.
They only stop the DISPLAY from leaning in. With OON benefits on the plan (a PPO), the committed
reading is "Out-of-Network with benefits", low confidence — which is what a biller needs.
"""

from network_probe.domain.determination import final_determination
from network_probe.domain.models import NetworkStatus

OREM = {
    "directory_networks": 11,
    "in_directory": True,
    "payer_label": "UnitedHealthcare",
    "plan_given": True,
    "matched_network": False,
}


def test_the_orem_case_does_not_lean_in_network():
    d = final_determination(NetworkStatus.UNKNOWN, True, evidence=OREM)
    assert d.display_code != "IN_NETWORK"
    assert d.provisional != "LIKELY_IN_NETWORK"
    assert d.display_code == "OUT_OF_NETWORK_WITH_BENEFITS"  # the plan is a PPO
    assert d.confidence == "low"
    assert d.code == "UNKNOWN"  # doctrine unchanged: this is still not proof of OON


def test_the_basis_says_the_networks_are_not_the_members():
    """A reader has to be able to see WHY 11 networks did not make it in-network."""
    d = final_determination(NetworkStatus.UNKNOWN, True, evidence=OREM)
    assert "11" in d.basis
    assert "none" in d.basis.lower() or "not" in d.basis.lower()


def test_no_plan_given_still_leans_in_network():
    """Unchanged: with no plan there is nothing to fail to match, and presence in the payer's own
    directory really is evidence they are contracted. Rows 1 / 7 / 10 depend on this."""
    d = final_determination(
        NetworkStatus.UNKNOWN, None,
        evidence={"directory_networks": 3, "payer_label": "UnitedHealthcare", "plan_given": False},
    )
    assert d.provisional == "LIKELY_IN_NETWORK"
    assert d.display_code == "IN_NETWORK"


def test_a_matched_network_still_leans_in_network():
    """If a network DID match the member's plan, the presence is about the right network."""
    d = final_determination(
        NetworkStatus.UNKNOWN, None,
        evidence={"directory_networks": 4, "plan_given": True, "matched_network": True},
    )
    assert d.provisional == "LIKELY_IN_NETWORK"


def test_a_portal_that_searched_and_did_not_list_them_suppresses_an_in_lean():
    """The member-facing portal is the accuracy check. It cannot prove OON, but a directory read
    must not out-vote it into a confident-looking IN."""
    d = final_determination(
        NetworkStatus.UNKNOWN, True,
        evidence={"directory_networks": 3, "plan_given": False, "portal_absent": True},
    )
    assert d.display_code != "IN_NETWORK"
    assert d.provisional != "LIKELY_IN_NETWORK"
    assert "portal" in (d.basis or "").lower()


def test_group_contracted_still_outranks_the_directory_read():
    """The physician-OON lean is stronger evidence than either suppressor and keeps precedence."""
    d = final_determination(
        NetworkStatus.UNKNOWN, None,
        evidence={"group_contracted": True, "roster_other_npis": 16,
                  "directory_networks": 11, "plan_given": True, "matched_network": False},
    )
    assert d.provisional == "LIKELY_PHYSICIAN_OUT_OF_NETWORK"
    assert d.display_code == "PHYSICIAN_OUT_OF_NETWORK"


def test_the_next_step_is_still_actionable():
    d = final_determination(NetworkStatus.UNKNOWN, True, evidence=OREM)
    assert d.next_step and len(d.next_step) > 10


def test_the_payer_name_is_not_mangled_in_the_basis():
    """`str.capitalize()` lowercases everything after the first character — it printed
    "unitedhealthcare's directory" on the demo path. This text is client-facing."""
    d = final_determination(
        NetworkStatus.UNKNOWN, True,
        evidence={**OREM, "payer_label": "UnitedHealthcare"},
    )
    assert "UnitedHealthcare" in d.basis
    assert "unitedhealthcare" not in d.basis


def test_both_suppressors_read_as_separate_sentences():
    d = final_determination(
        NetworkStatus.UNKNOWN, True,
        evidence={**OREM, "portal_absent": True, "payer_label": "UnitedHealthcare"},
    )
    assert d.basis.count(".") >= 2
    assert " and unitedhealthcare" not in d.basis.lower()
