"""Async portal-capture jobs — the bridge between a live eligibility check and a portal walk.

A walk takes 40-210 seconds against a real payer (measured 2026-07-28: Molina 40s, AZ Blue 73/78s,
Cigna 211s), so it cannot run inside an HTTP request. `check_eligibility` returns the fast verdict
(directory + TiC + credentialing + PBP) immediately; the caller submits a capture here and polls
until the portal proof lands.

Two deliberate constraints:

* **One worker.** Captures are serialised, never parallel. HANDOFF §7 caps this at human-scale
  volume — one lookup per provider — and it was three walks in ten minutes that got Cigna's F5
  BIG-IP to refuse us. `run_capture` also holds a process-wide lock, so a second worker would only
  queue behind it anyway.
* **In-memory job state.** The *result* is durable (run_capture appends it to `portal_captures`);
  only the in-flight status lives here, so a restart loses nothing but a spinner.

A job never raises to the caller: `run_capture` is itself documented never to raise, and if that
ever changes the job records the error instead of taking the request down with it.
"""

from __future__ import annotations

import threading
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field

from network_probe.portal.models import PortalCapture, PortalQuery

# queued -> running -> done | error
JobStatus = str


@dataclass
class CaptureJob:
    id: str
    payer_key: str
    npi: str
    status: JobStatus = "queued"
    capture: PortalCapture | None = None
    error: str | None = None
    #: The verdict as it stood when the walk was submitted, so the caller can reconcile the portal
    #: answer against it on completion. Opaque here — this layer tracks jobs, it does not judge.
    prior: dict | None = None
    _future: Future | None = field(default=None, repr=False, compare=False)

    def to_dict(self) -> dict:
        """JSON-safe view for the poll endpoint. `verdict` is flattened out of the capture so the
        UI does not have to know the PortalCapture shape."""
        cap = self.capture
        return {
            "job_id": self.id,
            "status": self.status,
            "payer_key": self.payer_key,
            "npi": self.npi,
            "error": self.error,
            "verdict": cap.status.value if cap else None,
            "portal_name": cap.portal_name if cap else None,
            "portal_url": cap.portal_url if cap else None,
            "driver": cap.driver if cap else None,
            "note": cap.note if cap else None,
            "screenshot": cap.screenshot if cap else None,
            "result_count": getattr(cap, "result_count", None) if cap else None,
            "matched_name": getattr(cap, "matched_name", None) if cap else None,
            "plan_pinned": getattr(cap, "plan_pinned", None) if cap else None,
            "duration_ms": getattr(cap, "duration_ms", None) if cap else None,
        }


class CaptureJobStore:
    def __init__(self, runner=None, max_workers: int = 1):
        self._runner = runner
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="portal-capture")
        self._jobs: dict[str, CaptureJob] = {}
        self._lock = threading.Lock()

    def _run(self) -> callable:
        if self._runner is not None:
            return self._runner
        from network_probe.portal.capture import run_capture

        return run_capture

    def submit(self, q: PortalQuery, prior: dict | None = None) -> str:
        job = CaptureJob(id=uuid.uuid4().hex[:12], payer_key=q.payer_key, npi=q.npi, prior=prior)
        with self._lock:
            self._jobs[job.id] = job

        def work() -> None:
            with self._lock:
                job.status = "running"
            try:
                cap = self._run()(q)
            except Exception as exc:  # noqa: BLE001 — a dead browser must not kill the request path
                with self._lock:
                    job.status, job.error = "error", f"{type(exc).__name__}: {exc}"
                return
            with self._lock:
                job.capture, job.status = cap, "done"

        job._future = self._pool.submit(work)
        return job.id

    def get(self, job_id: str) -> CaptureJob | None:
        with self._lock:
            return self._jobs.get(job_id)

    def wait(self, job_id: str, timeout: float | None = None) -> CaptureJob | None:
        """Block until the job settles. For tests and CLI use — the HTTP path polls `get`."""
        job = self.get(job_id)
        if job is None:
            return None
        if job._future is not None:
            job._future.result(timeout=timeout)
        return self.get(job_id)


_DEFAULT: CaptureJobStore | None = None


def default_capture_jobs() -> CaptureJobStore:
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = CaptureJobStore()
    return _DEFAULT
