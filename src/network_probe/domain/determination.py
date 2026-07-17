"""Final INN/OON determination — the client-facing label.

Combines the two independent halves of "is this claim in-network" into one verdict:
  1. provider network status  (NetworkStatus, from credentialing → TiC → directory, via reconcile)
  2. plan out-of-network coverage  (out_of_network_benefits, from the 271's benefit tiers)

  provider IN                         → IN_NETWORK
  provider OON + plan pays OON        → OUT_OF_NETWORK_WITH_BENEFITS   ("OON w/ Benefits")
  provider OON + plan doesn't / n/a   → OUT_OF_NETWORK
  provider REVIEW                     → REVIEW
  provider UNKNOWN                    → UNKNOWN   (reason still notes if the plan has OON benefits)

The client's "Physician OON" folds into OUT_OF_NETWORK (the provider is out); the with-/without-
benefits split comes from the 271, not the provider status.
"""

from __future__ import annotations

from dataclasses import dataclass

from network_probe.domain.models import NetworkStatus


@dataclass
class Determination:
    code: str  # IN_NETWORK | OUT_OF_NETWORK | OUT_OF_NETWORK_WITH_BENEFITS | REVIEW | UNKNOWN
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


def final_determination(network_status: NetworkStatus, out_of_network_benefits: bool | None) -> Determination:
    if network_status == NetworkStatus.IN_NETWORK:
        return Determination("IN_NETWORK", "In-Network", "Provider is in-network for the member's plan.")

    if network_status == NetworkStatus.OUT_OF_NETWORK:
        if out_of_network_benefits is True:
            return Determination(
                "OUT_OF_NETWORK_WITH_BENEFITS",
                "Out-of-Network (with benefits)",
                "Provider is out-of-network, but the plan pays out-of-network benefits.",
            )
        return Determination(
            "OUT_OF_NETWORK",
            "Out-of-Network",
            f"Provider is out-of-network and {_oon_tail(out_of_network_benefits)}.",
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
