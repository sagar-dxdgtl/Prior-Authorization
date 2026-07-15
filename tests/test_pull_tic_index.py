"""Tests for scripts/pull_tic_index.py (pure / offline — no network).

The network is never touched: every test that exercises ``run`` injects a fake
downloader that maps URLs to pre-existing local paths (the index temp file and
the existing ``tests/fixtures/tic-sample.json`` fixture).
"""

from __future__ import annotations

import csv
import gzip
import json
import sys
from pathlib import Path

# scripts/ is not importable as a package member, so add it to sys.path.
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from pull_tic_index import parse_index, run, select_files  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "tic-sample.json"


# ---------------------------------------------------------------------------
# Fixture index data
# ---------------------------------------------------------------------------

_INDEX_DATA = {
    "reporting_structure": [
        {
            "reporting_plans": [
                {"plan_name": "Cigna AZ HMO", "plan_market_type": "group"},
                {"plan_name": "Cigna AZ PPO", "plan_market_type": "group"},
            ],
            "in_network_files": [
                {
                    "description": "Cigna Arizona HMO network",
                    "location": "https://mrf.cigna.com/az_hmo.json.gz",
                },
                {
                    "description": "Cigna Arizona PPO network",
                    "location": "https://mrf.cigna.com/az_ppo.json.gz",
                },
            ],
        },
        {
            "reporting_plans": [
                {"plan_name": "Cigna TX HMO", "plan_market_type": "individual"},
            ],
            "in_network_files": [
                {
                    "description": "Cigna Texas HMO network",
                    "location": "https://mrf.cigna.com/tx_hmo.json.gz",
                },
            ],
        },
    ]
}


# ---------------------------------------------------------------------------
# parse_index
# ---------------------------------------------------------------------------


def test_parse_index_entry_count():
    entries = parse_index(_INDEX_DATA)
    assert len(entries) == 3


def test_parse_index_carries_plan_names():
    entries = parse_index(_INDEX_DATA)
    az = next(e for e in entries if "az_hmo" in e["location"])
    assert "Cigna AZ HMO" in az["plans"]
    assert "Cigna AZ PPO" in az["plans"]


def test_parse_index_carries_market():
    entries = parse_index(_INDEX_DATA)
    az = next(e for e in entries if "az_hmo" in e["location"])
    assert "group" in az["market"]
    tx = next(e for e in entries if "tx_hmo" in e["location"])
    assert "individual" in tx["market"]


def test_parse_index_carries_description():
    entries = parse_index(_INDEX_DATA)
    az = next(e for e in entries if "az_hmo" in e["location"])
    assert az["description"] == "Cigna Arizona HMO network"


def test_parse_index_location_field():
    entries = parse_index(_INDEX_DATA)
    locations = {e["location"] for e in entries}
    assert "https://mrf.cigna.com/az_hmo.json.gz" in locations
    assert "https://mrf.cigna.com/tx_hmo.json.gz" in locations


def test_parse_index_empty_structure():
    assert parse_index({"reporting_structure": []}) == []
    assert parse_index({}) == []


# ---------------------------------------------------------------------------
# select_files
# ---------------------------------------------------------------------------


def test_select_files_no_filter_returns_all():
    entries = parse_index(_INDEX_DATA)
    assert len(select_files(entries)) == 3


def test_select_files_state_az_returns_2():
    entries = parse_index(_INDEX_DATA)
    selected = select_files(entries, state="AZ")
    assert len(selected) == 2
    for e in selected:
        assert "az" in e["location"].lower()


def test_select_files_state_case_insensitive():
    entries = parse_index(_INDEX_DATA)
    assert len(select_files(entries, state="az")) == len(select_files(entries, state="AZ")) == 2


def test_select_files_state_matches_plan_name_when_url_lacks_it():
    # location has no "arizona" token but the description does.
    entries = [
        {
            "location": "https://mrf.cigna.com/file1.json.gz",
            "plans": ["Cigna Arizona HMO"],
            "market": ["group"],
            "description": "network for arizona members",
        }
    ]
    assert len(select_files(entries, state="arizona")) == 1


def test_select_files_state_no_match_returns_empty():
    entries = parse_index(_INDEX_DATA)
    assert select_files(entries, state="NY") == []


def test_select_files_plan_contains_filters():
    entries = parse_index(_INDEX_DATA)
    # All three entries carry at least one plan name containing "HMO".
    assert len(select_files(entries, plan_contains="HMO")) == 3


def test_select_files_plan_contains_market():
    entries = parse_index(_INDEX_DATA)
    selected = select_files(entries, plan_contains="individual")
    assert len(selected) == 1
    assert "tx_hmo" in selected[0]["location"]


def test_select_files_plan_contains_no_match():
    entries = parse_index(_INDEX_DATA)
    assert select_files(entries, plan_contains="NONEXISTENT") == []


def test_select_files_both_filters_intersection():
    entries = parse_index(_INDEX_DATA)
    selected = select_files(entries, state="TX", plan_contains="HMO")
    assert len(selected) == 1
    assert "tx_hmo" in selected[0]["location"]


# ---------------------------------------------------------------------------
# End-to-end with a fake downloader
# ---------------------------------------------------------------------------


def _fake_downloader(url_to_path: dict[str, str]):
    def _fake(url: str) -> str:
        return url_to_path[url]

    return _fake


def _write_index(tmp_path: Path, locations: list[str]) -> tuple[str, str]:
    """Write an index whose single reporting_structure lists *locations*.

    Returns ``(index_url, index_path)``.
    """
    files = [{"description": f"file {i}", "location": loc} for i, loc in enumerate(locations)]
    data = {
        "reporting_structure": [
            {
                "reporting_plans": [{"plan_name": "Cigna AZ HMO", "plan_market_type": "group"}],
                "in_network_files": files,
            }
        ]
    }
    index_path = tmp_path / "index.json"
    index_path.write_text(json.dumps(data), encoding="utf-8")
    return "fake://index.json", str(index_path)


def test_run_end_to_end_tin_filter(tmp_path):
    """run() with a tin_file -> out_csv has only the 2 rows for that TIN."""
    sample_url = "fake://sample.json"
    index_url, index_path = _write_index(tmp_path, [sample_url])
    fake = _fake_downloader({index_url: index_path, sample_url: str(FIXTURE)})

    tin_file = tmp_path / "tins.txt"
    tin_file.write_text("933510922\n", encoding="utf-8")

    out_csv = str(tmp_path / "out.csv")
    n = run(
        index_url=index_url,
        out_csv=out_csv,
        tin_file=str(tin_file),
        downloader=fake,
        payer="cigna",
    )

    assert n == 2
    with open(out_csv, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert {r["npi"] for r in rows} == {"1972603934", "1710305735"}
    assert {r["tin"] for r in rows} == {"933510922"}
    assert all(r["payer"] == "cigna" for r in rows)


def test_run_two_file_index_deduped(tmp_path):
    """2-file index -> rows from both files, deduplicated."""
    url_a = "fake://sample_a.json"
    url_b = "fake://sample_b.json"
    index_url, index_path = _write_index(tmp_path, [url_a, url_b])
    # Both point at the same fixture -> 3 unique pairs after dedup (not 6).
    fake = _fake_downloader(
        {index_url: index_path, url_a: str(FIXTURE), url_b: str(FIXTURE)}
    )

    out_csv = str(tmp_path / "out.csv")
    n = run(index_url=index_url, out_csv=out_csv, downloader=fake, payer="cigna")

    assert n == 3
    with open(out_csv, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 3
    pairs = {(r["npi"], r["tin"]) for r in rows}
    assert ("1972603934", "933510922") in pairs
    assert ("1710305735", "933510922") in pairs
    assert ("1679766943", "463812940") in pairs


def test_run_list_only_returns_0_no_csv(tmp_path):
    """--list mode: returns 0 and does not create out_csv."""
    sample_url = "fake://sample.json"
    index_url, index_path = _write_index(tmp_path, [sample_url])
    fake = _fake_downloader({index_url: index_path})

    out_csv = str(tmp_path / "out.csv")
    result = run(index_url=index_url, out_csv=out_csv, downloader=fake, list_only=True)

    assert result == 0
    assert not Path(out_csv).exists()


def test_run_select_no_match_warns_returns_0(tmp_path, capsys):
    """state filter that matches nothing -> 0 rows, warning printed, no csv."""
    sample_url = "fake://sample.json"
    index_url, index_path = _write_index(tmp_path, [sample_url])
    fake = _fake_downloader({index_url: index_path})

    out_csv = str(tmp_path / "out.csv")
    result = run(index_url=index_url, out_csv=out_csv, state="NY", downloader=fake)

    assert result == 0
    assert not Path(out_csv).exists()
    captured = capsys.readouterr()
    assert "WARNING" in captured.out
    # The available file should be listed for the operator.
    assert sample_url in captured.out


# ---------------------------------------------------------------------------
# gzip-compressed index file (scripts/pull_tic_index.py:271-277)
# ---------------------------------------------------------------------------


def test_run_gzip_compressed_index_file(tmp_path):
    """run() transparently decompresses a .gz-suffixed downloaded index file.

    Regression coverage for the gzip-detection branch in Step 1 of run():
    on the pre-fix code, ``open(index_local, "rt", encoding="utf-8")`` on raw
    gzip bytes raises UnicodeDecodeError before json.load ever runs. The
    in-network file itself is served plain (uncompressed) so this test
    isolates the INDEX file's gzip path specifically.
    """
    sample_url = "fake://sample.json"
    index_data = {
        "reporting_structure": [
            {
                "reporting_plans": [{"plan_name": "Cigna AZ HMO", "plan_market_type": "group"}],
                "in_network_files": [{"description": "file 0", "location": sample_url}],
            }
        ]
    }
    gz_index_path = tmp_path / "index.json.gz"
    with gzip.open(gz_index_path, "wt", encoding="utf-8") as f:
        json.dump(index_data, f)

    index_url = "fake://index.json.gz"
    fake = _fake_downloader({index_url: str(gz_index_path), sample_url: str(FIXTURE)})

    out_csv = str(tmp_path / "out.csv")
    n = run(index_url=index_url, out_csv=out_csv, downloader=fake, payer="cigna")

    assert n == 3
    with open(out_csv, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 3
    pairs = {(r["npi"], r["tin"]) for r in rows}
    assert ("1972603934", "933510922") in pairs
    assert ("1710305735", "933510922") in pairs
    assert ("1679766943", "463812940") in pairs


# ---------------------------------------------------------------------------
# --limit flag (scripts/pull_tic_index.py:286)
# ---------------------------------------------------------------------------


def _counting_downloader(url_to_path: dict[str, str]):
    """Like _fake_downloader, but records every URL it is called with."""
    calls: list[str] = []

    def _fake(url: str) -> str:
        calls.append(url)
        return url_to_path[url]

    _fake.calls = calls  # type: ignore[attr-defined]
    return _fake


def _write_multi_file_index(tmp_path: Path, n: int) -> tuple[str, str, list[str]]:
    """Write an index with *n* distinct in-network file entries.

    Returns ``(index_url, index_path, in_network_urls)``.
    """
    urls = [f"fake://sample_{i}.json" for i in range(n)]
    index_url, index_path = _write_index(tmp_path, urls)
    return index_url, index_path, urls


def test_run_limit_caps_file_count(tmp_path):
    """--limit 2 against a 4-file index only downloads/ingests 2 files."""
    index_url, index_path, urls = _write_multi_file_index(tmp_path, 4)
    url_to_path = {index_url: index_path, **{u: str(FIXTURE) for u in urls}}
    fake = _counting_downloader(url_to_path)

    out_csv = str(tmp_path / "out.csv")
    run(index_url=index_url, out_csv=out_csv, downloader=fake, payer="cigna", limit=2)

    in_network_calls = [u for u in fake.calls if u != index_url]  # type: ignore[attr-defined]
    assert len(in_network_calls) == 2
    # select_files preserves index order, so the first 2 entries are kept.
    assert set(in_network_calls) == {urls[0], urls[1]}


def test_run_limit_none_selects_all(tmp_path):
    """limit=None (the default) is unaffected -- all entries are selected.

    Uses a call-counting downloader against a 4-file index so the assertion
    is a genuine proof of "no cap applied", rather than relying on
    incidental dedup of identical fixture content across files.
    """
    index_url, index_path, urls = _write_multi_file_index(tmp_path, 4)
    url_to_path = {index_url: index_path, **{u: str(FIXTURE) for u in urls}}
    fake = _counting_downloader(url_to_path)

    out_csv = str(tmp_path / "out.csv")
    run(index_url=index_url, out_csv=out_csv, downloader=fake, payer="cigna")

    in_network_calls = [u for u in fake.calls if u != index_url]  # type: ignore[attr-defined]
    assert len(in_network_calls) == 4
    assert set(in_network_calls) == set(urls)


def test_run_limit_zero_selects_none(tmp_path, capsys):
    """--limit 0 selects zero files (0 is a valid cap, not "no limit").

    Regression coverage for the ``limit else`` -> ``limit is not None``
    truthiness fix: 0 is falsy in Python, so the pre-fix code treated
    ``--limit 0`` identically to no limit at all (selecting everything).
    """
    index_url, index_path, urls = _write_multi_file_index(tmp_path, 3)
    url_to_path = {index_url: index_path, **{u: str(FIXTURE) for u in urls}}
    fake = _counting_downloader(url_to_path)

    out_csv = str(tmp_path / "out.csv")
    result = run(index_url=index_url, out_csv=out_csv, downloader=fake, payer="cigna", limit=0)

    assert result == 0
    assert not Path(out_csv).exists()
    in_network_calls = [u for u in fake.calls if u != index_url]  # type: ignore[attr-defined]
    assert in_network_calls == []
    captured = capsys.readouterr()
    assert "WARNING" in captured.out
