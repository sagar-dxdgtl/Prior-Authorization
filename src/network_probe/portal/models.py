"""Data models for the live payer-portal capture layer.

The member-facing find-a-doctor portal is the accurate network source — the CMS-mandated FHIR
directory over-includes (false INs) and the TiC MRFs have gaps. This package drives those portals
in a real browser, one provider at a time, and records what the portal actually said plus a
screenshot as evidence.

`PortalStatus` deliberately adds BLOCKED to the domain's NetworkStatus vocabulary: a portal that
refuses automated access is a *transport* outcome, not a network verdict, and must never collapse
into OUT_OF_NETWORK. Absence-is-not-OON is the same rule the directory adapters follow.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum

from network_probe.domain.models import NetworkStatus


class PortalStatus(str, Enum):
    IN_NETWORK = "IN_NETWORK"
    OUT_OF_NETWORK = "OUT_OF_NETWORK"
    UNKNOWN = "UNKNOWN"  # portal reached, answer not determinable — DO NOT default to OON
    BLOCKED = "BLOCKED"  # portal refused automated access (WAF / challenge / timeout) — not a verdict

    def to_network_status(self) -> NetworkStatus:
        """Map to the shared domain vocabulary. BLOCKED and UNKNOWN both mean 'no answer'."""
        if self is PortalStatus.IN_NETWORK:
            return NetworkStatus.IN_NETWORK
        if self is PortalStatus.OUT_OF_NETWORK:
            return NetworkStatus.OUT_OF_NETWORK
        return NetworkStatus.UNKNOWN


class Reachability(str, Enum):
    """What a real browser met at the portal's entry URL. Drives which drivers are worth writing."""

    SEARCHABLE = "SEARCHABLE"  # loaded and a provider-search control is present
    LOADED = "LOADED"  # loaded, but no search control found (deeper navigation needed)
    CHALLENGE = "CHALLENGE"  # interactive bot challenge (reCAPTCHA / Turnstile / press-and-hold …)
    WAF_BLOCK = "WAF_BLOCK"  # hard refusal (403 / Access Denied / Imperva incident page)
    TIMEOUT = "TIMEOUT"  # no response in budget
    ERROR = "ERROR"  # navigation raised


@dataclass
class PortalQuery:
    """A single provider lookup. Provider + clinic data only — no member PHI ever reaches a portal."""

    payer_key: str
    npi: str
    provider_first_name: str | None = None
    provider_last_name: str | None = None
    plan: str | None = None  # the product to select in the portal, e.g. "AARP Medicare Advantage … (PPO)"
    state: str | None = None
    city: str | None = None
    zip_code: str | None = None
    tin: str | None = None  # carried through to the audit row, never sent to the portal


@dataclass
class PortalCapture:
    """What the portal said, and the proof. Persisted append-only to `portal_captures`."""

    payer_key: str
    npi: str
    status: PortalStatus
    portal_name: str
    portal_url: str  # the exact URL the answer was read from
    driver: str
    note: str
    plan: str | None = None
    tin: str | None = None
    screenshot: str | None = None  # filename under api/static/portal/live/
    result_count: int | None = None  # providers the portal returned for the search
    matched_name: str | None = None  # the name the portal showed for our NPI, when it matched
    #: EVERY network the portal says this provider participates in, when it will name them — not just
    #: the one we searched. A provider-first read: one walk answers the question for all of a payer's
    #: networks instead of one walk per network. AZ Blue names all 14 of Maydell's behind its
    #: "14 in network" card link. Empty tuple means the portal was not asked or did not say; it never
    #: means "none", so absence from this list is only evidence when the list is non-empty.
    networks_accepted: tuple[str, ...] = ()
    reachability: Reachability | None = None
    duration_ms: int | None = None
    captured_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def decisive(self) -> bool:
        """Only IN/OON are answers. UNKNOWN and BLOCKED must not override any other source."""
        return self.status in (PortalStatus.IN_NETWORK, PortalStatus.OUT_OF_NETWORK)

    def to_dict(self) -> dict:
        return {
            "payer_key": self.payer_key,
            "npi": self.npi,
            "status": self.status.value,
            "portal_name": self.portal_name,
            "portal_url": self.portal_url,
            "driver": self.driver,
            "note": self.note,
            "plan": self.plan,
            "tin": self.tin,
            "screenshot": self.screenshot,
            "result_count": self.result_count,
            "matched_name": self.matched_name,
            "networks_accepted": list(self.networks_accepted),
            "reachability": self.reachability.value if self.reachability else None,
            "duration_ms": self.duration_ms,
            "captured_at": self.captured_at.isoformat(),
            "decisive": self.decisive,
        }


@dataclass
class ProbeResult:
    """One entry-URL reachability observation, written to the probe report."""

    portal_key: str
    portal_name: str
    entry_url: str
    reachability: Reachability
    http_status: int | None = None
    final_url: str | None = None
    title: str | None = None
    challenge: str | None = None  # which vendor's challenge was detected, when CHALLENGE
    search_hint: str | None = None  # the selector that proved a search control exists
    protection: str | None = None  # bot-protection vendor in page source (informational on its own)
    content_chars: int = 0  # readable text rendered; ~0 means an interstitial or un-hydrated SPA
    screenshot: str | None = None
    duration_ms: int | None = None
    detail: str = ""

    def to_dict(self) -> dict:
        return {
            "portal_key": self.portal_key,
            "portal_name": self.portal_name,
            "entry_url": self.entry_url,
            "reachability": self.reachability.value,
            "http_status": self.http_status,
            "final_url": self.final_url,
            "title": self.title,
            "challenge": self.challenge,
            "search_hint": self.search_hint,
            "protection": self.protection,
            "content_chars": self.content_chars,
            "screenshot": self.screenshot,
            "duration_ms": self.duration_ms,
            "detail": self.detail,
        }
