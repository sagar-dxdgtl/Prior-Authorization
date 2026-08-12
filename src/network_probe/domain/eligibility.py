from __future__ import annotations

from network_probe.domain.benefits import EligibilityResult
from network_probe.domain.models import NetworkStatus, ProviderQuery
from network_probe.domain.service import check_network
from network_probe.payers.catalogue import DbPayerCatalogue, PayerCatalogue
from network_probe.stedi.client import EligibilitySource, StediEligibilityClient


def reconcile(stedi_status: NetworkStatus, verdict) -> tuple[NetworkStatus, list]:
    """Merge the 271-derived status with the provider-network verdict (the correctness core).

    Re-ranked: the provider-network verdict (credentialing / TiC / directory / enrollment) is the
    AUTHORITY on provider network. A 271 gives coverage + the plan's OON tier — NOT reliable
    provider-specific network — so:
      * a decisive verdict (IN / OON / REVIEW) wins outright. A 271-vs-verdict disagreement is NOT a
        conflict: genuine provider-source conflicts (e.g. credentialing vs TiC) already arrive as
        verdict.status == REVIEW and are preserved here.
      * only when the verdict is silent (None / UNKNOWN) does the weak 271 status stand as a fallback.
    This stops the 271's unreliable network indicator from demoting a real credentialing/TiC finding
    to REVIEW (the Perry/Munar false-conflicts).
    """
    if verdict is None:
        return _weak_271(stedi_status), []
    corr = verdict.corroboration or []
    if verdict.status != NetworkStatus.UNKNOWN:
        return verdict.status, corr
    return _weak_271(stedi_status), corr


def _weak_271(stedi_status: NetworkStatus) -> NetworkStatus:
    """What a 271 may assert on its own, with no provider-level source behind it.

    NOT in-network. The 271's network indicator is a PLAN-TIER flag — the evidence panel says so in
    its own words, "a 271 gives plan-tier only — provider-specific network is UNKNOWN here" — and
    letting it stand as the final verdict turned that flag into a confident provider-level IN backed
    by nothing. Live on 2026-08-03: Susan Smith (Aetna Medicare Prime Extra, NPI 1780175349) read
    "In-Network · high" while credentialing said NO_RECORD, TiC said N/A (Medicare is federally
    exempt), the payer directory errored and PECOS said only "enrolled != in-network". The member is
    on an ARIZONA Medicare HMO; the provider practises in Illinois.

    OUT_OF_NETWORK is deliberately left standing. It is equally plan-tier, but it is the conservative
    direction: a false OON is caught and recovered, a false IN is billed and denied. The asymmetry is
    the same one used everywhere else here.
    """
    return NetworkStatus.UNKNOWN if stedi_status == NetworkStatus.IN_NETWORK else stedi_status


def check_eligibility(
    q: ProviderQuery,
    base_url: str | None = None,
    catalogue: PayerCatalogue | None = None,
    stedi: EligibilitySource | None = None,
    tenant_id=None,
    override_store=None,
    stedi_payer_id: str | None = None,
) -> EligibilityResult:
    cat = catalogue or DbPayerCatalogue()
    payer = cat.resolve(q.payer)
    # An explicit stedi_payer_id (from a Stedi-directory-sourced payer with no roster row) wins;
    # otherwise use the resolved roster row's id.
    effective_id = stedi_payer_id or (payer.stedi_payer_id if payer else None)
    source = stedi or StediEligibilityClient(payer_id=effective_id)
    result = source.check(q)
    # THE PLAN CAN ARRIVE IN THE IDENTIFIER FIELD. When the payer names no plan — because it rejected
    # the identity, which is the normal outcome for a sheet whose only identifier column is a group
    # number — a CMS contract-PBP-segment typed into that field is still a valid plan pin, and for a
    # Medicare portal it is the ONLY thing that pins a network. Shape-tested, never swept: a value
    # that is not a contract-PBP-segment yields nothing, so no member identifier can leave this way.
    # See portal/from_271.plan_pin_from_identifier for why that test is the whole safety argument.
    if not result.selected_plan and not result.plan_name:
        from network_probe.portal.from_271 import plan_pin_from_identifier

        if (pin := plan_pin_from_identifier(getattr(q, "member_id", None))):
            result.selected_plan = pin
            result.plan_pin_source = "identifier field on the form (a CMS plan id, not the payer's 271)"
    # The 271 knows the member's real plan; scope the directory leg by it when the caller gave none.
    if not q.plan_hint and result.selected_plan:
        q.plan_hint = result.selected_plan
    result.stedi_network_status = result.network_status  # capture pre-merge (271-only) status
    # Directory engine still owns provider-specific network status; merge/corroborate.
    # Reuse the resolved catalogue so a payer with a verified-public `fhir_base_url` routes its
    # directory leg to the FHIR PDEX adapter (no second DB lookup, no live call in tests).
    kw: dict = {"catalogue": cat}
    if base_url:
        kw["base_url"] = base_url
    try:
        verdict = check_network(q, **kw)
    except Exception:
        verdict = None
    result.network_verdict = verdict
    result.network_status, result.corroboration = reconcile(result.network_status, verdict)
    # Apply tenant-scoped golden-record override as the authoritative last word.
    store = override_store
    if store is None and tenant_id is not None:
        from network_probe.domain.overrides import DbOverrideStore

        store = DbOverrideStore(tenant_id)
    if store is not None:
        ov = store.lookup(q)
        if ov is not None:
            result.network_status = NetworkStatus(ov.status)
            result.corroboration = (result.corroboration or []) + [
                {
                    "source": "override",
                    "result": "authoritative",
                    "detail": f"{ov.status} confirmed by {ov.verified_by} ({ov.verified_at})",
                }
            ]
            result.source_audit = {**(result.source_audit or {}), "override": f"{ov.verified_by} {ov.verified_at}"}
    # Final client-facing label: the reconciled provider network status (credentialing → TiC →
    # directory → override) combined with the 271's out-of-network benefit tier.
    from network_probe.domain.determination import final_determination

    # Group-contract signal (physician-OON vs payer-OON) — only meaningful when the provider is OON.
    gc = None
    if result.network_status == NetworkStatus.OUT_OF_NETWORK and q.npi and q.tin:
        from network_probe.domain.provider_network import group_contracted

        try:
            # Pass the LINE OF BUSINESS: TiC-derived evidence is commercial-only, so it must not
            # split physician-OON from payer-OON for a Medicare/Medicaid/Dual member. Caught live —
            # commercial-MRF facts made two AARP Medicare Advantage members read PHYSICIAN_OUT_OF_
            # NETWORK where the staff answer key says OON w/ Benefits.
            from network_probe.domain.benefit_type import benefit_type_for
            from network_probe.domain.line_of_business import line_of_business

            _plan = result.selected_plan or result.plan_name or q.plan_hint
            _bt = benefit_type_for(q.payer, _plan, catalogue=cat)
            gc = group_contracted(q.payer, q.tin, lob=line_of_business(_plan, _bt))
        except Exception:
            gc = None
    # The member's plan selects which of the payer key's benefit_type rows applies; the
    # catalogue's first row is only a fallback. See domain/benefit_type.py.
    from network_probe.domain.benefit_type import benefit_type_for

    _plan_for_line = result.selected_plan or result.plan_name or q.plan_hint
    benefit_type = benefit_type_for(q.payer, _plan_for_line, catalogue=cat)
    if benefit_type is None and payer:
        benefit_type = getattr(payer, "benefit_type", None)
    # Plan-type out-of-network TIER (Medicare/Dual only): resolve the member's plan to its structural
    # OON capability — the live CMS PBP plan when available, else the token written in the plan string.
    # This only FILLS a silent 271's OON tier; a definite 271 always wins (see final_determination).
    plan_cap = None
    try:
        from network_probe.domain.enrollment import live_enabled
        from network_probe.domain.plan_benefits import default_plan_benefit_store, resolve_plan_type

        pbp_store = default_plan_benefit_store() if live_enabled() else None
        # Prefer the Stedi 271's own plan name — it carries the H-number / product type (e.g.
        # "...DUAL COMPLETE HMOPOS FULL H0321", "...(PPO)") that pins the exact CMS PBP plan; the
        # caller's plan_hint is often a coarse marketing string. Fall back to the hint if the 271 was thin.
        plan_for_pbp = result.selected_plan or result.plan_name or q.plan_hint
        plan_cap = resolve_plan_type(plan_for_pbp, benefit_type, store=pbp_store).capability
    except Exception:
        plan_cap = None
    # Carried on the response so the async portal capture can reconcile with the same inputs.
    result.plan_oon_capability = plan_cap
    # An UNKNOWN row must never render blank: collect whatever we DO know so the determination can
    # state a best-available reading and the one thing that would settle it. Best-effort — evidence
    # gathering must never fail a check.
    # Each source of evidence gets its OWN try. They were once collected under a single one, whose
    # tail called an unbound name (`_catalogue_row` lives in domain/service and was never imported):
    # every check raised NameError there, hit `except: ev = {}`, and threw away the directory finding
    # gathered two lines earlier. Live result — a provider listed in UHC's own directory under 11
    # networks rendered as "Not yet established". A later, optional enrichment must never be able to
    # discard evidence that already succeeded.
    from network_probe.domain.service import _catalogue_row

    ev: dict = {
        # Did the 271 establish a member at all? `coverage_active is None` means the payer returned
        # nothing usable — no plan, nothing to pin, no search possible. A network status is a
        # property of a member's plan, so without one there is no direction to commit to; the
        # determination says "eligibility not established" instead of guessing. False (a definite
        # "not covered") is a real answer and does NOT suppress anything.
        "coverage_established": result.coverage_active is not None,
    }
    try:
        mp = getattr(verdict, "matched_provider", None) if verdict is not None else None
        if isinstance(mp, dict):
            nets = mp.get("networks")
            if nets:
                ev["directory_networks"] = len(nets)
            ev["in_directory"] = bool(mp.get("npi"))
            # Whether those networks are the MEMBER'S is the whole question. Adapters set
            # `matched_network` only when a network actually resolved to the member's plan, so a
            # plan given + no match means the directory hit is about OTHER products — which must
            # not read as in-network. See domain/determination._best_available.
            ev["plan_given"] = bool((q.plan_hint or "").strip() or result.selected_plan)
            ev["matched_network"] = bool(mp.get("matched_network"))
            if mp.get("group_contracted"):
                ev["group_contracted"] = True
                ev["roster_other_npis"] = len(mp.get("roster_npis_at_tin") or [])
            if mp.get("enrolled") is True:
                ev["medicare_enrolled"] = True
        elif verdict is not None and mp is None:
            ev["in_directory"] = False
    except Exception:  # noqa: BLE001 — evidence gathering must never fail a check
        pass
    try:
        row = _catalogue_row(q.payer, cat)
        if row is not None and getattr(row, "label", None):
            ev["payer_label"] = row.label
    except Exception:  # noqa: BLE001 — a missing label costs the wording, not the finding
        pass
    result.determination = final_determination(
        result.network_status, result.out_of_network_benefits,
        group_contracted=gc, plan_oon_capability=plan_cap, evidence=ev,
    ).to_dict()
    # Side-by-side evidence panel: what each source (Stedi 271, CMS PBP, credentialing, TiC, payer
    # directory) independently says. Best-effort — a live read never throws; benefit_type gates TiC/PBP.
    from network_probe.domain.evidence import assemble_evidence

    try:
        result.evidence_sources = assemble_evidence(
            q, result, benefit_type=benefit_type, catalogue=cat, run_directory=True,
        )
    except Exception:
        result.evidence_sources = []
    return result


def recheck_network(
    q: ProviderQuery,
    stedi_status: NetworkStatus,
    base_url: str | None = None,
    catalogue: PayerCatalogue | None = None,
    tenant_id=None,
    override_store=None,
) -> dict:
    """Re-run ONLY the directory leg for a newly chosen plan and re-merge against the prior 271
    status. No 270 is sent. Mirrors check_eligibility's merge + override tail."""
    cat = catalogue or DbPayerCatalogue()
    kw: dict = {"catalogue": cat}
    if base_url:
        kw["base_url"] = base_url
    try:
        verdict = check_network(q, **kw)
    except Exception:
        verdict = None
    status, corr = reconcile(stedi_status, verdict)
    store = override_store
    if store is None and tenant_id is not None:
        from network_probe.domain.overrides import DbOverrideStore

        store = DbOverrideStore(tenant_id)
    if store is not None:
        ov = store.lookup(q)
        if ov is not None:
            status = NetworkStatus(ov.status)
            corr = (corr or []) + [
                {
                    "source": "override",
                    "result": "authoritative",
                    "detail": f"{ov.status} confirmed by {ov.verified_by} ({ov.verified_at})",
                }
            ]
    return {
        "network_status": status.value,
        "network_verdict": verdict.to_dict() if verdict else None,
        "corroboration": corr,
    }
