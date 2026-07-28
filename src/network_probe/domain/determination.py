"""Final INN/OON determination — the client-facing label.

Combines the two independent halves of "is this claim in-network" into one verdict:
  1. provider network status  (NetworkStatus, from credentialing → TiC → directory, via reconcile)
  2. plan out-of-network coverage  (out_of_network_benefits, from the 271's benefit tiers)

  provider IN                                    → IN_NETWORK
  provider OON + group TIN contracted            → PHYSICIAN_OUT_OF_NETWORK  ("Physician OON")
  provider OON + no group contract + plan pays   → OUT_OF_NETWORK_WITH_BENEFITS   ("OON w/ Benefits")
  provider OON + no group contract + plan doesn't→ OUT_OF_NETWORK   (payer-level OON)
  provider REVIEW                                → REVIEW
  provider UNKNOWN                               → UNKNOWN   (reason still notes if the plan has OON benefits)

The client's four buckets map exactly: INN, Physician OON (the clinic's TIN is contracted with the
payer but this physician isn't in-network), OON w/ Benefits, and plain OON (payer-level — the clinic
has no contract with the payer). `group_contracted` is the clinic-TIN-vs-payer contract signal
(from persisted TiC facts and/or credentialing under any NPI at that TIN); when None (unknown), the
physician/payer split can't be made and it falls back to the benefits-based OON/OON-w-benefits split.
"""

from __future__ import annotations

from dataclasses import dataclass

from network_probe.domain.models import NetworkStatus


@dataclass
class Determination:
    # IN_NETWORK | PHYSICIAN_OUT_OF_NETWORK | OUT_OF_NETWORK_WITH_BENEFITS | OUT_OF_NETWORK | REVIEW | UNKNOWN
    code: str
    label: str  # human display
    reason: str
    #: Best-available reading when `code` is UNKNOWN — DISPLAY ONLY. A row must never render as a
    #: blank "UNKNOWN" when we do know the provider is contracted with the payer and merely cannot
    #: pin the network. But the moment `provisional` is allowed to drive billing or a client-facing
    #: determination it becomes the false IN this codebase exists to remove: read `code` for that.
    provisional: str | None = None
    basis: str | None = None  # the strongest evidence found, in one line
    next_step: str | None = None  # the single thing that would settle it

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "label": self.label,
            "reason": self.reason,
            "provisional": self.provisional,
            "basis": self.basis,
            "next_step": self.next_step,
        }


def _best_available(evidence: dict) -> tuple[str | None, str | None, str | None]:
    """(provisional, basis, next_step) for an UNKNOWN verdict, from whatever evidence exists.

    Only ever leans toward IN. Absence — from a directory, from an MRF — is never evidence of
    out-of-network, so it produces no provisional reading at all; that asymmetry is the rule the
    whole system turns on and a demo-facing label must not quietly break it.
    """
    ev = evidence or {}
    nets = ev.get("directory_networks") or 0
    payer = ev.get("payer_label") or "this payer"

    if ev.get("group_contracted") and (ev.get("roster_other_npis") or 0) > 0:
        n = ev["roster_other_npis"]
        return (
            "LIKELY_PHYSICIAN_OUT_OF_NETWORK",
            f"The clinic's billing TIN is in {payer}'s in-network MRF under {n} other NPI(s), but "
            f"not this physician's — the group is contracted and this physician may not be.",
            "Confirm with the clinic's credentialing record, or pin the member's network and "
            "re-run the portal check.",
        )

    if nets:
        return (
            "LIKELY_IN_NETWORK",
            f"The provider is listed in {payer}'s own directory under {nets} network(s), so they "
            f"are contracted with this payer — but a directory spans lines of business and states, "
            f"so which of those the member is on is not yet established.",
            "Supply the member's plan from the 271 to pin the network, then confirm on the payer's "
            "find-a-doctor portal.",
        )

    if ev.get("in_directory") is False:
        return (
            None,  # absence is NOT evidence of out-of-network — no lean in either direction
            f"The provider was not found in {payer}'s public directory. Directories are incomplete "
            f"and lag contracts, so this is not evidence of out-of-network.",
            "A clinic credentialing record for this (payer, NPI, billing TIN) would settle it; "
            "no plan string can, because the record is simply absent.",
        )

    if ev.get("medicare_enrolled"):
        return (
            None,  # necessary, never sufficient — clears a gate, does not pin a network
            "The provider is Medicare-enrolled (PECOS), which is required to be in any Medicare "
            "network but does not by itself place them in the member's network.",
            "Supply the member's plan, or the clinic's credentialing record for this payer.",
        )

    return (
        None,
        "No contract, MRF, directory or enrollment evidence was found for this provider and payer.",
        "Load the clinic's credentialing export, or supply the member's plan so the payer's own "
        "directory and portal can be searched.",
    )


def _oon_tail(out_of_network_benefits: bool | None) -> str:
    if out_of_network_benefits is True:
        return "the plan pays out-of-network benefits"
    if out_of_network_benefits is False:
        return "the plan has no out-of-network benefits"
    return "the plan's out-of-network coverage is undetermined"


def final_determination(
    network_status: NetworkStatus,
    out_of_network_benefits: bool | None,
    group_contracted: bool | None = None,
    plan_oon_capability: bool | None = None,
    evidence: dict | None = None,
) -> Determination:
    """`plan_oon_capability` is the structural OON-benefit signal derived from the plan TYPE
    (PPO/PFFS → True, pure HMO → False, HMO-POS/POS/D-SNP/unknown → None). It only ever *fills a
    silent 271* (out_of_network_benefits is None); a definite live 271 always wins, so we never
    contradict what the payer actually returned."""
    if network_status == NetworkStatus.IN_NETWORK:
        return Determination("IN_NETWORK", "In-Network", "Provider is in-network for the member's plan.")

    # The 271 wins when it spoke; the plan-type capability only fills a silent (None) 271.
    filled = out_of_network_benefits is None and plan_oon_capability is not None
    effective = out_of_network_benefits if out_of_network_benefits is not None else plan_oon_capability
    infer = " (inferred from plan type)" if filled else ""

    if network_status == NetworkStatus.OUT_OF_NETWORK:
        # Group TIN contracted with the payer, but this physician isn't in-network → Physician OON.
        if group_contracted is True:
            return Determination(
                "PHYSICIAN_OUT_OF_NETWORK",
                "Physician Out-of-Network",
                f"The clinic's billing TIN is contracted with this payer, but this physician is not "
                f"in-network; {_oon_tail(effective)}{infer}.",
            )
        if effective is True:
            return Determination(
                "OUT_OF_NETWORK_WITH_BENEFITS",
                "Out-of-Network (with benefits)",
                f"Provider is out-of-network, but the plan pays out-of-network benefits{infer}.",
            )
        payer_note = (
            " The clinic has no contract with this payer (payer-level out-of-network)."
            if group_contracted is False
            else ""
        )
        return Determination(
            "OUT_OF_NETWORK",
            "Out-of-Network",
            f"Provider is out-of-network and {_oon_tail(effective)}{infer}.{payer_note}",
        )

    if network_status == NetworkStatus.REVIEW:
        return Determination(
            "REVIEW",
            "Needs Review",
            f"Provider network status conflicts across sources; {_oon_tail(effective)}{infer}.",
        )

    # UNKNOWN, but never blank: surface the best available reading and what would settle it.
    provisional, basis, next_step = _best_available(evidence or {})
    label = {
        "LIKELY_IN_NETWORK": "Likely In-Network — not confirmed",
        "LIKELY_PHYSICIAN_OUT_OF_NETWORK": "Likely Physician Out-of-Network — not confirmed",
    }.get(provisional, "Not yet established")
    return Determination(
        "UNKNOWN",
        label,
        f"Provider network status could not be confirmed; {_oon_tail(effective)}{infer}.",
        provisional=provisional,
        basis=basis,
        next_step=next_step,
    )
