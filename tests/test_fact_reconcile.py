"""The §4a rule: a portal-vs-TiC disagreement is usually a DATE difference, not a conflict of fact.

Row 3 of Ins Test 3 is the worked example. Oscar's live directory said Sanders (NPI 1700846789) was
IN net 065; the TiC MRF said physician gap. Neither was wrong — the contract began 2026-01-27 and the
MRF was built before that. Comparing the contract's start against the source's build date settles it
deterministically, and converts a whole class of REVIEWs into answers.

See HANDOFF-2026-07-29.md §4a.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from network_probe.domain.fact_reconcile import Reconciliation, reconcile_network


def _fact(source, in_network, *, start=None, built=None):
    return {
        "source": source, "in_network": in_network, "network_name": "065",
        "start_date": start,
        "source_built_at": datetime(built.year, built.month, built.day, tzinfo=timezone.utc)
        if built else None,
    }


def test_sources_that_agree_need_no_arbitration():
    r = reconcile_network([_fact("directory", True), _fact("tic", True)])
    assert r.in_network is True
    assert r.status == "agreed"


def test_a_contract_starting_after_the_mrf_was_built_means_the_mrf_is_stale():
    """Row 3, exactly. Directory says IN from 2026-01-27; the MRF was built 2025-11-01 and says out.
    The MRF was correct WHEN BUILT, so the directory wins and this is not a conflict."""
    r = reconcile_network([
        _fact("directory", True, start=date(2026, 1, 27)),
        _fact("tic", False, built=date(2025, 11, 1)),
    ])
    assert r.in_network is True
    assert r.status == "stale_source"
    assert "2026-01-27" in r.reason and "2025-11-01" in r.reason


def test_a_contract_predating_the_mrf_is_a_genuine_conflict():
    """If the contract was already in force when the MRF was built, the MRF should have seen it.
    That is a real disagreement and must stay a REVIEW rather than be resolved by recency."""
    r = reconcile_network([
        _fact("directory", True, start=date(2024, 3, 1)),
        _fact("tic", False, built=date(2025, 11, 1)),
    ])
    assert r.status == "review"
    assert r.in_network is None


def test_disagreement_without_dates_cannot_be_arbitrated():
    """No start date and no build date means no basis to prefer either source. Never guess."""
    r = reconcile_network([_fact("directory", True), _fact("tic", False)])
    assert r.status == "review"
    assert r.in_network is None


def test_recency_alone_does_not_decide_it():
    """A newer source is not automatically right — only a contract that began after the older source
    was built explains the difference. Without a start date, recency proves nothing."""
    r = reconcile_network([
        _fact("directory", True, built=date(2026, 7, 1)),
        _fact("tic", False, built=date(2025, 11, 1)),
    ])
    assert r.status == "review", "newer-wins is not the rule; the contract start is"


def test_a_single_fact_is_returned_as_is():
    r = reconcile_network([_fact("directory", False)])
    assert r.in_network is False and r.status == "single_source"


def test_no_facts_is_unknown_not_out_of_network():
    r = reconcile_network([])
    assert r.in_network is None and r.status == "no_facts"
    assert isinstance(r, Reconciliation)
