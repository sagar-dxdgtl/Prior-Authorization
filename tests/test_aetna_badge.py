"""Regression: Aetna's network badge must never invert.

Found by the adversarial verification pass on 2026-07-28. `_IN_NETWORK_BADGE` is r"\\bin\\s*network\\b",
which matches INSIDE "Not In Network". The out-of-network branch was gated on `OON and not IN`, so on a
card the payer had explicitly badged "Not In Network" that guard never fired, execution fell through to
the IN branch, and the driver returned IN_NETWORK — a verdict inversion producing a false in-network on
the payer's own contrary word. These tests pin the ordering that fixes it: OON is tested first and wins.
"""

from __future__ import annotations

import pytest

from network_probe.portal.drivers.aetna_ahpublic import _badge

# Real badge text shapes Aetna renders, plus the spacing/casing variants a card can carry.
OON_TEXTS = [
    "Not In Network",
    "not in network",
    "NOT IN NETWORK",
    "Out of Network",
    "out-of-network" .replace("-", " "),
    "Tursunaliev, Serik, MD - Not In Network - 7420 Central Ave, River Forest, IL 60305",
    "Desir, Hedson, MD  Out  of  Network  1.2 miles",
]

IN_TEXTS = [
    "In Network",
    "in network",
    "IN NETWORK",
    "Tursunaliev, Serik, MD - In Network - 7420 Central Ave Bldg C Ste 230, River Forest, IL 60305",
]


@pytest.mark.parametrize("text", OON_TEXTS)
def test_oon_badges_never_read_as_in_network(text):
    """The whole bug in one assertion: a card the payer badged out-of-network must not read as IN."""
    assert _badge(text) == "oon", f"{text!r} must be read as out-of-network"
    assert _badge(text) != "in"


@pytest.mark.parametrize("text", IN_TEXTS)
def test_in_badges_read_as_in_network(text):
    assert _badge(text) == "in"


def test_unbadged_text_is_neither():
    """No badge is not a verdict — the caller must fall through to its absence logic, not guess."""
    assert _badge("Tursunaliev, Serik, MD - 7420 Central Ave, River Forest, IL 60305") is None
    assert _badge("") is None
    assert _badge(None) is None


def test_the_exact_inverting_string_is_covered():
    """Belt and braces: assert the raw regex really does match inside the OON phrase, so nobody
    'simplifies' _badge back into a bare IN test without tripping this."""
    from network_probe.portal.drivers.aetna_ahpublic import _IN_NETWORK_BADGE

    assert _IN_NETWORK_BADGE.search("Not In Network") is not None, (
        "the naive IN pattern still matches inside 'Not In Network' — _badge's OON-first ordering is "
        "the only thing preventing the inversion, so it must not be removed"
    )
    assert _badge("Not In Network") == "oon"
