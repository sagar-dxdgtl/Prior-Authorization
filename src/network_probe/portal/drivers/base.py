"""The single interface every portal driver implements.

One driver per portal UI (not per payer — HealthSparq and the Centene hub each serve several payers).
Keep this portal-agnostic; anything UHC-specific belongs in drivers/uhc_findcare.py.

Every driver follows the same plan-first shape, because that is what the portals enforce: 7 of the 9
probed on 2026-07-28 gate provider search behind plan selection. The plan comes from the member's live
271, so the chain is 271 → plan → portal plan selection → provider search → screenshot → verdict.

Verdict doctrine, shared by every driver and inherited from the directory adapters:

  * provider matched inside the selected plan's network        → IN_NETWORK
  * plan context CONFIRMED and the search returned a populated
    result set that does not contain this provider             → OUT_OF_NETWORK
  * plan context NOT confirmed, or the portal errored          → UNKNOWN
  * challenge / WAF / timeout                                  → BLOCKED

Absence is only evidence when we can prove we searched the right network. Without a confirmed plan,
absence is UNKNOWN — never OON. This is the same rule that stopped the directory leg inventing OONs.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from playwright.sync_api import Page

from network_probe.portal.models import PortalCapture, PortalQuery


class PortalDriver(ABC):
    #: portal key, matching the PortalTarget it drives (e.g. "uhc-findcare")
    key: str
    #: human-readable portal name, shown in the UI next to the screenshot
    portal_name: str

    @abstractmethod
    def capture(self, page: Page, q: PortalQuery, shot) -> PortalCapture:
        """Drive the portal for one provider and return what it said.

        `shot(name) -> str | None` takes a screenshot and returns its filename; call it at the moment
        the answer is on screen, and again on any failure path — a screenshot of a block is evidence too.
        """
        ...
