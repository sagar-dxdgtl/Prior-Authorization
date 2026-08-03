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
    #: The COMMITTED reading, always one of the four actionable codes (or REVIEW) — never UNKNOWN.
    #: A biller has to bill something either way, so the display commits to a direction and states
    #: how strongly it is held in `confidence`. Like `provisional`, this is DISPLAY ONLY: `code` is
    #: what billing, audit and the override store read, and it still says UNKNOWN when we do not know.
    display_code: str = ""
    display_label: str = ""
    confidence: str = "high"  # high (confirmed) | medium (conflict) | low (a lean, not a finding)
    #: The evidence this reading was computed from, echoed back so a later recomputation (the async
    #: portal capture reconciles in its own request) can use the SAME inputs. Without it an
    #: inconclusive walk erased a directory finding it never contradicted.
    evidence: dict | None = None

    def __post_init__(self) -> None:
        # A confirmed verdict displays as itself. Only the UNKNOWN branch passes these explicitly.
        self.display_code = self.display_code or self.code
        self.display_label = self.display_label or self.label

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "label": self.label,
            "reason": self.reason,
            "provisional": self.provisional,
            "basis": self.basis,
            "next_step": self.next_step,
            "display_code": self.display_code or self.code,
            "display_label": self.display_label or self.label,
            "confidence": self.confidence,
            "evidence": self.evidence,
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
        # Presence only leans IN when it is presence in the MEMBER'S network. Two things break that,
        # and either alone is enough — see test_lean_requires_the_members_network.py.
        #
        #   * a plan WAS given and none of the networks matched it. The same fact then points the
        #     other way: contracted with this payer for OTHER products. Leaning IN here is the false
        #     IN that resolved Georgia Medicare members onto New Mexico and Medicaid networks.
        #   * the payer's own member-facing portal searched and did not list them. It cannot prove
        #     out-of-network, but a public directory read must not out-vote it into a confident IN.
        plan_mismatch = ev.get("plan_given") and not ev.get("matched_network")
        portal_absent = bool(ev.get("portal_absent"))
        if plan_mismatch or portal_absent:
            # Sentences, not one clause joined by "and" — and never str.capitalize(), which
            # lowercases everything after the first character and turned "UnitedHealthcare" into
            # "unitedhealthcare" on screen.
            why = []
            if plan_mismatch:
                why.append(
                    f"The provider is in {payer}'s directory under {nets} network(s), but none of "
                    f"them matched the member's plan — so they are contracted with this payer for "
                    f"other products, not for the member's network."
                )
            if portal_absent:
                why.append(
                    f"{payer}'s own member-facing portal was then searched for the member's plan "
                    f"and did not list them."
                )
            return (
                None,  # no IN lean — but still not doctrinal evidence of OON; `code` stays UNKNOWN
                " ".join(why),
                "Map the member's plan to one of the payer's named networks, or load the clinic's "
                "credentialing record for this payer — either settles it.",
            )
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


def _committed_display(provisional: str | None, effective: bool | None) -> tuple[str, str]:
    """(display_code, display_label) for an UNKNOWN verdict — the direction we commit to on screen.

    `code` stays UNKNOWN; this is only what the tile reads, because a blank "Not yet established"
    gives the person working the account nothing to bill. The lean follows the evidence when there
    is any, and defaults to out-of-network when there is none: an unconfirmed IN bills as in-network
    and comes back denied, while an unconfirmed OON is the recoverable error. `confidence: low` and
    the determination's own `next_step` carry the caveat.

    Directory ABSENCE is still not doctrinal evidence of out-of-network — `_best_available` refuses
    to lean on it and `code` remains UNKNOWN, so no other layer may read this as a finding.

    The label is the bare reading. How strongly it is held travels in `confidence`, which the UI
    renders as a meter beside the label rather than as words inside it — a strength is a quantity,
    and reading it off a bar is faster than parsing a parenthetical.
    """
    if provisional == "LIKELY_IN_NETWORK":
        return "IN_NETWORK", "In-Network"
    if provisional == "LIKELY_PHYSICIAN_OUT_OF_NETWORK":
        return "PHYSICIAN_OUT_OF_NETWORK", "Physician Out-of-Network"
    if effective is True:
        return "OUT_OF_NETWORK_WITH_BENEFITS", "Out-of-Network with benefits"
    return "OUT_OF_NETWORK", "Out-of-Network"


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
    """The determination, with the evidence it was computed from echoed back on it.

    The echo is what lets the async portal capture recompute this in a separate request from the
    same inputs. See `Determination.evidence`.
    """
    d = _determine(network_status, out_of_network_benefits, group_contracted, plan_oon_capability, evidence)
    d.evidence = evidence or None
    return d


def _determine(
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
        # Not forced into a direction: a genuine cross-source conflict (a contract disagreeing with a
        # published directory) is a real state a human has to see, not a low-confidence lean.
        return Determination(
            "REVIEW",
            "Needs Review",
            f"Provider network status conflicts across sources; {_oon_tail(effective)}{infer}.",
            confidence="medium",
        )

    # UNKNOWN, but never blank: surface the best available reading and what would settle it.
    provisional, basis, next_step = _best_available(evidence or {})
    label = {
        "LIKELY_IN_NETWORK": "Likely In-Network — not confirmed",
        "LIKELY_PHYSICIAN_OUT_OF_NETWORK": "Likely Physician Out-of-Network — not confirmed",
    }.get(provisional, "Not yet established")
    d_code, d_label = _committed_display(provisional, effective)
    return Determination(
        "UNKNOWN",
        label,
        f"Provider network status could not be confirmed; {_oon_tail(effective)}{infer}.",
        provisional=provisional,
        basis=basis,
        next_step=next_step,
        display_code=d_code,
        display_label=d_label,
        confidence="low",
    )
