"""Derive the member's plan(s) from a 271's benefitsInformation[].planCoverage strings.

The plan the payer actually returns lives in benefitsInformation[].planCoverage (a string like
"DEVOTED CHOICE GIVEBACK 003 CO (PPO)" / "BASE SILVER CSR 150"), NOT planInformation (usually {}).
Most 271s carry 2+ distinct values (e.g. a dual-eligible member has an MA product line AND a
Medicaid segment); we rank a real product/network line above a coverage segment, and drop generic
junk ("Network") so we never scope a directory search on a meaningless string.
"""

from __future__ import annotations

import re

# Real product/network markers (metal tiers, network types, MA product words).
_PRODUCT = re.compile(r"\b(HMO|PPO|EPO|POS|SILVER|BRONZE|GOLD|PLATINUM|CHOICE|ESSENTIAL|ADVANTAGE)\b", re.I)
# Coverage-segment markers that are NOT a network to search (dual-eligible / affiliation lines).
_SEGMENT = re.compile(r"\b(SLMB|QMB|PARTIAL DUAL|DUAL|MEDICAID|AFFILIATION|CENTER)\b", re.I)
# Generic strings that carry no plan identity — never a usable directory scope.
_JUNK = {"", "network", "health benefit plan coverage", "medical", "coverage"}


def _is_product(s: str) -> bool:
    return bool(_PRODUCT.search(s))


def _is_segment(s: str) -> bool:
    return bool(_SEGMENT.search(s))


def _rank_bucket(s: str) -> int:
    if _is_segment(s):
        return 2
    if _is_product(s):
        return 0
    return 1


def derive_plan_candidates(benefits_information: list[dict] | None) -> tuple[list[dict], str | None]:
    order: list[str] = []
    seen: set[str] = set()
    for b in benefits_information or []:
        pc = (b.get("planCoverage") or "").strip()
        if pc.lower() in _JUNK or pc.lower() in seen:
            continue
        seen.add(pc.lower())
        order.append(pc)
    ranked = sorted(order, key=lambda s: (_rank_bucket(s), order.index(s)))
    candidates = [{"plan": s, "is_product": _is_product(s), "rank": i} for i, s in enumerate(ranked)]
    return candidates, (ranked[0] if ranked else None)
