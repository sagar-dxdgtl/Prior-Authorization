"""Plan-name → network-name aliases.

Payer directories name networks operationally (e.g. "TX Individual Exchange Benefit
Plan"), while eligibility (271) reports carry the member's *marketing* plan name (e.g.
"UHC Bronze Essential"). These don't string-match, which leaves the FHIR adapter at
INDETERMINATE even when the provider is clearly in the right network.

This map bridges that gap explicitly: given a payer + the plan hint (+ optional state),
it returns the canonical network name(s) to also try when matching. Adding a plan you've
seen in a report is a one-line entry — auditable and reliable, no fuzzy guessing.
"""

from __future__ import annotations

import re


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


# payer_key -> list of rules. A rule fires when `when` (normalized) is a substring of the
# normalized plan hint AND (if given) `state` matches the query state.
PLAN_NETWORK_ALIASES: dict[str, list[dict]] = {
    "uhc": [
        # UHC on-exchange metal plans map to the state Individual Exchange network.
        {"when": "bronze essential", "state": "TX", "networks": ["TX Individual Exchange Benefit Plan"]},
        {"when": "exchange", "state": "TX", "networks": ["TX Individual Exchange Benefit Plan"]},
    ],
    "humana-fhir": [
        # Humana MA "giveback" plans are sold on the Medicare PPO network.
        {"when": "giveback", "networks": ["Medicare PPO"]},
        {"when": "hum full ac", "networks": ["Medicare PPO"]},
    ],
    # cigna-fhir: add entries here as Cigna plan↔network names are confirmed.
}


def network_aliases(payer: str | None, plan_hint: str | None, state: str | None = None) -> list[str]:
    """Canonical network names to also try for this payer + plan hint (+ state)."""
    hint = _norm(plan_hint)
    st = (state or "").upper()
    out: list[str] = []
    if not hint:
        return out
    for rule in PLAN_NETWORK_ALIASES.get(payer or "", []):
        if rule.get("state") and rule["state"].upper() != st:
            continue
        if _norm(rule["when"]) in hint:
            for n in rule["networks"]:
                if n not in out:
                    out.append(n)
    return out
