"""Tests for the streaming TiC in-network MRF ingester (pure / offline)."""

from __future__ import annotations

import csv
import gzip
import shutil
from pathlib import Path

from network_probe.domain.tic_ingest import ingest_tic
from network_probe.domain.tin_crosswalk import TinCrosswalk

FIXTURE = Path(__file__).parent / "fixtures" / "tic-sample.json"


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
# Integration: ingest -> TinCrosswalk -> TinScopeSource
# ---------------------------------------------------------------------------


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
