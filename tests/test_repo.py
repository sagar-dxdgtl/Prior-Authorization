import uuid

import pytest

from network_probe.db.repo import EligibilityCheckRepo, OverrideRepo
from network_probe.db.session import tenant_session


@pytest.mark.db
def test_override_repo_add_and_lookup(demo_tenant):
    with tenant_session(demo_tenant) as s:
        repo = OverrideRepo(s, demo_tenant)
        repo.add(payer="oscar", npi="123", status="OUT_OF_NETWORK", verified_by="ops:jdoe", verified_at="2026-06-01")
        found = repo.lookup(payer="oscar", npi="123")
        assert found and found.status == "OUT_OF_NETWORK" and found.tenant_id == demo_tenant


@pytest.mark.db
def test_add_rejects_tenant_id_in_kwargs(demo_tenant):
    # passing tenant_id via kwargs must collide with the context-bound value (no cross-tenant write)
    with tenant_session(demo_tenant) as s:
        repo = OverrideRepo(s, demo_tenant)
        with pytest.raises(TypeError):
            repo.add(
                tenant_id=uuid.uuid4(),
                payer="x",
                npi="1",
                status="IN_NETWORK",
                verified_by="a",
                verified_at="2026-01-01",
            )


@pytest.mark.db
def test_eligibility_check_repo_records(demo_tenant):
    with tenant_session(demo_tenant) as s:
        repo = EligibilityCheckRepo(s, demo_tenant)
        row = repo.record(
            action="eligibility", payer_key="oscar", status="IN_NETWORK", result_jsonb={"ok": True}, source_audit={}
        )
        assert row.tenant_id == demo_tenant and row.action == "eligibility"
