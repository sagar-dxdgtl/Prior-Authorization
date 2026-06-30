"""Payer-agnostic dispatch: pick an adapter by payer, run the query."""

from __future__ import annotations

from network_probe.domain.models import NetworkVerdict, ProviderQuery
from network_probe.payers.adapters.base import PayerAdapter
from network_probe.payers.adapters.db_directory import DbDirectoryAdapter
from network_probe.payers.adapters.devoted import DevotedAdapter
from network_probe.payers.adapters.fhir_pdex import KNOWN_ENDPOINTS as _FHIR_ENDPOINTS
from network_probe.payers.adapters.fhir_pdex import FhirPdexAdapter
from network_probe.payers.adapters.oscar import OscarAdapter
from network_probe.payers.adapters.scan import ScanDirectoryAdapter


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


def _catalogue_row(payer: str, catalogue):
    """Resolve this payer to its catalogue row (for fhir_base_url / directory_access).

    Best-effort and failure-tolerant: if no catalogue is available (e.g. no DB), return None
    so the caller falls back to the normal 'no adapter' error — never a live call."""
    try:
        cat = catalogue
        if cat is None:
            from network_probe.payers.catalogue import DbPayerCatalogue

            cat = DbPayerCatalogue()
        return cat.resolve(payer)
    except Exception:
        return None


def _fhir_class_for(base_url: str):
    """Pick the FHIR adapter for a catalogue base_url. Most PDEX Plan-Net servers use the
    generic adapter; servers with a non-standard shape get a specialised one (e.g. SCAN's
    directory has no traversable network linkage → presence-based ScanDirectoryAdapter)."""
    if base_url and "scanhealthplan.com" in base_url:
        return ScanDirectoryAdapter
    return FhirPdexAdapter


def _build_anthem_adapter(base_url: str | None = None, year: int | None = None):
    """Build the OAuth2-authed Anthem/Elevance FHIR adapter from Settings, or None if its
    ANTHEM_FHIR_* credentials are not configured (caller then refuses the gated endpoint)."""
    from network_probe.core.config import get_settings

    s = get_settings()
    if not s.anthem_fhir_ready:
        return None
    from network_probe.payers.adapters.fhir_auth import build_authed_fhir_adapter

    return build_authed_fhir_adapter(
        payer_key="anthem",
        base_url=base_url or s.anthem_fhir_base_url,
        token_url=s.anthem_fhir_token_url,
        client_id=s.anthem_fhir_client_id,
        client_secret=s.anthem_fhir_client_secret,
        scope=s.anthem_fhir_scope,
        year=year,
    )


# Authorized-FHIR catalogue rows (directory_access == "authorized-fhir") map to an OAuth2 adapter
# builder. Gated on directory_access (NOT a label substring) so look-alike labels that merely
# contain "Elevance" — e.g. Wellpoint/Amerigroup, which has no live endpoint — never route here.
def _authed_builder_for(row, key: str):
    blob = f"{getattr(row, 'key', '') or ''} {getattr(row, 'label', '') or ''} {key}".lower()
    if "anthem" in blob or "elevance" in blob:
        return _build_anthem_adapter
    return None


def get_adapter(payer: str, catalogue=None, **kwargs) -> PayerAdapter:
    key = (payer or "").strip().lower()
    factory = _ADAPTER_FACTORIES.get(key)
    if factory is not None:
        return factory(**kwargs)
    # Direct authed-FHIR payer key (e.g. "anthem"): build the OAuth2 adapter from Settings creds.
    if key == "anthem":
        adapter = _build_anthem_adapter(base_url=kwargs.get("base_url"), year=kwargs.get("year"))
        if adapter is not None:
            return adapter
        raise ValueError("Payer 'anthem' needs ANTHEM_FHIR_* credentials in .env (none configured).")
    # No more-specific registered adapter: consult the catalogue row.
    row = _catalogue_row(payer, catalogue)
    # (a) verified-public FHIR base_url — passed explicitly, or the catalogue `fhir_base_url`.
    base_url = kwargs.get("base_url") or (getattr(row, "fhir_base_url", None) if row is not None else None)
    if base_url:
        # (a1) authorized-FHIR rows are OAuth2-gated — build with a bearer token, never anon.
        if row is not None and getattr(row, "directory_access", None) == "authorized-fhir":
            builder = _authed_builder_for(row, key)
            adapter = builder(base_url=base_url, year=kwargs.get("year")) if builder else None
            if adapter is not None:
                return adapter
            raise ValueError(
                f"Payer {payer!r} is an authorized-FHIR directory but its OAuth2 credentials are not "
                f"configured (set the payer's *_FHIR_* vars in .env). Refusing to query it unauthenticated."
            )
        kwargs["base_url"] = base_url
        return _fhir_class_for(base_url)(payer_name=key or "fhir", **kwargs)
    # (b) PDF-only directory (no FHIR, no NPIs — e.g. Align Senior Care): parsed rows in the DB.
    if row is not None and getattr(row, "directory_access", None) == "pdf-directory":
        kwargs.pop("base_url", None)
        return DbDirectoryAdapter(
            payer_name=getattr(row, "key", key), payer_label=getattr(row, "label", key), **kwargs
        )
    supported = ", ".join(sorted(_ADAPTER_FACTORIES)) or "(none)"
    raise ValueError(f"No adapter for payer {payer!r}. Supported: {supported}.")


def check_network(
    q: ProviderQuery, corroborate: bool = True, catalogue=None, **adapter_kwargs
) -> NetworkVerdict:
    adapter = get_adapter(q.payer, catalogue=catalogue, **adapter_kwargs)
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
