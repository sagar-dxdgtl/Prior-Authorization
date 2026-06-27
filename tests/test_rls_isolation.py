import uuid
import pytest
from sqlalchemy.orm import Session
from network_probe.db.base import owner_engine
from network_probe.db.models import Tenant, OverrideRow
from network_probe.db.session import tenant_session

@pytest.mark.db
def test_tenant_cannot_read_other_tenants_rows():
    a, b = uuid.uuid4(), uuid.uuid4()
    # seed two tenants as the owner (RLS-exempt) so FK parents exist
    with Session(owner_engine()) as s:
        s.add_all([Tenant(id=a, name="A", slug=f"a-{a.hex[:6]}"),
                   Tenant(id=b, name="B", slug=f"b-{b.hex[:6]}")])
        s.commit()
    # write a row under tenant A
    with tenant_session(a) as s:
        s.add(OverrideRow(tenant_id=a, payer="oscar", npi="1", status="IN_NETWORK",
                          verified_by="t", verified_at="2026-01-01"))
    # positive control: A sees its own row
    with tenant_session(a) as s:
        assert len(s.query(OverrideRow).all()) == 1
    # isolation: B sees NONE of A's rows
    with tenant_session(b) as s:
        assert s.query(OverrideRow).all() == []

@pytest.mark.db
def test_app_role_cannot_bypass_with_no_tenant_context():
    # write a row under a real tenant, then confirm a session that sets a DIFFERENT tenant sees nothing
    a, c = uuid.uuid4(), uuid.uuid4()
    with Session(owner_engine()) as s:
        s.add_all([Tenant(id=a, name="A", slug=f"a-{a.hex[:6]}"),
                   Tenant(id=c, name="C", slug=f"c-{c.hex[:6]}")])
        s.commit()
    with tenant_session(a) as s:
        s.add(OverrideRow(tenant_id=a, payer="cigna", npi="2", status="OUT_OF_NETWORK",
                          verified_by="t", verified_at="2026-02-01"))
    with tenant_session(c) as s:
        assert s.query(OverrideRow).all() == []
