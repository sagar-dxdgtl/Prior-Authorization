"""Capture orchestrator — resolve a driver, drive the portal once, screenshot, return the verdict.

Captures are serialised behind a process lock: one provider at a time, never a fan-out at a payer.
This is a human-scale lookup, the same one a clinic staffer performs by hand, and it stays that way.

Screenshots land in api/static/portal/live/ so the existing /api/portal-screenshot/{key} route serves
them as the evidence attachment — a timestamped image of the payer's own answer, which is what a No
Surprises Act directory-accuracy dispute turns on.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

from network_probe.portal import browser as pb
from network_probe.portal.drivers.aetna_ahpublic import AetnaFindCareDriver
from network_probe.portal.drivers.base import PortalDriver
from network_probe.portal.drivers.bcbsil_provider_finder import BcbsilProviderFinderDriver
from network_probe.portal.drivers.cigna_hcp import CignaHcpDriver
from network_probe.portal.drivers.healthsparq import HealthSparqDriver
from network_probe.portal.drivers.humana_finder import HumanaFinderDriver
from network_probe.portal.drivers.molina_provider_search import MolinaProviderSearchDriver
from network_probe.portal.drivers.oscar_care_options import OscarCareOptionsDriver
from network_probe.portal.drivers.uhc_findcare import UhcFindCareDriver
from network_probe.portal.drivers.wellcare_hub import WellcareHubDriver
from network_probe.portal.models import PortalCapture, PortalQuery, PortalStatus
from network_probe.portal.targets import target_for_payer

# One driver per portal UI, each adversarially reviewed before landing here (2026-07-28). Registration
# is NOT an assertion that a driver produces decisive answers — Oscar and Humana structurally cannot,
# because no label on either portal carries an identifier `plan_match` can match, so they return
# UNKNOWN plus a screenshot. They are registered as evidence sources; the decisive answer for those
# rows comes from the payer's FHIR directory or a human.
DRIVERS: tuple[PortalDriver, ...] = (
    UhcFindCareDriver(),
    HealthSparqDriver(),
    AetnaFindCareDriver(),
    CignaHcpDriver(),
    BcbsilProviderFinderDriver(),
    MolinaProviderSearchDriver(),
    WellcareHubDriver(),
    OscarCareOptionsDriver(),
    HumanaFinderDriver(),
)

log = logging.getLogger(__name__)

_LOCK = threading.Lock()  # serialise captures: never two concurrent hits at one payer

LIVE_SHOT_DIR = Path(__file__).resolve().parents[1] / "api" / "static" / "portal" / "live"


def driver_for(payer_key: str) -> PortalDriver | None:
    """The driver that can answer for this roster payer key, or None if we have no portal path yet."""
    target = target_for_payer(payer_key)
    if target is None:
        return None
    return next((d for d in DRIVERS if d.key == target.key), None)


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")[:60]


def run_capture(q: PortalQuery, headed: bool | None = None, shot_dir: Path | None = None) -> PortalCapture:
    """Drive the payer's portal for one provider. Never raises — a failure is a BLOCKED capture."""
    driver = driver_for(q.payer_key)
    if driver is None:
        target = target_for_payer(q.payer_key)
        return PortalCapture(
            payer_key=q.payer_key, npi=q.npi, plan=q.plan, tin=q.tin, status=PortalStatus.UNKNOWN,
            portal_name=target.portal_name if target else "—",
            portal_url=target.entry_url if target else "—", driver="none",
            note=(
                f"No portal driver for payer {q.payer_key!r} yet"
                + (f" — its portal is {target.portal_name}" if target else "")
                + (f"; use the sanctioned FHIR path instead: {target.fhir_fallback}"
                   if target and target.fhir_fallback else "")
                + "."
            ),
        )

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out = shot_dir or LIVE_SHOT_DIR
    started = time.monotonic()

    # The driver's own launch requirements win over the ambient default. An explicit `headed=` from the
    # caller still wins over both — but a driver that declares requires_headed can never be forced
    # headless by omission, which is what would otherwise make it return BLOCKED on every run.
    if headed is None and driver.requires_headed:
        headed = True

    with _LOCK:
        with pb.browser_session(headed=headed) as browser:
            with pb.portal_page(
                browser, portal_key=driver.key,
                reuse_session=not driver.requires_fresh_context,
            ) as page:
                def shot(label: str) -> str | None:
                    return pb.screenshot(page, out, f"{driver.key}-{q.npi}-{_slug(label)}-{stamp}")

                cap = driver.capture(page, q, shot)

    cap.duration_ms = int((time.monotonic() - started) * 1000)
    _record(cap)
    return cap


def _walk_trail(note: str | None) -> str | None:
    """Pull the "[portal walk: ...]" the drivers append to their note.

    Stored as its own column because a verdict is only as trustworthy as the path that produced it,
    and a reviewer should be able to read the path without parsing prose.
    """
    m = re.search(r"\[portal walk:\s*(.+?)\]\s*$", note or "", re.S)
    return m.group(1).strip()[:700] if m else None


def _record(cap: PortalCapture) -> None:
    """Append the capture to the audit log. Best-effort by design: a capture that reached the payer and
    produced a screenshot is still a valid answer if the database is down. Losing the audit row is bad;
    losing the answer as well would be worse."""
    try:
        from network_probe.portal.store import default_capture_store

        default_capture_store().record(cap, walk_trail=_walk_trail(cap.note))
    except Exception:  # noqa: BLE001
        log.warning("portal capture not persisted for %s/%s", cap.payer_key, cap.npi)


def main() -> None:
    ap = argparse.ArgumentParser(description="Capture one provider's network status from a payer portal.")
    ap.add_argument("--payer-key", required=True, help="roster payer key, e.g. unitedhealthcare-fl")
    ap.add_argument("--npi", required=True)
    ap.add_argument("--last-name", help="provider last name (the fallback search term)")
    ap.add_argument("--first-name", help="provider first name")
    ap.add_argument("--plan", help="plan/product from the member's 271 — what pins the network")
    ap.add_argument("--zip", dest="zip_code", help="clinic ZIP, to scope the search")
    ap.add_argument("--state")
    ap.add_argument("--tin", help="billing TIN — recorded in the audit row, never sent to the portal")
    ap.add_argument("--headed", action="store_true")
    args = ap.parse_args()

    q = PortalQuery(
        payer_key=args.payer_key, npi=args.npi, provider_first_name=args.first_name,
        provider_last_name=args.last_name, plan=args.plan, state=args.state,
        zip_code=args.zip_code, tin=args.tin,
    )
    cap = run_capture(q, headed=args.headed or None)
    print(json.dumps(cap.to_dict(), indent=2))


if __name__ == "__main__":
    main()
