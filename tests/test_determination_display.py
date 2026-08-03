"""Every determination renders as INN or OON with a stated confidence — never as "UNKNOWN".

Requested 2026-08-03: a row that reads "UNKNOWN" / "Not yet established" is useless to the person
working the account, who has to bill something either way. So the display always commits to a
direction and says how strongly it is held.

The commitment is DISPLAY ONLY and sits beside, never on top of, the existing fields:

  * `code`        — unchanged. Still UNKNOWN when the evidence is genuinely unsettled. Everything
                    downstream (billing, audit, the golden-record override) reads this.
  * `provisional` — unchanged. The pre-existing display hint, still only ever leaning IN.
  * `display_code` / `display_label` / `confidence` — NEW. The committed reading.

Why the no-evidence lean is OON and not IN: an unconfirmed IN is the false-IN this codebase exists
to remove — it bills as in-network and the claim comes back denied. An unconfirmed OON is the
conservative error, and `confidence: low` plus the determination's own `next_step` says out loud
that it is a lean rather than a finding. Directory ABSENCE still does not become a doctrinal OON:
`code` stays UNKNOWN, so no other layer may treat it as one.
"""

from network_probe.domain.determination import final_determination
from network_probe.domain.models import NetworkStatus

# --- confirmed verdicts keep their answer, at high confidence -----------------------------------


def test_confirmed_in_network_is_high_confidence():
    d = final_determination(NetworkStatus.IN_NETWORK, True)
    assert d.display_code == "IN_NETWORK"
    assert d.confidence == "high"


def test_confirmed_oon_with_benefits_is_high_confidence():
    d = final_determination(NetworkStatus.OUT_OF_NETWORK, True)
    assert d.display_code == "OUT_OF_NETWORK_WITH_BENEFITS"
    assert d.confidence == "high"


# --- unsettled verdicts still commit to a direction ----------------------------------------------


def test_no_determination_ever_displays_as_unknown():
    """The whole point: whatever the evidence, the label is never a blank UNKNOWN."""
    for ev in (
        {},
        {"directory_networks": 11},
        {"in_directory": False},
        {"medicare_enrolled": True},
        {"group_contracted": True, "roster_other_npis": 16},
    ):
        for oon in (True, False, None):
            d = final_determination(NetworkStatus.UNKNOWN, oon, evidence=ev)
            assert d.display_code in {
                "IN_NETWORK", "OUT_OF_NETWORK", "OUT_OF_NETWORK_WITH_BENEFITS",
                "PHYSICIAN_OUT_OF_NETWORK",
            }, f"{ev} / {oon} displayed as {d.display_code}"
            assert "UNKNOWN" not in d.display_label.upper()
            assert "not yet established" not in d.display_label.lower()
            assert d.confidence == "low"


def test_the_label_is_the_bare_reading_and_strength_lives_in_confidence():
    """The UI renders `confidence` as a meter, so the words must not carry it too.

    A strength is a quantity: a bar reads faster than a parenthetical, and duplicating it in the
    label gives the tile two things to keep in sync.
    """
    for ev in ({}, {"directory_networks": 11}, {"group_contracted": True, "roster_other_npis": 3}):
        d = final_determination(NetworkStatus.UNKNOWN, None, evidence=ev)
        assert "confidence" not in d.display_label.lower()
        assert "(" not in d.display_label


def test_directory_presence_leans_in_network_at_low_confidence():
    """The Orem case: in the payer's directory under 11 networks, plan not pinned."""
    d = final_determination(NetworkStatus.UNKNOWN, None, evidence={"directory_networks": 11})
    assert d.code == "UNKNOWN"          # doctrine unchanged
    assert d.display_code == "IN_NETWORK"
    assert d.confidence == "low"
    assert d.display_label == "In-Network"


def test_unsettled_with_oon_benefits_displays_oon_with_benefits():
    """The Norma Stephen case: nothing places the provider in the network, and the plan pays OON.

    "Out-of-Network (with benefits)" is the actionable answer — plain OON would understate what the
    member is owed, and UNKNOWN gives the biller nothing at all.
    """
    d = final_determination(NetworkStatus.UNKNOWN, True, evidence={"in_directory": False})
    assert d.display_code == "OUT_OF_NETWORK_WITH_BENEFITS"
    assert d.confidence == "low"


def test_silent_271_is_filled_by_plan_type_for_the_display_too():
    """A PPO with a silent 271 still displays OON *with benefits* — PPOs pay out-of-network."""
    d = final_determination(
        NetworkStatus.UNKNOWN, None, plan_oon_capability=True, evidence={"in_directory": False},
    )
    assert d.display_code == "OUT_OF_NETWORK_WITH_BENEFITS"


def test_group_contracted_leans_physician_oon():
    d = final_determination(
        NetworkStatus.UNKNOWN, None, evidence={"group_contracted": True, "roster_other_npis": 16},
    )
    assert d.display_code == "PHYSICIAN_OUT_OF_NETWORK"
    assert d.confidence == "low"


def test_no_evidence_at_all_leans_out_of_network():
    """The conservative direction. An unconfirmed IN bills wrong; an unconfirmed OON does not."""
    d = final_determination(NetworkStatus.UNKNOWN, False, evidence={})
    assert d.display_code == "OUT_OF_NETWORK"
    assert d.confidence == "low"


def test_review_is_not_forced_into_a_direction():
    """A genuine cross-source conflict is a real state a human must see — not a low-confidence lean."""
    d = final_determination(NetworkStatus.REVIEW, True)
    assert d.code == "REVIEW" and d.display_code == "REVIEW"
    assert d.confidence == "medium"


def test_display_fields_serialize():
    d = final_determination(NetworkStatus.UNKNOWN, None, evidence={"directory_networks": 4})
    js = d.to_dict()
    assert js["display_code"] == "IN_NETWORK"
    assert js["confidence"] == "low"
    assert js["display_label"]


def test_code_is_never_altered_by_the_display_lean():
    """The guard that keeps this a presentation change: `code` is what billing reads."""
    d = final_determination(NetworkStatus.UNKNOWN, True, evidence={"directory_networks": 11})
    assert d.code == "UNKNOWN"
    assert d.display_code == "IN_NETWORK"
