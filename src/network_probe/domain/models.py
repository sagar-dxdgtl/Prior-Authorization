"""Payer-agnostic data models shared by every adapter."""

from dataclasses import dataclass
from enum import Enum


class NetworkStatus(str, Enum):
    IN_NETWORK = "IN_NETWORK"
    OUT_OF_NETWORK = "OUT_OF_NETWORK"
    UNKNOWN = "UNKNOWN"  # could not determine — DO NOT default to OON
    REVIEW = "REVIEW"  # sources conflict — needs human verification, don't assert


@dataclass
class ProviderQuery:
    payer: str
    plan_hint: str  # e.g. "BASE SILVER CSR 150" / "SILVERSIMPLEPCPSAVER" / network name
    npi: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    state: str | None = None
    zip_code: str | None = None
    tin: str | None = None  # provider's billing TIN for the encounter — a known pre-auth input
    #                            (W-9 / PM config), not a lookup; contracts are at the TIN/group level
    member_id: str | None = None  # subscriber/member ID (for an eligibility 270/271 cross-check)
    dob: str | None = None  # subscriber DOB (MM/DD/YYYY as it appears on the 271)


@dataclass
class NetworkVerdict:
    status: NetworkStatus
    matched_provider: dict | None  # raw provider record that matched, for audit
    plan_or_network_checked: str
    source_url: str  # exact endpoint(s) queried — for the audit trail
    confidence: str  # "high" | "medium" | "low" | "conflict"
    notes: str  # human-readable explanation of how the verdict was reached
    corroboration: list | None = None  # cross-source signals [{source, result, detail}]
    evidence: dict | None = None  # additive: {payer_directory: {...}, signals: [...]}

    def to_dict(self) -> dict:
        return {
            "status": self.status.value,
            "matched_provider": self.matched_provider,
            "plan_or_network_checked": self.plan_or_network_checked,
            "source_url": self.source_url,
            "confidence": self.confidence,
            "notes": self.notes,
            "corroboration": self.corroboration,
            "evidence": self.evidence,
        }
