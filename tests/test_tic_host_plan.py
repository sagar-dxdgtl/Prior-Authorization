"""Picking the right in-network file out of a BlueCard control-plan index.

WHY A NEW SELECTOR AND NOT `pull_tic_index.select_files`. That one matches the state needle as a
substring of the LOCATION URL as well as the description — correct for a Cigna-style index whose
URLs encode the state, and catastrophic for a BlueCard index whose URLs are signed. Measured against
BCBS SC's real index (15,998 entries, 169 descriptions, 49 issuers) on 2026-08-12:

    --state SC  -> 15,872 of 15,998 entries        (99.2% — a filter that filters nothing)
    --state GA  ->  5,773 entries, headed by CareFirst BCBS, BCBS Alabama, BCBS Tennessee,
                    BCBS Minnesota, BCBS Wyoming — and no Georgia file in the top six

The needle hits random "ga"/"fl"/"sc" inside base64 signatures. Downloading that selection is
thousands of files and the wrong ones.

WHY THIS MATTERS AT ALL: a Blue's own MRF holds only its home-state providers (BCBS SC's is 8,534
pairs, 12/12 sampled in SC), so an out-of-state member is reached through the HOST plan and a TiC
check has to select by the CLINIC's state. See roster_seed's BCBS SC note.

WHAT IT REFUSES TO DO. `Anthem BCBS` carries 19 networks and names no state; Anthem operates in many.
CareFirst is MD/DC/VA. An issuer that cannot be tied to one state is never auto-selected — the
selector declines and names what it saw, because picking the wrong host plan produces a confidently
wrong network answer, which is the one failure this layer refuses.
"""

from __future__ import annotations

import pytest

from network_probe.domain.tic_index import (
    IndexFile,
    host_plan_files,
    index_files,
    parse_description,
)

# Issuer strings verbatim from the live index (2026-08-12).
LIVE_ISSUERS = [
    "BCBS Georgia - BlueChoice PPO",
    "BCBS Georgia - Blue Open Access POS",
    "BCBS Georgia - BCBS Georgia PAR providers",
    "BCBS Florida - Blue Choice PPO",
    "BCBS Florida - NetworkBlue",
    "BCBS South Carolina - PREFERRED BLUE",
    "BCBS Illinois - Blue Choice Options",
    "BCBS Tennessee, Inc. - Blue Network P",
    "CareFirst BCBS - Select Preferred Provider",
    "Anthem BCBS - Blue Access",
    "Empire BCBS - PPO",
    "Horizon BCBS New Jersey, Inc. - Managed Care",
    "Triple-S Management Corporation - BlueCard PPO",
    "Highmark BCBS WV - PPOBlue",
    "BCBS U.S.V.I. - US Virgin Island BlueCard PPO",
]


def _index(descriptions):
    return {"reporting_structure": [{
        "reporting_plans": [{"plan_name": "X", "plan_market_type": "group"}],
        "in_network_files": [
            {"description": d, "location": f"https://cdn.test/{i}.json.gz?Signature=aGZsc2NnYQ=="}
            for i, d in enumerate(descriptions)
        ],
    }]}


class TestDescriptionParsing:
    @pytest.mark.parametrize("desc,issuer,network", [
        ("BCBS Georgia - BlueChoice PPO", "BCBS Georgia", "BlueChoice PPO"),
        ("BCBS South Carolina - PREFERRED BLUE", "BCBS South Carolina", "PREFERRED BLUE"),
        # The network name itself contains " - "; only the FIRST separator splits.
        ("BCBS Georgia - Blue Open Access - POS", "BCBS Georgia", "Blue Open Access - POS"),
        ("Triple-S Management Corporation - BlueCard PPO",
         "Triple-S Management Corporation", "BlueCard PPO"),
    ])
    def test_issuer_and_network_split_on_the_first_separator(self, desc, issuer, network):
        assert parse_description(desc) == (issuer, network)

    def test_a_description_with_no_separator_is_all_issuer(self):
        assert parse_description("Some Plan") == ("Some Plan", "")


class TestTheSignedUrlIsNeverMatched:
    def test_a_signature_containing_the_state_letters_does_not_select_a_file(self):
        """The exact defect: 'ga'/'fl'/'sc' appear inside base64 signatures."""
        files = index_files(_index(["BCBS Tennessee, Inc. - Blue Network P"]))
        picked, _ = host_plan_files(files, "GA")
        assert picked == [], "matched a Tennessee file because its signed URL contained 'ga'"


class TestHostPlanSelection:
    def test_georgia_selects_only_georgia(self):
        files = index_files(_index(LIVE_ISSUERS))
        picked, why = host_plan_files(files, "GA")
        assert {f.issuer for f in picked} == {"BCBS Georgia"}
        assert len(picked) == 3, f"all three GA networks, got {[f.network for f in picked]}"
        assert "BCBS Georgia" in why

    def test_florida_selects_only_florida(self):
        files = index_files(_index(LIVE_ISSUERS))
        picked, _ = host_plan_files(files, "FL")
        assert {f.issuer for f in picked} == {"BCBS Florida"}
        assert sorted(f.network for f in picked) == ["Blue Choice PPO", "NetworkBlue"]

    def test_it_never_picks_ONE_network_for_the_caller(self):
        """Which of a host's networks a member reaches is a plan fact, not an index fact. The
        selector hands back every candidate; choosing one here would be a guess."""
        files = index_files(_index(LIVE_ISSUERS))
        picked, _ = host_plan_files(files, "GA")
        assert len(picked) > 1

    def test_a_state_named_issuer_is_matched_by_its_full_name_not_its_abbreviation(self):
        """'BCBS South Carolina' must not be reachable as 'NC', nor 'North Carolina' as 'CA'."""
        files = index_files(_index(["BCBS South Carolina - PREFERRED BLUE",
                                    "BCBS North Carolina - Blue Options"]))
        assert {f.issuer for f in host_plan_files(files, "SC")[0]} == {"BCBS South Carolina"}
        assert {f.issuer for f in host_plan_files(files, "NC")[0]} == {"BCBS North Carolina"}
        assert host_plan_files(files, "CA")[0] == []

    def test_an_abbreviation_in_the_issuer_name_still_matches(self):
        files = index_files(_index(["Highmark BCBS WV - PPOBlue"]))
        picked, _ = host_plan_files(files, "WV")
        assert [f.issuer for f in picked] == ["Highmark BCBS WV"]

    @pytest.mark.parametrize("issuer,state", [
        ("Empire BCBS - PPO", "NY"),
        ("Horizon BCBS New Jersey, Inc. - Managed Care", "NJ"),
        ("Triple-S Management Corporation - BlueCard PPO", "PR"),
        ("BCBS U.S.V.I. - US Virgin Island BlueCard PPO", "VI"),
    ])
    def test_curated_aliases_cover_blues_not_named_after_their_state(self, issuer, state):
        files = index_files(_index([issuer]))
        assert len(host_plan_files(files, state)[0]) == 1


class TestItDeclinesRatherThanGuess:
    def test_a_multi_state_issuer_is_never_auto_selected(self):
        """Anthem carries 19 networks in this index and names no state. Picking one of its files for
        a Georgia clinic would be a confidently wrong network answer."""
        files = index_files(_index(["Anthem BCBS - Blue Access", "Anthem Central - Blue Access"]))
        picked, why = host_plan_files(files, "GA")
        assert picked == []
        assert "anthem" in why.lower()

    def test_a_state_with_no_host_file_declines_and_names_what_was_there(self):
        files = index_files(_index(["BCBS Georgia - BlueChoice PPO"]))
        picked, why = host_plan_files(files, "WY")
        assert picked == []
        assert "BCBS Georgia" in why, "say what the index DID offer, so the operator can choose"

    def test_an_unknown_state_code_declines(self):
        files = index_files(_index(LIVE_ISSUERS))
        assert host_plan_files(files, "ZZ")[0] == []
        assert host_plan_files(files, "")[0] == []


class TestDeduplication:
    def test_the_same_file_repeated_across_reporting_structures_is_returned_once(self):
        """The live index repeats each file across 71 reporting structures — 15,998 entries for 169
        real files. Returning them all would download the same URL scores of times."""
        data = {"reporting_structure": [
            {"reporting_plans": [], "in_network_files": [
                {"description": "BCBS Georgia - BlueChoice PPO",
                 "location": "https://cdn.test/ga.json.gz?Expires=1&Signature=a"}]},
            {"reporting_plans": [], "in_network_files": [
                {"description": "BCBS Georgia - BlueChoice PPO",
                 "location": "https://cdn.test/ga.json.gz?Expires=2&Signature=b"}]},
        ]}
        assert len(index_files(data)) == 1, "same file, two signatures — one download"


def test_indexfile_keeps_the_signed_url_it_was_given():
    """Signed URLs expire, so the caller must fetch the one the index just handed out."""
    files = index_files(_index(["BCBS Georgia - BlueChoice PPO"]))
    assert isinstance(files[0], IndexFile)
    assert files[0].location.startswith("https://cdn.test/")


class TestShardedNetworks:
    """A network is usually SEVERAL files. Measured live: BCBS Georgia's BlueChoice PPO is 1_of_2
    and 2_of_2; BCBS Wyoming's Wyoming Select runs to 13_of_13. Ingesting one shard and reading the
    result as the whole network would manufacture an absence."""

    def _sharded(self):
        return {"reporting_structure": [{"reporting_plans": [], "in_network_files": [
            {"description": "BCBS Georgia - BlueChoice PPO",
             "location": "https://cdn.test/2026-08_102_11B0_in-network-rates_1_of_2.json.gz?Signature=a"},
            {"description": "BCBS Georgia - BlueChoice PPO",
             "location": "https://cdn.test/2026-08_102_11B0_in-network-rates_2_of_2.json.gz?Signature=b"},
            {"description": "BCBS Georgia - Blue Open Access POS",
             "location": "https://cdn.test/2026-08_102_11M0_in-network-rates_1_of_1.json.gz?Signature=c"},
        ]}]}

    def test_shards_of_one_network_are_all_kept(self):
        files = index_files(self._sharded())
        assert len(files) == 3, "de-dup must not collapse 1_of_2 and 2_of_2 into one"

    def test_by_network_groups_the_shards(self):
        from network_probe.domain.tic_index import by_network

        groups = by_network(index_files(self._sharded()))
        assert sorted(groups) == ["Blue Open Access POS", "BlueChoice PPO"]
        assert len(groups["BlueChoice PPO"]) == 2

    def test_the_reason_counts_networks_and_files_without_repeating_names(self):
        picked, why = host_plan_files(index_files(self._sharded()), "GA")
        assert len(picked) == 3
        assert "2 network(s) in 3 file(s)" in why
        assert why.count("BlueChoice PPO") == 1, f"network name repeated per shard: {why}"
