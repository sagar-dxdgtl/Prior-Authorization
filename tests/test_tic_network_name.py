"""The MRF states which network each provider group is in. Keep it.

Measured 2026-08-12 on BCBS SC's own in-network file
(`2026-08-01_bluecross_AI_in-network-rates_1_of_1.json.gz`, 126 MB gzipped, reached through the
index at d2vbl1kcu4hfid.cloudfront.net with a `Referer: https://provider.bcbssc.com/` and a US exit):

    {"provider_group_id":3800007931,
     "network_name":["PREFERRED BLUE"],
     "provider_groups":[{"npi":[1124443478,...],"tin":{"type":"ein","value":"62-1768106"}}]}

"PREFERRED BLUE" is the exact network the Sapphire portal pins for these members, so the MRF can
corroborate the portal *on the same network* rather than merely "somewhere at this payer". The
ingester threw it away and wrote `npi,tin,payer`, forcing the loader to label a whole file from a
`--network-name` flag. That is wrong in two directions: it cannot distinguish two networks inside one
file, and it lets a human type a label the data does not support. BCBS SC alone publishes 41
networks, and its index carries 24 distinct own-files plus 15,841 other Blues' BlueCard files.

Backwards compatible on purpose: `facts_from_crosswalk_csv` reads with `csv.DictReader`, so a CSV
written before this change simply has no `network` column and still falls back to the flag.
"""

from __future__ import annotations

import csv
import json

from network_probe.domain.tic_facts import facts_from_crosswalk_csv
from network_probe.domain.tic_ingest import ingest_tic

# Shaped exactly like the live BCBS SC file, including the network_name LIST.
BCBSSC = {
    "reporting_entity_name": "BlueCross BlueShield of South Carolina",
    "version": "2.0.0",
    "provider_references": [
        {"provider_group_id": 3800007931, "network_name": ["PREFERRED BLUE"],
         "provider_groups": [{"npi": [1124443478, 1366496937],
                              "tin": {"type": "ein", "value": "62-1768106"}}]},
        {"provider_group_id": 3800006553, "network_name": ["ADVANTAGE NETWORK"],
         "provider_groups": [{"npi": [1043395346],
                              "tin": {"type": "ein", "value": "57-0679807"}}]},
        # One group sold into two networks — the case a per-file label cannot express at all.
        {"provider_group_id": 3800009999, "network_name": ["PREFERRED BLUE", "STATE GROUP NETWORK"],
         "provider_groups": [{"npi": [1639148703],
                              "tin": {"type": "ein", "value": "92-1600050"}}]},
    ],
}


def _write(tmp_path, data):
    p = tmp_path / "mrf.json"
    p.write_text(json.dumps(data))
    return str(p)


def _rows(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_the_network_the_mrf_names_reaches_the_csv(tmp_path):
    out = str(tmp_path / "x.csv")
    ingest_tic(_write(tmp_path, BCBSSC), out, payer="bcbssc")
    by_npi = {r["npi"]: r for r in _rows(out)}
    assert by_npi["1124443478"]["network"] == "PREFERRED BLUE"
    assert by_npi["1043395346"]["network"] == "ADVANTAGE NETWORK"


def test_a_group_in_two_networks_becomes_two_facts(tmp_path):
    """A per-file label collapses this to one; the provider really is in both."""
    out = str(tmp_path / "x.csv")
    ingest_tic(_write(tmp_path, BCBSSC), out, payer="bcbssc")
    nets = sorted(r["network"] for r in _rows(out) if r["npi"] == "1639148703")
    assert nets == ["PREFERRED BLUE", "STATE GROUP NETWORK"]


def test_the_same_npi_tin_in_one_network_is_still_written_once(tmp_path):
    dupe = {"provider_references": [
        {"network_name": ["PREFERRED BLUE"],
         "provider_groups": [{"npi": [1124443478], "tin": {"type": "ein", "value": "62-1768106"}}]},
        {"network_name": ["PREFERRED BLUE"],
         "provider_groups": [{"npi": [1124443478], "tin": {"type": "ein", "value": "62-1768106"}}]},
    ]}
    out = str(tmp_path / "x.csv")
    assert ingest_tic(_write(tmp_path, dupe), out, payer="bcbssc") == 1


def test_a_file_that_names_no_network_still_ingests(tmp_path):
    """negotiated_rates groups carry no network_name — they must not vanish."""
    no_net = {"in_network": [{"negotiated_rates": [
        {"provider_groups": [{"npi": [1972603934], "tin": {"type": "ein", "value": "93-3510922"}}]}]}]}
    out = str(tmp_path / "x.csv")
    assert ingest_tic(_write(tmp_path, no_net), out, payer="uhc") == 1
    assert _rows(out)[0]["network"] == ""


def test_external_reference_files_keep_the_network_of_the_reference_that_pointed_at_them(tmp_path):
    """Cigna/Aetna style: the network is on the provider_reference, the groups are in another file."""
    ext = {"provider_references": [
        {"network_name": ["PREFERRED BLUE"], "location": "https://example.test/a.json"},
    ]}
    out = str(tmp_path / "x.csv")
    ingest_tic(
        _write(tmp_path, ext), out, payer="bcbssc",
        reference_resolver=lambda url: {"provider_groups": [
            {"npi": [1861933087], "tin": {"type": "ein", "value": "46-3812940"}}]},
    )
    r = _rows(out)[0]
    assert r["npi"] == "1861933087" and r["network"] == "PREFERRED BLUE"


class TestTheLoaderPrefersTheDataOverTheFlag:
    def test_the_csvs_own_network_wins_over_the_command_line_label(self, tmp_path):
        p = tmp_path / "c.csv"
        p.write_text("npi,tin,payer,network\n"
                     "1124443478,62-1768106,bcbssc,PREFERRED BLUE\n"
                     "1043395346,57-0679807,bcbssc,ADVANTAGE NETWORK\n")
        facts = facts_from_crosswalk_csv(p, "bcbs-south-carolina-ga-atlanta",
                                         network_name="WHATEVER THE OPERATOR TYPED")
        got = {f["npi"]: f["network_name"] for f in facts}
        assert got == {"1124443478": "PREFERRED BLUE", "1043395346": "ADVANTAGE NETWORK"}

    def test_one_npi_in_two_networks_yields_two_facts(self, tmp_path):
        p = tmp_path / "c.csv"
        p.write_text("npi,tin,payer,network\n"
                     "1639148703,92-1600050,bcbssc,PREFERRED BLUE\n"
                     "1639148703,92-1600050,bcbssc,STATE GROUP NETWORK\n")
        facts = facts_from_crosswalk_csv(p, "bcbs-south-carolina-ga-atlanta")
        assert sorted(f["network_name"] for f in facts) == ["PREFERRED BLUE", "STATE GROUP NETWORK"]

    def test_a_csv_written_before_this_change_still_loads_under_the_flag(self, tmp_path):
        """Three-column CSVs predate the network column and must keep working unchanged."""
        p = tmp_path / "old.csv"
        p.write_text("npi,tin,payer\n1124443478,62-1768106,bcbssc\n")
        facts = facts_from_crosswalk_csv(p, "bcbs-south-carolina-ga-atlanta",
                                         network_name="PREFERRED BLUE")
        assert facts[0]["network_name"] == "PREFERRED BLUE"

    def test_no_network_anywhere_leaves_the_fact_unlabelled(self, tmp_path):
        p = tmp_path / "old.csv"
        p.write_text("npi,tin,payer\n1124443478,62-1768106,bcbssc\n")
        facts = facts_from_crosswalk_csv(p, "bcbs-south-carolina-ga-atlanta")
        assert "network_name" not in facts[0]
