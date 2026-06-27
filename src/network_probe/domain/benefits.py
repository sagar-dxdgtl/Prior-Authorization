from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum

from network_probe.domain.models import NetworkStatus, NetworkVerdict


class Network(str, Enum):
    IN = "IN"; OON = "OON"; UNKNOWN = "UNKNOWN"

class BenefitCategory(str, Enum):
    COPAY = "copay"; COINSURANCE = "coinsurance"; DEDUCTIBLE = "deductible"
    OOP_MAX = "oop_max"; LIMITATION = "limitation"

class CoverageLevel(str, Enum):
    INDIVIDUAL = "individual"; FAMILY = "family"; UNKNOWN = "unknown"

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
            "service_type": self.service_type, "service_type_label": self.service_type_label,
            "network": self.network.value, "category": self.category.value, "level": self.level.value,
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

    def to_dict(self) -> dict:
        return {
            "coverage_active": self.coverage_active, "plan_name": self.plan_name, "group": self.group,
            "coverage_dates": self.coverage_dates, "network_status": self.network_status.value,
            "benefits": [b.to_dict() for b in self.benefits],
            "pcp_required": self.pcp_required, "prior_auth_required": self.prior_auth_required,
            "referral_required": self.referral_required, "cob": self.cob,
            "network_verdict": self.network_verdict.to_dict() if self.network_verdict else None,
            "corroboration": self.corroboration, "source_audit": self.source_audit,
        }
