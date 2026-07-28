"""Line-of-business classifier = the Transparency-in-Coverage eligibility gate.

The federal TiC MRF mandate applies to **commercial** group/individual coverage only. Medicare
(Advantage and FFS), Medicaid, Dual (D-SNP), TRICARE and VA are all exempt — no MRF exists for
them — so TiC must never be consulted for those lines (it would return a guaranteed blank on a
live check). This module decides, from the member's real plan name (the 271's plan) and/or the
payer row's benefit_type, whether a line is commercial (TiC-eligible) or not.

Signals, in precedence order:
  1. A non-commercial marker in EITHER the plan text or the benefit_type wins — a stale
     "Commercial" tag can't fire TiC on an "AARP Medicare Advantage" plan.
  2. Otherwise a commercial marker (Commercial/ACA/exchange, or a bare PPO/HMO/POS product name)
     → commercial.
  3. No signal either way → "unknown" (treated as NOT commercial: we never invite TiC on a guess).
"""

from __future__ import annotations

import re

# non-commercial line markers, most specific first (dual is a subset of both Medicare and Medicaid)
_DUAL = re.compile(r"\bdual\b|d-?snp|\bfide\b", re.I)
# NOTE the Medi-Cal forms. `medi-?cal` case-insensitively IS the word "Medical", so it matched
# every plan named "...Medical..." and — Medicaid being tested before Medicare — turned
# "Aetna Medicare Prime Extra Medical" into a Medicaid plan. California's programme is only
# ever written hyphenated ("Medi-Cal") or camel-cased ("MediCal"), so match exactly those:
# the hyphen case-insensitively, the camelCase form case-SENSITIVELY via (?-i:...).
_MEDICAID = re.compile(r"medicaid|ahcccs|medi-cal|(?-i:MediCal)|\bchip\b", re.I)
_MEDICARE = re.compile(
    r"medicare|advantage|\bmapd\b|\bma-?pd\b|\bpdp\b|\bpart [abcd]\b|\bh\d{4}\b|\br\d{4}\b|\bs\d{4}\b",
    re.I,
)
_FEDERAL = re.compile(r"tricare|champva|\bva\b|veteran", re.I)
# commercial markers — checked only when NO non-commercial marker is present anywhere
_COMMERCIAL = re.compile(r"commercial|\baca\b|exchange|marketplace|\bppo\b|\bhmo\b|\bepo\b|\bpos\b|open access", re.I)

# --- "is there a network at all?" markers (a different axis again — see has_provider_network) ---
# Medicare Advantage (Part C) HAS a network. Checked first so it always beats the two below: an H/R/S
# contract number or the word "Advantage" is decisive MA, and "supplemental benefits" is routine MA
# marketing copy that must never be read as Medigap.
_ADVANTAGE = re.compile(r"advantage|\bmapd\b|\bma-?pd\b|\bpart c\b|\bh\d{4}\b|\br\d{4}\b|\bs\d{4}\b", re.I)
# Medicare Supplement / Medigap — pays alongside Original Medicare, no network of its own.
_MEDIGAP = re.compile(r"medigap|medicare\s+supplement|\bmed\s?supp\b|supplement\s+plan\b", re.I)
# Original Medicare / fee-for-service Part A & B. "medicare a"/"medicare part b" style only — the
# \b after the letter keeps "Medicare Advantage" from matching on its leading "a".
_ORIGINAL_MEDICARE = re.compile(
    r"original\s+medicare|traditional\s+medicare|medicare\s+ffs|fee-?for-?service"
    r"|medicare\s+(part\s+)?(a\s*(and|&|/|\+)\s*b|[ab])\b",
    re.I,
)


def _noncommercial_lob(text: str | None) -> str | None:
    t = text or ""
    if not t.strip():
        return None
    if _DUAL.search(t):
        return "dual"
    if _MEDICAID.search(t):
        return "medicaid"
    if _MEDICARE.search(t):
        return "medicare"
    if _FEDERAL.search(t):
        return "federal"
    return None


def line_of_business(plan: str | None, benefit_type: str | None) -> str:
    """Return one of: commercial | medicare | medicaid | dual | federal | unknown.

    Only "commercial" is TiC-eligible. `plan` is the member's real plan name (271); `benefit_type`
    is the payer row's coarse class. Either may be None.
    """
    found = {x for x in (_noncommercial_lob(plan), _noncommercial_lob(benefit_type)) if x}
    for lob in ("dual", "medicaid", "medicare", "federal"):
        if lob in found:
            return lob
    if _COMMERCIAL.search(plan or "") or _COMMERCIAL.search(benefit_type or ""):
        return "commercial"
    return "unknown"


def is_commercial(plan: str | None, benefit_type: str | None) -> bool:
    """True only for commercial/ACA lines — the ones subject to the TiC MRF mandate."""
    return line_of_business(plan, benefit_type) == "commercial"


def _network_signal(text: str | None) -> bool | None:
    """True (has a network) / False (none) / None (this string says nothing).

    Medicare Advantage is tested FIRST **within one string**, which is the case that rule was
    written for: "supplemental benefits" is standard MA marketing copy and an H/R/S contract number
    is decisive MA, so neither must be allowed to read as Medigap.
    """
    t = text or ""
    if not t.strip():
        return None
    if _ADVANTAGE.search(t):
        return True
    if _MEDIGAP.search(t) or _ORIGINAL_MEDICARE.search(t):
        return False
    return None


def has_provider_network(plan: str | None, benefit_type: str | None) -> bool:
    """Does this coverage have a provider network at all?

    False for **Original Medicare (FFS)** and **Medicare Supplement / Medigap**: neither has a
    network, so a provider who accepts Medicare assignment is in-network by definition and a network
    directory has no opinion to give. Asking one anyway can only yield UNKNOWN or a false OON.

    The two arguments are NOT equal evidence, and reading them as one blob was a live-only defect.
    `benefit_type` is a coarse label on a payer *row* — `humana-co-denver` carries "Medicare
    Advantage" because that is what Humana mostly sells in Denver, not because this member bought
    it. `plan` is the member's actual coverage. So the plan is consulted alone first and settles it
    outright; the payer tag is a fallback for when the plan says nothing. Reading them together let
    the tag's "Advantage" override an explicit "Medicare Supplement" plan, which silently disabled
    this whole branch for the Medigap row it exists to serve.

    Defaults to True: only coverage positively identified as network-less takes the other path.
    """
    signal = _network_signal(plan)
    if signal is None:
        signal = _network_signal(benefit_type)
    return True if signal is None else signal


# --- plan TYPE (a different axis than line of business) ------------------------------------------
# Structural product type — HMO vs PPO vs HMO-POS vs PFFS — which governs whether an out-of-network
# provider gets any plan benefit at all. HMO-POS / HMOPOS must be tested BEFORE bare HMO/POS because
# the compound string contains both substrings.
_HMOPOS = re.compile(r"hmo[\s/-]?pos", re.I)  # must precede hmo & pos
_PFFS = re.compile(r"\bpffs\b", re.I)
_PPO = re.compile(r"\bppo\b", re.I)  # local & regional & employer PPO alike (all carry OON + combined MOOP)
_EPO = re.compile(r"\bepo\b", re.I)
_HMO = re.compile(r"\bhmo\b", re.I)
_POS = re.compile(r"\bpos\b", re.I)


def plan_type(plan: str | None) -> str:
    """Structural plan type parsed from the plan string: one of
    hmo | hmopos | ppo | pffs | epo | pos | unknown. Regional/Local/Employer PPO all fold to "ppo"
    (same OON capability). "unknown" when the string carries no explicit product token (most real
    payer strings don't) OR names MORE THAN ONE product (e.g. "HMO/PPO") — an ambiguous multi-type
    label must not be resolved to a single tier. Stays conservative: downstream defers on unknown."""
    t = plan or ""
    if _HMOPOS.search(t):
        return "hmopos"
    if _PFFS.search(t):
        return "pffs"
    open_hit = bool(_PPO.search(t))
    closed_hit = bool(_HMO.search(t) or _EPO.search(t))
    if open_hit and closed_hit:
        return "unknown"  # ambiguous multi-type label (e.g. "HMO/PPO") — refuse to pick a tier
    if open_hit:
        return "ppo"
    if _EPO.search(t):
        return "epo"
    if _HMO.search(t):
        return "hmo"
    if _POS.search(t):
        return "pos"
    return "unknown"


def plan_oon_capability(plan_type_token: str | None, *, dsnp: bool = False) -> bool | None:
    """Does this plan structurally pay OUT-of-network benefits? Deliberately narrow and defensible:

      * PPO / PFFS → True   (federally must carry OON basic benefits + a combined in+out MOOP)
      * HMO / EPO  → False  (routine OON not covered — emergency/urgent only)
      * HMO-POS / POS / unknown → None  (POS door is narrow; defer to the live 271, never guess)
      * ANY D-SNP  → None   (dual cost-sharing is Medicaid-wrapped / member-specific — defer)

    None means "no structural claim" — the caller keeps whatever the 271 said. We never contradict a
    definite live 271; a capability only *fills a silent one*."""
    if dsnp:
        return None
    if plan_type_token in ("ppo", "pffs"):
        return True
    if plan_type_token in ("hmo", "epo"):
        return False
    return None  # hmopos | pos | unknown | None


# CMS PBP `pbp_a_plan_type` code → our normalized token. Only the codes whose OON behavior is
# unambiguous are mapped; everything else (PSO, MSA, PACE, PDP, Cost, Fallback) → "unknown" so the
# capability defers rather than guesses. (RFB = "reduced-fee bid" variants share their base type.)
_PBP_CODE_TO_TYPE = {
    "01": "hmo", "42": "hmo",
    "02": "hmopos", "43": "hmopos",
    "04": "ppo", "31": "ppo", "44": "ppo", "47": "ppo",  # local / regional / RFB / employer PPO
    "09": "pffs", "08": "pffs", "40": "pffs",
}


def plan_type_from_pbp_code(code: str | None) -> str:
    """Map a CMS PBP plan-type code (`pbp_a_plan_type`, e.g. '01', '04') to our normalized token."""
    return _PBP_CODE_TO_TYPE.get((code or "").strip(), "unknown")
