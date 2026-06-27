import uuid, pytest
from sqlalchemy import text
from network_probe.db.session import tenant_session

@pytest.mark.db
def test_set_config_is_transaction_local():
    tid = uuid.uuid4()
    with tenant_session(tid) as s:
        assert s.execute(text("SELECT current_setting('app.tenant_id', true)")).scalar() == str(tid)
    # a fresh (possibly pooled) session must NOT carry the previous tenant's id
    other = uuid.uuid4()
    with tenant_session(other) as s2:
        got = s2.execute(text("SELECT current_setting('app.tenant_id', true)")).scalar()
        assert got == str(other) and got != str(tid)

def test_invalid_tenant_id_rejected():
    # empty / malformed tenant id must raise BEFORE any DB work (never set app.tenant_id='')
    with pytest.raises((ValueError, AttributeError, TypeError)):
        with tenant_session(""):
            pass
