"""Async portal-capture jobs.

A portal walk takes 40-210 seconds live (measured 2026-07-28: Molina 40s, AZ Blue 73/78s,
Cigna 211s), so it cannot run inside an HTTP eligibility request. This layer runs it on a
worker thread and lets the UI poll — the fast verdict renders immediately and the portal
proof lands when the walk finishes.

Captures are serialised (one worker) on purpose: HANDOFF §7 caps this at human-scale volume,
and parallel browser sessions are what got Cigna's F5 to refuse us.
"""

from __future__ import annotations

import threading

import pytest

from network_probe.portal.jobs import CaptureJobStore
from network_probe.portal.models import PortalCapture, PortalStatus, PortalQuery


def _q(npi="1346866332"):
    return PortalQuery(
        payer_key="bcbs-empire-anthem-elevance-az", npi=npi,
        provider_last_name="Desir", plan="Statewide / National PPO", zip_code="85382",
    )


def _capture(status=PortalStatus.OUT_OF_NETWORK, **kw):
    return PortalCapture(
        payer_key="bcbs-empire-anthem-elevance-az", npi="1346866332", status=status,
        portal_name="AZ Blue / HealthSparq", portal_url="https://azblue.healthsparq.com/x",
        driver="azblue-healthsparq", note="test capture", **kw,
    )


def test_submitted_job_is_pending_before_the_walk_finishes():
    gate = threading.Event()
    store = CaptureJobStore(runner=lambda q: (gate.wait(5), _capture())[1])
    job_id = store.submit(_q())
    assert store.get(job_id).status in ("queued", "running")
    assert store.get(job_id).capture is None
    gate.set()
    store.wait(job_id, timeout=5)


def test_completed_job_exposes_the_capture():
    store = CaptureJobStore(runner=lambda q: _capture(screenshot="shot.png", result_count=139))
    job_id = store.submit(_q())
    job = store.wait(job_id, timeout=5)
    assert job.status == "done"
    assert job.capture.status == PortalStatus.OUT_OF_NETWORK
    assert job.capture.screenshot == "shot.png"


def test_a_failing_walk_becomes_an_error_job_not_an_exception():
    """run_capture is documented never to raise, but a job must survive it if that ever changes."""
    def boom(q):
        raise RuntimeError("browser died")

    store = CaptureJobStore(runner=boom)
    job = store.wait(store.submit(_q()), timeout=5)
    assert job.status == "error"
    assert "browser died" in (job.error or "")
    assert job.capture is None


def test_unknown_job_id_is_none_not_an_error():
    assert CaptureJobStore(runner=lambda q: _capture()).get("nope") is None


def test_captures_are_serialised_never_run_in_parallel():
    """Human-scale volume (§7): two submissions must not drive two browsers at once."""
    live, peak = [], []
    lock = threading.Lock()

    def runner(q):
        with lock:
            live.append(1)
            peak.append(len(live))
        threading.Event().wait(0.05)
        with lock:
            live.pop()
        return _capture()

    store = CaptureJobStore(runner=runner)
    ids = [store.submit(_q(npi=f"100000000{i}")) for i in range(4)]
    for i in ids:
        store.wait(i, timeout=10)
    assert max(peak) == 1, f"expected serialised captures, saw {max(peak)} concurrent"


def test_to_dict_is_json_safe_for_the_poll_endpoint():
    store = CaptureJobStore(runner=lambda q: _capture(screenshot="s.png"))
    d = store.wait(store.submit(_q()), timeout=5).to_dict()
    assert d["status"] == "done"
    assert d["verdict"] == "OUT_OF_NETWORK"
    assert d["screenshot"] == "s.png"
    import json

    json.dumps(d)  # must not raise
