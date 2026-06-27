import pytest


@pytest.mark.db
def test_seed_admin(seed_admin):
    from sqlalchemy.orm import Session

    from network_probe.db.base import owner_engine
    from network_probe.db.models import User

    with Session(owner_engine()) as s:
        u = s.query(User).filter_by(username="admin").one()
        assert u.tenant_id == seed_admin["tenant_id"] and u.must_change_password is True


@pytest.mark.db
def test_auth_header_is_valid_access_token(auth_header):
    from network_probe.auth import jwt_tokens as jt

    tok = auth_header["Authorization"].split()[1]
    assert jt.decode_token(tok, expected_typ="access")["typ"] == "access"


@pytest.mark.db
def test_seed_payers(seed_payers):
    from sqlalchemy.orm import Session

    from network_probe.db.base import owner_engine
    from network_probe.db.models import Payer

    with Session(owner_engine()) as s:
        assert s.query(Payer).filter_by(key="oscar").one().stedi_payer_id == "OSCAR"


@pytest.mark.db
def test_admin_can_be_reseeded_after_truncate(seed_admin):
    # proves _clean_db truncated the prior test's 'admin' so the global-unique
    # lower(username) index is not violated by re-seeding.
    assert seed_admin["username"] == "admin"
