from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum

from network_probe.domain.models import NetworkStatus, NetworkVerdict


class Network(str, Enum):
    IN = "IN"
    OON = "OON"
    UNKNOWN = "UNKNOWN"


class BenefitCategory(str, Enum):
    COPAY = "copay"
    COINSURANCE = "coinsurance"
    DEDUCTIBLE = "deductible"
    OOP_MAX = "oop_max"
    LIMITATION = "limitation"


class CoverageLevel(str, Enum):
    INDIVIDUAL = "individual"
    FAMILY = "family"
    UNKNOWN = "unknown"


@dataclass
class BenefitLine:
    service_type: str
    service_type_label: str
    network: Network
    category: BenefitCategory
    level: CoverageLevel
    amount: Decimal | None
    percent: Decimal | None
    time_period: str | None
    met: Decimal | None
    remaining: Decimal | None
    raw_codes: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "service_type": self.service_type,
            "service_type_label": self.service_type_label,
            "network": self.network.value,
            "category": self.category.value,
            "level": self.level.value,
            "amount": None if self.amount is None else str(self.amount),
            "percent": None if self.percent is None else str(self.percent),
            "time_period": self.time_period,
            "met": None if self.met is None else str(self.met),
            "remaining": None if self.remaining is None else str(self.remaining),
            "raw_codes": self.raw_codes,
        }


@dataclass
class EligibilityResult:
    coverage_active: bool | None
    plan_name: str | None
    group: str | None
    coverage_dates: dict
    network_status: NetworkStatus
    benefits: list[BenefitLine]
    pcp_required: bool | None
    prior_auth_required: bool | None
    referral_required: bool | None
    cob: dict | None
    network_verdict: NetworkVerdict | None
    corroboration: list
    source_audit: dict
    plan_candidates: list = field(default_factory=list)
    selected_plan: str | None = None
    #: Where `selected_plan` came from, when it did NOT come from the payer's 271. Set only for the
    #: CMS-plan-id-in-the-identifier-field path, so a reader can tell a plan the payer named from one
    #: an operator typed — the two do not deserve equal confidence, and a portal walk pinned by the
    #: second is only as right as the value on the sheet. None means the payer named it.
    plan_pin_source: str | None = None
    stedi_network_status: NetworkStatus | None = None
    # Plan-level out-of-network coverage from the 271 benefit tiers: True if the plan returns OON
    # cost-shares (PPO-style → pays OON, i.e. "OON w/ benefits"), False if in-network-only (HMO-style),
    # None if the 271 carried no cost-share tiers to tell. Distinct from network_status, which is the
    # provider-specific verdict the 271 can't give.
    out_of_network_benefits: bool | None = None
    # The STRUCTURAL out-of-network capability of the member's plan TYPE (PPO/PFFS → True, pure HMO →
    # False, HMO-POS/POS/D-SNP/unknown → None), from the CMS PBP plan or the plan string. It only ever
    # fills a SILENT 271 — `out_of_network_benefits` always wins when the payer actually answered.
    # Exposed because the async portal capture re-runs `final_determination` in a separate request and
    # must reconcile with the same inputs; without it a silent-271 member reconciles to plain
    # "Out-of-Network" where the plan type says "Out-of-Network (with benefits)".
    plan_oon_capability: bool | None = None
    #: The member's 5-digit residence ZIP, read from the 271's own subscriber address. Carried ONLY
    #: because some portals scope their plan list by where the member lives (UHC Medicare: "Select
    #: the area where you live"), so the clinic ZIP cannot pin the member's plan. It selects a plan
    #: list and is never typed into a provider search box — no other member field ever leaves here.
    member_zip: str | None = None
    # Final client-facing INN/OON determination {code,label,reason}: provider network_status combined
    # with out_of_network_benefits (IN / OON / OON-with-benefits / REVIEW / UNKNOWN). Set by check_eligibility.
    determination: dict | None = None
    # Side-by-side evidence: what EACH source independently says (Stedi 271, credentialing, TiC,
    # payer directory) as [{source, answers, status, tone, detail}]. Set by check_eligibility.
    evidence_sources: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "coverage_active": self.coverage_active,
            "plan_name": self.plan_name,
            "group": self.group,
            "coverage_dates": self.coverage_dates,
            "network_status": self.network_status.value,
            "benefits": [b.to_dict() for b in self.benefits],
            "pcp_required": self.pcp_required,
            "prior_auth_required": self.prior_auth_required,
            "referral_required": self.referral_required,
            "cob": self.cob,
            "network_verdict": self.network_verdict.to_dict() if self.network_verdict else None,
            "corroboration": self.corroboration,
            "source_audit": self.source_audit,
            "plan_candidates": self.plan_candidates,
            "selected_plan": self.selected_plan,
            "plan_pin_source": self.plan_pin_source,
            "stedi_network_status": self.stedi_network_status.value if self.stedi_network_status else None,
            "out_of_network_benefits": self.out_of_network_benefits,
            "plan_oon_capability": self.plan_oon_capability,
            "member_zip": self.member_zip,
            "determination": self.determination,
            "evidence_sources": self.evidence_sources,
        }
