"""The async portal-capture endpoints.

A walk takes 40-210s live, so /api/portal/capture returns a job id at once and the UI polls.
No browser is driven here — the job store's runner is stubbed.
"""

import pytest
from fastapi.testclient import TestClient

from network_probe.api import app
from network_probe.portal.jobs import CaptureJobStore
from network_probe.portal.models import PortalCapture, PortalStatus

AZ = "bcbs-empire-anthem-elevance-az"
NPI = "1346866332"


def _client():
    return TestClient(app, raise_server_exceptions=False)


def _stub_store(monkeypatch, capture=None, boom=False):
    def runner(q):
        if boom:
            raise RuntimeError("browser died")
        return capture or PortalCapture(
            payer_key=q.payer_key, npi=q.npi, status=PortalStatus.OUT_OF_NETWORK,
            portal_name="AZ Blue / HealthSparq", portal_url="https://azblue.healthsparq.com/x",
            driver="azblue-healthsparq", note="stub", screenshot="shot.png",
        )

    store = CaptureJobStore(runner=runner)
    monkeypatch.setattr("network_probe.portal.jobs.default_capture_jobs", lambda: store)
    return store


@pytest.mark.db
def test_capture_routes_require_auth():
    c = _client()
    assert c.post("/api/portal/capture", json={"payer_key": AZ, "npi": NPI}).status_code == 401
    assert c.get("/api/portal/capture/abc").status_code == 401


@pytest.mark.db
def test_start_returns_a_job_id_without_blocking(monkeypatch, auth_header):
    store = _stub_store(monkeypatch)
    r = _client().post(
        "/api/portal/capture",
        json={"payer_key": AZ, "npi": NPI, "provider_last_name": "Desir",
              "plan": "Statewide / National PPO", "zip": "85382"},
        headers=auth_header,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "queued" and body["job_id"]
    store.wait(body["job_id"], timeout=5)


@pytest.mark.db
def test_poll_reports_the_portal_verdict_and_screenshot(monkeypatch, auth_header):
    store = _stub_store(monkeypatch)
    c = _client()
    job_id = c.post("/api/portal/capture", json={"payer_key": AZ, "npi": NPI},
                    headers=auth_header).json()["job_id"]
    store.wait(job_id, timeout=5)
    body = c.get(f"/api/portal/capture/{job_id}", headers=auth_header).json()
    assert body["status"] == "done"
    assert body["verdict"] == "OUT_OF_NETWORK"
    assert body["screenshot"] == "shot.png"


@pytest.mark.db
def test_a_dead_browser_is_an_error_job_not_a_500(monkeypatch, auth_header):
    store = _stub_store(monkeypatch, boom=True)
    c = _client()
    job_id = c.post("/api/portal/capture", json={"payer_key": AZ, "npi": NPI},
                    headers=auth_header).json()["job_id"]
    store.wait(job_id, timeout=5)
    body = c.get(f"/api/portal/capture/{job_id}", headers=auth_header).json()
    assert body["status"] == "error"
    assert "browser died" in body["error"]


@pytest.mark.db
def test_invalid_npi_and_unknown_payer_are_rejected(monkeypatch, auth_header):
    _stub_store(monkeypatch)
    c = _client()
    assert c.post("/api/portal/capture", json={"payer_key": AZ, "npi": "123"},
                  headers=auth_header).status_code == 400
    assert c.post("/api/portal/capture", json={"payer_key": "no-such-payer", "npi": NPI},
                  headers=auth_header).status_code == 404


@pytest.mark.db
def test_unknown_job_is_404(monkeypatch, auth_header):
    _stub_store(monkeypatch)
    assert _client().get("/api/portal/capture/deadbeef", headers=auth_header).status_code == 404


@pytest.mark.db
def test_screenshot_requires_auth():
    """Names embed the NPI and a timestamp, and NPIs are public — unauthenticated access would
    make the set effectively enumerable, and which providers a clinic is checking is not public."""
    assert _client().get("/api/portal/screenshot/anything.png").status_code == 401


@pytest.mark.db
@pytest.mark.parametrize("name", ["../../../../etc/passwd", "..%2Fsecret.png", "notapng.txt", "a/b.png"])
def test_screenshot_endpoint_refuses_path_traversal(name, auth_header):
    """Screenshots are served by name, so the name must never walk out of LIVE_SHOT_DIR —
    checked with a VALID token, so containment is proven independently of the auth gate."""
    r = _client().get(f"/api/portal/screenshot/{name}", headers=auth_header)
    assert r.status_code in (400, 404), f"{name} returned {r.status_code}"


@pytest.mark.db
def test_provider_name_is_resolved_from_nppes_not_supplied_by_the_caller(monkeypatch, auth_header):
    """A portal is searched by PROVIDER name + clinic ZIP. The eligibility form's first/last name
    fields hold the MEMBER's name (they sit between Member ID and DOB), and a member name reaching
    a payer's public search box is exactly what HANDOFF §7 forbids. So when no provider name is
    given, the server resolves it from NPPES by NPI rather than trusting the caller."""
    seen = {}

    def runner(q):
        seen["first"], seen["last"] = q.provider_first_name, q.provider_last_name
        return PortalCapture(
            payer_key=q.payer_key, npi=q.npi, status=PortalStatus.UNKNOWN,
            portal_name="AZ Blue / HealthSparq", portal_url="u", driver="d", note="n",
        )

    store = CaptureJobStore(runner=runner)
    monkeypatch.setattr("network_probe.portal.jobs.default_capture_jobs", lambda: store)
    monkeypatch.setattr(
        "network_probe.domain.report_ingest._nppes_name", lambda npi, client: ("Hedson", "Desir")
    )
    job_id = _client().post(
        "/api/portal/capture", json={"payer_key": AZ, "npi": NPI}, headers=auth_header
    ).json()["job_id"]
    store.wait(job_id, timeout=5)
    assert seen == {"first": "Hedson", "last": "Desir"}


@pytest.mark.db
def test_poll_reconciles_the_portal_answer_into_the_verdict(monkeypatch, auth_header):
    """The Naar case: a public directory says IN, the member-facing portal says OON. The portal
    wins and the determination is recomputed — it must not sit beside an unchanged verdict."""
    _stub_store(monkeypatch)  # stub returns OUT_OF_NETWORK
    c = _client()
    job_id = c.post(
        "/api/portal/capture",
        json={"payer_key": AZ, "npi": NPI,
              "prior_network_status": "IN_NETWORK",
              "prior_source_url": "https://flex.optum.com/fhirpublic/R4",
              "out_of_network_benefits": True},
        headers=auth_header,
    ).json()["job_id"]
    from network_probe.portal.jobs import default_capture_jobs
    default_capture_jobs().wait(job_id, timeout=5)

    r = c.get(f"/api/portal/capture/{job_id}", headers=auth_header).json()["reconciled"]
    assert r["network_status_before"] == "IN_NETWORK"
    assert r["network_status_after"] == "OUT_OF_NETWORK"
    assert r["changed"] is True
    assert r["signal"]["result"] == "overrides"
    assert r["determination"]["code"] == "OUT_OF_NETWORK_WITH_BENEFITS"


@pytest.mark.db
def test_portal_disagreeing_with_contract_evidence_becomes_review(monkeypatch, auth_header):
    _stub_store(monkeypatch)  # OUT_OF_NETWORK
    c = _client()
    job_id = c.post(
        "/api/portal/capture",
        json={"payer_key": AZ, "npi": NPI,
              "prior_network_status": "IN_NETWORK", "prior_source_url": "credentialing-matrix"},
        headers=auth_header,
    ).json()["job_id"]
    from network_probe.portal.jobs import default_capture_jobs
    default_capture_jobs().wait(job_id, timeout=5)

    r = c.get(f"/api/portal/capture/{job_id}", headers=auth_header).json()["reconciled"]
    assert r["network_status_after"] == "REVIEW"
    assert r["determination"]["code"] == "REVIEW"


@pytest.mark.db
def test_no_prior_verdict_means_no_reconciliation_block(monkeypatch, auth_header):
    _stub_store(monkeypatch)
    c = _client()
    job_id = c.post("/api/portal/capture", json={"payer_key": AZ, "npi": NPI},
                    headers=auth_header).json()["job_id"]
    from network_probe.portal.jobs import default_capture_jobs
    default_capture_jobs().wait(job_id, timeout=5)
    assert "reconciled" not in c.get(f"/api/portal/capture/{job_id}", headers=auth_header).json()


@pytest.mark.db
def test_reconciled_payload_carries_the_committed_display_reading(monkeypatch, auth_header):
    """The tiles read `display_*`, so the poll response has to carry them.

    The UI lifts this block into the page (Eligibility.onPortalReconciled). Before that existed the
    tab rendered "Verdict updated UNKNOWN → OUT OF NETWORK" while the Determination tile a few
    pixels above still read "Not yet established" — two contradictory answers on one screen.
    """
    _stub_store(monkeypatch)  # OUT_OF_NETWORK
    c = _client()
    job_id = c.post(
        "/api/portal/capture",
        json={"payer_key": AZ, "npi": NPI, "prior_network_status": "UNKNOWN",
              "out_of_network_benefits": True},
        headers=auth_header,
    ).json()["job_id"]
    from network_probe.portal.jobs import default_capture_jobs
    default_capture_jobs().wait(job_id, timeout=5)

    d = c.get(f"/api/portal/capture/{job_id}", headers=auth_header).json()["reconciled"]["determination"]
    assert d["display_code"] == "OUT_OF_NETWORK_WITH_BENEFITS"
    assert d["display_label"]
    assert d["confidence"] == "high"  # the portal settled it — this is a finding, not a lean


@pytest.mark.db
def test_plan_type_fills_a_silent_271_through_the_capture(monkeypatch, auth_header):
    """A PPO member whose 271 said nothing about the OON tier is still "OON with benefits".

    `plan_oon_capability` travels from the eligibility response, through the UI, to this endpoint.
    The field was already accepted here but was never exposed on the eligibility response, so the
    UI had nothing to send and every silent-271 member reconciled to plain "Out-of-Network".
    """
    _stub_store(monkeypatch)  # OUT_OF_NETWORK
    c = _client()
    job_id = c.post(
        "/api/portal/capture",
        json={"payer_key": AZ, "npi": NPI, "prior_network_status": "UNKNOWN",
              "out_of_network_benefits": None, "plan_oon_capability": True},
        headers=auth_header,
    ).json()["job_id"]
    from network_probe.portal.jobs import default_capture_jobs
    default_capture_jobs().wait(job_id, timeout=5)

    d = c.get(f"/api/portal/capture/{job_id}", headers=auth_header).json()["reconciled"]["determination"]
    assert d["code"] == "OUT_OF_NETWORK_WITH_BENEFITS"
    assert "inferred from plan type" in d["reason"]


@pytest.mark.db
def test_an_inconclusive_walk_does_not_flip_the_displayed_lean(monkeypatch, auth_header):
    """`reconcile_portal` promises a portal that could not answer "changes nothing at all".

    It kept that promise for `network_status` but not for the determination: `_reconcile_capture`
    recomputed `final_determination` with NO evidence, because the capture request never carried
    the directory finding the eligibility check had. So a provider listed in the payer's own
    directory under 11 networks — a LIKELY_IN_NETWORK lean — silently became an out-of-network
    reading the moment an UNKNOWN walk came back.

    Caught live on 2026-08-03 (Orem, UHC FL): the walk returned "no results ... guest plan
    unconfirmed" and the Determination tile still moved from "In-Network (low confidence)" to
    "Out-of-Network with benefits (low confidence)". A portal that could not answer must not be
    able to move a verdict in EITHER direction.
    """
    from network_probe.portal.models import PortalCapture, PortalStatus

    _stub_store(monkeypatch, capture=PortalCapture(
        payer_key=AZ, npi=NPI, status=PortalStatus.UNKNOWN, portal_name="UHC Find Care (guest)",
        portal_url="https://findcare.guest.uhc.com/x", driver="uhc-findcare",
        note="no results — an empty result set does not distinguish OON from a failed search.",
    ))
    c = _client()
    job_id = c.post(
        "/api/portal/capture",
        json={"payer_key": AZ, "npi": NPI, "prior_network_status": "UNKNOWN",
              "out_of_network_benefits": True,
              "prior_evidence": {"directory_networks": 11, "in_directory": True,
                                 "payer_label": "UnitedHealthcare"}},
        headers=auth_header,
    ).json()["job_id"]
    from network_probe.portal.jobs import default_capture_jobs
    default_capture_jobs().wait(job_id, timeout=5)

    r = c.get(f"/api/portal/capture/{job_id}", headers=auth_header).json()["reconciled"]
    assert r["changed"] is False
    assert r["signal"]["result"] == "inconclusive"
    d = r["determination"]
    assert d["provisional"] == "LIKELY_IN_NETWORK", "the directory evidence must survive the walk"
    assert d["display_code"] == "IN_NETWORK", (
        "an inconclusive walk flipped the committed reading from IN to OON"
    )


@pytest.mark.db
def test_a_walk_that_searched_and_did_not_list_them_stops_the_in_lean(monkeypatch, auth_header):
    """The Orem demo case: 11 directory networks, none the member's, and the portal shows nothing.

    The walk stays UNKNOWN and moves no verdict — but it must stop the directory read from
    DISPLAYING as in-network. "IN NETWORK · low confidence" for a provider the payer's own
    member-facing directory does not list is the false IN this system exists to remove.
    """
    from network_probe.portal.models import PortalCapture, PortalStatus

    _stub_store(monkeypatch, capture=PortalCapture(
        payer_key=AZ, npi=NPI, status=PortalStatus.UNKNOWN, portal_name="UHC Find Care (guest)",
        portal_url="https://findcare.guest.uhc.com/x", driver="uhc-findcare", result_count=0,
        note="no results — an empty result set does not distinguish OON from a failed search.",
    ))
    c = _client()
    job_id = c.post(
        "/api/portal/capture",
        json={"payer_key": AZ, "npi": NPI, "prior_network_status": "UNKNOWN",
              "out_of_network_benefits": True,
              "prior_evidence": {"directory_networks": 11, "in_directory": True,
                                 "plan_given": True, "matched_network": False,
                                 "payer_label": "UnitedHealthcare"}},
        headers=auth_header,
    ).json()["job_id"]
    from network_probe.portal.jobs import default_capture_jobs
    default_capture_jobs().wait(job_id, timeout=5)

    r = c.get(f"/api/portal/capture/{job_id}", headers=auth_header).json()["reconciled"]
    assert r["network_status_after"] == "UNKNOWN"   # the walk moved nothing, correctly
    d = r["determination"]
    assert d["display_code"] == "OUT_OF_NETWORK_WITH_BENEFITS"
    assert d["provisional"] != "LIKELY_IN_NETWORK"


@pytest.mark.db
def test_a_blocked_walk_suppresses_nothing(monkeypatch, auth_header):
    """A portal that refused automated access never searched, so it says nothing about the provider.
    Only a walk that actually reached the directory may suppress the lean."""
    from network_probe.portal.models import PortalCapture, PortalStatus

    _stub_store(monkeypatch, capture=PortalCapture(
        payer_key=AZ, npi=NPI, status=PortalStatus.BLOCKED, portal_name="UHC Find Care (guest)",
        portal_url="https://findcare.guest.uhc.com/x", driver="uhc-findcare",
        note="portal shell did not hydrate.",
    ))
    c = _client()
    job_id = c.post(
        "/api/portal/capture",
        json={"payer_key": AZ, "npi": NPI, "prior_network_status": "UNKNOWN",
              "prior_evidence": {"directory_networks": 3, "plan_given": False}},
        headers=auth_header,
    ).json()["job_id"]
    from network_probe.portal.jobs import default_capture_jobs
    default_capture_jobs().wait(job_id, timeout=5)

    d = c.get(f"/api/portal/capture/{job_id}", headers=auth_header).json()["reconciled"]["determination"]
    assert d["provisional"] == "LIKELY_IN_NETWORK"


@pytest.mark.db
def test_a_walk_that_FOUND_them_never_counts_as_absence(monkeypatch, auth_header):
    """`portal_absent` must mean "searched and did NOT list them" — not merely "returned UNKNOWN".

    UHC's driver returns UNKNOWN with BOTH `result_count` and `matched_name` set when it finds the
    provider in the un-pinned directory but cannot confirm which network it searched. Keying the
    suppressor on `result_count is not None` alone therefore read a provider the portal had just
    FOUND as evidence against them, and flipped a likely-IN into an OON lean.

    This is Test 3 row 1 (Manayan, NPI 1902811656, UHC Medicare Advantage GA) — the single row with
    staff ground truth, and that ground truth is IN. Getting it backwards there is the worst
    available outcome.
    """
    from network_probe.portal.models import PortalCapture, PortalStatus

    _stub_store(monkeypatch, capture=PortalCapture(
        payer_key=AZ, npi=NPI, status=PortalStatus.UNKNOWN, portal_name="UHC Find Care (guest)",
        portal_url="https://findcare.guest.uhc.com/x", driver="uhc-findcare",
        result_count=4, matched_name="Conrad Chang Manayan",
        note="present in the un-pinned directory, but the guest plan could not be confirmed.",
    ))
    c = _client()
    job_id = c.post(
        "/api/portal/capture",
        json={"payer_key": AZ, "npi": NPI, "prior_network_status": "UNKNOWN",
              "prior_evidence": {"directory_networks": 3, "plan_given": False}},
        headers=auth_header,
    ).json()["job_id"]
    from network_probe.portal.jobs import default_capture_jobs
    default_capture_jobs().wait(job_id, timeout=5)

    d = c.get(f"/api/portal/capture/{job_id}", headers=auth_header).json()["reconciled"]["determination"]
    assert d["provisional"] == "LIKELY_IN_NETWORK", (
        "the portal found them — that cannot be evidence of absence"
    )
    assert d["display_code"] == "IN_NETWORK"
