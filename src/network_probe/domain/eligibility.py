from __future__ import annotations

from network_probe.domain.benefits import EligibilityResult
from network_probe.domain.models import NetworkStatus, ProviderQuery
from network_probe.domain.service import check_network
from network_probe.payers.catalogue import DbPayerCatalogue, PayerCatalogue
from network_probe.stedi.client import EligibilitySource, StediEligibilityClient


def reconcile(stedi_status: NetworkStatus, verdict) -> tuple[NetworkStatus, list]:
    """Merge the 271-derived network status with the directory verdict (the correctness core).

    Rules (unchanged): directory IN vs 271 OUT -> REVIEW; directory OUT vs 271 IN -> REVIEW;
    271 UNKNOWN + decisive directory -> take the directory; otherwise keep the 271 status.
    """
    if verdict is None:
        return stedi_status, []
    corr = verdict.corroboration or []
    status = stedi_status
    if verdict.status == NetworkStatus.IN_NETWORK and stedi_status == NetworkStatus.OUT_OF_NETWORK:
        status = NetworkStatus.REVIEW
    elif verdict.status == NetworkStatus.OUT_OF_NETWORK and stedi_status == NetworkStatus.IN_NETWORK:
        status = NetworkStatus.REVIEW
    elif stedi_status == NetworkStatus.UNKNOWN and verdict.status != NetworkStatus.UNKNOWN:
        status = verdict.status
    return status, corr


def check_eligibility(
    q: ProviderQuery,
    base_url: str | None = None,
    catalogue: PayerCatalogue | None = None,
    stedi: EligibilitySource | None = None,
    tenant_id=None,
    override_store=None,
) -> EligibilityResult:
    cat = catalogue or DbPayerCatalogue()
    payer = cat.resolve(q.payer)
    source = stedi or StediEligibilityClient(payer_id=payer.stedi_payer_id if payer else None)
    result = source.check(q)
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
    return result
