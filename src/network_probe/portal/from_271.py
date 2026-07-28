"""Hand a live 271's plan over to the portal layer — the missing link between eligibility and capture.

The 270/271 path already exists and is live-validated (`domain.eligibility.check_eligibility`,
`stedi.parse_271`). What did not exist is any code that takes its answer and drives a portal with it:
nothing outside this package ever constructed a `PortalQuery`. So every portal verdict so far has been
"correct for the plan a human typed", not "correct for this member's plan". This module closes that.

Why it matters more than it looks. `plan_match` only licenses an OUT_OF_NETWORK when it can pin the
plan by IDENTIFIER — a contract H/R/S/E number, a PBP, a market code like FL-0026. A human typing
"AARP Medicare Advantage" supplies none of those, so the matcher (correctly) refuses to confirm and
every verdict degrades to UNKNOWN. A real 271 carries the identifiers, which is exactly what turns the
portal answer decisive. The 2026-07-28 CareFlex mis-pin is the worked example: the loose string matched
three AARP products equally well, while "…FL-0026 (PPO) / H2406018000" pins one.

PHI boundary, deliberate and load-bearing: the plan string built here is put into a payer's public
search box and stored in the capture's audit note. It is assembled ONLY from plan/product descriptors.
Member id, DOB, MRN and subscriber name are never included, and neither is `EligibilityResult.group` —
see `plan_string_from_271` for why that one is excluded despite looking like plan metadata.
"""

from __future__ import annotations

from dataclasses import dataclass

from network_probe.portal.models import PortalQuery
from network_probe.portal.plan_match import identifiers


@dataclass(frozen=True)
class ProviderTarget:
    """The provider + clinic being checked. Deliberately a separate object from the member: nothing
    here is PHI, and nothing here comes from the 271."""

    npi: str
    first_name: str | None = None
    last_name: str | None = None
    zip_code: str | None = None
    state: str | None = None
    tin: str | None = None


def _candidate_strings(result) -> list[str]:
    """Plan/product descriptors from the 271, best first, de-duplicated.

    `selected_plan` leads because it is the plan a human or the resolver already settled on for this
    member; `plan_name` is the 271's own label; `plan_candidates` are the alternatives the 271 offered,
    included because the identifier often appears there when the chosen label omits it.
    """
    out: list[str] = []
    for value in (getattr(result, "selected_plan", None), getattr(result, "plan_name", None)):
        if value and value not in out:
            out.append(str(value))
    for cand in getattr(result, "plan_candidates", None) or []:
        # Candidates may be plain strings or dicts depending on the 271 shape.
        text = cand if isinstance(cand, str) else " ".join(
            str(v) for k, v in (cand.items() if isinstance(cand, dict) else [])
            if v and k.lower() in ("planname", "plan_name", "name", "description",
                                   "groupdescription", "contract", "pbp", "label")
        )
        text = (text or "").strip()
        if text and text not in out:
            out.append(text)
    return out


def plan_string_from_271(result) -> str | None:
    """The plan string to pin in a portal, enriched with every identifier the 271 exposes.

    The chosen label often names the product without its contract number while a sibling candidate
    carries it. Appending the identifiers we found anywhere in the 271's plan data is what lets
    `plan_match` reach a HIGH-confidence identifier match instead of a weak token match — and only a
    HIGH match may license an out-of-network verdict.

    `EligibilityResult.group` is EXCLUDED on purpose. It reads like plan metadata, but in this client's
    own data the "Ins Group Number" column holds values such as SRGB10057830 and 101601541800 — member
    identifiers, not group numbers. Since this string is typed into a public payer search box and
    persisted in the audit note, including it risks leaking a member id into both. If a genuine group
    number is ever needed for plan pinning, pass it explicitly rather than sweeping the field in.

    Returns None when the 271 named no plan at all; the caller must then leave `PortalQuery.plan`
    unset, which makes the driver answer UNKNOWN rather than guess a network.
    """
    candidates = _candidate_strings(result)
    if not candidates:
        return None
    primary = candidates[0]
    known = identifiers(primary)
    extra: list[str] = []
    for other in candidates[1:]:
        for ident in sorted(identifiers(other) - known):
            # Skip an identifier that is merely a less specific prefix of one we already carry
            # (H2406 when we already have H2406018000) — it adds no precision.
            if any(ident != k and k.startswith(ident) for k in known | set(extra)):
                continue
            extra.append(ident)
            known.add(ident)
    return f"{primary} · {' '.join(extra)}" if extra else primary


def portal_query_from_271(result, provider: ProviderTarget, payer_key: str) -> PortalQuery:
    """Build the portal lookup for a member's live 271 result.

    The member is present only as the *plan* their 271 named. No member identifier crosses into the
    query, so nothing a driver types or records can carry PHI — see the module docstring.
    """
    return PortalQuery(
        payer_key=payer_key,
        npi=provider.npi,
        provider_first_name=provider.first_name,
        provider_last_name=provider.last_name,
        plan=plan_string_from_271(result),
        state=provider.state,
        zip_code=provider.zip_code,
        tin=provider.tin,
    )


def plan_is_pinnable(result) -> bool:
    """Whether this 271 can support a decisive portal verdict at all.

    True only when the plan string carries an identifier, because `plan_match` will not set
    `confirms_network` without one and the driver will therefore refuse to call an out-of-network. Use
    it to decide up front whether a portal capture can settle a row, instead of spending a live lookup
    to discover it cannot.
    """
    plan = plan_string_from_271(result)
    return bool(plan and identifiers(plan))
