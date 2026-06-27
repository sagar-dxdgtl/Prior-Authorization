import uuid

from network_probe.core.context import RequestContext
from network_probe.db import models  # noqa: F401
from network_probe.db.base import Base


def test_all_tables_registered():
    names = set(Base.metadata.tables.keys())
    assert {"tenants", "users", "payers", "eligibility_checks", "overrides"} <= names

def test_eligibility_check_has_action_and_name_enc():
    cols = Base.metadata.tables["eligibility_checks"].columns.keys()
    assert "action" in cols and "name_enc" in cols and "member_id_hash" in cols

def test_request_context_frozen():
    ctx = RequestContext(tenant_id=uuid.uuid4(), actor_id=uuid.uuid4(), role="user")
    import dataclasses

    import pytest
    with pytest.raises(dataclasses.FrozenInstanceError):
        ctx.role = "admin"
