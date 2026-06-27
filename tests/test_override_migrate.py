import json
import uuid

import pytest


@pytest.mark.db
def test_db_override_store_add_lookup_and_isolation(demo_tenant):
    from network_probe.domain.models import ProviderQuery
    from network_probe.domain.overrides import DbOverrideStore, Override
    DbOverrideStore(demo_tenant).add(Override(payer="devoted", npi="1629339312",
        status="OUT_OF_NETWORK", verified_by="Availity", verified_at="2026-05-21"))
    q = ProviderQuery(payer="devoted", plan_hint="PPO", npi="1629339312")
    found = DbOverrideStore(demo_tenant).lookup(q)
    assert found and found.status == "OUT_OF_NETWORK"
    # another tenant sees nothing (RLS isolation)
    from sqlalchemy.orm import Session

    from network_probe.db.base import owner_engine
    from network_probe.db.models import Tenant
    other = uuid.uuid4()
    with Session(owner_engine()) as s:
        s.add(Tenant(id=other, name="Other", slug=f"other-{other.hex[:6]}")); s.commit()
    assert DbOverrideStore(other).lookup(q) is None


@pytest.mark.db
def test_migrate_json_to_db_idempotent(tmp_path, demo_tenant):
    from network_probe.domain.models import ProviderQuery
    from network_probe.domain.overrides import DbOverrideStore
    from scripts.migrate_overrides import migrate
    p = tmp_path / "overrides.json"
    p.write_text(json.dumps([{"payer": "devoted", "npi": "1629339312", "status": "OUT_OF_NETWORK",
                              "verified_by": "Availity", "verified_at": "2026-05-21"}]))
    assert migrate(p, demo_tenant) == 1
    assert migrate(p, demo_tenant) == 0   # idempotent
    assert DbOverrideStore(demo_tenant).lookup(ProviderQuery(payer="devoted", plan_hint="", npi="1629339312")).status == "OUT_OF_NETWORK"


@pytest.mark.db
def test_check_eligibility_applies_tenant_override(demo_tenant):
    from network_probe.domain.models import NetworkStatus, ProviderQuery
    from network_probe.domain.overrides import DbOverrideStore, Override
    class FakeCat:
        def resolve(self, k): return None
    class FakeStedi:
        def check(self, q):
            from network_probe.domain.benefits import EligibilityResult
            return EligibilityResult(coverage_active=True, plan_name=None, group=None, coverage_dates={},
                network_status=NetworkStatus.UNKNOWN, benefits=[], pcp_required=None, prior_auth_required=None,
                referral_required=None, cob=None, network_verdict=None, corroboration=[], source_audit={})
    DbOverrideStore(demo_tenant).add(Override(payer="devoted", npi="1629339312",
        status="OUT_OF_NETWORK", verified_by="Availity", verified_at="2026-05-21"))
    # no directory adapter for 'devoted'? there is — avoid live call by monkeypatching check_network to raise
    import pytest as _p

    import network_probe.domain.eligibility as e
    monkey = _p.MonkeyPatch()
    monkey.setattr(e, "check_network", lambda q, **k: (_ for _ in ()).throw(ValueError("skip")))
    try:
        r = e.check_eligibility(ProviderQuery(payer="devoted", plan_hint="PPO", npi="1629339312"),
                                catalogue=FakeCat(), stedi=FakeStedi(), tenant_id=demo_tenant)
    finally:
        monkey.undo()
    assert r.network_status == NetworkStatus.OUT_OF_NETWORK
    assert any(c.get("source") == "override" for c in r.corroboration)
