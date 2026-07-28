"""Fold a live payer-portal capture back into the provider-network verdict.

The portal is the MEMBER-FACING directory — the surface a member is told to trust — so a decisive
portal answer outranks a public FHIR/PDEX read. That ranking is not a preference: flex.optum put
Naar IN_NETWORK while UHC's own findcare portal said OUT_OF_NETWORK, and the portal was right
(see the `test2-portal-verification` notes).

It does NOT outrank contract-level evidence:

  * **credentialing** is the clinic's own signed contract, and
  * a **TiC hit on NPI+TIN** is the payer's own filed in-network roster.

When either disagrees with the portal, that is a real conflict between a contract and a published
directory — the case a human has to see. It becomes REVIEW, never a silent flip in either
direction. This asymmetry is the same one used everywhere else here: we act on positive evidence
and refuse to let a weaker source erase a stronger one.

A portal that could not answer (UNKNOWN) or was refused (BLOCKED) changes nothing at all. A blocked
portal is emphatically not evidence of out-of-network.
"""

from __future__ import annotations

from network_probe.domain.models import NetworkStatus
from network_probe.portal.models import PortalStatus

#: Verdict sources that are contract-level rather than directory-level. `resolve_provider_network`
#: sets these on `NetworkVerdict.source_url` ("credentialing-matrix", "tic-mrf", or both joined).
_CONTRACT_SOURCES = ("credentialing", "tic-mrf")

_DECISIVE = (PortalStatus.IN_NETWORK, PortalStatus.OUT_OF_NETWORK)

_PORTAL_TO_NETWORK = {
    PortalStatus.IN_NETWORK: NetworkStatus.IN_NETWORK,
    PortalStatus.OUT_OF_NETWORK: NetworkStatus.OUT_OF_NETWORK,
}


def is_contract_backed(source_url: str | None) -> bool:
    """Did the prior verdict come from a contract, rather than from a directory read?"""
    s = (source_url or "").lower()
    return any(k in s for k in _CONTRACT_SOURCES)


def _sig(result: str, detail: str) -> dict:
    return {"source": "Payer portal", "result": result, "detail": detail}


def reconcile_portal(
    prior: NetworkStatus,
    portal: PortalStatus,
    *,
    source_url: str | None = None,
    portal_name: str | None = None,
    portal_note: str | None = None,
) -> tuple[NetworkStatus, dict]:
    """Merge a portal capture into the provider-network status.

    Returns (status, signal). The signal always names the portal so a reader can see which source
    moved the verdict — or that none did.
    """
    who = portal_name or "the payer's portal"

    if portal not in _DECISIVE:
        why = "was refused automated access" if portal == PortalStatus.BLOCKED else "could not answer"
        return prior, _sig(
            "inconclusive",
            f"{who} {why}, so the provider-network verdict is unchanged. "
            f"A portal that cannot answer is not evidence of out-of-network."
            + (f" {portal_note}" if portal_note else ""),
        )

    portal_status = _PORTAL_TO_NETWORK[portal]
    said = portal_status.value.replace("_", " ").lower()

    # An existing cross-source conflict is not resolved by adding another opinion.
    if prior == NetworkStatus.REVIEW:
        return prior, _sig(
            "inconclusive",
            f"{who} answered {said}, but the other sources already conflict — this stays for review.",
        )

    if prior == portal_status:
        return prior, _sig(
            "corroborates",
            f"{who} independently answered {said}, agreeing with the provider-network verdict.",
        )

    if prior == NetworkStatus.UNKNOWN:
        return portal_status, _sig(
            "settles",
            f"{who} answered {said} for the pinned plan, settling a verdict no other source could.",
        )

    # Genuine disagreement.
    if is_contract_backed(source_url):
        return NetworkStatus.REVIEW, _sig(
            "contradicts",
            f"{who} answered {said}, but the contract evidence "
            f"({source_url}) says {prior.value.replace('_', ' ').lower()}. A contract disagreeing "
            f"with a published directory is flagged for human verification, not silently flipped.",
        )

    return portal_status, _sig(
        "overrides",
        f"{who} answered {said}, overriding the public directory read of "
        f"{prior.value.replace('_', ' ').lower()}. The member-facing portal is the accuracy check on "
        f"a directory, which is known to over-include.",
    )
