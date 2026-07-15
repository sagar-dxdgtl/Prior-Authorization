"""Multi-source catalogue + FHIR directory routing.

Pure tests prove the routing logic and the seeded source columns; one db test proves the
columns persist and the DB catalogue surfaces them. No network: the FHIR routing test only
constructs the adapter (it never issues a request), and the catalogue is mocked.
"""

from __future__ import annotations

import pytest

from network_probe.core._http import CachedClient
from network_probe.domain import service as svc
from network_probe.payers.adapters.fhir_pdex import FhirPdexAdapter
from network_probe.payers.roster_seed import SOURCES, payer_rows

WELLPOINT = "Wellpoint / Amerigroup (Elevance)"
HEALTHSPRING = "Healthspring"
HEALTHSPRING_FHIR = "https://p-hi2.digitaledge.cigna.com/ProviderDirectory/v1"

KAISER = "Kaiser Permanente"
KAISER_FHIR = "https://kpx-service-bus.kp.org/service/hp/mhpo/healthplanproviderv1rc"
MOLINA = "Molina Healthcare"
MOLINA_FHIR = "https://api.interop.molinahealthcare.com/ProviderDirectory"
# Centene-family plans share one PDEX directory (no-auth; prod egress must be WAF-allowlisted).
CENTENE_FHIR = "https://iopc-pd.api.centene.com/iopc/pd/fhir/providerdirectory"
UHC_FHIR = "https://flex.optum.com/fhirpublic/R4"
_FHIR_PAYERS = {
    "Cigna Healthcare": "https://fhir.cigna.com/ProviderDirectory/v1",
    "Humana": "https://fhir.humana.com/api",
    "Devoted Health": "https://fhir.devoted.com/fhir",
    "Healthspring": HEALTHSPRING_FHIR,
    "AmeriHealth Caritas": "https://api-ext.amerihealthcaritas.com/NCEX/provider-api",
    "Kaiser Permanente": KAISER_FHIR,
    "Molina Healthcare": MOLINA_FHIR,
    "Ambetter (Centene)": CENTENE_FHIR,
    "Arizona Complete Health - Complete Care Plan (Centene)": CENTENE_FHIR,
    "Wellcare (Centene)": CENTENE_FHIR,
    "UnitedHealthcare Community Plan": UHC_FHIR,
}
_DIRECTORY_ACCESS = {"public-fhir", "authorized-fhir", "needs-authorized-api", "none", "pdf-directory"}

ANTHEM_LABEL = "BCBS / Empire (Anthem / Elevance)"
ANTHEM_FHIR = "https://totalview.healthos.elevancehealth.com/resources/unregistered/api/v1/fhir/cms_mandate/mcd"

HCSC_LABEL = "BCBS / Empire (Anthem / Elevance)(HCSC)"
HCSC_FHIR = "https://api.hcsc.net/providerfinder/sapphire/fhir"


class _FakeRow:
    def __init__(self, fhir_base_url: str | None):
        self.fhir_base_url = fhir_base_url


class _FakeCatalogue:
    """Stand-in PayerCatalogue: returns a row carrying a verified-public fhir_base_url."""

    def __init__(self, fhir_base_url: str | None):
        self._url = fhir_base_url

    def resolve(self, payer):
        return _FakeRow(self._url)


def _offline_client() -> CachedClient:
    return CachedClient(cache_dir=None, delay_seconds=0)


# ---- (a) a payer with fhir_base_url routes the directory leg to the FHIR PDEX adapter --------


def test_fhir_base_url_routes_directory_leg_to_pdex():
    # Healthspring now qualifies: verified public FHIR, fhir_base_url in catalogue.
    adapter = svc.get_adapter(HEALTHSPRING, catalogue=_FakeCatalogue(HEALTHSPRING_FHIR), client=_offline_client())
    assert isinstance(adapter, FhirPdexAdapter)
    assert adapter.base_url == HEALTHSPRING_FHIR  # constructed with the catalogue's verified URL


def test_kaiser_routes_directory_leg_to_pdex():
    # Kaiser now qualifies: verified public PDEX Plan-Net FHIR, fhir_base_url in catalogue.
    adapter = svc.get_adapter(KAISER, catalogue=_FakeCatalogue(KAISER_FHIR), client=_offline_client())
    assert isinstance(adapter, FhirPdexAdapter)
    assert adapter.base_url == KAISER_FHIR


def test_molina_routes_directory_leg_to_pdex():
    # Molina now qualifies: verified public PDEX Plan-Net FHIR, fhir_base_url in catalogue.
    adapter = svc.get_adapter(MOLINA, catalogue=_FakeCatalogue(MOLINA_FHIR), client=_offline_client())
    assert isinstance(adapter, FhirPdexAdapter)
    assert adapter.base_url == MOLINA_FHIR


def test_centene_family_routes_directory_leg_to_pdex():
    # All Centene-family plans share the one verified-public PDEX endpoint (no-auth; prod needs
    # WAF allowlist, but routing is driven purely by the catalogue fhir_base_url).
    for label in ("Ambetter (Centene)", "Wellcare (Centene)"):
        adapter = svc.get_adapter(label, catalogue=_FakeCatalogue(CENTENE_FHIR), client=_offline_client())
        assert isinstance(adapter, FhirPdexAdapter), label
        assert adapter.base_url == CENTENE_FHIR, label


SCAN = "Scan"
SCAN_FHIR = "https://providerdirectory.scanhealthplan.com"


def test_scan_routes_to_presence_adapter():
    # SCAN's directory has no traversable network linkage → presence-based ScanDirectoryAdapter,
    # NOT the generic FhirPdexAdapter.
    from network_probe.payers.adapters.scan import ScanDirectoryAdapter

    adapter = svc.get_adapter(SCAN, catalogue=_FakeCatalogue(SCAN_FHIR), client=_offline_client())
    assert isinstance(adapter, ScanDirectoryAdapter)
    assert not isinstance(adapter, FhirPdexAdapter)
    assert adapter.base_url == SCAN_FHIR


def test_scan_seeded_public_fhir():
    row = {r["label"]: r for r in payer_rows()}["Scan"]
    assert row["fhir_base_url"] == SCAN_FHIR
    assert row["directory_access"] == "public-fhir"


def test_align_routes_to_db_directory_adapter():
    # Align is a PDF-only network (no FHIR, no NPIs) → DbDirectoryAdapter, keyed by the roster slug.
    from network_probe.payers.adapters.db_directory import DbDirectoryAdapter

    class _Row:
        fhir_base_url = None
        directory_access = "pdf-directory"
        key = "align-senior-health-plan-fl-south-florida"
        label = "Align Senior Care"

    class _Cat:
        def resolve(self, payer):
            return _Row()

    adapter = svc.get_adapter("Align Senior Care", catalogue=_Cat())
    assert isinstance(adapter, DbDirectoryAdapter)
    assert adapter.payer_name == "align-senior-health-plan-fl-south-florida"
    assert adapter.payer_label == "Align Senior Care"


def test_align_seeded_as_pdf_directory():
    row = {r["label"]: r for r in payer_rows()}["Align Senior Health Plan"]
    assert row["directory_access"] == "pdf-directory"
    assert row["fhir_base_url"] is None


def test_no_fhir_base_url_and_no_adapter_raises_no_live_call():
    # A payer the catalogue can't help with stays a clean ValueError — never a live request.
    with pytest.raises(ValueError, match="No adapter"):
        svc.get_adapter("totally-unknown-payer", catalogue=_FakeCatalogue(None))


def test_registered_adapter_wins_over_catalogue():
    # Oscar has a more-specific registered adapter; the catalogue fhir_base_url must not override it.
    adapter = svc.get_adapter("oscar", catalogue=_FakeCatalogue(HEALTHSPRING_FHIR))
    assert not isinstance(adapter, FhirPdexAdapter)


# ---- authorized-FHIR (OAuth2-gated PDEX, e.g. Anthem) routing --------------------------------


class _AnthemRow:
    fhir_base_url = ANTHEM_FHIR
    directory_access = "authorized-fhir"
    key = "bcbs-empire-anthem-elevance-co-denver"
    label = ANTHEM_LABEL


class _AnthemCat:
    def resolve(self, payer):
        return _AnthemRow()


def test_direct_anthem_key_builds_oauth_adapter(monkeypatch):
    sentinel = object()
    monkeypatch.setattr(svc, "_build_anthem_adapter", lambda base_url=None, year=None: sentinel)
    assert svc.get_adapter("anthem") is sentinel


def test_direct_anthem_key_without_creds_raises(monkeypatch):
    monkeypatch.setattr(svc, "_build_anthem_adapter", lambda base_url=None, year=None: None)
    with pytest.raises(ValueError, match="ANTHEM_FHIR_"):
        svc.get_adapter("anthem")


def test_authorized_fhir_catalogue_row_routes_to_oauth_builder(monkeypatch):
    captured = {}

    def fake(base_url=None, year=None):
        captured["base_url"] = base_url
        return "anthem-adapter"

    monkeypatch.setattr(svc, "_build_anthem_adapter", fake)
    out = svc.get_adapter(ANTHEM_LABEL, catalogue=_AnthemCat())
    assert out == "anthem-adapter"
    assert captured["base_url"] == ANTHEM_FHIR  # the gated endpoint is passed to the OAuth2 builder


def test_authorized_fhir_without_creds_refuses_unauthenticated(monkeypatch):
    # No creds configured → builder returns None → refuse rather than hit the gated endpoint anon.
    monkeypatch.setattr(svc, "_build_anthem_adapter", lambda base_url=None, year=None: None)
    with pytest.raises(ValueError, match="authorized-FHIR"):
        svc.get_adapter(ANTHEM_LABEL, catalogue=_AnthemCat())


# ---- authorized-FHIR (static client_id header, e.g. HCSC) routing -----------------------------


class _HcscRow:
    fhir_base_url = HCSC_FHIR
    directory_access = "authorized-fhir"
    key = "bcbs-empire-anthem-elevance-hcsc-il"
    label = HCSC_LABEL


class _HcscCat:
    def resolve(self, payer):
        return _HcscRow()


def test_direct_hcsc_key_builds_apikey_adapter(monkeypatch):
    sentinel = object()
    monkeypatch.setattr(svc, "_build_hcsc_adapter", lambda base_url=None, year=None: sentinel)
    assert svc.get_adapter("hcsc") is sentinel


def test_direct_hcsc_key_without_creds_raises(monkeypatch):
    monkeypatch.setattr(svc, "_build_hcsc_adapter", lambda base_url=None, year=None: None)
    with pytest.raises(ValueError, match="HCSC_FHIR_CLIENT_ID"):
        svc.get_adapter("hcsc")


def test_hcsc_catalogue_row_routes_to_apikey_builder_not_anthem(monkeypatch):
    # Regression: the roster label literally contains "Anthem" AND "Elevance" AND "HCSC" — HCSC is
    # an independent Blue licensee, NOT Elevance, so this must NOT fall through to the Anthem
    # OAuth2 builder (wrong payer, wrong credentials).
    captured = {}

    def fake_hcsc(base_url=None, year=None):
        captured["base_url"] = base_url
        return "hcsc-adapter"

    def fake_anthem(base_url=None, year=None):
        raise AssertionError("must not route HCSC to the Anthem builder")

    monkeypatch.setattr(svc, "_build_hcsc_adapter", fake_hcsc)
    monkeypatch.setattr(svc, "_build_anthem_adapter", fake_anthem)
    out = svc.get_adapter(HCSC_LABEL, catalogue=_HcscCat())
    assert out == "hcsc-adapter"
    assert captured["base_url"] == HCSC_FHIR


def test_hcsc_without_creds_refuses_unauthenticated(monkeypatch):
    monkeypatch.setattr(svc, "_build_hcsc_adapter", lambda base_url=None, year=None: None)
    with pytest.raises(ValueError, match="authorized-FHIR"):
        svc.get_adapter(HCSC_LABEL, catalogue=_HcscCat())


# ---- (b) the seeded catalogue exposes fhir_base_url for the verified-public payers -----------


def test_seeded_fhir_base_urls_present():
    by_label = {r["label"]: r["fhir_base_url"] for r in payer_rows()}
    for label, url in _FHIR_PAYERS.items():
        assert by_label.get(label) == url, label
    # everything else has no baked FHIR base URL (honest: only verified-public servers)
    assert by_label["Aetna"] is None
    # Anthem/Elevance: CO-Denver routes to the authorized OAuth2 endpoint (Anthem BCBS Colorado IS
    # Elevance); AZ ("BCBS"=BCBSAZ) and FL ("BCBS"=Florida Blue) are independent licensees → unrouted.
    anthem_by_state = {r["state"]: r for r in payer_rows() if r["label"] == ANTHEM_LABEL}
    assert anthem_by_state["CO-Denver"]["fhir_base_url"] == ANTHEM_FHIR
    assert anthem_by_state["CO-Denver"]["directory_access"] == "authorized-fhir"
    assert anthem_by_state["AZ"]["fhir_base_url"] is None
    assert anthem_by_state["AZ"]["directory_access"] == "needs-authorized-api"
    assert anthem_by_state["FL-South Florida"]["fhir_base_url"] is None
    # Wellpoint is auth-gated (registered path returns 403) — must NOT carry a public fhir_base_url
    assert by_label[WELLPOINT] is None, "Wellpoint must not have a public fhir_base_url"
    # HCSC: one client_id-header-gated FHIR base covers all 3 markets this label spans.
    hcsc_by_state = {r["state"]: r for r in payer_rows() if r["label"] == HCSC_LABEL}
    for state in ("IL", "TX-Houston", "TX-Dallas"):
        assert hcsc_by_state[state]["fhir_base_url"] == HCSC_FHIR, state
        assert hcsc_by_state[state]["directory_access"] == "authorized-fhir", state


def test_wellpoint_is_auth_gated():
    rows_by_label = {r["label"]: r for r in payer_rows()}
    wp = rows_by_label[WELLPOINT]
    assert wp["fhir_base_url"] is None
    assert wp["directory_access"] == "needs-authorized-api"


# ---- (c) directory_access is populated for every roster row ----------------------------------


def test_directory_access_populated_for_all_rows():
    rows = list(payer_rows())
    assert rows, "roster produced no rows"
    for r in rows:
        assert r["directory_access"] in _DIRECTORY_ACCESS, (r["label"], r["directory_access"])


def test_public_fhir_rows_have_fhir_or_existing_adapter():
    # Any row flagged public-fhir must either carry a verified fhir_base_url or be UHC/Oscar
    # (whose public adapters are wired by adapter key, not by a catalogue URL).
    existing_adapter_labels = {"UnitedHealthcare", "Oscar"}
    for r in payer_rows():
        if r["directory_access"] == "public-fhir":
            assert r["fhir_base_url"] or r["label"] in existing_adapter_labels, r["label"]


def test_sources_keys_are_real_roster_labels():
    labels = {r["label"] for r in payer_rows()}
    assert set(SOURCES) <= labels  # no orphan source entries


# ---- db: columns persist and DbPayerCatalogue surfaces them ----------------------------------


@pytest.mark.db
def test_db_catalogue_surfaces_source_columns():
    from sqlalchemy.orm import Session

    from network_probe.db.base import owner_engine
    from network_probe.db.models import Payer
    from network_probe.payers.catalogue import DbPayerCatalogue

    with Session(owner_engine()) as s:  # _clean_db truncated payers; seed as owner
        for r in payer_rows():
            s.add(Payer(**r))
        s.commit()

    cat = DbPayerCatalogue()

    # Wellpoint is auth-gated: no public fhir_base_url, directory_access = needs-authorized-api
    wp = cat.resolve(WELLPOINT)
    assert wp is not None
    assert wp.fhir_base_url is None
    assert wp.directory_access == "needs-authorized-api"

    # Wellpoint no longer routes to a FHIR PDEX adapter via the catalogue URL
    with pytest.raises(ValueError, match="No adapter"):
        svc.get_adapter(WELLPOINT, catalogue=cat, client=_offline_client())

    # Healthspring has a verified-public FHIR URL → the engine routes to FhirPdexAdapter
    hs = cat.resolve(HEALTHSPRING)
    assert hs is not None
    assert hs.fhir_base_url == HEALTHSPRING_FHIR
    assert hs.directory_access == "public-fhir"
    hs_adapter = svc.get_adapter(HEALTHSPRING, catalogue=cat, client=_offline_client())
    assert isinstance(hs_adapter, FhirPdexAdapter)
    assert hs_adapter.base_url == HEALTHSPRING_FHIR

    # a govt/Medicaid program is honestly recorded as having no public directory source
    ahcccs = cat.resolve("Arizona Health Care Cost Containment System (AHCCCS)")
    assert ahcccs.directory_access == "none"
    assert ahcccs.fhir_base_url is None
