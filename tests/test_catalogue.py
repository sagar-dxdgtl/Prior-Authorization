import pytest

from network_probe.payers.roster_seed import ROSTER, payer_rows


def test_roster_includes_verified_payers():
    triples = {(l, b, s): (sid, e) for (l, b, s, sid, e) in ROSTER}
    assert triples[("Oscar", "ACA", "AZ")] == ("OSCAR", "supported")
    assert any(l == "Humana" and sid == "61101" for (l, b, s, sid, e) in ROSTER)
    assert any(l == "UnitedHealthcare" and sid == "87726" for (l, b, s, sid, e) in ROSTER)
    assert any(l == "Devoted Health" and sid == "DEVOT" for (l, b, s, sid, e) in ROSTER)
    assert len(ROSTER) >= 50


def test_payer_rows_well_formed():
    r0 = next(iter(payer_rows()))
    assert set(r0) == {
        "tenant_id",
        "key",
        "label",
        "benefit_type",
        "state",
        "stedi_payer_id",
        "enrollment_status",
        "network_indicator_supported",
    }
    assert r0["tenant_id"] is None


@pytest.mark.db
def test_resolve_against_seeded_roster():
    from sqlalchemy.orm import Session

    from network_probe.db.base import owner_engine
    from network_probe.db.models import Payer
    from network_probe.payers.catalogue import DbPayerCatalogue

    with Session(owner_engine()) as s:  # _clean_db truncated payers; seed as owner
        for r in payer_rows():
            s.add(Payer(**r))
        s.commit()
    cat = DbPayerCatalogue()
    assert cat.resolve("oscar").stedi_payer_id == "OSCAR"
    assert cat.resolve("cigna-fhir").stedi_payer_id == "62308"  # via ADAPTER_ALIASES
    assert cat.resolve("humana-fhir").stedi_payer_id == "61101"
    assert cat.resolve("uhc").stedi_payer_id == "87726"
    assert cat.resolve("definitely-not-a-payer-xyz") is None
