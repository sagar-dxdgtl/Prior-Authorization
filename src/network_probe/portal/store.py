"""Append-only writer for `portal_captures` — the capture evidence trail.

Every capture is recorded, including the ones that produced no answer: a BLOCKED capture carrying a
screenshot of the block is evidence about the payer's directory, and silently dropping it would make
the log a record of successes rather than a record of what happened.

Read semantics on this branch are deliberately narrow. `latest()` exists for the audit view and the
UI, and is NOT consulted by the network resolver: a stale capture must never be substituted for a
fresh one as an answer. (The read-through fallback that serves the most recent successful capture when
a live run is blocked belongs on the demo branch, where the override semantics live.)

Writes never raise into the caller. A capture that reached the payer and produced a screenshot is
still a valid result if the database happens to be down — losing the audit row is bad, losing the
answer as well would be worse.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import select

from network_probe.db.base import SessionLocal, app_engine
from network_probe.db.models import PortalCapture as PortalCaptureRow
from network_probe.portal.models import PortalCapture, PortalStatus
from network_probe.portal.plan_match import PlanMatch

log = logging.getLogger(__name__)


class PortalCaptureStore:
    def __init__(self, engine=None):
        self.engine = engine or app_engine()

    def record(
        self,
        cap: PortalCapture,
        tenant_id: uuid.UUID | None = None,
        plan_match: PlanMatch | None = None,
        walk_trail: str | None = None,
    ) -> uuid.UUID | None:
        """Append one capture. Returns the new row id, or None if the write failed (never raises).

        `in_network` is a mirror of `status` and stays NULL for UNKNOWN and BLOCKED — neither is an
        answer, and giving them a boolean would invite a caller to read one.
        """
        in_network: bool | None = None
        if cap.status is PortalStatus.IN_NETWORK:
            in_network = True
        elif cap.status is PortalStatus.OUT_OF_NETWORK:
            in_network = False

        row = PortalCaptureRow(
            tenant_id=tenant_id,
            payer_key=cap.payer_key,
            npi=cap.npi,
            tin=cap.tin,
            status=cap.status.value,
            in_network=in_network,
            plan=cap.plan,
            plan_pinned=plan_match.label if plan_match else None,
            plan_match_basis=plan_match.basis if plan_match else None,
            portal_name=cap.portal_name,
            portal_url=cap.portal_url,
            driver=cap.driver,
            screenshot=cap.screenshot,
            result_count=cap.result_count,
            matched_name=cap.matched_name,
            reachability=cap.reachability.value if cap.reachability else None,
            duration_ms=cap.duration_ms,
            walk_trail=walk_trail,
            note=cap.note,
            # NULL, not [], when the portal was not asked or does not name networks. An empty list
            # would assert the provider is in NO networks — a different and false claim, and the one
            # every capture written before 2026-07-31 would have made.
            networks_accepted=list(cap.networks_accepted) or None,
            captured_at=cap.captured_at,
        )
        try:
            with SessionLocal(bind=self.engine) as s:
                s.add(row)
                s.commit()
                return row.id
        except Exception as e:  # noqa: BLE001 — an audit-write failure must not void a live answer
            log.warning("portal_captures write failed for %s/%s: %s", cap.payer_key, cap.npi, e)
            return None

    def latest(self, payer_key: str, npi: str, decisive_only: bool = True) -> PortalCaptureRow | None:
        """Most recent capture for this provider at this payer — for the audit view and the UI.

        NOT for the resolver: on this branch a live capture is the only thing that answers. Callers
        that display this must label it with `captured_at` so a stale row can never read as fresh.
        """
        n = str(npi or "").strip()
        if not (payer_key and n):
            return None
        q = select(PortalCaptureRow).where(
            PortalCaptureRow.payer_key == payer_key, PortalCaptureRow.npi == n
        )
        if decisive_only:
            q = q.where(PortalCaptureRow.status.in_(
                (PortalStatus.IN_NETWORK.value, PortalStatus.OUT_OF_NETWORK.value)
            ))
        q = q.order_by(PortalCaptureRow.captured_at.desc()).limit(1)
        try:
            with SessionLocal(bind=self.engine) as s:
                return s.execute(q).scalars().first()
        except Exception as e:  # noqa: BLE001
            log.warning("portal_captures read failed for %s/%s: %s", payer_key, n, e)
            return None

    def history(self, npi: str, limit: int = 25) -> list[PortalCaptureRow]:
        """Everything ever captured for this NPI, newest first — the directory-accuracy paper trail."""
        n = str(npi or "").strip()
        if not n:
            return []
        try:
            with SessionLocal(bind=self.engine) as s:
                return list(
                    s.execute(
                        select(PortalCaptureRow)
                        .where(PortalCaptureRow.npi == n)
                        .order_by(PortalCaptureRow.captured_at.desc())
                        .limit(limit)
                    ).scalars().all()
                )
        except Exception as e:  # noqa: BLE001
            log.warning("portal_captures history failed for %s: %s", n, e)
            return []


_DEFAULT: PortalCaptureStore | None = None


def default_capture_store() -> PortalCaptureStore:
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = PortalCaptureStore()
    return _DEFAULT
