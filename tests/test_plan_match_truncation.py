"""The 271's plan string is prefixed and length-capped, and both defeated the matcher.

Driven by REAL data, not by one failing row: 40 distinct plan strings pulled from this system's own
audit tables plus a live 271 run on 2026-08-06 for the member whose capture failed.

    live 271, UnitedHealthcare (payer 87726):
        planCoverage = 'LPPO-UNITEDHEALTHCARE GROUP MEDICARE ADVANTAGE (PP'   <- 50 chars exactly

Two separate defects in one string:

1. **`LPPO-`** — Medicare's Local PPO, a plan TYPE, exactly the category `_NON_DISTINCTIVE` already
   discards (HMO/PPO/POS/SNP). No portal prints it, so it defeated the exact tier outright.

2. **Truncation.** Every value in the corpus that reached 50 characters was cut dead there, mid-token
   — `(PPO)` losing its last two characters — and none exceeded it:

       LPPO-UNITEDHEALTHCARE GROUP MEDICARE ADVANTAGE (PP    50
       LPPO-AARP MEDICARE ADVANTAGE FROM UHC FL-0026 (PPO    50

   A truncated name cannot equal a label, so tier 1.5 could never fire for a long plan name.

The fix stays inside the module's own doctrine: a truncated string may match a longer label by
prefix, but ONLY when exactly one option continues it. Two candidates is the ambiguity the tie rule
exists to refuse — and it is the common case here, since `…GROUP MEDICARE ADVANTAGE (` fits both the
PPO and the HMO product.

The length gate is what keeps this honest. `Statewide PPO` is a real AZ Blue network in its own
right and is 13 characters — nowhere near the cap — so it is complete, and must never absorb
`Statewide PPO/EPO`.
"""

from __future__ import annotations

import pytest

from network_probe.portal.plan_match import _TRUNCATION_LEN, distinctive_tokens, match_plan

# Cobb County's real UHC Medicare list (labels as the portal prints them), read live 2026-08-06.
UHC_GA = [
    "AARP Medicare Advantage from UHC GA-5 (HMO-POS)",
    "UHC Complete Care Support GS-1A (Regional PPO C-SNP)",
    "UHC Dual Complete GA-D001 (PPO D-SNP)",
    "UnitedHealthcare Group Medicare Advantage (HMO)",
    "UnitedHealthcare Group Medicare Advantage (PPO)",
]
MORGAN = "LPPO-UNITEDHEALTHCARE GROUP MEDICARE ADVANTAGE (PP"  # the live 271, verbatim


def test_the_live_271_string_is_exactly_at_the_cap():
    """If this ever stops being true the truncation tier stops firing, silently."""
    assert len(MORGAN) == _TRUNCATION_LEN == 50


def test_the_truncated_271_plan_now_resolves_to_the_one_plan_that_continues_it():
    """The capture this came from returned zero plans. `(PP` can only be `(PPO)` here."""
    m = match_plan(MORGAN, UHC_GA)
    assert m is not None, "a truncated name that names exactly one plan is not a guess"
    assert m.label == "UnitedHealthcare Group Medicare Advantage (PPO)"


def test_it_does_not_grab_the_hmo_sibling():
    """The two differ only after the truncation point in the FULL name, but not at `(PP`."""
    m = match_plan(MORGAN, UHC_GA)
    assert "(HMO)" not in m.label


def test_a_truncation_that_fits_two_plans_is_refused():
    """Cut one character earlier and `(P` fits both products — that is a tie, and ties are refused."""
    ambiguous = "LPPO-UNITEDHEALTHCARE GROUP MEDICARE ADVANTAGEXX ("
    assert len(ambiguous) == _TRUNCATION_LEN
    assert match_plan(ambiguous, [
        "UnitedHealthcare Group Medicare AdvantageXX (HMO)",
        "UnitedHealthcare Group Medicare AdvantageXX (PPO)",
    ]) is None


def test_a_name_match_never_licenses_an_out_of_network_reading():
    """A truncated name is still a name. Only an identifier may set confirms_network."""
    m = match_plan(MORGAN, UHC_GA)
    assert m.confidence == "medium"
    assert not m.confirms_network


def test_the_note_says_the_string_was_truncated():
    """§8: a reader must be able to see WHY this matched something it does not equal."""
    m = match_plan(MORGAN, UHC_GA)
    assert "cut" in m.basis.lower() or "truncat" in m.basis.lower()
    assert str(_TRUNCATION_LEN) in m.basis


# --- the length gate: short strings are COMPLETE and must not prefix-match --------------------------


def test_a_short_complete_name_never_absorbs_a_longer_sibling():
    """`Statewide PPO` is its own AZ Blue network. This is the regression the gate exists for."""
    assert match_plan("Statewide PPO", ["Statewide PPO/EPO", "Alliance HMO"]) is None


def test_a_short_name_that_IS_a_label_still_matches_exactly():
    m = match_plan("Statewide PPO", ["Statewide PPO", "Statewide PPO/EPO"])
    assert m is not None and m.label == "Statewide PPO"


# --- the LPPO- prefix ------------------------------------------------------------------------------


def test_the_plan_type_prefix_does_not_block_an_exact_name_match():
    """Un-truncated but prefixed: the portal never prints `LPPO-`, so it must come off."""
    m = match_plan("LPPO-UnitedHealthcare Group Medicare Advantage (PPO)", UHC_GA)
    assert m is not None and m.label == "UnitedHealthcare Group Medicare Advantage (PPO)"


@pytest.mark.parametrize("prefix", ["LPPO-", "RPPO-", "lppo-", "HMOPOS-", "PFFS-"])
def test_a_plan_type_prefix_is_stripped_as_a_FALLBACK(prefix):
    m = match_plan(f"{prefix}UnitedHealthcare Group Medicare Advantage (PPO)", UHC_GA)
    assert m is not None and m.label.endswith("(PPO)")


def test_lppo_is_not_treated_as_a_distinguishing_word():
    """It is a plan type, like the HMO/PPO/SNP already excluded — it identifies no plan."""
    assert "LPPO" not in distinctive_tokens("LPPO-UNITEDHEALTHCARE GROUP MEDICARE ADVANTAGE")
    assert "RPPO" not in distinctive_tokens("RPPO-SOMETHING")


# --- the property that keeps this from breaking ten rows to fix one --------------------------------


@pytest.mark.parametrize("name", [
    "EPO Connect IN",              # a REAL plan string from this system's audit rows
    "PPO Plus Advantage Select",   # a plan genuinely NAMED with a leading type word
    "HMO-POS Community Choice",    # a compound type that must not lose its head
    "POS Advantage Select",
    "MSA Select Care",
])
def test_a_plan_named_with_a_leading_type_word_still_matches_itself(name):
    """The pattern is broad, so it WOULD rewrite every one of these — "EPO Connect IN" becomes
    "Connect IN". It is harmless only because the raw string is tried first. If stripping ever stops
    being a fallback and becomes a substitution, these break immediately."""
    m = match_plan(name, [name])
    assert m is not None and m.label == name


def test_stripping_can_only_ADD_a_match_never_change_one():
    """The guarantee, stated as a test: for any plan string, if the string as sent resolves against
    a set of options, the prefix logic must return exactly that same option."""
    cases = [
        ("EPO Connect IN", ["EPO Connect IN", "Connect IN"]),
        ("PPO Plus Advantage Select", ["PPO Plus Advantage Select", "Plus Advantage Select"]),
        ("HMO-POS Community Choice", ["HMO-POS Community Choice", "POS Community Choice"]),
    ]
    for wanted, options in cases:
        m = match_plan(wanted, options)
        assert m is not None and m.label == wanted, (
            f"{wanted!r} must match itself, not the stripped sibling {options[1]!r}"
        )


def test_a_complete_name_is_never_beaten_by_a_prefix_match():
    """Ordering: every exact form is tried before any truncation form. A 50-char string that exactly
    IS one option must take that option, even though a longer one also continues it."""
    wanted = "LPPO-UnitedHealthcare Group Medicare Advantage!!!!"
    assert len(wanted) == _TRUNCATION_LEN
    m = match_plan(wanted, [
        "UnitedHealthcare Group Medicare Advantage!!!! Extended (PPO)",
        "UnitedHealthcare Group Medicare Advantage!!!!",
    ])
    assert m is not None and m.label == "UnitedHealthcare Group Medicare Advantage!!!!"


# --- the identifier tier still wins, and still outranks all of this --------------------------------


def test_an_identifier_still_beats_a_truncated_name():
    """The FL-0026 row: truncated AND prefixed, but it carries a market code, so tier 1 answers and
    the match is identifier-grade — which the truncation tier can never be."""
    m = match_plan("LPPO-AARP MEDICARE ADVANTAGE FROM UHC FL-0026 (PPO", [
        "AARP Medicare Advantage CareFlex from UHC FL-35 (HMO-POS)",
        "AARP Medicare Advantage Choice from UHC FL-0026 (PPO)",
    ])
    assert m is not None and "FL-0026" in m.label
    assert m.confidence == "high" and m.confirms_network


def test_the_real_corpus_of_plan_strings_still_behaves():
    """Every other distinct plan string this system has actually seen. None of them is at the cap,
    so none may acquire a prefix match from this change."""
    corpus = [
        "Aetna Medicare Premier (PPO) - H5521-016", "Aetna Medicare Prime Extra (HMO)",
        "BCBS Arizona", "Blue Choice PPO", "Blue Preferred POS", "Cigna Commercial",
        "EPO Connect IN", "Humana Honor PPO", "HumanaChoice", "Medicare PPO",
        "MeridianHealth: Medicaid", "Molina Healthcare Texas Medicaid", "Open Choice® PPO",
        "Oscar Health", "STAR+PLUS", "Statewide / National PPO", "Texas STAR",
        "UHC Commerical NHP Access HMO", "UHC Medicare Advantage GA", "Wellcare",
    ]
    for s in corpus:
        assert len(s) < _TRUNCATION_LEN, f"{s!r} is at the cap and would newly prefix-match"
        # unrelated options: nothing should match at all
        assert match_plan(s, ["Some Entirely Different Plan (HMO)"]) is None
