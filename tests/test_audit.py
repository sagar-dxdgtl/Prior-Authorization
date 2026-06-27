import uuid, json, pytest
from network_probe.context import RequestContext
from network_probe.models import ProviderQuery, NetworkStatus
from network_probe.benefits import EligibilityResult
from network_probe.audit import write_audit

def _res():
    return EligibilityResult(coverage_active=True, plan_name=None, group=None, coverage_dates={},
        network_status=NetworkStatus.OUT_OF_NETWORK, benefits=[], pcp_required=None,
        prior_auth_required=None, referral_required=None, cob=None, network_verdict=None,
        corroboration=[], source_audit={"source": "stedi"})

@pytest.mark.db
def test_audit_hashes_and_encrypts_phi(demo_tenant):
    from sqlalchemy.orm import Session
    from network_probe.db.base import owner_engine
    from network_probe.db.models import EligibilityCheck
    from network_probe.crypto import hash_member_id
    from network_probe.config import get_settings
    ctx = RequestContext(tenant_id=demo_tenant, actor_id=uuid.uuid4(), role="user")
    q = ProviderQuery(payer="oscar", plan_hint="", npi="1679766943", member_id="MBR-123",
                      dob="1980-01-02", first_name="Jane", last_name="Doe")
    write_audit(ctx, "eligibility", q, _res(), "req-abc")
    with Session(owner_engine()) as s:
        row = s.query(EligibilityCheck).filter_by(request_id="req-abc").one()
        assert row.action == "eligibility" and row.status == "OUT_OF_NETWORK" and row.tenant_id == demo_tenant
        assert row.member_id_hash == hash_member_id("MBR-123", get_settings().member_id_pepper)
        assert row.member_id_enc and "MBR-123" not in row.member_id_enc      # encrypted, not plaintext
        assert row.dob_enc and "1980" not in row.dob_enc
        assert row.name_enc and "Jane" not in row.name_enc
        blob = json.dumps(row.result_jsonb) + json.dumps(row.source_audit)
        assert "MBR-123" not in blob and "Jane" not in blob and "1980" not in blob

@pytest.mark.db
def test_audit_without_phi_skips_crypto(demo_tenant):
    from sqlalchemy.orm import Session
    from network_probe.db.base import owner_engine
    from network_probe.db.models import EligibilityCheck
    ctx = RequestContext(tenant_id=demo_tenant, actor_id=uuid.uuid4(), role="user")
    q = ProviderQuery(payer="oscar", plan_hint="", npi="1679766943")   # no member/dob/name
    write_audit(ctx, "network", q, _res(), "req-nophi")
    with Session(owner_engine()) as s:
        row = s.query(EligibilityCheck).filter_by(request_id="req-nophi").one()
        assert row.member_id_hash is None and row.member_id_enc is None and row.name_enc is None
        assert row.action == "network"
