"""portal_captures roundtrip, against a real Postgres (RLS is the thing being exercised).

Marked `db` because RLS + FORCE ROW LEVEL SECURITY behaviour cannot be reproduced against SQLite or a
mock: the property under test is that a global (tenant_id NULL) capture is insertable and readable by
the app role, which is a policy question, not an ORM question.
"""

from __future__ import annotations

import uuid

import pytest

from network_probe.portal.models import PortalCapture, PortalStatus, Reachability
from network_probe.portal.plan_match import match_plan
from network_probe.portal.store import PortalCaptureStore

pytestmark = pytest.mark.db


def _capture(status: PortalStatus, npi: str, **kw) -> PortalCapture:
    return PortalCapture(
        payer_key="unitedhealthcare-fl-south-florida",
        npi=npi,
        status=status,
        portal_name="UHC Find Care (guest)",
        portal_url="https://findcare.guest.uhc.com/guest-plan-selection/browse",
        driver="uhc-findcare",
        note="store roundtrip test",
        plan="AARP Medicare Advantage from UHC FL-0026 (PPO) / H2406018000",
        tin="463812940",
        reachability=Reachability.SEARCHABLE,
        duration_ms=1234,
        **kw,
    )


@pytest.fixture
def store() -> PortalCaptureStore:
    return PortalCaptureStore()


def test_decisive_capture_roundtrips_with_plan_provenance(store):
    """A decisive capture records the plan it pinned and why — the verdict is only valid for that plan."""
    npi = f"9{uuid.uuid4().int % 10**9:09d}"  # unique per run; the table is append-only
    m = match_plan(
        "AARP Medicare Advantage from UHC FL-0026 (PPO) / H2406018000",
        ["AARP Medicare Advantage CareFlex from UHC FL-35 (HMO-POS)",
         "AARP Medicare Advantage Patriot from UHC FL-0026 (PPO)"],
    )
    assert m is not None and m.confirms_network  # identifier match — precondition for an OON

    cap = _capture(PortalStatus.OUT_OF_NETWORK, npi, result_count=2)
    row_id = store.record(cap, plan_match=m, walk_trail="overlays → coverage: Medicare → plan pinned")
    assert row_id is not None, "write failed — check RLS grants for the app role"

    got = store.latest(cap.payer_key, npi)
    assert got is not None
    assert got.status == "OUT_OF_NETWORK"
    assert got.in_network is False
    assert got.plan_pinned == "AARP Medicare Advantage Patriot from UHC FL-0026 (PPO)"
    assert "FL-0026" in (got.plan_match_basis or "")
    assert got.result_count == 2
    assert "plan pinned" in (got.walk_trail or "")


@pytest.mark.parametrize("status", [PortalStatus.UNKNOWN, PortalStatus.BLOCKED])
def test_non_answers_are_recorded_but_in_network_stays_null(store, status):
    """UNKNOWN and BLOCKED are evidence and must persist — but neither may present as a boolean."""
    npi = f"9{uuid.uuid4().int % 10**9:09d}"
    assert store.record(_capture(status, npi)) is not None

    hist = store.history(npi)
    assert len(hist) == 1
    assert hist[0].status == status.value
    assert hist[0].in_network is None, "a non-answer must not be readable as in/out of network"

    # latest() defaults to decisive-only, so a non-answer never surfaces as an answer.
    assert store.latest("unitedhealthcare-fl-south-florida", npi) is None
    assert store.latest("unitedhealthcare-fl-south-florida", npi, decisive_only=False) is not None


def test_append_only_keeps_every_capture_for_the_same_identity(store):
    """No dedup, no upsert: the directory-accuracy trail is the point."""
    npi = f"9{uuid.uuid4().int % 10**9:09d}"
    for st in (PortalStatus.IN_NETWORK, PortalStatus.BLOCKED, PortalStatus.OUT_OF_NETWORK):
        assert store.record(_capture(st, npi)) is not None

    hist = store.history(npi)
    assert len(hist) == 3, "captures must accumulate, not overwrite"
    # Newest first, and the newest decisive one is what latest() returns.
    assert store.latest("unitedhealthcare-fl-south-florida", npi).status == "OUT_OF_NETWORK"


def test_missing_identifiers_are_safe(store):
    assert store.latest("", "1234567893") is None
    assert store.latest("some-payer", "") is None
    assert store.history("") == []
