from network_probe.domain.credentialing import CredentialingMatrix, CredentialRecord


def test_lookup_exact_payer_npi_tin():
    m = CredentialingMatrix(records=[CredentialRecord("uhc-fl", "1760457477", "463812940", False, plan="AARP MA")])
    r = m.lookup("uhc-fl", "1760457477", "463812940")
    assert r is not None and r.in_network is False


def test_lookup_normalizes_tin_and_is_case_insensitive_on_payer():
    m = CredentialingMatrix(records=[CredentialRecord("humana-co", "1801837109", "475181686", True)])
    assert m.lookup("HUMANA-CO", "1801837109", "47-5181686").in_network is True  # dashes stripped, payer casefold


def test_lookup_miss_returns_none():
    assert CredentialingMatrix(records=[]).lookup("x", "1", "2") is None


def test_same_provider_and_tin_differ_by_payer():
    # The contract is payer-specific: this is what makes credentialing generalize, not an override.
    m = CredentialingMatrix(records=[
        CredentialRecord("bcbs-az", "1992078745", "843447602", True),
        CredentialRecord("mercy-care-az", "1992078745", "843447602", False),
    ])
    assert m.lookup("bcbs-az", "1992078745", "843447602").in_network is True
    assert m.lookup("mercy-care-az", "1992078745", "843447602").in_network is False


def test_plan_disambiguates_when_multiple_records_share_payer_npi_tin():
    m = CredentialingMatrix(records=[
        CredentialRecord("uhc-fl", "1760457477", "463812940", False, plan="Medicare Advantage"),
        CredentialRecord("uhc-fl", "1760457477", "463812940", True, plan="Commercial"),
    ])
    assert m.lookup("uhc-fl", "1760457477", "463812940", plan="AARP Medicare Advantage").in_network is False
    assert m.lookup("uhc-fl", "1760457477", "463812940", plan="UHC Commercial PPO").in_network is True


def test_loads_from_csv(tmp_path):
    p = tmp_path / "cred.csv"
    p.write_text("payer,npi,tin,in_network\nuhc-ga,1902811656,921600050,true\n")
    m = CredentialingMatrix(records=[], path=str(p))
    assert m.lookup("uhc-ga", "1902811656", "921600050").in_network is True


def test_check_network_short_circuits_to_credentialing(monkeypatch):
    # A credentialing hit is authoritative and settles the verdict WITHOUT the directory. The payer
    # key here has no adapter, so if credentialing did NOT short-circuit, get_adapter would raise.
    from network_probe.domain import credentialing, service
    from network_probe.domain.models import NetworkStatus, ProviderQuery

    fake = CredentialingMatrix(records=[
        CredentialRecord("uhc-fl", "1760457477", "463812940", False, plan="Medicare Advantage", source="test")])
    monkeypatch.setattr(credentialing, "default_credentialing", lambda: fake)
    q = ProviderQuery(payer="uhc-fl", plan_hint="AARP Medicare Advantage", npi="1760457477", tin="463812940")
    v = service.check_network(q, corroborate=False)
    assert v.status == NetworkStatus.OUT_OF_NETWORK and v.confidence == "high"
    assert "credential" in (v.notes or "").lower()


def test_check_network_without_tin_falls_through_to_adapter():
    # No billing TIN -> credentialing can't key on it -> normal (adapter) path, which raises for a
    # bogus payer. Proves credentialing only short-circuits when it actually has an answer.
    import pytest

    from network_probe.domain import service
    from network_probe.domain.models import ProviderQuery

    q = ProviderQuery(payer="totally-unknown", plan_hint="", npi="1", tin=None)
    with pytest.raises(ValueError):
        service.check_network(q, corroborate=False)


def test_seed_covers_the_fl_and_il_groups_tic_could_not():
    from network_probe.domain.credentialing import default_credentialing

    m = default_credentialing()
    # FL group (463812940) and IL group (843012976) — unreachable via TiC, present in credentialing
    assert m.lookup("unitedhealthcare-fl-south-florida", "1760457477", "463812940").in_network is False
    assert m.lookup("oscar-fl-south-florida", "1568423168", "463812940").in_network is False
    assert m.lookup("meridian-health-il", "1588744650", "843012976").in_network is False
    assert m.lookup("national-government-services-inc-ngs-il", "1770578221", "843012976").in_network is True
