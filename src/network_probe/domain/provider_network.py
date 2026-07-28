"""Plan-type-aware provider-INN resolver — the top of check_network.

Fuses the two provider-network sources by line of business:

  * credentialing matrix — the clinic's own (payer, plan, NPI, TIN) contract; covers EVERY line
    (Medicare/Medicaid/commercial) and can assert both IN and OON.
  * TiC MRF (tic_network) — live, COMMERCIAL-only; a billing TIN found in a payer's real in-network
    file proves IN; absence proves nothing (MRFs are incomplete).

Fusion:
  COMMERCIAL / unknown line:
    - credentialing IN  + TiC IN            → IN   (both agree, high)
    - credentialing OON + TiC IN            → REVIEW (contract vs MRF conflict — human verify)
    - credentialing present, TiC silent     → credentialing (TiC inconclusive, not a flip)
    - no credentialing, TiC IN              → IN   (live, via TiC MRF)
    - neither                               → None (fall through to the directory leg)
  MEDICARE / MEDICAID / DUAL / FEDERAL line (TiC-exempt):
    - credentialing present                 → credentialing (TiC marked N/A — exempt, not blank)
    - none                                  → None (directory leg; TiC never consulted)

Returns a NetworkVerdict to short-circuit check_network, or None to let the directory leg run.
"""

from __future__ import annotations

from network_probe.domain.line_of_business import has_provider_network, line_of_business
from network_probe.domain.models import NetworkStatus, NetworkVerdict, ProviderQuery
from network_probe.domain.tic_network import (
    tic_network_status,
    tic_roster_networks,
    tic_tin_in_roster,
)

_EXEMPT = {"medicare", "medicaid", "dual", "federal"}


def enrollment_negative(q, lob, pecos_fn=None, medicaid_fn=None):
    """Decisive negative for Medicare/Medicaid lines: if the provider is CONFIRMED not enrolled in the
    program (a *successful* PECOS/state-Medicaid lookup that found no match), return an OON verdict —
    you cannot be in-network for a program you can't bill. Returns None on enrolled/undetermined/
    commercial, or on a failed lookup (never a false OON). `enrolled is True` only clears the gate."""
    if not q.npi:
        return None
    from network_probe.domain.enrollment import live_enabled, medicaid_enrollment, pecos_enrollment

    if pecos_fn is None and medicaid_fn is None and not live_enabled():
        return None  # test env, no injected lookup -> skip live enrollment (preserves prior behavior)
    pecos_fn = pecos_fn or pecos_enrollment
    medicaid_fn = medicaid_fn or medicaid_enrollment
    if lob in ("medicare", "dual"):
        res = pecos_fn(q.npi)  # dual is Medicare + Medicaid; PECOS covers the Medicare prerequisite
    elif lob == "medicaid":
        res = medicaid_fn(q.npi, q.state)
    else:
        return None
    if res is None or res.enrolled is not False:
        return None
    return NetworkVerdict(
        status=NetworkStatus.OUT_OF_NETWORK,
        matched_provider={"npi": q.npi, "enrollment_program": res.program, "enrolled": False},
        plan_or_network_checked=f"{q.payer} — {res.program} enrollment",
        source_url="enrollment",
        confidence="high",
        notes=f"{res.detail} A provider not enrolled in the program cannot be in-network for it.",
        corroboration=[{"source": "Enrollment", "result": "contradicts", "detail": res.detail}],
    )


def no_network_verdict(q, pecos_fn=None) -> NetworkVerdict:
    """Settle coverage that has NO provider network — Original Medicare (FFS) and Medicare
    Supplement / Medigap.

    There is nothing to be in or out of: a provider who accepts Medicare assignment is in-network by
    definition, and one not enrolled in Medicare cannot bill it at all. So Medicare participation
    (PECOS) IS the answer here, and a network directory — which has no opinion to give — must never
    be consulted. This ALWAYS returns a verdict rather than None: returning None would fall through
    to the directory leg, which is precisely the false-OON path this branch exists to close.
    """
    if not q.npi:
        return NetworkVerdict(
            status=NetworkStatus.UNKNOWN,
            matched_provider=None,
            plan_or_network_checked=f"{q.payer} — {q.plan_hint or 'Original Medicare / Medigap'}",
            source_url="enrollment",
            confidence="low",
            notes=(
                "This coverage has no provider network (Original Medicare / Medicare Supplement), so "
                "Medicare participation decides it — but no NPI was supplied to check."
            ),
        )

    from network_probe.domain.enrollment import live_enabled, pecos_enrollment

    if pecos_fn is None and live_enabled():
        pecos_fn = pecos_enrollment
    res = pecos_fn(q.npi) if pecos_fn is not None else None

    base = (
        f"{q.plan_hint or 'This coverage'} has no provider network — it pays alongside Original "
        f"Medicare — so a Medicare-participating provider is in-network by definition and the payer's "
        f"network directory has no opinion to give."
    )
    if res is not None and res.enrolled is True:
        return NetworkVerdict(
            status=NetworkStatus.IN_NETWORK,
            matched_provider={"npi": q.npi, "tin": q.tin, "enrollment_program": res.program, "enrolled": True},
            plan_or_network_checked=f"{q.payer} — {res.program} participation (no provider network)",
            source_url="enrollment",
            confidence="high",
            notes=f"{base} {res.detail}",
            corroboration=[{"source": "Enrollment", "result": "corroborates", "detail": res.detail}],
        )
    if res is not None and res.enrolled is False:
        return NetworkVerdict(
            status=NetworkStatus.OUT_OF_NETWORK,
            matched_provider={"npi": q.npi, "tin": q.tin, "enrollment_program": res.program, "enrolled": False},
            plan_or_network_checked=f"{q.payer} — {res.program} participation (no provider network)",
            source_url="enrollment",
            confidence="high",
            notes=f"{base} {res.detail} A provider not enrolled in Medicare cannot bill it at all.",
            corroboration=[{"source": "Enrollment", "result": "contradicts", "detail": res.detail}],
        )
    detail = res.detail if res is not None else "no Medicare enrollment lookup was available"
    return NetworkVerdict(
        status=NetworkStatus.UNKNOWN,
        matched_provider={"npi": q.npi, "tin": q.tin},
        plan_or_network_checked=f"{q.payer} — Medicare participation (no provider network)",
        source_url="enrollment",
        confidence="low",
        notes=(
            f"{base} Medicare participation could not be confirmed ({detail}), so this is UNKNOWN — "
            f"deliberately NOT referred to the payer's network directory, which could only answer "
            f"about a network this member does not have."
        ),
        corroboration=[{"source": "Enrollment", "result": "inconclusive", "detail": detail}],
    )


def group_contracted(payer, tin, credentialing=None, crosswalk=None, store=None) -> bool | None:
    """Is the clinic's billing TIN contracted with this payer under ANY NPI? True on positive evidence
    (an in-network credentialing row at that TIN, a TiC MRF hit for that TIN, or a persisted
    provider-network fact); None when there's no positive evidence (absence isn't proof). This splits
    Physician OON (group contracted, physician not) from payer-level OON.

    Sources are resolved lazily so callers can pass just what they have (or nothing → defaults)."""
    if not tin:
        return None
    if credentialing is None:
        from network_probe.domain.credentialing import default_credentialing

        credentialing = default_credentialing()
    if credentialing is not None and credentialing.group_contracted(payer, tin) is True:
        return True
    if crosswalk is None:
        from network_probe.domain.tin_crosswalk import default_crosswalk

        crosswalk = default_crosswalk()
    if crosswalk is not None and crosswalk.has_tin(payer, tin):
        return True
    if store is None:
        try:
            from network_probe.domain.network_facts import default_provider_network_store

            store = default_provider_network_store()
        except Exception:
            store = None
    if store is not None:
        try:
            if store.group_contracted(payer, tin) is True:
                return True
        except Exception:
            pass
    return None


def _sig(result: str, detail: str) -> dict:
    return {"source": "TIC", "result": result, "detail": detail}


def _cred_verdict(q: ProviderQuery, cred, tic_signal: dict | None) -> NetworkVerdict:
    io = "IN" if cred.in_network else "OUT"
    return NetworkVerdict(
        status=NetworkStatus.IN_NETWORK if cred.in_network else NetworkStatus.OUT_OF_NETWORK,
        matched_provider={"npi": q.npi, "tin": q.tin, "credentialing": True, "plan": cred.plan},
        plan_or_network_checked=f"{q.payer} credentialing (plan: {cred.plan or q.plan_hint or '—'})",
        source_url="credentialing-matrix",
        confidence="high",
        notes=(
            f"NPI {q.npi} billing under TIN {q.tin} is {io}-of-network for {q.payer} per "
            f"clinic credentialing ({cred.source})."
        ),
        corroboration=[tic_signal] if tic_signal else [],
    )


def _tic_in_verdict(q: ProviderQuery, known: list) -> NetworkVerdict:
    return NetworkVerdict(
        status=NetworkStatus.IN_NETWORK,
        matched_provider={"npi": q.npi, "tin": q.tin, "tic": True, "in_network_tins": known},
        plan_or_network_checked=f"{q.payer} Transparency-in-Coverage in-network MRF",
        source_url="tic-mrf",
        confidence="high",
        notes=(
            f"NPI {q.npi} billing under TIN {q.tin} is in-network for {q.payer} per the payer's "
            f"Transparency-in-Coverage in-network MRF (live, commercial)."
        ),
        corroboration=[
            _sig("corroborates", f"Billing TIN {q.tin} is in-network for {q.payer} per its "
                 f"Transparency-in-Coverage in-network MRF (live).")
        ],
    )


def _physician_gap_verdict(
    q: ProviderQuery, others: list, networks: list | None = None, npi_tins: list | None = None
) -> NetworkVerdict:
    """The one OON that Transparency-in-Coverage can license: the billing TIN is in the payer's
    in-network roster under other NPIs, ours is not. `group_contracted` is set so the determination
    layer labels it Physician OON rather than payer-level OON.

    `networks` names the roster(s) the evidence came from. A payer key can span several networks
    (Oscar GA sells both 063 and 065), and a TIN in one roster says nothing about the other — so
    the scope of the evidence goes in the note rather than being quietly generalised."""
    scope = (
        f" Evidence is from {', '.join(networks)}; a TIN in one network's roster says nothing "
        f"about the payer's other networks."
        if networks
        else ""
    )
    # "Contracted, but under a different tax ID" is a contracting problem, not a credentialing
    # one — and it is actionable in a way that a flat OON is not. Say which TIN, when we know.
    elsewhere = (
        f" NOTE: this NPI IS in-network with this payer under other billing TIN(s) "
        f"{', '.join(npi_tins)} — claims billed under {q.tin} would still price out-of-network, "
        f"but the provider is contracted, just not under this tax ID."
        if npi_tins
        else ""
    )
    return NetworkVerdict(
        status=NetworkStatus.OUT_OF_NETWORK,
        matched_provider={
            "npi": q.npi,
            "tin": q.tin,
            "tic": True,
            "group_contracted": True,
            "roster_npis_at_tin": others,
            "roster_networks": networks or [],
            "in_network_tins_for_npi": npi_tins or [],
        },
        plan_or_network_checked=f"{q.payer} Transparency-in-Coverage in-network MRF (roster at TIN {q.tin})",
        source_url="tic-mrf",
        confidence="medium",
        notes=(
            f"Physician gap: billing TIN {q.tin} is in-network in {q.payer}'s Transparency-in-Coverage "
            f"MRF under {len(others)} other NPI(s), but NPI {q.npi} is not among them — the group is "
            f"contracted and this physician is not. This is presence-under-other-NPIs, not absence: a "
            f"TIN merely missing from an MRF would prove nothing." + scope + elsewhere
        ),
        corroboration=[
            _sig(
                "contradicts",
                f"Billing TIN {q.tin} is in {q.payer}'s Transparency-in-Coverage in-network MRF under "
                f"{len(others)} other NPI(s); NPI {q.npi} is absent from that roster.",
            )
        ],
    )


def _conflict_verdict(q: ProviderQuery, cred, known: list) -> NetworkVerdict:
    return NetworkVerdict(
        status=NetworkStatus.REVIEW,
        matched_provider={"npi": q.npi, "tin": q.tin, "credentialing": True, "tic": True, "plan": cred.plan},
        plan_or_network_checked=f"{q.payer} credentialing vs Transparency-in-Coverage MRF",
        source_url="credentialing-matrix+tic-mrf",
        confidence="conflict",
        notes=(
            f"CONFLICT — clinic credentialing has NPI {q.npi} / TIN {q.tin} OUT-of-network for {q.payer}, "
            f"but that billing TIN appears in the payer's Transparency-in-Coverage in-network MRF. "
            f"Flagged for human verification."
        ),
        corroboration=[
            _sig("contradicts", f"Billing TIN {q.tin} IS in {q.payer}'s Transparency-in-Coverage "
                 f"in-network MRF, but clinic credentialing has it OUT-of-network — review.")
        ],
    )


def resolve_provider_network(
    q: ProviderQuery,
    benefit_type: str | None = None,
    credentialing=None,
    crosswalk=None,
    store=None,
    pecos_fn=None,
) -> NetworkVerdict | None:
    """Resolve provider-INN from credentialing + TiC, gated by line of business. See module docstring."""
    # Checked FIRST, and ahead of the billing-TIN gate: coverage with no provider network at all
    # (Original Medicare FFS / Medicare Supplement-Medigap) is answered by Medicare participation,
    # not by any network source, and needs only an NPI.
    if not has_provider_network(q.plan_hint, benefit_type):
        return no_network_verdict(q, pecos_fn=pecos_fn)

    if not (q.npi and q.tin):
        return None
    if credentialing is None:
        from network_probe.domain.credentialing import default_credentialing

        credentialing = default_credentialing()

    lob = line_of_business(q.plan_hint, benefit_type)
    cred = credentialing.lookup(q.payer, q.npi, q.tin, plan=q.plan_hint)

    # TiC-exempt lines (Medicare/Medicaid/Dual/federal): credentialing first, TiC never consulted.
    if lob in _EXEMPT:
        if cred is not None:
            na = _sig(
                "n/a",
                "Transparency-in-Coverage MRFs do not cover this line — Medicare Advantage, Medicaid and "
                "Dual are federally exempt; provider network taken from clinic credentialing.",
            )
            return _cred_verdict(q, cred, na)
        # No credentialing record — try the enrollment negative filter: a provider NOT enrolled in
        # Medicare (PECOS) / the state's Medicaid cannot be in-network for this line → decisive OON.
        # (Enrolled/undetermined → None, fall through to the directory leg — enrolled ≠ INN.)
        ev = enrollment_negative(q, lob)
        if ev is not None:
            return ev
        return None

    # Commercial (or unknown): TiC is the live signal.
    tic_status, known = tic_network_status(q.payer, q.npi, q.tin, crosswalk=crosswalk, store=store)
    tic_in = tic_status == NetworkStatus.IN_NETWORK

    if cred is not None and tic_in:
        if cred.in_network:
            v = _cred_verdict(
                q, cred,
                _sig("corroborates", f"Billing TIN {q.tin} confirmed in-network in {q.payer}'s "
                     f"Transparency-in-Coverage MRF (live)."),
            )
            v.source_url = "credentialing-matrix+tic-mrf"
            return v
        return _conflict_verdict(q, cred, known)

    # Our (NPI, TIN) pair isn't in the MRF. Is the TIN there under OTHER NPIs? That is the
    # physician gap — the group is contracted, this physician is not — and the only OON that
    # TiC can license. A TIN simply absent from the MRF still proves nothing.
    gap_present, gap_others = tic_tin_in_roster(
        q.payer, q.tin, npi=q.npi, crosswalk=crosswalk, store=store
    )
    gap = gap_present and bool(gap_others)

    if cred is not None:
        if gap:
            detail = (
                f"Billing TIN {q.tin} IS in {q.payer}'s Transparency-in-Coverage in-network MRF under "
                f"{len(gap_others)} other NPI(s), but NPI {q.npi} is not among them — the group is "
                f"contracted and this physician is not."
            )
            # A credentialing OON and an MRF physician gap say the same thing; a credentialing IN
            # is not contradicted by it, because absence from an MRF is never proof of OON.
            return _cred_verdict(
                q, cred, _sig("corroborates" if not cred.in_network else "inconclusive", detail)
            )
        detail = (
            f"Billing TIN {q.tin} not found in {q.payer}'s Transparency-in-Coverage MRF"
            + (f" (MRF lists other TINs for this NPI: {sorted(known)})" if known else "")
            + "; provider network taken from clinic credentialing."
        )
        return _cred_verdict(q, cred, _sig("inconclusive", detail))

    if tic_in:
        return _tic_in_verdict(q, known)

    if gap:
        return _physician_gap_verdict(
            q, gap_others,
            networks=tic_roster_networks(q.payer, q.tin, store=store),
            npi_tins=known,  # in-network TINs the MRF holds for THIS NPI (see tic_network_status)
        )

    return None  # nothing decisive → let the directory leg run
