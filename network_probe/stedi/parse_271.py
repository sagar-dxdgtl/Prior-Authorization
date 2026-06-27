from __future__ import annotations
from decimal import Decimal, InvalidOperation
from typing import Optional
from ..benefits import BenefitLine, EligibilityResult, Network, BenefitCategory, CoverageLevel
from ..models import NetworkStatus

_CATEGORY = {"B": BenefitCategory.COPAY, "A": BenefitCategory.COINSURANCE,
             "C": BenefitCategory.DEDUCTIBLE, "G": BenefitCategory.OOP_MAX, "F": BenefitCategory.LIMITATION}
_LEVEL = {"IND": CoverageLevel.INDIVIDUAL, "FAM": CoverageLevel.FAMILY}
_NET = {"Y": Network.IN, "N": Network.OON}
_TIME = {"23": "calendar year", "29": "remaining", "27": "visit", "22": "service year"}
_COB_ALLOW = {"primaryPayer", "secondaryPayer", "planSponsor", "ipa", "sequence",
              "payerResponsibilitySequenceNumberCode"}

def _dec(v) -> Optional[Decimal]:
    try:
        return Decimal(str(v)) if v not in (None, "") else None
    except (InvalidOperation, ValueError):
        return None

def _redact_cob(raw):
    if isinstance(raw, dict):
        red = {k: v for k, v in raw.items() if k in _COB_ALLOW}
        return red or None
    if isinstance(raw, list):
        out = [{k: v for k, v in item.items() if k in _COB_ALLOW} for item in raw if isinstance(item, dict)]
        out = [o for o in out if o]
        return out or None
    return None

def _pair_met(lines: list[BenefitLine]) -> list[BenefitLine]:
    """For deductible/OOP: when a calendar-year total and a remaining line exist for the same
    (category, network, level), enrich the total with met = total - remaining and drop the remaining line."""
    groups: dict = {}
    for l in lines:
        if l.category in (BenefitCategory.DEDUCTIBLE, BenefitCategory.OOP_MAX):
            groups.setdefault((l.category, l.network, l.level), []).append(l)
    drop_ids = set()
    for group in groups.values():
        total = next((l for l in group if l.time_period == "calendar year"), None)
        rem = next((l for l in group if l.time_period == "remaining"), None)
        if total is not None and rem is not None and total.amount is not None and rem.amount is not None:
            total.remaining = rem.amount
            total.met = total.amount - rem.amount
            drop_ids.add(id(rem))
    return [l for l in lines if id(l) not in drop_ids]

def parse_271_benefits(data: dict) -> EligibilityResult:
    if data.get("errors"):
        return EligibilityResult(
            coverage_active=None, plan_name=None, group=None, coverage_dates={},
            network_status=NetworkStatus.UNKNOWN, benefits=[], pcp_required=None,
            prior_auth_required=None, referral_required=None, cob=None,
            network_verdict=None, corroboration=[],
            source_audit={"source": "stedi-271",
                          "error_codes": [e.get("code") for e in data["errors"]],
                          "note": "payer could not respond"})
    infos = data.get("benefitsInformation") or []
    active = any(b.get("code") == "1" for b in infos)
    inactive = any(b.get("code") == "6" for b in infos)
    coverage_active = True if active else (False if inactive else None)

    lines: list[BenefitLine] = []
    prior_auth = referral = pcp = None
    for b in infos:
        text = " ".join(ai.get("description", "") for ai in (b.get("additionalInformation") or [])).lower()
        text += " " + (b.get("name", "") or "").lower()
        if "prior auth" in text or "preauth" in text or "pre-auth" in text or "precert" in text:
            prior_auth = True
        if "referral" in text:
            referral = True
        if "primary care" in text or "pcp" in text:
            pcp = True
        cat = _CATEGORY.get(b.get("code"))
        if cat is None:
            continue
        time_period = _TIME.get(b.get("timeQualifierCode"))
        amount = _dec(b.get("benefitAmount"))
        stc = (b.get("serviceTypeCodes") or [""])[0]
        label = (b.get("serviceTypes") or [b.get("name", "")])[0] if b.get("serviceTypes") else (b.get("name", "") or "")
        lines.append(BenefitLine(
            service_type=stc, service_type_label=label,
            network=_NET.get(b.get("inPlanNetworkIndicatorCode"), Network.UNKNOWN),
            category=cat, level=_LEVEL.get(b.get("coverageLevelCode"), CoverageLevel.UNKNOWN),
            amount=amount if cat != BenefitCategory.COINSURANCE else None,
            percent=_dec(b.get("benefitPercent")),
            time_period=time_period,
            met=None,
            remaining=amount if time_period == "remaining" else None,
            raw_codes={k: b.get(k) for k in ("code", "coverageLevelCode", "inPlanNetworkIndicatorCode", "timeQualifierCode")}))

    lines = _pair_met(lines)
    nets = {l.network for l in lines}
    if Network.IN in nets and Network.OON not in nets:
        status = NetworkStatus.IN_NETWORK
    elif Network.OON in nets and Network.IN not in nets:
        status = NetworkStatus.OUT_OF_NETWORK
    else:
        status = NetworkStatus.UNKNOWN     # mixed/none → defer to the directory engine, never guess

    plan = data.get("planInformation") or {}
    return EligibilityResult(
        coverage_active=coverage_active,
        plan_name=plan.get("planName") or plan.get("groupDescription"),
        group=plan.get("groupNumber"),
        coverage_dates=data.get("planDateInformation") or {},
        network_status=status, benefits=lines,
        pcp_required=pcp, prior_auth_required=prior_auth, referral_required=referral,
        cob=_redact_cob(data.get("coordinationOfBenefits")),
        network_verdict=None, corroboration=[],
        source_audit={"source": "stedi-271"})
