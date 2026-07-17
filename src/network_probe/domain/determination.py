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

    def to_dict(self) -> dict:
        return {"code": self.code, "label": self.label, "reason": self.reason}


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
) -> Determination:
    if network_status == NetworkStatus.IN_NETWORK:
        return Determination("IN_NETWORK", "In-Network", "Provider is in-network for the member's plan.")

    if network_status == NetworkStatus.OUT_OF_NETWORK:
        # Group TIN contracted with the payer, but this physician isn't in-network → Physician OON.
        if group_contracted is True:
            return Determination(
                "PHYSICIAN_OUT_OF_NETWORK",
                "Physician Out-of-Network",
                f"The clinic's billing TIN is contracted with this payer, but this physician is not "
                f"in-network; {_oon_tail(out_of_network_benefits)}.",
            )
        if out_of_network_benefits is True:
            return Determination(
                "OUT_OF_NETWORK_WITH_BENEFITS",
                "Out-of-Network (with benefits)",
                "Provider is out-of-network, but the plan pays out-of-network benefits.",
            )
        payer_note = (
            " The clinic has no contract with this payer (payer-level out-of-network)."
            if group_contracted is False
            else ""
        )
        return Determination(
            "OUT_OF_NETWORK",
            "Out-of-Network",
            f"Provider is out-of-network and {_oon_tail(out_of_network_benefits)}.{payer_note}",
        )

    if network_status == NetworkStatus.REVIEW:
        return Determination(
            "REVIEW",
            "Needs Review",
            f"Provider network status conflicts across sources; {_oon_tail(out_of_network_benefits)}.",
        )

    return Determination(
        "UNKNOWN",
        "Undetermined",
        f"Provider network status could not be confirmed; {_oon_tail(out_of_network_benefits)}.",
    )
