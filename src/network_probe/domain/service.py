"""Payer-agnostic dispatch: pick an adapter by payer, run the query."""

from __future__ import annotations

from network_probe.domain.models import NetworkVerdict, ProviderQuery
from network_probe.payers.adapters.base import PayerAdapter
from network_probe.payers.adapters.devoted import DevotedAdapter
from network_probe.payers.adapters.fhir_pdex import KNOWN_ENDPOINTS as _FHIR_ENDPOINTS
from network_probe.payers.adapters.fhir_pdex import FhirPdexAdapter
from network_probe.payers.adapters.oscar import OscarAdapter


def _fhir_factory(payer_key: str):
    """Build a FHIR PDEX adapter bound to a known payer endpoint."""
    def make(**kwargs):
        return FhirPdexAdapter(payer_name=payer_key, **kwargs)
    return make


# Registry of available adapters. Adding a payer = add a class + one line here.
# Web-scrape adapters (open private endpoints):
_ADAPTER_FACTORIES = {
    "oscar": OscarAdapter,
    "devoted": DevotedAdapter,
    # Generic FHIR PDEX Plan-Net (compliant CMS Provider Directory API) — pass base_url=...:
    "fhir": FhirPdexAdapter,
}
# Bound FHIR endpoints (compliant, no auth): oscar/devoted style key -> FHIR adapter.
# Includes "uhc" -> UnitedHealthcare's public Optum FHIR Layer Exchange (no login required).
for _key in _FHIR_ENDPOINTS:
    _ADAPTER_FACTORIES[_key] = _fhir_factory(_key)


def get_adapter(payer: str, **kwargs) -> PayerAdapter:
    key = (payer or "").strip().lower()
    factory = _ADAPTER_FACTORIES.get(key)
    if factory is None:
        supported = ", ".join(sorted(_ADAPTER_FACTORIES)) or "(none)"
        raise ValueError(f"No adapter for payer {payer!r}. Supported: {supported}.")
    return factory(**kwargs)


def check_network(q: ProviderQuery, corroborate: bool = True, **adapter_kwargs) -> NetworkVerdict:
    adapter = get_adapter(q.payer, **adapter_kwargs)
    raw = adapter.check_network(q)
    snapshot = {
        "status": raw.status.value,
        "confidence": raw.confidence,
        "matched_provider": raw.matched_provider,
        "plan_or_network_checked": raw.plan_or_network_checked,
        "source_url": raw.source_url,
        "notes": raw.notes,
    }
    final = raw
    signals: list = []
    if corroborate:
        from network_probe.domain.corroboration import default_sources, finalize, run_display_signals
        client = getattr(adapter, "client", None)
        sources = default_sources(client)
        # signals are computed once against the raw directory verdict so they are available for
        # display even when an override decides the final verdict; finalize reuses them.
        sig_objs = run_display_signals(raw, q, sources)
        signals = [s.as_dict() for s in sig_objs]
        final = finalize(raw, q, sources, signals=sig_objs)
    final.evidence = {"payer_directory": snapshot, "signals": signals}
    return final
