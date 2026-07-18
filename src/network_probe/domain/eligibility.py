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
        return stedi_status, []
    corr = verdict.corroboration or []
    if verdict.status != NetworkStatus.UNKNOWN:
        return verdict.status, corr
    return stedi_status, corr


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
            gc = group_contracted(q.payer, q.tin)
        except Exception:
            gc = None
    benefit_type = getattr(payer, "benefit_type", None) if payer else None
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
    result.determination = final_determination(
        result.network_status, result.out_of_network_benefits,
        group_contracted=gc, plan_oon_capability=plan_cap,
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
