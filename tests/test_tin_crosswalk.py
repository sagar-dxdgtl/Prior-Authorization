"""Tests for the NPI→TIN crosswalk loader (Phase 3) and its use by TinScopeSource."""

from __future__ import annotations

import json

from network_probe.domain.corroboration import TinScopeSource
from network_probe.domain.models import NetworkStatus, NetworkVerdict, ProviderQuery
from network_probe.domain.tin_crosswalk import TinCrosswalk


def _v(tins=None):
    mp = {"npi": "1629339312", "name": "Jing Li"}
    if tins is not None:
        mp["in_network_tins"] = tins
    return NetworkVerdict(NetworkStatus.IN_NETWORK, mp, "devoted CO PPO", "u", "high", "n")


def test_empty_crosswalk_is_falsy():
    cw = TinCrosswalk(path="/nonexistent/x.json")
    assert not cw and cw.tins_for("devoted", "1629339312") == []


def test_load_json_dict(tmp_path):
    p = tmp_path / "x.json"
    p.write_text(json.dumps({"devoted": {"1629339312": ["111111111", "222222222"]}}))
    cw = TinCrosswalk(path=str(p))
    assert cw and cw.tins_for("devoted", "1629339312") == ["111111111", "222222222"]
    assert cw.tins_for("oscar", "1629339312") == []  # payer-scoped


def test_load_json_list_and_records():
    cw = TinCrosswalk(records=[{"payer": "uhc", "npi": "1972603934", "tin": "463812940"}])
    assert cw.tins_for("uhc", "1972603934") == ["463812940"]


def test_load_csv_and_wildcard_payer(tmp_path):
    p = tmp_path / "x.csv"
    p.write_text("npi,tin,payer\n1629339312,111111111,devoted\n1700000000,999999999,\n")
    cw = TinCrosswalk(path=str(p))
    assert cw.tins_for("devoted", "1629339312") == ["111111111"]
    # blank payer stored under "*" → matches any payer
    assert cw.tins_for("anything", "1700000000") == ["999999999"]


def test_tinscope_uses_crosswalk_when_directory_has_none():
    cw = TinCrosswalk(records=[{"payer": "devoted", "npi": "1629339312", "tin": "111111111"}])
    src = TinScopeSource(crosswalk=cw)
    # member bills under the in-network TIN -> corroborates
    q_ok = ProviderQuery(payer="devoted", plan_hint="PPO", npi="1629339312", tin="111111111")
    assert src.check(q_ok, _v()).result == "corroborates"
    # member bills under a different TIN -> contradicts (group-level OON)
    q_bad = ProviderQuery(payer="devoted", plan_hint="PPO", npi="1629339312", tin="463812940")
    assert src.check(q_bad, _v()).result == "contradicts"


def test_tinscope_crosswalk_fires_even_when_verdict_is_not_in_network():
    # A TiC-confirmed contract is independent evidence even when the directory adapter itself
    # couldn't confidently determine network status (UNKNOWN/ambiguous plan match) or found the
    # provider OON -- the crosswalk must not be skipped just because verdict.status isn't IN_NETWORK.
    # Since the verdict ISN'T IN_NETWORK, a real TIN match here CONTRADICTS that verdict (it's
    # evidence the directory missed/couldn't determine), not "corroborates" -- that wording only
    # makes sense when confirming an already-IN_NETWORK verdict's specific billing TIN.
    cw = TinCrosswalk(records=[{"payer": "cigna-healthcare-co-denver", "npi": "1629339312", "tin": "475181686"}])
    src = TinScopeSource(crosswalk=cw)
    unknown_verdict = NetworkVerdict(
        NetworkStatus.UNKNOWN, {"npi": "1629339312", "name": "Jing Li"}, "cigna", "u", "medium", "ambiguous"
    )
    q = ProviderQuery(payer="cigna-healthcare-co-denver", plan_hint="x", npi="1629339312", tin="475181686")
    s = src.check(q, unknown_verdict)
    assert s.result == "contradicts" and "475181686" in s.detail and "crosswalk" in s.detail.lower()


def test_default_crosswalk_has_seeded_uhc_uvc():
    # TiC-verified UnitedHealthcare TX-exchange mapping (United Vein & Vascular Centers, TIN 933510922).
    from network_probe.domain.tin_crosswalk import default_crosswalk

    assert default_crosswalk().tins_for("uhc", "1972603934") == ["933510922"]
    assert default_crosswalk().tins_for("uhc", "1710305735") == ["933510922"]


def test_default_crosswalk_has_2026_07_08_tic_sweep_findings():
    # Real, independently-verified findings from the 2026-07-08 UVC demo-cases TiC sweep.
    from network_probe.domain.tin_crosswalk import default_crosswalk

    cw = default_crosswalk()
    assert cw.tins_for("cigna-healthcare-co-denver", "1629339312") == ["475181686"]
    assert cw.tins_for("kaiser-permanente-co-denver", "1598895435") == ["475181686"]
    assert cw.tins_for("unitedhealthcare-az", "1992078745") == ["843447602"]
    assert cw.tins_for("ambetter-centene-tx-dallas", "1710305735") == ["412049581", "933510922"]


def test_tinscope_corroborates_uhc_fradkin_via_seed():
    # IN directory verdict + billing TIN 933510922 matched against the seeded crosswalk -> corroborates
    q = ProviderQuery(
        payer="uhc", plan_hint="Bronze Essential", npi="1972603934", provider_last_name="Fradkin", tin="933510922"
    )
    v = NetworkVerdict(
        NetworkStatus.IN_NETWORK,
        {"npi": "1972603934", "name": "Kevin Fradkin"},
        "uhc / TX Individual Exchange",
        "u",
        "high",
        "n",
    )
    s = TinScopeSource().check(q, v)
    assert s.result == "corroborates" and "933510922" in s.detail and "crosswalk" in s.detail.lower()
