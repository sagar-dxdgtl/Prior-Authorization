"""Payer-agnostic dispatch: pick an adapter by payer, run the query."""

from __future__ import annotations

from network_probe.domain.models import NetworkStatus, NetworkVerdict, ProviderQuery
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


# HCSC's Sapphire PDEX FHIR base is fixed/public (verified live) — only the client_id credential
# varies by environment, so it lives in Settings while the URL stays a constant here.
HCSC_FHIR_BASE_URL = "https://api.hcsc.net/providerfinder/sapphire/fhir"


def _build_hcsc_adapter(base_url: str | None = None, year: int | None = None):
    """Build the client_id-header-authed HCSC (BCBS IL/TX/MT/NM/OK) FHIR adapter from Settings,
    or None if HCSC_FHIR_CLIENT_ID is not configured (caller then refuses the gated endpoint)."""
    from network_probe.core.config import get_settings

    s = get_settings()
    if not s.hcsc_fhir_ready:
        return None
    from network_probe.payers.adapters.fhir_auth import build_apikey_fhir_adapter

    return build_apikey_fhir_adapter(
        payer_key="hcsc",
        base_url=base_url or HCSC_FHIR_BASE_URL,
        header_name="client_id",
        header_value=s.hcsc_fhir_client_id,
        year=year,
    )


# Authorized-FHIR catalogue rows (directory_access == "authorized-fhir") map to a builder for
# their specific auth mechanism. Gated on directory_access (NOT a label substring) so look-alike
# labels that merely contain "Elevance" — e.g. Wellpoint/Amerigroup, which has no live endpoint —
# never route here. "hcsc" is checked FIRST: the roster label "BCBS / Empire (Anthem /
# Elevance)(HCSC)" contains both "anthem"/"elevance" AND "hcsc" (HCSC is an independent Blue
# licensee, NOT Elevance) — checking anthem/elevance first would misroute it to the wrong payer's
# OAuth2 adapter.
def _authed_builder_for(row, key: str):
    blob = f"{getattr(row, 'key', '') or ''} {getattr(row, 'label', '') or ''} {key}".lower()
    if "hcsc" in blob:
        return _build_hcsc_adapter
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
    # Direct authed-FHIR payer key "hcsc": build the client_id-header adapter from Settings creds.
    if key == "hcsc":
        adapter = _build_hcsc_adapter(base_url=kwargs.get("base_url"), year=kwargs.get("year"))
        if adapter is not None:
            return adapter
        raise ValueError("Payer 'hcsc' needs HCSC_FHIR_CLIENT_ID in .env (none configured).")
    # No more-specific registered adapter: consult the catalogue row.
    row = _catalogue_row(payer, catalogue)
    # (a) verified-public FHIR base_url — passed explicitly, or the catalogue `fhir_base_url`.
    base_url = kwargs.get("base_url") or (getattr(row, "fhir_base_url", None) if row is not None else None)
    if base_url:
        # (a1) authorized-FHIR rows are credential-gated (OAuth2 or a static header) — never anon.
        if row is not None and getattr(row, "directory_access", None) == "authorized-fhir":
            builder = _authed_builder_for(row, key)
            adapter = builder(base_url=base_url, year=kwargs.get("year")) if builder else None
            if adapter is not None:
                return adapter
            raise ValueError(
                f"Payer {payer!r} is an authorized-FHIR directory but its credentials are not "
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
    # (c) bespoke short-key adapter reached via a roster key. Payers like Oscar register their
    # adapter only under a short key ("oscar") but callers resolve them by roster key
    # ("oscar-fl-south-florida"), which has no fhir_base_url to fall through to. Map the first key
    # segment onto the short-key factory so those resolve instead of raising "No adapter". Reached
    # only after the base_url/pdf paths fail, so FHIR-base payers (UHC/Cigna/Humana/Devoted) are
    # unaffected.
    seg = key.split("-", 1)[0]
    if seg and seg != key and seg in _ADAPTER_FACTORIES:
        return _ADAPTER_FACTORIES[seg](**kwargs)
    supported = ", ".join(sorted(_ADAPTER_FACTORIES)) or "(none)"
    raise ValueError(f"No adapter for payer {payer!r}. Supported: {supported}.")


def check_network(
    q: ProviderQuery, corroborate: bool = True, catalogue=None, **adapter_kwargs
) -> NetworkVerdict:
    # (0) Clinic credentialing matrix — the authoritative, plan-scoped provider-INN signal, keyed on
    # the billing TIN. When the clinic's own contract record answers (payer, plan, NPI, TIN), it
    # settles the verdict WITHOUT the unreliable public directory, and covers MA/Medicaid lines that
    # TiC can't. Fires only when a billing TIN is present to key on; otherwise fall through as before.
    if q.npi and q.tin:
        from network_probe.domain.credentialing import default_credentialing

        cred = default_credentialing().lookup(q.payer, q.npi, q.tin, plan=q.plan_hint)
        if cred is not None:
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
            )

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
