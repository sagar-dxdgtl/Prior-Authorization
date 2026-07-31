"""An exact name match must not be refused as a tie.

`match_plan` ranks by distinctive-token overlap and refuses a tie rather than guess — correct, and
deliberately so. But it applied that refusal even when the 271's plan string was the portal's label
VERBATIM: measured 2026-07-31 against AZ Blue's 14 published networks, feeding each label back in as
the plan string resolved only 6 of 14. "Alliance HMO" did not match "Alliance HMO", because ALLIANCE
recurs in Alliance PPO/EPO and HMO recurs in Statewide HMO, so the top two scores tied.

An exact match is not a tie. There is nothing to guess between when one option IS the string.

Affects the drivers that route through `match_plan` — Oscar, Wellcare, Molina, Humana. AZ Blue has
its own `_best_network`, which already resolved 14/14 and is untouched.
"""

from network_probe.portal.plan_match import match_plan

# AZ Blue's published networks, read live from the payer gate on 2026-07-31.
AZ_BLUE = [
    "Statewide/National PPO/EPO",
    "Statewide/National PPO + Prosano",
    "Statewide PPO",
    "CHS Arizona PPO (No access outside of Arizona)",
    "Indemnity",
    "Statewide HMO",
    "Alliance PPO/EPO",
    "Alliance PPO + Prosano",
    "Alliance HMO",
    "BlueHPN National EPO (in AZ: Alliance Network)",
    "Neighborhood",
    "Workers Compensation",
    "Blue Best Life - Classic/Plus",
    "Senior Preferred Medicare Supplement",
]


def test_every_exact_label_resolves_to_itself():
    """The regression this tier exists for: was 6/14."""
    misses = [lab for lab in AZ_BLUE
              if not (m := match_plan(lab, AZ_BLUE)) or m.label != lab]
    assert misses == [], f"{len(misses)} exact labels still unmatched: {misses}"


def test_exact_match_survives_a_token_tie():
    """'Alliance HMO' ties with 'Alliance PPO/EPO' and 'Statewide HMO' on token overlap."""
    m = match_plan("Alliance HMO", AZ_BLUE)
    assert m is not None and m.label == "Alliance HMO"


def test_exact_match_ignores_case_and_slash_spacing():
    """A 271 writes 'Statewide / National PPO'; the payer publishes 'Statewide/National PPO/EPO' and
    'Statewide PPO'. Spacing around punctuation must not decide which one is 'exact'."""
    assert match_plan("statewide ppo", AZ_BLUE).label == "Statewide PPO"
    assert match_plan("  ALLIANCE   PPO / EPO ", AZ_BLUE).label == "Alliance PPO/EPO"


def test_exact_match_does_not_license_an_out_of_network():
    """Doctrine, stated in five drivers: only an IDENTIFIER clears `confirms_network`. An exact name
    is a much stronger name than token overlap, but it is still a name — two payers, or two markets
    of one payer, can print the same network name. It pins the search; it does not license an OON."""
    m = match_plan("Alliance HMO", AZ_BLUE)
    assert m.confidence == "medium"
    assert m.confirms_network is False


def test_an_identifier_still_outranks_an_exact_name():
    """Tier order must not change: the identifier is the more reliable evidence."""
    options = ["Alliance HMO", "Some Plan H1234-005"]
    m = match_plan("Alliance HMO H1234-005", options)
    assert m.label == "Some Plan H1234-005"
    assert m.confidence == "high"


def test_an_ambiguous_exact_match_is_refused():
    """If a portal lists the same label twice there is nothing to disambiguate, and picking the first
    would be exactly the guess the tie rule exists to prevent."""
    assert match_plan("Statewide PPO", ["Statewide PPO", "statewide  ppo"]) is None


def test_a_non_exact_string_still_falls_through_to_the_token_tier():
    """The new tier must add a case, not replace the old behaviour."""
    m = match_plan("Blue Best Life Classic", AZ_BLUE)
    assert m is not None and m.label == "Blue Best Life - Classic/Plus"
    assert m.confidence == "medium"


def test_still_refuses_a_genuine_tie_with_no_exact_match():
    """`_best_network returning None for 'Alliance PPO' is CORRECT` — it ties between two real AZ Blue
    networks. The exact tier must not rescue that: 'Alliance PPO' is not any label verbatim."""
    assert match_plan("Alliance PPO", ["Alliance PPO/EPO", "Alliance PPO + Prosano"]) is None
