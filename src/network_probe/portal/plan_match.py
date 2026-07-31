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

# Market/plan codes payers print in plan names: "FL-0026", "FL-35", "IL-0001" — and also "FL-001P",
# "FL-MA01", "FL-MA2". The suffix is NOT digits-only: measured on 2026-07-29 against UHC's live FL AARP
# list, a digits-only pattern saw 4 of 7 real codes and silently missed the rest. Medicare is the one
# line where identifiers converge between a 271 and a portal label, so an unreadable code is a tier-1
# match that never happens. A digit is still required (see `_market_codes`) or "HMO-POS" would qualify.
_MARKET = re.compile(r"\b([A-Z]{2}-[A-Z0-9]{1,5})\b", re.I)

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
    "STATEWIDE", "NATIONAL", "REGIONAL", "OPEN", "SELECT", "PREFERRED", "STANDARD",
})
# NOTE "ACCESS" was removed from that set on 2026-07-29. It is not near-universal — it is the ONLY thing
# separating UHC's "NHP HMO/POS" from "NHP HMO/POS Access", so discarding it made those two options
# indistinguishable. The bar for this set is "appears in nearly every plan name for a line of business";
# ACCESS does not clear it.

_MIN_DISTINCTIVE_TOKENS = 2  # below this, word overlap is noise
# 3, not 4. Network names carry short but highly discriminating codes — NHP (Neighborhood Health
# Partnership), OAP, HPN, BCO. A 4-char floor discarded them, which is why "UHC Commerical NHP Access
# HMO" — the one Ins Test 3 string that names a real network — scored zero against every option.
# Generic plan TYPES of the same length (HMO/PPO/POS/EPO/SNP) stay excluded via _NON_DISTINCTIVE.
_MIN_TOKEN_LEN = 3
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


def _market_codes(text: str | None) -> set[str]:
    """Market/plan codes like FL-0026, FL-001P, FL-MA01 — requiring at least one digit.

    The digit requirement is what keeps the loosened pattern honest: without it "HMO-POS", "D-SNP" and
    any other hyphenated product word would read as a plan identifier and could license an OON.
    """
    return {
        m.group(1).upper()
        for m in _MARKET.finditer(text or "")
        if any(ch.isdigit() for ch in m.group(1))
    }


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
    found |= _market_codes(text)
    for m in _HIOS.finditer(text or ""):
        base, variant = m.group(1).upper(), m.group(2)
        found.add(base)
        if variant:
            found.add(base + variant)
    return found


def _exact_key(text: str | None) -> str:
    """Normalised form for deciding "this string IS that label".

    Case and whitespace only. A 271 writes "Statewide / National PPO" where the payer publishes
    "Statewide/National PPO", and spacing around a slash must not decide the question. Punctuation is
    deliberately NOT stripped: "Statewide/National PPO/EPO", "Statewide/National PPO + Prosano" and
    "Statewide PPO" are three different AZ Blue networks, and flattening their punctuation would
    collapse distinctions the payer draws on purpose.
    """
    return re.sub(r"\s+", "", (text or "")).upper()


def distinctive_tokens(text: str | None) -> set[str]:
    """Words that actually distinguish one plan from another in the same market and line."""
    return {
        t for t in _TOKEN_RE.split((text or "").upper())
        if len(t) >= _MIN_TOKEN_LEN and t not in _NON_DISTINCTIVE and not t.isdigit()
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

    # --- Tier 1.5: the plan string IS one of the labels. An exact match is not a tie.
    #
    # Tier 2 refuses a tie rather than guess, which is right, but it was applying that refusal to
    # strings that needed no guessing at all. Measured 2026-07-31 against AZ Blue's 14 published
    # networks, feeding each label back in as the plan string resolved only 6 of 14: "Alliance HMO"
    # lost to a tie because ALLIANCE recurs in "Alliance PPO/EPO" and HMO in "Statewide HMO". When one
    # option IS the string, there is nothing to choose between.
    #
    # Confidence stays "medium" — identifier-only is the bar for `confirms_network`, stated in five
    # drivers, and an exact name is still a name: two markets of one payer can print the same network
    # name. This pins the search; it does not license an out-of-network reading.
    want_exact = _exact_key(wanted)
    if want_exact:
        exact = [i for i, label in enumerate(options) if _exact_key(label) == want_exact]
        if len(exact) == 1:
            i = exact[0]
            return PlanMatch(
                index=i, label=options[i], confidence="medium",
                tokens=want_tokens & distinctive_tokens(options[i]),
                basis=(
                    f"the plan string names this network exactly ({options[i]!r}). Exact, so not the "
                    f"ambiguity the tie rule guards against — but a name is not an identifier, so it "
                    f"pins the search and does NOT license an out-of-network reading."
                ),
            )
        if len(exact) > 1:
            return None  # the portal lists the label twice; picking one would be the guess we refuse

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


def match_plan_with_fallback(
    wanted: str | None, options: list[str], *, client=None, enabled: bool | None = None
) -> PlanMatch | None:
    """`match_plan` with tier 3 (model disambiguation) underneath it.

    The deterministic tiers always run first and always win — an identifier match is free, decisive,
    and the only thing that can license an OUT_OF_NETWORK, so tier 3 is never consulted when tier 1
    or 2 answered. Tier 3 only ever converts an UNKNOWN into a searchable plan; it cannot promote a
    verdict, because the PlanMatch it returns is confidence "medium" and so fails `confirms_network`.

    Off unless explicitly enabled (Settings.plan_llm_enabled), and a disabled call is byte-for-byte
    the old behaviour. See portal/plan_llm.py for the PHI and safety boundaries.
    """
    m = match_plan(wanted, options)
    if m is not None:
        return m
    if not needs_disambiguation(wanted, options):
        return None
    if enabled is None:
        try:
            from network_probe.core.config import get_settings

            enabled = bool(get_settings().plan_llm_enabled)
        except Exception:  # noqa: BLE001 — unconfigured means off, not broken
            enabled = False
    if not enabled:
        return None
    from network_probe.portal.plan_llm import disambiguate_plan

    return disambiguate_plan(wanted, options, client=client)
