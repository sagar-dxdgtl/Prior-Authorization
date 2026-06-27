from __future__ import annotations

from network_probe.domain.benefits import EligibilityResult
from network_probe.domain.models import NetworkStatus, ProviderQuery
from network_probe.domain.service import check_network
from network_probe.payers.catalogue import DbPayerCatalogue, PayerCatalogue
from network_probe.stedi.client import EligibilitySource, StediEligibilityClient


def check_eligibility(q: ProviderQuery, base_url: str | None = None,
                      catalogue: PayerCatalogue | None = None,
                      stedi: EligibilitySource | None = None,
                      tenant_id=None,
                      override_store=None) -> EligibilityResult:
    cat = catalogue or DbPayerCatalogue()
    payer = cat.resolve(q.payer)
    source = stedi or StediEligibilityClient(payer_id=payer.stedi_payer_id if payer else None)
    result = source.check(q)
    # Directory engine still owns provider-specific network status; merge/corroborate.
    try:
        verdict = check_network(q, **({"base_url": base_url} if base_url else {}))
    except Exception:
        verdict = None
    if verdict is not None:
        result.network_verdict = verdict
        result.corroboration = verdict.corroboration or []
        if verdict.status == NetworkStatus.IN_NETWORK and result.network_status == NetworkStatus.OUT_OF_NETWORK:
            result.network_status = NetworkStatus.REVIEW
        elif verdict.status == NetworkStatus.OUT_OF_NETWORK and result.network_status == NetworkStatus.IN_NETWORK:
            result.network_status = NetworkStatus.REVIEW
        elif result.network_status == NetworkStatus.UNKNOWN and verdict.status != NetworkStatus.UNKNOWN:
            result.network_status = verdict.status
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
                {"source": "override", "result": "authoritative",
                 "detail": f"{ov.status} confirmed by {ov.verified_by} ({ov.verified_at})"}]
            result.source_audit = {**(result.source_audit or {}),
                                   "override": f"{ov.verified_by} {ov.verified_at}"}
    return result
