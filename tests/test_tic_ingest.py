"""Tests for the streaming TiC in-network MRF ingester (pure / offline)."""

from __future__ import annotations

import csv
import gzip
import shutil
from pathlib import Path

from network_probe.domain.tic_ingest import ingest_tic
from network_probe.domain.tin_crosswalk import TinCrosswalk

FIXTURE = Path(__file__).parent / "fixtures" / "tic-sample.json"
_FIXTURE_LOCATION = Path(__file__).parent / "fixtures" / "tic-location-refs.json"
_FIXTURE_MIXED = Path(__file__).parent / "fixtures" / "tic-mixed.json"


def _read_rows(csv_path: str) -> list[dict]:
    with open(csv_path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


# ---------------------------------------------------------------------------
# Basic ingest from .json
# ---------------------------------------------------------------------------


def test_ingest_returns_3_rows(tmp_path):
    out = str(tmp_path / "out.csv")
    n = ingest_tic(str(FIXTURE), out, payer="uhc")
    assert n == 3


def test_ingest_csv_has_expected_pairs(tmp_path):
    out = str(tmp_path / "out.csv")
    ingest_tic(str(FIXTURE), out, payer="uhc")
    rows = _read_rows(out)
    pairs = {(r["npi"], r["tin"]) for r in rows}
    assert ("1972603934", "933510922") in pairs
    assert ("1710305735", "933510922") in pairs
    assert ("1679766943", "463812940") in pairs


def test_ingest_csv_payer_column(tmp_path):
    out = str(tmp_path / "out.csv")
    ingest_tic(str(FIXTURE), out, payer="uhc")
    rows = _read_rows(out)
    assert all(r["payer"] == "uhc" for r in rows)


# ---------------------------------------------------------------------------
# NPI filter
# ---------------------------------------------------------------------------


def test_npi_filter_returns_1_row(tmp_path):
    out = str(tmp_path / "out.csv")
    n = ingest_tic(str(FIXTURE), out, npi_filter={"1679766943"}, payer="uhc")
    assert n == 1
    rows = _read_rows(out)
    assert rows[0]["npi"] == "1679766943"
    assert rows[0]["tin"] == "463812940"


# ---------------------------------------------------------------------------
# .json.gz variant
# ---------------------------------------------------------------------------


def test_ingest_gz(tmp_path):
    gz_path = str(tmp_path / "tic-sample.json.gz")
    with open(str(FIXTURE), "rb") as f_in, gzip.open(gz_path, "wb") as f_out:
        shutil.copyfileobj(f_in, f_out)
    out = str(tmp_path / "out.csv")
    n = ingest_tic(gz_path, out, payer="uhc")
    assert n == 3
    rows = _read_rows(out)
    pairs = {(r["npi"], r["tin"]) for r in rows}
    assert ("1972603934", "933510922") in pairs
    assert ("1679766943", "463812940") in pairs


# ---------------------------------------------------------------------------
# TIN filter
# ---------------------------------------------------------------------------


def test_tin_filter_returns_2_rows(tmp_path):
    """tin_filter={"933510922"} should keep only the 2 NPIs under that TIN."""
    out = str(tmp_path / "out.csv")
    n = ingest_tic(str(FIXTURE), out, tin_filter={"933510922"}, payer="uhc")
    assert n == 2
    rows = _read_rows(out)
    npis = {r["npi"] for r in rows}
    assert npis == {"1972603934", "1710305735"}
    # The 463812940 TIN row must not appear
    tins = {r["tin"] for r in rows}
    assert tins == {"933510922"}


def test_tin_filter_dash_normalized(tmp_path):
    """93-3510922 should normalize to 933510922 and match the same rows."""
    out = str(tmp_path / "out.csv")
    n = ingest_tic(str(FIXTURE), out, tin_filter={"93-3510922"}, payer="uhc")
    assert n == 2
    rows = _read_rows(out)
    npis = {r["npi"] for r in rows}
    assert npis == {"1972603934", "1710305735"}


def test_tin_and_npi_filter_intersection(tmp_path):
    """When both filters active, a row must pass both (intersection)."""
    out = str(tmp_path / "out.csv")
    # npi_filter includes 1972603934 (under 933510922) and 1679766943 (under 463812940)
    # tin_filter includes only 933510922
    # Intersection: only 1972603934 survives
    n = ingest_tic(
        str(FIXTURE),
        out,
        npi_filter={"1972603934", "1679766943"},
        tin_filter={"933510922"},
        payer="uhc",
    )
    assert n == 1
    rows = _read_rows(out)
    assert rows[0]["npi"] == "1972603934"
    assert rows[0]["tin"] == "933510922"


# ---------------------------------------------------------------------------
# Integration: ingest -> TinCrosswalk -> TinScopeSource
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# External provider_reference.location resolution (Cigna-style)
# ---------------------------------------------------------------------------

_FAKE_RESOLVER_DATA = {
    "loc://az/1": {
        "provider_groups": [
            {"npi": [1972603934, 1710305735], "tin": {"type": "ein", "value": "933510922"}}
        ]
    },
    "loc://az/2": {
        "provider_groups": [
            {"npi": [1689726403], "tin": {"type": "ein", "value": "112233445"}}
        ]
    },
}


def _fake_resolver(url: str):
    return _FAKE_RESOLVER_DATA[url]


def test_location_refs_emits_rows_from_resolved_files(tmp_path):
    """Pure location-ref file: resolver called, 3 rows emitted across both refs."""
    out = str(tmp_path / "out.csv")
    n = ingest_tic(
        str(_FIXTURE_LOCATION),
        out,
        payer="cigna",
        reference_resolver=_fake_resolver,
    )
    assert n == 3
    rows = _read_rows(out)
    pairs = {(r["npi"], r["tin"]) for r in rows}
    assert ("1972603934", "933510922") in pairs
    assert ("1710305735", "933510922") in pairs
    assert ("1689726403", "112233445") in pairs


def test_location_refs_with_tin_filter(tmp_path):
    """tin_filter={"933510922"} with location refs → only 2 NPIs under that TIN emitted."""
    out = str(tmp_path / "out.csv")
    n = ingest_tic(
        str(_FIXTURE_LOCATION),
        out,
        tin_filter={"933510922"},
        payer="cigna",
        reference_resolver=_fake_resolver,
    )
    assert n == 2
    rows = _read_rows(out)
    npis = {r["npi"] for r in rows}
    assert npis == {"1972603934", "1710305735"}


def test_mixed_inline_and_location_both_contribute(tmp_path):
    """Mixed file: inline group (463812940) + resolved location (933510922) both appear."""
    out = str(tmp_path / "out.csv")
    n = ingest_tic(
        str(_FIXTURE_MIXED),
        out,
        payer="aetna",
        reference_resolver=_fake_resolver,
    )
    assert n == 3
    rows = _read_rows(out)
    pairs = {(r["npi"], r["tin"]) for r in rows}
    assert ("1679766943", "463812940") in pairs  # inline
    assert ("1972603934", "933510922") in pairs  # resolved
    assert ("1710305735", "933510922") in pairs  # resolved


def test_resolver_error_skips_ref_continues_run(tmp_path):
    """When resolver raises for one URL, that ref is skipped; others still emitted."""

    def _flaky_resolver(url: str):
        if url == "loc://az/1":
            raise RuntimeError("simulated CDN error")
        return _FAKE_RESOLVER_DATA[url]

    out = str(tmp_path / "out.csv")
    n = ingest_tic(
        str(_FIXTURE_LOCATION),
        out,
        payer="cigna",
        reference_resolver=_flaky_resolver,
    )
    # loc://az/2 still resolves → 1 NPI from it
    assert n == 1
    rows = _read_rows(out)
    assert rows[0]["npi"] == "1689726403"
    assert rows[0]["tin"] == "112233445"


def test_no_resolver_with_location_file_returns_0(tmp_path):
    """Without a resolver, location-only file silently returns 0 (existing behavior guard)."""
    out = str(tmp_path / "out.csv")
    n = ingest_tic(str(_FIXTURE_LOCATION), out, payer="cigna")
    assert n == 0


def test_integration_crosswalk_and_tin_scope(tmp_path):
    from network_probe.domain.corroboration import TinScopeSource
    from network_probe.domain.models import NetworkStatus, NetworkVerdict, ProviderQuery

    out = str(tmp_path / "crosswalk.csv")
    ingest_tic(str(FIXTURE), out, payer="uhc")

    cw = TinCrosswalk(path=out)
    assert "933510922" in cw.tins_for("uhc", "1972603934")

    src = TinScopeSource(crosswalk=cw)

    verdict_in = NetworkVerdict(
        NetworkStatus.IN_NETWORK,
        {"npi": "1972603934", "name": "Test Provider"},
        "uhc",
        "u",
        "high",
        "in-network per directory",
    )

    # billing TIN in the in-network set -> corroborates (or at least not contradicts)
    q_ok = ProviderQuery(payer="uhc", plan_hint="PPO", npi="1972603934", tin="933510922")
    sig_ok = src.check(q_ok, verdict_in)
    assert sig_ok is not None and sig_ok.result != "contradicts"

    # billing TIN NOT in the set -> contradicts
    q_bad = ProviderQuery(payer="uhc", plan_hint="PPO", npi="1972603934", tin="000000000")
    sig_bad = src.check(q_bad, verdict_in)
    assert sig_bad is not None and sig_bad.result == "contradicts"
