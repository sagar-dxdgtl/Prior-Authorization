from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from network_probe.domain.benefits import BenefitCategory, BenefitLine, CoverageLevel, EligibilityResult, Network
from network_probe.domain.models import NetworkStatus
from network_probe.domain.plan_candidates import derive_plan_candidates

_CATEGORY = {
    "B": BenefitCategory.COPAY,
    "A": BenefitCategory.COINSURANCE,
    "C": BenefitCategory.DEDUCTIBLE,
    "G": BenefitCategory.OOP_MAX,
    "F": BenefitCategory.LIMITATION,
}
_LEVEL = {"IND": CoverageLevel.INDIVIDUAL, "FAM": CoverageLevel.FAMILY}
_NET = {"Y": Network.IN, "N": Network.OON}
# Service-type codes for physician/professional services a specialist provider renders. An OON
# copay/coinsurance on one of these = the plan genuinely pays out-of-network for the provider's care
# (the "w/ benefits" in "OON w/ benefits"); OON deductible/OOP-max on general coverage (stc "30")
# does not, and is excluded.
_PROFESSIONAL_STC = {"1", "96", "98", "BY", "BZ", "UC"}
_TIME = {"23": "calendar year", "29": "remaining", "27": "visit", "22": "service year"}
_COB_ALLOW = {
    "primaryPayer",
    "secondaryPayer",
    "planSponsor",
    "ipa",
    "sequence",
    "payerResponsibilitySequenceNumberCode",
}


def _dec(v) -> Decimal | None:
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


# AAA reject code → static, PHI-safe meaning (never surface the payer's raw description verbatim).
_AAA_MEANINGS = {
    "42": "payer unable to respond right now — retry",
    "71": "date of birth mismatch",
    "72": "this member ID is not recognised",
    "73": "subscriber name mismatch",
    "75": "subscriber not found — verify member ID",
    "79": "invalid participant identification",
}

# AN IDENTITY REJECT IS THE PAYER ANSWERING. Only 42 means "ask again later"; every other AAA code
# is the payer telling us our identity data is wrong, in seconds, having named itself in the 271.
# Those two outcomes call for OPPOSITE actions — retry an outage, correct the data on a reject — so
# collapsing both into "payer could not respond" (which is what this module used to report for every
# code) sends the user to retry a request that will never succeed. Measured live 2026-08-12: BCBS SC
# returned AAA-72 for all four identity shapes the retry ladder can build, because the value in the
# member-id field was a GROUP number; the no-member-id last resort came back `AAA*N` — this payer
# will not answer without one at all.
_AAA_NO_ANSWER = {"42"}

# What to DO about it, kept separate from the meaning so the meaning stays a plain statement of what
# the payer said. Static text only — the payer's own description echoes the member id back.
_AAA_ACTIONS = {
    "71": "Check the date of birth against the member's card.",
    "72": (
        "Enter the ID exactly as printed on the member's card — a group number is not a member ID, "
        "and the 80840 card-issuer prefix is not part of it."
    ),
    "73": "Check the spelling of the member's name as the payer holds it.",
    "75": "Check the member ID and that this is the plan that covers them.",
}


def _subscriber_zip(data: dict) -> str | None:
    """The member's 5-digit residence ZIP from the 271's own subscriber address.

    Needed because some portals scope their PLAN LIST by where the member lives rather than where the
    clinic is — UHC Medicare's plan step is literally "Select the area where you live", and its lists
    are disjoint between counties. The payer already told us this in its own response, so it costs no
    extra data entry and cannot drift from what the payer believes.

    Measured 2026-08-03 across the cached real 271s: present in 7 of 7, spanning UnitedHealthcare,
    Humana, Devoted, Oscar and Cigna. Returned as ZIP+4 by several payers ("338111635"), so it is
    truncated — the portals' location boxes take 5 digits and reject the 9-digit form.
    """
    addr = ((data.get("subscriber") or {}).get("address")) or {}
    raw = re.sub(r"\D", "", str(addr.get("postalCode") or ""))
    return raw[:5] if len(raw) >= 5 else None


def parse_271_benefits(data: dict) -> EligibilityResult:
    if data.get("errors"):
        errs = data["errors"]
        codes = [e.get("code") for e in errs if e.get("code")]
        # PHI-safe reason for the UI: map the AAA CODE to STATIC text — never echo the payer's raw
        # description, which can contain the member ID. AAA-72/73/75 mean the payer couldn't match
        # the subscriber (fix the member ID / DOB / name); 42 is a transient payer outage.
        answered = bool(codes) and any(c not in _AAA_NO_ANSWER for c in codes)
        said = "; ".join(
            f"{_AAA_MEANINGS.get(c, 'the request was rejected')} (AAA {c})" for c in codes
        ) if codes else "payer could not respond"
        do = " ".join(_AAA_ACTIONS[c] for c in dict.fromkeys(codes) if c in _AAA_ACTIONS)
        if answered:
            # Name the payer so the line cannot be read as "nobody was home". The payer's NAME is not
            # PHI — the member's identifiers, which sit next to it in the 271, are, and none of them
            # are touched here.
            who = " ".join(str((data.get("payer") or {}).get("name") or "The payer").split())[:60]
            error_note = f"{who} answered: {said}." + (f" {do}" if do else "")
        else:
            error_note = said
        return EligibilityResult(
            coverage_active=None,
            plan_name=None,
            group=None,
            coverage_dates={},
            network_status=NetworkStatus.UNKNOWN,
            benefits=[],
            pcp_required=None,
            prior_auth_required=None,
            referral_required=None,
            cob=None,
            network_verdict=None,
            corroboration=[],
            source_audit={
                "source": "stedi-271",
                "error_codes": codes,
                "error_note": error_note,
                # The UI's Network Finding tab renders `note`, and it used to read "payer could not
                # respond" for every AAA code — including the ones where the payer answered in
                # seconds. Same text in both places now, so the two tabs cannot disagree.
                "note": error_note,
                "payer_answered": answered,
            },
        )
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
        label = (
            (b.get("serviceTypes") or [b.get("name", "")])[0] if b.get("serviceTypes") else (b.get("name", "") or "")
        )
        lines.append(
            BenefitLine(
                service_type=stc,
                service_type_label=label,
                network=_NET.get(b.get("inPlanNetworkIndicatorCode"), Network.UNKNOWN),
                category=cat,
                level=_LEVEL.get(b.get("coverageLevelCode"), CoverageLevel.UNKNOWN),
                amount=amount if cat != BenefitCategory.COINSURANCE else None,
                percent=_dec(b.get("benefitPercent")),
                time_period=time_period,
                met=None,
                remaining=amount if time_period == "remaining" else None,
                raw_codes={
                    k: b.get(k)
                    for k in ("code", "coverageLevelCode", "inPlanNetworkIndicatorCode", "timeQualifierCode")
                },
            )
        )

    lines = _pair_met(lines)
    nets = {l.network for l in lines}
    if Network.IN in nets and Network.OON not in nets:
        status = NetworkStatus.IN_NETWORK
    elif Network.OON in nets and Network.IN not in nets:
        status = NetworkStatus.OUT_OF_NETWORK
    else:
        status = NetworkStatus.UNKNOWN  # mixed/none → defer to the directory engine, never guess

    # Plan-level OON coverage, labelled the way an RCM team does: "OON w/ benefits" ONLY when the
    # plan actually pays out-of-network for a PHYSICIAN/professional service (a real OON copay or
    # coinsurance on those service types). A PPO returns those (Desormeaux: OON physician copay $45);
    # an HMOPOS / D-SNP returns OON lines too, but only structural deductible/OOP-max on general
    # coverage and no OON physician cost-share (Birenbaum) → that's plain "OON", not "OON w/ benefits".
    # False = has cost-share tiers but doesn't pay OON professional; None = no tiers to tell.
    pays_oon_professional = any(
        l.network == Network.OON
        and l.category in (BenefitCategory.COPAY, BenefitCategory.COINSURANCE)
        and l.service_type in _PROFESSIONAL_STC
        for l in lines
    )
    has_tiers = Network.IN in nets or Network.OON in nets
    if pays_oon_professional:
        oon_benefits: bool | None = True
    elif has_tiers:
        oon_benefits = False
    else:
        oon_benefits = None

    candidates, selected = derive_plan_candidates(infos)
    plan = data.get("planInformation") or {}
    member_zip = _subscriber_zip(data)
    return EligibilityResult(
        coverage_active=coverage_active,
        # `groupDescription` is deliberately NOT a fallback here. It is the EMPLOYER (or the carrier's
        # own legal entity) — never the plan. Reproduced live 2026-07-29 on a cached Cigna 271 whose only
        # planCoverage was the junk value "Network": plan_name became "DISNEY WORLDWIDE SERVICES, INC.",
        # which `plan_string_from_271` then handed to a payer's public search box and to the capture's
        # audit note. When the 271 names no plan, None is the correct answer — it makes the driver
        # return UNKNOWN instead of searching on an employer name. The group number is still carried
        # below, as `group`, where it belongs.
        plan_name=selected or plan.get("planName"),
        group=plan.get("groupNumber"),
        coverage_dates=data.get("planDateInformation") or {},
        network_status=status,
        benefits=lines,
        pcp_required=pcp,
        prior_auth_required=prior_auth,
        referral_required=referral,
        cob=_redact_cob(data.get("coordinationOfBenefits")),
        network_verdict=None,
        corroboration=[],
        source_audit={"source": "stedi-271"},
        plan_candidates=candidates,
        selected_plan=selected,
        out_of_network_benefits=oon_benefits,
        member_zip=member_zip,
    )
