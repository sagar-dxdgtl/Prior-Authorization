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


def test_long_portal_url_and_walk_trail_survive_the_roundtrip(store):
    """A capture must not be silently dropped for being well-evidenced.

    The clean Cigna run on 2026-07-28 read its answer from a 596-character URL whose tail —
    medicalProductCode/medicalEcnCode — is the proof of WHICH network answered, and the insert
    failed on `character varying(400)`. `run_capture._record` is best-effort, so the row vanished
    with only a log line. Both columns are TEXT as of migration 0030.
    """
    npi = f"9{uuid.uuid4().int % 10**9:09d}"
    url = (
        "https://hcpdirectory.cigna.com/web/public/consumer/directory/doctors?"
        + "&".join(f"param{i}=value-{i:03d}" for i in range(40))
        + "&medicalProductCode=OAP&medicalEcnCode=OA001"
    )
    trail = " → ".join(f"walk step {i} with an explanatory clause" for i in range(30))
    assert len(url) > 400 and len(trail) > 700, "fixture must exceed the OLD column caps"

    cap = _capture(PortalStatus.UNKNOWN, npi)
    cap.portal_url = url
    store.record(cap, walk_trail=trail)

    rows = store.history(npi)
    assert len(rows) == 1
    assert rows[0].portal_url == url  # not truncated
    assert rows[0].portal_url.endswith("medicalEcnCode=OA001")  # the proof survived
    assert rows[0].walk_trail == trail


def test_networks_accepted_roundtrips_and_is_queryable():
    """The provider-first network list must SURVIVE the capture, not live only inside the note prose.

    Storing it is what makes one walk reusable: AZ Blue names all 14 of Maydell's networks, and the
    payer publishes 24, so the stored list answers both directions without walking again.
    """
    store = PortalCaptureStore()
    npi = f"9{uuid.uuid4().int % 10**9:09d}"
    nets = ("Statewide PPO", "Alliance HMO", "Indemnity")
    rid = store.record(PortalCapture(
        payer_key="bcbs-empire-anthem-elevance-az", npi=npi, status=PortalStatus.IN_NETWORK,
        portal_name="AZ Blue / HealthSparq", portal_url="https://azblue.healthsparq.com/x",
        driver="azblue-healthsparq", note="stub", networks_accepted=nets,
    ))
    assert rid is not None

    from sqlalchemy import text
    with store.engine.connect() as c:
        got = c.execute(text("SELECT networks_accepted FROM portal_captures WHERE id = :i"),
                        {"i": rid}).scalar_one()
        assert got == list(nets)
        # The point of JSONB: ask "who is in this network?" without re-walking the portal.
        # CAST(), not ::jsonb — SQLAlchemy's text() reads the second ':' as a bind parameter.
        hit = c.execute(text(
            "SELECT npi FROM portal_captures "
            "WHERE networks_accepted @> CAST(:n AS jsonb) AND npi = :npi"),
            {"n": '["Alliance HMO"]', "npi": npi}).scalar_one_or_none()
        assert hit == npi


def test_networks_accepted_defaults_to_null_not_an_empty_list():
    """NULL means 'not asked'. An empty list would assert the provider is in NO networks, which is a
    different and false claim — and every capture written before 2026-07-31 was never asked."""
    store = PortalCaptureStore()
    npi = f"9{uuid.uuid4().int % 10**9:09d}"
    rid = store.record(PortalCapture(
        payer_key="cigna-healthcare-il", npi=npi, status=PortalStatus.UNKNOWN,
        portal_name="Cigna", portal_url="https://hcpdirectory.cigna.com/", driver="cigna-hcp",
        note="portal did not name networks",
    ))
    from sqlalchemy import text
    with store.engine.connect() as c:
        got = c.execute(text("SELECT networks_accepted FROM portal_captures WHERE id = :i"),
                        {"i": rid}).scalar_one()
    assert got is None
