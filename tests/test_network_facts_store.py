"""`provider_network_facts` as a provider PARTICIPATION store, not a single in/out flag.

The reverse lookup proven on 2026-07-29 (ask the provider, read every network they are in) produces a
SET of facts per NPI — Oscar's profile for Sanders returns net 065 IN, 082 OUT, 083 OUT, all from one
fetch, all the same source. Storing that set is the whole point; a store that keeps one row per
(payer, npi, tin, source) cannot hold it.

See HANDOFF-2026-07-29.md §2 and §4a.
"""

from __future__ import annotations

import uuid

import pytest

from network_probe.domain.network_facts import ProviderNetworkStore

_NPI = "1700846789"  # Sanders — Ins Test 3 row 3
_TIN = "921600050"


def _payer() -> str:
    """A unique payer key per test: this table is not truncated by the _clean_db fixture."""
    return f"oscar-test-{uuid.uuid4().hex[:10]}"


@pytest.mark.db
def test_a_providers_whole_network_set_is_stored_not_just_the_first():
    """Oscar's profile returns one provider's participation across every network, in a single read.

    `upsert` de-duplicated on (payer_key, npi, tin, source) with network_name absent from the key, so
    the second and third networks were silently skipped as duplicates and the provider's network SET
    collapsed to whichever row happened to be iterated first. That makes the stored answer depend on
    input order — and it discards exactly the OUT-of-network facts that make an absence decisive.
    """
    store, payer = ProviderNetworkStore(), _payer()
    rows = [
        {"payer_key": payer, "npi": _NPI, "tin": _TIN, "in_network": True,
         "source": "directory", "network_name": "065"},
        {"payer_key": payer, "npi": _NPI, "tin": _TIN, "in_network": False,
         "source": "directory", "network_name": "082"},
        {"payer_key": payer, "npi": _NPI, "tin": _TIN, "in_network": False,
         "source": "directory", "network_name": "083"},
    ]
    written = store.upsert(rows)
    assert written == 3, "all three networks are distinct facts"

    facts = store.facts_for(payer, npi=_NPI)
    assert {f.network_name for f in facts} == {"065", "082", "083"}
    assert {f.network_name: f.in_network for f in facts} == {"065": True, "082": False, "083": False}


@pytest.mark.db
def test_the_stored_set_does_not_depend_on_input_order():
    """The order-dependence is the sharp edge: reversed, the old key kept the OUT fact and dropped
    the IN one, turning an in-network provider into an out-of-network answer."""
    store, payer = ProviderNetworkStore(), _payer()
    store.upsert([
        {"payer_key": payer, "npi": _NPI, "tin": _TIN, "in_network": False,
         "source": "directory", "network_name": "082"},
        {"payer_key": payer, "npi": _NPI, "tin": _TIN, "in_network": True,
         "source": "directory", "network_name": "065"},
    ])
    facts = {f.network_name: f.in_network for f in store.facts_for(payer, npi=_NPI)}
    assert facts == {"082": False, "065": True}


@pytest.mark.db
def test_re_running_the_same_read_is_still_idempotent():
    """Widening the key must not break the property the store already had."""
    store, payer = ProviderNetworkStore(), _payer()
    rows = [
        {"payer_key": payer, "npi": _NPI, "tin": _TIN, "in_network": True,
         "source": "directory", "network_name": "065"},
        {"payer_key": payer, "npi": _NPI, "tin": _TIN, "in_network": False,
         "source": "directory", "network_name": "082"},
    ]
    assert store.upsert(rows) == 2
    assert store.upsert(rows) == 0, "a repeated ingest writes nothing"
    assert len(store.facts_for(payer, npi=_NPI)) == 2


@pytest.mark.db
def test_facts_without_a_network_name_still_dedupe():
    """TiC crosswalk rows carry no network_name; they must not multiply on re-ingest."""
    store, payer = ProviderNetworkStore(), _payer()
    row = [{"payer_key": payer, "npi": _NPI, "tin": _TIN, "in_network": True, "source": "tic"}]
    assert store.upsert(row) == 1
    assert store.upsert(row) == 0
    assert len(store.facts_for(payer, npi=_NPI)) == 1


@pytest.mark.db
def test_the_same_network_from_two_sources_is_two_facts():
    """A directory read and a TiC pull disagreeing about one network is the §4a case — both must be
    kept so the effective dates can arbitrate. Collapsing them would destroy the disagreement."""
    store, payer = ProviderNetworkStore(), _payer()
    store.upsert([
        {"payer_key": payer, "npi": _NPI, "tin": _TIN, "in_network": True,
         "source": "directory", "network_name": "065"},
        {"payer_key": payer, "npi": _NPI, "tin": _TIN, "in_network": False,
         "source": "tic", "network_name": "065"},
    ])
    facts = store.facts_for(payer, npi=_NPI)
    assert {(f.source, f.in_network) for f in facts} == {("directory", True), ("tic", False)}


# --- dated participation: what Oscar gives and nothing else records --------------------------------

@pytest.mark.db
def test_effective_dates_round_trip():
    """Oscar's `network_infos` carries start/end per network. Storing them is what lets a verdict be
    scoped to a DATE OF SERVICE instead of silently meaning "today"."""
    from datetime import date

    store, payer = ProviderNetworkStore(), _payer()
    store.upsert([{
        "payer_key": payer, "npi": _NPI, "tin": _TIN, "in_network": True, "source": "directory",
        "network_name": "065", "start_date": date(2026, 1, 27), "end_date": date(2026, 12, 31),
    }])
    f = store.facts_for(payer, npi=_NPI)[0]
    assert f.start_date == date(2026, 1, 27)
    assert f.end_date == date(2026, 12, 31)


@pytest.mark.db
def test_source_built_at_round_trips_separately_from_retrieved_at():
    """`retrieved_at` is when WE wrote the row; `source_built_at` is when the SOURCE was built (an MRF's
    publish date, a portal read). Only the second can say whether a source predates a contract, which
    is the comparison the §4a reconciliation rule needs — and nothing recorded it."""
    from datetime import datetime, timezone

    store, payer = ProviderNetworkStore(), _payer()
    built = datetime(2025, 11, 1, tzinfo=timezone.utc)
    store.upsert([{
        "payer_key": payer, "npi": _NPI, "tin": _TIN, "in_network": False, "source": "tic",
        "network_name": "065", "source_built_at": built,
    }])
    f = store.facts_for(payer, npi=_NPI)[0]
    assert f.source_built_at == built
    assert f.retrieved_at is not None and f.retrieved_at != built


@pytest.mark.db
def test_as_of_filters_to_facts_effective_on_that_date():
    """Sanders is in-network today and OUT from 2027-01-01. A verdict that cannot express that is
    already wrong for a January date of service."""
    from datetime import date

    store, payer = ProviderNetworkStore(), _payer()
    store.upsert([
        {"payer_key": payer, "npi": _NPI, "tin": _TIN, "in_network": True, "source": "directory",
         "network_name": "065", "start_date": date(2026, 1, 27), "end_date": date(2026, 12, 31)},
        {"payer_key": payer, "npi": _NPI, "tin": _TIN, "in_network": False, "source": "directory",
         "network_name": "065-term", "start_date": date(2027, 1, 1), "end_date": None},
    ])
    today = [f.in_network for f in store.facts_for(payer, npi=_NPI, as_of=date(2026, 7, 29))]
    january = [f.in_network for f in store.facts_for(payer, npi=_NPI, as_of=date(2027, 1, 15))]
    assert today == [True]
    assert january == [False]


@pytest.mark.db
def test_undated_facts_are_returned_for_any_as_of():
    """Only Oscar gives dates. An undated fact must not vanish when a date of service is supplied —
    absence of dating is not evidence the fact has expired."""
    from datetime import date

    store, payer = ProviderNetworkStore(), _payer()
    store.upsert([{"payer_key": payer, "npi": _NPI, "tin": _TIN, "in_network": True,
                   "source": "tic", "network_name": "PPO"}])
    assert len(store.facts_for(payer, npi=_NPI, as_of=date(2030, 1, 1))) == 1


@pytest.mark.db
def test_a_networks_timeline_is_kept_not_just_one_period():
    """A dated fact store must hold a network's PERIODS, not one row per network.

    Sanders' real Oscar history for net 065 is three periods — OUT until 2026-01-26, IN from
    2026-01-27, OUT again from 2027-01-01. They share a network_name, so a key without the period
    collapsed them and whichever was iterated first survived. Caught by running real Oscar data end to
    end: the expired 2019-2026 period won, and net 065 then had NO fact effective today at all.

    This is the same defect as the network collapse, one level down: identity must include everything
    that distinguishes a fact, and for a dated store that includes when it applies.
    """
    from datetime import date

    store, payer = ProviderNetworkStore(), _payer()
    rows = [
        {"payer_key": payer, "npi": _NPI, "tin": _TIN, "in_network": False, "source": "directory",
         "network_name": "065", "start_date": date(2019, 2, 6), "end_date": date(2026, 1, 26)},
        {"payer_key": payer, "npi": _NPI, "tin": _TIN, "in_network": True, "source": "directory",
         "network_name": "065", "start_date": date(2026, 1, 27), "end_date": date(2026, 12, 31)},
        {"payer_key": payer, "npi": _NPI, "tin": _TIN, "in_network": False, "source": "directory",
         "network_name": "065", "start_date": date(2027, 1, 1), "end_date": None},
    ]
    assert store.upsert(rows) == 3, "three periods are three facts"

    def on(day):
        return [f.in_network for f in store.facts_for(payer, npi=_NPI, as_of=day)
                if f.network_name == "065"]

    assert on(date(2025, 6, 1)) == [False], "before the contract began"
    assert on(date(2026, 7, 29)) == [True], "today"
    assert on(date(2027, 1, 15)) == [False], "after it terminates"
    assert store.upsert(rows) == 0, "still idempotent"
