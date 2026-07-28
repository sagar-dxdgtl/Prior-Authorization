"""Match a member's 271 plan to an option in a payer portal's own plan list.

This is the load-bearing step of the whole capture: the pinned plan is what makes an absence
reading valid. Pin the wrong plan and the driver produces a confident OUT_OF_NETWORK about a network
the member isn't in — the worst failure this layer can have. So the matcher's job is as much
*declining to answer* as answering.

Why identifiers first. On 2026-07-28 a live UHC capture was given the loose plan string
"AARP Medicare Advantage" and pinned "AARP Medicare Advantage CareFlex from UHC FL-35 (HMO-POS)" —
one of several AARP products in that market — purely on shared words. Every UHC MA plan shares the
words "AARP", "Medicare" and "Advantage"; none of them distinguishes a plan. A real 271 does not give
words, it gives *identifiers*: the contract (H/R/S/E number), PBP, and segment — the same keys the
`plan_benefits` PBP layer is built on. Portal labels carry them too ("FL-0026", "H2406018000"). An
identifier match is decisive; word overlap is a weak tiebreaker that must clear a floor.

Tiers, in order:
  1. identifier overlap (contract / contract+PBP / market code)  -> HIGH
  2. distinctive-token overlap, unique best, above floor          -> MEDIUM
  3. nothing clears the floor                                     -> None

`None` is a real answer. The caller must treat it as plan_confirmed=False, which makes the verdict
UNKNOWN rather than OUT_OF_NETWORK. A weak match must make the driver decline to answer, never
quietly answer about the wrong plan.

Tier 3 (an LLM disambiguating genuinely ambiguous marketing names) is layered on top of this module,
not inside it: it is consulted only when this returns None or MEDIUM with ties, and its choice is
recorded in the capture note because the verdict's validity then depends on it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# CMS contract ids: a letter + 4 digits (H2406, R5287, S5601, E0654). A 271 may return the contract
# alone, or contract+PBP(+segment) concatenated (H2406018000 = H2406 / 018 / 000).
_CONTRACT = re.compile(r"\b([HRSE]\d{4})(\d{3})?(\d{3})?\b", re.I)

# Market/plan codes payers print in plan names: "FL-0026", "FL-35", "IL-0001".
_MARKET = re.compile(r"\b([A-Z]{2}-\d{1,4})\b", re.I)

# ACA / marketplace HIOS Standard Component ID: 5-digit issuer + 2-char state + 7 digits, optionally
# with a "-01" variant suffix (e.g. 12345FL0010001-01). Added because the Medicare-shaped identifiers
# above are the WRONG shape for the exchange lines: measured on 2026-07-28, 0 of Oscar's 90 Florida
# plan labels carried anything the contract/market patterns recognise, so `confirms_network` was
# unreachable for every ACA row and both IN and OON were impossible regardless of driver quality.
# The variant suffix is captured separately so a 271 naming the variant still matches a portal that
# prints only the base component id.
_HIOS = re.compile(r"\b(\d{5}[A-Z]{2}\d{7})(-\d{2})?\b", re.I)

# Words that appear in nearly every plan name for a line of business and therefore distinguish
# nothing. This list is the direct fix for the CareFlex mis-pin: without it, three shared words
# ("AARP", "MEDICARE", "ADVANTAGE") looked like a strong match to every AARP product in the market.
_NON_DISTINCTIVE = frozenset({
    "PLAN", "PLANS", "HEALTH", "HEALTHCARE", "CARE", "MEDICARE", "MEDICAID", "ADVANTAGE",
    "INSURANCE", "GROUP", "NETWORK", "BENEFIT", "BENEFITS", "COVERAGE", "FROM", "WITH", "THE",
    "AND", "FOR", "INC", "LLC", "COMPANY", "MEMBER", "MEMBERS", "CHOICE",  # too generic alone
    "HMO", "PPO", "POS", "EPO", "SNP", "HMOPOS", "PFFS",  # plan TYPE, not plan identity
    "COMMERCIAL", "EMPLOYER", "INDIVIDUAL", "FAMILY", "MARKETPLACE", "EXCHANGE",
    "STATEWIDE", "NATIONAL", "REGIONAL", "OPEN", "ACCESS", "SELECT", "PREFERRED", "STANDARD",
})

_MIN_DISTINCTIVE_TOKENS = 2  # below this, word overlap is noise
_TOKEN_RE = re.compile(r"[^A-Za-z0-9]+")


@dataclass
class PlanMatch:
    """A chosen option, with the basis recorded so the capture note can justify the verdict."""

    index: int
    label: str
    confidence: str  # "high" (identifier) | "medium" (distinctive tokens)
    basis: str  # human-readable reason, carried into the capture note
    identifiers: set[str] = field(default_factory=set)
    tokens: set[str] = field(default_factory=set)

    @property
    def confirms_network(self) -> bool:
        """Whether this match is strong enough to let absence be read as OUT_OF_NETWORK.

        Only an identifier match clears this bar. A word-overlap match pins a plan for the *search*
        but must not license an OON — that is precisely the CareFlex case.
        """
        return self.confidence == "high"


def identifiers(text: str | None) -> set[str]:
    """Contract / PBP / market identifiers in a plan string. These are what actually identify a plan.

    A concatenated contract+PBP yields all three granularities (H2406018000 -> H2406, H2406018,
    H2406018000) so a 271's full id still matches a portal that prints only the contract. A HIOS id
    likewise yields the base component id alongside any variant.

    Coverage is deliberately per-line, because the lines genuinely identify plans differently:
    Medicare by contract/PBP, ACA by HIOS component id, commercial by market code. Managed Medicaid
    has NO such identifier at all — that is a real gap, not an oversight, and a Medicaid driver
    therefore cannot reach `confirms_network` through this function.
    """
    found: set[str] = set()
    for m in _CONTRACT.finditer(text or ""):
        contract, pbp, segment = m.group(1).upper(), m.group(2), m.group(3)
        found.add(contract)
        if pbp:
            found.add(contract + pbp)
            if segment:
                found.add(contract + pbp + segment)
    for m in _MARKET.finditer(text or ""):
        found.add(m.group(1).upper())
    for m in _HIOS.finditer(text or ""):
        base, variant = m.group(1).upper(), m.group(2)
        found.add(base)
        if variant:
            found.add(base + variant)
    return found


def distinctive_tokens(text: str | None) -> set[str]:
    """Words that actually distinguish one plan from another in the same market and line."""
    return {
        t for t in _TOKEN_RE.split((text or "").upper())
        if len(t) >= 4 and t not in _NON_DISTINCTIVE and not t.isdigit()
    }


def match_plan(wanted: str | None, options: list[str]) -> PlanMatch | None:
    """Best option for the member's plan, or None when nothing clears the floor.

    `wanted` is the plan as the 271 describes it; `options` are the portal's own labels, in the order
    the portal listed them (the returned `index` addresses that list). Returning None is a success
    when the evidence is weak — the caller maps it to plan_confirmed=False -> UNKNOWN.
    """
    if not wanted or not options:
        return None

    want_ids = identifiers(wanted)
    want_tokens = distinctive_tokens(wanted)

    # --- Tier 1: identifier overlap. Decisive, and prefers the most specific id that matched.
    id_hits: list[tuple[int, set[str]]] = []
    for i, label in enumerate(options):
        shared = want_ids & identifiers(label)
        if shared:
            id_hits.append((i, shared))
    if id_hits:
        # Most specific match wins: longest shared identifier (contract+PBP+segment beats contract).
        i, shared = max(id_hits, key=lambda p: (max(len(s) for s in p[1]), len(p[1])))
        best = ", ".join(sorted(shared))
        note = ""
        if len(id_hits) > 1:
            note = (
                f" ({len(id_hits)} options shared an identifier; took the most specific — "
                f"review if the portal lists segments separately)"
            )
        return PlanMatch(
            index=i, label=options[i], confidence="high", identifiers=shared,
            basis=f"plan identifier match on {best}{note}",
        )

    # --- Tier 2: distinctive-token overlap. Weak, floored, and must have a unique winner.
    scored = sorted(
        ((len(want_tokens & distinctive_tokens(label)), i) for i, label in enumerate(options)),
        reverse=True,
    )
    if not scored:
        return None
    top_score, top_i = scored[0]
    if top_score < _MIN_DISTINCTIVE_TOKENS:
        return None
    if len(scored) > 1 and scored[1][0] == top_score:
        return None  # tie — cannot distinguish; escalate rather than guess

    shared_tokens = want_tokens & distinctive_tokens(options[top_i])
    return PlanMatch(
        index=top_i, label=options[top_i], confidence="medium", tokens=shared_tokens,
        basis=(
            f"no plan identifier in either the 271 plan string or the portal labels; matched on "
            f"{top_score} distinctive term(s) ({', '.join(sorted(shared_tokens))}). Names alone do "
            f"not identify a plan, so this pins the search but does NOT license an out-of-network "
            f"reading."
        ),
    )


def needs_disambiguation(wanted: str | None, options: list[str]) -> bool:
    """Whether tier 3 (LLM disambiguation) is worth a call: the deterministic tiers found no
    identifier and either tied or fell below the floor, but there is still something to choose from."""
    if not wanted or len(options) < 2:
        return False
    return match_plan(wanted, options) is None
