"""Arbitrate disagreeing network facts using effective dates rather than recency.

A live directory and a TiC MRF disagreeing about one network usually differ about the *date*, not the
fact. Ins Test 3 row 3 is the worked example: Oscar's directory said Sanders (NPI 1700846789) was IN
net 065 while the ingested MRF recorded a physician gap. Neither source was wrong — the contract began
**2026-01-27** and the MRF was built before that, so the MRF was correct when it was built.

    2019-02-06 -> 2026-01-26   in_network = False     (seven years OUT)
    2026-01-27 -> 2026-12-31   in_network = TRUE      <- contract STARTS
    2027-01-01 -> (open)       in_network = False     <- contract TERMINATES

The rule below turns that class of REVIEWs into answers, deterministically and with no model in the
path. See HANDOFF-2026-07-29.md §4a.

**Recency is deliberately NOT the rule.** "The newer source wins" would be wrong whenever the newer
source is simply less complete. Only a contract that *began after the other source was built* explains
a disagreement; absent that evidence the honest answer is REVIEW.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime


@dataclass(frozen=True)
class Reconciliation:
    """The arbitrated answer, with the basis recorded so a verdict note can justify itself.

    `in_network` is None whenever the sources cannot be reconciled — never a guess, and never a
    silent flip of one source to match the other.
    """

    in_network: bool | None
    status: str  # "no_facts" | "single_source" | "agreed" | "stale_source" | "review"
    reason: str = ""


def _as_date(v) -> date | None:
    if isinstance(v, datetime):
        return v.date()
    return v if isinstance(v, date) else None


def _get(fact, name):
    """Read a field from either a dict or an ORM row, so callers need not convert."""
    return fact.get(name) if isinstance(fact, dict) else getattr(fact, name, None)


def reconcile_network(facts) -> Reconciliation:
    """Resolve one network's facts from possibly-disagreeing sources.

    `facts` are rows for the SAME (payer, npi, network) — the caller groups them. Each needs
    `in_network`, and may carry `source`, `start_date` and `source_built_at`.
    """
    facts = list(facts or [])
    if not facts:
        return Reconciliation(None, "no_facts", "no stored facts for this provider and network")
    if len(facts) == 1:
        f = facts[0]
        return Reconciliation(
            bool(_get(f, "in_network")), "single_source",
            f"one fact, from {_get(f, 'source') or 'an unnamed source'}",
        )

    values = {bool(_get(f, "in_network")) for f in facts}
    if len(values) == 1:
        only = values.pop()
        srcs = ", ".join(sorted({str(_get(f, "source") or "?") for f in facts}))
        return Reconciliation(only, "agreed", f"{len(facts)} facts agree ({srcs})")

    # --- they disagree. A contract that began after the other source was BUILT explains it.
    for winner in facts:
        start = _as_date(_get(winner, "start_date"))
        if start is None:
            continue
        for other in facts:
            if other is winner or bool(_get(other, "in_network")) == bool(_get(winner, "in_network")):
                continue
            built = _as_date(_get(other, "source_built_at"))
            if built is not None and start > built:
                return Reconciliation(
                    bool(_get(winner, "in_network")), "stale_source",
                    f"{_get(winner, 'source') or 'one source'} records the contract starting "
                    f"{start.isoformat()}, after {_get(other, 'source') or 'the other source'} was "
                    f"built {built.isoformat()} — so the older source was correct when built and is "
                    f"now stale, not in conflict.",
                )

    return Reconciliation(
        None, "review",
        "sources disagree and no contract start post-dates the other source's build date, so nothing "
        "explains the difference — a human must settle it rather than recency deciding it.",
    )
