"""Loader from an ingest_tic crosswalk CSV into `provider_network_facts`.

This closes the gap that made P2 possible in the first place: `scripts/ingest_tic.py` writes a
CSV, `tic_network_status` reads `provider_network_facts`, and nothing joined the two — so every
MRF pull landed somewhere the signal could not see. See HANDOFF-2026-07-28.md §1 P2.
"""

from network_probe.domain.tic_facts import facts_from_crosswalk_csv

_PAYER = "oscar-ga-atlanta"


def _csv(tmp_path, body):
    p = tmp_path / "crosswalk.csv"
    p.write_text("npi,tin,payer\n" + body)
    return p


def test_builds_in_network_facts_with_the_given_payer_key(tmp_path):
    rows = facts_from_crosswalk_csv(_csv(tmp_path, "1619937786,92-1600050,oscar\n"), _PAYER)
    assert rows == [
        {
            "payer_key": _PAYER,
            "npi": "1619937786",
            "tin": "921600050",  # dashes stripped
            "in_network": True,  # an in-network MRF only ever yields in-network facts
            "source": "tic",
        }
    ]


def test_csv_payer_column_is_ignored_in_favour_of_the_roster_key(tmp_path):
    """ingest_tic writes the short key ("oscar"); the store is keyed by market-qualified roster key."""
    rows = facts_from_crosswalk_csv(_csv(tmp_path, "1619937786,921600050,oscar\n"), _PAYER)
    assert rows[0]["payer_key"] == _PAYER


def test_incomplete_rows_are_skipped(tmp_path):
    body = "1619937786,921600050,oscar\n,921600050,oscar\n1669458766,,oscar\n"
    rows = facts_from_crosswalk_csv(_csv(tmp_path, body), _PAYER)
    assert [r["npi"] for r in rows] == ["1619937786"]


def test_duplicate_pairs_are_collapsed(tmp_path):
    body = "1619937786,921600050,oscar\n1619937786,92-1600050,oscar\n1669458766,921600050,oscar\n"
    rows = facts_from_crosswalk_csv(_csv(tmp_path, body), _PAYER)
    assert len(rows) == 2
    assert sorted(r["npi"] for r in rows) == ["1619937786", "1669458766"]


def test_optional_network_name_is_carried_through(tmp_path):
    rows = facts_from_crosswalk_csv(
        _csv(tmp_path, "1619937786,921600050,oscar\n"), _PAYER, network_name="Individual Georgia HMO Open Access"
    )
    assert rows[0]["network_name"] == "Individual Georgia HMO Open Access"
